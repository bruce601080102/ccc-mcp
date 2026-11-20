"""Stage C acceptance: one approved goal, several dependent phases, run to a verified end by the goal
supervisor, with a simulated CLI that really edits files.

EVIDENCE LEVEL (say it before reading the results)
  * simulated model     - the "Codex" and "Claude" here are a script (see phase_support.py).
  * real code paths     - the real Codex and Claude adapters, the real worker function
                          (`run_execution`), the existing Task/Workflow engine and Review Gate, the
                          Stage B phase runner and the Stage C goal supervisor, real worker processes
                          where a test says so.
  * real host test      - `python -m pytest` runs in a real git project, for real.
  * real model          - NOT exercised. No provider, no quota, no network is used.
"""

import json
import subprocess
import sys

import pytest
from phase_support import (
    BUGGY,
    FIXED,
    PHASES,
    cli_calls,
    drive_goal,
    install_fake_cli,
    make_ready_goal,
    make_slug_project,
)

PYTEST = [sys.executable, "-m", "pytest", "-q"]
FIX = {"file": "app.py", "old": 'return text.lower().replace(" ", "-")',
       "new": 'return "-".join(text.lower().split())'}


@pytest.fixture
def goal_env(bridge, human, tmp_path, monkeypatch):
    bridge.phase_adapter_default = "real"
    bridge.goal_adapter_default = "real"
    bridge.spawn_phase_worker = lambda run_id: None
    bridge.spawn_goal_worker = lambda run_id: None
    root = make_slug_project(bridge, tmp_path)
    return bridge, root


def test_one_goal_two_phases_fail_repair_pass_verify_and_pass(goal_env, tmp_path, monkeypatch):
    bridge, root = goal_env
    _, log = install_fake_cli(tmp_path, monkeypatch, phases=PHASES, pm_fix=FIX)
    goal = make_ready_goal(bridge, PYTEST, phases=2)
    assert goal["state"] == "READY"

    started = bridge.start_goal_run("demo", goal["id"])
    assert started["status"] == "RUNNING" and started["already_started"] is False
    done = drive_goal(bridge, started["id"])

    assert done["status"] == "PASSED", (done["reason"], done["detail"])
    assert [p["state"] for p in done["phases"]] == ["PASSED", "PASSED"]
    p1, p2 = done["phases"]
    assert p1["task_id"] == "GOAL-0001-P1" and p2["task_id"] == "GOAL-0001-P2"
    assert p1["attempts"]["used"] == 2 and p2["attempts"]["used"] == 1       # P1 failed once, was repaired
    assert [r["phase_id"] for r in done["ledger"]] == ["P1", "P2"]
    assert bridge.get_goal("demo", goal["id"])["state"] == "PASSED"
    (verification,) = done["verifications"]
    assert verification["status"] == "PASSED"
    assert verification["evidence"]["host_test"]["passed"] is True
    assert "2 passed" in verification["evidence"]["host_test"]["stdout"] or \
        "3 passed" in verification["evidence"]["host_test"]["stdout"]
    # nothing was committed or pushed on anyone's behalf
    log_out = subprocess.run(["git", "-C", str(root), "log", "--oneline"], capture_output=True,
                             text=True, check=True).stdout.splitlines()
    assert len(log_out) == 2
    calls = cli_calls(log)
    roles = [(c["phase"], c["role"], c["schema"]) for c in calls]
    assert ("P1", "SA", "goal_repair") in roles and roles.count(("P2", "RD", "agent_report")) == 1

    # 9, 10: phase 2 was created by the supervisor, nobody relayed anything, and it was handed what
    # phase 1 delivered and how it got there
    assert bridge.store.one("SELECT goal_run_id FROM goal_phase_runs WHERE phase_id='P2'")[
        "goal_run_id"] == started["id"]
    second = [c for c in calls if c["phase"] == "P2"]
    assert second and all("Context from phases that already passed" in json.dumps(c) for c in second)
    assert all("GOAL-0001-P1" in json.dumps(c) for c in second)
    assert "Attempt 1 failed" in bridge.store.one(
        "SELECT handoff FROM goal_phase_runs WHERE phase_id='P2'")["handoff"]

    # 14: the evidence of every phase is in the ledger: who did what, with which prompt, on which tree
    for record in done["ledger"]:
        evidence = record["evidence"]
        assert evidence["git"]["head"] and evidence["git"]["tree_digest"]
        assert evidence["test"]["executed_by_host"] is True and evidence["test"]["passed"] is True
        assert all(e["instruction_sha256"] and e["provider"] in ("codex", "claude")
                   for e in evidence["executions"])
        assert {e["role"] for e in evidence["executions"]} >= {"SA", "SD", "RD", "REVIEW"}
    # what the budget says it spent is what the scripted CLI was really started for
    assert done["budget"]["dispatches"]["used"] == len(calls)
    assert done["budget"]["dispatches"]["analyses"] == 1


