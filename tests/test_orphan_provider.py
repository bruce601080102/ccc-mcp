"""A worker that is SIGKILLed must not leave its provider CLI running.

The Delivery PM's CLI (planning or repair analysis) is read-only, but a developer's CLI WRITES files:
if it outlives its worker, a later redispatch would run a second developer beside it. Each test kills
a real worker process, lets the recovery path run, and checks that the old CLI process is gone before
anything can be started again. Evidence level: a scripted stand-in CLI; real processes and signals.
"""

import json
import os
import signal
import subprocess
import sys
import time

import psutil
import pytest
from phase_support import (
    BUGGY,
    FAKE_PHASE_CLI,
    cli_calls,
    install_fake_cli,
    make_ready_goal,
    make_slug_project,
)

pytestmark = pytest.mark.skipif(os.name != "posix", reason="SIGKILL semantics are POSIX")
PYTEST = [sys.executable, "-m", "pytest", "-q"]
REQUIREMENT = "Build a slug tool with tests."


def wait_for(condition, timeout=40):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.1)
    return False


def cli_pids(log):
    return [c["pid"] for c in cli_calls(log) if "pid" in c]


@pytest.fixture
def slow(bridge, human, tmp_path, monkeypatch):
    """The scripted CLI, but a call listed in `hang` records its pid and then never finishes."""
    import phase_support

    text = FAKE_PHASE_CLI.replace(
        'with open(os.environ["FAKE_CLI_LOG"], "a") as log:',
        'if key in config.get("hang", []):\n'
        '    open(os.environ["FAKE_CLI_CONFIG"] + ".pid." + key, "w").write(str(os.getpid()))\n'
        '    import time; time.sleep(300)\n'
        'with open(os.environ["FAKE_CLI_LOG"], "a") as log:', 1)
    monkeypatch.setattr(phase_support, "FAKE_PHASE_CLI", text)
    bridge.spawn_phase_worker = lambda run_id: None
    bridge.spawn_worker = lambda execution_id: None
    created = []

    def pid_of(config_path, key):
        path = f"{config_path}.pid.{key}"
        assert wait_for(lambda: os.path.exists(path) and open(path).read().strip()), key
        pid = int(open(path).read())
        created.append(pid)
        return pid

    yield bridge, pid_of
    for pid in created:
        try:
            psutil.Process(pid).kill()
        except psutil.Error:
            pass


def alive(pid):
    try:
        return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except psutil.NoSuchProcess:
        return False


