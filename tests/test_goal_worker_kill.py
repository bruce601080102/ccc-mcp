"""A planning worker that is killed outright must not leave its goal stuck in "planning".

Stage A's own test simulated the dead worker by holding its lock in the test process. This one
starts the real `python -m ccc_mcp.goal_worker` process, lets it call a (fake) native CLI that
never finishes, and sends SIGKILL: no `finally`, no `except`, nothing in the worker runs.
"""

import os
import signal
import stat
import subprocess
import sys
import time

import pytest

REQUIREMENT = "Add a command that prints the project version and cover it with a test."

SLOW_CLI = '''#!{python}
import json, os, sys, time
argv = sys.argv[1:]
if "--version" in argv:
    print("slow-cli 1"); sys.exit(0)
if "--help" in argv:
    print(" ".join(["--print --output-format --json-schema --tools --permission-mode",
        "--setting-sources --strict-mcp-config --mcp-config --no-session-persistence --safe-mode",
        "--sandbox --json --output-schema --output-last-message --ignore-user-config",
        "--skip-git-repo-check --disable-slash-commands --allowedTools"]))
    sys.exit(0)
open(os.environ["SLOW_CLI_PID"], "w").write(str(os.getpid()))
sys.stdin.read()
time.sleep(120)
'''


@pytest.fixture
def slow_codex(tmp_path, monkeypatch):
    folder = tmp_path / "bin"
    folder.mkdir()
    script = folder / "codex"
    script.write_text(SLOW_CLI.format(python=sys.executable), encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    pid_file = tmp_path / "cli.pid"
    monkeypatch.setenv("PATH", f"{folder}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("SLOW_CLI_PID", str(pid_file))
    monkeypatch.setenv("CCC_MCP_ENABLE_REAL_AGENTS", "1")
    yield pid_file
    if pid_file.exists():  # the provider process outlives a killed worker; do not leave it behind
        try:
            os.kill(int(pid_file.read_text()), signal.SIGKILL)
        except (OSError, ValueError):
            pass


def wait_for(condition, timeout=30):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.1)
    return False


@pytest.mark.skipif(os.name != "posix", reason="SIGKILL semantics are POSIX")
def test_a_killed_planning_worker_does_not_leave_the_goal_planning_forever(bridge, slow_codex):
    bridge.configure_role("demo", "SA", "codex")
    goal = bridge.create_goal("demo", REQUIREMENT)
    run = bridge.request_goal_plan("demo", goal["id"], provider="codex", spawn=False)
    worker = subprocess.Popen(
        [sys.executable, "-m", "ccc_mcp.goal_worker", "--home", str(bridge.home), "--run", run["id"]],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        assert wait_for(lambda: slow_codex.exists()), "the fake provider never started"
        assert bridge.get_goal("demo", goal["id"])["state"] == "PLANNING"

        os.kill(worker.pid, signal.SIGKILL)
        worker.wait(timeout=10)

        # The lock the dead worker held is released by the kernel; the next read closes the run.
        assert wait_for(lambda: bridge.get_goal("demo", goal["id"])["state"] != "PLANNING", 10)
        after = bridge.get_goal("demo", goal["id"])
        assert after["state"] == "DRAFT" and after["plan"] is None
        assert after["last_run"]["status"] == "FAILED"
        assert after["last_run"]["failure_kind"] == "INTERRUPTED"
        events = [event["kind"] for event in after["events"]]
        assert events.count("plan_run_failed") == 1

        # The goal is usable again: a new explicit request is accepted, nothing is blocked.
        again = bridge.request_goal_plan("demo", goal["id"], provider="codex", spawn=False)
        assert again["status"] == "QUEUED" and again["seq"] == 2
        # Reading again never closes a run twice or invents events.
        bridge.get_goal("demo", goal["id"])
        assert [e["kind"] for e in bridge.get_goal("demo", goal["id"])["events"]].count(
            "plan_run_failed") == 1
    finally:
        if worker.poll() is None:
            worker.kill()
            worker.wait()


@pytest.mark.skipif(os.name != "posix", reason="SIGKILL semantics are POSIX")
def test_a_live_planning_worker_is_never_closed_by_a_read(bridge, slow_codex):
    bridge.configure_role("demo", "SA", "codex")
    goal = bridge.create_goal("demo", REQUIREMENT)
    run = bridge.request_goal_plan("demo", goal["id"], provider="codex", spawn=False)
    worker = subprocess.Popen(
        [sys.executable, "-m", "ccc_mcp.goal_worker", "--home", str(bridge.home), "--run", run["id"]],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        assert wait_for(lambda: slow_codex.exists())
        for _ in range(3):
            assert bridge.get_goal("demo", goal["id"])["state"] == "PLANNING"
        stored = bridge.store.one("SELECT status FROM goal_plan_runs WHERE id=?", (run["id"],))
        assert stored["status"] == "RUNNING"
    finally:
        worker.kill()
        worker.wait()
