"""P0A: Fake native CLI through the real subprocess path (run_process / record_output).

No real Codex or Claude binary is started. The fake writes a fixture file to stdout and exits
with a configured code. These tests check the audit that the production code would store.
"""

import hashlib
import sys
from pathlib import Path

import pytest

from ccc_mcp.adapters.agents.base import run_process
from ccc_mcp.infra.errors import AgentProcessError, AgentTimeout
from ccc_mcp.infra.telemetry import audit_scope, is_quota_error

ROOT = Path(__file__).parent
FAKE = ROOT / "fixtures" / "fake_cli" / "fake_native_cli.py"
FIXTURES = ROOT / "fixtures" / "native_cli"


def _never_cancelled():
    return False


def _run(
    audit, monkeypatch, tmp_path, *, fixture=None, exit_code=0, stderr="", sleep=0, timeout=20
):
    if fixture is None:
        monkeypatch.delenv("FAKE_NATIVE_FIXTURE", raising=False)
    else:
        monkeypatch.setenv("FAKE_NATIVE_FIXTURE", str(fixture))
    monkeypatch.setenv("FAKE_NATIVE_EXIT", str(exit_code))
    monkeypatch.setenv("FAKE_NATIVE_STDERR", stderr)
    monkeypatch.setenv("FAKE_NATIVE_SLEEP", str(sleep))
    with audit_scope(audit):
        return run_process([sys.executable, str(FAKE)], tmp_path, "", timeout, _never_cancelled)


def test_codex_fake_run_stores_canonical_usage_and_both_time_layers(monkeypatch, tmp_path):
    audit = {"provider": "codex", "requested": {"model": None}}
    fixture = FIXTURES / "codex_clamp_rawcapture_2026-11-10.jsonl"
    stdout, _stderr = _run(audit, monkeypatch, tmp_path, fixture=fixture)

    assert stdout == fixture.read_text(encoding="utf-8")
    usage = audit["usage"]
    assert usage["available"] is True
    assert usage["canonical"]["total_assumed"] == 66985
    assert usage["canonical"]["total_status"] == "OBSERVED_ONLY"
    assert usage["identity"]["status"] == "UNKNOWN"
    assert audit["stdout_sha256"] == hashlib.sha256(stdout.encode()).hexdigest()
    # Process-layer timing is recorded separately from execution-layer timing.
    assert audit["exit_code"] == 0
    assert audit["started_at"] and audit["finished_at"]
    assert audit["cli_invoked"] is True


def test_claude_fake_run_matches_alias_and_keeps_cache_unverified(monkeypatch, tmp_path):
    audit = {"provider": "claude", "requested": {"model": "sonnet"}}
    _run(audit, monkeypatch, tmp_path, fixture=FIXTURES / "claude_cache_synthetic.json")

    usage = audit["usage"]
    assert usage["identity"]["status"] == "MATCH"
    assert usage["identity"]["auxiliary"] == ["claude-haiku-test"]
    assert usage["canonical"]["total_range"] == [8, 19]
    assert usage["canonical"]["total_status"] == "UNVERIFIED"


def test_reported_model_different_from_request_is_mismatch(monkeypatch, tmp_path):
    audit = {"provider": "claude", "requested": {"model": "sonnet"}}
    _run(audit, monkeypatch, tmp_path, fixture=FIXTURES / "claude_no_cache_synthetic.json")
    assert audit["usage"]["identity"]["status"] == "MISMATCH"


def test_failed_exit_still_records_usage_that_the_cli_printed(monkeypatch, tmp_path):
    audit = {"provider": "codex", "requested": {"model": None}}
    fixture = FIXTURES / "codex_clamp_rawcapture_2026-11-10.jsonl"
    with pytest.raises(AgentProcessError):
        _run(audit, monkeypatch, tmp_path, fixture=fixture, exit_code=1, stderr="boom")
    assert audit["exit_code"] == 1
    assert audit["usage"]["available"] is True
    assert audit["usage"]["canonical"]["total_assumed"] == 66985


def test_quota_failure_has_unknown_tokens_not_zero(monkeypatch, tmp_path):
    audit = {"provider": "claude", "requested": {"model": "sonnet"}}
    with pytest.raises(AgentProcessError) as raised:
        _run(audit, monkeypatch, tmp_path, exit_code=1, stderr="error: insufficient_quota")
    assert is_quota_error(raised.value.stderr)
    usage = audit["usage"]
    assert usage["available"] is False
    assert usage["canonical"]["status"] == "UNAVAILABLE"
    assert usage["canonical"]["total_assumed"] is None


def test_timeout_leaves_no_usage_record_so_coverage_is_visibly_incomplete(monkeypatch, tmp_path):
    audit = {"provider": "codex", "requested": {"model": None}}
    with pytest.raises(AgentTimeout):
        _run(audit, monkeypatch, tmp_path, sleep=5, timeout=0.5)
    assert "usage" not in audit
    assert "stdout_sha256" not in audit


def test_non_json_claude_output_is_unavailable_and_identity_unknown(monkeypatch, tmp_path):
    audit = {"provider": "claude", "requested": {"model": "sonnet"}}
    _run(audit, monkeypatch, tmp_path, fixture=FIXTURES / "claude_not_json_synthetic.txt")
    assert audit["usage"]["available"] is False
    assert audit["usage"]["identity"]["status"] == "UNKNOWN"
