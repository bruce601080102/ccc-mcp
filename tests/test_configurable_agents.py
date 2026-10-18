import json
import sys
from pathlib import Path

import pytest
from conftest import approve
from typer.testing import CliRunner

from ccc_mcp.agents import ClaudeCodeAdapter, CodexAdapter
from ccc_mcp.agents.base import run_process
from ccc_mcp.cli import app
from ccc_mcp.errors import AgentProcessError, BridgeError, PermissionDenied, VersionConflict
from ccc_mcp.models import AgentReport
from ccc_mcp.service import CCCMCP
from ccc_mcp.telemetry import audit_scope, usage_from_output
from ccc_mcp.worker import run_execution


def report(**kwargs):
    return AgentReport(
        summary="Observed source", next_action="PM refine context", **kwargs
    ).model_dump()


def dispatch(bridge, role="SA", **kwargs):
    return bridge.dispatch_agent(
        "demo",
        "TASK-0001",
        "auto",
        role,
        bridge.latest("demo")["version"],
        "Perform role",
        **kwargs,
    )


def test_role_settings_persist_and_resolve_per_execution(bridge, monkeypatch):
    monkeypatch.setenv("CCC_MCP_ENABLE_REAL_AGENTS", "1")
    bridge.configure_role("demo", "SA", "claude", "sonnet", "high", "chatgpt")
    bridge.create_task("demo", "Analyze")
    queued = dispatch(bridge)
    row = bridge.get_task_result("demo", "TASK-0001")[-1]
    assert row["id"] == queued["execution_id"]
    assert row["agent"] == "claude"
    assert row["options"]["agent_settings"]["model"] == "sonnet"
    bridge.configure_role("demo", "SA", "chatgpt")
    assert row["package"]["agent_settings"]["model"] == "sonnet"
    assert CCCMCP(bridge.home).get_project_settings("demo")["roles"]["SA"]["provider"] == "chatgpt"


@pytest.mark.parametrize("role", ["SA", "SD", "RD", "SD/RD", "REVIEW"])
@pytest.mark.parametrize("provider", ["codex", "claude", "chatgpt"])
def test_role_is_independent_of_provider(bridge, role, provider):
    bridge.configure_role("demo", role, provider)
    assert bridge.resolve_agent_settings("demo", role)["provider"] == provider


def test_invalid_config_and_chatgpt_model_not_faked(bridge):
    with pytest.raises(BridgeError):
        bridge.configure_role("demo", "SA", "claude", effort="ultra")
    with pytest.raises(BridgeError):
        bridge.configure_role("demo", "SA", "chatgpt", model="pretend-model")
    with pytest.raises(BridgeError):
        bridge.configure_role("demo", "SA", "codex", model="--unsafe")


def test_codex_implementation_model_effort_and_permissions(monkeypatch, tmp_path):
    monkeypatch.setattr("ccc_mcp.agents.codex.probe", lambda *args: "codex.exe")

    def invoke(argv, cwd, *args):
        assert argv[argv.index("--sandbox") + 1] == "workspace-write"
        assert argv[argv.index("--model") + 1] == "chosen-model"
        assert "model_reasoning_effort=high" in argv
        Path(argv[argv.index("--output-last-message") + 1]).write_text(json.dumps(report()))
        return '{"type":"turn.completed","usage":{"input_tokens":8,"output_tokens":3}}', ""

    monkeypatch.setattr("ccc_mcp.agents.codex.run_process", invoke)
    audit = {"provider": "codex"}
    with audit_scope(audit):
        CodexAdapter().run(
            {"role": "RD", "agent_settings": {"model": "chosen-model", "effort": "high"}},
            "Implement",
            tmp_path,
            10,
            lambda: False,
        )
    assert audit["usage"]["tokens"]["input_tokens"] == 8


@pytest.mark.parametrize("role", ["SA", "REVIEW"])
def test_claude_readonly_role_with_model_and_effort(monkeypatch, tmp_path, role):
    monkeypatch.setattr("ccc_mcp.agents.claude.probe", lambda *args: "claude.exe")

    def invoke(argv, *args):
        assert argv[argv.index("--permission-mode") + 1] == "plan"
        assert argv[argv.index("--tools") + 1] == "Read,Glob,Grep"
        assert argv[argv.index("--model") + 1] == "sonnet"
        assert argv[argv.index("--effort") + 1] == "high"
        return json.dumps({"structured_output": report()}), ""

    monkeypatch.setattr("ccc_mcp.agents.claude.run_process", invoke)
    ClaudeCodeAdapter().run(
        {"role": role, "agent_settings": {"model": "sonnet", "effort": "high"}},
        "Analyze",
        tmp_path,
        10,
        lambda: False,
    )


