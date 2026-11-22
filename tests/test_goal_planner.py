"""Stage A: Goal Planner. A requirement, a Delivery PM's plan, a person's approval.

Everything here uses the scripted fake PM or a fake native CLI; no real model is called. The
tests also pin what Stage A must NOT do: start any Task or Agent, approve on anyone's behalf,
or claim a budget limit is enforced.
"""

import json
import os
import sqlite3
import stat
import subprocess
import sys
import threading
from pathlib import Path

import pytest

from ccc_mcp.adapters.storage import now
from ccc_mcp.core.goal_plan import (
    MAX_PHASES,
    GoalPlanInvalid,
    forbidden_command,
    validate_plan,
)
from ccc_mcp.core.goals import MAX_PLANNING_RUNS, approval_challenge
from ccc_mcp.infra.errors import BridgeError, PermissionDenied, VersionConflict
from ccc_mcp.service import CCCMCP

WORKFLOWS = {"standard", "full"}
LEGACY_SQL = Path(__file__).parent / "fixtures" / "legacy_schema_0_4_0.sql"
REQUIREMENT = "Build a slug tool: a slugify function with tests, and a small command line."


@pytest.fixture
def goals(bridge, human):
    """The shared project with a scripted Delivery PM and no worker process."""
    bridge.planning_adapter_default = "fake"
    bridge.spawn_planning_worker = lambda run_id: None
    return bridge


def plan(service, goal_id, scenario="success", change=None, **extra):
    run = service.request_goal_plan(
        "demo", goal_id, change_request=change, fake_scenario=scenario, spawn=False, **extra)
    service.execute_planning_run(run["id"])
    return service.get_goal("demo", goal_id)


def approve(service, goal):
    return service.approve_goal_local(
        "demo", goal["id"], goal["plan"]["version"], goal["plan"]["sha256"])


def counts(service):
    tables = ("tasks", "agent_executions", "plans", "approvals", "context_versions", "task_flows",
              "pending_dispatch", "task_controls", "decisions", "host_test_runs")
    return {name: service.store.one(f"SELECT COUNT(*) AS n FROM {name}")["n"] for name in tables}


def good_plan(phases=2):
    def criterion(name):
        return {"id": name, "statement": f"{name} holds",
                "verify": {"kind": "host_test", "command": ["python", "-m", "pytest", "-q"]}}

    return {
        "summary": "A plan", "in_scope": ["the goal"], "out_of_scope": [],
        "phases": [
            {"id": f"P{i}", "title": f"Phase {i}", "scope": "do it", "outputs": ["a thing"],
             "depends_on": [f"P{i - 1}"] if i > 1 else [], "acceptance": [criterion(f"C{i}")],
             "workflow": "standard", "risk": "low"}
            for i in range(1, phases + 1)
        ],
        "final_acceptance": [criterion("F")], "decisions_needed": [], "assumptions": [],
        "risks": [],
    }


# 1, 2: the PM decides how many phases; the count is not a template ---------------------------


@pytest.mark.parametrize("count", [1, 2, 3, 5, 7, 12])
def test_the_pm_can_plan_any_number_of_phases(goals, count):
    goal = goals.create_goal("demo", REQUIREMENT)

    planned = plan(goals, goal["id"], f"phases:{count}")

    assert planned["state"] == "AWAITING_APPROVAL"
    phases = planned["plan"]["plan"]["phases"]
    assert [phase["id"] for phase in phases] == [f"P{i}" for i in range(1, count + 1)]
    rows = goals.store.all(
        "SELECT phase_id, ordinal, depends_on FROM goal_plan_phases WHERE goal_id=? ORDER BY ordinal",
        (goal["id"],))
    assert [row["phase_id"] for row in rows] == [phase["id"] for phase in phases]
    assert [row["ordinal"] for row in rows] == list(range(1, count + 1))
    assert planned["plan"]["message"] and planned["plan"]["source"] == "provider"
    assert planned["plan"]["author"]["provider"] == "codex"


def test_the_default_plan_is_not_a_fixed_three_phase_template(goals):
    sizes = {len(plan(goals, goals.create_goal("demo", f"{REQUIREMENT} {n}")["id"], s)["plan"][
        "plan"]["phases"]) for n, s in enumerate(["success", "phases:1", "phases:4"])}
    assert sizes == {2, 1, 4}


def test_more_phases_than_the_runaway_guard_are_refused(goals):
    goal = goals.create_goal("demo", REQUIREMENT)
    after = plan(goals, goal["id"], f"phases:{MAX_PHASES + 1}")
    assert after["current_version"] == 0 and after["state"] == "DRAFT"
    assert after["last_run"]["failure_kind"] == "INVALID_OUTPUT"
    assert f"1 to {MAX_PHASES}" in after["last_run"]["error"]


# 3, 4: dependencies are checked; invalid model output is refused ----------------------------


@pytest.mark.parametrize("scenario, fragment", [
    ("cyclic", "cycle"),
    ("duplicate_ids", "duplicate phase id"),
    ("missing_dependency", "not a phase of this plan"),
    ("no_acceptance", "at least 1 acceptance criterion"),
    ("shell_command", "shell"),
    ("git_push", "read-only git"),
    ("unknown_workflow", "not one this project can run"),
    ("empty", "1 to 12 phases"),
    ("schema_error", "invalid structured report"),
])
def test_an_invalid_model_answer_is_refused_and_nothing_is_stored(goals, scenario, fragment):
    goal = goals.create_goal("demo", REQUIREMENT)

    after = plan(goals, goal["id"], scenario)

    assert after["current_version"] == 0 and after["plan"] is None and after["state"] == "DRAFT"
    run = after["last_run"]
    assert run["status"] == "FAILED" and run["failure_kind"] == "INVALID_OUTPUT"
    assert fragment in run["error"]
    assert goals.store.one("SELECT COUNT(*) AS n FROM goal_plan_versions")["n"] == 0
    assert [e["kind"] for e in after["events"]][-1] == "plan_run_failed"
    if scenario != "schema_error":
        stored = goals.store.one("SELECT result FROM goal_plan_runs WHERE id=?", (run["id"],))
        assert stored["result"], "the refused answer is kept for inspection"


