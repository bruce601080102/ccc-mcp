"""Evaluation Engine acceptance tests (see the Multi-Agent Evaluation Engine brief).
Follows tests/test_test_evidence.py's and tests/test_meeting_mode.py's own
conventions: `bridge`/`human` fixtures, `adapter="fake"` dispatches, writing straight
into the task's worktree to manufacture real host-observed diffs/test evidence.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

from ccc_mcp.infra.errors import BridgeError
from ccc_mcp.worker import run_execution


def pending_patch(bridge, execution_id):
    result = next(
        item for item in bridge.latest("demo")["snapshot"]["agent_results"]
        if item["execution_id"] == execution_id
    )
    return {
        "base_context_version": bridge.latest("demo")["version"],
        "changes": {"summary": f"PM reviewed {result['role']} evidence."},
        "source_execution_ids": [execution_id],
        "source_task_ids": [result["task_id"]],
        "source_document_refs": [result["source_document_ref"]],
        "update_reason": "PM verified the completed Agent result",
        "semantic_review_ack": True,
    }


def dispatch_and_run(bridge, task_id, role, fake_scenario="success"):
    agent = "codex" if role in {"SA", "REVIEW"} else "claude"
    result = bridge.dispatch_agent(
        "demo", task_id, agent, role, bridge.latest("demo")["version"], "Perform role",
        adapter="fake", fake_scenario=fake_scenario,
    )
    run_execution(bridge, result["execution_id"])
    return result["execution_id"]


def worktree_of(bridge, execution_id):
    return Path(bridge.store.one(
        "SELECT worktree FROM agent_executions WHERE id=?", (execution_id,)
    )["worktree"])


def start_task(bridge, requirement="Implement a change", acceptance=None, full_suite=False):
    bridge.authorize_project_local("demo")
    bridge.configure_evaluation_engine("demo", True)
    if full_suite:
        bridge.configure_test_command("demo", [sys.executable, "-m", "pytest", "-q"])
    return bridge.create_task("demo", requirement, acceptance=acceptance)["id"]


def advance_to_implementation(bridge, task_id):
    """SA -> approve -> first SD/RD round (already run). Returns its execution_id."""
    sa = dispatch_and_run(bridge, task_id, "SA")
    implementation = bridge.advance_project(
        "demo", pending_patch(bridge, sa), "Approved plan", adapter="fake"
    )["next_execution"]
    run_execution(bridge, implementation["execution_id"])
    return implementation["execution_id"]


def send_back_for_revision(bridge, task_id, implementation_id):
    """REVIEW asks for a revision; returns (worktree, review_execution_id) so the
    caller can write new files into the SAME (already-created, deterministic-per-task)
    worktree before dispatching the redo round — the only way to get a real,
    host-observed diff out of the file-untouching fake adapter (see worker.py: it
    captures `git diff HEAD` right after its own, still no-op, adapter.run() call)."""
    worktree = worktree_of(bridge, implementation_id)
    review_id = bridge.advance_project(
        "demo", pending_patch(bridge, implementation_id), adapter="fake", fake_scenario="revision",
    )["next_execution"]["execution_id"]
    run_execution(bridge, review_id)
    return worktree, review_id


def redo_round(bridge, task_id, review_id, worktree, files):
    for name, content in files.items():
        (worktree / name).write_text(content)
    round2 = bridge.advance_project(
        "demo", pending_patch(bridge, review_id), adapter="fake"
    )["next_execution"]
    run_execution(bridge, round2["execution_id"])
    return round2["execution_id"]


def commit_pre_existing_test(repo_root):
    (repo_root / "test_pre_existing.py").write_text(
        "def test_pre_existing():\n    assert 1 + 1 == 2\n"
    )
    for args in (["add", "."], ["-c", "user.name=t", "-c", "user.email=t@example.invalid",
                                 "commit", "-m", "add pre-existing test"]):
        subprocess.run(["git", "-C", str(repo_root), *args], check=True, capture_output=True)


def set_review_findings(bridge, execution_id, findings):
    """Edit a REVIEW execution's already-stored report in place (Python-side JSON
    round trip, no SQLite JSON1 dependency) — simulates REVIEW having cited specific
    files, which the fake adapter's own canned report text never does."""
    row = bridge.store.one("SELECT result FROM agent_executions WHERE id=?", (execution_id,))
    result = json.loads(row["result"])
    result["report"]["findings"] = findings
    bridge.store.execute(
        "UPDATE agent_executions SET result=? WHERE id=?", (json.dumps(result), execution_id)
    )


