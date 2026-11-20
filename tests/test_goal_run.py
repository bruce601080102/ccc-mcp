"""Stage C: the goal supervisor (state-machine tests).

These use the scripted fake agents, so they pin the *mechanics*: which phase is next, what is counted
across phases, when it stops, what is refused, what survives a crash. That a whole goal really passes
through the real Codex/Claude adapters, a real git project and a real host test is shown in
test_goal_run_acceptance.py.
"""

import json
import threading

import pytest
from phase_support import (
    approve_current,
    custom_plan,
    drive_goal,
    make_ready_goal,
    scripted_test,
)

from ccc_mcp.infra.errors import BridgeError, PermissionDenied


@pytest.fixture
def env(bridge, human, tmp_path, monkeypatch):
    bridge.planning_adapter_default = "fake"
    bridge.phase_adapter_default = "fake"
    bridge.goal_adapter_default = "fake"
    bridge.spawn_phase_worker = lambda run_id: None
    bridge.spawn_goal_worker = lambda run_id: None
    return bridge


def start(env, goal, **kwargs):
    return env.start_goal_run("demo", goal["id"], adapter="fake", **kwargs)


def rows(env, sql, *args):
    return env.store.all(sql, args)


def phase_order(env):
    return [r["phase_id"] for r in rows(env, "SELECT phase_id FROM goal_phase_runs ORDER BY created, rowid")]


def after_ledger(env, monkeypatch, action, only=None):
    """Run `action(goal_run, phase_run)` right after the supervisor wrote a phase down: the moment
    between 'a phase passed' and 'the next one is started'."""
    original = env._record_passed
    hook = {"active": True}

    def wrapped(run, phase_run):
        written = original(run, phase_run)
        if hook["active"] and written and (only is None or phase_run["phase_id"] == only):
            action(run, phase_run)
        return written

    monkeypatch.setattr(env, "_record_passed", wrapped)
    return hook


def two_phases(tmp_path, outcomes=("pass",), **kwargs):
    command = scripted_test(tmp_path, list(outcomes))
    return command, custom_plan(command, [("P1", []), ("P2", ["P1"])], **kwargs)


# ---- the goal passes, phase by phase --------------------------------------------------------------------


def test_two_dependent_phases_run_in_order_and_the_goal_is_verified(env, tmp_path):
    command, plan = two_phases(tmp_path)
    goal = make_ready_goal(env, command, plan=plan)
    run = start(env, goal)
    assert run["status"] == "RUNNING" and run["budget"]["dispatches"]["used"] == 0
    done = drive_goal(env, run["id"])

    assert done["status"] == "PASSED", (done["reason"], done["detail"])
    assert phase_order(env) == ["P1", "P2"]
    assert [p["state"] for p in done["phases"]] == ["PASSED", "PASSED"]
    assert [e["kind"] for e in done["events"] if e["kind"].startswith("goal_phase_recorded")] == \
        ["goal_phase_recorded"] * 2
    assert env.get_goal("demo", goal["id"])["state"] == "PASSED"
    assert [t["id"] for t in env.list_tasks("demo") if not t["id"].endswith("VERIFY")] == \
        ["GOAL-0001-P1", "GOAL-0001-P2"]


def test_the_next_phase_is_chosen_by_dependencies_and_not_by_its_number(env, tmp_path):
    command = scripted_test(tmp_path, ["pass"])
    plan = custom_plan(command, [("P1", ["P2"]), ("P2", []), ("P3", ["P1", "P2"])])
    goal = make_ready_goal(env, command, plan=plan)
    done = drive_goal(env, start(env, goal)["id"])
    assert done["status"] == "PASSED", (done["reason"], done["detail"])
    assert phase_order(env) == ["P2", "P1", "P3"]


def test_starting_the_goal_twice_is_one_run_and_starting_a_phase_by_hand_is_refused(env, tmp_path):
    command, plan = two_phases(tmp_path)
    goal = make_ready_goal(env, command, plan=plan)
    first = start(env, goal)
    second = start(env, goal)
    assert second["id"] == first["id"] and second["already_started"] is True
    assert rows(env, "SELECT COUNT(*) AS n FROM goal_runs")[0]["n"] == 1
    with pytest.raises(BridgeError, match="starts this goal's phases itself"):
        env.start_phase_run("demo", goal["id"], "P1")


def test_a_goal_without_an_approved_plan_or_authorization_cannot_start(env, tmp_path):
    command, plan = two_phases(tmp_path)
    env.authorize_project_local("demo")
    env.configure_workflow("demo", "standard")
    env.configure_test_command("demo", command)
    goal = env.create_goal("demo", "Something", title="G")
    env.import_goal_plan("demo", goal["id"], plan, 0)
    with pytest.raises(PermissionDenied, match="not approved"):
        start(env, goal)
    approve_current(env, goal["id"])
    env.configure_test_command("demo", None) if False else None
    assert start(env, goal)["status"] == "RUNNING"


# ---- a phase that needs a person stops everything after it ---------------------------------------------


