"""P0A: canonical usage normalisation, double-count guards and golden fixtures.

No model is called. The golden expectations were written by hand (see
tests/fixtures/native_cli/README.md); the code under test is never used to produce them.
"""

import hashlib
import json
from pathlib import Path

import pytest

from ccc_mcp.infra.telemetry import (
    NORMALIZATION_VERSION,
    SEMANTIC_STATUS,
    canonical_usage,
    model_identity,
    usage_from_output,
)

FIXTURES = Path(__file__).parent / "fixtures" / "native_cli"
ORIGINAL_EVIDENCE = (
    Path(__file__).parents[1]
    / "benchmarks/toy-agents/smoke-test-evidence/2026-11-10-clamp-raw-codex-stream.jsonl"
)
CASES = sorted(path.name[: -len(".expected.json")] for path in FIXTURES.glob("*.expected.json"))


def _stdout_file(case):
    return next(
        path for path in FIXTURES.glob(case + ".*") if not path.name.endswith(".expected.json")
    )


def _run_normalise(provider, stdout):
    usage = usage_from_output(provider, stdout)
    canonical = canonical_usage(
        provider,
        usage.get("tokens"),
        turns=usage.get("turns", 0) or 1,
        last_turn=usage.get("last_turn"),
    )
    return usage, canonical


@pytest.mark.parametrize("case", CASES)
def test_golden_fixture_matches_hand_written_expectation(case):
    expected = json.loads((FIXTURES / f"{case}.expected.json").read_text(encoding="utf-8"))
    provider = expected["provider"]
    usage, canonical = _run_normalise(provider, _stdout_file(case).read_text(encoding="utf-8"))

    for key, value in expected["usage"].items():
        assert usage.get(key) == value, (case, "usage", key)
    for key, value in expected["canonical"].items():
        assert canonical[key] == value, (case, "canonical", key, canonical[key])
    identity = model_identity(provider, expected["requested"], usage.get("reported_models"))
    for key, value in expected["identity"].items():
        assert identity[key] == value, (case, "identity", key, identity)


def test_real_capture_is_byte_identical_to_original_evidence():
    expected = json.loads(
        (FIXTURES / "codex_clamp_rawcapture_2026-11-10.expected.json").read_text(encoding="utf-8")
    )
    copy = FIXTURES / "codex_clamp_rawcapture_2026-11-10.jsonl"
    digest = hashlib.sha256(copy.read_bytes()).hexdigest()
    assert digest == expected["provenance"]["sha256"]
    if ORIGINAL_EVIDENCE.exists():
        assert hashlib.sha256(ORIGINAL_EVIDENCE.read_bytes()).hexdigest() == digest


def test_components_sum_to_total_once_and_naive_field_sum_is_rejected():
    stdout = _stdout_file("codex_cache_write_synthetic").read_text(encoding="utf-8")
    _, canonical = _run_normalise("codex", stdout)
    fields = canonical["fields"]
    assert sum(fields.values()) == canonical["total_assumed"] == 1300
    assert canonical["components_sum"] == 1300
    naive_field_sum = 1000 + 600 + 100 + 300 + 120  # what a careless total would add up
    assert naive_field_sum != canonical["total_assumed"]


def test_reasoning_and_cache_are_never_added_on_top_of_their_container():
    raw = {
        "input_tokens": 200,
        "cached_input_tokens": 50,
        "cache_write_input_tokens": 0,
        "output_tokens": 100,
        "reasoning_output_tokens": 40,
    }
    canonical = canonical_usage("codex", raw)
    assert canonical["total_assumed"] == 300  # input + output only
    assert canonical["fields"]["output_visible"] == 60
    assert canonical["fields"]["input_uncached"] == 150


def test_cache_read_larger_than_input_is_a_conflict_not_a_number():
    canonical = canonical_usage(
        "codex",
        {
            "input_tokens": 100,
            "cached_input_tokens": 150,
            "cache_write_input_tokens": 0,
            "output_tokens": 10,
            "reasoning_output_tokens": 0,
        },
    )
    assert canonical["status"] == "CONFLICT"
    assert canonical["fields"]["input_uncached"] is None
    assert canonical["total_status"] == "UNVERIFIED"
    assert "CONFLICT_CACHE_READ_EXCEEDS_INPUT" in canonical["flags"]


