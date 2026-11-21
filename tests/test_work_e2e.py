"""A request that starts in ChatGPT, runs to a verified end, and is readable from the same Goal.

EVIDENCE LEVEL: the "Codex" and "Claude" here are a script (tests/phase_support.py). Everything
around them is real: the same service functions the MCP tools and the console call, the real
adapters, worker function, workflow engine, goal supervisor, git and a real pytest host test. No real
model, quota or network is used, so this is evidence about ccc-mcp's own control flow, not about how
often a real model succeeds.
"""

import pytest
from phase_support import (
    PHASES,
    custom_plan,
    drive_goal,
    install_fake_cli,
    make_slug_project,
    plan_dict,
)
from test_goal_run_acceptance import FIX, PYTEST

from ccc_mcp.infra.errors import BridgeError

REQUEST = "Build a slug tool with tests, then test and review it."


@pytest.fixture
def chat(bridge, human, tmp_path, monkeypatch):
    bridge.phase_adapter_default = "real"
    bridge.goal_adapter_default = "real"
    bridge.spawn_phase_worker = lambda run_id: None
    bridge.spawn_goal_worker = lambda run_id: None
    make_slug_project(bridge, tmp_path)
    bridge.authorize_project_local("demo")
    bridge.configure_workflow("demo", "standard")
    bridge.configure_test_command("demo", PYTEST)
    return bridge


def approve_as_the_person(bridge, goal_id):
    plan = bridge.get_goal("demo", goal_id)["plan"]
    bridge.approve_goal_local("demo", goal_id, plan["version"], plan["sha256"])


def test_a_chat_request_runs_to_a_verified_result_and_the_overview_shows_the_group(
    chat, tmp_path, monkeypatch
):
    bridge = chat
    _, log = install_fake_cli(tmp_path, monkeypatch, phases=PHASES, pm_fix=FIX)

    # ChatGPT: a multi-step request becomes a Goal, once, with a plan it wrote.
    started = bridge.start_goal_work(
        "demo", REQUEST, title="Slug tool", request_key="chat-e2e-0001", plan=plan_dict(PYTEST, 2))
    assert started["status"] == "created" and started["state"] == "AWAITING_APPROVAL"
    goal_id = started["id"]
    retry = bridge.start_goal_work(
        "demo", REQUEST, title="Slug tool", request_key="chat-e2e-0001", plan=plan_dict(PYTEST, 2))
    assert retry["status"] == "exists" and retry["id"] == goal_id

    # Nothing runs without the person. The tools can show what to approve, never approve it.
    ask = bridge.goal_approval_request("demo", goal_id)
    assert ask["status"] == "approval_needed" and ask["phrase_to_type"].startswith("APPROVE ")
    with pytest.raises(BridgeError):
        bridge.control_goal("demo", goal_id, "start")
    assert bridge.store.one("SELECT COUNT(*) AS n FROM tasks WHERE id LIKE 'GOAL-%'")["n"] == 0

    approve_as_the_person(bridge, goal_id)
    assert bridge.goal_approval_request("demo", goal_id)["status"] == "already_approved"

    # ChatGPT starts it; ccc-mcp's own supervisor does the rest (here driven in-process).
    run = bridge.control_goal("demo", goal_id, "start")["goal"]
    assert run["state"] == "RUNNING" and run["goal_run_id"]
    again = bridge.control_goal("demo", goal_id, "start")
    assert again["already_started"] is True
    done = drive_goal(bridge, run["goal_run_id"])
    assert done["status"] == "PASSED", (done["reason"], done["detail"])

    # The console and ChatGPT read the same group: finished, two of two steps, both tasks linked.
    overview = bridge.work_overview("demo")
    (group,) = overview["groups"]
    assert group["title"] == "Slug tool" and group["state"] == "PASSED" and group["finished"]
    assert group["progress"] == {"done": 2, "total": 2}
    assert [p["task_id"] for p in group["phases"]] == ["GOAL-0001-P1", "GOAL-0001-P2"]
    assert overview["singles"] == [] and overview["needs_you"] == 0
    view = bridge.goal_view("demo", goal_id)
    assert [e["task_id"] for e in view["evidence_reads"]] == ["GOAL-0001-P1", "GOAL-0001-P2"]
    assert bridge.get_task_result("demo", "GOAL-0001-P1")  # the evidence the step names exists

    # The failure in phase 1 was repaired by the supervisor without anyone relaying a prompt.
    p1 = next(p for p in done["phases"] if p["id"] == "P1")
    assert p1["attempts"]["used"] == 2
    assert log.exists()


def test_a_goal_stops_for_the_person_exactly_where_a_test_cannot_decide(chat, tmp_path, monkeypatch):
    bridge = chat
    install_fake_cli(tmp_path, monkeypatch, phases=PHASES, pm_fix=FIX)
    plan = custom_plan(PYTEST, [("P1", []), ("P2", ["P1"])], final_manual=True)
    goal = bridge.start_goal_work("demo", REQUEST, title="Slug tool", plan=plan)
    approve_as_the_person(bridge, goal["id"])
    started = bridge.control_goal("demo", goal["id"], "start")["goal"]

    done = drive_goal(bridge, started["goal_run_id"])

    assert done["status"] == "WAITING_USER" and done["reason"] == "FINAL_ACCEPTANCE_NEEDED"
    view = bridge.goal_view("demo", goal["id"])
    assert view["state"] == "WAITING_USER" and view["needs_user"] == "FINAL_ACCEPTANCE_NEEDED"
    assert view["acceptance_request"]["challenge"] == f"ACCEPT {goal['id']}"
    assert [c["id"] for c in view["acceptance_request"]["criteria"]] == ["FM"]
    assert bridge.work_overview("demo")["needs_you"] == 1

    # Only the person's acceptance finishes it; no tool accepts on their behalf.
    bridge.accept_goal_local("demo", started["goal_run_id"])
    assert bridge.goal_view("demo", goal["id"])["state"] == "PASSED"


def test_pausing_and_cancelling_work_through_the_same_control(chat, tmp_path, monkeypatch):
    bridge = chat
    install_fake_cli(tmp_path, monkeypatch, phases=PHASES, pm_fix=FIX)
    goal = bridge.start_goal_work("demo", REQUEST, plan=plan_dict(PYTEST, 2))
    approve_as_the_person(bridge, goal["id"])
    bridge.control_goal("demo", goal["id"], "start")

    paused = bridge.control_goal("demo", goal["id"], "pause", reason="lunch")["goal"]
    assert paused["state"] == "PAUSED"
    resumed = bridge.control_goal("demo", goal["id"], "resume")["goal"]
    assert resumed["state"] == "RUNNING"
    cancelled = bridge.control_goal("demo", goal["id"], "cancel", reason="not needed")["goal"]
    assert cancelled["state"] == "CANCELLED" and cancelled["finished"]
