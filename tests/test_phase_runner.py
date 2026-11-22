"""Stage B: a phase run, the repair loop and its limits (state-machine tests).

These use the scripted fake agents, so they pin the *mechanics* (what is counted, when it stops,
what is refused). Whether a repair really changes files through the real Codex/Claude adapters and
the real host test is shown in test_phase_acceptance.py.
"""

import json

import pytest
from phase_support import drive, make_ready_goal, plan_dict, scripted_test

from ccc_mcp.core.approval import local_identity
from ccc_mcp.infra.errors import BridgeError, PermissionDenied


@pytest.fixture
def env(bridge, human, tmp_path, monkeypatch):
    bridge.planning_adapter_default = "fake"
    bridge.phase_adapter_default = "fake"
    bridge.spawn_phase_worker = lambda run_id: None
    return bridge


def test_smoke_a_phase_passes_on_the_first_attempt(env, tmp_path):
    command = scripted_test(tmp_path, ["pass"])
    goal = make_ready_goal(env, command)
    run = env.start_phase_run("demo", goal["id"], "P1")
    assert run["status"] in ("STARTING", "RUNNING"), run
    done = drive(env, run["id"])
    assert done["status"] == "PASSED", done


# helpers -------------------------------------------------------------------------------------------


def rows(service, sql, *args):
    return service.store.all(sql, args)


def executions(service, task_id):
    return [(r["role"], r["status"]) for r in rows(
        service, "SELECT role,status FROM agent_executions WHERE task_id=? ORDER BY created", task_id)]


def builders(service, task_id):
    return [r for r in rows(
        service, "SELECT id FROM agent_executions WHERE task_id=? AND role IN ('RD','SD/RD') "
        "ORDER BY created", task_id)]


def start(service, goal, phase="P1", **kwargs):
    return service.start_phase_run("demo", goal["id"], phase, **kwargs)


# the acceptance story, mechanics only -----------------------------------------------------------------


def test_a_failed_first_attempt_is_repaired_and_the_phase_passes(env, tmp_path):
    goal = make_ready_goal(env, scripted_test(tmp_path, ["fail", "pass"]))
    run = start(env, goal)
    done = drive(env, run["id"])

    assert done["status"] == "PASSED", done["detail"]
    assert [a["status"] for a in done["attempts"]] == ["FAILED", "PASSED"]
    assert [a["kind"] for a in done["attempts"]] == ["INITIAL", "REPAIR"]
    (repair,) = done["repairs"]
    assert repair["status"] == "SUCCEEDED" and repair["decision"] == "repair"
    assert repair["spec"]["root_causes"][0]["confidence"] == "confirmed"
    assert repair["spec"]["root_causes"][0]["evidence"][0]["verified"] is True
    assert "assert 1 == 3" in repair["spec"]["root_causes"][0]["evidence"][0]["quote"]
    assert repair["restart_execution_id"]
    assert done["host_test"]["passed"] is True
    # the existing engine ran it: one Task, the fixed ID, the Standard workflow's roles
    assert done["task_id"] == "GOAL-0001-P1"
    assert [t["id"] for t in env.list_tasks("demo")] == ["GOAL-0001-P1"]
    roles = [role for role, _ in executions(env, done["task_id"])]
    assert roles == ["SA", "SD", "RD", "RD", "REVIEW"]
    # Stage B stops after one phase
    assert env.store.one("SELECT COUNT(*) AS n FROM goal_phase_runs")["n"] == 1
    assert done["budget"]["attempts"] == {"used": 2, "max": 3, "left": 1}
    assert done["budget"]["dispatches"]["analyses"] == 1
    assert done["budget"]["dispatches"]["used"] == 6   # 5 agent runs + 1 Delivery PM analysis


# limits: attempts, dispatches, time, progress ------------------------------------------------------


def test_three_failed_attempts_stop_safely_and_nothing_more_is_dispatched(env, tmp_path):
    goal = make_ready_goal(env, scripted_test(tmp_path, ["fail-a", "fail-b", "fail-c", "pass"]))
    done = drive(env, start(env, goal)["id"])

    assert done["status"] == "EXHAUSTED" and done["reason"] == "ATTEMPTS_EXHAUSTED", done
    assert len(builders(env, done["task_id"])) == 3
    assert [a["status"] for a in done["attempts"]] == ["FAILED", "FAILED", "FAILED"]
    assert done["budget"]["attempts"]["left"] == 0
    assert env.task("demo", done["task_id"])["state"] == "CANCELLED"
    # a fourth try is impossible even if someone asks the engine directly
    n = len(executions(env, done["task_id"]))
    drive(env, done["id"])
    assert len(executions(env, done["task_id"])) == n
    assert env.get_goal("demo", goal["id"])["phase_runs"][0]["status"] == "EXHAUSTED"


def test_the_attempt_limit_holds_in_the_dispatch_gate_not_only_in_the_runner(env, tmp_path):
    from ccc_mcp.core.phase_runner import BudgetExhausted

    goal = make_ready_goal(env, scripted_test(tmp_path, ["fail"]), budget={"max_attempts_per_phase": 2})
    done = drive(env, start(env, goal, pm_scenario="needs_human")["id"])
    assert done["status"] == "NEEDS_HUMAN" and len(builders(env, done["task_id"])) == 1

    def ask_for_a_developer():
        return env.dispatch_agent(
            "demo", done["task_id"], "auto", "RD", env.latest("demo")["version"], "again",
            adapter="fake")

    # Anyone asking the engine for a developer is held to the same limit: the second is allowed
    # (it is attempt 2 of 2), the third is not, whoever asks and whatever the runner thinks.
    second = ask_for_a_developer()
    assert second["status"] == "QUEUED"
    env.store.execute("UPDATE agent_executions SET status='SUCCEEDED' WHERE id=?",
                      (second["execution_id"],))
    with pytest.raises(BudgetExhausted, match="2 of 2 attempts"):
        ask_for_a_developer()


def test_the_dispatch_limit_holds_for_any_role_and_any_caller(env, tmp_path):
    from ccc_mcp.core.phase_runner import BudgetExhausted

    goal = make_ready_goal(env, scripted_test(tmp_path, ["fail"]), budget={"max_agent_dispatches": 5})
    done = drive(env, start(env, goal, pm_scenario="needs_human")["id"])
    assert done["budget"]["dispatches"]["used"] == 4
    result = env.dispatch_agent(
        "demo", done["task_id"], "auto", "REVIEW", env.latest("demo")["version"], "look",
        adapter="fake")
    env.store.execute("UPDATE agent_executions SET status='SUCCEEDED' WHERE id=?", (result["execution_id"],))
    with pytest.raises(BudgetExhausted, match="5 of 5 agent dispatches"):
        env.dispatch_agent(
            "demo", done["task_id"], "auto", "REVIEW", env.latest("demo")["version"], "again",
            adapter="fake")