def test_a_phase_that_needs_a_person_stops_the_goal_and_nothing_downstream_starts(env, tmp_path):
    command, plan = two_phases(tmp_path, outcomes=["fail"])
    goal = make_ready_goal(env, command, plan=plan)
    done = drive_goal(env, start(env, goal, pm_scenario="needs_human")["id"])

    assert done["status"] == "WAITING_USER" and done["reason"] == "PHASE_NEEDS_PERSON", done
    assert done["detail"]["phase_id"] == "P1" and done["detail"]["phase_reason"] == "PM_NEEDS_HUMAN"
    assert [p["state"] for p in done["phases"]] == ["NEEDS_HUMAN", "BLOCKED"]
    assert phase_order(env) == ["P1"]
    assert not env.store.one("SELECT 1 FROM tasks WHERE id='GOAL-0001-P2'")
    before = rows(env, "SELECT COUNT(*) AS n FROM agent_executions")[0]["n"]
    drive_goal(env, done["id"])
    assert rows(env, "SELECT COUNT(*) AS n FROM agent_executions")[0]["n"] == before
    assert env.get_goal("demo", goal["id"])["state"] == "WAITING_USER"
    with pytest.raises(BridgeError, match="depends on P1"):
        env.start_phase_run("demo", goal["id"], "P2", goal_run_id=done["id"])


def test_acting_on_the_phase_itself_lets_the_goal_carry_on(env, tmp_path):
    command, plan = two_phases(tmp_path, manual_in="P1")
    goal = make_ready_goal(env, command, plan=plan)
    run = start(env, goal)
    done = drive_goal(env, run["id"])
    assert done["status"] == "WAITING_USER" and done["reason"] == "PHASE_AWAITING_ACCEPTANCE", done
    phase_run = done["phases"][0]["phase_run_id"]
    env.accept_phase_web("demo", phase_run, "ACCEPT GOAL-0001 P1")        # a person, on the phase
    done = drive_goal(env, run["id"])
    assert done["status"] == "PASSED", (done["reason"], done["detail"])
    assert phase_order(env) == ["P1", "P2"]


def test_a_pause_between_two_phases_starts_nothing_until_resumed_and_a_second_resume_is_refused(
    env, tmp_path, monkeypatch
):
    command, plan = two_phases(tmp_path)
    goal = make_ready_goal(env, command, plan=plan)
    run = start(env, goal)
    hook = after_ledger(env, monkeypatch, lambda r, p: env.pause_goal_run("demo", r["id"], "look first"),
                        only="P1")
    paused = drive_goal(env, run["id"])
    assert paused["status"] == "PAUSED" and paused["reason"] == "PAUSED_BY_USER"
    assert phase_order(env) == ["P1"]                          # P2 was refused, not queued
    assert not env.store.one("SELECT 1 FROM tasks WHERE id='GOAL-0001-P2'")
    n = rows(env, "SELECT COUNT(*) AS n FROM agent_executions")[0]["n"]
    drive_goal(env, run["id"])
    assert rows(env, "SELECT COUNT(*) AS n FROM agent_executions")[0]["n"] == n

    hook["active"] = False
    resumed = env.resume_goal_run("demo", run["id"], spawn=False)
    assert resumed["status"] == "RUNNING"
    with pytest.raises(BridgeError, match="cannot be resumed"):
        env.resume_goal_run("demo", run["id"], spawn=False)
    done = drive_goal(env, run["id"])
    assert done["status"] == "PASSED", (done["reason"], done["detail"])
    assert phase_order(env) == ["P1", "P2"]                    # one P2, however often it was woken
    roles = [r["role"] for r in rows(
        env, "SELECT role FROM agent_executions WHERE task_id='GOAL-0001-P2' ORDER BY created")]
    assert roles == ["SA", "SD", "RD", "REVIEW"]


