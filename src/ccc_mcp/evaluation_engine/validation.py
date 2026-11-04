"""Check the scorer against ground truth: does a higher quality_score really go with a
change that actually resolved the task?

The scorer only ever sees what ccc-mcp could observe in its own run (the diff, the
host-executed local test output, the agent's report). SWE-bench's verdict comes from
hidden tests the agent never saw. Comparing the two is the only honest way to learn
whether "tests are green locally" predicts "really resolved" (the astropy__astropy-13398
incident is exactly a case where it did not).

Pure functions over plain dicts: no store, no project, no LLM. A case is one JSON object
per line:

    {"instance_id": "astropy__astropy-13398",   # required
     "resolved": false,                          # ground truth; or supply a labels file
     "role": "SD/RD",                            # default
     "diff": "diff --git a/... (unified diff the agent produced)",
     "test_result": {"executed_by_host": true, "passed": true, "stdout": "3 passed in 0.1s"},
     "report": {"summary": "...", "next_action": "...", "findings": []},
     "acceptance": ["..."],                      # optional
     "static_findings": [{"code": "E501"}]}      # optional, ruff-style
"""

import json
from pathlib import Path

from ..infra.errors import BridgeError
from . import disagreement_detector, feature_extractor, quality_scorer
from .config import validated_role_weights
from .models import PASSED

_THRESHOLDS = (50, 60, 70, 80, 90)


def _bundle(case):
    diff = case.get("diff")
    static = case.get("static_findings")
    return {
        "evidence": [
            {
                "kind": "static_analysis",
                "status": "VERIFIED" if static is not None else "UNVERIFIED",
                "payload": {"findings": static},
            }
        ],
        "task": {"acceptance": case.get("acceptance") or []},
        "report": case.get("report") or {},
        "test_result": case.get("test_result"),
        "diff_text": diff or None,
        "prior_report": None,
        "review_report": case.get("review_report"),
        "later_diffs": [],
    }


def score_case(case, weights=None):
    """Score one case with exactly the production feature/score code path."""
    role = case.get("role") or "SD/RD"
    weights = weights or validated_role_weights(None)
    if role not in weights:
        raise BridgeError(f"{case.get('instance_id')}: unknown role {role!r}")
    bundle = _bundle(case)
    detected = disagreement_detector.detect(bundle)
    features = feature_extractor.extract(bundle, detected["review_tally"], detected["disagreements"])
    scored = quality_scorer.score(role, features, weights)
    return {
        "instance_id": case["instance_id"],
        "resolved": case["resolved"],
        "quality_score": scored["quality_score"],
        "evidence_coverage": scored["evidence_coverage"],
        "task_status": scored["task_status"],
        "only_self_authored_tests_passed": features["only_self_authored_tests_passed"],
    }


def load_cases(path):
    cases = []
    for number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            case = json.loads(line)
        except ValueError as exc:
            raise BridgeError(f"{path}:{number}: not valid JSON") from exc
        if not isinstance(case, dict) or not isinstance(case.get("instance_id"), str):
            raise BridgeError(f"{path}:{number}: every case needs a string instance_id")
        cases.append(case)
    if not cases:
        raise BridgeError(f"{path}: no cases found")
    return cases


def load_labels(path):
    """Ground truth from a SWE-bench harness report: {"resolved_ids": [...],
    "unresolved_ids": [...]} (also accepts `resolved_instances` style lists)."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BridgeError(f"{path}: cannot read labels file") from exc
    if not isinstance(data, dict):
        raise BridgeError(f"{path}: labels file must be a JSON object")
    resolved = set(data.get("resolved_ids") or [])
    unresolved = set(data.get("unresolved_ids") or []) | set(data.get("error_ids") or [])
    if not resolved and not unresolved:
        raise BridgeError(f"{path}: expected resolved_ids / unresolved_ids lists")
    return {**{i: True for i in resolved}, **{i: False for i in unresolved - resolved}}


def auroc(positive, negative):
    """Probability that a random resolved case outscores a random unresolved one
    (ties count half). 0.5 = no better than a coin flip; None when a class is empty."""
    if not positive or not negative:
        return None
    wins = sum(
        1.0 if p > n else 0.5 if p == n else 0.0 for p in positive for n in negative
    )
    return round(wins / (len(positive) * len(negative)), 4)


def _mean(values):
    return round(sum(values) / len(values), 2) if values else None


def _threshold_row(scored, threshold):
    predicted = [row["quality_score"] >= threshold for row in scored]
    truth = [row["resolved"] for row in scored]
    tp = sum(p and t for p, t in zip(predicted, truth))
    fp = sum(p and not t for p, t in zip(predicted, truth))
    fn = sum(not p and t for p, t in zip(predicted, truth))
    tn = len(scored) - tp - fp - fn
    return {
        "threshold": threshold,
        "accuracy": round((tp + tn) / len(scored), 4),
        "precision": round(tp / (tp + fp), 4) if tp + fp else None,
        "recall": round(tp / (tp + fn), 4) if tp + fn else None,
        "false_green": fp,
    }


def validate(cases, labels=None, weights=None):
    """Score every case and compare with ground truth. Returns a summary dict with the
    per-case rows, AUROC, per-threshold accuracy, and how often a status of PASSED was
    wrong ("false green" — the costly failure for a verification tool)."""
    weights = weights or validated_role_weights(None)
    rows = []
    for case in cases:
        case = dict(case)
        if labels is not None and case["instance_id"] in labels:
            case["resolved"] = labels[case["instance_id"]]
        if not isinstance(case.get("resolved"), bool):
            raise BridgeError(f"{case['instance_id']}: no ground-truth label (resolved)")
        rows.append(score_case(case, weights))

    scored = [row for row in rows if row["quality_score"] is not None]
    resolved = [row["quality_score"] for row in scored if row["resolved"]]
    unresolved = [row["quality_score"] for row in scored if not row["resolved"]]
    passed = [row for row in rows if row["task_status"] == PASSED]
    false_green = [row["instance_id"] for row in passed if not row["resolved"]]
    missed = [
        row["instance_id"] for row in rows if row["resolved"] and row["task_status"] != PASSED
    ]
    area = auroc(resolved, unresolved)
    return {
        "cases": len(rows),
        "resolved": sum(row["resolved"] for row in rows),
        "unscored": len(rows) - len(scored),
        "auroc": area,
        "mean_score_resolved": _mean(resolved),
        "mean_score_unresolved": _mean(unresolved),
        "thresholds": [_threshold_row(scored, t) for t in _THRESHOLDS] if scored else [],
        "status_passed": len(passed),
        "false_green": false_green,
        "false_green_rate": round(len(false_green) / len(passed), 4) if passed else None,
        "resolved_not_passed": missed,
        "verdict": _verdict(area, len(scored), len(resolved), len(unresolved), false_green, passed),
        "rows": rows,
    }


def _verdict(area, scored, resolved, unresolved, false_green, passed):
    if scored < 30 or min(resolved, unresolved) < 5:
        return (
            f"Only {scored} scored case(s) ({resolved} resolved / {unresolved} unresolved): "
            "too few to say anything statistically; treat the numbers as a smoke test."
        )
    if area is None:
        return "Both classes are needed to measure ranking quality."
    if area < 0.6:
        text = f"AUROC {area}: the score barely separates resolved from unresolved work."
    elif area < 0.75:
        text = f"AUROC {area}: a weak-to-moderate signal; useful as a hint, not as a gate."
    else:
        text = f"AUROC {area}: the score ranks resolved work above unresolved work."
    if passed and false_green:
        text += f" {len(false_green)} of {len(passed)} PASSED verdicts were not resolved."
    return text