def test_reasoning_larger_than_output_is_a_conflict():
    canonical = canonical_usage(
        "codex",
        {
            "input_tokens": 100,
            "cached_input_tokens": 0,
            "cache_write_input_tokens": 0,
            "output_tokens": 10,
            "reasoning_output_tokens": 40,
        },
    )
    assert canonical["status"] == "CONFLICT"
    assert canonical["fields"]["output_visible"] is None


def test_absent_fields_stay_unknown_never_zero():
    canonical = canonical_usage("codex", {"input_tokens": 40, "output_tokens": 10})
    assert canonical["fields"]["cache_write"] is None
    assert canonical["fields"]["reasoning"] is None
    assert canonical["total_range"] == [50, None]
    assert canonical["total_status"] == "UNVERIFIED"


def test_unknown_reasoning_placement_gives_a_range_not_a_single_verified_value():
    canonical = canonical_usage(
        "codex",
        {
            "input_tokens": 1000,
            "cached_input_tokens": 600,
            "cache_write_input_tokens": 0,
            "output_tokens": 300,
            "reasoning_output_tokens": 120,
        },
    )
    assert canonical["total_range"] == [1300, 1420]
    assert canonical["total_status"] == "UNVERIFIED"
    assert "reasoning_inside_output_tokens" in canonical["unknown_semantics"]


def test_multi_turn_codex_usage_is_unverified_with_a_range():
    stdout = _stdout_file("codex_multi_turn_synthetic").read_text(encoding="utf-8")
    usage, canonical = _run_normalise("codex", stdout)
    assert usage["turns"] == 2
    assert usage["tokens"]["input_tokens"] == 1200  # legacy summed value is kept as-is
    assert canonical["total_range"] == [780, 1330]
    assert "TURN_SEMANTICS_UNKNOWN" in canonical["flags"]


def test_claude_zero_cache_is_determined_and_nonzero_cache_is_unverified():
    zero = canonical_usage(
        "claude",
        {
            "input_tokens": 12,
            "output_tokens": 4,
            "cache_read_input_tokens": 0,
            "cache_creation_input_tokens": 0,
        },
    )
    assert zero["total_status"] == "DETERMINED" and zero["total_assumed"] == 16

    cached = canonical_usage(
        "claude",
        {
            "input_tokens": 5,
            "output_tokens": 3,
            "cache_read_input_tokens": 9,
            "cache_creation_input_tokens": 2,
        },
    )
    assert cached["total_range"] == [8, 19]
    assert cached["total_status"] == "UNVERIFIED"
    assert "cache_outside_input_tokens" in cached["unknown_semantics"]


def test_legacy_tokens_are_preserved_and_legacy_total_formula_is_unchanged():
    stdout = _stdout_file("codex_clamp_rawcapture_2026-11-10").read_text(encoding="utf-8")
    usage = usage_from_output("codex", stdout)
    tokens = usage["tokens"]
    assert tokens["input_tokens"] == 66628
    assert tokens["cached_input_tokens"] == 61696
    assert tokens["output_tokens"] == 357
    assert tokens["reasoning_output_tokens"] == 0
    # The legacy total used by evaluation_service and the ACQE harness is input + output.
    assert tokens["input_tokens"] + tokens["output_tokens"] == 66985


def test_semantics_table_claims_nothing_verified_yet():
    values = {value for table in SEMANTIC_STATUS.values() for value in table.values()}
    assert values <= {"OBSERVED", "UNKNOWN", "NOT_REPORTED"}
    assert NORMALIZATION_VERSION == 1


def test_unsupported_provider_is_unavailable_not_guessed():
    canonical = canonical_usage("chatgpt", {"input_tokens": 1, "output_tokens": 1})
    assert canonical["status"] == "UNAVAILABLE"
    assert canonical["total_assumed"] is None
    assert canonical["flags"] == ["UNSUPPORTED_PROVIDER"]


def test_malformed_lines_are_skipped_and_do_not_hide_valid_usage(tmp_path):
    stream = tmp_path / "mixed.jsonl"
    stream.write_text(
        "not json at all\n"
        '{"type":"turn.completed","usage":{"input_tokens":7,"cached_input_tokens":0,'
        '"cache_write_input_tokens":0,"output_tokens":3,"reasoning_output_tokens":0}}\n',
        encoding="utf-8",
    )
    usage = usage_from_output("codex", stream.read_text(encoding="utf-8"))
    assert usage["available"] is True
    assert usage["tokens"]["input_tokens"] == 7
