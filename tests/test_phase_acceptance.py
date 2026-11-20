"""Stage B acceptance: a phase repairs itself, with a simulated CLI that really edits files.

EVIDENCE LEVEL (say it before reading the results)
  * simulated model     - the "Codex" and "Claude" here are a script (see phase_support.py).
  * real code paths     - the real Codex and Claude adapters, the real worker function
                          (`run_execution`), the existing Task/Workflow engine and Review Gate, the
                          Delivery PM through the same adapters, and the phase runner.
  * real host test      - `python -m pytest` runs in the project's workspace, for real.
  * real model          - NOT exercised. No provider, no quota, no network is used.
"""

import json
import sys

import pytest
from phase_support import (
    BUGGY,
    FIXED,
    cli_calls,
    drive,
    install_fake_cli,
    make_ready_goal,
    make_slug_project,
)

from ccc_mcp.infra.errors import BridgeError

PYTEST = [sys.executable, "-m", "pytest", "-q"]
FIX = {"file": "app.py", "old": 'return text.lower().replace(" ", "-")',
       "new": 'return "-".join(text.lower().split())'}


@pytest.fixture
def slug(bridge, human, tmp_path, monkeypatch):
    bridge.phase_adapter_default = "real"
    bridge.spawn_phase_worker = lambda run_id: None
    root = make_slug_project(bridge, tmp_path)
    return bridge, root


def workspace_file(service, run, name):
    row = service.store.one(
        "SELECT worktree FROM agent_executions WHERE task_id=? AND worktree IS NOT NULL "
        "ORDER BY created DESC LIMIT 1", (run["task_id"],))
    from pathlib import Path
    return (Path(row["worktree"]) / name).read_text(encoding="utf-8")