def test_a_failure_that_comes_back_unchanged_is_not_retried_blindly(env, tmp_path):
    goal = make_ready_goal(env, scripted_test(tmp_path, ["fail", "fail", "pass"]))
    done = drive(env, start(env, goal)["id"])

    assert done["status"] == "NEEDS_HUMAN" and done["reason"] == "NO_PROGRESS", done
    assert done["detail"]["attempt"] == 2 and done["detail"]["changed_files"] == []
    assert len(builders(env, done["task_id"])) == 2          # the third was not spent
    assert done["budget"]["attempts"]["left"] == 1          # what is left is still there


def test_a_failure_that_changes_is_progress_even_when_it_still_fails(env, tmp_path):
    goal = make_ready_goal(env, scripted_test(tmp_path, ["fail-a", "fail-b", "pass"]))
    done = drive(env, start(env, goal)["id"])
    assert done["status"] == "PASSED"
    assert [a["status"] for a in done["attempts"]] == ["FAILED", "FAILED", "PASSED"]


def test_the_dispatch_budget_is_enforced_by_the_dispatch_gate(env, tmp_path):
    # SA, SD, RD (3), the Delivery PM (4), the repair RD (5); the sixth dispatch is refused.
    goal = make_ready_goal(env, scripted_test(tmp_path, ["fail", "pass"]),
                           budget={"max_agent_dispatches": 5})
    done = drive(env, start(env, goal)["id"])

    assert done["status"] == "EXHAUSTED" and done["reason"] == "DISPATCH_BUDGET", done
    assert done["budget"]["dispatches"]["used"] == 5 and done["budget"]["dispatches"]["left"] == 0
    assert [r for r, _ in executions(env, done["task_id"])] == ["SA", "SD", "RD", "RD"]


def test_too_little_dispatch_budget_for_a_repair_stops_before_asking_the_delivery_pm(env, tmp_path):
    goal = make_ready_goal(env, scripted_test(tmp_path, ["fail", "pass"]),
                           budget={"max_agent_dispatches": 4})
    done = drive(env, start(env, goal)["id"])
    assert done["status"] == "EXHAUSTED" and done["reason"] == "DISPATCH_BUDGET"
    assert done["repairs"] == []                       # no quota burnt on a repair that cannot run


def test_the_time_budget_stops_a_run_that_has_used_it_up(env, tmp_path):
    goal = make_ready_goal(env, scripted_test(tmp_path, ["pass"]),
                           budget={"max_wall_clock_minutes": 1})
    run = start(env, goal)
    env.store.execute("UPDATE goal_phase_runs SET active_seconds=61 WHERE id=?", (run["id"],))
    done = drive(env, run["id"])
    assert done["status"] == "EXHAUSTED" and done["reason"] == "TIME_BUDGET"
    assert done["budget"]["minutes"]["left"] == 0


def test_waiting_for_a_person_does_not_spend_the_time_budget(env, tmp_path):
    goal = make_ready_goal(env, scripted_test(tmp_path, ["pass"]))
    run = start(env, goal)
    env.pause_phase_run("demo", run["id"])
    paused = env.get_phase_run("demo", run["id"])
    before = paused["budget"]["minutes"]["used"]
    env.store.execute("UPDATE goal_phase_runs SET updated='2020-01-01T00:00:00Z' WHERE id=?", (run["id"],))
    assert env.get_phase_run("demo", run["id"])["budget"]["minutes"]["used"] == before
    row = env.store.one("SELECT active_since FROM goal_phase_runs WHERE id=?", (run["id"],))
    assert row["active_since"] is None


# the review gate and the host test decide, never the run ----------------------------------------------


def test_a_review_that_does_not_pass_is_never_a_pass(env, tmp_path):
    goal = make_ready_goal(env, scripted_test(tmp_path, ["pass"]))
    done = drive(env, start(env, goal, fake_scenario="REVIEW:revision")["id"])

    # the host test passes every time and the reviewer never does; the same objection coming back
    # with nothing changed stops the loop for a person instead of spending the last attempt
    assert done["status"] == "NEEDS_HUMAN" and done["reason"] == "NO_PROGRESS", done
    assert done["status"] != "PASSED" and done["host_test"]["passed"] is True
    assert all(g["verdict"] != "PASS" for g in done["gates"])
    assert env.task("demo", done["task_id"])["state"] != "COMPLETED"
    # the reviewer's findings reached the Delivery PM as evidence
    assert all(r["input_summary"]["kind"] == "review_not_passed" for r in done["repairs"])


def test_a_review_that_fails_once_is_repaired_and_then_passes(env, tmp_path):
    goal = make_ready_goal(env, scripted_test(tmp_path, ["pass"]))
    done = drive(env, start(env, goal, fake_scenario="REVIEW:revision,success")["id"])
    assert done["status"] == "PASSED", done
    assert [g["verdict"] for g in done["gates"]] == ["NEEDS_REVISION", "PASS"]
    assert [a["status"] for a in done["attempts"]] == ["FAILED", "PASSED"]


def test_passing_needs_the_configured_test_command_and_not_a_claim(env, tmp_path):
    goal = make_ready_goal(env, scripted_test(tmp_path, ["pass"]))
    run = start(env, goal)
    done = drive(env, run["id"])
    assert done["status"] == "PASSED"
    # the evidence the pass rests on is the host's, for the operator's command
    assert done["host_test"]["executed_by_host"] is True
    assert done["host_test"]["command"][-1].endswith("gate_test.py")
    assert done["test_command"] == env.get_project_settings("demo")["test_command"]


# what the Delivery PM may not do -------------------------------------------------------------------------


@pytest.mark.parametrize("scenario,why", [
    ("wrong_tests", "rerun_tests"),
    ("drop_criterion", "missing"),
    ("invent_criterion", "not approved"),
    ("edit_criteria", "acceptance_changes"),
    ("protected_scope", "protected"),
    ("no_cause", "root cause"),
])
def test_a_repair_spec_that_changes_what_was_approved_is_refused_and_acted_on_by_nobody(
    env, tmp_path, scenario, why
):
    goal = make_ready_goal(env, scripted_test(tmp_path, ["fail", "pass"]))
    criteria_before = env.store.one("SELECT criteria FROM goal_phase_runs LIMIT 1")
    run = start(env, goal, pm_scenario=scenario)
    done = drive(env, run["id"])

    assert done["status"] == "NEEDS_HUMAN" and done["reason"] == "PM_INVALID_OUTPUT", done
    assert why in " ".join(done["detail"]["problems"]), done["detail"]
    assert len(builders(env, done["task_id"])) == 1        # nothing was sent to the developer
    (repair,) = done["repairs"]
    assert repair["status"] == "FAILED" and repair["failure_kind"] == "INVALID_OUTPUT"
    # the approved criteria are exactly what they were
    stored = env.store.one("SELECT criteria FROM goal_phase_runs WHERE id=?", (run["id"],))
    assert [c["id"] for c in __import__("json").loads(stored["criteria"])] == ["C1"]
    assert criteria_before is None


