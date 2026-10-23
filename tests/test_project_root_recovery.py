"""A registered project's root directory can be deleted outside ccc-mcp (a manual
`rm -rf`). There is no supported way to delete the registration itself —
project_access_events/context_versions/decisions are deliberately append-only — so
instead of leaving the project permanently unusable, ccc-mcp recreates an empty,
Git-initialized root and records the recovery as a visible Context update, never
silently.
"""

import os
import subprocess
from pathlib import Path

import pytest
from conftest import remove_tree
from typer.testing import CliRunner

from ccc_mcp.infra.errors import BridgeError
from ccc_mcp.interfaces.cli import app


def root(bridge):
    return Path(bridge.project("demo")["root"])


def test_missing_project_root_is_automatically_recreated(bridge):
    project_root = root(bridge)
    version_before = bridge.latest("demo")["version"]
    remove_tree(project_root)
    assert not project_root.exists()

    row = bridge.project("demo")

    assert Path(row["root"]) == project_root
    assert project_root.is_dir()
    log = subprocess.run(
        ["git", "-C", str(project_root), "log", "--oneline"],
        capture_output=True, text=True, check=True,
    ).stdout
    assert log.strip()

    latest = bridge.latest("demo")
    assert latest["version"] == version_before + 1
    assert latest["metadata"]["author_role"] == "SYSTEM"
    assert "重建" in latest["snapshot"]["status"]


def test_missing_project_root_recovery_is_idempotent(bridge):
    project_root = root(bridge)
    remove_tree(project_root)
    bridge.project("demo")
    version_after_first_repair = bridge.latest("demo")["version"]
    commit_after_first_repair = subprocess.run(
        ["git", "-C", str(project_root), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout

    # Calling it again (directly, and through any other method that touches the
    # project) must not re-run the recovery or bump Context a second time.
    bridge.project("demo")
    bridge.list_tasks("demo")

    assert bridge.latest("demo")["version"] == version_after_first_repair
    commit_after_second_call = subprocess.run(
        ["git", "-C", str(project_root), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    ).stdout
    assert commit_after_second_call == commit_after_first_repair


def test_missing_project_root_recovery_preserves_project_settings(bridge, human):
    """Regression (reported smoke test): `config profile chatgpt-only` lives in
    SQLite, not on disk, so it must survive the root directory being deleted and
    recreated — the project is usable again with the same configuration, not reset."""
    bridge.authorize_project_local("demo")
    bridge.configure_profile("demo", "chatgpt-only")
    assert bridge.public_project_settings("demo")["profile"] == "chatgpt-only"

    remove_tree(root(bridge))

    settings = bridge.public_project_settings("demo")
    assert settings["profile"] == "chatgpt-only"
    assert settings["project_access"]["mode"] == "read-write"
    for role in ("SA", "SD", "RD", "SD/RD", "REVIEW"):
        assert settings["roles"][role]["provider"] == "chatgpt"


def test_cli_config_show_transparently_recovers_a_deleted_project_root(bridge):
    """The exact real-world repro: a command that looks read-only (`config show`)
    must not just fail forever after the project folder is deleted by hand."""
    remove_tree(root(bridge))

    result = CliRunner().invoke(
        app, ["--home", str(bridge.home), "--project", "demo", "config", "show"]
    )

    assert result.exit_code == 0, result.output
    assert root(bridge).is_dir()


def test_symlinked_project_root_is_still_rejected_not_repaired(bridge, tmp_path):
    """Security regression: a root that exists but resolves somewhere else (tampered
    via a symlink) must still be rejected outright, never "repaired" by trusting
    whatever that symlink points to."""
    project_root = root(bridge)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    remove_tree(project_root)
    os.symlink(elsewhere, project_root)

    with pytest.raises(BridgeError, match="moved or is unavailable"):
        bridge.project("demo")
