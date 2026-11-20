"""Stage C: what happens when the supervisor dies, restarts or is woken twice.

Real `kill -9` of a real process is used where a test says so (a child Python process that runs the
real `run_goal_worker`, with one method replaced by `os.kill(getpid(), SIGKILL)` so it dies at exactly
the named moment: nothing in `finally` or `except` runs). The agents are the scripted fake ones; the
host test is a real subprocess. EVIDENCE LEVEL: no model, no provider, no quota.
"""

import os
import signal
import subprocess
import sys
import threading
import time

import pytest
from phase_support import (
    custom_plan,
    drive,
    drive_goal,
    make_ready_goal,
    scripted_test,
)

from ccc_mcp.service import CCCMCP

pytestmark = pytest.mark.skipif(os.name != "posix", reason="SIGKILL semantics are POSIX")

CHILD = r'''
import os, signal, sys
from ccc_mcp.service import CCCMCP
home, run_id, where = sys.argv[1:4]
b = CCCMCP(home)
b.spawn_phase_worker = lambda r: None
b.spawn_goal_worker = lambda r: None
def die(*args, **kwargs):
    os.kill(os.getpid(), signal.SIGKILL)
if where == "before_ledger":
    b._record_passed = die
elif where == "after_ledger":
    original = b._record_passed
    def after(run, phase):
        original(run, phase)
        die()
    b._record_passed = after
elif where == "before_next_phase":
    b._start_next_phase = die
b.run_goal_worker(run_id)
'''


@pytest.fixture
def env(bridge, human, tmp_path, monkeypatch):
    bridge.planning_adapter_default = "fake"
    bridge.phase_adapter_default = "fake"
    bridge.goal_adapter_default = "fake"
    bridge.spawn_phase_worker = lambda run_id: None
    bridge.spawn_goal_worker = lambda run_id: None
    return bridge


def rows(env, sql, *args):
    return env.store.all(sql, args)


def counts(env):
    return {
        "p2_runs": rows(env, "SELECT COUNT(*) AS n FROM goal_phase_runs WHERE phase_id='P2'")[0]["n"],
        "p2_tasks": rows(env, "SELECT COUNT(*) AS n FROM tasks WHERE id='GOAL-0001-P2'")[0]["n"],
        "ledger": [r["phase_id"] for r in rows(
            env, "SELECT phase_id FROM goal_run_phases ORDER BY rowid")],
        "p1_executions": rows(
            env, "SELECT COUNT(*) AS n FROM agent_executions WHERE task_id='GOAL-0001-P1'")[0]["n"],
    }


def phase_one_passed(env, tmp_path, outcomes=("pass",)):
    """A started goal run whose first phase has passed but which the supervisor has not looked at
    since: the moment a crash would cut it off."""
    command = scripted_test(tmp_path, list(outcomes))
    plan = custom_plan(command, [("P1", []), ("P2", ["P1"])])
    goal = make_ready_goal(env, command, plan=plan)
    run = env.start_goal_run("demo", goal["id"], adapter="fake")
    started = env.step_goal_run(run["id"])
    assert started["acted"] == "phase_started"
    phase = env.store.one("SELECT id FROM goal_phase_runs WHERE phase_id='P1'")["id"]
    assert drive(env, phase)["status"] == "PASSED"
    return run["id"], goal


def kill_child(env, run_id, where):
    child = subprocess.run(
        [sys.executable, "-c", CHILD, str(env.home), run_id, where], capture_output=True, timeout=120,
        check=False)
    assert child.returncode == -signal.SIGKILL, child.stderr.decode()[-500:]


@pytest.mark.parametrize("where", ["before_ledger", "after_ledger", "before_next_phase"])
def test_a_supervisor_killed_after_a_phase_passed_loses_nothing_and_starts_the_next_phase_once(
    env, tmp_path, where
):
    run_id, _ = phase_one_passed(env, tmp_path)
    before = counts(env)
    assert before["p2_runs"] == 0 and before["ledger"] == []
    kill_child(env, run_id, where)

    after_kill = counts(env)
    assert after_kill["p2_runs"] == 0 and after_kill["p2_tasks"] == 0      # nothing half-made
    assert after_kill["ledger"] == (["P1"] if where != "before_ledger" else [])
    assert not env._supervisor_alive(run_id)                               # the lock died with it
    assert env.get_goal_run("demo", run_id)["stalled"] in (True, False)

    report = env.recover_goal_runs("demo")                                 # the restart / recover path
    assert report[0]["goal_run_id"] == run_id
    done = drive_goal(env, run_id)
    assert done["status"] == "PASSED", (done["reason"], done["detail"])
    final = counts(env)
    assert final["ledger"] == ["P1", "P2"]
    assert final["p2_runs"] == 1 and final["p2_tasks"] == 1
    assert final["p1_executions"] == 4                                     # P1 was not run again
    assert len([e for e in done["events"] if e["kind"] == "goal_phase_recorded"]) == 2