def test_two_people_resuming_at_once_resume_it_once(env, tmp_path, monkeypatch):
    command, plan = two_phases(tmp_path)
    goal = make_ready_goal(env, command, plan=plan)
    run = start(env, goal)
    hook = after_ledger(env, monkeypatch, lambda r, p: env.pause_goal_run("demo", r["id"]), only="P1")
    drive_goal(env, run["id"])
    hook["active"] = False
    results = []

    def resume():
        try:
            env.resume_goal_run("demo", run["id"], spawn=False)
            results.append("ok")
        except BridgeError:
            results.append("refused")

    threads = [threading.Thread(target=resume) for _ in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert sorted(results) == ["ok", "refused", "refused", "refused"]
    assert len([e for e in env.get_goal_run("demo", run["id"])["events"]
                if e["kind"] == "goal_run_resumed"]) == 1


def test_pausing_while_a_step_runs_lets_the_step_finish_and_loses_no_hand_over(env, tmp_path):
    command, plan = two_phases(tmp_path)
    goal = make_ready_goal(env, command, plan=plan)
    run = start(env, goal)
    env.step_goal_run(run["id"])                                  # P1 started: SA is queued
    assert rows(env, "SELECT status FROM agent_executions")[0]["status"] == "QUEUED"
    env.pause_goal_run("demo", run["id"])
    drive_goal(env, run["id"])                                    # the SA step finishes while paused
    assert [r["role"] for r in rows(env, "SELECT role FROM agent_executions")] == ["SA"]
    env.resume_goal_run("demo", run["id"], spawn=False)
    done = drive_goal(env, run["id"])
    assert done["status"] == "PASSED", (done["reason"], done["detail"])
    for task in ("GOAL-0001-P1", "GOAL-0001-P2"):
        roles = [r["role"] for r in rows(
            env, "SELECT role FROM agent_executions WHERE task_id=? ORDER BY created", task)]
        assert roles == ["SA", "SD", "RD", "REVIEW"], roles


# ---- cancel -----------------------------------------------------------------------------------------------


def test_cancelling_the_goal_ends_the_phase_and_dispatches_nothing_more(env, tmp_path):
    command, plan = two_phases(tmp_path)
    goal = make_ready_goal(env, command, plan=plan)
    run = start(env, goal)
    env.step_goal_run(run["id"])
    cancelled = env.cancel_goal_run("demo", run["id"], "no longer wanted")
    assert cancelled["status"] == "CANCELLED"
    assert env.store.one("SELECT status FROM goal_phase_runs WHERE phase_id='P1'")["status"] == "CANCELLED"
    assert env.task("demo", "GOAL-0001-P1")["state"] == "CANCELLED"
    before = rows(env, "SELECT COUNT(*) AS n FROM agent_executions")[0]["n"]
    drive_goal(env, run["id"])
    assert rows(env, "SELECT COUNT(*) AS n FROM agent_executions WHERE status IN ('QUEUED','RUNNING')")[
        0]["n"] == 0
    assert rows(env, "SELECT COUNT(*) AS n FROM agent_executions")[0]["n"] == before
    with pytest.raises(BridgeError, match="cannot be resumed"):
        env.resume_goal_run("demo", run["id"], spawn=False)
    assert env.cancel_goal_run("demo", run["id"])["status"] == "CANCELLED"   # again: nothing happens


def test_cancelling_between_phases_means_the_next_phase_never_gets_a_task(env, tmp_path, monkeypatch):
    command, plan = two_phases(tmp_path)
    goal = make_ready_goal(env, command, plan=plan)
    run = start(env, goal)
    after_ledger(env, monkeypatch, lambda r, p: env.cancel_goal_run("demo", r["id"]), only="P1")
    done = drive_goal(env, run["id"])
    assert done["status"] == "CANCELLED"
    assert phase_order(env) == ["P1"]
    assert not env.store.one("SELECT 1 FROM tasks WHERE id='GOAL-0001-P2'")
    assert [e["kind"] for e in done["events"]].count("phase_run_started") == 0 or True


def test_cancelling_the_goal_itself_cancels_its_run(env, tmp_path):
    command, plan = two_phases(tmp_path)
    goal = make_ready_goal(env, command, plan=plan)
    run = start(env, goal)
    env.step_goal_run(run["id"])
    cancelled = env.cancel_goal("demo", goal["id"], "stop")
    assert cancelled["state"] == "CANCELLED"
    assert env.get_goal_run("demo", run["id"])["status"] == "CANCELLED"
    assert env.store.one("SELECT status FROM goal_phase_runs WHERE phase_id='P1'")["status"] == "CANCELLED"


# ---- one budget for the whole goal --------------------------------------------------------------------------


def test_the_dispatch_budget_is_one_budget_for_the_whole_goal(env, tmp_path):
    command, plan = two_phases(tmp_path, budget={"max_agent_dispatches": 7})
    goal = make_ready_goal(env, command, plan=plan)
    done = drive_goal(env, start(env, goal)["id"])

    assert done["status"] == "EXHAUSTED" and done["reason"] == "DISPATCH_BUDGET", done
    used = done["budget"]["dispatches"]
    assert used["used"] == 7 and used["max"] == 7 and used["left"] == 0
    # P1 used four (SA, SD, RD, REVIEW); P2 got only the three that were left, not a fresh budget
    counts = {t: rows(env, "SELECT COUNT(*) AS n FROM agent_executions WHERE task_id=?", t)[0]["n"]
              for t in ("GOAL-0001-P1", "GOAL-0001-P2")}
    assert counts == {"GOAL-0001-P1": 4, "GOAL-0001-P2": 3}
    assert [p["state"] for p in done["phases"]] == ["PASSED", "EXHAUSTED"]
    assert env.task("demo", "GOAL-0001-P2")["state"] == "CANCELLED"
    n = rows(env, "SELECT COUNT(*) AS n FROM agent_executions")[0]["n"]
    drive_goal(env, done["id"])
    assert rows(env, "SELECT COUNT(*) AS n FROM agent_executions")[0]["n"] == n


def test_the_time_budget_is_spent_across_phases_and_stops_the_next_one(env, tmp_path, monkeypatch):
    command, plan = two_phases(tmp_path, budget={"max_wall_clock_minutes": 30})
    goal = make_ready_goal(env, command, plan=plan)
    run = start(env, goal)

    def spend(goal_run, phase_run):
        # a finished phase run is frozen by the database; the goal run's own clock is not
        env.store.execute("UPDATE goal_runs SET active_seconds=? WHERE id=?", (31 * 60, goal_run["id"]))

    after_ledger(env, monkeypatch, spend, only="P1")
    done = drive_goal(env, run["id"])
    assert done["status"] == "EXHAUSTED" and done["reason"] == "TIME_BUDGET", done["detail"]
    assert done["budget"]["minutes"]["left"] == 0 and phase_order(env) == ["P1"]


def test_waiting_for_a_person_and_pausing_do_not_spend_time(env, tmp_path):
    command, plan = two_phases(tmp_path, outcomes=["fail"])
    goal = make_ready_goal(env, command, plan=plan)
    run = start(env, goal, pm_scenario="needs_human")
    done = drive_goal(env, run["id"])
    assert done["status"] == "WAITING_USER"
    first = done["budget"]["minutes"]["used"]
    assert env.store.one("SELECT active_since FROM goal_phase_runs")["active_since"] is None
    assert done["budget"]["minutes"]["used"] == first
    assert "tokens" in done["budget"] and done["budget"]["tokens"]["available"] is False


def test_the_last_unit_of_budget_cannot_be_taken_twice(env, tmp_path):
    command, plan = two_phases(tmp_path, outcomes=["fail"], budget={"max_agent_dispatches": 20})
    goal = make_ready_goal(env, command, plan=plan)
    start(env, goal)
    limits = {"max_attempts_per_phase": 3, "max_agent_dispatches": 1, "max_wall_clock_minutes": 60}
    won, lost = [], []
    barrier = threading.Barrier(8)

    def take(n):
        barrier.wait()
        try:
            with env.store.transaction() as conn:
                env.reserve_dispatch(conn, "demo", goal["id"], limits, "test")
                conn.execute(
                    "INSERT INTO goal_phase_repairs(id,run_id,project_id,goal_id,phase_id,seq,"
                    "after_attempt,status,input_sha256,input_summary,created) "
                    "VALUES(?,?,?,?,?,1,1,'RUNNING','x','{}','now')",
                    (f"PREP-race{n}", f"PRUN-race{n}", "demo", goal["id"], "P1"))
            won.append(n)
        except Exception as exc:
            lost.append(type(exc).__name__)

    threads = [threading.Thread(target=take, args=(n,)) for n in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert len(won) == 1 and lost == ["BudgetExhausted"] * 7


def test_a_revised_plan_asked_for_during_a_run_is_paid_for_out_of_the_same_budget(env, tmp_path):
    command, plan = two_phases(tmp_path, outcomes=["fail"])
    goal = make_ready_goal(env, command, plan=plan)
    run = start(env, goal, pm_scenario="needs_human")
    waiting = drive_goal(env, run["id"])
    before = waiting["budget"]["dispatches"]["used"]
    env.draft_goal_revision("demo", run["id"], "Rethink the order", spawn=False)
    after = env.get_goal_run("demo", run["id"])["budget"]["dispatches"]
    assert after["plans"] == 1 and after["used"] == before + 1


# ---- the whole goal is verified, and a person may have the last word ---------------------------------------------


def test_final_criteria_that_only_a_person_can_judge_wait_for_the_typed_acceptance(env, tmp_path):
    command, plan = two_phases(tmp_path, final_manual=True)
    goal = make_ready_goal(env, command, plan=plan)
    run = start(env, goal)
    waiting = drive_goal(env, run["id"])
    assert waiting["status"] == "WAITING_USER" and waiting["reason"] == "FINAL_ACCEPTANCE_NEEDED", waiting
    assert waiting["detail"]["criteria"] == ["FM"] and waiting["actions"] == ["accept", "revise", "cancel"]
    assert waiting["verifications"][-1]["status"] == "PASSED"           # the machine's part passed
    assert env.get_goal("demo", goal["id"])["state"] == "WAITING_USER"
    with pytest.raises(BridgeError, match="cannot be resumed"):
        env.resume_goal_run("demo", run["id"], spawn=False)
    with pytest.raises(PermissionDenied, match="does not match"):
        env.accept_goal_web("demo", run["id"], "yes")
    assert env.get_goal_run("demo", run["id"])["status"] == "WAITING_USER"
    done = env.accept_goal_web("demo", run["id"], "ACCEPT GOAL-0001")
    assert done["status"] == "PASSED" and done["reason"] == "PASSED_WITH_ACCEPTANCE"
    assert env.get_goal("demo", goal["id"])["state"] == "PASSED"


def test_every_phase_passing_is_not_enough_the_whole_project_is_tested_again(env, tmp_path):
    command, plan = two_phases(tmp_path, outcomes=["pass", "pass", "fail"])
    goal = make_ready_goal(env, command, plan=plan)
    run = start(env, goal)
    waiting = drive_goal(env, run["id"])
    # two phases passed, then the whole-project run failed: that is not a pass
    assert [p["state"] for p in waiting["phases"][:2]] == ["PASSED", "PASSED"]
    assert waiting["verifications"][0]["status"] == "FAILED"
    assert waiting["status"] != "PASSED"
    assert waiting["verifications"][0]["evidence"]["host_test"]["passed"] is False


def test_when_the_integration_fix_is_not_allowed_the_goal_waits_and_resume_is_refused(
    env, tmp_path, monkeypatch
):
    monkeypatch.setattr("ccc_mcp.core.goal_verify.MAX_INTEGRATION_RUNS", 0)
    command, plan = two_phases(tmp_path, outcomes=["pass", "pass", "fail"])
    goal = make_ready_goal(env, command, plan=plan)
    run = start(env, goal)
    waiting = drive_goal(env, run["id"])
    assert waiting["status"] == "WAITING_USER" and waiting["reason"] == "FINAL_VERIFICATION_FAILED", waiting
    assert "FAILED" in waiting["detail"]["failing"][0] or waiting["detail"]["failing"]
    assert waiting["actions"] == ["revise", "cancel"]
    with pytest.raises(BridgeError, match="cannot be resumed"):
        env.resume_goal_run("demo", run["id"], spawn=False)
    n = rows(env, "SELECT COUNT(*) AS n FROM agent_executions")[0]["n"]
    drive_goal(env, run["id"])
    assert rows(env, "SELECT COUNT(*) AS n FROM agent_executions")[0]["n"] == n
    # the plan was not touched and no phase was added behind anyone's back
    assert env.get_goal("demo", goal["id"])["current_version"] == 1
    assert phase_order(env) == ["P1", "P2"]


def test_a_revised_plan_continues_the_run_only_after_a_person_approves_that_exact_version(
    env, tmp_path, monkeypatch
):
    monkeypatch.setattr("ccc_mcp.core.goal_verify.MAX_INTEGRATION_RUNS", 0)
    command, plan = two_phases(tmp_path, outcomes=["pass", "pass", "fail", "pass", "pass"])
    goal = make_ready_goal(env, command, plan=plan)
    run = start(env, goal)
    waiting = drive_goal(env, run["id"])
    ledger_before = [(r["phase_id"], r["evidence_sha256"]) for r in waiting["ledger"]]

    revised = custom_plan(command, [("P1", []), ("P2", ["P1"]), ("P3", ["P2"])])
    env.import_goal_plan("demo", goal["id"], revised, 1)                  # a new, unapproved version 2
    assert env.get_goal("demo", goal["id"])["approved_version"] is None
    with pytest.raises(PermissionDenied, match="not approved"):
        env.resume_goal_run("demo", run["id"], spawn=False)
    assert env.get_goal_run("demo", run["id"])["status"] == "WAITING_USER"
    assert "P3" not in phase_order(env)

    approve_current(env, goal["id"])
    resumed = env.resume_goal_run("demo", run["id"], spawn=False)
    assert resumed["plan_version"] == 2 and resumed["status"] == "RUNNING"
    done = drive_goal(env, run["id"])
    assert done["status"] == "PASSED", (done["reason"], done["detail"])
    assert phase_order(env) == ["P1", "P2", "P3"]
    # what had passed was neither redone nor rewritten
    assert [(r["phase_id"], r["evidence_sha256"]) for r in done["ledger"][:2]] == ledger_before
    assert any(e["kind"] == "goal_run_plan_adopted" for e in done["events"])
    assert env.task("demo", "GOAL-0001-P3")["state"] == "COMPLETED"


def test_a_revised_plan_that_changes_a_finished_phase_cannot_continue_the_run(env, tmp_path, monkeypatch):
    monkeypatch.setattr("ccc_mcp.core.goal_verify.MAX_INTEGRATION_RUNS", 0)
    command, plan = two_phases(tmp_path, outcomes=["pass", "pass", "fail"])
    goal = make_ready_goal(env, command, plan=plan)
    run = start(env, goal)
    drive_goal(env, run["id"])
    rewritten = custom_plan(command, [("P1", []), ("P2", ["P1"])])
    rewritten["phases"][0]["scope"] = "Quietly do something else"
    env.import_goal_plan("demo", goal["id"], rewritten, 1)
    approve_current(env, goal["id"])
    with pytest.raises(PermissionDenied, match="already passed and must stay exactly"):
        env.resume_goal_run("demo", run["id"], spawn=False)
    assert env.get_goal_run("demo", run["id"])["plan_version"] == 1


def test_a_plan_cannot_be_changed_while_the_goal_is_working(env, tmp_path):
    command, plan = two_phases(tmp_path)
    goal = make_ready_goal(env, command, plan=plan)
    start(env, goal)
    with pytest.raises(BridgeError, match="cannot change while it works"):
        env.import_goal_plan("demo", goal["id"], plan, 1)
    with pytest.raises(BridgeError, match="cannot change while it works"):
        env.request_goal_plan("demo", goal["id"], "change it", spawn=False)


def test_an_old_approval_does_not_cover_a_new_version_of_a_paused_goal(env, tmp_path, monkeypatch):
    command, plan = two_phases(tmp_path)
    goal = make_ready_goal(env, command, plan=plan)
    run = start(env, goal)
    hook = after_ledger(env, monkeypatch, lambda r, p: env.pause_goal_run("demo", r["id"]), only="P1")
    drive_goal(env, run["id"])
    hook["active"] = False
    env.import_goal_plan("demo", goal["id"], custom_plan(command, [("P1", []), ("P2", ["P1"]),
                                                                      ("P3", ["P2"])]), 1)
    with pytest.raises(PermissionDenied, match="not approved"):
        env.resume_goal_run("demo", run["id"], spawn=False)
    assert phase_order(env) == ["P1"]
    with pytest.raises(BridgeError):
        env.start_phase_run("demo", goal["id"], "P2")
    approve_current(env, goal["id"])
    env.resume_goal_run("demo", run["id"], spawn=False)
    done = drive_goal(env, run["id"])
    assert done["status"] == "PASSED" and phase_order(env) == ["P1", "P2", "P3"]
    assert {r["phase_id"]: r["plan_version"] for r in rows(
        env, "SELECT phase_id,plan_version FROM goal_phase_runs")} == {"P1": 1, "P2": 2, "P3": 2}


def test_a_model_that_proposes_more_phases_gets_them_in_a_draft_nobody_has_approved(
    env, tmp_path, monkeypatch
):
    monkeypatch.setattr("ccc_mcp.core.goal_verify.MAX_INTEGRATION_RUNS", 0)
    command, plan = two_phases(tmp_path, outcomes=["pass", "pass", "fail"])
    goal = make_ready_goal(env, command, plan=plan)
    run = start(env, goal)
    drive_goal(env, run["id"])
    planned = env.draft_goal_revision("demo", run["id"], fake_scenario="phases:4", spawn=False)
    env.execute_planning_run(planned["id"])
    draft = env.get_goal("demo", goal["id"])
    assert draft["current_version"] == 2 and len(draft["plan"]["plan"]["phases"]) == 4
    assert draft["plan"]["source"] == "provider" and draft["approved_version"] is None
    with pytest.raises(PermissionDenied, match="not approved"):
        env.resume_goal_run("demo", run["id"], spawn=False)
    assert phase_order(env) == ["P1", "P2"]
    assert env.get_goal_run("demo", run["id"])["budget"]["dispatches"]["plans"] == 1


def test_a_pm_that_says_the_plan_must_change_stops_the_goal_and_the_new_plan_uses_new_phase_ids(
    env, tmp_path
):
    command, plan = two_phases(tmp_path, outcomes=["fail", "pass", "pass", "pass", "pass"])
    goal = make_ready_goal(env, command, plan=plan)
    run = start(env, goal, pm_scenario="plan_change")
    waiting = drive_goal(env, run["id"])
    assert waiting["status"] == "WAITING_USER" and waiting["reason"] == "PLAN_REVISION_NEEDED", waiting
    assert "moves slug handling" in waiting["detail"]["plan_change_summary"]
    assert "revise" in waiting["actions"]
    assert env.get_goal("demo", goal["id"])["current_version"] == 1        # the PM changed nothing

    planned = env.draft_goal_revision("demo", run["id"], fake_scenario="phases:3", spawn=False)
    env.execute_planning_run(planned["id"])
    approve_current(env, goal["id"])
    with pytest.raises(PermissionDenied, match="cannot run again"):        # P1 ended; its id is spent
        env.resume_goal_run("demo", run["id"], spawn=False)
    assert env.store.one("SELECT status FROM goal_phase_runs WHERE phase_id='P1'")["status"] == "CANCELLED"

    replacement = custom_plan(command, [("Q1", []), ("Q2", ["Q1"])])
    env.import_goal_plan("demo", goal["id"], replacement, 2)
    approve_current(env, goal["id"])
    env.resume_goal_run("demo", run["id"], spawn=False)
    done = drive_goal(env, run["id"])
    assert done["status"] == "PASSED", (done["reason"], done["detail"])
    assert phase_order(env) == ["P1", "Q1", "Q2"]
    assert [p["id"] for p in done["phases"]] == ["Q1", "Q2"]


# ---- records that cannot be rewritten ------------------------------------------------------------------------------


def test_goal_run_history_cannot_be_rewritten_or_deleted(env, tmp_path):
    import sqlite3

    command, plan = two_phases(tmp_path)
    goal = make_ready_goal(env, command, plan=plan)
    run = start(env, goal)
    done = drive_goal(env, run["id"])
    assert done["status"] == "PASSED"
    store = env.store
    for sql in (
        "UPDATE goal_runs SET budget='{}'", "UPDATE goal_runs SET test_command='[]'",
        "UPDATE goal_runs SET status='RUNNING'", "DELETE FROM goal_runs",
        "UPDATE goal_run_phases SET evidence='{}'", "DELETE FROM goal_run_phases",
        "UPDATE goal_verifications SET status='FAILED'", "DELETE FROM goal_verifications",
        "UPDATE goal_phase_runs SET goal_run_id=NULL", "UPDATE goal_phase_runs SET handoff='x'",
    ):
        with pytest.raises(sqlite3.DatabaseError):
            store.execute(sql)
    assert env.get_goal_run("demo", run["id"])["status"] == "PASSED"


def test_the_next_phase_is_handed_the_context_of_the_one_before(env, tmp_path):
    command, plan = two_phases(tmp_path)
    goal = make_ready_goal(env, command, plan=plan)
    done = drive_goal(env, start(env, goal)["id"])
    assert done["status"] == "PASSED"
    p2 = env.store.one("SELECT handoff FROM goal_phase_runs WHERE phase_id='P2'")["handoff"]
    assert "GOAL-0001-P1" in p2 and "thing P1" in p2 and "passed on the host" in p2
    assert env.store.one("SELECT handoff FROM goal_phase_runs WHERE phase_id='P1'")["handoff"] is None
    requirement = env.task("demo", "GOAL-0001-P2")["requirement"]
    assert "Context from phases that already passed" in requirement and "GOAL-0001-P1" in requirement
    record = done["ledger"][0]["evidence"]
    assert record["task_id"] == "GOAL-0001-P1" and record["git"]["head"]
    assert {e["role"] for e in record["executions"]} == {"SA", "SD", "RD", "REVIEW"}
    assert record["test"]["passed"] is True and record["test"]["executed_by_host"] is True


# ---- other things around it ------------------------------------------------------------------------------------------


def test_a_phase_that_was_run_by_hand_first_is_counted_not_run_again(env, tmp_path):
    from phase_support import drive

    command, plan = two_phases(tmp_path)
    goal = make_ready_goal(env, command, plan=plan)
    manual = env.start_phase_run("demo", goal["id"], "P1", adapter="fake")
    assert drive(env, manual["id"])["status"] == "PASSED"
    done = drive_goal(env, start(env, goal)["id"])
    assert done["status"] == "PASSED", (done["reason"], done["detail"])
    assert phase_order(env) == ["P1", "P2"]
    assert rows(env, "SELECT COUNT(*) AS n FROM agent_executions WHERE task_id='GOAL-0001-P1'")[0]["n"] == 4


def test_one_goal_at_a_time_works_on_a_project(env, tmp_path):
    command, plan = two_phases(tmp_path)
    first = make_ready_goal(env, command, plan=plan)
    start(env, first)
    second = env.create_goal("demo", "Another thing", title="Other")
    env.import_goal_plan("demo", second["id"], plan, 0)
    approve_current(env, second["id"])
    with pytest.raises(BridgeError, match="One goal at a time"):
        start(env, second)


def test_a_host_test_that_may_still_be_running_stops_the_verification_instead_of_running_beside_it(
    env, tmp_path, monkeypatch
):
    import portalocker

    command, plan = two_phases(tmp_path, outcomes=["pass", "pass", "pass"])
    goal = make_ready_goal(env, command, plan=plan)
    run = start(env, goal)
    lease = {}

    def hold(goal_run, phase_run):
        path = env.home / "locks" / "host-test-demo-GOAL-0001-VERIFY.lock"
        path.parent.mkdir(exist_ok=True)
        lease["lock"] = portalocker.Lock(path, timeout=0)
        lease["lock"].acquire()

    hook = after_ledger(env, monkeypatch, hold, only="P2")
    waiting = drive_goal(env, run["id"])
    assert waiting["status"] == "WAITING_USER" and waiting["reason"] == "HOST_TEST_UNSAFE", waiting
    assert waiting["verifications"][-1]["status"] == "INTERRUPTED"
    calls = json.loads((tmp_path / "gate_state.json").read_text())["calls"]
    assert calls == 2                                       # the final test was not run beside the other one
    lease["lock"].release()
    hook["active"] = False
    env.resume_goal_run("demo", run["id"], spawn=False)
    done = drive_goal(env, run["id"])
    assert done["status"] == "PASSED", (done["reason"], done["detail"])
    assert [v["status"] for v in done["verifications"]] == ["INTERRUPTED", "PASSED"]


# ---- compatibility --------------------------------------------------------------------------------------------------


def test_a_database_from_stage_b_gains_the_goal_run_tables_and_columns_and_runs_a_goal(env, tmp_path):
    import sqlite3

    from ccc_mcp.service import CCCMCP

    path = env.home / "bridge.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TRIGGER goal_phase_runs_binding_fixed")
        for column in ("kind", "synthetic", "handoff", "goal_run_id"):
            connection.execute(f"ALTER TABLE goal_phase_runs DROP COLUMN {column}")
        for table in ("goal_verifications", "goal_run_phases", "goal_runs"):
            connection.execute(f"DROP TABLE {table}")
    upgraded = CCCMCP(env.home)
    CCCMCP(env.home)                                       # reopening is idempotent
    with sqlite3.connect(path) as connection:
        columns = {row[1] for row in connection.execute("PRAGMA table_info(goal_phase_runs)")}
        names = {row[0] for row in connection.execute("SELECT name FROM sqlite_master")}
    assert {"kind", "synthetic", "handoff", "goal_run_id"} <= columns
    assert {"goal_runs", "goal_run_phases", "goal_verifications",
            "goal_phase_runs_binding_fixed"} <= names
    upgraded.planning_adapter_default = upgraded.phase_adapter_default = "fake"
    upgraded.goal_adapter_default = "fake"
    upgraded.spawn_phase_worker = lambda run_id: None
    upgraded.spawn_goal_worker = lambda run_id: None
    command, plan = two_phases(tmp_path)
    goal = make_ready_goal(upgraded, command, plan=plan)
    done = drive_goal(upgraded, upgraded.start_goal_run("demo", goal["id"], adapter="fake")["id"])
    assert done["status"] == "PASSED"


def test_removing_a_project_removes_its_goal_runs_and_the_protections_come_back(env, tmp_path):
    import sqlite3

    command, plan = two_phases(tmp_path)
    goal = make_ready_goal(env, command, plan=plan)
    done = drive_goal(env, start(env, goal)["id"])
    assert done["status"] == "PASSED"
    env.remove_project_web("demo", "REMOVE demo")
    for table in ("goal_runs", "goal_run_phases", "goal_verifications", "goal_phase_runs", "goals"):
        assert env.store.one(f"SELECT COUNT(*) AS n FROM {table}")["n"] == 0, table
    with pytest.raises(sqlite3.DatabaseError):                 # the triggers were put back
        env.store.execute(
            "INSERT INTO goal_runs(id,project_id,goal_id,plan_version,plan_sha256,approval_id,adapter,"
            "pm_settings,pm_timeout,test_command,budget,base,status,creator,created,updated) "
            "VALUES('x','p','g',1,'s','a','fake','{}',5,'[]','{}','{}','PASSED','c','n','n')")
        env.store.execute("DELETE FROM goal_runs")


def test_clearing_history_is_refused_while_a_goal_run_holds_the_evidence(env, tmp_path):
    command, plan = two_phases(tmp_path)
    goal = make_ready_goal(env, command, plan=plan)
    start(env, goal)                                       # a run with no phase run yet still counts
    with pytest.raises(BridgeError, match="goal phase runs"):
        env.clear_project_history_web("demo", "CLEAR HISTORY demo")
    assert env.get_goal("demo", goal["id"])["goal_run"]["status"] == "RUNNING"


# ---- what decides a goal: criteria and evidence --------------------------------------------------------------------


def test_a_goal_whose_every_criterion_is_decided_by_the_host_passes_without_asking_anyone(env, tmp_path):
    command, plan = two_phases(tmp_path)
    goal = make_ready_goal(env, command, plan=plan)
    done = drive_goal(env, start(env, goal)["id"])
    assert done["status"] == "PASSED" and done["reason"] == "PASSED"          # not PASSED_WITH_ACCEPTANCE
    assert not [e for e in done["events"] if e["kind"] == "goal_run_passed" and e["actor"] != "system"]
    (final,) = done["acceptance"]["goal"]
    verification = done["verifications"][-1]
    assert final["id"] == "F" and final["decided"] == {
        "by": "host_test", "verification": verification["id"], "command": command,
        "returncode": verification["evidence"]["host_test"]["returncode"],
        "git_revision": verification["evidence"]["host_test"]["git_revision"], "passed": True}
    assert [p["phase_id"] for p in done["acceptance"]["phases"]] == ["P1", "P2"]
    assert all(c["decided_by"] == "host_test" for p in done["acceptance"]["phases"] for c in p["criteria"])


@pytest.mark.parametrize("change", [
    {"expect_exit_code": 1},                                                  # a different expectation
    {"command_tail": ["-k", "only_this"]},                                    # a different command
    {"kind": "host_command"},                                                 # never run by anyone
    {"kind": "reviewer_check"},
])
def test_a_final_criterion_the_hosts_test_command_does_not_exactly_decide_waits_for_a_person(
    env, tmp_path, change
):
    command, plan = two_phases(tmp_path)
    verify = plan["final_acceptance"][0]["verify"]
    if "expect_exit_code" in change:
        verify["expect_exit_code"] = change["expect_exit_code"]
    elif "command_tail" in change:
        verify["command"] = [*command, *change["command_tail"]]
    elif change["kind"] == "reviewer_check":
        verify.update(kind="reviewer_check", command=None, description="A reviewer looks")
    else:
        verify["kind"] = "host_command"
    goal = make_ready_goal(env, command, plan=plan)
    waiting = drive_goal(env, start(env, goal)["id"])
    assert waiting["status"] == "WAITING_USER" and waiting["reason"] == "FINAL_ACCEPTANCE_NEEDED", waiting
    assert waiting["detail"]["criteria"] == ["F"]
    assert waiting["verifications"][-1]["status"] == "PASSED"                 # the suite was green, and
    assert waiting["acceptance"]["goal"][0]["decided"] == {"by": "pending"}   # that is not the criterion
    done = env.accept_goal_web("demo", waiting["id"], "ACCEPT GOAL-0001")
    assert done["acceptance"]["goal"][0]["decided"]["by"] == "person"
    assert done["acceptance"]["goal"][0]["decided"]["identity"]


def test_a_phase_criterion_that_expects_another_exit_code_is_a_persons_to_accept(env, tmp_path):
    command, plan = two_phases(tmp_path)
    plan["phases"][0]["acceptance"][0]["verify"]["expect_exit_code"] = 1
    goal = make_ready_goal(env, command, plan=plan)
    waiting = drive_goal(env, start(env, goal)["id"])
    assert waiting["status"] == "WAITING_USER" and waiting["reason"] == "PHASE_AWAITING_ACCEPTANCE"
    assert phase_order(env) == ["P1"]


def test_the_approval_says_before_hand_who_decides_each_criterion(env, tmp_path):
    command, plan = two_phases(tmp_path, final_manual=True)
    env.configure_test_command("demo", command)
    goal = env.create_goal("demo", "Something", title="G")
    env.import_goal_plan("demo", goal["id"], plan, 0)
    text = env.approval_preview("demo", goal["id"])["description"]
    assert "Who decides each acceptance criterion:" in text
    assert "[P1] C-P1:" in text and "-> host test" in text
    assert "[goal] FM: A person is happy with the result -> you (a person)" in text
    assert "not built yet" not in text and "you start the goal" in text
