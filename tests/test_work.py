"""One work model for ChatGPT and the console: groups of steps under a Goal, single tasks, and the
guards a chat needs (no duplicate on retry, no guessing the project, no self-approval).

No real model is called. Planning uses the scripted fake planner or a plan written in the "chat".
"""

import json
import os
import subprocess
import sys

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from test_goal_planner import good_plan

from ccc_mcp.core.work import similarity, suggest_title
from ccc_mcp.infra.errors import BridgeError
from ccc_mcp.interfaces.server import create_server
from ccc_mcp.service import CCCMCP

REQUEST = "Build a small Python slug tool, then test it and review it."


@pytest.fixture
def work(bridge, human):
    bridge.planning_adapter_default = "fake"
    bridge.spawn_planning_worker = lambda run_id: None
    return bridge


def test_a_titled_goal_is_created_once_and_grouped_in_the_overview(work):
    first = work.start_goal_work("demo", REQUEST, title="Slug tool", request_key="chat-req-0001")
    again = work.start_goal_work("demo", REQUEST, title="Slug tool", request_key="chat-req-0001")

    assert first["status"] == "created" and first["created_now"] is True
    assert again["status"] == "exists" and again["id"] == first["id"]
    assert work.store.one("SELECT COUNT(*) AS n FROM goals")["n"] == 1
    overview = work.work_overview("demo")
    assert [g["title"] for g in overview["groups"]] == ["Slug tool"]
    assert overview["groups"][0]["state"] == "DRAFT"
    assert overview["singles"] == []


def test_a_retry_without_any_key_does_not_make_a_second_goal(work):
    first = work.start_goal_work("demo", REQUEST)
    retry = work.start_goal_work("demo", f"  {REQUEST.upper()}  ")

    assert retry["id"] == first["id"] and retry["created_now"] is False
    assert len(work.list_goals("demo")) == 1


def test_a_missing_title_is_derived_without_filler_words(work):
    assert suggest_title("please help me build a login page\nmore detail") == "build a login page"
    assert suggest_title("幫我開發會員登入功能") == "開發會員登入功能"
    started = work.start_goal_work("demo", "Please build a CSV export for reports")
    assert started["title"] == "build a CSV export for reports"


def test_a_follow_up_that_looks_like_open_work_is_a_question_not_a_second_goal(work):
    work.start_goal_work("demo", "Build the slug tool with tests", title="Slug tool")

    question = work.start_goal_work("demo", "Add unicode support to the slug tool with tests")

    assert question["status"] == "needs_choice" and question["reason"] == "similar_goal"
    assert question["choices"][0]["title"] == "Slug tool"
    assert len(work.list_goals("demo")) == 1
    separate = work.start_goal_work(
        "demo", "Add unicode support to the slug tool with tests", confirm_new=True)
    assert separate["status"] == "created" and len(work.list_goals("demo")) == 2


def test_an_unrelated_request_needs_no_question(work):
    work.start_goal_work("demo", "Build the slug tool with tests")
    other = work.start_goal_work("demo", "Write a deployment checklist for the billing database")
    assert other["status"] == "created"


def test_the_project_is_never_guessed(work, tmp_path):
    other = tmp_path / "second"
    other.mkdir()
    subprocess.run(["git", "-C", str(other), "init"], check=True, capture_output=True)
    work.register_project(other, "second", "another project")

    question = work.start_goal_work(None, REQUEST)
    unknown = work.start_goal_work("nope", REQUEST)

    assert question["status"] == "needs_choice" and question["reason"] == "ambiguous_project"
    assert set(question["choices"]) == {"demo", "second"}
    assert unknown["reason"] == "unknown_project"
    assert work.store.one("SELECT COUNT(*) AS n FROM goals")["n"] == 0
    assert work.work_overview("second")["groups"] == []


def test_a_single_project_is_used_when_none_is_named(work):
    assert work.start_goal_work(None, REQUEST)["project_id"] == "demo"


