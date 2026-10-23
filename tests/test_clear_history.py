import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ccc_mcp.infra.errors import BridgeError, PermissionDenied
from ccc_mcp.interfaces.cli import app
from ccc_mcp.worker import run_execution


def test_clear_history_requires_interactive_local_confirmation(bridge):
    with pytest.raises(PermissionDenied, match="interactive"):
        bridge.clear_project_history_local("demo")
    assert bridge.latest("demo")["version"] == 1


def test_clear_history_archives_and_restarts_same_project_id(bridge, human):
    bridge.authorize_project_local("demo")
    bridge.configure_meeting_mode("demo", True, 0, 2)
    task = bridge.create_task("demo", "Old requirement")
    execution = bridge.dispatch_agent(
        "demo", task["id"], "codex", "SA", 1, "Analyze", adapter="fake"
    )
    run_execution(bridge, execution["execution_id"])
    root = Path(bridge.project("demo")["root"])

    result = bridge.clear_project_history_local("demo")

    assert result["history_cleared"] is True
    assert result["context_version"] == 1
    assert result["project_files_deleted"] is False
    assert result["authorization_preserved"] == "read-write"
    assert root.joinpath("app.py").is_file()
    assert bridge.list_tasks("demo") == []
    assert bridge.latest("demo")["version"] == 1
    assert bridge.get_project_settings("demo")["meeting_mode"]["enabled"] is True
    assert "demo" in {project["id"] for project in bridge.list_projects()}
    assert not bridge.store.one("SELECT 1 FROM project_deletions WHERE project_id='demo'")

    archive = Path(result["archive"])
    assert archive.joinpath("bridge.sqlite3").is_file()
    assert archive.joinpath("project-metadata").is_dir()
    with sqlite3.connect(archive / "bridge.sqlite3") as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE project_id='demo'"
        ).fetchone()[0] == 1
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        bridge.store.execute("DELETE FROM context_versions WHERE project_id='demo'")

    fresh = bridge.create_task("demo", "Fresh requirement")
    assert fresh["id"] == "TASK-0001"


def test_clear_history_rejects_live_execution(bridge, human):
    bridge.authorize_project_local("demo")
    bridge.start_project_request("demo", "Still running")

    with pytest.raises(BridgeError, match="active execution"):
        bridge.clear_project_history_local("demo")


def test_cli_clear_history_preserves_project_registration(bridge, human):
    bridge.create_task("demo", "Old requirement")
    result = CliRunner().invoke(
        app,
        ["--home", str(bridge.home), "--project", "demo", "clear-history"],
    )

    assert result.exit_code == 0, result.output
    assert '"history_cleared": true' in result.output
    assert bridge.list_tasks("demo") == []
    assert "demo" in {project["id"] for project in bridge.list_projects()}


def test_cli_clear_history_two_word_alias(bridge, human):
    bridge.create_task("demo", "Old requirement")
    result = CliRunner().invoke(
        app,
        ["--home", str(bridge.home), "--project", "demo", "clear", "history"],
    )

    assert result.exit_code == 0, result.output
    assert '"history_cleared": true' in result.output