def test_chatgpt_readonly_and_pm_context_refinement_roundtrip(bridge):
    bridge.configure_role("demo", "SA", "chatgpt")
    bridge.create_task("demo", "Analyze")
    first = dispatch(bridge)
    assert first["status"] == "AWAITING_CHATGPT"
    with pytest.raises(PermissionDenied):
        bridge.write_task_file("demo", first["execution_id"], "app.py", "changed")
    bridge.submit_agent_report(
        "demo", first["execution_id"], report(context_updates=["Candidate fact"])
    )
    assert bridge.latest("demo")["version"] == 1
    updates = bridge.collect_context_updates("demo")
    original = next(item for item in updates["evidence"] if "CONTEXT-UPDATES.md" in item["path"])
    ref = {key: original[key] for key in ("path", "sha256")}
    patch = bridge.propose_context_patch(
        "demo",
        {
            "base_context_version": 1,
            "changes": {"summary": "PM refined shared context"},
            "source_task_ids": ["TASK-0001"],
            "source_document_refs": [ref],
            "update_reason": "PM reviewed candidate facts",
            "semantic_review_ack": True,
        },
    )
    bridge.commit_context_patch("demo", patch["patch_id"])
    second = dispatch(bridge)
    assert (
        second["context_package"]["content"]["shared_context"]["summary"]
        == "PM refined shared context"
    )
    assert second["context_package"]["context_version"] == 2


def test_chatgpt_implementation_requires_approval_and_uses_cas(bridge, human):
    bridge.configure_role("demo", "RD", "chatgpt")
    bridge.create_task("demo", "Implement")
    with pytest.raises(PermissionDenied):
        dispatch(bridge, "RD")
    approve(bridge)
    queued = dispatch(bridge, "RD")
    execution_id = queued["execution_id"]
    with pytest.raises(VersionConflict):
        bridge.write_task_file("demo", execution_id, "app.py", "changed")
    for path in ("../escape.py", ".git/config", ".ccc-mcp/context/PROJECT.md", ".env"):
        with pytest.raises(BridgeError):
            bridge.write_task_file("demo", execution_id, path, "data")
    receipt = bridge.write_task_file("demo", execution_id, "hello.py", 'print("Hello")\n')
    assert (
        bridge.read_workspace("demo", "hello.py", "worktree", execution_id)["sha256"]
        == receipt["sha256"]
    )
    bridge.submit_agent_report("demo", execution_id, report(context_updates=["Added hello.py"]))
    result = bridge.get_task_result("demo", "TASK-0001")[-1]["result"]
    assert result["invocation"]["cli_invoked"] is False
    assert result["invocation"]["usage"]["available"] is False
    assert "hello.py" in result["new_file_contents"]
    assert bridge.task("demo", "TASK-0001")["state"] == "AWAITING_PM_REVIEW"
    assert (
        bridge.read_workspace("demo", "hello.py", "worktree", execution_id)["sha256"]
        == receipt["sha256"]
    )
    with pytest.raises(PermissionDenied):
        bridge.write_task_file("demo", execution_id, "late.py", "late")


def test_chatgpt_stale_context_cannot_write(bridge, human):
    bridge.configure_role("demo", "RD", "chatgpt")
    bridge.create_task("demo", "Implement")
    approve(bridge)
    queued = dispatch(bridge, "RD")
    patch = bridge.propose_context_patch(
        "demo",
        {
            "base_context_version": 1,
            "changes": {"summary": "Changed"},
            "update_reason": "New facts",
            "semantic_review_ack": True,
        },
    )
    bridge.commit_context_patch("demo", patch["patch_id"])
    with pytest.raises(VersionConflict):
        bridge.write_task_file("demo", queued["execution_id"], "new.py", "pass")


def test_chatgpt_cancel_closes_execution_and_prevents_late_submission(bridge):
    bridge.configure_role("demo", "SA", "chatgpt")
    bridge.create_task("demo", "Analyze")
    queued = dispatch(bridge)
    bridge.cancel_task("demo", "TASK-0001")
    assert bridge.get_task_result("demo", "TASK-0001")[-1]["status"] == "CANCELLED"
    with pytest.raises(PermissionDenied):
        bridge.submit_agent_report("demo", queued["execution_id"], report())