def test_standalone_tasks_and_goal_tasks_are_separated(work):
    single = work.create_task("demo", "Fix one small bug")
    goal = work.start_goal_work("demo", REQUEST, plan=good_plan(2))
    phase_task = work.create_task("demo", "Phase 1 of the slug tool", task_id="GOAL-0001-P1")
    plan = work.get_goal("demo", goal["id"])["plan"]
    stamp = "2026-01-01T00:00:00+00:00"
    work.store.execute(
        "INSERT INTO goal_phase_runs(id,project_id,goal_id,phase_id,plan_version,plan_sha256,"
        "approval_id,task_id,workflow_id,requirement_sha256,adapter,pm_settings,pm_timeout,budget,"
        "criteria,test_command,status,creator,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,"
        "?,?,?,?,?,?)",
        ("GPRUN-1", "demo", goal["id"], "P1", 1, plan["sha256"], "GAPP-1", phase_task["id"],
         "standard", "x", "fake", "{}", 300,
         json.dumps({"max_attempts_per_phase": 3, "max_agent_dispatches": 20,
                     "max_wall_clock_minutes": 60}), "[]", "[]", "RUNNING", "t", stamp, stamp))
    overview = work.work_overview("demo")

    assert goal["state"] == "AWAITING_APPROVAL"
    assert [s["id"] for s in overview["singles"]] == [single["id"]]
    assert [p["task_id"] for p in overview["groups"][0]["phases"]] == ["GOAL-0001-P1", None]
    assert overview["groups"][0]["progress"] == {"done": 0, "total": 2}
    assert overview["needs_you"] == 1  # the plan needs approval


def test_the_overview_revision_changes_only_when_something_shown_changes(work):
    started = work.start_goal_work("demo", REQUEST)
    before = work.work_overview("demo")["revision"]
    assert work.work_overview("demo")["revision"] == before
    work.plan_goal_work("demo", started["id"], plan=good_plan(1))
    assert work.work_overview("demo")["revision"] != before


def test_a_plan_written_in_the_chat_waits_for_a_person_and_tools_cannot_approve(work):
    goal = work.start_goal_work("demo", REQUEST)
    planned = work.plan_goal_work("demo", goal["id"], plan=good_plan(2))
    assert planned["state"] == "AWAITING_APPROVAL"

    ask = work.goal_approval_request("demo", goal["id"])
    assert ask["status"] == "approval_needed"
    assert ask["phrase_to_type"] == f"APPROVE {goal['id']} v1"
    assert "cannot approve" in ask["note"]
    assert work.get_goal("demo", goal["id"])["approval"] is None
    with pytest.raises(BridgeError, match="approved|approval|plan"):
        work.control_goal("demo", goal["id"], "start")
    assert work.store.one("SELECT COUNT(*) AS n FROM goal_runs")["n"] == 0


def test_after_a_person_approves_the_same_request_reports_it(work):
    goal = work.start_goal_work("demo", REQUEST, plan=good_plan(1))
    plan = work.get_goal("demo", goal["id"])["plan"]
    work.approve_goal_local("demo", goal["id"], plan["version"], plan["sha256"])

    assert work.goal_approval_request("demo", goal["id"])["status"] == "already_approved"
    assert work.goal_view("demo", goal["id"])["state"] == "READY"


def test_a_change_after_approval_needs_a_new_approval(work):
    goal = work.start_goal_work("demo", REQUEST, plan=good_plan(1))
    plan = work.get_goal("demo", goal["id"])["plan"]
    work.approve_goal_local("demo", goal["id"], plan["version"], plan["sha256"])

    revised = work.plan_goal_work("demo", goal["id"], plan=good_plan(2))

    assert revised["state"] == "AWAITING_APPROVAL" and revised["plan_version"] == 2


def test_the_scripted_planner_plans_a_goal_through_the_same_call(work):
    goal = work.start_goal_work("demo", REQUEST)
    run = work.request_goal_plan("demo", goal["id"], fake_scenario="phases:3", spawn=False)
    work.execute_planning_run(run["id"])

    view = work.goal_view("demo", goal["id"])
    assert view["state"] == "AWAITING_APPROVAL"
    assert [p["id"] for p in view["phases"]] == ["P1", "P2", "P3"]
    assert "approval" in view["headline"].lower()


def test_cancelling_a_goal_that_never_started_ends_it(work):
    goal = work.start_goal_work("demo", REQUEST)
    result = work.control_goal("demo", goal["id"], "cancel", "changed my mind")
    assert result["goal"]["state"] == "CANCELLED"
    with pytest.raises(BridgeError, match="not been started"):
        work.control_goal("demo", goal["id"], "pause")
    with pytest.raises(BridgeError, match="action must be"):
        work.control_goal("demo", goal["id"], "approve")