def run_goal(bridge, goal, **kwargs):
    started = bridge.start_goal_run("demo", goal["id"], **kwargs)
    return started, drive_goal(bridge, started["id"])


def test_the_final_verification_finds_a_regression_and_one_integration_fix_repairs_it(
    goal_env, tmp_path, monkeypatch
):
    bridge, root = goal_env
    phases = {**PHASES, "INT-1": {"initial": [{"file": "app.py", "content": FIXED}]}}
    _, log = install_fake_cli(tmp_path, monkeypatch, phases=phases, pm_fix=FIX)
    goal = make_ready_goal(bridge, PYTEST, phases=2)
    original = bridge._run_verification
    state = {"done": False}

    def phase_two_breaks_phase_one(run, plan):
        # After every phase passed on its own, something changes slugify() under it (this is the
        # shape of a cross-phase regression: each phase's own run was green, the whole is not).
        if not state["done"]:
            state["done"] = True
            (root / "app.py").write_text(BUGGY, encoding="utf-8")
        return original(run, plan)

    monkeypatch.setattr(bridge, "_run_verification", phase_two_breaks_phase_one)
    started, done = run_goal(bridge, goal)

    assert done["status"] == "PASSED", (done["reason"], done["detail"])
    first, second = done["verifications"]
    assert first["status"] == "FAILED" and second["status"] == "PASSED"
    assert first["evidence"]["host_test"]["passed"] is False
    assert "FAILED test_app.py::test_extra_spaces" in first["evidence"]["host_test"]["stdout"]
    assert {p["code"] for p in first["evidence"]["problems"]} >= {"TREE_CHANGED"}
    assert second["evidence"]["problems"] == []
    # a traceable integration fix, not a rewrite of history
    kinds = [(r["phase_id"], r["kind"]) for r in done["ledger"]]
    assert kinds == [("P1", "PLAN"), ("P2", "PLAN"), ("INT-1", "INTEGRATION")]
    assert [p["state"] for p in done["phases"]] == ["PASSED", "PASSED", "PASSED"]
    assert done["phases"][2]["task_id"] == "GOAL-0001-INT-1" and done["phases"][2]["kind"] == "INTEGRATION"
    assert any(e["kind"] == "goal_integration_requested" for e in done["events"])
    integration = [c for c in cli_calls(log) if c["phase"] == "INT-1"]
    assert integration and all("Integration fix" in json.dumps(c) for c in integration)
    assert all("FAILED test_app.py::test_extra_spaces" in json.dumps(c) for c in integration[:1])
    assert (root / "app.py").read_text(encoding="utf-8") == FIXED
    # the approved plan and its criteria were not touched, and no phase was added to it
    current = bridge.get_goal("demo", goal["id"])
    assert current["current_version"] == 1 and len(current["plan"]["plan"]["phases"]) == 2
    assert done["budget"]["dispatches"]["used"] == len(cli_calls(log))


