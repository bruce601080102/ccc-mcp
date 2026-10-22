"""`task_preflight` (CLI: `ccc-mcp task preflight`) is a read-only, pre-dispatch
environment check: can the configured test command be found, is a dependency
manifest already installed, and is the Git/worktree state usable — surfaced before
a real dispatch wastes a round, never by installing, testing, or touching Git.
"""

import json
import subprocess
import sys
from pathlib import Path

from typer.testing import CliRunner

from ccc_mcp.adapters.test_runner import venv_python_path
from ccc_mcp.infra.security import safe_path
from ccc_mcp.interfaces.cli import app


def root(bridge):
    return Path(bridge.project("demo")["root"])


def test_default_pytest_command_resolves_and_is_ready(bridge, human):
    bridge.authorize_project_local("demo")
    task_id = bridge.create_task("demo", "Implement a change")["id"]

    result = bridge.task_preflight("demo", task_id)

    assert result["ready"] is True
    assert result["test_command"]["configured"] is False
    assert result["test_command"]["resolvable"] is True
    assert result["test_command"]["command"][-2:] == ["pytest", "-q"]
    assert result["dependencies"] == {
        "workspace_exists": True,
        "checked_path": str(root(bridge)),
        "manifest": None,
        "venv_ready": None,
    }
    assert result["git"] == {"initial_commit": True, "clean": True, "reason": None}


def test_unresolvable_test_command_is_not_ready(bridge, human):
    bridge.authorize_project_local("demo")
    bridge.configure_test_command("demo", ["definitely-not-a-real-command-xyz"])
    task_id = bridge.create_task("demo", "Implement a change")["id"]

    result = bridge.task_preflight("demo", task_id)

    assert result["ready"] is False
    assert result["test_command"]["resolvable"] is False
    assert "not found" in result["test_command"]["reason"]


def test_dependency_manifest_is_detected_without_installing_anything(bridge, human):
    bridge.authorize_project_local("demo")
    (root(bridge) / "requirements.txt").write_text("requests\n")
    task_id = bridge.create_task("demo", "Implement a change")["id"]

    result = bridge.task_preflight("demo", task_id)

    assert result["dependencies"]["manifest"] == "requirements.txt"
    assert result["dependencies"]["venv_ready"] is False
    # Never actually installs: no venv was created as a side effect.
    venv_dir = safe_path(bridge.home / "venvs", "demo")
    assert not venv_python_path(venv_dir).is_file()
    # sync_documents always writes a placeholder TEST-RESULT.md on task creation;
    # preflight must not have replaced it with real (fabricated) test evidence.
    placeholder = (root(bridge) / ".ccc-mcp" / "tasks" / task_id / "TEST-RESULT.md").read_text()
    assert "尚未產生" in placeholder


def test_venv_ready_once_the_dedicated_venv_already_exists(bridge, human):
    bridge.authorize_project_local("demo")
    (root(bridge) / "requirements.txt").write_text("requests\n")
    task_id = bridge.create_task("demo", "Implement a change")["id"]
    venv_dir = safe_path(bridge.home / "venvs", "demo")
    subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True)

    result = bridge.task_preflight("demo", task_id)

    assert result["dependencies"]["venv_ready"] is True


def test_uncommitted_changes_block_readiness_in_project_mode(bridge, human):
    bridge.authorize_project_local("demo")
    task_id = bridge.create_task("demo", "Implement a change")["id"]
    (root(bridge) / "app.py").write_text("# original comment\nvalue = 2\n")

    result = bridge.task_preflight("demo", task_id)

    assert result["git"]["clean"] is False
    assert "commit" in result["git"]["reason"]
    assert result["ready"] is False
    # Read-only: the dirty file is still there, untouched, no git operation ran on it.
    assert "value = 2" in (root(bridge) / "app.py").read_text()


def test_missing_initial_commit_is_reported_and_blocks_readiness(bridge, tmp_path, human):
    folder = tmp_path / "plain"
    folder.mkdir()
    bridge.register_project(folder, "plain")
    bridge.authorize_project_local("plain")
    task_id = bridge.create_task("plain", "Implement a change")["id"]

    result = bridge.task_preflight("plain", task_id)

    assert result["git"]["initial_commit"] is False
    assert result["git"]["clean"] is None  # not checked without a commit to diff against
    assert result["ready"] is False


def test_worktree_mode_checks_project_root_before_a_worktree_exists(bridge, human):
    task_id = bridge.create_task("demo", "Implement a change")["id"]

    result = bridge.task_preflight("demo", task_id)

    assert result["dependencies"]["workspace_exists"] is False
    assert result["dependencies"]["checked_path"] == str(root(bridge))
    # Worktree mode has nothing to diff against; the clean check only applies to
    # project mode (an authorized project modifies the original repo in place).
    assert result["git"]["clean"] is None


def test_defaults_to_the_project_workstream_task(bridge, human):
    bridge.authorize_project_local("demo")
    task_id = bridge.create_task("demo", "Implement a change")["id"]
    assert bridge.task_preflight("demo")["task_id"] == task_id


def test_cli_task_preflight_round_trip(bridge, human):
    bridge.authorize_project_local("demo")
    task_id = bridge.create_task("demo", "Implement a change")["id"]

    result = CliRunner().invoke(
        app, ["--home", str(bridge.home), "--project", "demo", "task", "preflight", task_id]
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["task_id"] == task_id
    assert "ready" in payload
