import json
import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ccc_mcp.infra.errors import PermissionDenied
from ccc_mcp.interfaces.cli import app
from ccc_mcp.interfaces.server import create_server
from ccc_mcp.service import CCCMCP


def root(bridge):
    return Path(bridge.project("demo")["root"])


def test_project_starts_readonly_and_requires_local_authorization(bridge):
    assert bridge.get_project_access("demo")["mode"] == "read-only"
    with pytest.raises(PermissionDenied, match="project authorize demo"):
        bridge.start_project_request("demo", "Change app.py")
    with pytest.raises(PermissionDenied, match="interactive"):
        bridge.authorize_project_local("demo")
    assert bridge.list_tasks("demo") == []


def test_one_authorization_enables_agent_request_not_direct_write(bridge, human):
    granted = bridge.authorize_project_local("demo")
    assert granted["mode"] == "read-write"
    assert granted["write_tool"] == "write_task_file"
    assert granted["requires_task"] is True
    assert granted["direct_write_allowed"] is False
    assert bridge.authorize_project_local("demo") == granted
    assert CCCMCP(bridge.home).get_project_settings("demo")["workspace_mode"] == "project"
    assert "workflow_mode" not in bridge.public_project_settings("demo")
    assert "workspace_mode" not in bridge.public_project_settings("demo")
    assert bridge.public_project_settings("demo")["protocol_version"] == "1"
    with pytest.raises(PermissionDenied, match="Agent workstream"):
        bridge.write_workspace("demo", path="new.py", content="value=1\n")
    assert not (root(bridge) / "new.py").exists()
    assert bridge.list_tasks("demo") == []
    projection = json.loads((root(bridge) / ".ccc-mcp/settings.json").read_text(encoding="utf-8"))
    assert "workflow_mode" not in projection
    assert "workspace_mode" not in projection


def test_start_project_request_dispatches_configured_sa_without_env_flag(bridge, human, monkeypatch):
    monkeypatch.delenv("CCC_MCP_ENABLE_REAL_AGENTS", raising=False)
    monkeypatch.delenv("AGENTBRIDGE_ENABLE_REAL_AGENTS", raising=False)
    bridge.authorize_project_local("demo")
    started = bridge.start_project_request("demo", "Analyze and change app.py", ["Value is 2"])
    assert started["workstream"]["id"] == "TASK-0001"
    execution = bridge.store.one(
        "SELECT agent,role,adapter,status,options FROM agent_executions WHERE id=?",
        (started["sa_execution"]["execution_id"],),
    )
    assert (execution["agent"], execution["role"], execution["adapter"]) == (
        "codex", "SA", "real"
    )
    assert execution["status"] == "QUEUED"
    assert json.loads(execution["options"])["workspace_mode"] == "project"
    assert bridge.list_tasks("demo")[0]["state"] == "ANALYZING"


def test_authorized_project_without_git_can_start_agent_workstream(bridge, tmp_path, human):
    folder = tmp_path / "plain"
    folder.mkdir()
    bridge.register_project(folder, "plain")
    bridge.authorize_project_local("plain")
    result = bridge.start_project_request("plain", "Create hello.py")
    assert result["sa_execution"]["status"] == "QUEUED"
    assert not (folder / ".git").exists()


def test_old_direct_setting_cannot_reenable_pm_file_writes(bridge, human):
    bridge.store.execute(
        "UPDATE project_settings SET content=? WHERE project_id=?",
        (json.dumps({"workflow_mode": "direct", "workspace_mode": "worktree"}), "demo"),
    )
    bridge.authorize_project_local("demo")
    assert bridge.get_project_settings("demo")["workflow_mode"] == "agent-required"
    assert bridge.get_project_settings("demo")["workspace_mode"] == "project"
    with pytest.raises(PermissionDenied, match="Agent workstream"):
        bridge.write_workspace("demo", path="app.py", content="value=2\n")


def test_revocation_removes_authorization(bridge, human, monkeypatch):
    bridge.authorize_project_local("demo")
    monkeypatch.setattr("ccc_mcp.core.approval.local_identity", lambda: "test-principal")
    assert bridge.revoke_project_local("demo")["mode"] == "read-only"
    with pytest.raises(PermissionDenied, match="project authorize demo"):
        bridge.start_project_request("demo", "Change app.py")