def test_a_phase_fails_a_real_test_is_analysed_repaired_and_passes(slug, tmp_path, monkeypatch):
    bridge, root = slug
    _, log = install_fake_cli(
        tmp_path, monkeypatch, initial=[{"file": "app.py", "content": BUGGY}], pm_fix=FIX)
    goal = make_ready_goal(bridge, PYTEST, phases=2)                         # 1, 2: approved, 2 phases
    assert len(goal["plan"]["plan"]["phases"]) == 2

    run = bridge.start_phase_run("demo", goal["id"], "P1")                   # 3: started on purpose
    assert run["task_id"] == "GOAL-0001-P1" and run["status"] in ("STARTING", "RUNNING")  # 4
    done = drive(bridge, run["id"])

    # 13: recorded as PASSED, by the real Review Gate and a real passing host test
    assert done["status"] == "PASSED", (done["reason"], done["detail"])
    assert bridge.task("demo", done["task_id"])["state"] == "COMPLETED"
    assert [g["verdict"] for g in done["gates"]] == ["PASS"]
    assert done["host_test"]["executed_by_host"] is True and done["host_test"]["passed"] is True
    assert "2 passed" in done["host_test"]["stdout"]                         # 11: second round passed

    calls = cli_calls(log)
    roles = [(c["role"], c["schema"]) for c in calls]
    assert roles == [("SA", "agent_report"), ("SD", "agent_report"), ("RD", "agent_report"),
                     ("SA", "goal_repair"), ("RD", "agent_report"), ("REVIEW", "agent_report")]

    # 5: the developer really changed the file in the first round, and the first test really failed
    first_dev, pm, second_dev = calls[2], calls[3], calls[4]
    assert "REPAIR (attempt" not in first_dev["instruction"]
    first = bridge.store.all(
        "SELECT result FROM agent_executions WHERE role='RD' ORDER BY created")
    assert "app.py" in json.loads(first[0]["result"])["changed_files"]
    assert done["attempts"][0]["status"] == "FAILED"
    assert done["attempts"][0]["evidence"]["kind"] == "host_test_failed"     # 6: real failure
    assert "assert" in " ".join(done["attempts"][0]["evidence"]["signature_lines"]).lower()

    # 7: the Delivery PM was given the whole failure, not a summary
    seen = pm["content"]
    assert pm["schema"] == "goal_repair" and seen["goal"]["requirement"].startswith("Build a slug tool")
    assert seen["acceptance_criteria"][0]["id"] == "C1"
    assert seen["approved_plan"] == {"version": goal["plan"]["version"], "sha256": goal["plan"]["sha256"]}
    assert seen["failure"]["kind"] == "host_test_failed"
    assert "FAILED test_app.py::test_extra_spaces" in seen["failure"]["host_test"]["stdout"]
    assert seen["failure"]["host_test"]["executed_by_host"] is True
    assert "+    return text.lower().replace" in seen["failure"]["diff"]        # the real git diff
    assert seen["failure"]["changed_files"] == ["app.py"]
    assert seen["budget_remaining"]["attempts_left_after_this_one"] == 1
    assert seen["host_test_command"] == PYTEST
    assert "--sandbox" in pm["argv"] and pm["argv"][pm["argv"].index("--sandbox") + 1] == "read-only"

    # 8: a structured repair spec
    (repair,) = done["repairs"]
    assert repair["decision"] == "repair" and repair["spec"]["rerun_tests"] == PYTEST
    assert repair["spec"]["root_causes"][0]["confidence"] == "confirmed"
    assert repair["spec"]["root_causes"][0]["evidence"][0]["verified"] is True
    assert [c["criterion_id"] for c in repair["spec"]["completion_conditions"]] == ["C1"]

    # 9: sent to the developer automatically, nobody relayed anything (this test never dispatched);
    # 10: and the simulated CLI changed the file because of what the spec said
    assert "REPAIR (attempt 2" in second_dev["instruction"]
    assert "REPLACE_IN app.py" in second_dev["instruction"]
    assert workspace_file(bridge, done, "app.py") == FIXED
    # the developer's permissions did not change between the rounds
    for flag in ("--sandbox", "--permission-mode", "--tools"):
        if flag in first_dev["argv"]:
            assert second_dev["argv"][second_dev["argv"].index(flag) + 1] == \
                first_dev["argv"][first_dev["argv"].index(flag) + 1]
    assert [a["status"] for a in done["attempts"]] == ["FAILED", "PASSED"]

    # 14: phase 2 has not started: no run, no Task, no execution of its own
    assert [r["phase_id"] for r in bridge.get_goal("demo", goal["id"])["phase_runs"]] == ["P1"]
    assert [t["id"] for t in bridge.list_tasks("demo")] == ["GOAL-0001-P1"]
    assert not bridge.store.one("SELECT 1 FROM agent_executions WHERE task_id='GOAL-0001-P2'")
    # nothing was committed or pushed on anyone's behalf
    import subprocess
    log_out = subprocess.run(["git", "-C", str(root), "log", "--oneline"], capture_output=True,
                             text=True, check=True).stdout.splitlines()
    assert len(log_out) == 2                                                 # the fixture's two commits


def test_three_attempts_that_never_pass_stop_and_the_fourth_never_happens(slug, tmp_path, monkeypatch):
    bridge, _ = slug
    variants = [
        {"file": "app.py", "content": BUGGY},
        {"file": "app.py", "content": 'def slugify(text):\n    return text.lower().strip().replace(" ", "-")\n'},
        {"file": "app.py", "content": 'def slugify(text):\n    return text.lower().replace(" ", "")\n'},
        {"file": "app.py", "content": FIXED},
    ]
    _, log = install_fake_cli(tmp_path, monkeypatch, variants=variants, pm_fix=FIX)
    goal = make_ready_goal(bridge, PYTEST)
    done = drive(bridge, bridge.start_phase_run("demo", goal["id"], "P1")["id"])

    assert done["status"] == "EXHAUSTED" and done["reason"] == "ATTEMPTS_EXHAUSTED", done
    developer = [c for c in cli_calls(log) if c["role"] == "RD"]
    assert len(developer) == 3                                              # the fourth never ran
    assert [a["status"] for a in done["attempts"]] == ["FAILED"] * 3
    assert len({a["evidence"]["signature"] for a in done["attempts"]}) == 3  # it kept changing, so no
    assert bridge.task("demo", done["task_id"])["state"] == "CANCELLED"      # "no progress" stop
    n = len(cli_calls(log))
    drive(bridge, done["id"])
    assert len(cli_calls(log)) == n