def test_read_roots_are_explicit_bounded_and_revocable(bridge, tmp_path):
    external = tmp_path / "notes"
    external.mkdir()
    (external / "notes.txt").write_text("One\nTwo\nThree\n")
    (external / ".env").write_text("secret")
    with pytest.raises(PermissionDenied):
        bridge.read_workspace("demo", "notes.txt", "notes")
    bridge.configure_read_root("demo", "notes", external)
    assert (
        bridge.read_workspace("demo", "notes.txt", "notes", start_line=2, max_lines=1)["content"]
        == "Two"
    )
    assert bridge.list_workspace("demo", root_id="notes")["entries"] == [
        {"path": "notes.txt", "directory": False}
    ]
    with pytest.raises(PermissionDenied):
        bridge.read_workspace("demo", ".env", "notes")
    with pytest.raises(BridgeError):
        bridge.read_workspace("demo", "../notes.txt", "notes")
    bridge.revoke_read_root("demo", "notes")
    with pytest.raises(PermissionDenied):
        bridge.read_workspace("demo", "notes.txt", "notes")


def test_project_output_and_worktree_locations_are_configurable(bridge, human, tmp_path):
    projects = tmp_path / "output"
    bridge.set_projects_directory(projects)
    created = bridge.create_project("created")
    assert Path(created["root"]) == projects / "created"
    assert bridge.git_revision(created["root"]) != "UNBORN"
    with pytest.raises(BridgeError):
        bridge.create_project("created")
    custom = tmp_path / "worktrees"
    bridge.configure_worktree_dir("demo", custom)
    bridge.configure_role("demo", "RD", "chatgpt")
    bridge.create_task("demo", "Implement")
    approve(bridge)
    queued = dispatch(bridge, "RD")
    assert Path(queued["workspace"]) == custom / "demo/TASK-0001"
    with pytest.raises(BridgeError):
        bridge.configure_worktree_dir("demo", tmp_path / "moved")


@pytest.mark.parametrize("quota", [True, False])
def test_quota_fallback_queues_chatgpt_without_reapproval(bridge, human, monkeypatch, quota):
    monkeypatch.setenv("CCC_MCP_ENABLE_REAL_AGENTS", "1")
    bridge.configure_role("demo", "RD", "claude", quota_fallback="chatgpt")
    bridge.create_task("demo", "Implement")
    approve(bridge)

    def fail(*args):
        raise BridgeError("Usage limit reached" if quota else "Not logged in")

    monkeypatch.setattr("ccc_mcp.agents.claude.ClaudeCodeAdapter.run", fail)
    queued = dispatch(bridge, "RD")
    run_execution(bridge, queued["execution_id"])
    results = bridge.get_task_result("demo", "TASK-0001")
    assert len(results) == (2 if quota else 1)
    if quota:
        assert results[0]["result"]["failure_kind"] == "QUOTA_EXHAUSTED"
        assert results[1]["agent"] == "chatgpt"
        assert results[1]["options"]["fallback_from"] == results[0]["id"]
        assert bridge.bootstrap_project("demo")["pending_chatgpt_handoffs"]
        assert bridge.recover_workers("demo")["interrupted_executions"] == []


def test_cli_settings_commands_and_no_model_invocations(bridge):
    runner = CliRunner()
    prefix = ["--home", str(bridge.home), "--project", "demo"]
    result = runner.invoke(
        app, prefix + ["config", "role", "SA", "claude", "--model", "sonnet", "--effort", "high"]
    )
    assert result.exit_code == 0, result.output
    assert bridge.get_project_settings("demo")["roles"]["SA"]["model"] == "sonnet"


def test_native_audit_records_real_subprocess_without_model(tmp_path):
    audit = {"provider": "codex", "cli_invoked": False}
    output = json.dumps(
        {"type": "turn.completed", "usage": {"input_tokens": 12, "output_tokens": 4}}
    )
    with audit_scope(audit):
        run_process(
            [sys.executable, "-c", "print(" + repr(output) + ")"], tmp_path, "", 10, lambda: False
        )
    assert audit["cli_invoked"]
    assert audit["executable"] == sys.executable
    assert audit["pid"] > 0 and audit["exit_code"] == 0
    assert audit["started_at"] <= audit["finished_at"]
    assert audit["usage"]["tokens"] == {"input_tokens": 12, "output_tokens": 4}


