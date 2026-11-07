"""An everyday team played by ChatGPT hands over from one step to the next on the saved
report alone, so the PM round that only restated the summary no longer sits between roles."""

import pytest

from ccc_mcp.core.models import AgentReport

LIFE = ["PA", "RS", "AN", "PL", "VC"]


def report(**kwargs):
    return AgentReport(
        summary="Step done", next_action="Hand over", document="# Notes\nfacts", **kwargs
    ).model_dump()


@pytest.fixture
def life_task(bridge, human):
    bridge.authorize_project_local("demo")
    bridge.configure_workflow("demo", "life")
    started = bridge.start_project_request("demo", "Plan a trip to Osaka")
    return started["workstream"]["id"], started


def active(bridge, task_id):
    return bridge.store.one(
        "SELECT id,role,adapter FROM agent_executions WHERE project_id='demo' AND task_id=? "
        "AND status IN ('QUEUED','RUNNING')",
        (task_id,),
    )


def roles(bridge, task_id):
    return [
        r["role"]
        for r in bridge.store.all(
            "SELECT role FROM agent_executions WHERE project_id='demo' AND task_id=? ORDER BY created",
            (task_id,),
        )
    ]


def test_every_step_hands_over_without_a_pm_round(bridge, life_task):
    task_id, _ = life_task
    outcomes = []
    while (item := active(bridge, task_id)):
        assert item["adapter"] == "interactive"
        outcomes.append(bridge.submit_agent_report("demo", item["id"], report()))

    assert roles(bridge, task_id) == LIFE
    assert all(outcome["auto_advanced"] for outcome in outcomes)
    assert bridge.task("demo", task_id)["state"] == "COMPLETED"


def test_the_next_step_is_already_waiting_for_chatgpt(bridge, life_task):
    task_id, _ = life_task
    first = active(bridge, task_id)
    bridge.submit_agent_report("demo", first["id"], report())

    handoff = bridge.wait_for_task("demo", task_id, 0)
    assert handoff["handoff"]["waiting_on"] == "CHATGPT_HANDOFF_PENDING"
    assert handoff["must_continue_in_this_turn"] is True
    assert handoff["action_required"] is not None
    assert active(bridge, task_id)["role"] == "RS"


def test_a_gate_that_asks_for_revision_still_sends_work_back(bridge, life_task):
    task_id, _ = life_task
    for expected in LIFE[:-1]:
        item = active(bridge, task_id)
        assert item["role"] == expected
        bridge.submit_agent_report("demo", item["id"], report())

    gate = active(bridge, task_id)
    assert gate["role"] == "VC"
    outcome = bridge.submit_agent_report("demo", gate["id"], report(needs_revision=True))

    assert outcome["auto_advanced"] is True
    assert active(bridge, task_id)["role"] == "PL"


def test_a_paused_task_waits_for_the_user(bridge, life_task):
    task_id, _ = life_task
    first = active(bridge, task_id)
    bridge.pause_task("demo", task_id)
    outcome = bridge.submit_agent_report("demo", first["id"], report())

    assert outcome["auto_advanced"] is False
    assert active(bridge, task_id) is None


def test_software_steps_still_wait_for_the_pm(bridge, human):
    bridge.configure_profile("demo", "chatgpt-only")
    bridge.authorize_project_local("demo")
    started = bridge.start_project_request("demo", "Implement a change")
    outcome = bridge.submit_agent_report(
        "demo", started["sa_execution"]["execution_id"], report()
    )
    assert outcome["auto_advanced"] is False


def test_a_specialist_reviewer_in_the_flow_does_not_stop_the_hand_over(bridge, human):
    flow = ["PA", "WR", "ED", "VC", "AR", "ED"]
    bridge.authorize_project_local("demo")
    bridge.configure_role("demo", "AR", "chatgpt")
    saved = bridge.save_workflow("demo", flow)
    bridge.configure_workflow("demo", saved["id"])
    task_id = bridge.start_project_request("demo", "Write a toast")["workstream"]["id"]

    outcomes = []
    while (item := active(bridge, task_id)):
        outcomes.append(bridge.submit_agent_report("demo", item["id"], report()))

    assert roles(bridge, task_id) == flow
    assert all(outcome["auto_advanced"] for outcome in outcomes)
    assert bridge.task("demo", task_id)["state"] == "COMPLETED"


