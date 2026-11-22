import os
import shutil
import stat
import subprocess

import pytest

from ccc_mcp.service import CCCMCP

# Typer forces colour whenever GITHUB_ACTIONS (or FORCE_COLOR / PY_COLORS) is set, and then splits an
# option such as `--confirm-host-tests-stopped` across escape codes, so a plain substring check on
# `--help` output passes on a laptop and fails on a CI runner. Tests read plain text everywhere.
try:
    import typer.rich_utils as _typer_rich_utils

    _typer_rich_utils.FORCE_TERMINAL = False
except ImportError:  # a Typer without rich help formatting has nothing to turn off
    pass


def pytest_addoption(parser):
    parser.addoption(
        "--shard", default="1/1",
        help="run only part of the suite, as INDEX/COUNT (for example 2/4); CI uses it to spread the "
             "slow platforms over several runners. The parts together are the whole suite.",
    )


def remove_tree(path):
    """Remove a test Git repository, including read-only object files on Windows."""

    def make_writable_and_retry(function, name, exc_info):
        try:
            os.chmod(name, os.stat(name).st_mode | stat.S_IWRITE)
            function(name)
        except OSError:
            raise exc_info[1]

    shutil.rmtree(path, onerror=make_writable_and_retry)


@pytest.fixture(scope="session")
def _template_repository(tmp_path_factory):
    """The tiny committed Git project every `bridge` test starts from, built once per run (a worker
    process under xdist) and copied, instead of running four git subprocesses for every test."""
    repo = tmp_path_factory.mktemp("template") / "repository"
    repo.mkdir()
    (repo / "app.py").write_text(
        "# original comment\ndef value():\n    return 1\n", encoding="utf-8"
    )
    for args in [
        ["init"],
        ["add", "."],
        ["-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-m", "initial"],
    ]:
        # The template is copied as soon as this fixture returns. Git's background maintenance
        # can otherwise create and remove locks while copytree is walking the template.
        subprocess.run(
            ["git", "-c", "maintenance.auto=false", "-c", "gc.auto=0", "-C", str(repo), *args],
            check=True, capture_output=True,
        )
    return repo


@pytest.fixture
def bridge(tmp_path, monkeypatch, _template_repository):
    repo = tmp_path / "repository"
    shutil.copytree(_template_repository, repo)
    service = CCCMCP(tmp_path / "home")
    service.register_project(repo, "demo", "建立可跨 Session 恢復的測試專案")
    monkeypatch.setattr(service, "spawn_worker", lambda execution_id: None)
    return service


@pytest.fixture
def human(monkeypatch):
    # Tests substitute the operator ceremony, never a production approved=true API.
    monkeypatch.setattr("ccc_mcp.core.approval.confirm_local", lambda *args: "test-principal")


def approve(bridge, task="TASK-0001", plan="PLAN-A"):
    version = bridge.latest("demo")["version"]
    bridge.propose_plan("demo", task, plan, "新增 value API；保留原始註解。", version)
    bridge.request_approval("demo", task, plan)
    return bridge.approve_local("demo", task, plan)


def dispatch(bridge, role="SA", **kwargs):
    return bridge.dispatch_agent(
        "demo",
        "TASK-0001",
        "codex" if role in {"SA", "REVIEW"} else "claude",
        role,
        bridge.latest("demo")["version"],
        "執行當前任務",
        adapter="fake",
        **kwargs,
    )


# Files whose tests start real worker processes, real pytest host tests or a real browser. They are the
# slow part of the suite and stay in the full run; `pytest -m "not integration"` is the fast loop.
INTEGRATION_FILES = {
    "test_goal_run.py", "test_goal_run_acceptance.py", "test_goal_run_crash.py",
    "test_goal_run_web_cli.py", "test_phase_acceptance.py", "test_phase_runner.py",
    "test_host_test_recovery.py", "test_goal_worker_kill.py", "test_orphan_provider.py",
    "test_work_e2e.py", "test_ui_browser.py",
}


def pytest_collection_modifyitems(config, items):
    for item in items:
        if item.path.name in INTEGRATION_FILES:
            item.add_marker(pytest.mark.integration)
    # `--shard INDEX/COUNT`: deal the tests out one by one, in a fixed order, so every part gets a share
    # of each slow file. The tests do not depend on one another (they already run on any worker under
    # xdist).
    index, count = (int(part) for part in config.getoption("--shard").split("/"))
    if count == 1:
        return
    ordered = sorted(items, key=lambda item: item.nodeid)
    mine = {id(item) for position, item in enumerate(ordered) if position % count == index - 1}
    skipped = [item for item in items if id(item) not in mine]
    config.hook.pytest_deselected(items=skipped)
    items[:] = [item for item in items if id(item) in mine]