def test_a_cause_with_no_evidence_behind_it_is_recorded_as_a_hypothesis(env, tmp_path):
    goal = make_ready_goal(env, scripted_test(tmp_path, ["fail", "pass"]))
    done = drive(env, start(env, goal, pm_scenario="unverified_cause")["id"])

    assert done["status"] == "PASSED"
    (repair,) = done["repairs"]
    cause = repair["spec"]["root_causes"][0]
    assert cause["confidence"] == "hypothesis" and cause["downgraded"] is True
    assert cause["evidence"][0]["verified"] is False
    assert any("recorded as a hypothesis" in note for note in repair["spec"]["notes"])
    assert "[hypothesis]" in repair["message_to_developer"]
    assert "NOT found in the evidence" in repair["message_to_developer"]


def test_the_developer_is_always_told_what_cannot_change(env, tmp_path):
    goal = make_ready_goal(env, scripted_test(tmp_path, ["fail", "pass"]))
    done = drive(env, start(env, goal)["id"])
    (repair,) = done["repairs"]
    text = repair["message_to_developer"]
    assert "C1: C1 holds" in text and "gate_test.py" in text
    assert "Do not commit, push, reset, rebase, release or deploy" in text
    assert "Do not edit tests, test configuration, CI files, .git or .ccc-mcp" in text


def test_a_delivery_pm_that_cannot_find_a_cause_asks_for_a_person(env, tmp_path):
    goal = make_ready_goal(env, scripted_test(tmp_path, ["fail", "pass"]))
    done = drive(env, start(env, goal, pm_scenario="needs_human")["id"])
    assert done["status"] == "NEEDS_HUMAN" and done["reason"] == "PM_NEEDS_HUMAN"
    assert "a person must look" in done["detail"]["why"]
    assert len(builders(env, done["task_id"])) == 1


def test_a_provider_failure_of_the_delivery_pm_pauses_and_never_loops(env, tmp_path):
    goal = make_ready_goal(env, scripted_test(tmp_path, ["fail", "pass"]))
    done = drive(env, start(env, goal, pm_scenario="error")["id"])
    assert done["status"] == "NEEDS_HUMAN" and done["reason"] == "PM_UNAVAILABLE"
    assert len(done["repairs"]) == 1 and len(builders(env, done["task_id"])) == 1
    again = drive(env, done["id"])                     # looking again changes nothing
    assert len(again["repairs"]) == 1


def test_a_repair_that_asks_for_new_analysis_goes_back_to_the_designer(env, tmp_path):
    goal = make_ready_goal(env, scripted_test(tmp_path, ["fail", "pass"]))
    done = drive(env, start(env, goal, pm_scenario="reanalysis")["id"])
    (repair,) = done["repairs"]
    assert repair["restart_step"] == 1                 # SD in the Standard workflow (SA, SD, RD, REVIEW)
    assert done["status"] == "PASSED"
    assert [r for r, _ in executions(env, done["task_id"])] == ["SA", "SD", "RD", "SD", "RD", "REVIEW"]


# starting a phase: what is checked, and that it happens once ---------------------------------------------


def test_the_run_is_bound_to_the_approved_version_and_hash(env, tmp_path):
    goal = make_ready_goal(env, scripted_test(tmp_path, ["pass"]))
    run = start(env, goal)
    assert run["plan_version"] == goal["plan"]["version"]
    assert run["plan_sha256"] == goal["plan"]["sha256"]
    assert run["approval_id"] == goal["approval"]["id"]
    created = [e for e in run["events"] if e["kind"] == "phase_run_started"][0]["detail"]
    assert created["plan_sha256"] == goal["plan"]["sha256"] and created["workflow"] == "standard"
    assert created["coverage"] == {"C1": "host_test"}


def test_starting_the_same_phase_twice_creates_one_run_and_one_task(env, tmp_path):
    goal = make_ready_goal(env, scripted_test(tmp_path, ["pass"]))
    first = start(env, goal)
    second = start(env, goal)
    assert second["id"] == first["id"] and second["already_started"] is True
    assert env.store.one("SELECT COUNT(*) AS n FROM goal_phase_runs")["n"] == 1
    assert [t["id"] for t in env.list_tasks("demo")] == ["GOAL-0001-P1"]
    assert len(executions(env, "GOAL-0001-P1")) == 1