def test_a_failed_run_never_becomes_a_plan_and_a_good_run_can_follow(goals):
    goal = goals.create_goal("demo", REQUIREMENT)
    assert plan(goals, goal["id"], "cyclic")["state"] == "DRAFT"
    assert plan(goals, goal["id"], "phases:3")["state"] == "AWAITING_APPROVAL"


def test_dependency_rules_in_the_validator():
    diamond = good_plan(4)
    diamond["phases"][1]["depends_on"] = ["P1"]
    diamond["phases"][2]["depends_on"] = ["P1"]
    diamond["phases"][3]["depends_on"] = ["P2", "P3"]
    assert len(validate_plan(diamond, known_workflows=WORKFLOWS).phases) == 4

    forward = good_plan(2)
    forward["phases"][0]["depends_on"] = ["P2"]
    forward["phases"][1]["depends_on"] = []
    validate_plan(forward, known_workflows=WORKFLOWS)  # a forward reference is fine if acyclic

    for mutate, text in [
        (lambda p: p["phases"][0].update(depends_on=["P1"]), "depends on itself"),
        (lambda p: p["phases"][0].update(depends_on=["P3"]), "cycle"),
        (lambda p: p["phases"][1].update(depends_on=["P1", "p1"]), "twice"),
    ]:
        broken = good_plan(3)
        broken["phases"][1]["depends_on"] = ["P3"] if text == "cycle" else broken["phases"][1][
            "depends_on"]
        broken["phases"][2]["depends_on"] = ["P2"]
        mutate(broken)
        with pytest.raises(GoalPlanInvalid, match=text):
            validate_plan(broken, known_workflows=WORKFLOWS)

    long_cycle = good_plan(5)
    long_cycle["phases"][0]["depends_on"] = ["P5"]
    with pytest.raises(GoalPlanInvalid, match="P1 -> P5|P5 -> P4|cycle"):
        validate_plan(long_cycle, known_workflows=WORKFLOWS)


def test_every_problem_is_reported_not_just_the_first():
    bad = good_plan(2)
    bad["phases"][0]["title"] = ""
    bad["phases"][1]["workflow"] = "nope"
    bad["phases"][1]["acceptance"] = []
    with pytest.raises(GoalPlanInvalid) as raised:
        validate_plan(bad, known_workflows=WORKFLOWS)
    assert len(raised.value.problems) >= 3


@pytest.mark.parametrize("mutate, fragment", [
    (lambda p: p.update(extra="x"), "extra"),
    (lambda p: p["phases"][0].update(risk="spicy"), "risk"),
    (lambda p: p["phases"][0].update(provider_hint="chatgpt"), "provider_hint"),
    (lambda p: p["phases"][0].update(id="has space"), "id must be"),
    (lambda p: p["phases"][0]["acceptance"][0].update(id="F"), "duplicate criterion id"),
    (lambda p: p["phases"][0]["acceptance"][0]["verify"].update(command=None), "needs a command"),
    (lambda p: p["phases"][0]["acceptance"][0]["verify"].update(expect_exit_code=999), "exit_code"),
    (lambda p: p["phases"][0]["acceptance"][0].update(
        verify={"kind": "manual", "description": None}), "description is required"),
    (lambda p: p["phases"][0]["acceptance"][0].update(
        verify={"kind": "manual", "description": "look", "command": ["ls"]}), "only a host check"),
    (lambda p: p["phases"][0].update(scope="x‮y"), "unsupported characters"),
    (lambda p: p["phases"][0].update(scope="password: hunter2hunter2"), "secret"),
    (lambda p: p["budget"].update(enforced=True) if "budget" in p else p.update(
        budget={"enforced": True}), "enforced"),
    (lambda p: p.update(budget={"max_attempts_per_phase": 99}), "budget.max_attempts_per_phase"),
    (lambda p: p.update(budget={"max_wall_clock_minutes": 0}), "budget.max_wall_clock_minutes"),
    (lambda p: p.update(in_scope=[]), "in_scope"),
])
def test_the_validator_refuses_what_it_cannot_trust(mutate, fragment):
    candidate = good_plan(2)
    mutate(candidate)
    with pytest.raises(GoalPlanInvalid) as raised:
        validate_plan(candidate, known_workflows=WORKFLOWS)
    assert fragment in str(raised.value)


@pytest.mark.parametrize("argv, allowed", [
    (["python", "-m", "pytest", "-q"], True),
    (["pytest"], True),
    (["git", "diff", "--stat"], True),
    (["git", "log", "-1"], True),
    (["git", "push"], False), (["git", "commit", "-m", "x"], False),
    (["git", "-C", "x", "reset", "--hard"], False),
    (["sh", "-c", "pytest"], False), (["bash", "-lc", "x"], False),
    (["pytest -q"], False), (["pytest", "&&", "ls"], False), (["python", "a", ";", "b"], False),
    (["curl", "http://x"], False), (["rm", "-rf", "x"], False), (["docker", "push", "x"], False),
    (["npm", "publish"], False), (["twine", "upload", "d"], False), (["gh", "release"], False),
])
def test_host_commands_in_a_plan_are_plain_non_publishing_argument_lists(argv, allowed):
    assert (forbidden_command(argv) is None) is allowed


def test_the_plan_declares_the_budget_and_the_plan_itself_never_enables_it(goals):
    goal = plan(goals, goals.create_goal("demo", REQUIREMENT)["id"])
    budget = goal["plan"]["plan"]["budget"]
    assert (budget["max_attempts_per_phase"], budget["max_agent_dispatches"],
            budget["max_wall_clock_minutes"], budget["enforced"]) == (3, 20, 60, False)
    # Stage B enforces the declared budget when a phase runs (core/phase_runner.py); the plan's own
    # `enforced` flag stays False and cannot be set by a plan. An unapproved goal cannot run.
    assert goal["budget_enforced"] is True and goal["execution"]["available"] is False