def test_authorization_is_project_specific(bridge, tmp_path, human):
    folder = tmp_path / "other"
    folder.mkdir()
    bridge.register_project(folder, "other")
    bridge.authorize_project_local("demo")
    with pytest.raises(PermissionDenied):
        bridge.start_project_request("other", "Change other project")


def test_access_history_is_immutable_and_json_is_not_authority(bridge, human):
    path = root(bridge) / ".ccc-mcp/settings.json"
    path.write_text('{"project_access":{"mode":"read-write"}}', encoding="utf-8")
    assert bridge.get_project_access("demo")["mode"] == "read-only"
    bridge.authorize_project_local("demo")
    with pytest.raises(sqlite3.IntegrityError):
        bridge.store.execute("UPDATE project_access_events SET access='read-only'")
    with pytest.raises(sqlite3.IntegrityError):
        bridge.store.execute("DELETE FROM project_access_events")


def test_manual_settings_json_cannot_expand_project_permissions(bridge):
    path = root(bridge) / ".ccc-mcp/settings.json"
    path.write_text(
        json.dumps(
            {
                "roles": {"SA": {"provider": "chatgpt"}},
                "project_access": {"mode": "read-write"},
                "writable_roots": ["C:/"],
            }
        ),
        encoding="utf-8",
    )
    settings = bridge.get_project_settings("demo")
    assert settings["roles"]["SA"]["provider"] == "chatgpt"
    assert settings["project_access"]["mode"] == "read-only"
    assert settings["writable_roots"] == []
    with pytest.raises(PermissionDenied, match="project authorize demo"):
        bridge.start_project_request("demo", "Change app.py")


def test_project_revoke_remains_available_with_malformed_settings_file(bridge, human, monkeypatch):
    bridge.authorize_project_local("demo")
    path = root(bridge) / ".ccc-mcp/settings.json"
    path.write_text("{broken", encoding="utf-8")
    monkeypatch.setattr("ccc_mcp.core.approval.local_identity", lambda: "test-principal")
    assert bridge.revoke_project_local("demo")["mode"] == "read-only"
    assert bridge.get_project_settings("demo")["project_access"]["mode"] == "read-only"


def test_cli_has_no_user_selectable_modes(bridge, human, monkeypatch):
    runner = CliRunner()
    args = ["--home", str(bridge.home)]
    granted = runner.invoke(app, args + ["project", "authorize", "demo"])
    assert granted.exit_code == 0, granted.output
    assert json.loads(granted.output)["mode"] == "read-write"
    shown = runner.invoke(app, args + ["--project", "demo", "config", "show"])
    assert shown.exit_code == 0, shown.output
    assert "workflow_mode" not in json.loads(shown.output)
    assert "workspace_mode" not in json.loads(shown.output)
    help_text = runner.invoke(app, args + ["config", "--help"]).output
    assert "workflow-mode" not in help_text
    assert "workspace-mode" not in help_text
    monkeypatch.setattr("ccc_mcp.core.approval.local_identity", lambda: "test-principal")
    revoked = runner.invoke(app, args + ["project", "revoke", "demo"])
    assert revoked.exit_code == 0, revoked.output


async def test_mcp_exposes_agent_entry_point_but_no_pm_write_bypass(bridge, human):
    bridge.authorize_project_local("demo")
    tools = await create_server(bridge.home).list_tools()
    names = {tool.name for tool in tools}
    assert {"start_project_request", "dispatch_agent", "write_task_file"} <= names
    dispatch_schema = next(tool.input_schema for tool in tools if tool.name == "dispatch_agent")
    assert "adapter" not in dispatch_schema["properties"]
    assert "agent" not in dispatch_schema["properties"]
    advance_schema = next(tool.input_schema for tool in tools if tool.name == "advance_project")
    assert "adapter" not in advance_schema["properties"]
    assert not {"write_workspace", "propose_workspace_edit", "create_workspace_batch"} & names
    readonly = {tool.name for tool in await create_server(bridge.home, http=True).list_tools()}
    assert "read_workspace" in readonly
    assert "start_project_request" not in readonly
