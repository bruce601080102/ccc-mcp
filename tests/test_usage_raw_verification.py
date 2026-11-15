"""P0A: a stored usage record is VERIFIED only when linked to, and reproduced from, its raw output.

Everything else is UNVERIFIED with a reason. Historical evidence files are read, never changed.
"""

import hashlib
import json
from pathlib import Path

import pytest

from ccc_mcp.infra.telemetry import usage_from_output, verify_usage_against_raw

FIXTURES = Path(__file__).parent / "fixtures" / "native_cli"
RAW = FIXTURES / "codex_clamp_rawcapture_2026-11-10.jsonl"
EVIDENCE_DIR = Path(__file__).parents[1] / "benchmarks/toy-agents/smoke-test-evidence"
SMOKE_RECORD = EVIDENCE_DIR / "2026-11-10-clamp-b_retry.jsonl"
SMOKE_RAW = EVIDENCE_DIR / "2026-11-10-clamp-raw-codex-stream.jsonl"


def _raw_text():
    return RAW.read_text(encoding="utf-8")


def test_sha_linked_and_reproduced_is_verified():
    raw = _raw_text()
    stored = usage_from_output("codex", raw)
    sha = hashlib.sha256(raw.encode()).hexdigest()
    result = verify_usage_against_raw("codex", stored, sha, raw)
    assert result == {"status": "VERIFIED", "reason": "SHA_LINKED_AND_TOKENS_EQUAL"}


def test_sha_mismatch_is_unverified_even_if_tokens_look_equal():
    raw = _raw_text()
    stored = usage_from_output("codex", raw)
    result = verify_usage_against_raw("codex", stored, "0" * 64, raw)
    assert result["status"] == "UNVERIFIED"
    assert result["reason"] == "RAW_SHA_MISMATCH"


def test_raw_not_archived_is_unverified():
    stored = usage_from_output("codex", _raw_text())
    result = verify_usage_against_raw("codex", stored, "0" * 64, None)
    assert result == {"status": "UNVERIFIED", "reason": "RAW_NOT_ARCHIVED"}


def test_legacy_record_without_link_is_unverified_even_when_tokens_equal():
    raw = _raw_text()
    stored = usage_from_output("codex", raw)
    result = verify_usage_against_raw("codex", stored, None, raw)
    assert result == {"status": "UNVERIFIED", "reason": "LEGACY_RECORD_NO_LINK"}


def test_no_stored_usage_is_unverified():
    result = verify_usage_against_raw("codex", {"available": False}, None, _raw_text())
    assert result == {"status": "UNVERIFIED", "reason": "NO_STORED_USAGE"}


def test_unparseable_raw_is_unverified():
    stored = usage_from_output("codex", _raw_text())
    result = verify_usage_against_raw("codex", stored, None, "not a stream")
    assert result["status"] == "UNVERIFIED"
    assert result["reason"] == "RAW_NOT_PARSEABLE"


@pytest.mark.skipif(
    not SMOKE_RECORD.exists() or not SMOKE_RAW.exists(), reason="ACQE evidence absent"
)
def test_smoke_record_disagrees_with_its_same_named_raw_stream_and_is_flagged():
    """Regression for P0A finding F3: the stored record and the raw stream do not agree.

    The record has no sha256 link to the raw stream, so the honest status is UNVERIFIED,
    with the differing fields listed. The historical files are not modified by this test.
    """
    record = json.loads(SMOKE_RECORD.read_text(encoding="utf-8").splitlines()[0])
    stored = {"available": True, "tokens": record["tokens"]}
    result = verify_usage_against_raw("codex", stored, None, SMOKE_RAW.read_text(encoding="utf-8"))
    assert result["status"] == "UNVERIFIED"
    assert result["reason"] == "RAW_TOKENS_DIFFER"
    diffs = result["differences"]
    assert diffs["input_tokens"] == {"stored": 66370, "raw": 66628}
    assert diffs["cached_input_tokens"] == {"stored": 61568, "raw": 61696}
    assert diffs["output_tokens"] == {"stored": 289, "raw": 357}