def kill_worker(command, home_flag):
    worker = subprocess.Popen(
        command, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return worker


def test_a_killed_planning_worker_leaves_no_provider_cli_running(slow, tmp_path, monkeypatch):
    bridge, pid_of = slow
    config, _ = install_fake_cli(tmp_path, monkeypatch, hang=["SA"])
    bridge.configure_role("demo", "SA", "codex")
    monkeypatch.setenv("CCC_MCP_ENABLE_REAL_AGENTS", "1")
    goal = bridge.create_goal("demo", REQUIREMENT)
    run = bridge.request_goal_plan("demo", goal["id"], provider="codex", spawn=False)
    worker = kill_worker([sys.executable, "-m", "ccc_mcp.goal_worker", "--home", str(bridge.home),
                          "--run", run["id"]], None)
    try:
        orphan = pid_of(config, "SA")
        assert alive(orphan)
        os.kill(worker.pid, signal.SIGKILL)
        worker.wait(timeout=10)
        assert alive(orphan)                                 # the CLI outlives its worker...

        assert bridge.get_goal("demo", goal["id"])["state"] == "DRAFT"       # ...until recovery runs
        assert wait_for(lambda: not alive(orphan), 10), "the orphan planning CLI was left running"
        # no second planner beside it, and nothing late can be stored: the run is closed for good
        again = bridge.request_goal_plan("demo", goal["id"], provider="codex", spawn=False)
        assert again["seq"] == 2
        assert bridge.store.one("SELECT status FROM goal_plan_runs WHERE seq=1")["status"] == "FAILED"
    finally:
        if worker.poll() is None:
            worker.kill()


def test_a_killed_developer_worker_leaves_no_writing_cli_before_any_redispatch(slow, tmp_path, monkeypatch):
    bridge, pid_of = slow
    make_slug_project(bridge, tmp_path)
    config, _ = install_fake_cli(tmp_path, monkeypatch, initial=[{"file": "app.py", "content": BUGGY}],
                                 hang=["RD"])
    goal = make_ready_goal(bridge, PYTEST)
    run = bridge.start_phase_run("demo", goal["id"], "P1", adapter="real")
    for _ in range(12):                                      # analyst and designer finish in-process
        queued = bridge.store.one("SELECT id,role FROM agent_executions WHERE status='QUEUED'")
        if queued and queued["role"] == "RD":
            break
        if queued:
            from ccc_mcp.worker import run_execution
            run_execution(bridge, queued["id"])
        bridge.step_phase_run(run["id"])
    rd = bridge.store.one("SELECT id FROM agent_executions WHERE status='QUEUED' AND role='RD'")
    worker = kill_worker([sys.executable, "-m", "ccc_mcp.worker", "--home", str(bridge.home),
                          "--execution", rd["id"]], None)
    try:
        orphan = pid_of(config, "RD")
        os.kill(worker.pid, signal.SIGKILL)
        worker.wait(timeout=10)
        assert alive(orphan)

        report = bridge.recover_phase_runs("demo")
        assert report[0]["recovered"]["interrupted_executions"] == [rd["id"]]
        assert not alive(orphan), "a developer CLI was left running after its worker was killed"
        stopped = bridge.get_phase_run("demo", run["id"])
        assert stopped["status"] == "NEEDS_HUMAN" and stopped["reason"] == "INTERRUPTED"
    finally:
        if worker.poll() is None:
            worker.kill()


def test_a_developer_cli_that_cannot_be_stopped_blocks_the_redispatch(slow, tmp_path, monkeypatch):
    bridge, pid_of = slow
    make_slug_project(bridge, tmp_path)
    config, _ = install_fake_cli(tmp_path, monkeypatch, initial=[{"file": "app.py", "content": BUGGY}],
                                 hang=["RD"])
    goal = make_ready_goal(bridge, PYTEST)
    run = bridge.start_phase_run("demo", goal["id"], "P1", adapter="real")
    from ccc_mcp.worker import run_execution
    for _ in range(12):
        queued = bridge.store.one("SELECT id,role FROM agent_executions WHERE status='QUEUED'")
        if queued and queued["role"] == "RD":
            break
        if queued:
            run_execution(bridge, queued["id"])
        bridge.step_phase_run(run["id"])
    rd = bridge.store.one("SELECT id FROM agent_executions WHERE status='QUEUED' AND role='RD'")
    worker = kill_worker([sys.executable, "-m", "ccc_mcp.worker", "--home", str(bridge.home),
                          "--execution", rd["id"]], None)
    try:
        orphan = pid_of(config, "RD")
        os.kill(worker.pid, signal.SIGKILL)
        worker.wait(timeout=10)
        monkeypatch.setattr("ccc_mcp.core.tasks.stop_process", lambda child: "failed")
        result = bridge.recover_workers("demo")
        assert result["interrupted_executions"] == []
        assert result["executions_not_recovered"][0]["execution_id"] == rd["id"]
        assert bridge.store.one("SELECT status FROM agent_executions WHERE id=?", (rd["id"],))["status"] == "RUNNING"
        assert alive(orphan) and json.dumps(result)
    finally:
        if worker.poll() is None:
            worker.kill()


def test_a_killed_phase_worker_leaves_no_delivery_pm_cli_and_no_second_analysis_beside_it(
    slow, tmp_path, monkeypatch
):
    bridge, pid_of = slow
    make_slug_project(bridge, tmp_path)
    config, log = install_fake_cli(
        tmp_path, monkeypatch, initial=[{"file": "app.py", "content": BUGGY}], hang=["PM"])
    goal = make_ready_goal(bridge, PYTEST)
    run = bridge.start_phase_run("demo", goal["id"], "P1", adapter="real")
    from ccc_mcp.worker import run_execution
    real = bridge._on_failure
    bridge._on_failure = lambda *a, **k: {"status": "RUNNING", "acted": "held"}   # not asked yet
    for _ in range(20):
        queued = bridge.store.one("SELECT id FROM agent_executions WHERE status='QUEUED'")
        if queued:
            run_execution(bridge, queued["id"])
        if bridge.store.one("SELECT 1 FROM flow_escalations WHERE status='OPEN'"):
            break
        bridge.step_phase_run(run["id"])
    bridge._on_failure = real
    worker = kill_worker([sys.executable, "-m", "ccc_mcp.phase_worker", "--home", str(bridge.home),
                          "--run", run["id"]], None)
    try:
        orphan = pid_of(config, "PM")
        os.kill(worker.pid, signal.SIGKILL)
        worker.wait(timeout=10)
        assert alive(orphan)

        settings = json.loads(config.read_text())
        settings["hang"] = []                                # the next analysis may answer
        config.write_text(json.dumps(settings))
        bridge.recover_phase_runs("demo")
        assert not alive(orphan), "the orphan Delivery PM CLI was left running"
        statuses = [r["status"] for r in bridge.store.all("SELECT status FROM goal_phase_repairs ORDER BY seq")]
        # the dead call is recorded as interrupted (its late answer can never be used) and exactly
        # one new analysis ran, after the old CLI was gone: never two beside each other
        assert statuses[0] == "INTERRUPTED" and len(statuses) == 2
        assert bridge.get_phase_run("demo", run["id"])["budget"]["dispatches"]["analyses"] == 2
    finally:
        if worker.poll() is None:
            worker.kill()


def builders_of(service, run):
    return service.store.all(
        "SELECT id FROM agent_executions WHERE task_id=? AND role IN ('RD','SD/RD')", (run["task_id"],))
