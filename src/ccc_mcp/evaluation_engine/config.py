"""Evaluation Engine settings: off by default (see DEFAULT_EVALUATION_ENGINE), wired
into project settings exactly like meeting_mode/test_command (core/configuration.py
imports _validated_evaluation_engine and DEFAULT_EVALUATION_ENGINE from here)."""

from ..infra.errors import BridgeError

# Below this many verified outcomes, a reliability estimate is still mostly the Beta
# prior, not real signal — reputation_tracker flags it rather than hiding the number.
DEFAULT_LOW_SAMPLE_THRESHOLD = 5

DEFAULT_EVALUATION_ENGINE = {
    "enabled": False,
    # Default off even once enabled: an operator must opt into the hook that runs after
    # every SA/SD/RD/REVIEW execution (see core/evaluation.py's maybe_auto_evaluate).
    # Manual evaluate_task calls work the moment "enabled" is true.
    "auto_evaluate": False,
    "low_sample_threshold": DEFAULT_LOW_SAMPLE_THRESHOLD,
}

# role -> {dimension: weight}, weights summing to 1.0 within each role. A dimension
# absent from a role's dict is "not applicable to this role" (never enters that role's
# evidence_coverage denominator) — different from "applicable but this round has no
# evidence for it" (dimension present, sub-score None; still counts against coverage).
# See quality_scorer.py for what each dimension measures per role (REVIEW overloads the
# five generic names onto its own defect-identification concerns; documented there).
DEFAULT_ROLE_WEIGHTS = {
    "SA": {"requirement": 0.70, "review": 0.30},
    "SD": {"test": 0.40, "requirement": 0.25, "code_quality": 0.15, "security": 0.10, "review": 0.10},
    "RD": {"test": 0.40, "requirement": 0.25, "code_quality": 0.15, "security": 0.10, "review": 0.10},
    "SD/RD": {
        "test": 0.40, "requirement": 0.25, "code_quality": 0.15, "security": 0.10, "review": 0.10,
    },
    "REVIEW": {"test": 0.40, "requirement": 0.30, "review": 0.30},
}
_DIMENSIONS = {"test", "requirement", "code_quality", "security", "review"}


def _validated_weights(role, value):
    if not isinstance(value, dict) or not value:
        raise BridgeError(f"evaluation_engine.weights.{role} must be a non-empty object")
    total = 0.0
    validated = {}
    for dimension, weight in value.items():
        if dimension not in _DIMENSIONS:
            raise BridgeError(f"Unknown evaluation dimension: {dimension!r}")
        if not isinstance(weight, (int, float)) or isinstance(weight, bool) or weight < 0:
            raise BridgeError(f"evaluation_engine.weights.{role}.{dimension} must be >= 0")
        validated[dimension] = float(weight)
        total += weight
    if not 0.99 <= total <= 1.01:
        raise BridgeError(f"evaluation_engine.weights.{role} must sum to 1.0 (got {total})")
    return validated


def validated_role_weights(value):
    """Partial per-role overrides merged over DEFAULT_ROLE_WEIGHTS; a role not present
    in `value` keeps its default profile untouched."""
    if value is None:
        return dict(DEFAULT_ROLE_WEIGHTS)
    if not isinstance(value, dict):
        raise BridgeError("evaluation_engine.weights must be an object")
    merged = dict(DEFAULT_ROLE_WEIGHTS)
    for role, weights in value.items():
        if role not in DEFAULT_ROLE_WEIGHTS:
            raise BridgeError(f"Unknown evaluation role: {role!r}")
        merged[role] = _validated_weights(role, weights)
    return merged


def validated_evaluation_engine(value):
    if not isinstance(value, dict):
        raise BridgeError("evaluation_engine must be an object")
    enabled = value.get("enabled", False)
    auto_evaluate = value.get("auto_evaluate", False)
    low_sample_threshold = value.get("low_sample_threshold", DEFAULT_LOW_SAMPLE_THRESHOLD)
    weights = value.get("weights")
    if not isinstance(enabled, bool):
        raise BridgeError("evaluation_engine.enabled must be a boolean")
    if not isinstance(auto_evaluate, bool):
        raise BridgeError("evaluation_engine.auto_evaluate must be a boolean")
    if not isinstance(low_sample_threshold, int) or isinstance(low_sample_threshold, bool) or (
        low_sample_threshold < 1
    ):
        raise BridgeError("evaluation_engine.low_sample_threshold must be an integer >= 1")
    validated = {
        "enabled": enabled,
        "auto_evaluate": auto_evaluate,
        "low_sample_threshold": low_sample_threshold,
    }
    if weights is not None:
        validated["weights"] = validated_role_weights(weights)
    return validated