# 5, 6, 7: revising, and what an approval covers -----------------------------------------------


def test_a_change_request_makes_a_new_version_and_keeps_the_old_one(goals):
    goal = plan(goals, goals.create_goal("demo", REQUIREMENT)["id"], "phases:2")
    first = goal["plan"]

    revised = plan(goals, goal["id"], "phases:3", change="Add a security review phase")

    assert revised["current_version"] == 2
    second = revised["plan"]
    assert second["sha256"] != first["sha256"] and second["based_on"] == 1
    assert second["change_request"] == "Add a security review phase"
    assert "Revised for: Add a security review phase" in second["plan"]["phases"][0]["scope"]
    versions = {v["version"]: v for v in revised["versions"]}
    assert versions[1]["sha256"] == first["sha256"] and versions[1]["phase_count"] == 2
    stored = goals.store.one(
        "SELECT sha256 FROM goal_plan_versions WHERE goal_id=? AND version=1", (goal["id"],))
    assert stored["sha256"] == first["sha256"], "version 1 is untouched"


def test_changing_the_plan_ends_the_old_approval(goals):
    goal = plan(goals, goals.create_goal("demo", REQUIREMENT)["id"])
    approve(goals, goal)
    assert goals.get_goal("demo", goal["id"])["state"] == "READY"

    revised = plan(goals, goal["id"], change="Make it smaller")

    assert revised["state"] == "AWAITING_APPROVAL"
    assert revised["approval"] is None and revised["approved_version"] is None
    assert {v["version"]: v["approved"] for v in revised["versions"]} == {1: True, 2: False}
    with pytest.raises(VersionConflict, match="plan changed"):
        goals.approve_goal_local("demo", goal["id"], 1, goal["plan"]["sha256"])
    with pytest.raises(VersionConflict, match="no longer the current plan"):
        goals._approve("demo", goal["id"], 1, goal["plan"]["sha256"], "someone")
    approve(goals, revised)
    assert goals.get_goal("demo", goal["id"])["approved_version"] == 2


def test_an_approval_needs_the_hash_of_the_version_it_names(goals):
    goal = plan(goals, goals.create_goal("demo", REQUIREMENT)["id"])
    with pytest.raises(VersionConflict, match="hash"):
        goals._approve("demo", goal["id"], 1, "0" * 64, "someone")
    assert goals.store.one("SELECT COUNT(*) AS n FROM goal_approvals")["n"] == 0


# 7: approving twice -----------------------------------------------------------------------------


def test_approving_the_same_version_again_changes_nothing(goals):
    goal = plan(goals, goals.create_goal("demo", REQUIREMENT)["id"])
    first = approve(goals, goal)
    second = approve(goals, goal)

    assert first["already_approved"] is False and second["already_approved"] is True
    assert first["approval_id"] == second["approval_id"]
    after = goals.get_goal("demo", goal["id"])
    assert [e["kind"] for e in after["events"]].count("approved") == 1
    assert goals.store.one("SELECT COUNT(*) AS n FROM goal_approvals")["n"] == 1
    assert after["state"] == "READY"
    assert after["approval"]["identity"] == "test-principal"
    assert after["approval"]["scope"]["does_not_cover"]


def test_an_approval_records_who_when_and_the_project_authorization_it_ran_under(goals):
    goal = plan(goals, goals.create_goal("demo", REQUIREMENT)["id"])
    approve(goals, goal)
    row = goals.store.one("SELECT * FROM goal_approvals")
    assert row["identity"] == "test-principal" and row["created"]
    assert json.loads(row["project_access"]) == {"mode": "read-only", "authorization_id": None}
    assert row["plan_sha256"] == goal["plan"]["sha256"]


# 8: restart -------------------------------------------------------------------------------------


def test_after_a_restart_the_goal_its_plan_and_approval_are_the_same(goals):
    goal = plan(goals, goals.create_goal("demo", REQUIREMENT)["id"], "phases:4")
    approve(goals, goal)
    before = goals.get_goal("demo", goal["id"])
    runs_before = goals.store.one("SELECT COUNT(*) AS n FROM goal_plan_runs")["n"]

    restarted = CCCMCP(goals.home)
    after = restarted.get_goal("demo", goal["id"])

    assert after["state"] == "READY"
    assert after["plan"]["sha256"] == before["plan"]["sha256"]
    assert after["approval"]["id"] == before["approval"]["id"]
    assert after["requirement"] == REQUIREMENT
    assert restarted.list_goals("demo")[0]["id"] == goal["id"]
    assert restarted.store.one("SELECT COUNT(*) AS n FROM goal_plan_runs")["n"] == runs_before, \
        "reading never plans again"
    assert [e["seq"] for e in after["events"]] == [e["seq"] for e in before["events"]]


def test_goal_ids_belong_to_one_project_and_are_never_reused(goals, tmp_path):
    first = goals.create_goal("demo", "First requirement")
    goals.cancel_goal("demo", first["id"])
    second = goals.create_goal("demo", "Second requirement")
    assert (first["id"], second["id"]) == ("GOAL-0001", "GOAL-0002")

    other = tmp_path / "other"
    other.mkdir()
    goals.register_project(other, "other", "another project")
    assert goals.create_goal("other", "Elsewhere")["id"] == "GOAL-0001"
    with pytest.raises(BridgeError, match="Goal not found"):
        goals.get_goal("other", "GOAL-0002")
    assert [g["id"] for g in goals.list_goals("demo")] == ["GOAL-0001", "GOAL-0002"]


# 9: concurrency and duplicates ------------------------------------------------------------------