def test_a_service_restart_between_the_phases_continues_from_sqlite(env, tmp_path):
    run_id, _ = phase_one_passed(env, tmp_path)
    env.step_goal_run(run_id)                       # records P1 and starts P2 ...
    env.store.execute("SELECT 1")
    restarted = CCCMCP(env.home)                    # ... and the whole service is restarted
    restarted.phase_adapter_default = restarted.goal_adapter_default = "fake"
    restarted.spawn_phase_worker = lambda r: None
    restarted.spawn_goal_worker = lambda r: None
    done = drive_goal(restarted, run_id)
    assert done["status"] == "PASSED", (done["reason"], done["detail"])
    assert counts(restarted)["p2_runs"] == 1 and counts(restarted)["p2_tasks"] == 1


def test_waking_a_goal_run_many_times_never_makes_a_second_task(env, tmp_path):
    run_id, _ = phase_one_passed(env, tmp_path)
    for _ in range(5):
        env.wake_goal_run(run_id)
        env.run_goal_worker(run_id)
    assert counts(env)["p2_runs"] == 1 and counts(env)["p2_tasks"] == 1
    assert env.get_goal_run("demo", run_id)["events"]
    done = drive_goal(env, run_id)
    assert done["status"] == "PASSED"
    assert counts(env)["ledger"] == ["P1", "P2"]


def test_two_supervisors_looking_at_the_same_goal_run_act_once(env, tmp_path, monkeypatch):
    run_id, _ = phase_one_passed(env, tmp_path)
    original = env._start_next_phase
    started, release = threading.Event(), threading.Event()

    def slow(run, plan, phase):
        started.set()
        release.wait(30)                            # the first supervisor is busy deciding
        return original(run, plan, phase)

    monkeypatch.setattr(env, "_start_next_phase", slow)
    outcomes = {}

    def go(name):
        outcomes[name] = env.run_goal_worker(run_id)

    first = threading.Thread(target=go, args=("first",))
    first.start()
    assert started.wait(30)
    go("second")                                    # finds the lock held and leaves
    release.set()
    first.join(60)
    assert outcomes["second"].get("busy") is True
    assert counts(env)["p2_runs"] == 1 and counts(env)["p2_tasks"] == 1


def test_a_supervisor_killed_during_the_final_host_test_never_runs_a_second_test_beside_it(
    env, tmp_path
):
    # calls: P1 pass, P2 pass, the final verification hangs (and is killed with its supervisor),
    # the verification after recovery passes.
    command = scripted_test(tmp_path, ["pass", "pass", "hang", "pass"])
    plan = custom_plan(command, [("P1", []), ("P2", ["P1"])])
    goal = make_ready_goal(env, command, plan=plan)
    run = env.start_goal_run("demo", goal["id"], adapter="fake")
    # everything up to the final verification, in this process
    while True:
        env.step_goal_run(run["id"])
        for row in rows(env, "SELECT id FROM goal_phase_runs WHERE status IN ('STARTING','RUNNING')"):
            drive(env, row["id"])
        passed = rows(env, "SELECT COUNT(*) AS n FROM goal_phase_runs WHERE status='PASSED'")[0]["n"]
        if passed == 2:
            break
    child = subprocess.Popen(
        [sys.executable, "-c", CHILD, str(env.home), run["id"], "none"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    orphan = None
    try:
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            test = rows(env, "SELECT child FROM host_test_runs WHERE status='RUNNING'")
            if test and test[0]["child"]:
                orphan = __import__("json").loads(test[0]["child"])["pid"]
                break
            time.sleep(0.2)
        assert orphan, "the final host test never started"
        child.send_signal(signal.SIGKILL)
        child.wait(30)
        assert rows(env, "SELECT status FROM goal_verifications")[0]["status"] == "RUNNING"
        os.kill(orphan, 0)                          # the test process outlived its supervisor

        report = env.recover_goal_runs("demo")
        done = drive_goal(env, run["id"])
        assert done["status"] == "PASSED", (done["reason"], done["detail"], report)
        assert [v["status"] for v in done["verifications"]] == ["INTERRUPTED", "PASSED"]
        with pytest.raises(OSError):
            time.sleep(0.5)
            os.kill(orphan, 0)                      # the orphan was stopped, provably the recorded one
        import json
        assert json.loads((tmp_path / "gate_state.json").read_text())["calls"] == 4
    finally:
        if orphan:
            try:
                os.kill(orphan, signal.SIGKILL)
            except OSError:
                pass
        if child.poll() is None:
            child.kill()


def test_with_every_process_dead_and_no_console_nothing_moves_until_someone_wakes_or_recovers_it(
    env, tmp_path
):
    """The honest limit of an event-driven supervisor, pinned: nothing wakes a run whose processes
    all died. The page can say so (`stalled`), the console's watchdog can wake it, and
    `goal run-recover` can continue it; without one of those it simply waits in SQLite."""
    run_id, _ = phase_one_passed(env, tmp_path)
    kill_child(env, run_id, "before_ledger")
    env.goal_stall_seconds = 0
    time.sleep(0.2)
    before = counts(env)
    view = env.get_goal_run("demo", run_id)
    assert view["status"] == "RUNNING" and view["stalled"] is True and view["supervisor_alive"] is False
    assert view["automation"]["resident_daemon"] is False
    time.sleep(1)
    assert counts(env) == before                           # no thread, no daemon: nothing happened
    assert env.wake_stalled_goal_runs() == [run_id]        # what the console's watchdog does: wake only
    assert counts(env) == before                           # (spawning is patched out here) a wake alone
    done = drive_goal(env, run_id)                         # is not progress; a worker or recovery is
    assert done["status"] == "PASSED"
