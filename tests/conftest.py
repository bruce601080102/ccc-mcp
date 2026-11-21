import os
import shutil
import stat
import subprocess

import pytest

from ccc_mcp.service import CCCMCP


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
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
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


def pytest_collection_modifyitems(items):
    for item in items:
        if item.path.name in INTEGRATION_FILES:
            item.add_marker(pytest.mark.integration)