def race(count, work):
    results, errors = [], []
    start = threading.Barrier(count)

    def run(index):
        start.wait()
        try:
            results.append(work(index))
        except Exception as exc:  # noqa: BLE001 - the test inspects every outcome
            errors.append(exc)

    threads = [threading.Thread(target=run, args=(i,)) for i in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(60)
    return results, errors


def test_a_goal_sent_many_times_with_one_key_is_created_once(goals):
    results, errors = race(8, lambda i: goals.create_goal(
        "demo", REQUIREMENT, request_key="key-for-one-goal"))
    assert not errors
    assert {r["id"] for r in results} == {"GOAL-0001"}
    assert sum(r["created_now"] for r in results) == 1
    assert goals.store.one("SELECT COUNT(*) AS n FROM goals")["n"] == 1
    with pytest.raises(BridgeError, match="different requirement"):
        goals.create_goal("demo", "something else", request_key="key-for-one-goal")


def test_goals_created_at_once_get_distinct_ids(goals):
    results, errors = race(6, lambda i: goals.create_goal("demo", f"Requirement {i}"))
    assert not errors
    assert sorted(r["id"] for r in results) == [f"GOAL-{n:04d}" for n in range(1, 7)]


def test_simultaneous_approvals_record_one_approval_and_one_event(goals):
    goal = plan(goals, goals.create_goal("demo", REQUIREMENT)["id"])
    results, errors = race(8, lambda i: goals._approve(
        "demo", goal["id"], 1, goal["plan"]["sha256"], f"operator-{i}"))
    assert not errors
    assert sum(not r["already_approved"] for r in results) == 1
    assert len({r["approval_id"] for r in results}) == 1
    after = goals.get_goal("demo", goal["id"])
    assert [e["kind"] for e in after["events"]].count("approved") == 1
    assert goals.store.one("SELECT COUNT(*) AS n FROM goal_approvals")["n"] == 1


def test_two_edits_from_the_same_version_cannot_both_win(goals):
    goal = plan(goals, goals.create_goal("demo", REQUIREMENT)["id"])
    results, errors = race(2, lambda i: goals.import_goal_plan(
        "demo", goal["id"], good_plan(2 + i), 1, f"edit {i}"))
    assert len(results) == 1 and len(errors) == 1 and isinstance(errors[0], VersionConflict)
    after = goals.get_goal("demo", goal["id"])
    assert [v["version"] for v in after["versions"]] == [1, 2], "no version lost or duplicated"


def test_an_edit_racing_an_approval_never_loses_either(goals):
    goal = plan(goals, goals.create_goal("demo", REQUIREMENT)["id"])
    results, errors = race(2, lambda i: (
        goals._approve("demo", goal["id"], 1, goal["plan"]["sha256"], "operator")
        if i == 0 else goals.import_goal_plan("demo", goal["id"], good_plan(3), 1, "edit")))
    assert not [e for e in errors if not isinstance(e, VersionConflict)]
    after = goals.get_goal("demo", goal["id"])
    assert after["current_version"] == 2, "the edit always lands"
    approvals = goals.store.all("SELECT plan_version FROM goal_approvals")
    assert [a["plan_version"] for a in approvals] in ([1], []), "an approval is only ever for v1"
    assert after["state"] == "AWAITING_APPROVAL" and after["approval"] is None


def test_only_one_planning_run_is_live_per_goal(goals):
    goal = goals.create_goal("demo", REQUIREMENT)
    goals.request_goal_plan("demo", goal["id"], spawn=False)
    results, errors = race(4, lambda i: goals.request_goal_plan("demo", goal["id"], spawn=False))
    assert not results and len(errors) == 4
    assert all("already planning" in str(e) for e in errors)
    assert goals.store.one("SELECT COUNT(*) AS n FROM goal_plan_runs")["n"] == 1


def test_a_run_that_finishes_after_the_plan_changed_is_kept_but_not_applied(goals):
    goal = goals.create_goal("demo", REQUIREMENT)
    run = goals.request_goal_plan("demo", goal["id"], spawn=False, fake_scenario="phases:5")
    goals.import_goal_plan("demo", goal["id"], good_plan(2), 0, "the user wrote this meanwhile")

    goals.execute_planning_run(run["id"])

    after = goals.get_goal("demo", goal["id"])
    assert [v["version"] for v in after["versions"]] == [1]
    assert after["plan"]["source"] == "manual" and len(after["plan"]["plan"]["phases"]) == 2
    assert after["last_run"]["status"] == "FAILED" and after["last_run"]["failure_kind"] == "CONFLICT"
    stored = goals.store.one("SELECT result FROM goal_plan_runs WHERE id=?", (run["id"],))
    assert len(json.loads(stored["result"])["phases"]) == 5, "the PM's answer is not lost"


# 10, 11: Stage A touches no Task and starts no Agent -------------------------------------------


def test_the_whole_goal_lifecycle_creates_no_task_context_or_agent_work(goals, monkeypatch):
    task = goals.create_task("demo", "An ordinary task")
    before = counts(goals)
    version = goals.latest("demo")["version"]

    def boom(*args, **kwargs):
        raise AssertionError("Stage A must never start agent work")

    for name in ("spawn_worker", "dispatch_agent", "start_project_request", "advance_project",
                 "run_configured_test_command", "create_task", "propose_plan"):
        monkeypatch.setattr(goals, name, boom)
    goal = plan(goals, goals.create_goal("demo", REQUIREMENT)["id"], "phases:3")
    approve(goals, goal)
    plan(goals, goal["id"], change="Smaller please")
    goals.import_goal_plan("demo", goal["id"], good_plan(2), 2, "manual")
    goals.cancel_goal("demo", goal["id"], "no longer needed")

    assert counts(goals) == before
    assert goals.latest("demo")["version"] == version, "the shared Context is untouched"
    monkeypatch.undo()
    assert [t["id"] for t in goals.list_tasks("demo")] == [task["id"]]
    assert goals.task("demo", task["id"])["state"] == "CREATED"


def test_approving_a_plan_starts_nothing_by_itself(goals):
    goal = plan(goals, goals.create_goal("demo", REQUIREMENT)["id"])
    approve(goals, goal)
    ready = goals.get_goal("demo", goal["id"])
    assert ready["state"] == "READY"
    # Approving only makes a phase startable; nothing is created or started by the approval itself.
    assert ready["execution"]["available"] is True
    assert ready["execution"]["automatic_next_phase"] is False
    assert ready["phase_runs"] == [] and goals.list_tasks("demo") == []
    assert "nothing starts by itself" in ready["next_action"]


def test_existing_task_workflows_behave_as_before(goals):
    first = goals.create_task("demo", "Existing work")
    plan(goals, goals.create_goal("demo", REQUIREMENT)["id"])
    assert goals.create_task("demo", "More work", task_id="TASK-0009")["id"] == "TASK-0009"
    assert [t["id"] for t in goals.list_tasks("demo")] == [first["id"], "TASK-0009"]
    assert goals.project_workstream_task("demo") == "TASK-0009"


# 12: no provider, no pretending ------------------------------------------------------------------


def test_a_chatgpt_only_project_says_so_and_stores_nothing(bridge, human):
    bridge.configure_role("demo", "SA", "chatgpt")
    goal = bridge.create_goal("demo", REQUIREMENT)
    with pytest.raises(BridgeError, match="ChatGPT"):
        bridge.request_goal_plan("demo", goal["id"], spawn=False)
    after = bridge.get_goal("demo", goal["id"])
    assert after["state"] == "DRAFT" and after["runs"] == [] and after["plan"] is None


def test_a_missing_cli_is_reported_before_any_run_exists(bridge, human, monkeypatch):
    bridge.configure_role("demo", "SA", "codex")
    monkeypatch.setattr(
        "ccc_mcp.core.goals.native_executable",
        lambda name: (_ for _ in ()).throw(BridgeError("Native codex executable unavailable")))
    bridge.authorize_project_local("demo")
    goal = bridge.create_goal("demo", REQUIREMENT)
    with pytest.raises(BridgeError, match="provider is unavailable"):
        bridge.request_goal_plan("demo", goal["id"], spawn=False)
    assert bridge.get_goal("demo", goal["id"])["runs"] == []


def test_real_agents_stay_disabled_for_an_unauthorized_project(bridge, human, monkeypatch):
    bridge.configure_role("demo", "SA", "codex")
    monkeypatch.delenv("CCC_MCP_ENABLE_REAL_AGENTS", raising=False)
    goal = bridge.create_goal("demo", REQUIREMENT)
    with pytest.raises(PermissionDenied, match="Real agents disabled"):
        bridge.request_goal_plan("demo", goal["id"], spawn=False)


def test_a_provider_failure_is_reported_as_a_failure(goals):
    goal = goals.create_goal("demo", REQUIREMENT)
    after = plan(goals, goal["id"], "error")
    assert after["state"] == "DRAFT" and after["plan"] is None
    assert after["last_run"]["status"] == "FAILED" and "Simulated" in after["last_run"]["error"]


# Fake native CLIs through the real Codex and Claude adapters ------------------------------------

FAKE_CLI = '''#!{python}
import json, os, sys
from ccc_mcp.adapters.agents.fake import scripted_goal_plan
argv = sys.argv[1:]
if "--version" in argv:
    print("fake-cli 1.2.3"); sys.exit(0)
if "--help" in argv:
    print(" ".join(["--print --output-format --json-schema --tools --permission-mode",
        "--setting-sources --strict-mcp-config --mcp-config --no-session-persistence --safe-mode",
        "--sandbox --json --output-schema --output-last-message --ignore-user-config",
        "--skip-git-repo-check --disable-slash-commands --allowedTools"]))
    sys.exit(0)
open(os.environ["FAKE_CLI_ARGV"], "w").write(json.dumps(argv))
sys.stdin.read()
report = scripted_goal_plan(os.environ.get("FAKE_CLI_SCENARIO", "phases:3"),
    {{"content": {{"workflows": [{{"id": "standard"}}], "goal": {{"title": "slug"}}}}}})
if "--output-last-message" in argv:
    open(argv[argv.index("--output-last-message") + 1], "w").write(json.dumps(report))
else:
    print(json.dumps({{"structured_output": report, "is_error": False}}))
'''


posix_script = pytest.mark.skipif(
    os.name == "nt", reason="the fake claude/codex executables are POSIX scripts")


@posix_script
@pytest.mark.parametrize("provider", ["codex", "claude"])
def test_the_real_adapters_run_the_delivery_pm_read_only_and_store_a_valid_plan(
    bridge, human, monkeypatch, tmp_path, provider
):
    folder = tmp_path / "bin"
    folder.mkdir()
    script = folder / provider
    script.write_text(FAKE_CLI.format(python=sys.executable), encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    argv_file = tmp_path / "argv.json"
    monkeypatch.setenv("PATH", f"{folder}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_CLI_ARGV", str(argv_file))
    monkeypatch.setenv("CCC_MCP_ENABLE_REAL_AGENTS", "1")
    bridge.spawn_planning_worker = lambda run_id: None
    goal = bridge.create_goal("demo", REQUIREMENT)

    run = bridge.request_goal_plan("demo", goal["id"], provider=provider, spawn=False)
    bridge.execute_planning_run(run["id"])

    after = bridge.get_goal("demo", goal["id"])
    assert after["state"] == "AWAITING_APPROVAL", after["last_run"]
    assert len(after["plan"]["plan"]["phases"]) == 3
    assert after["plan"]["author"]["provider"] == provider
    assert after["plan"]["author"]["cli_version"] == "fake-cli 1.2.3"
    argv = json.loads(argv_file.read_text())
    if provider == "codex":
        assert argv[argv.index("--sandbox") + 1] == "read-only"
    else:
        assert argv[argv.index("--permission-mode") + 1] == "plan"
        assert argv[argv.index("--tools") + 1] == "Read,Glob,Grep"
    stored = bridge.store.one("SELECT * FROM goal_plan_runs WHERE id=?", (run["id"],))
    assert stored["prompt_sha256"] and json.loads(stored["invocation"])["source"] == "native_cli"


@posix_script
def test_an_invalid_answer_from_a_real_adapter_is_refused(bridge, human, monkeypatch, tmp_path):
    folder = tmp_path / "bin"
    folder.mkdir()
    script = folder / "codex"
    script.write_text(FAKE_CLI.format(python=sys.executable), encoding="utf-8")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setenv("PATH", f"{folder}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_CLI_ARGV", str(tmp_path / "argv.json"))
    monkeypatch.setenv("FAKE_CLI_SCENARIO", "cyclic")
    monkeypatch.setenv("CCC_MCP_ENABLE_REAL_AGENTS", "1")
    goal = bridge.create_goal("demo", REQUIREMENT)
    run = bridge.request_goal_plan("demo", goal["id"], provider="codex", spawn=False)
    bridge.execute_planning_run(run["id"])
    after = bridge.get_goal("demo", goal["id"])
    assert after["plan"] is None and after["last_run"]["failure_kind"] == "INVALID_OUTPUT"


# Only a person approves ---------------------------------------------------------------------------


def test_project_write_authorization_does_not_approve_a_plan(bridge, monkeypatch):
    bridge.planning_adapter_default = "fake"
    bridge.spawn_planning_worker = lambda run_id: None
    root = bridge.project("demo")["root"]
    with bridge.store.transaction() as conn:
        conn.execute(
            "INSERT INTO project_access_events(id,project_id,root,access,identity,created) "
            "VALUES('AUTH-test','demo',?,'read-write','tester',?)", (root, now()))
    assert bridge.get_project_access("demo")["mode"] == "read-write"
    goal = plan(bridge, bridge.create_goal("demo", REQUIREMENT)["id"])

    assert goal["state"] == "AWAITING_APPROVAL" and goal["approval"] is None
    assert bridge.store.one("SELECT COUNT(*) AS n FROM goal_approvals")["n"] == 0
    with pytest.raises(PermissionDenied, match="interactive"):
        bridge.approve_goal_local("demo", goal["id"], 1, goal["plan"]["sha256"])
    assert bridge.store.one("SELECT COUNT(*) AS n FROM goal_approvals")["n"] == 0

    monkeypatch.setattr("ccc_mcp.core.approval.confirm_local", lambda *a: "operator")
    approve(bridge, goal)
    row = bridge.store.one("SELECT project_access FROM goal_approvals")
    assert json.loads(row["project_access"])["authorization_id"] == "AUTH-test", \
        "the approval records which authorization it ran under, it is not derived from it"


def test_the_web_approval_needs_the_exact_typed_phrase(goals):
    goal = plan(goals, goals.create_goal("demo", REQUIREMENT)["id"])
    challenge = approval_challenge(goal["id"], 1)
    assert challenge == f"APPROVE {goal['id']} v1"
    for wrong in ("", "approve", f"APPROVE {goal['id']} v2", "yes"):
        with pytest.raises(PermissionDenied, match="cancelled"):
            goals.approve_goal_web("demo", goal["id"], 1, goal["plan"]["sha256"], wrong)
    assert goals.store.one("SELECT COUNT(*) AS n FROM goal_approvals")["n"] == 0
    assert goals.approve_goal_web(
        "demo", goal["id"], 1, goal["plan"]["sha256"], challenge)["status"] == "APPROVED"


# Importing a plan written elsewhere ----------------------------------------------------------------


def test_an_imported_plan_is_labelled_manual_validated_and_still_needs_approval(goals):
    goal = goals.create_goal("demo", REQUIREMENT)
    result = goals.import_goal_plan("demo", goal["id"], json.dumps(good_plan(3)), 0, "from chat")
    after = result["goal"]
    assert after["state"] == "AWAITING_APPROVAL"
    assert after["plan"]["source"] == "manual" and after["plan"]["author"]["kind"] == "manual"
    assert after["plan"]["message"] == "from chat"
    with pytest.raises(BridgeError, match="not valid JSON"):
        goals.import_goal_plan("demo", goal["id"], "{nope", 1)
    with pytest.raises(GoalPlanInvalid):
        goals.import_goal_plan("demo", goal["id"], {**good_plan(2), "phases": []}, 1)
    with pytest.raises(VersionConflict):
        goals.import_goal_plan("demo", goal["id"], good_plan(2), 0)


# The Delivery PM is only ever started on purpose, and is bounded ----------------------------------


def test_creating_and_reading_never_call_a_model(goals):
    goal = goals.create_goal("demo", REQUIREMENT)
    goals.get_goal("demo", goal["id"])
    goals.list_goals("demo")
    assert goals.store.one("SELECT COUNT(*) AS n FROM goal_plan_runs")["n"] == 0


def test_a_goal_can_only_use_a_bounded_number_of_planning_runs(goals):
    goal = goals.create_goal("demo", REQUIREMENT)
    for _ in range(MAX_PLANNING_RUNS):
        plan(goals, goal["id"], "error")
    with pytest.raises(BridgeError, match=f"used its {MAX_PLANNING_RUNS} planning runs"):
        goals.request_goal_plan("demo", goal["id"], spawn=False)
    assert goals.get_goal("demo", goal["id"])["limits"]["planning_runs_used"] == MAX_PLANNING_RUNS


def test_a_failed_run_is_not_retried_by_itself(goals):
    goal = goals.create_goal("demo", REQUIREMENT)
    plan(goals, goal["id"], "error")
    assert goals.store.one("SELECT COUNT(*) AS n FROM goal_plan_runs")["n"] == 1
    goals.recover_planning_runs("demo")
    goals.get_goal("demo", goal["id"])
    assert goals.store.one("SELECT COUNT(*) AS n FROM goal_plan_runs")["n"] == 1


@pytest.mark.parametrize("timeout", [0, 4, 901, True, "300"])
def test_the_planning_timeout_is_bounded(goals, timeout):
    goal = goals.create_goal("demo", REQUIREMENT)
    with pytest.raises(BridgeError, match="timeout"):
        goals.request_goal_plan("demo", goal["id"], timeout=timeout, spawn=False)


def test_a_queued_run_can_be_stopped_and_a_cancelled_run_never_plans(goals):
    goal = goals.create_goal("demo", REQUIREMENT)
    run = goals.request_goal_plan("demo", goal["id"], spawn=False)
    assert goals.cancel_planning_run("demo", goal["id"])["status"] == "CANCELLED"
    goals.execute_planning_run(run["id"])
    after = goals.get_goal("demo", goal["id"])
    assert after["state"] == "DRAFT" and after["plan"] is None
    with pytest.raises(BridgeError, match="No planning run"):
        goals.cancel_planning_run("demo", goal["id"])


def test_a_dead_planning_worker_is_closed_and_a_live_one_is_left_alone(goals):
    import portalocker

    goal = goals.create_goal("demo", REQUIREMENT)
    run = goals.request_goal_plan("demo", goal["id"], spawn=False)
    stale = "2026-01-01T00:00:00+00:00"
    with goals.store.transaction() as conn:
        conn.execute("UPDATE goal_plan_runs SET status='RUNNING',started=?,spawned_at=? "
                     "WHERE id=?", (stale, stale, run["id"]))
    lock = goals.home / "locks" / f"goalrun-{run['id']}.lock"
    lock.parent.mkdir(exist_ok=True)
    with portalocker.Lock(lock, timeout=0):  # a live worker holds its lock
        assert goals.recover_planning_runs("demo") == []
        assert goals.get_goal("demo", goal["id"])["state"] == "PLANNING"
    assert goals.recover_planning_runs("demo") == [run["id"]]
    after = goals.get_goal("demo", goal["id"])
    assert after["state"] == "DRAFT" and after["last_run"]["failure_kind"] == "INTERRUPTED"
    assert goals.recover_planning_runs("demo") == []


def test_a_just_spawned_run_is_not_mistaken_for_a_dead_one(goals):
    goal = goals.create_goal("demo", REQUIREMENT)
    run = goals.request_goal_plan("demo", goal["id"], spawn=False)
    goals.store.execute("UPDATE goal_plan_runs SET spawned_at=? WHERE id=?", (now(), run["id"]))
    assert goals.recover_planning_runs("demo") == []
    assert goals.get_goal("demo", goal["id"])["state"] == "PLANNING"


def test_a_worker_that_cannot_start_fails_the_run_instead_of_hanging(bridge, human):
    bridge.planning_adapter_default = "fake"
    goal = bridge.create_goal("demo", REQUIREMENT)

    def broken(run_id):
        raise OSError("no process")

    bridge.spawn_planning_worker = broken
    with pytest.raises(BridgeError, match="Unable to start"):
        bridge.request_goal_plan("demo", goal["id"])
    after = bridge.get_goal("demo", goal["id"])
    assert after["state"] == "DRAFT" and after["last_run"]["failure_kind"] == "SPAWN"


def test_the_worker_entry_point_plans_in_its_own_process(goals):
    goal = goals.create_goal("demo", REQUIREMENT)
    run = goals.request_goal_plan("demo", goal["id"], spawn=False, fake_scenario="phases:3")
    finished = subprocess.run(
        [sys.executable, "-m", "ccc_mcp.goal_worker", "--home", str(goals.home), "--run", run["id"]],
        capture_output=True, timeout=120, check=False)
    assert finished.returncode == 0, finished.stderr.decode()
    after = goals.get_goal("demo", goal["id"])
    assert after["state"] == "AWAITING_APPROVAL" and len(after["plan"]["plan"]["phases"]) == 3


# Cancelling -------------------------------------------------------------------------------------------


def test_a_cancelled_goal_stays_cancelled_and_accepts_nothing(goals):
    goal = plan(goals, goals.create_goal("demo", REQUIREMENT)["id"])
    run = goals.request_goal_plan("demo", goal["id"], spawn=False)
    cancelled = goals.cancel_goal("demo", goal["id"], "changed our minds")
    assert cancelled["state"] == "CANCELLED" and cancelled["cancel_reason"] == "changed our minds"
    assert goals.get_goal("demo", goal["id"])["runs"][0]["status"] == "CANCELLED"
    goals.execute_planning_run(run["id"])
    assert goals.cancel_goal("demo", goal["id"])["state"] == "CANCELLED"
    for call in (
        lambda: goals.request_goal_plan("demo", goal["id"], spawn=False),
        lambda: approve(goals, goal),
        lambda: goals.import_goal_plan("demo", goal["id"], good_plan(2), 1),
    ):
        with pytest.raises(BridgeError, match="cancelled"):
            call()
    assert [e["kind"] for e in goals.get_goal("demo", goal["id"])["events"]].count(
        "goal_cancelled") == 1


# Rules the database itself enforces -------------------------------------------------------------------


def test_the_history_cannot_be_rewritten_even_by_a_direct_sql_write(goals):
    goal = plan(goals, goals.create_goal("demo", REQUIREMENT)["id"])
    approve(goals, goal)
    run_id = goal["last_run"]["id"]
    statements = [
        "UPDATE goals SET requirement='changed'",
        "UPDATE goals SET title='changed'",
        "UPDATE goals SET current_version=0",
        "DELETE FROM goals",
        "UPDATE goal_plan_versions SET content='{}'",
        "DELETE FROM goal_plan_versions",
        "UPDATE goal_plan_phases SET title='x'",
        "DELETE FROM goal_plan_phases",
        "UPDATE goal_approvals SET plan_version=2",
        "DELETE FROM goal_approvals",
        "UPDATE goal_events SET detail='{}'",
        "DELETE FROM goal_events",
        f"UPDATE goal_plan_runs SET status='QUEUED' WHERE id='{run_id}'",
        f"UPDATE goal_plan_runs SET goal_id='GOAL-9999' WHERE id='{run_id}'",
        "DELETE FROM goal_plan_runs",
    ]
    with sqlite3.connect(goals.store.path) as connection:
        for sql in statements:
            with pytest.raises(sqlite3.DatabaseError, match="immutable|cannot|final|backwards"):
                connection.execute(sql)
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO goal_approvals(id,project_id,goal_id,plan_version,plan_sha256,"
                "identity,project_access,scope,created) VALUES('GAPP-dup','demo',?,1,'x','x','{}',"
                "'{}','x')", (goal["id"],))
    goals.cancel_goal("demo", goal["id"])
    with sqlite3.connect(goals.store.path) as connection:
        with pytest.raises(sqlite3.DatabaseError, match="stays cancelled"):
            connection.execute("UPDATE goals SET cancelled=NULL")


def test_a_goals_state_is_derived_so_it_cannot_contradict_the_rows(goals):
    columns = {row[1] for row in sqlite3.connect(goals.store.path).execute(
        "PRAGMA table_info(goals)")}
    assert "state" not in columns and "status" not in columns


# Migration and compatibility ---------------------------------------------------------------------------

GOAL_TABLES = {"goals", "goal_plan_versions", "goal_plan_phases", "goal_approvals", "goal_events",
               "goal_plan_runs"}


def legacy_service(tmp_path):
    home = tmp_path / "legacy-home"
    home.mkdir()
    with sqlite3.connect(home / "bridge.sqlite3") as connection:
        connection.executescript(LEGACY_SQL.read_text(encoding="utf-8"))
    repo = tmp_path / "legacy-repo"
    repo.mkdir()
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    for args in (["init", "-q"], ["add", "."],
                 ["-c", "user.name=T", "-c", "user.email=t@example.invalid", "commit", "-qm", "i"]):
        subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)
    service = CCCMCP(home)
    service.register_project(repo, "demo", "legacy database project")
    return service


def test_an_old_database_gains_only_the_goal_tables(tmp_path):
    home = tmp_path / "legacy-home"
    home.mkdir()
    with sqlite3.connect(home / "bridge.sqlite3") as connection:
        connection.executescript(LEGACY_SQL.read_text(encoding="utf-8"))
        before = {row[0]: [c[1] for c in connection.execute(f"PRAGMA table_info({row[0]})")]
                  for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    CCCMCP(home)
    CCCMCP(home)  # reopening is idempotent
    with sqlite3.connect(home / "bridge.sqlite3") as connection:
        names = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")}
        assert GOAL_TABLES <= names
        for table, old in before.items():
            columns = [c[1] for c in connection.execute(f"PRAGMA table_info({table})")]
            from ccc_mcp.adapters.storage import ADDED_COLUMNS

            assert columns[:len(old)] == old, f"{table} keeps its columns, in order"
            # The only columns an old table may gain are the ones that predate goals.
            assert set(columns) - set(old) <= set(ADDED_COLUMNS.get(table, ())), table


def test_goals_work_on_a_database_written_by_the_old_version(tmp_path, human):
    service = legacy_service(tmp_path)
    service.planning_adapter_default = "fake"
    service.spawn_planning_worker = lambda run_id: None
    task = service.create_task("demo", "A task that predates goals")
    goal = plan(service, service.create_goal("demo", REQUIREMENT)["id"], "phases:3")
    approve(service, goal)
    assert service.get_goal("demo", goal["id"])["state"] == "READY"
    assert [t["id"] for t in service.list_tasks("demo")] == [task["id"]]
    assert CCCMCP(service.home).get_goal("demo", goal["id"])["plan"]["sha256"] == goal["plan"]["sha256"]


def test_the_goal_schema_statements_are_safe_to_run_twice():
    source = (Path(__file__).parents[1] / "src/ccc_mcp/adapters/storage.py").read_text("utf-8")
    block = source[source.index("-- Goals (core/goals.py"):source.index('"""\n\n# Like the role tables')]
    import re

    statements = re.findall(r"CREATE (?:TABLE|VIEW|TRIGGER|UNIQUE INDEX|INDEX)[^;]*;", block, re.S)
    assert len(statements) >= 20 and all("IF NOT EXISTS" in s for s in statements)


def test_removing_a_project_removes_its_goals_and_keeps_other_projects_goals(goals, tmp_path):
    other = tmp_path / "keep"
    other.mkdir()
    goals.register_project(other, "keep", "kept")
    plan(goals, goals.create_goal("demo", REQUIREMENT)["id"])
    goals.create_goal("keep", "Stays")

    goals.remove_project_web("demo", "REMOVE demo")

    assert goals.store.one("SELECT COUNT(*) AS n FROM goals WHERE project_id='demo'")["n"] == 0
    for table in GOAL_TABLES:
        assert goals.store.one(
            f"SELECT COUNT(*) AS n FROM {table} WHERE project_id='demo'")["n"] == 0
    assert [g["id"] for g in goals.list_goals("keep")] == ["GOAL-0001"]
    with sqlite3.connect(goals.store.path) as connection:
        with pytest.raises(sqlite3.DatabaseError, match="cannot be deleted"):
            connection.execute("DELETE FROM goals")


def test_clearing_task_history_leaves_goals_alone(goals):
    goal = plan(goals, goals.create_goal("demo", REQUIREMENT)["id"])
    approve(goals, goal)
    goals.clear_project_history_web("demo", "CLEAR HISTORY demo")
    after = goals.get_goal("demo", goal["id"])
    assert after["state"] == "READY" and after["plan"]["sha256"] == goal["plan"]["sha256"]


def test_a_project_bundle_refuses_goals_instead_of_silently_dropping_them(goals):
    goals.create_goal("demo", REQUIREMENT)
    with pytest.raises(BridgeError, match="does not yet support table goals"):
        goals.export_project_bundle("demo")


def test_the_stage_zero_host_test_guarantees_still_hold_beside_goals(goals):
    """Goal rows and host-test rows live side by side and neither blocks the other."""
    goal = plan(goals, goals.create_goal("demo", REQUIREMENT)["id"])
    task = goals.create_task("demo", "A task")["id"]
    with goals.host_test_session("demo", task, enforce_runnable=False) as outcome:
        outcome[0] = "passed"
    assert goals.get_goal("demo", goal["id"])["state"] == "AWAITING_APPROVAL"
    assert goals.store.one("SELECT COUNT(*) AS n FROM host_test_runs")["n"] == 1