def test_similarity_ignores_unrelated_text_and_handles_chinese():
    assert similarity("build a slug tool", "build a slug tool with tests") > 0.5
    assert similarity("write a billing checklist", "build a slug tool") == 0
    assert similarity("開發會員登入功能", "會員登入功能開發") > 0.5


# ---- the real MCP protocol over stdio ---------------------------------------------------------


def _server(home, *extra):
    return StdioServerParameters(
        command=sys.executable, args=["-m", "ccc_mcp", "--home", str(home), "serve", *extra],
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )


async def _call(session, name, arguments):
    result = await session.call_tool(name, arguments)
    assert not result.is_error, result.content
    return result.structured_content


async def test_chatgpt_tools_create_and_read_the_same_goal_the_console_lists(bridge, human):
    async with stdio_client(_server(bridge.home)) as (reader, writer):
        # Starting a real Python server can take longer on a busy parallel runner.
        async with ClientSession(reader, writer, read_timeout_seconds=60) as session:
            await session.initialize()
            names = {t.name for t in (await session.list_tools()).tools}
            assert {"start_goal", "plan_goal", "get_goal", "get_work_overview",
                    "request_goal_approval", "control_goal"} <= names

            started = await _call(session, "start_goal", {
                "requirement": REQUEST, "title": "Slug tool", "request_key": "chat-req-0002"})
            retried = await _call(session, "start_goal", {
                "requirement": REQUEST, "title": "Slug tool", "request_key": "chat-req-0002"})
            planned = await _call(session, "plan_goal", {
                "project_id": "demo", "goal_id": started["id"], "plan": good_plan(2)})
            overview = await _call(session, "get_work_overview", {})
            ask = await _call(session, "request_goal_approval", {
                "project_id": "demo", "goal_id": started["id"]})
            refused = await session.call_tool("control_goal", {
                "project_id": "demo", "goal_id": started["id"], "action": "start"})

    assert started["status"] == "created" and retried["status"] == "exists"
    assert retried["id"] == started["id"]
    assert planned["state"] == "AWAITING_APPROVAL"
    assert overview["groups"][0]["id"] == started["id"]
    assert ask["status"] == "approval_needed"
    assert refused.is_error  # not approved: the tool reports it instead of working around it

    # The console reads the same rows through the same service.
    console = CCCMCP(bridge.home).work_overview("demo")
    assert console["groups"][0]["id"] == started["id"]
    assert console["groups"][0]["title"] == "Slug tool"
    assert console["revision"] == overview["revision"]


async def test_read_only_http_exposes_the_reads_and_none_of_the_writes(bridge):
    tools = {t.name for t in await create_server(bridge.home, http=True).list_tools()}
    assert {"get_work_overview", "get_goal", "request_goal_approval"} <= tools
    assert not {"start_goal", "plan_goal", "control_goal"} & tools


def test_goal_tools_are_json_safe(work):
    goal = work.start_goal_work("demo", REQUEST, plan=good_plan(1))
    json.dumps(work.goal_view("demo", goal["id"]), default=str)


async def test_the_tunnel_launches_this_exact_server_and_ships_the_goal_tools_with_honest_annotations(bridge):
    """The ChatGPT tunnel runs `python -m ccc_mcp --home H serve` (adapters/tunnel_client.py), the same
    stdio server started here, so this is the tool list a connector refresh receives. What cannot be
    shown from this machine is ChatGPT itself choosing and calling them."""
    from ccc_mcp.adapters import tunnel_client

    source = open(tunnel_client.__file__, encoding="utf-8").read()
    assert "-m ccc_mcp" in source and "serve" in source

    async with stdio_client(_server(bridge.home)) as (reader, writer):
        async with ClientSession(reader, writer, read_timeout_seconds=60) as session:
            await session.initialize()
            tools = {t.name: t for t in (await session.list_tools()).tools}
    assert len(tools) <= 36                      # few enough that a model is not left choosing between lookalikes
    reads = {"get_work_overview", "get_goal", "request_goal_approval"}
    writes = {"start_goal", "plan_goal", "control_goal"}
    for name in reads:
        assert tools[name].annotations.read_only_hint is True, name
    for name in writes:
        assert tools[name].annotations.read_only_hint is False, name
    assert tools["start_goal"].input_schema["required"] == ["requirement"]
    assert set(tools["control_goal"].input_schema["required"]) == {"project_id", "goal_id", "action"}
    # nothing offered can approve a plan
    assert not [n for n in tools if "approve" in n and n not in reads]
