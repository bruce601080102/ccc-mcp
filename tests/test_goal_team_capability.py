"""A Goal phase needs a team with a developer step. The refusal is real, and it comes early.

A phase is accepted on host-run tests of what a developer changed, and a failed step is repaired by
sending the work back to that developer. The everyday teams (life, business, content, decision) write
reports and never change files, so a phase could never be verified on them. These tests pin that the
limit is enforced where the plan is written (not after approving and pressing start), that the console
and the planner are told from the server's own data, and that a team which can run a phase still can.
No model is called.
"""

import pytest
from starlette.testclient import TestClient
from test_goal_planner import good_plan

from ccc_mcp.core.flows import BUILTIN_FLOWS, flow_definition, goal_blocker
from ccc_mcp.core.goal_plan import GoalPlanInvalid
from ccc_mcp.interfaces.web import create_app


def test_blocker_follows_the_steps_not_the_name():
    capable = {name for name in BUILTIN_FLOWS if goal_blocker(flow_definition(name)) is None}
    assert capable == {"classic", "standard", "full"}
    for name in ("life", "business", "content", "decision"):
        assert "has no developer step" in goal_blocker(flow_definition(name))
    custom = {"mine": {"name": "Mine", "steps": [{"role": "SA"}, {"role": "RD"}, {"role": "REVIEW"}]}}
    assert goal_blocker(flow_definition("mine", custom)) is None
    custom = {"mine": {"name": "Mine", "steps": [{"role": "SA"}, {"role": "REVIEW"}]}}
    assert "Mine workflow has no developer step" in goal_blocker(flow_definition("mine", custom))


def test_a_plan_on_the_selected_everyday_team_is_refused_when_it_is_written(bridge, human):
    bridge.configure_workflow("demo", "content")
    goal = bridge.create_goal("demo", "Write the launch post", "Post", None)
    plan = good_plan(1)
    plan["phases"][0]["workflow"] = "default"
    with pytest.raises(GoalPlanInvalid) as refused:
        bridge.import_goal_plan("demo", goal["id"], plan, 0, "from the chat")
    assert "Content creation team workflow has no developer step" in str(refused.value)
    assert bridge.get_goal("demo", goal["id"])["plan"] is None

    plan["phases"][0]["workflow"] = "standard"
    bridge.import_goal_plan("demo", goal["id"], plan, 0, "from the chat")
    assert bridge.get_goal("demo", goal["id"])["plan"]["plan"]["phases"][0]["workflow"] == "standard"


def test_the_planner_is_told_which_teams_can_run_a_phase(bridge, human):
    bridge.planning_adapter_default = "fake"
    goal = bridge.create_goal("demo", "Add a feature", "Feature", None)
    run = bridge.request_goal_plan("demo", goal["id"], fake_scenario="success", spawn=False)
    content, _language = bridge._planning_input(
        "demo", bridge.get_goal("demo", goal["id"]), bridge.store.one(
            "SELECT * FROM goal_plan_runs WHERE id=?", (run["id"],)))
    ready = {flow["id"]: flow["goal_ready"] for flow in content["workflows"]}
    assert ready["standard"] is True and ready["content"] is False
    assert all(flow["steps"] and all(step["provider"] for step in flow["steps"])
               for flow in content["workflows"])


def test_the_console_gets_the_capability_from_the_server(bridge, human):
    client = TestClient(create_app(bridge.home, token="t"), headers={"X-CCC-UI-Token": "t"})
    choices = client.get("/api/projects/demo/settings").json()["workflow_choices"]
    assert {c["id"]: c["goal_ready"] for c in choices}["content"] is False
    assert {c["id"]: c["goal_ready"] for c in choices}["full"] is True
    revision = client.get("/api/projects/demo/settings").json()["team_revision"]
    saved = client.post("/api/projects/demo/workflows", json={
        "name": "只寫報告", "steps": [{"role": "SA"}, {"role": "REVIEW"}], "revision": revision})
    assert saved.status_code == 200, saved.text
    own = client.post("/api/projects/demo/workflows", json={
        "name": "含開發", "steps": [{"role": "SA"}, {"role": "RD"}, {"role": "REVIEW"}],
        "revision": client.get("/api/projects/demo/settings").json()["team_revision"]})
    assert own.status_code == 200, own.text
    by_name = {c["name"]: c["goal_ready"] for c in
               client.get("/api/projects/demo/settings").json()["workflow_choices"]}
    assert by_name["只寫報告"] is False and by_name["含開發"] is True
