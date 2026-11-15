"""P0A evidence chain: a minimal, hashed usage event that never holds prompts, code, or agent text.

Every event is UNVERIFIED unless the raw native output is available and reproduces it. Codex's
unconfirmed token semantics never become a single forced total.
"""

import copy
import hashlib
import json
from pathlib import Path

from ccc_mcp.infra.telemetry import (
    EVENT_SCHEMA,
    PARSER_VERSION,
    audit_scope,
    build_usage_event,
    canonical_usage,
    model_identity,
    record_output,
    usage_event_hash,
    usage_from_output,
    verify_usage_event,
)

FIXTURES = Path(__file__).parent / "fixtures" / "native_cli"
REAL = FIXTURES / "codex_clamp_rawcapture_2026-11-10.jsonl"
CLAUDE = FIXTURES / "claude_cache_synthetic.json"
CODEX_ZERO = FIXTURES / "codex_model_event_synthetic.jsonl"


def _event(provider, path, requested):
    stdout = path.read_text(encoding="utf-8")
    usage = usage_from_output(provider, stdout)
    canonical = canonical_usage(
        provider,
        usage.get("tokens"),
        turns=usage.get("turns", 0) or 1,
        last_turn=usage.get("last_turn"),
    )
    identity = model_identity(provider, requested, usage.get("reported_models"))
    event = build_usage_event(
        provider, requested, stdout, usage, canonical, identity, "run-test-0001"
    )
    return event, stdout


def test_event_carries_hashes_provenance_and_parser_version():
    event, stdout = _event("codex", REAL, None)
    assert event["schema"] == EVENT_SCHEMA
    assert event["run_id"] == "run-test-0001"
    assert event["provider"] == "codex"
    assert event["parser_version"] == PARSER_VERSION
    assert event["raw_output_sha256"] == hashlib.sha256(stdout.encode()).hexdigest()
    assert event["usage_event_sha256"] == usage_event_hash(event)
    assert event["usage_source"]["shape"] == "codex.turn_completed.sum"


def test_event_holds_no_prompt_code_or_agent_text():
    event, _ = _event("codex", REAL, None)
    text = json.dumps(event, ensure_ascii=False)
    for forbidden in ("mod.py", "clamp", "pytest", "upper bound", "thread_id", "01a1c646"):
        assert forbidden not in text, forbidden


def test_codex_unconfirmed_semantics_are_never_forced_into_one_total():
    event, _ = _event("codex", REAL, None)
    # cached input is OBSERVED only (one capture), so the total stays a range, not a single value
    assert event["canonical"]["total"] is None
    assert event["canonical"]["total_range"] == [66985, 66985]
    assert event["canonical"]["total_status"] == "OBSERVED_ONLY"


def test_codex_total_is_a_single_value_only_when_fully_determined():
    event, _ = _event("codex", CODEX_ZERO, "gpt-test-model")
    assert event["canonical"]["total"] == 15
    assert event["canonical"]["total_status"] == "DETERMINED"
    assert event["identity"]["status"] == "MATCH"


def test_claude_unconfirmed_cache_placement_is_a_range_not_a_total():
    event, _ = _event("claude", CLAUDE, "sonnet")
    assert event["canonical"]["total"] is None
    assert event["canonical"]["total_range"] == [8, 19]
    assert event["identity"]["status"] == "MATCH"
    assert event["identity"]["matched_as"] == "alias"


def test_identity_status_is_carried_as_match_mismatch_or_unknown():
    assert _event("codex", REAL, None)[0]["identity"]["status"] == "UNKNOWN"
    assert _event("claude", CLAUDE, "opus")[0]["identity"]["status"] == "MISMATCH"


def test_a_new_event_is_unverified_until_its_raw_output_is_archived():
    event, stdout = _event("codex", REAL, None)
    assert event["verification"] == {"status": "UNVERIFIED", "reason": "RAW_NOT_ARCHIVED"}
    assert verify_usage_event(event, None) == {"status": "UNVERIFIED", "reason": "RAW_NOT_ARCHIVED"}


def test_verified_only_when_raw_hash_and_reparse_both_agree():
    event, stdout = _event("codex", REAL, None)
    assert verify_usage_event(event, stdout) == {
        "status": "VERIFIED",
        "reason": "RAW_SHA_AND_TOKENS_REPRODUCED",
    }


def test_altered_raw_output_is_unverified_with_a_reason():
    event, stdout = _event("codex", REAL, None)
    assert (
        verify_usage_event(event, stdout.replace("66628", "66629"))["reason"] == "RAW_SHA_MISMATCH"
    )


def test_tampered_event_fails_its_own_hash_before_anything_else():
    event, stdout = _event("codex", REAL, None)
    tampered = copy.deepcopy(event)
    tampered["raw_fields"]["output_tokens"] = 1
    assert verify_usage_event(tampered, stdout) == {
        "status": "UNVERIFIED",
        "reason": "EVENT_HASH_MISMATCH",
    }


def test_a_stored_record_without_an_event_is_unverified_not_guessed():
    assert verify_usage_event(None, None) == {"status": "UNVERIFIED", "reason": "NO_EVENT"}
    assert verify_usage_event({"schema": "other"}, None) == {
        "status": "UNVERIFIED",
        "reason": "NO_EVENT",
    }


def test_suspect_event_is_withheld_not_stored(tmp_path):
    # A model name that looks like a live key must never be written into evidence.
    envelope = json.dumps(
        {
            "usage": {
                "input_tokens": 1,
                "output_tokens": 1,
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
            },
            "modelUsage": {"sk-live-abcdefghijklmnop": {}},
        }
    )
    usage = usage_from_output("claude", envelope)
    canonical = canonical_usage("claude", usage["tokens"])
    identity = model_identity("claude", None, usage["reported_models"])
    event = build_usage_event("claude", None, envelope, usage, canonical, identity, "run-x")
    assert event == {
        "schema": EVENT_SCHEMA,
        "run_id": "run-x",
        "provider": "claude",
        "status": "WITHHELD",
        "reason": "SECRET_SUSPECT_IN_EVENT",
        "verification": {"status": "UNVERIFIED", "reason": "WITHHELD"},
    }
    assert "sk-live" not in json.dumps(event)


def test_record_output_attaches_one_stable_run_id_to_the_audit():
    audit = {"provider": "codex", "requested": {"model": None}}
    stdout = REAL.read_text(encoding="utf-8")
    with audit_scope(audit):
        record_output("codex", stdout)
        first = audit["run_id"]
        record_output("codex", stdout)  # the adapter records the same output again
    assert audit["run_id"] == first
    assert audit["usage_evidence"]["run_id"] == first
    assert audit["usage_evidence"]["verification"]["reason"] == "RAW_NOT_ARCHIVED"


def test_legacy_fields_are_unchanged_by_the_evidence_chain():
    stdout = REAL.read_text(encoding="utf-8")
    usage = usage_from_output("codex", stdout)
    assert usage["tokens"]["input_tokens"] == 66628
    assert usage["tokens"]["cached_input_tokens"] == 61696
    assert usage["tokens"]["output_tokens"] == 357
