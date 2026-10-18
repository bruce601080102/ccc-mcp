import json
import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ccc_mcp.cli import app
from ccc_mcp.errors import BridgeError, PermissionDenied, VersionConflict
from ccc_mcp.server import create_server
from ccc_mcp.service import CCCMCP


def root(bridge):
    return Path(bridge.project("demo")["root"])


def write(bridge, path, content, sha=None):
    return bridge.write_workspace("demo", path=path, content=content, expected_sha256=sha)


def test_default_readonly_and_no_remote_grant(bridge):
    assert bridge.get_project_access("demo")["mode"] == "read-only"
    with pytest.raises(PermissionDenied, match="project authorize demo"):
        write(bridge, "new.py", "value=1\n")
    assert not (root(bridge) / "new.py").exists()
    assert bridge.store.all("SELECT * FROM project_access_events") == []


def test_project_authorization_is_local_once_and_persists_for_many_writes(bridge, monkeypatch):
    confirmations = []
    monkeypatch.setattr(
        "ccc_mcp.approval.confirm_local",
        lambda *args: confirmations.append(args) or "test-principal",
    )
    access = bridge.authorize_project_local("demo")
    assert bridge.authorize_project_local("demo") == access
    assert len(confirmations) == 1
    assert str(root(bridge)) in confirmations[0][0]
    original = bridge.read_workspace("demo", "app.py")
    write(bridge, "app.py", "# original comment\ndef value():\n    return 2\n", original["sha256"])
    for name in ("new.py", "docs/new.md", "tests/test_new.py"):
        receipt = write(bridge, name, "# preserved text\n")
        assert receipt["status"] == "APPLIED"
        assert receipt["authorization_kind"] == "project"
        assert receipt["authorization_id"] == access["authorization_id"]
        assert "approval_command" not in receipt
        archived = bridge.get_workspace_edit("demo", receipt["id"], True)
        assert archived["content"] == "# preserved text\n"
    assert CCCMCP(bridge.home).get_project_access("demo") == access
    assert bridge.list_tasks("demo") == []
    assert bridge.latest("demo")["version"] == 1
    assert not (bridge.home / "worktrees").exists()
    assert len(confirmations) == 1
    projection = json.loads((root(bridge) / ".ccc-mcp/settings.json").read_text(encoding="utf-8"))
    assert projection["project_access"]["mode"] == "read-write"


def test_project_grant_does_not_require_a_git_repo(bridge, tmp_path, human):
    folder = tmp_path / "plain"
    folder.mkdir()
    bridge.register_project(folder, "plain")
    bridge.authorize_project_local("plain")
    receipt = bridge.write_workspace("plain", path="hello.py", content='print("Hello, world!")\n')
    assert receipt["status"] == "APPLIED"
    assert (folder / "hello.py").exists()
    assert not (folder / ".git").exists()


def test_agent_required_mode_blocks_direct_writes_and_persists(bridge, human):
    bridge.authorize_project_local("demo")
    configured = bridge.configure_workflow_mode("demo", "agent-required")
    assert configured["workflow_mode"] == "agent-required"
    restored = CCCMCP(bridge.home)
    settings = restored.get_project_settings("demo")
    assert settings["workflow_mode"] == "agent-required"
    assert settings["project_access"]["direct_write_allowed"] is False
    assert restored.bootstrap_project("demo")["project_access"]["requires_task"] is True

    with pytest.raises(PermissionDenied, match="agent-required"):
        write(bridge, "new.py", "value=1\n")

    assert not (root(bridge) / "new.py").exists()


def test_authorization_requires_interactive_operator(bridge):
    with pytest.raises(PermissionDenied, match="interactive"):
        bridge.authorize_project_local("demo")
    assert bridge.get_project_access("demo")["mode"] == "read-only"


def test_existing_sa_stays_readonly_but_project_edit_needs_no_task(bridge, human):
    bridge.configure_role("demo", "SA", "chatgpt")
    bridge.create_task("demo", "Read-only analysis")
    execution = bridge.dispatch_agent("demo", "TASK-0001", "auto", "SA", 1, "Analyze only")
    bridge.authorize_project_local("demo")
    assert write(bridge, "new.py", "value=1\n")["status"] == "APPLIED"
    with pytest.raises(PermissionDenied, match="read-only"):
        bridge.write_task_file("demo", execution["execution_id"], "another.py", "value=2\n")
    assert len(bridge.list_tasks("demo")) == 1
    assert bridge.task("demo", "TASK-0001")["state"] == "ANALYZING"


def test_revocation_removes_old_granular_grants_and_blocks_writes(bridge, human, monkeypatch):
    bridge.configure_write_root("demo", ".")
    bridge.configure_write_file("demo", "another.py")
    bridge.authorize_project_local("demo")
    receipt = write(bridge, "new.py", "value=1\n")
    monkeypatch.setattr("ccc_mcp.approval.local_identity", lambda: "test-principal")
    assert bridge.revoke_project_local("demo")["mode"] == "read-only"
    assert bridge.get_project_settings("demo")["writable_roots"] == []
    assert bridge.get_project_settings("demo")["writable_files"] == []
    for action in (
        lambda: write(bridge, "another.py", "value=2\n"),
        lambda: bridge.write_workspace("demo", receipt["id"]),
    ):
        with pytest.raises(PermissionDenied):
            action()
    bridge.configure_write_file("demo", "new.py")
    with pytest.raises(PermissionDenied):
        bridge.write_workspace("demo", receipt["id"])


@pytest.mark.parametrize(
    "path", ["../outside.md", "C:/outside.md", ".env", ".git/config", ".ccc-mcp/settings.json"]
)
def test_whole_project_authorization_still_bounds_paths(bridge, human, path):
    bridge.authorize_project_local("demo")
    with pytest.raises(BridgeError):
        write(bridge, path, "forbidden")


