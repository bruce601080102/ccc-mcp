"""Stage A through its two operator entry points: the local console's API and the CLI.

The console is token-gated like every other route, and approving a plan is the one request that
also needs the operator's typed phrase. The scripted fake PM stands in for a model.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest
from starlette.testclient import TestClient
from typer.testing import CliRunner

from ccc_mcp.core.goals import GoalHub
from ccc_mcp.interfaces.cli import app as cli_app
from ccc_mcp.interfaces.web import create_app

TOKEN = "goal-session-token"
REQUIREMENT = "Build a slug tool: a slugify function with tests, and a small command line."
STATIC = Path(__file__).parents[1] / "src/ccc_mcp/interfaces/webui_static"


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path / "home", token=TOKEN)
    service = app.state.bridge
    service.planning_adapter_default = "fake"
    # The worker process is replaced by running the planning inline, so a request returns finished.
    service.spawn_planning_worker = lambda run_id: service.execute_planning_run(run_id)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "README.md").write_text("x", encoding="utf-8")
    test_client = TestClient(app, headers={"X-CCC-UI-Token": TOKEN})
    assert test_client.post("/api/projects/add", json={"path": str(repo), "id": "demo"}).status_code == 200
    return test_client


def goal_path(goal_id=""):
    return "/api/projects/demo/goals" + (f"/{goal_id}" if goal_id else "")


CREATE = "/api/projects/demo/work/goals"
WORK = "/api/projects/demo/work"


def make_goal(client, **extra):
    """A draft goal, as a person who asked ChatGPT (or the CLI) for one would have: no plan yet."""
    return client.app.state.bridge.create_goal("demo", REQUIREMENT, **extra)


# The API ---------------------------------------------------------------------------------------------


def test_every_goal_route_is_behind_the_session_token(client, tmp_path):
    anonymous = TestClient(client.app)
    for method, path in [("get", WORK), ("post", CREATE), ("get", goal_path("GOAL-0001")),
                         ("post", goal_path("GOAL-0001") + "/plan"),
                         ("post", goal_path("GOAL-0001") + "/approve"),
                         ("post", goal_path("GOAL-0001") + "/cancel")]:
        assert getattr(anonymous, method)(path).status_code == 401, (method, path)


def test_the_console_creates_plans_revises_and_approves_a_goal(client):
    goal = make_goal(client, title="Slug tool")
    assert goal["state"] == "DRAFT" and goal["id"] == "GOAL-0001" and goal["title"] == "Slug tool"

    planned = client.post(goal_path(goal["id"]) + "/plan", json={})
    assert planned.status_code == 200 and planned.json()["status"] == "SUCCEEDED"
    shown = client.get(goal_path(goal["id"])).json()
    assert shown["state"] == "AWAITING_APPROVAL" and len(shown["plan"]["plan"]["phases"]) == 2
    assert shown["plan"]["message"] and shown["plan"]["sha256"]
    assert [e["kind"] for e in shown["events"]] == [
        "goal_created", "plan_requested", "plan_version_created"]

    revised = client.post(goal_path(goal["id"]) + "/plan", json={
        "change_request": "Add a security review", "base_version": 1})
    assert revised.status_code == 200
    second = client.get(goal_path(goal["id"])).json()
    assert second["current_version"] == 2 and second["plan"]["change_request"] == "Add a security review"
    assert [v["version"] for v in second["versions"]] == [1, 2]

    stale = client.post(goal_path(goal["id"]) + "/plan", json={"base_version": 1})
    assert stale.status_code == 400 and "changed" in stale.json()["error"]

    version, sha = second["plan"]["version"], second["plan"]["sha256"]
    wrong = client.post(goal_path(goal["id"]) + "/approve",
                        json={"version": version, "sha256": sha, "confirm": "approve"})
    assert wrong.status_code == 400
    assert client.get(goal_path(goal["id"])).json()["approval"] is None

    phrase = f"APPROVE {goal['id']} v{version}"
    old = client.post(goal_path(goal["id"]) + "/approve", json={
        "version": 1, "sha256": shown["plan"]["sha256"], "confirm": f"APPROVE {goal['id']} v1"})
    assert old.status_code == 400 and "no longer the current plan" in old.json()["error"]

    done = client.post(goal_path(goal["id"]) + "/approve",
                       json={"version": version, "sha256": sha, "confirm": phrase})
    assert done.status_code == 200 and done.json()["already_approved"] is False
    again = client.post(goal_path(goal["id"]) + "/approve",
                        json={"version": version, "sha256": sha, "confirm": phrase})
    assert again.json()["already_approved"] is True
    ready = client.get(goal_path(goal["id"])).json()
    assert ready["state"] == "READY" and ready["approval"]["identity"]
    assert ready["execution"]["available"] is True and ready["phase_runs"] == []
    assert [e["kind"] for e in ready["events"]].count("approved") == 1


def test_creating_a_goal_twice_with_one_key_returns_the_same_goal(client):
    first = make_goal(client, request_key="ui-key-one-goal")
    again = make_goal(client, request_key="ui-key-one-goal")
    assert first["id"] == again["id"] and again["created_now"] is False
    assert len(client.get(WORK).json()["groups"]) == 1


def test_the_api_refuses_unknown_and_unsafe_fields(client):
    goal = make_goal(client)
    base = goal_path(goal["id"])
    for path, body in [
        (CREATE, {"requirement": "x", "adapter": "fake"}),
        (base + "/plan", {"adapter": "fake"}),
        (base + "/plan", {"fake_scenario": "success"}),
        (base + "/plan", {"provider": "chatgpt"}),
        (base + "/approve", {"version": "1", "sha256": "x", "confirm": "x"}),
        (base + "/approve", {"version": 1, "sha256": "x", "confirm": "x", "identity": "someone"}),
        (base + "/cancel", {"force": True}),
    ]:
        assert client.post(path, json=body).status_code == 400, (path, body)
    assert client.post(CREATE, json={"requirement": "  "}).status_code == 400
    assert client.get(goal_path("GOAL-9999")).status_code == 400


def test_the_console_imports_and_cancels(client):
    goal = make_goal(client)
    plan_text = json.dumps({
        "summary": "S", "in_scope": ["x"], "out_of_scope": [], "decisions_needed": [],
        "assumptions": [], "risks": [], "phases": [{
            "id": "P1", "title": "One", "scope": "s", "outputs": ["o"], "depends_on": [],
            "acceptance": [{"id": "C1", "statement": "ok", "verify": {
                "kind": "host_test", "command": ["pytest", "-q"]}}],
            "workflow": "standard", "risk": "low"}],
        "final_acceptance": [{"id": "F", "statement": "ok", "verify": {
            "kind": "manual", "description": "look"}}],
    })
    bad = client.post(goal_path(goal["id"]) + "/import", json={"plan": "{nope", "base_version": 0})
    assert bad.status_code == 400
    good = client.post(goal_path(goal["id"]) + "/import",
                       json={"plan": plan_text, "base_version": 0, "note": "from chat"})
    assert good.status_code == 200 and good.json()["goal"]["plan"]["source"] == "manual"
    assert client.post(goal_path(goal["id"]) + "/plan/cancel").status_code == 400
    done = client.post(goal_path(goal["id"]) + "/cancel", json={"reason": "no"})
    assert done.status_code == 200 and done.json()["state"] == "CANCELLED"
    assert client.post(goal_path(goal["id"]) + "/plan", json={}).status_code == 400


def test_a_chatgpt_only_project_gets_a_clear_refusal_from_the_console(tmp_path):
    app = create_app(tmp_path / "home", token=TOKEN)
    repo = tmp_path / "repo"
    repo.mkdir()
    client = TestClient(app, headers={"X-CCC-UI-Token": TOKEN})
    client.post("/api/projects/add", json={"path": str(repo), "id": "demo"})
    app.state.bridge.configure_role("demo", "SA", "chatgpt")
    goal = make_goal(client)
    response = client.post(goal_path(goal["id"]) + "/plan", json={})
    assert response.status_code == 400 and "ChatGPT" in response.json()["error"]
    assert client.get(goal_path(goal["id"])).json()["runs"] == []


def test_the_goals_page_script_is_served_and_loaded_before_the_console(client):
    page = client.get("/")
    assert page.status_code == 200
    assert page.text.index("goals.js") < page.text.index("app.js")
    script = client.get("/goals.js")
    assert script.status_code == 200 and "cccGoals" in script.text


# The CLI -----------------------------------------------------------------------------------------------


@pytest.fixture
def cli(tmp_path, human, monkeypatch):
    monkeypatch.setattr(GoalHub, "planning_adapter_default", "fake")
    monkeypatch.setattr(
        GoalHub, "spawn_planning_worker", lambda self, run_id: self.execute_planning_run(run_id))
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "f.txt").write_text("x", encoding="utf-8")
    runner = CliRunner()
    home = tmp_path / "home"

    def run(*args, ok=True):
        result = runner.invoke(cli_app, ["--home", str(home), "--project", "demo", *args])
        assert (result.exit_code == 0) is ok, result.output
        return json.loads(result.stdout) if ok else result

    first = runner.invoke(cli_app, ["--home", str(home), "project", "add", str(repo), "--id", "demo"])
    assert first.exit_code == 0, first.output
    return run


def test_the_cli_walks_a_goal_from_requirement_to_approval(cli, tmp_path):
    goal = cli("goal", "create", REQUIREMENT, "--request-key", "cli-key-one-goal")
    assert goal["id"] == "GOAL-0001" and goal["state"] == "DRAFT"
    assert cli("goal", "create", REQUIREMENT, "--request-key", "cli-key-one-goal")["created_now"] is False
    assert [g["id"] for g in cli("goal", "list")] == ["GOAL-0001"]

    planned = cli("goal", "plan", "GOAL-0001")
    assert planned["state"] == "AWAITING_APPROVAL" and planned["current_version"] == 1
    revised = cli("goal", "plan", "GOAL-0001", "--change", "Split the first phase")
    assert revised["current_version"] == 2

    approved = cli("goal", "approve", "GOAL-0001")
    assert approved["status"] == "APPROVED" and approved["plan_version"] == 2
    assert cli("goal", "approve", "GOAL-0001")["already_approved"] is True
    shown = cli("goal", "show", "GOAL-0001")
    assert shown["state"] == "READY" and shown["approval"]["plan_version"] == 2

    plan_file = tmp_path / "plan.json"
    plan_file.write_text(json.dumps(shown["plan"]["plan"]), encoding="utf-8")
    imported = cli("goal", "import", "GOAL-0001", "--file", str(plan_file), "--base-version", "2",
                   "--note", "tweaked")
    assert imported["version"] == 3
    assert cli("goal", "show", "GOAL-0001")["state"] == "AWAITING_APPROVAL"
    assert cli("goal", "cancel", "GOAL-0001", "--reason", "done with it")["state"] == "CANCELLED"


def test_the_cli_reports_a_plan_the_model_got_wrong_instead_of_storing_it(cli, monkeypatch):
    original = GoalHub.request_goal_plan

    def broken(self, *args, **kwargs):
        return original(self, *args, **{**kwargs, "fake_scenario": "cyclic"})

    monkeypatch.setattr(GoalHub, "request_goal_plan", broken)
    cli("goal", "create", REQUIREMENT)
    shown = cli("goal", "plan", "GOAL-0001")
    assert shown["state"] == "DRAFT" and shown["last_run"]["failure_kind"] == "INVALID_OUTPUT"


def test_the_cli_needs_a_real_terminal_to_approve_and_a_choice_of_cli_provider(
    tmp_path, monkeypatch
):
    repo = tmp_path / "repo"
    repo.mkdir()
    runner = CliRunner()
    home = tmp_path / "home"
    runner.invoke(cli_app, ["--home", str(home), "project", "add", str(repo), "--id", "demo"])
    runner.invoke(cli_app, ["--home", str(home), "--project", "demo", "config", "role", "SA",
                            "chatgpt"])
    created = runner.invoke(cli_app, ["--home", str(home), "--project", "demo", "goal", "create", "x y z"])
    assert created.exit_code == 0, created.output
    plan = runner.invoke(cli_app, ["--home", str(home), "--project", "demo", "goal", "plan", "GOAL-0001"])
    assert plan.exit_code != 0 and "ChatGPT" in str(plan.exception or plan.output)
    approve = runner.invoke(cli_app, ["--home", str(home), "--project", "demo", "goal", "approve", "GOAL-0001"])
    assert approve.exit_code != 0


# The page itself (static checks; the behaviour was also exercised in a real browser) ------------------

GOALS_JS = (STATIC / "goals.js").read_text(encoding="utf-8")
I18N = (STATIC / "i18n.js").read_text(encoding="utf-8")


def zh_keys():
    start = I18N.index("const zh = {")
    block = I18N[start:I18N.index("\n  };\n", start)]
    return {m.group(1) for m in re.finditer(r'"((?:[^"\\]|\\.)*)"\s*:\s*"', block)}


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
@pytest.mark.parametrize("name", ["goals.js", "app.js", "i18n.js", "phases.js"])
def test_the_console_scripts_still_parse(name):
    result = subprocess.run(
        ["node", "--check", str(STATIC / name)], capture_output=True, check=False)
    assert result.returncode == 0


def test_every_sentence_the_goal_pages_show_has_a_traditional_chinese_entry():
    literals = {m.group(1) for m in re.finditer(r'"((?:[^"\\]|\\.)*)"', GOALS_JS)}
    shown = {text for text in literals if re.fullmatch(r"[A-Z][A-Za-z0-9 ,.'’()\-:?]+", text)}
    # Names that are not prose: protocol words, ids and CSS-like tokens.
    ignore = {"PM", "APPROVE", "APPROVE ", "Enter", "POST", "PLANNING", "READY"}
    missing = sorted(shown - ignore - zh_keys())
    assert not missing, missing


def test_the_goal_pages_never_put_server_text_into_markup_unescaped():
    assert ".innerHTML" not in GOALS_JS and "insertAdjacentHTML" not in GOALS_JS
    bare = re.findall(
        r"\$\{\s*(?:goal|d|v|phase|criterion|plan|body|event|row|verify|decision|option)"
        r"(?:\.[A-Za-z_]+|\[[^\]]+\])+\s*\}", GOALS_JS)
    assert not bare, bare
    assert "${esc(" in GOALS_JS and "${T(" in GOALS_JS


def test_a_goal_opens_from_the_tasks_page_and_has_no_page_of_its_own():
    app_js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "goalsView.renderGoalDetail" in app_js and 'data-nav="goals"' not in app_js
    assert "/tasks/${encodeURIComponent(group.id)}" in (STATIC / "tasks.js").read_text(encoding="utf-8")



def test_the_consoles_start_button_also_asks_the_planner_so_nobody_hunts_for_a_plan_button(client):
    started = client.post(CREATE, json={"requirement": REQUIREMENT, "title": "Slug tool"})
    assert started.status_code == 200, started.text
    body = started.json()
    assert body["planning"] == "started" and body["created_now"] is True
    shown = client.get(goal_path(body["id"])).json()
    assert shown["state"] == "AWAITING_APPROVAL" and shown["approval"] is None   # planned, never approved
