"""Gather already-durable facts about one Agent execution — never a new LLM call,
never trusting an Agent's own claim as verification (constraint #6/#7 of the brief).

Everything here is read from data ccc-mcp already persisted: agent_executions,
the per-execution CHANGES.diff artifact, and the task's host-run TEST-RESULT.md.
Static analysis (ruff) is the one piece of NEW computation, and it is a host-run
tool over already-written files, not a model call.
"""

import json
import shutil
import subprocess
from pathlib import Path

from ..adapters.storage import now
from . import diffs
from .models import CLAIMED, FAILED, UNVERIFIED, VERIFIED, evidence_item

IMPLEMENTATION_ROLES = {"SD", "RD", "SD/RD"}
_RUFF_TIMEOUT_SECONDS = 60


def _find_execution(executions, execution_id):
    for row in executions:
        if row["id"] == execution_id:
            return row
    raise ValueError(f"Execution not found on this task: {execution_id}")


def _nearest(executions, before_created, roles, require_worktree=False):
    """Most recent execution with role in `roles` strictly before `before_created`."""
    candidates = [
        row for row in executions
        if row["role"] in roles and row["created"] < before_created
        and (not require_worktree or row["worktree"])
    ]
    return candidates[-1] if candidates else None


def _diff_text(bridge, project_id, execution_row):
    """`git diff HEAD` (CHANGES.diff) plus a synthesized block for every still-
    untracked new file — see diffs.synthesize_untracked_diff for why the plain
    tracked-file diff alone would silently miss every brand-new file this round."""
    result = execution_row.get("result") or {}
    reference = result.get("diff_reference")
    if not reference:
        return None
    row = bridge.store.one(
        "SELECT content FROM artifacts WHERE project_id=? AND path=?", (project_id, reference)
    )
    tracked = row["content"] if row else ""
    untracked = diffs.synthesize_untracked_diff(result.get("new_file_contents"))
    combined = "\n".join(part for part in (tracked, untracked) if part)
    return combined or None


def _run_ruff(worktree, paths):
    """Best-effort static analysis; never raises — an unavailable/failing tool is a
    missing feature (see feature_extractor), not an evaluation crash (brief #10)."""
    ruff = shutil.which("ruff")
    if not ruff or not worktree or not paths:
        return None, "ruff not available or nothing to check" if not ruff else "no changed Python files"
    existing = [p for p in paths if (Path(worktree) / p).is_file()]
    if not existing:
        return None, "changed Python files no longer exist in the worktree"
    try:
        result = subprocess.run(
            [ruff, "check", "--output-format=json", *existing],
            cwd=worktree, capture_output=True, timeout=_RUFF_TIMEOUT_SECONDS,
            stdin=subprocess.DEVNULL, shell=False, check=False,
        )
        return json.loads(result.stdout or "[]"), None
    except (OSError, subprocess.TimeoutExpired, ValueError) as exc:
        return None, f"ruff failed: {exc}"


