"""Entry point the rest of ccc-mcp calls (core/evaluation.py is the thin Hub wrapper
that gives this module a `bridge` with store/project/task access). Orchestrates:
collect evidence -> detect disagreements -> extract features -> score -> persist ->
update reputation — and nothing here ever raises out past evaluate_execution's own
transaction in a way that could touch the task's own already-committed state (brief
#10: an evaluation crash must never lose an already-completed Task's data; see
core/evaluation.py's maybe_auto_evaluate for the try/except boundary)."""

import json
import re
import uuid

from ..adapters.storage import now
from ..infra.errors import BridgeError
from ..infra.security import canonical
from . import disagreement_detector, evidence_collector, feature_extractor, quality_scorer
from . import reputation_tracker as reputation
from .config import validated_role_weights
from .evidence_collector import IMPLEMENTATION_ROLES
from .models import SCHEMA_VERSION


def _settings(bridge, project_id):
    return bridge.get_project_settings(project_id).get("evaluation_engine", {})


def evaluate_execution(bridge, project_id, task_id, execution_id, *, task_type="general", trigger="manual"):
    """Always creates a NEW version row (brief #4.5 "重新評估並保留版本紀錄") — never
    overwrites a prior evaluation of the same execution."""
    row = bridge.store.one(
        "SELECT * FROM agent_executions WHERE id=? AND project_id=? AND task_id=?",
        (execution_id, project_id, task_id),
    )
    if not row:
        raise BridgeError("Execution not found on this task")
    role, agent = row["role"], row["agent"]
    options = json.loads(row["options"]) if row["options"] else {}
    model = options.get("agent_settings", {}).get("model") or agent

    bundle = evidence_collector.collect(bridge, project_id, task_id, role, execution_id)
    detected = disagreement_detector.detect(bundle)
    features = feature_extractor.extract(bundle, detected["review_tally"], detected["disagreements"])
    weights = validated_role_weights(_settings(bridge, project_id).get("weights"))
    scored = quality_scorer.score(role, features, weights)

    with bridge.store.transaction() as conn:
        existing = conn.execute(
            "SELECT MAX(version) AS v FROM evaluations WHERE source_execution_id=?", (execution_id,)
        ).fetchone()
        version = 1 + (existing["v"] or 0)
        evaluation_id = "EVAL-" + uuid.uuid4().hex[:16]
        created = now()
        scoring_details = {
            "sub_scores": scored["sub_scores"], "weights": scored["weights"], "features": features,
        }
        conn.execute(
            "INSERT INTO evaluations(id,project_id,task_id,role,agent,model,task_type,"
            "source_execution_id,version,trigger,quality_score,evidence_coverage,task_status,"
            "scoring_details,evidence,disagreements,schema_version,created) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                evaluation_id, project_id, task_id, role, agent, model, task_type, execution_id,
                version, trigger, scored["quality_score"], scored["evidence_coverage"],
                scored["task_status"], canonical(scoring_details), canonical(bundle["evidence"]),
                canonical(detected["disagreements"]), SCHEMA_VERSION, created,
            ),
        )
        outcome = reputation.outcome_for(scored["task_status"])
        if outcome:
            # `conn` (the live transaction) satisfies record_outcome's store.execute(sql,
            # args) duck type directly — sqlite3.Connection.execute has the same shape as
            # Store.execute — so this lands in the SAME transaction as the evaluation row.
            reputation.record_outcome(
                conn, project_id=project_id, task_id=task_id, role=role, agent=agent,
                model=model, task_type=task_type, execution_id=execution_id, outcome=outcome,
            )

    low_sample_threshold = _settings(bridge, project_id).get(
        "low_sample_threshold", reputation.DEFAULT_LOW_SAMPLE_THRESHOLD
    )
    reliability_row = next(
        (
            r for r in reputation.query(
                bridge.store, agent=agent, role=role, task_type=task_type,
                low_sample_threshold=low_sample_threshold,
            )
            if r["model"] == model
        ),
        None,
    )
    return {
        "schema_version": SCHEMA_VERSION,
        "evaluation_id": evaluation_id,
        "version": version,
        "trigger": trigger,
        "task_id": task_id,
        "source_execution_id": execution_id,
        "agent": agent,
        "model": model,
        "role": role,
        "task_type": task_type,
        "quality_score": scored["quality_score"],
        "reliability_score": reliability_row["reliability_score"] if reliability_row else None,
        # Never a fabricated number (brief #3's "不要憑空產生 0.9 之類的信心值"): a real
        # calibration mechanism (e.g. Brier score against later-settled task_status
        # outcomes, once enough history exists) is future work, not v1.
        "confidence": None,
        "evidence_coverage": scored["evidence_coverage"],
        "task_status": scored["task_status"],
        "scoring_details": scoring_details,
        "evidence": bundle["evidence"],
        "disagreements": detected["disagreements"],
        "evaluation_timestamp": created,
    }