def test_authorization_is_project_specific(bridge, tmp_path, human):
    folder = tmp_path / "other"
    folder.mkdir()
    bridge.register_project(folder, "other")
    bridge.authorize_project_local("demo")
    with pytest.raises(PermissionDenied):
        bridge.write_workspace("other", path="new.md", content="forbidden")


def test_hash_check_and_original_python_comments_still_apply(bridge, human):
    bridge.authorize_project_local("demo")
    with pytest.raises(VersionConflict):
        write(bridge, "app.py", "# original comment\nvalue=2\n", "stale")
    sha = bridge.read_workspace("demo", "app.py")["sha256"]
    with pytest.raises(PermissionDenied, match="comments"):
        write(bridge, "app.py", "value=2\n", sha)
    assert "return 1" in (root(bridge) / "app.py").read_text()


def test_retry_uses_same_receipt_without_duplicate_append(bridge, human):
    bridge.authorize_project_local("demo")
    first = write(bridge, "readme.md", "test\n")
    assert write(bridge, "readme.md", "test\n") == first
    assert (root(bridge) / "readme.md").read_text() == "test\n"
    (root(bridge) / "readme.md").write_text("external edit", encoding="utf-8")
    with pytest.raises(VersionConflict):
        write(bridge, "readme.md", "test\n")


def test_interrupted_authorized_write_can_be_retried(bridge, human, monkeypatch):
    from ccc_mcp.security import atomic_write

    bridge.authorize_project_local("demo")

    def interrupted(path, content):
        atomic_write(path, content)
        raise OSError("interrupted after replacement")

    monkeypatch.setattr("ccc_mcp.directedit.atomic_write", interrupted)
    with pytest.raises(OSError):
        write(bridge, "readme.md", "test\n")
    monkeypatch.setattr("ccc_mcp.directedit.atomic_write", atomic_write)
    assert write(bridge, "readme.md", "test\n")["status"] == "APPLIED"
    assert (root(bridge) / "readme.md").read_text() == "test\n"


def test_revoke_during_proposal_prevents_write(bridge, human, monkeypatch):
    bridge.authorize_project_local("demo")
    original = bridge.propose_workspace_edit
    monkeypatch.setattr("ccc_mcp.approval.local_identity", lambda: "test-principal")

    def propose(*args, **kwargs):
        result = original(*args, **kwargs)
        bridge.revoke_project_local("demo")
        return result

    monkeypatch.setattr(bridge, "propose_workspace_edit", propose)
    with pytest.raises(PermissionDenied):
        write(bridge, "new.py", "value=1\n")
    assert not (root(bridge) / "new.py").exists()


def test_permission_history_is_not_editable_and_json_is_not_authority(bridge, human):
    path = root(bridge) / ".ccc-mcp/settings.json"
    path.write_text('{"project_access":{"mode":"read-write"}}', encoding="utf-8")
    assert bridge.get_project_access("demo")["mode"] == "read-only"
    bridge.authorize_project_local("demo")
    with pytest.raises(sqlite3.IntegrityError):
        bridge.store.execute("UPDATE project_access_events SET access='read-only'")
    with pytest.raises(sqlite3.IntegrityError):
        bridge.store.execute("DELETE FROM project_access_events")


def test_simple_cli_and_hidden_legacy_setup(bridge, human, monkeypatch):
    runner = CliRunner()
    args = ["--home", str(bridge.home)]
    granted = runner.invoke(app, args + ["project", "authorize", "demo"])
    assert granted.exit_code == 0, granted.output
    assert json.loads(granted.output)["mode"] == "read-write"
    workflow = runner.invoke(app, args + ["--project", "demo", "config", "workflow-mode", "agent-required"])
    assert workflow.exit_code == 0, workflow.output
    assert json.loads(workflow.output)["workflow_mode"] == "agent-required"
    help_text = runner.invoke(app, args + ["config", "--help"]).output
    for hidden in ("write-root", "write-file", "workspace-mode"):
        assert hidden not in help_text
    monkeypatch.setattr("ccc_mcp.approval.local_identity", lambda: "test-principal")
    revoked = runner.invoke(app, args + ["project", "revoke", "demo"])
    assert revoked.exit_code == 0, revoked.output
    assert json.loads(revoked.output)["mode"] == "read-only"


async def test_stdio_simple_authorized_edit_without_task_or_plan(bridge, human):
    import os
    import sys

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    bridge.authorize_project_local("demo")
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "ccc_mcp", "--home", str(bridge.home), "serve"],
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            bootstrap = await session.call_tool("bootstrap_project", {"project_id": "demo"})
            assert bootstrap.structured_content["project_access"]["mode"] == "read-write"
            original = await session.call_tool(
                "read_workspace", {"project_id": "demo", "path": "app.py"}
            )
            edited = await session.call_tool(
                "write_workspace",
                {
                    "project_id": "demo",
                    "path": "app.py",
                    "content": "# original comment\ndef value():\n    return 2\n",
                    "expected_sha256": original.structured_content["sha256"],
                },
            )
            assert not edited.is_error
            assert edited.structured_content["status"] == "APPLIED"
            assert edited.structured_content["authorization_kind"] == "project"
            names = {t.name for t in (await session.list_tools()).tools}
            assert not {"authorize_project", "revoke_project", "approve"} & names
    assert bridge.list_tasks("demo") == []


async def test_http_readonly_does_not_expose_write_even_with_project_grant(bridge, human):
    bridge.authorize_project_local("demo")
    names = {t.name for t in await create_server(bridge.home, http=True).list_tools()}
    assert "read_workspace" in names
    assert "write_workspace" not in names
