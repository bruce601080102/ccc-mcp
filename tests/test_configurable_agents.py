import json
import sys
from pathlib import Path

import pytest
from conftest import approve
from typer.testing import CliRunner

from ccc_mcp.adapters.agents import ClaudeCodeAdapter, CodexAdapter
from ccc_mcp.adapters.agents.base import run_process
from ccc_mcp.core.models import AgentReport
from ccc_mcp.infra.errors import AgentProcessError, BridgeError, PermissionDenied, VersionConflict
from ccc_mcp.infra.telemetry import audit_scope, is_quota_error, usage_from_output
from ccc_mcp.interfaces.cli import app
from ccc_mcp.service import CCCMCP
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


def test_manual_settings_json_edits_control_next_dispatch(bridge, human):
    bridge.authorize_project_local("demo")
    path = Path(bridge.project("demo")["root"]) / ".ccc-mcp/settings.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["roles"]["SA"]["provider"] = "chatgpt"
    document["roles"]["SA"]["quota_fallback"] = None
    document["roles"]["SD/RD"]["provider"] = "chatgpt"
    document["language"] = "ch"
    path.write_text(json.dumps(document, ensure_ascii=False, indent=2), encoding="utf-8")

    started = bridge.start_project_request("demo", "Analyze app.py")
    execution = bridge.store.one(
        "SELECT agent,role,adapter,options FROM agent_executions WHERE id=?",
        (started["sa_execution"]["execution_id"],),
    )
    assert (execution["agent"], execution["role"], execution["adapter"]) == (
        "chatgpt", "SA", "interactive"
    )
    saved = json.loads(
        bridge.store.one("SELECT content FROM project_settings WHERE project_id='demo'")["content"]
    )
    assert saved["roles"]["SD/RD"]["provider"] == "chatgpt"
    assert saved["language"] == "ch"
    assert json.loads(execution["options"])["agent_settings"]["provider"] == "chatgpt"


def test_manual_settings_json_edits_survive_later_cli_change(bridge, human):
    bridge.authorize_project_local("demo")
    path = Path(bridge.project("demo")["root"]) / ".ccc-mcp/settings.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["roles"]["SD/RD"]["provider"] = "chatgpt"
    path.write_text(json.dumps(document), encoding="utf-8")

    bridge.configure_role("demo", "REVIEW", "codex", effort="high")
    assert bridge.get_project_settings("demo")["roles"]["SD/RD"]["provider"] == "chatgpt"
    assert json.loads(path.read_text(encoding="utf-8"))["roles"]["SD/RD"]["provider"] == "chatgpt"


