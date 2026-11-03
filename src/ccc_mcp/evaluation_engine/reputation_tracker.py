"""Agent reliability per (agent, model, role, task_type), Beta-Bernoulli (brief #5).

Counts live in an append-only `reputation_events` ledger, one row per execution ever
counted — never a mutable counter — keyed UNIQUE on source_execution_id so
re-evaluating the same execution can never double-count (brief's test #7). reliability
is always `(success + 1) / (success + failure + 2)` recomputed from that ledger, the
same "derive it from an append-only table, never store a running total" pattern
storage.py already uses for meeting_clarifications' rounds_used.

Only PASSED/FAILED task_status outcomes are ever recorded — NEEDS_VERIFICATION and
INSUFFICIENT_EVIDENCE are explicitly never counted (brief's test #3/#6: "未驗證的判斷
不能當成成功或失敗"); evaluation_service enforces that before calling record_outcome.
"""

import json
import uuid

from ..adapters.storage import now
from .config import DEFAULT_LOW_SAMPLE_THRESHOLD
from .models import PASSED, TASK_FAILED

OUTCOMES = {"success", "failure"}


def outcome_for(task_status):
    """None for anything that must never move a reliability estimate."""
    if task_status == PASSED:
        return "success"
    if task_status == TASK_FAILED:
        return "failure"
    return None


def record_outcome(store, *, project_id, task_id, role, agent, model, task_type, execution_id, outcome):
    if outcome not in OUTCOMES:
        raise ValueError(f"Invalid reputation outcome: {outcome!r}")
    store.execute(
        "INSERT OR IGNORE INTO reputation_events"
        "(id,project_id,task_id,role,agent,model,task_type,source_execution_id,outcome,created) "
        "VALUES(?,?,?,?,?,?,?,?,?,?)",
        (
            "REP-" + uuid.uuid4().hex[:16], project_id, task_id, role, agent, model,
            task_type, execution_id, outcome, now(),
        ),
    )


def _reliability(success, failure):
    return (success + 1) / (success + failure + 2)


def _uncertainty(success, failure):
    """Standard deviation of Beta(success+1, failure+1) — a cheap, dependency-free
    stand-in for a calibrated confidence interval (no scipy here); see brief #5's
    "樣本數太少時應呈現不確定性"."""
    a, b = success + 1, failure + 1
    variance = (a * b) / ((a + b) ** 2 * (a + b + 1))
    return variance ** 0.5


def query(store, *, agent=None, role=None, task_type=None, low_sample_threshold=DEFAULT_LOW_SAMPLE_THRESHOLD):
    """Aggregated reliability rows, one per (agent, model, role, task_type) that has at
    least one recorded outcome; REVIEW rows also carry precision from the latest
    evaluation per source_execution_id (never double-counting a re-evaluated round)."""
    clauses, args = [], []
    for column, value in (("agent", agent), ("role", role), ("task_type", task_type)):
        if value is not None:
            clauses.append(f"{column}=?")
            args.append(value)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    rows = store.all(
        f"SELECT agent,model,role,task_type,outcome FROM reputation_events {where}", args
    )
    groups = {}
    for row in rows:
        key = (row["agent"], row["model"], row["role"], row["task_type"])
        bucket = groups.setdefault(key, {"success": 0, "failure": 0})
        bucket[row["outcome"]] += 1
    review_precision = _review_precision(store, agent=agent, task_type=task_type)
    results = []
    for (agent_name, model, role_name, task_type_name), bucket in groups.items():
        success, failure = bucket["success"], bucket["failure"]
        sample_count = success + failure
        results.append(
            {
                "agent": agent_name,
                "model": model,
                "role": role_name,
                "task_type": task_type_name,
                "verified_success": success,
                "verified_failure": failure,
                "sample_count": sample_count,
                "reliability_score": round(_reliability(success, failure), 4),
                "uncertainty": round(_uncertainty(success, failure), 4),
                "low_sample": sample_count < low_sample_threshold,
                # Never claimed as real Recall (brief #5: no ground-truth defect set
                # exists outside a benchmark harness) — precision only, and only for REVIEW.
                "review_precision": (
                    review_precision.get((agent_name, task_type_name)) if role_name == "REVIEW" else None
                ),
                "review_recall": None,
            }
        )
    return sorted(results, key=lambda r: (r["agent"], r["role"], r["task_type"] or ""))


def _review_precision(store, *, agent=None, task_type=None):
    """verified/(verified+false) from each REVIEW execution's LATEST evaluation version
    only — re-evaluating a round must not count its findings twice."""
    clauses, args = ["role='REVIEW'"], []
    if agent is not None:
        clauses.append("agent=?")
        args.append(agent)
    if task_type is not None:
        clauses.append("task_type=?")
        args.append(task_type)
    rows = store.all(
        "SELECT agent,task_type,source_execution_id,version,scoring_details FROM evaluations "
        f"WHERE {' AND '.join(clauses)}",
        args,
    )
    latest = {}
    for row in rows:
        key = row["source_execution_id"]
        if key not in latest or row["version"] > latest[key]["version"]:
            latest[key] = row
    tallies = {}
    for row in latest.values():
        details = json.loads(row["scoring_details"])
        sub = tallies.setdefault((row["agent"], row["task_type"]), {"verified": 0, "false": 0})
        sub["verified"] += details.get("features", {}).get("verified_review_findings", 0)
        sub["false"] += details.get("features", {}).get("false_review_findings", 0)
    precision = {}
    for key, tally in tallies.items():
        total = tally["verified"] + tally["false"]
        if total:
            precision[key] = round(tally["verified"] / total, 4)
    return precision