def test_a_repair_that_edits_the_test_configuration_is_never_a_pass(slug, tmp_path, monkeypatch):
    bridge, _ = slug
    cheat = "import app\napp.slugify = lambda text: '-'.join(text.lower().split())\n"
    install_fake_cli(
        tmp_path, monkeypatch, initial=[{"file": "app.py", "content": BUGGY}], pm_fix=FIX,
        also_write=[{"file": "conftest.py", "content": cheat, "on": [2]}])
    goal = make_ready_goal(bridge, PYTEST)
    done = drive(bridge, bridge.start_phase_run("demo", goal["id"], "P1")["id"])

    assert done["status"] == "NEEDS_HUMAN" and done["reason"] == "PROTECTED_PATH", done
    assert done["status"] != "PASSED"
    assert any(p["path"] == "conftest.py" for p in done["detail"]["paths"])
    assert done["host_test"]["passed"] is True            # the tests did pass, and it still is not a pass
    with pytest.raises(Exception, match="protected"):
        bridge.resume_phase_run("demo", done["id"], spawn=False)


def test_a_provider_that_crashes_waits_for_a_person_then_the_same_step_runs_again(slug, tmp_path, monkeypatch):
    bridge, _ = slug
    _, log = install_fake_cli(
        tmp_path, monkeypatch, initial=[{"file": "app.py", "content": FIXED}],
        fail={"RD": [1]})
    goal = make_ready_goal(bridge, PYTEST)
    run = bridge.start_phase_run("demo", goal["id"], "P1")
    halted = drive(bridge, run["id"])
    assert halted["status"] == "NEEDS_HUMAN" and halted["reason"] == "EXECUTION_ERROR", halted
    assert "provider crash" in halted["detail"]["error"] or halted["detail"]["error"]
    n = len(cli_calls(log))
    drive(bridge, run["id"])
    assert len(cli_calls(log)) == n                         # it does not retry on its own

    bridge.resume_phase_run("demo", run["id"], note="the network was down", spawn=False)
    done = drive(bridge, run["id"])
    assert done["status"] == "PASSED", done
    assert [c["role"] for c in cli_calls(log)].count("RD") == 2
    assert done["attempts"][-1]["kind"] == "REPAIR" and done["budget"]["attempts"]["used"] == 2


def test_the_approved_requirement_and_criteria_are_the_same_after_the_whole_run(slug, tmp_path, monkeypatch):
    bridge, _ = slug
    install_fake_cli(tmp_path, monkeypatch, initial=[{"file": "app.py", "content": BUGGY}], pm_fix=FIX)
    goal = make_ready_goal(bridge, PYTEST)
    run = bridge.start_phase_run("demo", goal["id"], "P1")
    before = bridge.store.one("SELECT * FROM goal_phase_runs WHERE id=?", (run["id"],))
    task_before = bridge.task("demo", run["task_id"])
    import sqlite3
    with pytest.raises(sqlite3.DatabaseError, match="cannot change"):     # the database itself refuses
        bridge.store.execute("UPDATE goal_phase_runs SET criteria='[]' WHERE id=?", (run["id"],))
    done = drive(bridge, run["id"])
    after = bridge.store.one("SELECT * FROM goal_phase_runs WHERE id=?", (run["id"],))
    assert done["status"] == "PASSED"
    for column in ("criteria", "requirement_sha256", "plan_sha256", "plan_version", "test_command",
                   "budget", "task_id", "workflow_id"):
        assert after[column] == before[column], column
    task_after = bridge.task("demo", run["task_id"])
    assert task_after["requirement"] == task_before["requirement"]
    assert task_after["acceptance"] == task_before["acceptance"] == ["C1 holds"]
    assert bridge.get_goal("demo", goal["id"])["plan"]["sha256"] == goal["plan"]["sha256"]
    with pytest.raises(sqlite3.DatabaseError, match="finished"):
        bridge.store.execute("UPDATE goal_phase_runs SET status='RUNNING' WHERE id=?", (run["id"],))
    assert BridgeError