def test_config_show_imports_manual_settings_json_edit(bridge, human):
    bridge.authorize_project_local("demo")
    path = Path(bridge.project("demo")["root"]) / ".ccc-mcp/settings.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["roles"]["SD/RD"]["provider"] = "chatgpt"
    path.write_text(json.dumps(document), encoding="utf-8")

    result = CliRunner().invoke(
        app, ["--home", str(bridge.home), "--project", "demo", "config", "show"]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["roles"]["SD/RD"]["provider"] == "chatgpt"


def test_manual_settings_import_waits_until_outer_transaction_finishes(bridge, human):
    bridge.authorize_project_local("demo")
    path = Path(bridge.project("demo")["root"]) / ".ccc-mcp/settings.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["roles"]["SD/RD"]["provider"] = "chatgpt"
    path.write_text(json.dumps(document), encoding="utf-8")

    with bridge.store.transaction():
        assert bridge.get_project_settings("demo")["roles"]["SD/RD"]["provider"] == "claude"
    assert bridge.get_project_settings("demo")["roles"]["SD/RD"]["provider"] == "chatgpt"


@pytest.mark.parametrize(
    "content",
    [
        "{bad json",
        '{"roles":{"SD/RD":{"provider":"unknown"}}}',
        '{"roles":{"SD/RD":{"provider":"chatgpt","model":"sonnet"}}}',
    ],
)
def test_invalid_manual_settings_json_fails_closed(bridge, human, content):
    bridge.authorize_project_local("demo")
    path = Path(bridge.project("demo")["root"]) / ".ccc-mcp/settings.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(BridgeError):
        bridge.resolve_agent_settings("demo", "SD/RD")
    stored = json.loads(
        bridge.store.one("SELECT content FROM project_settings WHERE project_id='demo'")["content"]
    )
    assert stored["roles"]["SD/RD"]["provider"] == "claude"


@pytest.mark.parametrize("role", ["SA", "SD", "RD", "SD/RD", "REVIEW"])
@pytest.mark.parametrize("provider", ["codex", "claude", "chatgpt"])
def test_role_is_independent_of_provider(bridge, role, provider):
    bridge.configure_role("demo", role, provider)
    assert bridge.resolve_agent_settings("demo", role)["provider"] == provider


def test_configure_test_command_persists_as_argv(bridge):
    assert bridge.get_project_settings("demo")["test_command"] is None
    bridge.configure_test_command("demo", [sys.executable, "-m", "pytest", "-q"])
    assert bridge.get_project_settings("demo")["test_command"] == [
        sys.executable, "-m", "pytest", "-q",
    ]
    bridge.configure_test_command("demo", None)
    assert bridge.get_project_settings("demo")["test_command"] is None


@pytest.mark.parametrize("command", ["pytest -q", [], [123], [""], ["a" * 5000]])
def test_configure_test_command_rejects_non_argv(bridge, command):
    with pytest.raises(BridgeError):
        bridge.configure_test_command("demo", command)


def test_config_test_command_cli_round_trip(bridge):
    runner = CliRunner()
    set_result = runner.invoke(
        app,
        ["--home", str(bridge.home), "--project", "demo", "config", "test-command", "pytest -q"],
    )
    assert set_result.exit_code == 0, set_result.output
    assert bridge.get_project_settings("demo")["test_command"] == ["pytest", "-q"]

    clear_result = runner.invoke(
        app, ["--home", str(bridge.home), "--project", "demo", "config", "test-command", "--clear"]
    )
    assert clear_result.exit_code == 0, clear_result.output
    assert bridge.get_project_settings("demo")["test_command"] is None


def test_provider_overview_separates_editable_from_read_only(bridge, human):
    bridge.authorize_project_local("demo")
    overview = bridge.provider_overview("demo")

    assert set(overview["editable"]) == {
        "roles", "language", "test_command", "context_token_budget", "meeting_mode", "runtime",
    }
    assert overview["read_only_status"]["project_access"]["mode"] == "read-write"
    assert overview["available_providers"]["codex"]
    assert overview["available_providers"]["claude"]
    assert overview["available_providers"]["chatgpt"] == []
    assert any("SD/RD" in note for note in overview["notes"])


def test_config_providers_cli_reports_current_roles(bridge):
    result = CliRunner().invoke(
        app, ["--home", str(bridge.home), "--project", "demo", "config", "providers"]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["editable"]["roles"]["SA"]["provider"] == "codex"
    assert "read_only_status" in payload


def native_clis(monkeypatch, *installed):
    """Deterministically control which native CLIs `best-model` sees as installed,
    instead of depending on whatever happens to be on the machine actually running
    the tests (this repo's own dev box has Claude Code but not Codex, which is
    exactly the scenario this logic exists to handle)."""

    def fake(executable):
        if executable in installed:
            return f"/fake/bin/{executable}"
        raise BridgeError(f"Native {executable} executable unavailable; install the official CLI")

    monkeypatch.setattr("ccc_mcp.core.configuration.native_executable", fake)


def test_default_profile_is_best_model_when_both_native_clis_are_installed(bridge, monkeypatch):
    native_clis(monkeypatch, "codex", "claude")
    assert bridge.public_project_settings("demo")["profile"] == "best-model"
    assert bridge.provider_overview("demo")["profile"] == "best-model"


def test_configure_profile_chatgpt_only_clears_other_provider_settings(bridge):
    """Regression (real-world smoke test): switching to chatgpt-only must leave every
    role slot — including standalone SD/RD, which advance_project never auto-dispatches
    but which `config show` still displays — reading as ChatGPT, not a provider mix."""
    bridge.configure_role("demo", "SA", "codex", effort="high", quota_fallback="chatgpt")
    bridge.configure_role("demo", "SD", "claude", model="sonnet", effort="high")
    bridge.configure_role("demo", "RD", "claude", model="sonnet", effort="high")
    bridge.configure_role("demo", "SD/RD", "claude", model="sonnet", effort="high")
    bridge.configure_role("demo", "REVIEW", "codex", effort="xhigh")

    result = bridge.configure_profile("demo", "chatgpt-only")

    for role in ("SA", "SD", "RD", "SD/RD", "REVIEW"):
        assert result["roles"][role] == {
            "provider": "chatgpt", "model": None, "effort": None, "quota_fallback": None,
        }
    assert bridge.public_project_settings("demo")["profile"] == "chatgpt-only"
    assert bridge.provider_overview("demo")["profile"] == "chatgpt-only"


def test_configure_profile_best_model_restores_the_split_when_both_clis_are_installed(
    bridge, monkeypatch
):
    native_clis(monkeypatch, "codex", "claude")
    bridge.configure_profile("demo", "chatgpt-only")

    result = bridge.configure_profile("demo", "best-model")

    assert result["roles"]["SA"] == {
        "provider": "codex", "model": None, "effort": None, "quota_fallback": None,
    }
    for role in ("SD", "RD", "SD/RD"):
        assert result["roles"][role] == {
            "provider": "claude", "model": None, "effort": None, "quota_fallback": None,
        }
    assert result["roles"]["REVIEW"] == {
        "provider": "codex", "model": None, "effort": None, "quota_fallback": None,
    }
    assert bridge.public_project_settings("demo")["profile"] == "best-model"


@pytest.mark.parametrize("installed", ["codex", "claude"])
def test_configure_profile_best_model_uses_the_one_installed_cli_for_every_role(
    bridge, monkeypatch, installed
):
    """Regression (user feedback): "best-model" is only meaningful relative to what is
    actually installed on this machine — someone with only Claude Code (no Codex), or
    only Codex (no Claude Code), must get that single CLI assigned to every role, not
    a fixed split that silently leaves half the roles pointed at a CLI that isn't
    there."""
    native_clis(monkeypatch, installed)
    bridge.configure_profile("demo", "chatgpt-only")

    result = bridge.configure_profile("demo", "best-model")

    for role in ("SA", "SD", "RD", "SD/RD", "REVIEW"):
        assert result["roles"][role] == {
            "provider": installed, "model": None, "effort": None, "quota_fallback": None,
        }
    assert bridge.public_project_settings("demo")["profile"] == "best-model"


def test_configure_profile_best_model_rejects_when_no_native_cli_is_installed(
    bridge, monkeypatch
):
    native_clis(monkeypatch)
    with pytest.raises(BridgeError, match="Neither the Codex nor the Claude Code CLI"):
        bridge.configure_profile("demo", "best-model")


def test_detect_profile_reports_custom_for_a_mixed_combination(bridge, monkeypatch):
    native_clis(monkeypatch, "codex", "claude")
    bridge.configure_role("demo", "SA", "chatgpt")
    bridge.configure_role("demo", "SD/RD", "claude")
    bridge.configure_role("demo", "REVIEW", "codex")
    assert bridge.public_project_settings("demo")["profile"] == "custom"
    assert bridge.provider_overview("demo")["profile"] == "custom"


def test_detect_profile_reports_custom_when_best_model_combination_is_not_runnable(
    bridge, monkeypatch
):
    """The default, untouched role config (codex/claude split) must not be labeled
    "best-model" on a machine where one of those CLIs isn't actually installed — that
    combination can't really run here, so calling it "best" would be misleading."""
    native_clis(monkeypatch, "claude")
    assert bridge.public_project_settings("demo")["profile"] == "custom"


def test_configure_profile_rejects_unknown_name(bridge):
    with pytest.raises(BridgeError):
        bridge.configure_profile("demo", "fastest")


def test_config_profile_cli_round_trip(bridge, monkeypatch):
    native_clis(monkeypatch, "codex", "claude")
    runner = CliRunner()
    prefix = ["--home", str(bridge.home), "--project", "demo"]
    result = runner.invoke(app, prefix + ["config", "profile", "chatgpt-only"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["roles"]["SA"]["provider"] == "chatgpt"
    assert payload["roles"]["REVIEW"]["provider"] == "chatgpt"
    assert bridge.public_project_settings("demo")["profile"] == "chatgpt-only"

    back = runner.invoke(app, prefix + ["config", "profile", "best-model"])
    assert back.exit_code == 0, back.output
    assert bridge.public_project_settings("demo")["profile"] == "best-model"


def test_config_meeting_mode_cli_escalation_round_trip(bridge):
    runner = CliRunner()
    prefix = ["--home", str(bridge.home), "--project", "demo"]
    result = runner.invoke(
        app,
        prefix + [
            "config", "meeting-mode", "on",
            "--min-rounds", "1", "--max-rounds", "3", "--escalation", "auto-pm",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["meeting_mode"] == {
        "enabled": True, "min_rounds": 1, "max_rounds": 3, "escalation": "auto_pm",
    }
    assert bridge.get_project_settings("demo")["meeting_mode"]["escalation"] == "auto_pm"

    rejected = runner.invoke(
        app, prefix + ["config", "meeting-mode", "on", "--escalation", "auto_pm"]
    )
    assert rejected.exit_code != 0


def test_create_project_with_profile_applies_it_in_one_step(bridge, tmp_path):
    created = bridge.create_project("fresh", tmp_path / "fresh", profile="chatgpt-only")
    assert created["profile"] == "chatgpt-only"
    assert bridge.public_project_settings("fresh")["profile"] == "chatgpt-only"
    for role in ("SA", "SD", "RD", "SD/RD", "REVIEW"):
        assert bridge.get_project_settings("fresh")["roles"][role]["provider"] == "chatgpt"


def test_create_project_without_profile_keeps_default_roles(bridge, tmp_path):
    created = bridge.create_project("fresh", tmp_path / "fresh")
    assert "profile" not in created
    assert bridge.get_project_settings("fresh")["roles"]["SA"]["provider"] == "codex"


def test_create_project_rejects_unknown_profile_before_creating_anything(bridge, tmp_path):
    target = tmp_path / "fresh"
    with pytest.raises(BridgeError):
        bridge.create_project("fresh", target, profile="fastest")
    assert not target.exists()
    assert bridge.store.one("SELECT id FROM projects WHERE id='fresh'") is None


def test_create_project_rejects_best_model_profile_when_no_native_cli_installed(
    bridge, tmp_path, monkeypatch
):
    native_clis(monkeypatch)
    target = tmp_path / "fresh"
    with pytest.raises(BridgeError, match="Neither the Codex nor the Claude Code CLI"):
        bridge.create_project("fresh", target, profile="best-model")
    assert not target.exists()
    assert bridge.store.one("SELECT id FROM projects WHERE id='fresh'") is None


def test_create_project_cli_accepts_profile_option(bridge, tmp_path, monkeypatch):
    native_clis(monkeypatch, "codex", "claude")
    result = CliRunner().invoke(
        app,
        [
            "--home", str(bridge.home), "project", "create", "fresh",
            "--path", str(tmp_path / "fresh"), "--profile", "chatgpt-only",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["profile"] == "chatgpt-only"
    assert bridge.public_project_settings("fresh")["profile"] == "chatgpt-only"


def test_doctor_reports_tunnel_client_and_workflow_guidance(bridge):
    result = CliRunner().invoke(app, ["--home", str(bridge.home), "doctor"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert "available" in payload["tunnel_client"]
    assert "chatgpt_only" in payload["workflows"]
    assert "Codex" in payload["workflows"]["chatgpt_only"] or "codex" in payload["workflows"]["chatgpt_only"]
    assert "best_model" in payload["workflows"]


@pytest.mark.parametrize("role", ["SA", "SD", "RD", "SD/RD", "REVIEW"])
def test_role_prompt_preview_matches_what_dispatch_would_actually_send(bridge, role):
    """Regression (Feature 2): the preview must be built from the same functions the
    real adapters use, not a second hand-written copy that can drift."""
    from ccc_mcp.adapters.agents import CodexAdapter
    from ccc_mcp.adapters.agents.base import operator_policy
    from ccc_mcp.core.context import context_policy

    preview = bridge.role_prompt_preview("demo", role)

    assert preview["role"] == role
    assert preview["adapter_operator_policy"] == operator_policy("zh-TW")
    assert preview["context_handoff_policy"] == context_policy("zh-TW")
    if preview["provider"] == "codex":
        assert preview["provider_extra_instruction"] == CodexAdapter.EXTRA_INSTRUCTION
    else:
        assert preview["provider_extra_instruction"] is None


def test_role_prompt_preview_rejects_unknown_role(bridge):
    with pytest.raises(BridgeError):
        bridge.role_prompt_preview("demo", "QA")


def test_role_prompt_preview_tool_access_follows_cli_lang_but_payload_does_not(bridge, monkeypatch):
    """Regression: tool_access/note are this command's own description, so they follow
    CCC_MCP_LANG; adapter_operator_policy/context_handoff_policy are the real payload
    sent to the Agent, so they follow the project's `language` setting instead — never
    CCC_MCP_LANG, or this preview would stop matching what a real dispatch actually sends."""
    monkeypatch.delenv("CCC_MCP_LANG", raising=False)
    zh_preview = bridge.role_prompt_preview("demo", "SA")
    assert "唯讀" in zh_preview["tool_access"]
    assert "zh-TW" in zh_preview["note"] or "繁體" in zh_preview["note"]

    monkeypatch.setenv("CCC_MCP_LANG", "en")
    en_preview = bridge.role_prompt_preview("demo", "SA")
    assert "read-only" in en_preview["tool_access"]
    assert "CCC_MCP_LANG" in en_preview["note"]
    # The actual payload must be identical regardless of CCC_MCP_LANG.
    assert en_preview["adapter_operator_policy"] == zh_preview["adapter_operator_policy"]
    assert en_preview["context_handoff_policy"] == zh_preview["context_handoff_policy"]


def test_config_prompt_cli_is_read_only_and_does_not_dispatch(bridge):
    result = CliRunner().invoke(
        app, ["--home", str(bridge.home), "--project", "demo", "config", "prompt", "SA"]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["provider"] == "codex"
    assert "tool_access" in payload
    assert bridge.list_tasks("demo") == []


def test_configure_context_token_budget_persists_and_validates(bridge):
    assert bridge.get_project_settings("demo")["context_token_budget"] is None
    bridge.configure_context_token_budget("demo", 50000)
    assert bridge.get_project_settings("demo")["context_token_budget"] == 50000
    bridge.configure_context_token_budget("demo", None)
    assert bridge.get_project_settings("demo")["context_token_budget"] is None
    with pytest.raises(BridgeError):
        bridge.configure_context_token_budget("demo", 10)
    with pytest.raises(BridgeError):
        bridge.configure_context_token_budget("demo", "50000")


def test_dispatch_agent_passes_configured_budget_into_the_real_handoff(bridge):
    """Regression (P1-4): dispatch_agent previously called build_handoff with no
    token_budget at all, so a configured budget never applied to real dispatches —
    only to the MCP build_handoff/get_context tools when a caller passed one by hand."""
    bridge.configure_context_token_budget("demo", 1000)
    task_id = bridge.create_task("demo", "需求")["id"]
    for i in range(20):
        bridge.store.execute(
            "INSERT INTO decisions VALUES(?,?,?,?,?,?,?,?,?)",
            (
                f"DEC-test-{i:02d}", "demo", task_id, f"decision {i}", "reason", "tester",
                "Approved", bridge.latest("demo")["version"], "2026-01-01T00:00:00+00:00",
            ),
        )
    dispatch(bridge, "SA", adapter="fake")
    package = bridge.get_task_result("demo", task_id)[-1]["package"]
    assert package["token_budget"] == 1000
    assert package["budget_omitted"], "the configured budget never reached build_handoff"


def test_manual_settings_json_edit_validates_test_command(bridge, human):
    bridge.authorize_project_local("demo")
    path = Path(bridge.project("demo")["root"]) / ".ccc-mcp/settings.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    document["test_command"] = ["pytest", "-q"]
    path.write_text(json.dumps(document), encoding="utf-8")
    assert bridge.get_project_settings("demo")["test_command"] == ["pytest", "-q"]

    document["test_command"] = "pytest -q"
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(BridgeError):
        bridge.get_project_settings("demo")


def test_invalid_config_and_chatgpt_model_not_faked(bridge):
    with pytest.raises(BridgeError):
        bridge.configure_role("demo", "SA", "claude", effort="ultra")
    with pytest.raises(BridgeError):
        bridge.configure_role("demo", "SA", "chatgpt", model="pretend-model")
    with pytest.raises(BridgeError):
        bridge.configure_role("demo", "SA", "codex", model="--unsafe")


def test_codex_implementation_model_effort_and_permissions(monkeypatch, tmp_path):
    monkeypatch.setattr("ccc_mcp.adapters.agents.codex.probe", lambda *args: "codex.exe")

    def invoke(argv, cwd, *args):
        assert argv[argv.index("--sandbox") + 1] == "workspace-write"
        assert argv[argv.index("--model") + 1] == "chosen-model"
        assert "model_reasoning_effort=high" in argv
        Path(argv[argv.index("--output-last-message") + 1]).write_text(json.dumps(report()))
        return '{"type":"turn.completed","usage":{"input_tokens":8,"output_tokens":3}}', ""

    monkeypatch.setattr("ccc_mcp.adapters.agents.codex.run_process", invoke)
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
    monkeypatch.setattr("ccc_mcp.adapters.agents.claude.probe", lambda *args: "claude.exe")

    def invoke(argv, *args):
        assert argv[argv.index("--permission-mode") + 1] == "plan"
        assert argv[argv.index("--tools") + 1] == "Read,Glob,Grep"
        assert argv[argv.index("--model") + 1] == "sonnet"
        assert argv[argv.index("--effort") + 1] == "high"
        return json.dumps({"structured_output": report()}), ""

    monkeypatch.setattr("ccc_mcp.adapters.agents.claude.run_process", invoke)
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
    assert bridge.latest("demo")["version"] == 2
    automatic = bridge.latest("demo")["snapshot"]["agent_results"][0]
    assert automatic["provider"] == "chatgpt"
    assert automatic["context_updates"] == ["Candidate fact"]
    updates = bridge.collect_context_updates("demo")
    original = next(item for item in updates["evidence"] if "CONTEXT-UPDATES.md" in item["path"])
    ref = {key: original[key] for key in ("path", "sha256")}
    patch = bridge.propose_context_patch(
        "demo",
        {
            "base_context_version": 2,
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
    assert second["context_package"]["context_version"] == 3


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


def test_task_usage_clearly_labels_chatgpt_interactive_execution(bridge):
    """Regression target for the chatgpt-only profile: `cli_invoked: False` on an
    interactive ChatGPT role must read as "ChatGPT web/MCP, by design", never as the
    ambiguous generic failure a missing/false CLI invocation would otherwise suggest."""
    bridge.configure_role("demo", "SA", "chatgpt")
    bridge.create_task("demo", "Analyze")
    queued = dispatch(bridge)
    before_submit = bridge.get_task_usage("demo", "TASK-0001")[-1]
    assert before_submit["interactive"] is True
    assert before_submit["invocation"]["source"] == "chatgpt_web"
    assert before_submit["invocation"]["cli_invoked"] is False
    assert "never invokes a local CLI" in before_submit["invocation"]["usage"]["reason"]

    bridge.submit_agent_report("demo", queued["execution_id"], report())
    after_submit = bridge.get_task_usage("demo", "TASK-0001")[-1]
    assert after_submit["interactive"] is True
    assert after_submit["invocation"]["source"] == "chatgpt_web"
    assert after_submit["invocation"]["cli_invoked"] is False


def test_task_usage_labels_a_cancelled_chatgpt_interactive_execution_too(bridge):
    """A cancelled interactive execution never reaches submit_agent_report, so it saves
    no result/invocation at all — get_task_usage must still derive the ChatGPT-by-design
    label from the execution row itself, not fall back to the legacy "metadata missing"
    default that a cancelled native-CLI execution would correctly get instead."""
    bridge.configure_role("demo", "SA", "chatgpt")
    bridge.create_task("demo", "Analyze")
    dispatch(bridge)
    bridge.cancel_task("demo", "TASK-0001")
    usage = bridge.get_task_usage("demo", "TASK-0001")[-1]
    assert usage["status"] == "CANCELLED"
    assert usage["interactive"] is True
    assert usage["invocation"]["cli_invoked"] is False
    assert usage["invocation"]["source"] == "chatgpt_web"


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

    monkeypatch.setattr("ccc_mcp.adapters.agents.claude.ClaudeCodeAdapter.run", fail)
    queued = dispatch(bridge, "RD")
    run_execution(bridge, queued["execution_id"])
    results = bridge.get_task_result("demo", "TASK-0001")
    assert len(results) == (2 if quota else 1)
    if quota:
        assert results[0]["result"]["failure_kind"] == "QUOTA_EXHAUSTED"
        assert results[0]["result"]["fallback"] == {
            "attempted": True,
            "target": "chatgpt",
            "dispatched": True,
            "fallback_execution_id": results[1]["id"],
        }
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

    monkeypatch.setattr("ccc_mcp.adapters.agents.codex.CodexAdapter.run", observed_process)
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

    monkeypatch.setattr("ccc_mcp.adapters.agents.codex.CodexAdapter.run", stale)
    queued = dispatch(bridge)
    run_execution(bridge, queued["execution_id"])
    results = bridge.get_task_result("demo", "TASK-0001")
    assert len(results) == 1 and results[0]["result"]["failure_kind"] == "QUOTA_EXHAUSTED"
    assert not bridge.pending_chatgpt_handoffs("demo")
    # Regression (P1-1): the fallback attempt's failure must be recorded, not silently
    # dropped — this is the exact "備援派工失敗也會被靜默略過" gap from the review.
    fallback = results[0]["result"]["fallback"]
    assert fallback["attempted"] is True
    assert fallback["dispatched"] is False
    assert fallback["target"] == "chatgpt"
    assert fallback["failed_reason"]
    brief = bridge.get_task_status("demo", "TASK-0001")["brief"]
    assert brief["fallback"] == fallback


@pytest.mark.parametrize(
    "message",
    [
        "You've hit your session limit, try again in 2 hours.",
        "Error code: rate_limit_error",
        "insufficient_quota",
    ],
)
def test_is_quota_error_recognizes_observed_messages(message):
    """Regression (P1-1): "you've hit your session limit" was previously NOT a substring
    of the only marker on file ("you've hit your limit"), so a real observed quota
    message silently failed to trigger the fallback mechanism at all."""
    assert is_quota_error(message) is True


def test_is_quota_error_does_not_flag_unrelated_errors():
    assert is_quota_error("Not logged in") is False


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