def test_worker_persists_host_process_audit(bridge, monkeypatch):
    monkeypatch.setenv("CCC_MCP_ENABLE_REAL_AGENTS", "1")
    bridge.create_task("demo", "Analyze")

    def observed_process(self, package, instruction, cwd, timeout, cancelled):
        output = json.dumps(
            {"type": "turn.completed", "usage": {"input_tokens": 7, "output_tokens": 2}}
        )
        run_process(
            [sys.executable, "-c", "print(" + repr(output) + ")"], cwd, "", timeout, cancelled
        )
        return report(context_updates=["Observed source"])

    monkeypatch.setattr("ccc_mcp.agents.codex.CodexAdapter.run", observed_process)
    queued = dispatch(bridge)
    run_execution(bridge, queued["execution_id"])
    saved = bridge.get_task_result("demo", "TASK-0001")[-1]["result"]["invocation"]
    assert saved["cli_invoked"] and saved["pid"] > 0
    assert saved["usage"]["tokens"]["output_tokens"] == 2
    assert bridge.store.one(
        "SELECT sha256 FROM artifacts WHERE project_id=? AND path=?",
        (
            "demo",
            f".ccc-mcp/tasks/TASK-0001/executions/{queued['execution_id']}/INVOCATION.json",
        ),
    )


def test_quota_fallback_does_not_bypass_stale_context(bridge, monkeypatch):
    monkeypatch.setenv("CCC_MCP_ENABLE_REAL_AGENTS", "1")
    bridge.configure_role("demo", "SA", "codex", quota_fallback="chatgpt")
    bridge.create_task("demo", "Analyze")

    def stale(self, *args):
        patch = bridge.propose_context_patch(
            "demo",
            {
                "base_context_version": 1,
                "changes": {"summary": "New context"},
                "update_reason": "PM refined",
                "semantic_review_ack": True,
            },
        )
        bridge.commit_context_patch("demo", patch["patch_id"])
        raise AgentProcessError(1, '{"error":"insufficient_quota"}', "")

    monkeypatch.setattr("ccc_mcp.agents.codex.CodexAdapter.run", stale)
    queued = dispatch(bridge)
    run_execution(bridge, queued["execution_id"])
    results = bridge.get_task_result("demo", "TASK-0001")
    assert len(results) == 1 and results[0]["result"]["failure_kind"] == "QUOTA_EXHAUSTED"
    assert not bridge.pending_chatgpt_handoffs("demo")


def test_native_usage_is_not_taken_from_agent_report():
    output = json.dumps(
        {
            "usage": {"input_tokens": 5, "output_tokens": 3, "cache_read_input_tokens": 9},
            "modelUsage": {"actual-model": {}},
            "total_cost_usd": 0.02,
            "structured_output": {"claimed_tokens": 999999},
        }
    )
    usage = usage_from_output("claude", output)
    assert usage["tokens"]["input_tokens"] == 5
    assert usage["reported_models"] == ["actual-model"]
    assert usage["cli_estimated_cost_usd"] == 0.02
    assert usage_from_output("codex", "not metadata")["available"] is False


@pytest.mark.parametrize(
    "provider,output",
    [
        ("codex", '{"type":"turn.completed","usage":{"input_tokens":null,"output_tokens":-1}}'),
        ("claude", '{"usage":{"input_tokens":true,"output_tokens":null}}'),
    ],
)
def test_missing_or_invalid_tokens_are_not_filled_with_zero(provider, output):
    usage = usage_from_output(provider, output)
    assert usage["available"] is False
    assert "tokens" not in usage


def test_usage_summary_keeps_legacy_metadata_unknown(bridge):
    bridge.create_task("demo", "Analyze")
    queued = dispatch(bridge, adapter="fake")
    usage = bridge.get_task_usage("demo", "TASK-0001")
    assert usage[0]["execution_id"] == queued["execution_id"]
    assert usage[0]["invocation"]["cli_invoked"] is None
    assert usage[0]["invocation"]["usage"]["available"] is False


def test_pm_can_read_native_worktree_without_granting_write_access(bridge, human):
    bridge.create_task("demo", "Implement")
    approve(bridge)
    queued = dispatch(bridge, "RD", adapter="fake")
    run_execution(bridge, queued["execution_id"])
    assert (
        "original comment"
        in bridge.read_workspace("demo", "app.py", "worktree", queued["execution_id"])["content"]
    )
    with pytest.raises(PermissionDenied):
        bridge.write_task_file("demo", queued["execution_id"], "bad.py", "pass")
