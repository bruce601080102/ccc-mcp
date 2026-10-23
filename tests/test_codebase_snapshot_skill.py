"""adapters/codebase_snapshot.py: turn a codebase-memory-mcp index into a Skill.

Uses a fake CLI runner so the real tool is not required (CI machines do not have it).
"""

import re
from pathlib import Path

import pytest

from ccc_mcp.adapters import codebase_snapshot as snap
from ccc_mcp.adapters import skills
from ccc_mcp.infra.errors import BridgeError

PROJECT = "Users-me-app"


def fake_runner(repo, *, indexed, calls=None):
    state = {"indexed": indexed}

    def run(tool, args):
        if calls is not None:
            calls.append(tool)
        if tool == "list_projects":
            rows = f"  {PROJECT} {repo} main\n" if state["indexed"] else ""
            return f"projects: 1  (cols: name root_path branch)\n{rows}total: 1"
        if tool == "index_repository":
            state["indexed"] = True
            return "ok"
        if tool == "index_status":
            return "status: ready\nindexed_at: 2026-10-20T04:18:37Z"
        if tool == "get_architecture":
            aspect = (args.get("aspects") or ["summary"])[0]
            return f"aspect={aspect}\nFunction 345"
        raise AssertionError(tool)

    return run


def test_builds_a_skill_whose_references_all_exist(tmp_path):
    repo, out = tmp_path / "app", tmp_path / "skill"
    repo.mkdir()
    result = snap.build_snapshot(fake_runner(repo.resolve(), indexed=True), repo, out)
    assert result["skill"] == "codebase-snapshot-app"

    body = (out / "SKILL.md").read_text(encoding="utf-8")
    listed = re.findall(r"`(references/[a-z_]+\.md)`", body)
    assert len(listed) == len(snap.SECTIONS)
    assert all((out / path).is_file() for path in listed)
    # The canary an agent can only get by reading the deeper file.
    assert "Function 345" in (out / "references" / "architecture.md").read_text(encoding="utf-8")
    assert "Function 345" not in body


def test_installs_through_the_normal_skill_path(tmp_path):
    repo, out, home = tmp_path / "app", tmp_path / "skill", tmp_path / "home"
    repo.mkdir()
    snap.build_snapshot(fake_runner(repo.resolve(), indexed=True), repo, out)
    skills.install_skill(home, str(out))
    entry = next(s for s in skills.available_skills(home) if s["id"] == "codebase-snapshot-app")
    assert (Path(entry["path"]) / "references" / "hotspots.md").is_file()
    assert "available_skills" in entry["content"]  # the path rule agents need


def test_an_unindexed_repo_is_indexed_automatically(tmp_path):
    repo = tmp_path / "app"
    repo.mkdir()
    calls = []
    result = snap.build_snapshot(
        fake_runner(repo.resolve(), indexed=False, calls=calls), repo, tmp_path / "skill"
    )
    assert calls.index("index_repository") < calls.index("get_architecture")
    assert result["indexed_now"] is True

    calls.clear()
    result = snap.build_snapshot(
        fake_runner(repo.resolve(), indexed=True, calls=calls), repo, tmp_path / "skill2"
    )
    assert "index_repository" not in calls
    assert result["indexed_now"] is False


def test_refuses_a_non_empty_output_directory(tmp_path):
    repo, out = tmp_path / "app", tmp_path / "skill"
    repo.mkdir()
    out.mkdir()
    (out / "keep.txt").write_text("x")
    with pytest.raises(BridgeError, match="not empty"):
        snap.build_snapshot(fake_runner(repo.resolve(), indexed=True), repo, out)


def test_skill_install_accepts_the_codebase_memory_source(tmp_path, monkeypatch):
    repo, home = tmp_path / "app", tmp_path / "home"
    repo.mkdir()
    monkeypatch.setattr(
        "ccc_mcp.adapters.codebase_snapshot.resolve_binary", lambda explicit=None: "fake-cbm"
    )
    monkeypatch.setattr(
        "ccc_mcp.adapters.codebase_snapshot.cli_runner",
        lambda binary: fake_runner(repo.resolve(), indexed=True),
    )
    installed = skills.install_skill(home, f"codebase-memory:{repo}")
    assert installed["id"] == "codebase-snapshot-app"
    # An empty path falls back to the selected project's root (supplied by the CLI).
    again = skills.install_skill(home, "codebase-memory:", repo_root=str(repo))
    assert again["id"] == "codebase-snapshot-app"
    with pytest.raises(BridgeError, match="repo path"):
        skills.install_skill(home, "codebase-memory:")


def test_cli_uses_the_selected_projects_root_when_the_path_is_empty(bridge, monkeypatch):
    import json

    from typer.testing import CliRunner

    from ccc_mcp.interfaces.cli import app

    root = Path(bridge.project("demo")["root"]).resolve()
    monkeypatch.setattr(
        "ccc_mcp.adapters.codebase_snapshot.resolve_binary", lambda explicit=None: "fake-cbm"
    )
    monkeypatch.setattr(
        "ccc_mcp.adapters.codebase_snapshot.cli_runner",
        lambda binary: fake_runner(root, indexed=True),
    )
    result = CliRunner().invoke(
        app, ["--home", str(bridge.home), "--project", "demo", "skill", "install", "codebase-memory:"]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["id"] == f"codebase-snapshot-{snap.slug(root.name)}"
