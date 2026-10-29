import shutil
import sqlite3
from pathlib import Path

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


def test_remove_keeps_directory_and_allows_id_reuse(bridge, human):
    root = bridge.project("demo")["root"]
    bridge.authorize_project_local("demo")
    bridge.create_task("demo", "Check the app")
    result = bridge.remove_project_local("demo")
    assert result["removed"] is True
    assert result["files_deleted"] is False
    assert result["archive"]
    assert (Path(root) / "app.py").exists()
    assert "demo" not in {p["id"] for p in bridge.list_projects()}
    assert bridge.store.one("SELECT 1 FROM projects WHERE id='demo'") is None
    again = bridge.register_project(root, "demo")
    assert again["id"] == "demo"
    assert bridge.list_tasks("demo") == []


def test_remove_with_delete_files_removes_only_selected_root(bridge, human, tmp_path):
    root = Path(bridge.project("demo")["root"])
    sibling = tmp_path / "keep-me"
    sibling.mkdir()
    result = bridge.remove_project_local("demo", delete_files=True)
    assert result["files_deleted"] is True
    assert not root.exists()
    assert sibling.exists()
    assert "demo" not in {p["id"] for p in bridge.list_projects()}


def test_remove_requires_confirmation_and_blocks_live_execution(bridge, human, monkeypatch):
    from ccc_mcp.core import approval

    bridge.authorize_project_local("demo")
    bridge.start_project_request("demo", "Analyze app.py")
    with pytest.raises(BridgeError, match="active execution"):
        bridge.remove_project_local("demo")
    bridge.store.execute(
        "UPDATE agent_executions SET status='FAILED' WHERE project_id='demo'"
    )
    monkeypatch.setattr(approval, "confirm_local", lambda _description, challenge: (_ for _ in ()).throw(PermissionDenied(challenge)))
    with pytest.raises(PermissionDenied, match="REMOVE demo"):
        bridge.remove_project_local("demo")
    with pytest.raises(PermissionDenied, match="REMOVE FILES demo"):
        bridge.remove_project_local("demo", delete_files=True)


def test_remove_rejects_directory_containing_another_registered_project(bridge, human, tmp_path):
    root = Path(bridge.project("demo")["root"])
    nested = root / "nested"
    nested.mkdir()
    bridge.register_project(nested, "nested")
    with pytest.raises(BridgeError, match="Another registered project"):
        bridge.remove_project_local("demo", delete_files=True)
    assert root.exists()


def test_cli_project_remove_keeps_folder_by_default(bridge, human):
    import json

    from typer.testing import CliRunner

    from ccc_mcp.interfaces.cli import app

    root = Path(bridge.project("demo")["root"])
    result = CliRunner().invoke(
        app, ["--home", str(bridge.home), "project", "remove", "demo"]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["files_deleted"] is False
    assert root.exists()


def test_remove_missing_project_root_does_not_recreate_it(bridge, human):
    root = Path(bridge.project("demo")["root"])
    shutil.rmtree(root)
    bridge.remove_project_local("demo")
    assert not root.exists()
    assert "demo" not in {p["id"] for p in bridge.list_projects()}


def test_remove_requires_local_human_confirmation(bridge):
    with pytest.raises(PermissionDenied, match="interactive"):
        bridge.remove_project_local("demo")
    assert "demo" in {p["id"] for p in bridge.list_projects()}


def test_remove_rolls_back_folder_move_if_database_write_fails(bridge, human, monkeypatch):
    root = Path(bridge.project("demo")["root"])
    original_transaction = bridge.store.transaction

    def fail_transaction():
        raise sqlite3.OperationalError("simulated transaction failure")

    monkeypatch.setattr(bridge.store, "transaction", fail_transaction)
    with pytest.raises(sqlite3.OperationalError, match="simulated"):
        bridge.remove_project_local("demo", delete_files=True)
    monkeypatch.setattr(bridge.store, "transaction", original_transaction)
    assert root.exists()
    assert (root / "app.py").exists()
    assert "demo" in {p["id"] for p in bridge.list_projects()}
