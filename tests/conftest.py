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


@pytest.fixture
def bridge(tmp_path, monkeypatch):
    repo = tmp_path / "repository"
    repo.mkdir()
    (repo / "app.py").write_text(
        "# original comment\ndef value():\n    return 1\n", encoding="utf-8"
    )
    for args in [
        ["init"],
        ["add", "."],
        [
            "-c",
            "user.name=Test",
            "-c",
            "user.email=test@example.invalid",
            "commit",
            "-m",
            "initial",
        ],
    ]:
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
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