def evaluation_of(reports, role):
    return next(r for r in reports if r["role"] == role)


# --- 1. full green pipeline -------------------------------------------------------

def test_full_pipeline_scores_cleanly(bridge, human):
    task_id = start_task(bridge, acceptance=["Implement the change"], full_suite=True)
    implementation_id = advance_to_implementation(bridge, task_id)
    worktree, review1_id = send_back_for_revision(bridge, task_id, implementation_id)
    round2_id = redo_round(
        bridge, task_id, review1_id, worktree,
        {"test_clean.py": "def test_clean():\n    assert 1 + 1 == 2\n"},
    )
    review2 = bridge.advance_project(
        "demo", pending_patch(bridge, round2_id), adapter="fake"
    )["next_execution"]
    run_execution(bridge, review2["execution_id"])

    reports = bridge.evaluate_task("demo", task_id, force=True)
    sd_rd = evaluation_of(reports, "SD/RD")
    assert sd_rd["task_status"] == "PASSED"
    assert sd_rd["evidence_coverage"] > 0
    assert sd_rd["quality_score"] is not None
    assert evaluation_of(reports, "REVIEW")["task_status"] == "PASSED"


# --- 2. FAIL_TO_PASS failing must never be offset by PASS_TO_PASS passing --------

def test_new_test_failing_forces_task_failed_even_if_old_tests_pass(bridge, human):
    repo_root = Path(bridge.project("demo")["root"])
    commit_pre_existing_test(repo_root)
    task_id = start_task(bridge, full_suite=True)
    implementation_id = advance_to_implementation(bridge, task_id)
    worktree, review1_id = send_back_for_revision(bridge, task_id, implementation_id)
    round2_id = redo_round(
        bridge, task_id, review1_id, worktree,
        {"test_new_feature.py": "def test_new_feature():\n    assert False, 'not implemented yet'\n"},
    )
    result = bridge.advance_project(
        "demo", pending_patch(bridge, round2_id), adapter="fake", fake_scenario="revision"
    )
    assert result["test_evidence"]["passed"] is False

    report = bridge.evaluate_task("demo", task_id, role="SD/RD", force=True)[0]
    features = report["scoring_details"]["features"]
    assert features["fail_to_pass_rate"] == 0.0
    assert features["regression_pass_rate"] == 1.0
    assert report["task_status"] == "FAILED"


# --- 3. no test evidence -> never PASSED ------------------------------------------

def test_no_test_evidence_never_counts_as_passed(bridge, human):
    task_id = start_task(bridge)
    advance_to_implementation(bridge, task_id)

    report = bridge.evaluate_task("demo", task_id, role="SD/RD")[0]
    assert report["scoring_details"]["features"]["host_tests_executed"] is False
    assert report["task_status"] != "PASSED"
    assert report["task_status"] in {"NEEDS_VERIFICATION", "INSUFFICIENT_EVIDENCE"}


# --- 4. REVIEW claims a bug with no real evidence -> never a confirmed defect ----

