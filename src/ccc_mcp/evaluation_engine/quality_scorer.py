"""Evidence-weighted quality score, kept strictly separate from task_status (brief
#3: "品質分數與任務完成狀態必須分開"). quality_score answers "how good was what we
could verify"; task_status answers "did this round actually meet acceptance", via a
handful of hard rules that quality_score's weighted average can never override — a
high weighted score must never paper over a failed acceptance test or a missing
security review.

REVIEW's role profile overloads the five generic dimension names onto its own
concerns, since "write code"/"security" don't apply to a read-only role:
  test       -> did REVIEW's own "no revision needed" conclusion hold up against the
                host-executed test command (ccc-mcp already treats host evidence as
                authoritative over any Agent's self-report; see tasks.py's
                _review_reverted_due_to_host_evidence, which this mirrors)
  requirement-> coverage of the acceptance checklist in REVIEW's own written findings
  review     -> precision of REVIEW's findings (verified / (verified + false),
                from disagreement_detector's structural confirmation)
  code_quality/security are omitted from REVIEW's weight profile entirely (not
  "missing evidence" — simply not applicable to a role that writes no code).
"""

from .models import INSUFFICIENT_EVIDENCE, NEEDS_VERIFICATION, PASSED, TASK_FAILED

IMPLEMENTATION_ROLES = {"SD", "RD", "SD/RD"}
_EVIDENCE_COVERAGE_FLOOR = 0.60


def _clamp(value):
    return max(0.0, min(100.0, value))


def _test_score(role, features):
    if role == "REVIEW":
        needs_revision = features["review_needs_revision"]
        if needs_revision is None or not features["host_tests_executed"]:
            return None
        mismatch = needs_revision is False and (
            features["host_test_passed"] is False or features["host_no_tests_collected"]
        )
        return 0.0 if mismatch else 100.0
    rate = features["test_pass_rate"]
    return None if rate is None else _clamp(100 * rate)


def _requirement_score(features):
    coverage = features["requirement_coverage"]
    return None if coverage is None else _clamp(100 * coverage)


def _code_quality_score(features):
    count = features["static_analysis_issue_count"]
    return None if count is None else _clamp(100 - 10 * count)


def _security_score(features):
    count = features["security_issue_count"]
    return None if count is None else (100.0 if count == 0 else _clamp(100 - 50 * count))


def _review_score(role, features):
    if role == "SA":
        # No CONFIRMED disagreement type implicates SA yet (see disagreement_detector);
        # returning a number here would be exactly the fabricated sub-score brief #2
        # forbids. Left as a documented extension point, not a silent zero.
        return None
    if role == "REVIEW":
        verified = features["verified_review_findings"]
        false = features["false_review_findings"]
        if verified + false == 0:
            # An empty finding list cannot establish review precision.
            return None
        return _clamp(100 * verified / (verified + false))
    needs_revision = features["review_needs_revision"]
    if needs_revision is None:
        return None
    return 40.0 if needs_revision else 100.0


def _sub_scores(role, features):
    return {
        "test": _test_score(role, features),
        "requirement": _requirement_score(features),
        "code_quality": _code_quality_score(features),
        "security": _security_score(features),
        "review": _review_score(role, features),
    }


def _task_status(role, features, evidence_coverage):
    if features["host_runtime_blocked"]:
        return INSUFFICIENT_EVIDENCE
    if role in IMPLEMENTATION_ROLES:
        # Acceptance scenario #2: PASS_TO_PASS succeeding en masse never offsets
        # FAIL_TO_PASS failing outright.
        if features["fail_to_pass_rate"] is not None and features["fail_to_pass_rate"] < 1.0:
            return TASK_FAILED
        if features["security_issue_count"]:
            return TASK_FAILED
        if not features["host_tests_executed"]:
            # Acceptance scenario #3: no test evidence -> never PASSED.
            return NEEDS_VERIFICATION
        if features["host_no_tests_collected"]:
            return NEEDS_VERIFICATION
        if features["host_test_passed"] is False:
            return TASK_FAILED
        status = PASSED
    elif role == "REVIEW":
        needs_revision = features["review_needs_revision"]
        if needs_revision is None:
            return INSUFFICIENT_EVIDENCE
        if not features["host_tests_executed"]:
            return NEEDS_VERIFICATION
        mismatch = needs_revision is False and (
            features["host_test_passed"] is False or features["host_no_tests_collected"]
        )
        status = TASK_FAILED if mismatch else PASSED
    elif role == "SA":
        coverage = features["requirement_coverage"]
        if coverage is None:
            return INSUFFICIENT_EVIDENCE
        status = PASSED if coverage >= 0.8 else NEEDS_VERIFICATION
    else:
        return INSUFFICIENT_EVIDENCE
    if status != TASK_FAILED and evidence_coverage < _EVIDENCE_COVERAGE_FLOOR:
        # A hard failure is still a real failure even with thin evidence elsewhere;
        # anything else this thin is not safe to call PASSED or even NEEDS_VERIFICATION.
        return INSUFFICIENT_EVIDENCE
    return status


def score(role, features, weights_by_role):
    """Returns {"quality_score": float|None, "evidence_coverage": float,
    "task_status": str, "sub_scores": {...}, "weights": {...}}."""
    weights = weights_by_role.get(role, {})
    sub_scores = _sub_scores(role, features)
    available = {dim: sub_scores[dim] for dim in weights if sub_scores[dim] is not None}
    total_weight = sum(weights.values())
    evidence_coverage = (
        sum(weights[dim] for dim in available) / total_weight if total_weight else 0.0
    )
    if available:
        available_weight = sum(weights[dim] for dim in available)
        quality_score = round(
            sum(sub_scores[dim] * weights[dim] for dim in available) / available_weight, 2
        )
    else:
        quality_score = None
    return {
        "quality_score": quality_score,
        "evidence_coverage": round(evidence_coverage, 4),
        "task_status": _task_status(role, features, evidence_coverage),
        "sub_scores": sub_scores,
        "weights": weights,
    }
