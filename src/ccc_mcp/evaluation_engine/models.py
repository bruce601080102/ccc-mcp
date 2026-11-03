"""Shared vocabulary for the Evaluation Engine. Plain dicts + string constants, not
pydantic models: nothing here is parsed from untrusted Agent-structured output (that's
AgentReport's job in core/models.py) — it is all host-computed, so there is no
LLM-output-shape to validate against.
"""

# Report schema published to CLI/MCP/UI/other Agents; bump only for incompatible
# changes to the evaluations table's JSON columns (mirrors models.PROTOCOL_VERSION).
SCHEMA_VERSION = "1.0"

# Evidence.status: how much to trust one collected fact.
VERIFIED = "VERIFIED"      # host-observed (git diff, pytest exit code, ruff output)
CLAIMED = "CLAIMED"        # only the Agent's own self-report says so
UNVERIFIED = "UNVERIFIED"  # relevant, but no verification was ever attempted
FAILED = "FAILED"          # a verification attempt itself failed/errored
EVIDENCE_STATUSES = {VERIFIED, CLAIMED, UNVERIFIED, FAILED}

# EvaluationReport.task_status: separate from quality_score and from the task's own
# TaskHub.STATES — this never mutates the real task state machine (see core/evaluation.py).
PASSED = "PASSED"
TASK_FAILED = "FAILED"
NEEDS_VERIFICATION = "NEEDS_VERIFICATION"
INSUFFICIENT_EVIDENCE = "INSUFFICIENT_EVIDENCE"
TASK_STATUSES = {PASSED, TASK_FAILED, NEEDS_VERIFICATION, INSUFFICIENT_EVIDENCE}

# Disagreement.resolution_status
CONFIRMED = "CONFIRMED"
DISMISSED = "DISMISSED"
RESOLUTION_STATUSES = {CONFIRMED, DISMISSED, NEEDS_VERIFICATION}


def evidence_item(
    *, kind, status, project_id, task_id, role, agent, source, collected_at, payload, notes=None
):
    """One objective fact plus how it was obtained. `payload` is kind-specific raw data
    (e.g. the pytest summary, or the ruff findings list) so every number downstream can
    be traced back to this record (see feature_extractor.py)."""
    if status not in EVIDENCE_STATUSES:
        raise ValueError(f"Invalid evidence status: {status!r}")
    return {
        "kind": kind,
        "status": status,
        "project_id": project_id,
        "task_id": task_id,
        "role": role,
        "agent": agent,
        "source": source,
        "collected_at": collected_at,
        "payload": payload,
        "notes": notes,
    }


def disagreement(*, kind, description, roles, evidence_refs, resolution_status):
    if resolution_status not in RESOLUTION_STATUSES:
        raise ValueError(f"Invalid resolution_status: {resolution_status!r}")
    return {
        "kind": kind,
        "description": description,
        "roles": roles,
        "evidence_refs": evidence_refs,
        "resolution_status": resolution_status,
    }