def test_unaddressed_review_finding_is_not_treated_as_a_real_defect(bridge, human):
    task_id = start_task(bridge, full_suite=True)
    implementation_id = advance_to_implementation(bridge, task_id)
    review_id = bridge.advance_project(
        "demo", pending_patch(bridge, implementation_id), adapter="fake"
    )["next_execution"]["execution_id"]
    run_execution(bridge, review_id)
    # REVIEW cites a specific file but nothing ever touches it again, and the task
    # still passes host tests: must land as NEEDS_VERIFICATION, never CONFIRMED.
    set_review_findings(bridge, review_id, ["Possible off-by-one in `app.py`"])

    report = bridge.evaluate_task("demo", task_id, role="REVIEW", force=True)[0]
    kinds = {d["kind"]: d["resolution_status"] for d in report["disagreements"]}
    assert "review_finding_confirmed" not in kinds
    features = report["scoring_details"]["features"]
    assert features["false_review_findings"] == 0
    assert features["review_findings_needing_verification"] == 1


# --- 5. cross-role disagreement is recorded --------------------------------------

def test_scope_mismatch_between_sa_plan_and_diff_is_recorded(bridge, human):
    task_id = start_task(bridge)
    sa = dispatch_and_run(bridge, task_id, "SA")
    set_review_findings(bridge, sa, ["Plan: only touch `module_a.py`."])
    implementation = bridge.advance_project(
        "demo", pending_patch(bridge, sa), "Approved plan content", adapter="fake"
    )["next_execution"]
    run_execution(bridge, implementation["execution_id"])
    worktree, review1_id = send_back_for_revision(bridge, task_id, implementation["execution_id"])
    redo_round(bridge, task_id, review1_id, worktree, {"module_b.py": "def other():\n    return 2\n"})

    report = bridge.evaluate_task("demo", task_id, role="SD/RD", force=True)[0]
    kinds = [d["kind"] for d in report["disagreements"]]
    assert "scope_mismatch" in kinds


# --- 6/7. reputation: correct math, no double counting ---------------------------

def test_reputation_reliability_and_dedup(bridge, human, monkeypatch):
    task_id = start_task(bridge, acceptance=["Implement the change"], full_suite=True)
    implementation_id = advance_to_implementation(bridge, task_id)
    worktree, review1_id = send_back_for_revision(bridge, task_id, implementation_id)
    round2_id = redo_round(
        bridge, task_id, review1_id, worktree,
        {"test_clean.py": "def test_clean():\n    assert 1 + 1 == 2\n"},
    )
    review2 = bridge.advance_project(
        "demo", pending_patch(bridge, round2_id), adapter="fake"
    )["next_execution"]
    run_execution(bridge, review2["execution_id"])

    bridge.evaluate_task("demo", task_id, role="REVIEW", force=True)
    rows = bridge.get_agent_reliability(role="REVIEW")
    review_row = next(r for r in rows if r["role"] == "REVIEW")
    success, failure = review_row["verified_success"], review_row["verified_failure"]
    assert review_row["sample_count"] == success + failure
    assert review_row["reliability_score"] == pytest.approx(
        (success + 1) / (success + failure + 2), abs=1e-4
    )

    from ccc_mcp.evaluation_engine import quality_scorer

    original_score = quality_scorer.score
    monkeypatch.setattr(
        quality_scorer, "score",
        lambda *args: {**original_score(*args), "task_status": "FAILED"},
    )
    bridge.reevaluate_execution("demo", task_id, review2["execution_id"])
    ledger_rows = bridge.store.one(
        "SELECT COUNT(*) AS n FROM reputation_events WHERE source_execution_id=?",
        (review2["execution_id"],),
    )["n"]
    assert ledger_rows == 1
    corrected = next(r for r in bridge.get_agent_reliability(role="REVIEW") if r["role"] == "REVIEW")
    assert corrected["verified_success"] == 0
    assert corrected["verified_failure"] == 1
    versions = bridge.get_task_evaluations("demo", task_id, role="REVIEW")
    assert [v["version"] for v in versions] == [1, 2]


# --- 8. evaluation data survives restart ------------------------------------------