def collect(bridge, project_id, task_id, role, execution_id):
    """Returns {"evidence": [EvidenceItem,...], "report": dict, "test_result": dict|None,
    "diff_text": str|None, "worktree": str|None, "prior_report": dict|None,
    "review_report": dict|None, "review_execution": dict|None}. The trailing fields are
    plain re-reads of already-fetched rows, handed to feature_extractor/
    disagreement_detector so they never re-query the store themselves.
    """
    task = bridge.task(project_id, task_id)
    executions = bridge.get_task_result(project_id, task_id)
    target = _find_execution(executions, execution_id)
    agent = target["agent"]
    report = (target.get("result") or {}).get("report") or {}
    collected_at = now()
    evidence = []

    evidence.append(
        evidence_item(
            kind="requirement", status=VERIFIED, project_id=project_id, task_id=task_id,
            role=role, agent=agent, source="tasks.requirement", collected_at=collected_at,
            payload={"requirement": task["requirement"], "acceptance": task["acceptance"]},
        )
    )
    if target.get("status") == "SUCCEEDED" and report:
        evidence.append(
            evidence_item(
                kind="agent_output", status=CLAIMED, project_id=project_id, task_id=task_id,
                role=role, agent=agent, source=f"agent_executions:{execution_id}",
                collected_at=collected_at, payload=report,
                notes="Agent's own self-report; not verification of correctness.",
            )
        )
    elif target.get("status") == "FAILED":
        evidence.append(
            evidence_item(
                kind="agent_output", status=FAILED, project_id=project_id, task_id=task_id,
                role=role, agent=agent, source=f"agent_executions:{execution_id}",
                collected_at=collected_at, payload={"error": target.get("error")},
            )
        )

    diff_text = None
    worktree = target.get("worktree")
    if role in IMPLEMENTATION_ROLES:
        diff_text = _diff_text(bridge, project_id, target)
    elif role == "REVIEW":
        prior_impl = _nearest(executions, target["created"], IMPLEMENTATION_ROLES, require_worktree=True)
        if prior_impl:
            diff_text = _diff_text(bridge, project_id, prior_impl)
            worktree = prior_impl.get("worktree")
    if diff_text is not None:
        evidence.append(
            evidence_item(
                kind="git_diff", status=VERIFIED, project_id=project_id, task_id=task_id,
                role=role, agent=agent, source="CHANGES.diff", collected_at=collected_at,
                payload={"touched_files": diffs.touched_files(diff_text), "length": len(diff_text)},
            )
        )

    test_result = bridge._latest_test_result(project_id, task_id)
    if test_result is None:
        evidence.append(
            evidence_item(
                kind="pytest_result", status=UNVERIFIED, project_id=project_id, task_id=task_id,
                role=role, agent=agent, source="TEST-RESULT.md", collected_at=collected_at,
                payload={}, notes="No host test evidence recorded for this task yet.",
            )
        )
    else:
        executed = bool(test_result.get("executed_by_host"))
        status = VERIFIED if executed else UNVERIFIED
        evidence.append(
            evidence_item(
                kind="pytest_result", status=status, project_id=project_id, task_id=task_id,
                role=role, agent=agent, source="TEST-RESULT.md", collected_at=collected_at,
                payload={
                    "executed_by_host": executed,
                    "passed": test_result.get("passed"),
                    "no_tests_collected": test_result.get("no_tests_collected"),
                    "stdout": test_result.get("stdout"),
                    "command": test_result.get("command"),
                },
            )
        )

    static_findings, static_reason = (None, "No diff to analyze")
    if diff_text:
        python_paths = diffs.changed_python_files(diff_text)
        static_findings, static_reason = _run_ruff(worktree, python_paths)
    evidence.append(
        evidence_item(
            kind="static_analysis", status=VERIFIED if static_findings is not None else UNVERIFIED,
            project_id=project_id, task_id=task_id, role=role, agent=agent, source="ruff check",
            collected_at=collected_at, payload={"findings": static_findings}, notes=static_reason,
        )
    )

    review_execution = None
    review_report = None
    if role in IMPLEMENTATION_ROLES:
        review_execution = next(
            (row for row in executions if row["role"] == "REVIEW" and row["created"] > target["created"]),
            None,
        )
    elif role == "REVIEW":
        review_execution, review_report = target, report
    if review_execution and review_execution is not target:
        review_report = (review_execution.get("result") or {}).get("report") or {}
    if review_execution:
        evidence.append(
            evidence_item(
                kind="review_finding", status=CLAIMED, project_id=project_id, task_id=task_id,
                role="REVIEW", agent=review_execution["agent"],
                source=f"agent_executions:{review_execution['id']}", collected_at=collected_at,
                payload={
                    "needs_revision": review_report.get("needs_revision"),
                    "findings": review_report.get("findings", []),
                    "risks": review_report.get("risks", []),
                },
                notes="REVIEW's own self-report; verification status is in disagreements, not here.",
            )
        )

    prior_sa = _nearest(executions, target["created"], {"SA"})
    prior_report = (
        ((prior_sa.get("result") or {}).get("report") or {}) if prior_sa else None
    )

    later_diffs = []
    if review_execution:
        for row in executions:
            if row["created"] > review_execution["created"] and row["role"] in IMPLEMENTATION_ROLES:
                text = _diff_text(bridge, project_id, row)
                if text:
                    later_diffs.append(text)

    return {
        "evidence": evidence,
        "task": task,
        "report": report,
        "test_result": test_result,
        "diff_text": diff_text,
        "worktree": worktree,
        "prior_report": prior_report,
        "review_report": review_report,
        "review_execution": review_execution,
        "later_diffs": later_diffs,
        "executions": executions,
        "target": target,
    }
