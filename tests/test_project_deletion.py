import sqlite3

import pytest

from ccc_mcp.infra.errors import BridgeError, PermissionDenied


def test_deregister_requires_interactive_confirmation(bridge):
    with pytest.raises(PermissionDenied, match="interactive"):
        bridge.deregister_project_local("demo")
    # Nothing was recorded; the project is still fully usable.
    assert "demo" in [p["id"] for p in bridge.list_projects()]


def test_deregister_hides_project_and_blocks_further_access(bridge, human):
    result = bridge.deregister_project_local("demo")
    assert result == {
        "project_id": "demo",
        "deregistered": True,
        "identity": "test-principal",
    }

    assert "demo" not in [p["id"] for p in bridge.list_projects()]

    for call in (
        lambda: bridge.project("demo"),
        lambda: bridge.public_project_settings("demo"),
        lambda: bridge.bootstrap_project("demo"),
        lambda: bridge.list_tasks("demo"),
    ):
        with pytest.raises(BridgeError, match="deregistered"):
            call()


def test_deregister_blocks_id_reuse(bridge, human, tmp_path):
    bridge.deregister_project_local("demo")

    with pytest.raises(BridgeError, match="deregistered"):
        bridge.create_project("demo")

    other_repo = tmp_path / "other"
    other_repo.mkdir()
    with pytest.raises(BridgeError, match="deregistered"):
        bridge.register_project(other_repo, "demo")


def test_deregister_requires_revoking_project_authorization_first(bridge, human):
    bridge.authorize_project_local("demo")
    with pytest.raises(PermissionDenied, match="project revoke demo"):
        bridge.deregister_project_local("demo")
    bridge.revoke_project_local("demo")
    assert bridge.deregister_project_local("demo")["deregistered"] is True


def test_deregister_blocks_while_an_execution_is_live(bridge, human):
    bridge.authorize_project_local("demo")
    bridge.start_project_request("demo", "Analyze app.py")
    bridge.revoke_project_local("demo")
    with pytest.raises(BridgeError, match="active execution"):
        bridge.deregister_project_local("demo")


def test_deregistration_history_is_immutable(bridge, human):
    bridge.deregister_project_local("demo")
    row = bridge.store.one("SELECT * FROM project_deletions WHERE project_id='demo'")
    assert row is not None
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        bridge.store.execute(
            "UPDATE project_deletions SET identity='someone-else' WHERE id=?", (row["id"],)
        )
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        bridge.store.execute("DELETE FROM project_deletions WHERE id=?", (row["id"],))


def test_cli_project_delete(bridge, human):
    import json

    from typer.testing import CliRunner

    from ccc_mcp.interfaces.cli import app

    runner = CliRunner()
    args = ["--home", str(bridge.home)]
    deleted = runner.invoke(app, args + ["project", "delete", "demo"])
    assert deleted.exit_code == 0, deleted.output
    assert json.loads(deleted.output)["deregistered"] is True
    listed = runner.invoke(app, args + ["project", "list"])
    assert "demo" not in listed.output