def test_evaluations_persist_across_a_fresh_bridge_instance(bridge, human):
    from ccc_mcp.service import CCCMCP

    task_id = start_task(bridge)
    implementation_id = advance_to_implementation(bridge, task_id)
    review = bridge.advance_project(
        "demo", pending_patch(bridge, implementation_id), adapter="fake"
    )["next_execution"]
    run_execution(bridge, review["execution_id"])
    bridge.evaluate_task("demo", task_id, force=True)

    reopened = CCCMCP(bridge.home)
    restored = reopened.get_task_evaluations("demo", task_id)
    assert len(restored) >= 2
    assert {r["role"] for r in restored} >= {"SD/RD", "REVIEW"}


# --- 9. disabled Evaluation Engine never touches the original flow ---------------

def test_disabled_evaluation_engine_leaves_workflow_untouched(bridge, human):
    task_id = bridge.create_task("demo", "Implement a change")["id"]
    bridge.authorize_project_local("demo")
    assert bridge.get_project_settings("demo")["evaluation_engine"]["enabled"] is False
    implementation_id = advance_to_implementation(bridge, task_id)
    review = bridge.advance_project(
        "demo", pending_patch(bridge, implementation_id), adapter="fake"
    )["next_execution"]
    run_execution(bridge, review["execution_id"])

    assert bridge.task("demo", task_id)["state"] == "AWAITING_PM_REVIEW"
    assert bridge.store.all("SELECT 1 FROM evaluations") == []
    with pytest.raises(BridgeError):
        bridge.evaluate_task("demo", task_id)


# --- 10. an evaluation crash must never lose already-completed task data --------

def test_auto_evaluate_exception_never_affects_the_task(bridge, human, monkeypatch):
    task_id = start_task(bridge)
    bridge.configure_evaluation_engine("demo", True, auto_evaluate=True)

    from ccc_mcp.evaluation_engine import evaluation_service

    def boom(*args, **kwargs):
        raise RuntimeError("simulated evaluation crash")

    monkeypatch.setattr(evaluation_service, "evaluate_execution", boom)
    implementation_id = advance_to_implementation(bridge, task_id)  # must not raise

    row = bridge.store.one(
        "SELECT status FROM agent_executions WHERE id=?", (implementation_id,)
    )
    assert row["status"] == "SUCCEEDED"
    assert bridge.store.all("SELECT 1 FROM evaluations") == []


# --- 11. different roles apply different scoring rules ---------------------------

def test_role_specific_weight_profiles_differ(bridge, human):
    task_id = start_task(bridge)
    implementation_id = advance_to_implementation(bridge, task_id)
    review = bridge.advance_project(
        "demo", pending_patch(bridge, implementation_id), adapter="fake"
    )["next_execution"]
    run_execution(bridge, review["execution_id"])

    reports = bridge.evaluate_task("demo", task_id, force=True)
    sa_weights = evaluation_of(reports, "SA")["scoring_details"]["weights"]
    sd_rd_weights = evaluation_of(reports, "SD/RD")["scoring_details"]["weights"]
    review_weights = evaluation_of(reports, "REVIEW")["scoring_details"]["weights"]
    assert "code_quality" not in sa_weights and "security" not in sa_weights
    assert "code_quality" not in review_weights and "security" not in review_weights
    assert "code_quality" in sd_rd_weights and "security" in sd_rd_weights


# --- 12. missing vs. zero-scored features are distinguished ----------------------

def test_missing_feature_is_none_not_a_fabricated_score(bridge, human):
    task_id = start_task(bridge)
    advance_to_implementation(bridge, task_id)

    report = bridge.evaluate_task("demo", task_id, role="SD/RD")[0]
    sub_scores = report["scoring_details"]["sub_scores"]
    # No diff was ever produced for this round (fake adapter touches nothing, nothing
    # was written to the worktree) -> code_quality/security must stay None, never 0.
    assert sub_scores["code_quality"] is None
    assert sub_scores["security"] is None
    assert report["evidence_coverage"] < 1.0
