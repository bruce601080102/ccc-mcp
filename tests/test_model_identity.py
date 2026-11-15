"""P0A: model identity is MATCH, MISMATCH or UNKNOWN. An unverifiable identity is never MATCH."""

import pytest

from ccc_mcp.infra.telemetry import model_identity


@pytest.mark.parametrize(
    "requested, reported, status, reason",
    [
        ("claude-sonnet-4-5", ["claude-sonnet-4-5"], "MATCH", "EXACT"),
        ("Claude-Sonnet-4-5", ["claude-sonnet-4-5"], "MATCH", "EXACT"),
        (
            "claude-sonnet-4-5",
            ["claude-sonnet-4-5-20250929"],
            "MATCH",
            "DATE_SUFFIX_ONLY_DIFFERENCE",
        ),
        ("sonnet", ["claude-sonnet-test"], "MATCH", "ALIAS_FAMILY"),
        ("sonnet", ["claude-sonnet-a", "claude-sonnet-b"], "UNKNOWN", "AMBIGUOUS_ALIAS"),
        ("opus", ["claude-sonnet-test"], "MISMATCH", "ALIAS_FAMILY_ABSENT"),
        ("claude-sonnet-4", ["claude-sonnet-4-5"], "MISMATCH", "REQUESTED_NOT_REPORTED"),
        ("gpt-5", ["gpt-5-mini"], "MISMATCH", "REQUESTED_NOT_REPORTED"),
        (None, ["claude-sonnet-test"], "UNKNOWN", "REQUEST_UNSPECIFIED"),
        ("   ", ["claude-sonnet-test"], "UNKNOWN", "REQUEST_UNSPECIFIED"),
        ("sonnet", [], "UNKNOWN", "CLI_REPORTED_NO_MODEL"),
    ],
)
def test_identity_rules(requested, reported, status, reason):
    result = model_identity("claude", requested, reported)
    assert result["status"] == status
    assert result["reason"] == reason


def test_extra_reported_models_are_auxiliary_and_do_not_cause_mismatch():
    result = model_identity("claude", "sonnet", ["claude-sonnet-test", "claude-haiku-test"])
    assert result["status"] == "MATCH"
    assert result["auxiliary"] == ["claude-haiku-test"]


def test_codex_without_a_reported_model_is_unknown_never_match():
    result = model_identity("codex", "gpt-5", [])
    assert result["status"] == "UNKNOWN"
    assert result["reason"] == "CLI_REPORTED_NO_MODEL"


def test_default_model_request_is_unverifiable_not_a_match():
    result = model_identity("claude", None, ["claude-sonnet-test"])
    assert result["status"] == "UNKNOWN"
    assert result["reason"] == "REQUEST_UNSPECIFIED"