def test_the_whole_repair_runs_through_real_worker_processes_with_nobody_driving(
    slug, tmp_path, monkeypatch
):
    """Same story, but nothing here steps the run: every execution is a real `ccc_mcp.worker`
    process, every wake a real `ccc_mcp.phase_worker` process, started by the code itself. The test
    only starts the phase and then watches."""
    import subprocess
    import time

    import psutil

    bridge, _ = slug
    del bridge.spawn_phase_worker            # back to the real spawn (the fixture silenced it)
    del bridge.spawn_worker                  # and the real worker spawn (the shared fixture's no-op)
    _, log = install_fake_cli(
        tmp_path, monkeypatch, initial=[{"file": "app.py", "content": BUGGY}], pm_fix=FIX)
    goal = make_ready_goal(bridge, PYTEST)
    run = bridge.start_phase_run("demo", goal["id"], "P1")

    deadline = time.monotonic() + 150
    seen = run
    while time.monotonic() < deadline:
        seen = bridge.get_phase_run("demo", run["id"])
        if seen["status"] not in ("STARTING", "RUNNING"):
            break
        time.sleep(0.5)
    assert seen["status"] == "PASSED", (seen["status"], seen["reason"], seen["detail"])
    assert [a["status"] for a in seen["attempts"]] == ["FAILED", "PASSED"]
    assert [c["schema"] for c in cli_calls(log)].count("goal_repair") == 1
    assert workspace_file(bridge, seen, "app.py") == FIXED
    # no process of ours is left running behind it
    time.sleep(1.5)
    leftovers = [
        p.info["cmdline"] for p in psutil.process_iter(["cmdline"])
        if p.info["cmdline"] and any(part in ("ccc_mcp.worker", "ccc_mcp.phase_worker")
                                     for part in p.info["cmdline"])
        and str(bridge.home) in " ".join(p.info["cmdline"])
    ]
    assert leftovers == []
    assert subprocess


def test_a_step_that_finishes_while_the_run_is_paused_carries_on_after_resume(slug, tmp_path, monkeypatch):
    """Found in the real console: pausing while a worker is running lets that worker finish (its
    result is recorded), but the engine's own hand-over to the next role is refused while the Task
    is paused. Resuming has to pick that hand-over up, or the run waits forever."""
    import threading
    import time

    from ccc_mcp.worker import run_execution

    bridge, _ = slug
    _, log = install_fake_cli(
        tmp_path, monkeypatch, initial=[{"file": "app.py", "content": FIXED}], sleep={"SD": 2.0})
    goal = make_ready_goal(bridge, PYTEST)
    run = bridge.start_phase_run("demo", goal["id"], "P1")
    for _ in range(10):                                     # until the designer (SD) is the live step
        queued = bridge.store.one("SELECT id,role FROM agent_executions WHERE status='QUEUED'")
        if queued and queued["role"] == "SD":
            break
        if queued:
            run_execution(bridge, queued["id"])
        bridge.step_phase_run(run["id"])
    sd = bridge.store.one("SELECT id FROM agent_executions WHERE status='QUEUED' AND role='SD'")
    worker = threading.Thread(target=run_execution, args=(bridge, sd["id"]))
    worker.start()
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and bridge.store.one(
            "SELECT status FROM agent_executions WHERE id=?", (sd["id"],))["status"] != "RUNNING":
        time.sleep(0.05)
    bridge.pause_phase_run("demo", run["id"], "while the designer works")   # the worker is mid-run
    worker.join(timeout=30)

    assert bridge.store.one("SELECT status FROM agent_executions WHERE id=?", (sd["id"],))["status"] \
        == "SUCCEEDED"                                      # its result was recorded
    assert [c["role"] for c in cli_calls(log)] == ["SA", "SD"]   # and nothing after it started
    assert bridge.get_phase_run("demo", run["id"])["status"] == "PAUSED"
    drive(bridge, run["id"])
    assert [c["role"] for c in cli_calls(log)] == ["SA", "SD"]   # paused means paused

    bridge.resume_phase_run("demo", run["id"], spawn=False)
    done = drive(bridge, run["id"])
    assert done["status"] == "PASSED", (done["status"], done["reason"], done["detail"])
    assert [c["role"] for c in cli_calls(log)] == ["SA", "SD", "RD", "REVIEW"]
    assert len(builders_of(bridge, done)) == 1              # nothing was done twice