def test_an_integration_fix_that_cannot_fix_it_stops_the_goal_and_changes_no_finished_phase(
    goal_env, tmp_path, monkeypatch
):
    bridge, root = goal_env
    wrong = [{"file": "app.py", "content": f'def slugify(text):\n    return text + "{n}"\n'}
             for n in range(3)]
    phases = {**PHASES, "INT-1": {"variants": wrong}}
    install_fake_cli(tmp_path, monkeypatch, phases=phases, pm_fix=FIX)
    goal = make_ready_goal(bridge, PYTEST, phases=2)
    original = bridge._run_verification
    state = {"done": False}

    def break_it(run, plan):
        if not state["done"]:
            state["done"] = True
            (root / "app.py").write_text(BUGGY, encoding="utf-8")
        return original(run, plan)

    monkeypatch.setattr(bridge, "_run_verification", break_it)
    _, done = run_goal(bridge, goal)
    assert done["status"] == "EXHAUSTED" and done["reason"] == "ATTEMPTS_EXHAUSTED", done
    assert done["phases"][2]["state"] == "EXHAUSTED"
    assert [v["status"] for v in done["verifications"]] == ["FAILED"]
    assert bridge.get_goal("demo", goal["id"])["state"] == "EXHAUSTED"
    # P1 and P2 stay as they were recorded
    assert [p["state"] for p in done["phases"][:2]] == ["PASSED", "PASSED"]
    for task in ("GOAL-0001-P1", "GOAL-0001-P2"):
        assert bridge.task("demo", task)["state"] == "COMPLETED"
    assert [r["phase_id"] for r in done["ledger"]] == ["P1", "P2"]


def test_a_second_phase_that_fails_three_times_stops_the_goal_and_the_fourth_try_never_happens(
    goal_env, tmp_path, monkeypatch
):
    bridge, _ = goal_env
    bad = [{"file": "cli.py", "content": f'from app import slugify\n\n\ndef title_url(title):\n'
                                         f'    return "/p{n}/" + slugify(title)\n'} for n in range(4)]
    phases = {"P1": {"initial": [{"file": "app.py", "content": FIXED}]},
              "P2": {"variants": bad,
                     "also_write": [{"file": "test_cli.py", "content": PHASES["P2"]["initial"][1]["content"]}]}}
    _, log = install_fake_cli(tmp_path, monkeypatch, phases=phases, pm_fix=FIX)
    goal = make_ready_goal(bridge, PYTEST, phases=2)
    _, done = run_goal(bridge, goal)

    assert done["status"] == "EXHAUSTED" and done["reason"] == "ATTEMPTS_EXHAUSTED", done
    assert [p["state"] for p in done["phases"]] == ["PASSED", "EXHAUSTED"]
    developer = [c for c in cli_calls(log) if c["phase"] == "P2" and c["role"] == "RD"]
    assert len(developer) == 3                                  # the fourth never ran
    assert done["verifications"] == []                          # no final verification of a failed goal
    assert [r["phase_id"] for r in done["ledger"]] == ["P1"]
    n = len(cli_calls(log))
    drive_goal(bridge, done["id"])
    assert len(cli_calls(log)) == n


def test_the_budget_is_counted_over_the_whole_goal_with_the_real_adapters(goal_env, tmp_path, monkeypatch):
    bridge, _ = goal_env
    _, log = install_fake_cli(tmp_path, monkeypatch, phases=PHASES, pm_fix=FIX)
    goal = make_ready_goal(bridge, PYTEST, phases=2, budget={"max_agent_dispatches": 9})
    _, done = run_goal(bridge, goal)
    # P1 used five (SA, SD, RD, RD again, REVIEW) and the Delivery PM one: six. P2 needs four, has three.
    assert done["status"] == "EXHAUSTED" and done["reason"] == "DISPATCH_BUDGET", done
    assert done["budget"]["dispatches"]["used"] == 9 == len(cli_calls(log))
    assert [p["state"] for p in done["phases"]] == ["PASSED", "EXHAUSTED"]