def test_many_callers_starting_the_same_phase_at_once_still_make_one_task(env, tmp_path):
    import threading

    goal = make_ready_goal(env, scripted_test(tmp_path, ["pass"]))
    results, errors = [], []
    barrier = threading.Barrier(6)

    def go():
        barrier.wait()
        try:
            results.append(start(env, goal)["id"])
        except Exception as exc:  # losing the race is allowed to be refused, never to duplicate
            errors.append(exc)

    threads = [threading.Thread(target=go) for _ in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(set(results)) == 1 and results
    assert env.store.one("SELECT COUNT(*) AS n FROM goal_phase_runs")["n"] == 1
    assert [t["id"] for t in env.list_tasks("demo")] == ["GOAL-0001-P1"]
    assert len(executions(env, "GOAL-0001-P1")) == 1


def test_a_start_that_died_half_way_is_finished_not_duplicated(env, tmp_path, monkeypatch):
    goal = make_ready_goal(env, scripted_test(tmp_path, ["pass"]))
    real = env.create_task
    calls = {"n": 0}

    def crash_after_creating(*args, **kwargs):
        calls["n"] += 1
        real(*args, **kwargs)
        raise RuntimeError("process died right after the Task was created")

    monkeypatch.setattr(env, "create_task", crash_after_creating)
    with pytest.raises(RuntimeError):
        start(env, goal)
    monkeypatch.setattr(env, "create_task", real)
    stuck = env.store.one("SELECT * FROM goal_phase_runs")
    assert stuck["status"] == "STARTING"
    assert env.task("demo", "GOAL-0001-P1")["state"] == "CREATED"

    again = start(env, goal)               # the same call, repeated
    assert again["already_started"] is True and again["status"] == "RUNNING"
    assert calls["n"] == 1
    assert len(executions(env, "GOAL-0001-P1")) == 1
    assert drive(env, again["id"])["status"] == "PASSED"


def test_a_phase_waits_for_the_phases_it_depends_on_and_is_never_started_for_you(env, tmp_path):
    goal = make_ready_goal(env, scripted_test(tmp_path, ["pass"]))
    with pytest.raises(BridgeError, match="depends on P1, which has not passed"):
        start(env, goal, "P2")
    done = drive(env, start(env, goal)["id"])
    assert done["status"] == "PASSED"
    # Phase 1 is PASSED and Phase 2 is still nothing: no run, no Task, no execution
    assert env.store.one("SELECT COUNT(*) AS n FROM goal_phase_runs WHERE phase_id='P2'")["n"] == 0
    assert [t["id"] for t in env.list_tasks("demo")] == ["GOAL-0001-P1"]
    goal_now = env.get_goal("demo", goal["id"])
    assert [r["phase_id"] for r in goal_now["phase_runs"]] == ["P1"]
    assert goal_now["execution"]["automatic_next_phase"] is False
    # it can be started on purpose, and then it is its own Task
    second = start(env, goal, "P2")
    assert second["task_id"] == "GOAL-0001-P2"


def test_only_one_phase_is_open_at_a_time(env, tmp_path):
    goal = make_ready_goal(env, scripted_test(tmp_path, ["pass"]))
    start(env, goal)
    other = env.create_goal("demo", "A different goal", title="Other")
    env.import_goal_plan("demo", other["id"], plan_dict(json.loads(
        env.store.one("SELECT test_command FROM goal_phase_runs")["test_command"]), 1), 0)
    cur = env.get_goal("demo", other["id"])
    env.approve_goal_local("demo", other["id"], cur["plan"]["version"], cur["plan"]["sha256"])
    with pytest.raises(BridgeError, match="still open"):
        env.start_phase_run("demo", other["id"], "P1")


def test_an_unapproved_or_changed_plan_cannot_be_run(env, tmp_path):
    command = scripted_test(tmp_path, ["pass"])
    env.authorize_project_local("demo")
    env.configure_workflow("demo", "standard")
    env.configure_test_command("demo", command)
    goal = env.create_goal("demo", "Something")
    with pytest.raises(BridgeError, match="no plan"):
        start(env, goal)
    env.import_goal_plan("demo", goal["id"], plan_dict(command, 1), 0)
    with pytest.raises(PermissionDenied, match="not approved"):
        start(env, goal)
    cur = env.get_goal("demo", goal["id"])
    env.approve_goal_local("demo", goal["id"], cur["plan"]["version"], cur["plan"]["sha256"])
    env.import_goal_plan("demo", goal["id"], plan_dict(command, 2), 1)       # a new version
    with pytest.raises(PermissionDenied, match="not approved"):               # the old approval is void
        start(env, goal)
    assert env.store.one("SELECT COUNT(*) AS n FROM goal_phase_runs")["n"] == 0
    assert env.list_tasks("demo") == []


def test_the_plan_cannot_change_under_a_run(env, tmp_path):
    goal = make_ready_goal(env, scripted_test(tmp_path, ["pass"]))
    run = start(env, goal)
    with pytest.raises(BridgeError, match="cannot change until that phase is finished"):
        env.import_goal_plan("demo", goal["id"], plan_dict(["python", "-c", "1"], 1), 1)
    with pytest.raises(BridgeError, match="cannot change until that phase is finished"):
        env.request_goal_plan("demo", goal["id"], spawn=False)
    with pytest.raises(BridgeError, match="cancel that phase run first"):
        env.cancel_goal("demo", goal["id"])
    env.cancel_phase_run("demo", run["id"])
    assert env.cancel_goal("demo", goal["id"])["state"] == "CANCELLED"


def test_a_read_only_project_is_refused(bridge, human, tmp_path):
    bridge.planning_adapter_default = "fake"
    bridge.phase_adapter_default = "fake"
    command = scripted_test(tmp_path, ["pass"])
    bridge.configure_workflow("demo", "standard")
    bridge.configure_test_command("demo", command)
    goal = bridge.create_goal("demo", "Something")
    bridge.import_goal_plan("demo", goal["id"], plan_dict(command, 1), 0)
    cur = bridge.get_goal("demo", goal["id"])
    bridge.approve_goal_local("demo", goal["id"], cur["plan"]["version"], cur["plan"]["sha256"])
    assert bridge.get_goal("demo", goal["id"])["state"] == "READY"
    with pytest.raises(PermissionDenied, match="Approving a goal does not authorize work"):
        bridge.start_phase_run("demo", goal["id"], "P1")
    assert bridge.list_tasks("demo") == []


def test_a_test_command_picked_automatically_is_not_good_enough(env, tmp_path):
    command = scripted_test(tmp_path, ["pass"])
    goal = make_ready_goal(env, command)
    env.configure_test_command("demo", None)
    with pytest.raises(BridgeError, match="Configure the project's test command"):
        start(env, goal)


def test_switching_to_classic_does_not_override_a_saved_plan(env, tmp_path):
    goal = make_ready_goal(env, scripted_test(tmp_path, ["pass"]))
    env.configure_workflow("demo", "classic")
    run = start(env, goal)
    assert env._task_flow("demo", run["task_id"])["id"] == "standard"


def test_a_role_that_needs_a_browser_cannot_be_run_unattended(env, tmp_path):
    command = scripted_test(tmp_path, ["pass"])
    env.configure_role("demo", "SD", "chatgpt")
    goal = make_ready_goal(env, command)
    env.configure_role("demo", "SD", "codex")
    with pytest.raises(BridgeError, match="ChatGPT in a browser"):
        env.start_phase_run("demo", goal["id"], "P1", adapter="real")
    assert env.list_tasks("demo") == []


def test_other_tasks_are_never_reused_or_overwritten(env, tmp_path):
    goal = make_ready_goal(env, scripted_test(tmp_path, ["pass"]))
    before = env.create_task("demo", "Existing unfinished work")            # TASK-0001, not finished
    run = start(env, goal)
    assert env.task("demo", before["id"])["requirement"] == "Existing unfinished work"
    assert env.task("demo", before["id"])["state"] == "CREATED"
    assert run["task_id"] == "GOAL-0001-P1" and run["task_id"] != before["id"]
    # and afterwards a plain create_task is not folded into the phase's unfinished Task either
    after = env.create_task("demo", "More work")
    assert after["id"] not in {"GOAL-0001-P1"} and after["reused"] is False
    assert env.task("demo", "GOAL-0001-P1")["requirement"].startswith("Goal GOAL-0001")


# pause, resume, cancel, accept: each with a state and an audit record -------------------------------------


def kinds(run):
    return [e["kind"] for e in run["events"]]


def test_pause_stops_new_work_and_resume_carries_on_from_the_same_place(env, tmp_path):
    goal = make_ready_goal(env, scripted_test(tmp_path, ["pass"]))
    run = start(env, goal)
    paused = env.pause_phase_run("demo", run["id"], "lunch")
    assert paused["status"] == "PAUSED" and paused["reason"] == "PAUSED_BY_USER"
    assert paused["resumable"] is True and env.task_control("demo", paused["task_id"])["paused"]
    n = len(executions(env, paused["task_id"]))
    drive(env, run["id"])                                  # nothing moves while it is paused
    assert len(executions(env, paused["task_id"])) == n
    assert env.get_phase_run("demo", run["id"])["status"] == "PAUSED"

    resumed = env.resume_phase_run("demo", run["id"], spawn=False)
    assert resumed["status"] == "RUNNING"
    done = drive(env, run["id"])
    assert done["status"] == "PASSED"
    audit = [e for e in done["events"] if e["kind"] in ("phase_run_paused", "phase_run_resumed")]
    assert [e["kind"] for e in audit] == ["phase_run_paused", "phase_run_resumed"]
    assert [e["actor"] for e in audit] == [local_identity()] * 2
    assert audit[0]["detail"]["detail"]["note"] == "lunch"
    assert len(builders(env, done["task_id"])) == 1        # nothing already done was done again


def test_cancel_ends_the_run_and_the_task_and_cannot_be_undone(env, tmp_path):
    goal = make_ready_goal(env, scripted_test(tmp_path, ["pass"]))
    run = start(env, goal)
    cancelled = env.cancel_phase_run("demo", run["id"], "wrong goal")
    assert cancelled["status"] == "CANCELLED"
    assert env.task("demo", cancelled["task_id"])["state"] == "CANCELLED" or \
        env.task_control("demo", cancelled["task_id"])["stop_requested"]
    with pytest.raises(BridgeError, match="cannot be resumed"):
        env.resume_phase_run("demo", run["id"])
    n = len(executions(env, cancelled["task_id"]))
    drive(env, run["id"])
    assert len(executions(env, cancelled["task_id"])) == n
    assert "phase_run_cancelled" in kinds(env.get_phase_run("demo", run["id"]))
    again = start(env, goal)                                # the phase is not started a second time
    assert again["id"] == run["id"] and again["already_started"] is True


def test_manual_criteria_end_awaiting_a_persons_acceptance_not_passed(env, tmp_path):
    goal = make_ready_goal(env, scripted_test(tmp_path, ["pass"]), manual_in=1)
    done = drive(env, start(env, goal)["id"])
    assert done["status"] == "AWAITING_ACCEPTANCE" and done["reason"] == "MANUAL_CRITERIA", done
    assert done["detail"]["criteria"] == ["M1"]
    info = env.accept_phase_description("demo", done["id"])
    assert info["challenge"] == "ACCEPT GOAL-0001 P1" and "M1" in info["description"]
    with pytest.raises(PermissionDenied, match="does not match"):
        env.accept_phase_web("demo", done["id"], "yes")
    assert env.get_phase_run("demo", done["id"])["status"] == "AWAITING_ACCEPTANCE"

    passed = env.accept_phase_web("demo", done["id"], info["challenge"])
    assert passed["status"] == "PASSED"
    accepted = [e for e in passed["events"] if e["kind"] == "phase_run_passed"][-1]
    assert accepted["detail"]["detail"]["accepted_criteria"] == ["M1"]
    assert accepted["actor"] == local_identity()
    with pytest.raises(BridgeError, match="not waiting"):
        env.accept_phase_web("demo", done["id"], info["challenge"])


def test_a_command_in_the_plan_that_is_not_the_projects_test_command_is_never_run(env, tmp_path):
    command = scripted_test(tmp_path, ["pass"])
    env.authorize_project_local("demo")
    env.configure_workflow("demo", "standard")
    env.configure_test_command("demo", command)
    plan = plan_dict(command, 1)
    plan["phases"][0]["acceptance"].append({
        "id": "X1", "statement": "Another check",
        "verify": {"kind": "host_command", "command": ["python", "-c", "open('/tmp/pwned','w')"]}})
    goal = env.create_goal("demo", "Something")
    env.import_goal_plan("demo", goal["id"], plan, 0)
    cur = env.get_goal("demo", goal["id"])
    env.approve_goal_local("demo", goal["id"], cur["plan"]["version"], cur["plan"]["sha256"])
    run = env.start_phase_run("demo", goal["id"], "P1")
    assert {c["id"]: c["coverage"] for c in run["criteria"]} == {"C1": "host_test", "X1": "needs_person"}
    done = drive(env, run["id"])
    assert done["status"] == "AWAITING_ACCEPTANCE" and done["detail"]["criteria"] == ["X1"]
    assert done["host_test"]["command"][-1].endswith("gate_test.py")       # only the operator's command ran
    assert not __import__("pathlib").Path("/tmp/pwned").exists()


# providers that fail, workers that die ------------------------------------------------------------------------


def test_a_failed_agent_run_waits_for_a_person_and_resuming_redoes_only_that_step(env, tmp_path):
    goal = make_ready_goal(env, scripted_test(tmp_path, ["pass"]))
    done = drive(env, start(env, goal, fake_scenario="RD:error,success")["id"])
    assert done["status"] == "NEEDS_HUMAN" and done["reason"] == "EXECUTION_ERROR", done
    assert done["resumable"] is True and "Simulated agent failure" in done["detail"]["error"]
    assert [a["status"] for a in done["attempts"]] == ["FAILED"]
    n = len(executions(env, done["task_id"]))
    drive(env, done["id"])                                  # it does not retry by itself
    assert len(executions(env, done["task_id"])) == n

    env.resume_phase_run("demo", done["id"], note="try again", spawn=False)
    after = drive(env, done["id"])
    assert after["status"] == "PASSED", after
    assert [r for r, _ in executions(env, after["task_id"])] == ["SA", "SD", "RD", "RD", "REVIEW"]
    assert after["budget"]["attempts"]["used"] == 2           # the failed run counted
    assert [a["kind"] for a in after["attempts"]] == ["INITIAL", "REPAIR"]


def test_an_exhausted_provider_quota_is_reported_and_not_retried(env, tmp_path, monkeypatch):
    monkeypatch.setattr("ccc_mcp.worker.is_quota_error", lambda text: True)
    goal = make_ready_goal(env, scripted_test(tmp_path, ["pass"]))
    done = drive(env, start(env, goal, fake_scenario="RD:error,success")["id"])
    assert done["status"] == "NEEDS_HUMAN" and done["reason"] == "QUOTA_EXHAUSTED"
    assert len(builders(env, done["task_id"])) == 1


def test_a_worker_that_died_is_recovered_as_interrupted_and_nothing_is_replayed(env, tmp_path):
    goal = make_ready_goal(env, scripted_test(tmp_path, ["pass"]))
    run = start(env, goal)
    drive(env, run["id"], rounds=2)
    # the developer's worker started and then vanished: RUNNING, no lock, no result
    live = env.store.one("SELECT id,role FROM agent_executions WHERE status='QUEUED'")
    if live is None:
        drive(env, run["id"], rounds=1)
        live = env.store.one("SELECT id,role FROM agent_executions WHERE status='QUEUED'")
    env.store.execute("UPDATE agent_executions SET status='RUNNING' WHERE id=?", (live["id"],))
    n = len(executions(env, run["task_id"]))

    report = env.recover_phase_runs("demo")
    assert report[0]["recovered"]["interrupted_executions"] == [live["id"]]
    stopped = env.get_phase_run("demo", run["id"])
    assert stopped["status"] == "NEEDS_HUMAN" and stopped["reason"] == "INTERRUPTED", stopped
    assert len(executions(env, run["task_id"])) == n      # recovery replays nothing by itself

    env.resume_phase_run("demo", run["id"], spawn=False)
    done = drive(env, run["id"])
    assert done["status"] == "PASSED", done


def test_a_dead_delivery_pm_call_is_recorded_as_interrupted_and_asked_again_once(env, tmp_path):
    goal = make_ready_goal(env, scripted_test(tmp_path, ["fail", "pass"]))
    run = start(env, goal)
    # a stepper died in the middle of the Delivery PM's analysis, leaving the row RUNNING
    original = env._run_delivery_pm
    calls = {"n": 0}

    def die_once(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise SystemExit("killed")
        return original(*args, **kwargs)

    env._run_delivery_pm = die_once
    with pytest.raises(SystemExit):
        drive(env, run["id"])
    stale = env.store.all("SELECT status FROM goal_phase_repairs")
    assert [r["status"] for r in stale] == ["RUNNING"]
    done = drive(env, run["id"])
    assert done["status"] == "PASSED", done
    assert [r["status"] for r in done["repairs"]] == ["INTERRUPTED", "SUCCEEDED"]
    assert done["budget"]["dispatches"]["analyses"] == 2     # both calls were spent, and counted
    assert len(builders(env, done["task_id"])) == 2


def test_a_repair_recorded_but_not_yet_sent_is_sent_once_without_asking_the_pm_again(
    env, tmp_path, monkeypatch
):
    goal = make_ready_goal(env, scripted_test(tmp_path, ["fail", "pass"]))
    run = start(env, goal)
    real = env.restart_from_step
    calls = {"n": 0}

    def die_before_sending(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            raise SystemExit("killed between recording the repair and sending it")
        return real(*args, **kwargs)

    monkeypatch.setattr(env, "restart_from_step", die_before_sending)
    with pytest.raises(SystemExit):
        drive(env, run["id"])
    (repair,) = env.store.all("SELECT * FROM goal_phase_repairs")
    assert repair["status"] == "SUCCEEDED" and repair["restart_execution_id"] is None
    done = drive(env, run["id"])
    assert done["status"] == "PASSED", done
    assert len(done["repairs"]) == 1 and done["budget"]["dispatches"]["analyses"] == 1
    assert len(builders(env, done["task_id"])) == 2
    assert done["repairs"][0]["restart_execution_id"]


def test_two_runners_looking_at_one_run_act_once(env, tmp_path, monkeypatch):
    import threading

    from ccc_mcp.worker import run_execution

    goal = make_ready_goal(env, scripted_test(tmp_path, ["fail", "pass"]))
    run = start(env, goal)
    # bring it to the point where the escalation is open and the Delivery PM must be asked
    real = env._on_failure
    monkeypatch.setattr(env, "_on_failure", lambda *a, **k: {"status": "RUNNING", "acted": "held"})
    for _ in range(30):
        for row in env.store.all("SELECT id FROM agent_executions WHERE status='QUEUED'"):
            run_execution(env, row["id"])
        env.step_phase_run(run["id"])
        if env.store.one("SELECT 1 FROM flow_escalations WHERE status='OPEN'"):
            break
    assert env.store.one("SELECT 1 FROM flow_escalations WHERE status='OPEN'")
    monkeypatch.setattr(env, "_on_failure", real)

    barrier = threading.Barrier(4)
    outcomes = []

    def go():
        barrier.wait()
        outcomes.append(env.run_phase_worker(run["id"]))

    threads = [threading.Thread(target=go) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(env.store.all("SELECT id FROM goal_phase_repairs")) == 1     # one Delivery PM call
    assert len(builders(env, run["task_id"])) == 2                           # one repair sent
    assert sum(1 for o in outcomes if o.get("busy")) + sum(1 for o in outcomes if not o.get("busy")) == 4
    done = drive(env, run["id"])
    assert done["status"] == "PASSED" and len(done["repairs"]) == 1


def test_waking_a_run_is_recorded_so_a_busy_stepper_looks_again(env, tmp_path, monkeypatch):
    goal = make_ready_goal(env, scripted_test(tmp_path, ["pass"]))
    run = start(env, goal)
    spawned = []
    monkeypatch.setattr(env, "spawn_phase_worker", lambda run_id: spawned.append(run_id))
    before = env._phase_run(run["id"])["wake_seq"]
    first = env.store.one("SELECT id FROM agent_executions WHERE task_id=?", (run["task_id"],))
    env.notify_phase_runner(first["id"])
    assert env._phase_run(run["id"])["wake_seq"] == before + 1 and spawned == [run["id"]]
    # a Task that no run supervises is left alone
    other = env.create_task("demo", "Plain work")
    env.notify_phase_runner("EXEC-does-not-exist")
    assert spawned == [run["id"]] and other["state"] == "CREATED"


def test_an_unsafe_leftover_host_test_is_not_run_again_and_stops_the_run(env, tmp_path, monkeypatch):
    goal = make_ready_goal(env, scripted_test(tmp_path, ["pass"]))
    run = start(env, goal)
    monkeypatch.setattr(env, "recover_workers", lambda project_id, confirmed=False: {
        "interrupted_executions": [], "interrupted_host_tests": [],
        "host_tests_not_recovered": [{"task_id": run["task_id"], "reason": "cannot prove it is gone"}],
    })
    report = env.recover_phase_runs("demo")
    assert report[0]["status"] == "NEEDS_HUMAN"
    stopped = env.get_phase_run("demo", run["id"])
    assert stopped["reason"] == "HOST_TEST_UNSAFE" and stopped["resumable"] is True
    state = json.loads(env.store.one("SELECT content FROM project_settings")["content"])["test_command"]
    gate_state = json.loads((tmp_path / "gate_state.json").read_text())
    assert gate_state["calls"] == 0                     # the test was not run, not even once
    # resuming without the operator's confirmation still refuses
    env.resume_phase_run("demo", run["id"], spawn=False)
    assert env.get_phase_run("demo", run["id"])["reason"] == "HOST_TEST_UNSAFE"
    assert json.loads((tmp_path / "gate_state.json").read_text())["calls"] == 0
    assert state


# a real process is killed in the middle of the host test ---------------------------------------------------


def wait_for(condition, timeout=40):
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if condition():
            return True
        time.sleep(0.1)
    return False


@pytest.mark.skipif(__import__("os").name != "posix", reason="SIGKILL and file leases are POSIX")
def test_a_runner_killed_during_the_host_test_is_recovered_without_replaying_the_developer(
    env, tmp_path
):
    import os
    import signal
    import subprocess
    import sys

    import psutil

    from ccc_mcp.worker import run_execution

    goal = make_ready_goal(env, scripted_test(tmp_path, ["hang", "pass"]))
    run = start(env, goal)
    # in this process: SA, the runner's plan, SD, RD. Stop with the developer's work recorded and
    # its host test still to run (a fake adapter's worker does not run it by itself).
    for _ in range(30):
        for row in env.store.all("SELECT id FROM agent_executions WHERE status='QUEUED'"):
            run_execution(env, row["id"])
        if env.store.one("SELECT 1 FROM agent_executions WHERE role='RD' AND status='SUCCEEDED'"):
            break
        env.step_phase_run(run["id"])
    assert env.store.one("SELECT 1 FROM agent_executions WHERE role='RD' AND status='SUCCEEDED'")
    developer_runs = len(builders(env, run["task_id"]))

    worker = subprocess.Popen(
        [sys.executable, "-m", "ccc_mcp.phase_worker", "--home", str(env.home), "--run", run["id"]],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        assert wait_for(lambda: env.store.one(
            "SELECT 1 FROM host_test_runs WHERE status='RUNNING' AND child IS NOT NULL")), \
            "the host test never started"
        child = json.loads(env.store.one("SELECT child FROM host_test_runs")["child"])

        def script_entered():
            # Recording the child PID does not mean Python has reached the test script yet.
            # Under load, killing the worker here could interrupt the first invocation
            # before it advances the fixture to its "pass" outcome for the retry.
            try:
                return json.loads((tmp_path / "gate_state.json").read_text())["calls"] == 1
            except json.JSONDecodeError:
                return False  # the script is writing the counter

        assert wait_for(script_entered), "the child never entered the test script"
        os.kill(worker.pid, signal.SIGKILL)                 # no finally block, no cleanup runs
        worker.wait(timeout=10)
        assert psutil.pid_exists(child["pid"])              # the test process is an orphan, still running
        assert env.store.one("SELECT status FROM host_test_runs")["status"] == "RUNNING"

        report = env.recover_phase_runs("demo")
        assert report and report[0]["recovered"]["interrupted_host_tests"], report
        assert not report[0]["recovered"].get("host_tests_not_recovered")
        assert not psutil.pid_exists(child["pid"]) or psutil.Process(child["pid"]).status() == "zombie"

        done = drive(env, run["id"])
        assert done["status"] == "PASSED", done
        states = [r["status"] for r in env.store.all("SELECT status FROM host_test_runs ORDER BY started")]
        assert states == ["INTERRUPTED", "FINISHED"]        # the cut-off run is recorded, never a pass
        assert json.loads((tmp_path / "gate_state.json").read_text())["calls"] == 2
        assert len(builders(env, run["task_id"])) == developer_runs      # nothing was written twice
    finally:
        if worker.poll() is None:
            worker.kill()
            worker.wait()
        for row in env.store.all("SELECT child FROM host_test_runs WHERE child IS NOT NULL"):
            try:
                psutil.Process(json.loads(row["child"])["pid"]).kill()
            except (psutil.Error, KeyError, ValueError):
                pass


def test_a_person_can_send_a_stalled_failure_back_to_the_pm_after_no_progress(env, tmp_path):
    goal = make_ready_goal(env, scripted_test(tmp_path, ["fail", "fail", "pass"]))
    stopped = drive(env, start(env, goal)["id"])
    assert stopped["reason"] == "NO_PROGRESS"
    env.resume_phase_run("demo", stopped["id"], note="look at the second assertion", spawn=False)
    after = drive(env, stopped["id"])
    assert after["status"] == "PASSED", after
    assert [a["status"] for a in after["attempts"]] == ["FAILED", "FAILED", "PASSED"]
    assert after["budget"]["attempts"]["used"] == 3
    assert after["repairs"][-1]["spec"] is not None


def test_a_person_cannot_add_budget_by_resuming(env, tmp_path):
    goal = make_ready_goal(env, scripted_test(tmp_path, ["fail-a", "fail-b", "fail-c", "pass"]))
    done = drive(env, start(env, goal)["id"])
    assert done["status"] == "EXHAUSTED"
    with pytest.raises(BridgeError, match="cannot be resumed"):
        env.resume_phase_run("demo", done["id"])
    assert env.get_phase_run("demo", done["id"])["budget"]["attempts"]["left"] == 0


# the database: old files, removal, history ------------------------------------------------------------------

PHASE_TABLES = ("goal_phase_runs", "goal_phase_attempts", "goal_phase_repairs")


def test_an_old_database_gains_only_tables_and_a_phase_runs_on_it(tmp_path, human):
    import sqlite3
    import subprocess
    from pathlib import Path

    from ccc_mcp.service import CCCMCP

    legacy = Path(__file__).parent / "fixtures" / "legacy_schema_0_4_0.sql"
    home = tmp_path / "legacy-home"
    home.mkdir()
    with sqlite3.connect(home / "bridge.sqlite3") as connection:
        connection.executescript(legacy.read_text(encoding="utf-8"))
        before = {row[0]: [c[1] for c in connection.execute(f"PRAGMA table_info({row[0]})")]
                  for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    repo = tmp_path / "legacy-repo"
    repo.mkdir()
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    for args in (["init", "-q"], ["add", "."],
                 ["-c", "user.name=T", "-c", "user.email=t@example.invalid", "commit", "-qm", "i"]):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    service = CCCMCP(home)
    CCCMCP(home)                                            # reopening is idempotent
    service.register_project(repo, "demo", "legacy database project")
    with sqlite3.connect(home / "bridge.sqlite3") as connection:
        names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert set(PHASE_TABLES) <= names
        for table, old in before.items():
            columns = [c[1] for c in connection.execute(f"PRAGMA table_info({table})")]
            assert columns[:len(old)] == old, f"{table} keeps its columns, in order"

    service.planning_adapter_default = service.phase_adapter_default = "fake"
    service.spawn_phase_worker = lambda run_id: None
    service.spawn_worker = lambda execution_id: None
    old_task = service.create_task("demo", "A task that predates phase runs")
    goal = make_ready_goal(service, scripted_test(tmp_path, ["fail", "pass"]), phases=1)
    done = drive(service, service.start_phase_run("demo", goal["id"], "P1")["id"])
    assert done["status"] == "PASSED"
    assert [t["id"] for t in service.list_tasks("demo")] == [old_task["id"], "GOAL-0001-P1"]
    assert CCCMCP(home).get_phase_run("demo", done["id"])["status"] == "PASSED"


def test_the_phase_schema_statements_are_safe_to_run_twice():
    import re
    from pathlib import Path

    source = (Path(__file__).parents[1] / "src/ccc_mcp/adapters/storage.py").read_text("utf-8")
    block = source[source.index("-- Phase runs (core/phase_runner.py"):source.index('"""\n\n# Like the role tables')]
    statements = re.findall(r"CREATE (?:TABLE|VIEW|TRIGGER|UNIQUE INDEX|INDEX)[^;]*;", block, re.S)
    assert len(statements) >= 12 and all("IF NOT EXISTS" in s for s in statements)


def test_removing_a_project_removes_its_phase_runs_and_nobody_elses(env, tmp_path):
    other = tmp_path / "keep"
    other.mkdir()
    env.register_project(other, "keep", "kept")
    goal = make_ready_goal(env, scripted_test(tmp_path, ["fail", "pass"]))
    done = drive(env, start(env, goal)["id"])
    assert done["status"] == "PASSED"
    for table in PHASE_TABLES:
        assert env.store.one(f"SELECT COUNT(*) AS n FROM {table} WHERE project_id='demo'")["n"] > 0

    env.remove_project_web("demo", "REMOVE demo")

    for table in PHASE_TABLES:
        assert env.store.one(f"SELECT COUNT(*) AS n FROM {table} WHERE project_id='demo'")["n"] == 0
    import sqlite3
    with sqlite3.connect(env.store.path) as connection:     # and removal put the protection back
        triggers = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
    assert {f"{table}_no_delete" for table in PHASE_TABLES} <= triggers
    assert {"evaluations_no_delete", "flow_gate_results_no_delete", "task_deletions_no_delete"} <= triggers


def test_clearing_history_is_refused_while_phase_runs_hold_the_tasks_evidence(env, tmp_path):
    goal = make_ready_goal(env, scripted_test(tmp_path, ["pass"]))
    done = drive(env, start(env, goal)["id"])
    with pytest.raises(BridgeError, match="goal phase runs"):
        env.clear_project_history_web("demo", "CLEAR HISTORY demo")
    assert env.get_phase_run("demo", done["id"])["status"] == "PASSED"
    assert env.task("demo", done["task_id"])["state"] == "COMPLETED"


def test_phase_history_cannot_be_rewritten_or_deleted(env, tmp_path):
    import sqlite3

    goal = make_ready_goal(env, scripted_test(tmp_path, ["fail", "pass"]))
    done = drive(env, start(env, goal)["id"])
    assert done["status"] == "PASSED"
    attempt, repair = done["attempts"][0], done["repairs"][0]
    for sql, match in [
        (f"UPDATE goal_phase_attempts SET outcome='x' WHERE id='{attempt['id']}'", "immutable"),
        (f"UPDATE goal_phase_attempts SET number=9 WHERE id='{attempt['id']}'", "immutable"),
        ("DELETE FROM goal_phase_attempts", "immutable"),
        (f"UPDATE goal_phase_repairs SET input_sha256='x' WHERE id='{repair['id']}'", "immutable"),
        (f"UPDATE goal_phase_repairs SET decision='needs_human' WHERE id='{repair['id']}'", "immutable"),
        ("DELETE FROM goal_phase_repairs", "immutable"),
        ("DELETE FROM goal_phase_runs", "immutable"),
        (f"UPDATE goal_phase_runs SET plan_sha256='x' WHERE id='{done['id']}'", "cannot change|stays finished"),
        (f"UPDATE goal_phase_runs SET status='RUNNING' WHERE id='{done['id']}'", "stays finished"),
        ("DELETE FROM goal_events", "immutable"),
    ]:
        with pytest.raises(sqlite3.DatabaseError, match=match):
            env.store.execute(sql)
    assert env.get_phase_run("demo", done["id"])["status"] == "PASSED"


def test_only_one_live_delivery_pm_analysis_can_exist_per_run(env, tmp_path):
    import sqlite3

    goal = make_ready_goal(env, scripted_test(tmp_path, ["pass"]))
    run = start(env, goal)
    row = ("INSERT INTO goal_phase_repairs(id,run_id,project_id,goal_id,phase_id,seq,after_attempt,"
           "status,input_sha256,input_summary,created) VALUES(?,?, 'demo','GOAL-0001','P1',?,1,"
           "'RUNNING','x','{}','2026-01-01T00:00:00Z')")
    env.store.execute(row, ("PREP-a", run["id"], 1))
    with pytest.raises(sqlite3.IntegrityError):
        env.store.execute(row, ("PREP-b", run["id"], 2))


def test_a_project_that_ran_a_step_workflow_can_be_removed(env, tmp_path):
    """Found while testing project removal for phase runs: removing any project that had run a
    Standard-workflow Task failed on a foreign key (it did on the version before Stage B too)."""
    env.authorize_project_local("demo")
    env.configure_workflow("demo", "standard")
    task = env.create_task("demo", "A step-workflow task")
    assert env.store.one("SELECT COUNT(*) AS n FROM task_flows WHERE project_id='demo'")["n"] == 1
    assert task["id"]
    env.remove_project_web("demo", "REMOVE demo")
    assert env.store.one("SELECT COUNT(*) AS n FROM task_flows")["n"] == 0