def builders_of(service, run):
    return service.store.all(
        "SELECT id FROM agent_executions WHERE task_id=? AND role IN ('RD','SD/RD')", (run["task_id"],))


def test_after_a_restart_the_run_shows_its_last_prompt_provider_task_and_repair_round(
    slug, tmp_path, monkeypatch
):
    from ccc_mcp.service import CCCMCP

    bridge, _ = slug
    install_fake_cli(tmp_path, monkeypatch, initial=[{"file": "app.py", "content": BUGGY}], pm_fix=FIX)
    goal = make_ready_goal(bridge, PYTEST)
    run = bridge.start_phase_run("demo", goal["id"], "P1")
    done = drive(bridge, run["id"])
    assert done["status"] == "PASSED"

    reopened = CCCMCP(bridge.home).get_phase_run("demo", run["id"])          # a new process, same files
    assert reopened["task_id"] == "GOAL-0001-P1" and reopened["pm"]["provider"] == "codex"
    last_developer = [e for e in reopened["executions"] if e["role"] == "RD"][-1]
    assert last_developer["agent"] == "claude" and len(last_developer["instruction_sha256"]) == 64
    row = bridge.store.one("SELECT instruction FROM agent_executions WHERE id=?", (last_developer["id"],))
    import hashlib
    assert last_developer["instruction_sha256"] == hashlib.sha256(row["instruction"].encode()).hexdigest()
    (repair,) = reopened["repairs"]
    assert len(repair["prompt_sha256"]) == 64 and repair["after_attempt"] == 1
    assert [(a["number"], a["kind"]) for a in reopened["attempts"]] == [(1, "INITIAL"), (2, "REPAIR")]
    assert repair["restart_execution_id"] == last_developer["id"]


# Pause / Resume races (real adapters, scripted CLI) -------------------------------------------------------


def _mid_step_pause(bridge, run, role="SD"):
    """Bring the run to `role` live and return a started worker thread plus its execution id."""
    import threading
    import time

    from ccc_mcp.worker import run_execution

    for _ in range(10):
        queued = bridge.store.one("SELECT id,role FROM agent_executions WHERE status='QUEUED'")
        if queued and queued["role"] == role:
            break
        if queued:
            run_execution(bridge, queued["id"])
        bridge.step_phase_run(run["id"])
    sd = bridge.store.one("SELECT id FROM agent_executions WHERE status='QUEUED' AND role=?", (role,))
    worker = threading.Thread(target=run_execution, args=(bridge, sd["id"]))
    worker.start()
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and bridge.store.one(
            "SELECT status FROM agent_executions WHERE id=?", (sd["id"],))["status"] != "RUNNING":
        time.sleep(0.05)
    return worker, sd["id"]


