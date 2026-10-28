"""Tests for the CLI consolidation work: `project add/create --write` combine
registration and continuous-write authorization into one step (so a new user
no longer needs a separate `project authorize` afterwards), while the legacy
two-step commands keep working with a one-line deprecation hint on stderr —
never mixed into the JSON payload on stdout.
"""

import json

from typer.testing import CliRunner

from ccc_mcp.interfaces.cli import app


def test_project_add_with_write_combines_register_and_authorize(tmp_path, human):
    runner = CliRunner()
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "f.txt").write_text("x", encoding="utf-8")

    result = runner.invoke(
        app, ["--home", str(home), "project", "add", str(repo), "--id", "wproj", "--write"]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert payload["mode"] == "read-write"

    shown = runner.invoke(app, ["--home", str(home), "--project", "wproj", "config", "show"])
    assert json.loads(shown.stdout)["project_access"]["mode"] == "read-write"


def test_project_add_without_write_stays_read_only(tmp_path, human):
    runner = CliRunner()
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()

    result = runner.invoke(app, ["--home", str(home), "project", "add", str(repo), "--id", "ro1"])
    assert result.exit_code == 0, result.output
    payload = json.loads(result.stdout)
    assert "mode" not in payload  # register_project's own return, not an access grant

    shown = runner.invoke(app, ["--home", str(home), "--project", "ro1", "config", "show"])
    assert json.loads(shown.stdout)["project_access"]["mode"] == "read-only"


def test_project_create_with_write_combines_create_and_authorize(tmp_path, human):
    runner = CliRunner()
    home = tmp_path / "home"

    result = runner.invoke(
        app,
        [
            "--home",
            str(home),
            "project",
            "create",
            "wproj2",
            "--path",
            str(tmp_path / "newp"),
            "--write",
        ],
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.stdout)["mode"] == "read-write"


def test_project_add_write_still_needs_interactive_confirmation_without_the_human_fixture(
    tmp_path,
):
    runner = CliRunner()
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()

    result = runner.invoke(
        app, ["--home", str(home), "project", "add", str(repo), "--id", "noninteractive", "--write"]
    )
    assert result.exit_code != 0
    assert "interactive" in str(result.exception).lower()


def test_init_and_project_authorize_print_deprecation_hints_to_stderr_only(tmp_path, human):
    runner = CliRunner()
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()

    added = runner.invoke(app, ["--home", str(home), "init", str(repo), "--id", "dep1"])
    assert added.exit_code == 0, added.output
    assert "project add" in added.stderr
    # stdout stays pure JSON — scripts piping it to `jq` must not break.
    json.loads(added.stdout)

    authorized = runner.invoke(app, ["--home", str(home), "project", "authorize", "dep1"])
    assert authorized.exit_code == 0, authorized.output
    assert "--write" in authorized.stderr
    assert json.loads(authorized.stdout)["mode"] == "read-write"


def test_clear_history_and_clear_history_alias_both_still_work(tmp_path, human):
    runner = CliRunner()
    home = tmp_path / "home"
    repo = tmp_path / "repo"
    repo.mkdir()
    runner.invoke(app, ["--home", str(home), "project", "add", str(repo), "--id", "ch1"])

    one = runner.invoke(app, ["--home", str(home), "--project", "ch1", "clear-history"])
    assert one.exit_code == 0, one.output
    assert json.loads(one.stdout)["history_cleared"] is True

    two = runner.invoke(app, ["--home", str(home), "--project", "ch1", "clear", "history"])
    assert two.exit_code == 0, two.output
    assert json.loads(two.stdout)["history_cleared"] is True