def evaluate_task(bridge, project_id, task_id, *, role=None, task_type="general", trigger="manual", force=False):
    """No `role`: evaluate the latest SUCCEEDED execution of every role that has one.
    Skips a role whose latest execution was already evaluated at least once, unless
    `force` — the auto-evaluate hook relies on this to stay idempotent per execution
    (brief's test #7 applies just as much to a whole-task re-run as to one execution)."""
    executions = bridge.get_task_result(project_id, task_id)
    if role is not None:
        candidates = [r for r in executions if r["role"] == role and r["status"] == "SUCCEEDED"]
        if not candidates:
            raise BridgeError(f"No succeeded {role} execution on this task")
        targets = [candidates[-1]]
    else:
        latest_by_role = {}
        for row in executions:
            if row["status"] == "SUCCEEDED":
                latest_by_role[row["role"]] = row
        targets = list(latest_by_role.values())
    reports = []
    for row in targets:
        already = bridge.store.one(
            "SELECT 1 AS x FROM evaluations WHERE source_execution_id=?", (row["id"],)
        )
        if already and not force:
            continue
        reports.append(
            evaluate_execution(
                bridge, project_id, task_id, row["id"], task_type=task_type, trigger=trigger
            )
        )
    return reports


def _decode(row):
    return {
        "schema_version": row["schema_version"],
        "evaluation_id": row["id"],
        "version": row["version"],
        "trigger": row["trigger"],
        "task_id": row["task_id"],
        "source_execution_id": row["source_execution_id"],
        "agent": row["agent"],
        "model": row["model"],
        "role": row["role"],
        "task_type": row["task_type"],
        "quality_score": row["quality_score"],
        "evidence_coverage": row["evidence_coverage"],
        "task_status": row["task_status"],
        "scoring_details": json.loads(row["scoring_details"]),
        "evidence": json.loads(row["evidence"]),
        "disagreements": json.loads(row["disagreements"]),
        "evaluation_timestamp": row["created"],
    }


def get_evaluations(bridge, project_id, task_id, *, role=None):
    clauses, args = ["project_id=?", "task_id=?"], [project_id, task_id]
    if role is not None:
        clauses.append("role=?")
        args.append(role)
    rows = bridge.store.all(
        f"SELECT * FROM evaluations WHERE {' AND '.join(clauses)} ORDER BY created, version", args
    )
    return [_decode(r) for r in rows]


def get_reliability(bridge, *, agent=None, role=None, task_type=None):
    """Reliability is tracked globally (across every project on this `bridge`/home),
    not per-project: an Agent/model's track record is a property of the model, not of
    one repository. reputation_events still keeps project_id/task_id for traceability."""
    return reputation.query(
        bridge.store, agent=agent, role=role, task_type=task_type,
        low_sample_threshold=reputation.DEFAULT_LOW_SAMPLE_THRESHOLD,
    )


_INSTANCE_ID = re.compile(r"\b[A-Za-z0-9_.-]+__[A-Za-z0-9_.-]+-\d+\b")


def _task_tokens(bridge, project_id, task_id):
    """Sum of CLI-reported tokens over a task's executions. ChatGPT web/MCP rounds never
    invoke a local CLI, so they have no usage record; `complete` says whether every round
    had one, so a partial total is never mistaken for the whole cost."""
    totals, rounds, with_usage = {}, 0, 0
    for row in bridge.get_task_usage(project_id, task_id):
        rounds += 1
        usage = (row["invocation"] or {}).get("usage") or {}
        if not usage.get("available"):
            continue
        with_usage += 1
        for key, value in (usage.get("tokens") or {}).items():
            totals[key] = totals.get(key, 0) + value
    return {
        "tokens": totals,
        # None, not 0, when no round reported usage: zero would read as "free".
        "tokens_total": (
            totals.get("input_tokens", 0) + totals.get("output_tokens", 0) if with_usage else None
        ),
        "usage_rounds": with_usage,
        "total_rounds": rounds,
        "usage_complete": rounds > 0 and with_usage == rounds,
    }


def export_cases(bridge, project_id, task_ids=None):
    """One validation case per task (see validation.py): the latest succeeded
    implementation round's diff, host test result and report, as the scorer sees them.
    `instance_id` is the SWE-bench id found in the task's requirement text, else the task
    id; ground truth is deliberately not included — it comes from the benchmark harness."""
    tasks = task_ids or [t["id"] for t in bridge.list_tasks(project_id)]
    cases = []
    for task_id in tasks:
        rounds = [
            r for r in bridge.get_task_result(project_id, task_id)
            if r["role"] in IMPLEMENTATION_ROLES and r["status"] == "SUCCEEDED"
        ]
        if not rounds:
            continue
        target = rounds[-1]
        bundle = evidence_collector.collect(bridge, project_id, task_id, target["role"], target["id"])
        task = bundle["task"]
        static = next((e for e in bundle["evidence"] if e["kind"] == "static_analysis"), None)
        test_result = bundle["test_result"] or {}
        match = _INSTANCE_ID.search(task.get("requirement") or "")
        cases.append(
            {
                "instance_id": match.group(0) if match else task_id,
                "task_id": task_id,
                "role": target["role"],
                "diff": bundle["diff_text"],
                "test_result": {
                    key: test_result.get(key)
                    for key in ("executed_by_host", "passed", "no_tests_collected", "stdout", "command")
                } if bundle["test_result"] else None,
                "report": bundle["report"],
                "acceptance": task.get("acceptance") or [],
                "static_findings": ((static or {}).get("payload") or {}).get("findings"),
                "review_report": bundle["review_report"],
                **_task_tokens(bridge, project_id, task_id),
            }
        )
    return cases