def test_resuming_twice_and_from_two_places_hands_over_exactly_once(slug, tmp_path, monkeypatch):
    import threading

    bridge, _ = slug
    _, log = install_fake_cli(
        tmp_path, monkeypatch, initial=[{"file": "app.py", "content": FIXED}], sleep={"SD": 1.5})
    goal = make_ready_goal(bridge, PYTEST)
    run = bridge.start_phase_run("demo", goal["id"], "P1")
    worker, _ = _mid_step_pause(bridge, run)
    bridge.pause_phase_run("demo", run["id"])
    worker.join(timeout=30)

    results = []

    def resume():
        try:
            results.append(bridge.resume_phase_run("demo", run["id"], spawn=False)["status"])
        except Exception as exc:        # the loser of the race is refused, never doubled
            results.append(type(exc).__name__)

    threads = [threading.Thread(target=resume) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert results.count("RUNNING") >= 1 and set(results) <= {"RUNNING", "BridgeError"}
    # and a fifth, later resume of a run that is already running is refused, not repeated
    with pytest.raises(BridgeError, match="cannot be resumed"):
        bridge.resume_phase_run("demo", run["id"], spawn=False)

    threads = [threading.Thread(target=drive, args=(bridge, run["id"])) for _ in range(3)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    done = drive(bridge, run["id"])
    assert done["status"] == "PASSED", (done["status"], done["reason"])
    assert [c["role"] for c in cli_calls(log)] == ["SA", "SD", "RD", "REVIEW"]   # nothing doubled
    assert len(builders_of(bridge, done)) == 1
    assert len([e for e in done["events"] if e["kind"] == "phase_run_resumed"]) == 1


def test_a_restart_between_pause_and_resume_loses_no_hand_over(slug, tmp_path, monkeypatch):
    from ccc_mcp.service import CCCMCP

    bridge, _ = slug
    _, log = install_fake_cli(
        tmp_path, monkeypatch, initial=[{"file": "app.py", "content": FIXED}], sleep={"SD": 1.0})
    goal = make_ready_goal(bridge, PYTEST)
    run = bridge.start_phase_run("demo", goal["id"], "P1")
    worker, _ = _mid_step_pause(bridge, run)
    bridge.pause_phase_run("demo", run["id"])
    worker.join(timeout=30)

    fresh = CCCMCP(bridge.home)                     # "the machine restarted": new service, same files
    fresh.spawn_phase_worker = lambda run_id: None
    fresh.spawn_worker = lambda execution_id: None
    assert fresh.get_phase_run("demo", run["id"])["status"] == "PAUSED"
    fresh.resume_phase_run("demo", run["id"], spawn=False)
    done = drive(fresh, run["id"])
    assert done["status"] == "PASSED"
    assert [c["role"] for c in cli_calls(log)] == ["SA", "SD", "RD", "REVIEW"]


def test_pause_and_the_end_of_a_step_at_the_same_moment_never_lose_the_hand_over(
    slug, tmp_path, monkeypatch
):
    """Pause lands at many offsets around the moment the designer finishes. Whatever the order, after
    a resume every role runs exactly once and the phase passes."""
    import threading
    import time

    from ccc_mcp.worker import run_execution

    bridge, _ = slug
    bridge.authorize_project_local("demo")
    bridge.configure_workflow("demo", "standard")
    bridge.configure_test_command("demo", PYTEST)
    for offset in (0.0, 0.3, 0.6):
        _, log = install_fake_cli(
            tmp_path, monkeypatch, initial=[{"file": "app.py", "content": FIXED}], sleep={"SD": 0.6})
        goal = bridge.create_goal("demo", f"Goal for offset {offset}", title=f"G{offset}")
        bridge.import_goal_plan("demo", goal["id"], plan_dict_for(bridge), 0)
        cur = bridge.get_goal("demo", goal["id"])
        bridge.approve_goal_local("demo", goal["id"], cur["plan"]["version"], cur["plan"]["sha256"])
        run = bridge.start_phase_run("demo", goal["id"], "P1")
        for _ in range(10):
            queued = bridge.store.one(
                "SELECT id,role FROM agent_executions WHERE status='QUEUED' AND task_id=?", (run["task_id"],))
            if queued and queued["role"] == "SD":
                break
            if queued:
                run_execution(bridge, queued["id"])
            bridge.step_phase_run(run["id"])
        sd = bridge.store.one("SELECT id FROM agent_executions WHERE status='QUEUED' AND role='SD' "
                              "AND task_id=?", (run["task_id"],))
        worker = threading.Thread(target=run_execution, args=(bridge, sd["id"]))
        worker.start()
        time.sleep(offset)
        try:
            bridge.pause_phase_run("demo", run["id"])
        except BridgeError:
            pass
        worker.join(timeout=30)
        if bridge.get_phase_run("demo", run["id"])["status"] == "PAUSED":
            bridge.resume_phase_run("demo", run["id"], spawn=False)
        done = drive(bridge, run["id"])
        assert done["status"] == "PASSED", (offset, done["status"], done["reason"])
        assert [c["role"] for c in cli_calls(log)].count("RD") == 1, offset
        assert [e["role"] for e in done["executions"]] == ["SA", "SD", "RD", "REVIEW"], offset
        assert bridge.store.one(
            "SELECT COUNT(*) AS n FROM agent_executions WHERE status IN ('QUEUED','RUNNING')")["n"] == 0
        (tmp_path / "fake_cli.json.counts").unlink(missing_ok=True)


def plan_dict_for(service):
    from phase_support import plan_dict

    return plan_dict(service.get_project_settings("demo")["test_command"], 1)


# The budget is enforced, not just recorded (real adapters, scripted CLI) --------------------------------


def test_the_counters_are_the_real_counts_of_what_ran(slug, tmp_path, monkeypatch):
    bridge, _ = slug
    _, log = install_fake_cli(tmp_path, monkeypatch, initial=[{"file": "app.py", "content": BUGGY}], pm_fix=FIX)
    goal = make_ready_goal(bridge, PYTEST)
    done = drive(bridge, bridge.start_phase_run("demo", goal["id"], "P1")["id"])
    calls = cli_calls(log)
    developers = [c for c in calls if c["role"] == "RD" and c["schema"] == "agent_report"]
    analyses = [c for c in calls if c["schema"] == "goal_repair"]
    assert done["budget"]["attempts"]["used"] == len(developers) == 2
    assert done["budget"]["dispatches"]["executions"] == len(calls) - len(analyses) == 5
    assert done["budget"]["dispatches"]["analyses"] == len(analyses) == 1
    assert done["budget"]["dispatches"]["used"] == len(calls) == 6
    assert done["budget"]["minutes"]["used"] > 0 and done["budget"]["enforced"] is True


def test_the_dispatch_budget_stops_a_real_cli_from_being_started(slug, tmp_path, monkeypatch):
    bridge, _ = slug
    _, log = install_fake_cli(tmp_path, monkeypatch, initial=[{"file": "app.py", "content": BUGGY}], pm_fix=FIX)
    goal = make_ready_goal(bridge, PYTEST, budget={"max_agent_dispatches": 5})
    done = drive(bridge, bridge.start_phase_run("demo", goal["id"], "P1")["id"])
    assert done["status"] == "EXHAUSTED" and done["reason"] == "DISPATCH_BUDGET"
    assert len(cli_calls(log)) == 5                          # the sixth process was never started
    drive(bridge, done["id"])
    assert len(cli_calls(log)) == 5


def test_the_attempt_budget_stops_a_real_cli_from_being_started(slug, tmp_path, monkeypatch):
    bridge, _ = slug
    variants = [{"file": "app.py", "content": BUGGY},
                {"file": "app.py", "content": 'def slugify(text):\n    return text.lower().replace(" ", "")\n'}]
    _, log = install_fake_cli(tmp_path, monkeypatch, variants=variants, pm_fix=FIX)
    goal = make_ready_goal(bridge, PYTEST, budget={"max_attempts_per_phase": 2})
    done = drive(bridge, bridge.start_phase_run("demo", goal["id"], "P1")["id"])
    assert done["status"] == "EXHAUSTED" and done["reason"] == "ATTEMPTS_EXHAUSTED"
    assert len([c for c in cli_calls(log) if c["role"] == "RD"]) == 2


def test_the_time_budget_stops_a_real_cli_from_being_started(slug, tmp_path, monkeypatch):
    bridge, _ = slug
    _, log = install_fake_cli(tmp_path, monkeypatch, initial=[{"file": "app.py", "content": FIXED}])
    goal = make_ready_goal(bridge, PYTEST, budget={"max_wall_clock_minutes": 1})
    run = bridge.start_phase_run("demo", goal["id"], "P1")
    calls_before = len(cli_calls(log))
    bridge.store.execute("UPDATE goal_phase_runs SET active_seconds=61 WHERE id=?", (run["id"],))
    from ccc_mcp.worker import run_execution
    queued = bridge.store.one("SELECT id FROM agent_executions WHERE status='QUEUED'")
    run_execution(bridge, queued["id"])                      # the analyst already queued may finish...
    done = drive(bridge, run["id"])
    assert done["status"] == "EXHAUSTED" and done["reason"] == "TIME_BUDGET"
    assert len(cli_calls(log)) <= calls_before + 1           # ...but nothing is started after it
    assert [c["role"] for c in cli_calls(log)] == ["SA"]


def test_phase_two_stays_unstarted_whatever_wakes_the_runner_and_all_evidence_is_kept(
    slug, tmp_path, monkeypatch
):
    from ccc_mcp.service import CCCMCP

    bridge, _ = slug
    _, log = install_fake_cli(tmp_path, monkeypatch, initial=[{"file": "app.py", "content": BUGGY}], pm_fix=FIX)
    goal = make_ready_goal(bridge, PYTEST, phases=2)
    run = bridge.start_phase_run("demo", goal["id"], "P1")
    done = drive(bridge, run["id"])
    assert done["status"] == "PASSED"
    calls = len(cli_calls(log))

    # everything that could wake or recover a runner, again and again
    for execution in bridge.store.all("SELECT id FROM agent_executions"):
        bridge.notify_phase_runner(execution["id"])
    bridge.wake_phase_run(run["id"])
    bridge.run_phase_worker(run["id"], recovering=True)
    assert bridge.recover_phase_runs("demo") == []
    drive(bridge, run["id"])
    for _ in range(3):
        bridge.step_phase_run(run["id"], recovering=True)

    assert len(cli_calls(log)) == calls                              # nothing ran
    assert [r["phase_id"] for r in bridge.get_goal("demo", goal["id"])["phase_runs"]] == ["P1"]
    assert not bridge.store.one("SELECT 1 FROM tasks WHERE id='GOAL-0001-P2'")
    assert not bridge.store.one("SELECT 1 FROM goal_phase_runs WHERE phase_id='P2'")

    # the evidence is all still there, in a new process
    after = CCCMCP(bridge.home).get_phase_run("demo", run["id"])
    assert [a["status"] for a in after["attempts"]] == ["FAILED", "PASSED"]
    assert after["repairs"][0]["spec"]["root_causes"] and after["repairs"][0]["message_to_developer"]
    assert after["host_test"]["passed"] is True and "2 passed" in after["host_test"]["stdout"]
    assert len(after["executions"]) == 5 and all(e["status"] == "SUCCEEDED" for e in after["executions"])
    kinds = {e["kind"] for e in after["events"]}
    assert {"phase_run_started", "phase_attempt_started", "phase_attempt_finished", "phase_repair_requested",
            "phase_repair_finished", "phase_repair_dispatched", "phase_run_passed"} <= kinds
    artifacts = {r["path"].rsplit("/", 1)[-1] for r in bridge.store.all(
        "SELECT path FROM artifacts WHERE path LIKE '%GOAL-0001-P1%'")}
    assert {"TEST-RESULT.md", "CHANGES.diff", "REVIEW.md"} <= artifacts
    assert len(bridge.store.all("SELECT id FROM host_test_runs WHERE status='FINISHED'")) == 2