# -- the boss sends the work back to a chosen step -------------------------------------------


def run_to(bridge, task_id, stop_before):
    """Hand in reports until the next step waiting for ChatGPT is `stop_before`."""
    while (item := active(bridge, task_id)) and item["role"] != stop_before:
        bridge.submit_agent_report("demo", item["id"], report())
    return active(bridge, task_id)


def test_restart_from_an_earlier_step_redoes_it_and_carries_on(bridge, life_task):
    task_id, _ = life_task
    waiting = run_to(bridge, task_id, "PL")

    outcome = bridge.restart_from_step("demo", task_id, 1, "Make the research more careful")

    assert outcome["role"] == "RS" and outcome["step"] == 1
    assert bridge.store.one(
        "SELECT status,error FROM agent_executions WHERE id=?", (waiting["id"],)
    )["status"] == "CANCELLED"
    redo = active(bridge, task_id)
    assert redo["role"] == "RS"
    package = bridge.store.one("SELECT instruction,package FROM agent_executions WHERE id=?", (redo["id"],))
    assert "Make the research more careful" in package["instruction"]
    assert "Make the research more careful" in package["package"]

    while (item := active(bridge, task_id)):
        bridge.submit_agent_report("demo", item["id"], report())
    assert roles(bridge, task_id)[-4:] == ["RS", "AN", "PL", "VC"]
    assert bridge.task("demo", task_id)["state"] == "COMPLETED"


def test_the_role_greets_the_boss_in_the_chat_before_it_works(bridge, life_task):
    task_id, _ = life_task
    run_to(bridge, task_id, "AN")
    bridge.restart_from_step("demo", task_id, 0, "Start over, please")

    events = bridge.task_timeline("demo", task_id)["events"]
    kinds = [e["event_type"] for e in events]
    returned = events[kinds.index("role_returned")]
    assert returned["role"] == "PA" and returned["summary"]
    assert kinds.index("user_message") < kinds.index("role_returned")
    redo_start = next(
        i for i, e in enumerate(events)
        if e["event_type"] == "agent_started" and e["execution_id"] == active(bridge, task_id)["id"]
    )
    assert kinds.index("role_returned") < redo_start


def test_a_finished_task_can_be_reopened_from_any_step(bridge, life_task):
    task_id, _ = life_task
    while (item := active(bridge, task_id)):
        bridge.submit_agent_report("demo", item["id"], report())
    assert bridge.task("demo", task_id)["state"] == "COMPLETED"

    bridge.restart_from_step("demo", task_id, 3, "The plan misses the budget")

    assert bridge.task("demo", task_id)["state"] == "IMPLEMENTING"
    assert active(bridge, task_id)["role"] == "PL"
    while (item := active(bridge, task_id)):
        bridge.submit_agent_report("demo", item["id"], report())
    assert bridge.task("demo", task_id)["state"] == "COMPLETED"


def test_restart_resumes_a_paused_task(bridge, life_task):
    task_id, _ = life_task
    run_to(bridge, task_id, "AN")
    bridge.add_task_message("demo", task_id, "Hold on", pause=True)
    assert bridge.task_control("demo", task_id)["paused"]

    bridge.restart_from_step("demo", task_id, 1, "Redo the research")

    assert not bridge.task_control("demo", task_id)["paused"]
    assert active(bridge, task_id)["role"] == "RS"


def test_restart_rejects_a_bad_step_an_empty_message_and_a_stopped_task(bridge, life_task):
    from ccc_mcp.infra.errors import BridgeError, PermissionDenied

    task_id, _ = life_task
    with pytest.raises(BridgeError):
        bridge.restart_from_step("demo", task_id, 99, "Redo")
    with pytest.raises(BridgeError):
        bridge.restart_from_step("demo", task_id, 0, "   ")
    bridge.cancel_task("demo", task_id)
    with pytest.raises(PermissionDenied):
        bridge.restart_from_step("demo", task_id, 0, "Redo")
