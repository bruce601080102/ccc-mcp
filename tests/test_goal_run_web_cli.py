"""Stage C through its operator entry points: the console's API, the CLI and the page's scripts.

Real worker processes (the goal supervisor, the phase stepper and the agent workers, all started by
the chain of wakes, nothing driven from the test) and the scripted native CLI stand behind the API
test; the other tests only need the routes' rules. EVIDENCE LEVEL: a scripted CLI, a real git project,
a real host test; no real model.
"""

import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest
from phase_support import BUGGY, FIXED, PHASES, install_fake_cli, plan_dict
from starlette.testclient import TestClient
from typer.testing import CliRunner

from ccc_mcp.core.goal_orchestrator import GOAL_REASONS
from ccc_mcp.interfaces.cli import app as cli_app
from ccc_mcp.interfaces.web import create_app

TOKEN = "goal-run-session-token"
STATIC = Path(__file__).parents[1] / "src/ccc_mcp/interfaces/webui_static"
PYTEST = [sys.executable, "-m", "pytest", "-q"]
FIX = {"file": "app.py", "old": 'return text.lower().replace(" ", "-")',
       "new": 'return "-".join(text.lower().split())'}


def git_repo(path):
    path.mkdir()
    (path / "app.py").write_text("def slugify(text):\n    raise NotImplementedError\n", encoding="utf-8")
    (path / "test_app.py").write_text(
        "from app import slugify\n\n\ndef test_words():\n    assert slugify('Hello World') == 'hello-world'\n\n\n"
        "def test_extra_spaces():\n    assert slugify('  A  B ') == 'a-b'\n", encoding="utf-8")
    for args in (["init", "-q"], ["add", "."], ["-c", "user.name=T", "-c", "user.email=t@example.invalid",
                                                  "commit", "-q", "-m", "init"]):
        subprocess.run(["git", "-C", str(path), *args], check=True, capture_output=True)


@pytest.fixture
def client(tmp_path, human):
    app = create_app(tmp_path / "home", token=TOKEN)
    repo = tmp_path / "repo"
    git_repo(repo)
    test_client = TestClient(app, headers={"X-CCC-UI-Token": TOKEN})
    assert test_client.post("/api/projects/add", json={"path": str(repo), "id": "demo"}).status_code == 200
    return test_client


def ready(client, phases=2):
    service = client.app.state.bridge
    service.authorize_project_local("demo")
    service.configure_test_command("demo", PYTEST)
    goal = client.post("/api/projects/demo/goals", json={"requirement": "Build a slug tool"}).json()
    imported = client.post(f"/api/projects/demo/goals/{goal['id']}/import",
                           json={"plan": plan_dict(PYTEST, phases), "base_version": 0})
    assert imported.status_code == 200, imported.text
    shown = client.get(f"/api/projects/demo/goals/{goal['id']}").json()
    done = client.post(f"/api/projects/demo/goals/{goal['id']}/approve", json={
        "version": shown["plan"]["version"], "sha256": shown["plan"]["sha256"],
        "confirm": f"APPROVE {goal['id']} v{shown['plan']['version']}"})
    assert done.status_code == 200, done.text
    return goal["id"]


def test_every_goal_run_route_is_behind_the_session_token(client):
    anonymous = TestClient(client.app)
    for method, path in [
        ("post", "/api/projects/demo/goals/GOAL-0001/run"),
        ("get", "/api/projects/demo/goal-runs/GORUN-x"),
        ("post", "/api/projects/demo/goal-runs/GORUN-x/pause"),
        ("post", "/api/projects/demo/goal-runs/GORUN-x/resume"),
        ("post", "/api/projects/demo/goal-runs/GORUN-x/cancel"),
        ("post", "/api/projects/demo/goal-runs/GORUN-x/accept"),
        ("get", "/api/projects/demo/goal-runs/GORUN-x/accept-preview"),
        ("post", "/api/projects/demo/goal-runs/GORUN-x/revise"),
        ("post", "/api/projects/demo/goal-runs/recover"),
    ]:
        assert getattr(anonymous, method)(path).status_code == 401, (method, path)


def test_the_console_cannot_raise_a_limit_choose_a_command_or_approve_a_plan(client):
    goal = ready(client)
    start = f"/api/projects/demo/goals/{goal}/run"
    for field in ({"adapter": "fake"}, {"budget": {"max_agent_dispatches": 999}},
                  {"test_command": ["true"]}, {"operator_confirmed": True}, {"fake_scenario": "x"},
                  {"plan_version": 9}):
        response = client.post(start, json=field)
        assert response.status_code == 400, (field, response.text)
    assert client.post(start, json={"pm_provider": "chatgpt"}).status_code == 400
    assert client.get(f"/api/projects/demo/goals/{goal}").json()["goal_run"] is None
    for suffix, field in (("resume", {"operator_confirmed": True}), ("resume", {"budget": 5}),
                          ("pause", {"x": 1}), ("cancel", {"x": 1}), ("accept", {"x": 1}),
                          ("revise", {"approve": True}), ("revise", {"plan": {}})):
        assert client.post(f"/api/projects/demo/goal-runs/GORUN-nope/{suffix}", json=field).status_code == 400


def test_an_unapproved_goal_and_a_missing_authorization_are_refused_with_a_reason(client):
    goal = client.post("/api/projects/demo/goals", json={"requirement": "x y z"}).json()
    refused = client.post(f"/api/projects/demo/goals/{goal['id']}/run", json={})
    assert refused.status_code >= 400 and "no plan" in refused.text
    service = client.app.state.bridge
    service.configure_test_command("demo", PYTEST)
    client.post(f"/api/projects/demo/goals/{goal['id']}/import",
                json={"plan": plan_dict(PYTEST, 2), "base_version": 0})
    refused = client.post(f"/api/projects/demo/goals/{goal['id']}/run", json={})
    assert refused.status_code >= 400 and "not approved" in refused.text
    assert service.list_tasks("demo") == []


def wait_for(client, run_id, until, timeout=240):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        seen = client.get(f"/api/projects/demo/goal-runs/{run_id}").json()
        if until(seen):
            return seen
        time.sleep(0.5)
    return seen


def test_the_console_runs_a_whole_goal_through_real_workers_and_a_reopened_page_shows_the_same(
    client, tmp_path, monkeypatch
):
    _, log = install_fake_cli(tmp_path, monkeypatch, phases=PHASES, pm_fix=FIX)
    goal = ready(client)
    started = client.post(f"/api/projects/demo/goals/{goal}/run", json={})
    assert started.status_code == 200, started.text
    run = started.json()
    assert run["status"] == "RUNNING" and run["already_started"] is False

    seen = wait_for(client, run["id"], lambda r: r["status"] not in ("RUNNING", "VERIFYING"))
    assert seen["status"] == "PASSED", (seen["status"], seen["reason"], seen["detail"])
    assert [p["state"] for p in seen["phases"]] == ["PASSED", "PASSED"]
    assert [r["phase_id"] for r in seen["ledger"]] == ["P1", "P2"]
    assert seen["verifications"][-1]["status"] == "PASSED"
    assert seen["budget"]["dispatches"]["used"] == len(
        [line for line in log.read_text().splitlines() if line.strip()])

    # the browser was closed and opened again: another app object over the same home shows the same
    reopened = TestClient(create_app(client.app.state.bridge.home, token=TOKEN),
                          headers={"X-CCC-UI-Token": TOKEN})
    again = reopened.get(f"/api/projects/demo/goals/{goal}").json()
    assert again["state"] == "PASSED" and again["goal_run"]["id"] == run["id"]
    assert again["goal_run"]["verifications"] == seen["verifications"]
    assert [r["phase_id"] for r in again["phase_runs"]] == ["P1", "P2"]
    assert again["phase_runs"][1]["goal_run_id"] == run["id"]
    # nothing was committed or pushed
    out = subprocess.run(["git", "-C", str(client.app.state.bridge.project("demo")["root"]), "log",
                          "--oneline"], capture_output=True, text=True, check=True).stdout.splitlines()
    assert len(out) == 1
    assert client.post(f"/api/projects/demo/goals/{goal}/run", json={}).json()["already_started"] is True
    assert FIXED and BUGGY


def test_the_console_can_pause_resume_and_stop_a_goal_run(client, tmp_path, monkeypatch):
    install_fake_cli(tmp_path, monkeypatch, phases=PHASES, pm_fix=FIX, sleep={"SA": 3})
    goal = ready(client)
    run = client.post(f"/api/projects/demo/goals/{goal}/run", json={}).json()
    paused = client.post(f"/api/projects/demo/goal-runs/{run['id']}/pause", json={"reason": "wait"})
    assert paused.status_code == 200 and paused.json()["status"] == "PAUSED"
    time.sleep(5)
    assert client.get(f"/api/projects/demo/goal-runs/{run['id']}").json()["status"] == "PAUSED"
    resumed = client.post(f"/api/projects/demo/goal-runs/{run['id']}/resume", json={})
    assert resumed.status_code == 200 and resumed.json()["status"] == "RUNNING"
    assert client.post(f"/api/projects/demo/goal-runs/{run['id']}/resume", json={}).status_code >= 400
    stopped = client.post(f"/api/projects/demo/goal-runs/{run['id']}/cancel", json={"reason": "enough"})
    assert stopped.json()["status"] == "CANCELLED"
    time.sleep(4)
    final = client.get(f"/api/projects/demo/goal-runs/{run['id']}").json()
    assert final["status"] == "CANCELLED"
    assert client.app.state.bridge.store.one(
        "SELECT COUNT(*) AS n FROM agent_executions WHERE status IN ('QUEUED','RUNNING')")["n"] == 0
    assert client.get(f"/api/projects/demo/goals/{goal}").json()["state"] == "CANCELLED"


def test_pausing_the_moment_a_developer_step_ends_and_resuming_still_finishes_by_itself(
    client, tmp_path, monkeypatch
):
    """Found in a real browser run: the step that finishes while the goal is paused had its hand-over
    done on resume, but nobody looked at what that hand-over left behind (a failed host test waiting to
    be analysed) until a watchdog woke the run a minute later. With real worker processes and no
    watchdog, resuming must carry the goal all the way to PASSED."""
    install_fake_cli(tmp_path, monkeypatch, phases=PHASES, pm_fix=FIX, sleep={"RD": 3})
    goal = ready(client)
    service = client.app.state.bridge
    run = client.post(f"/api/projects/demo/goals/{goal}/run", json={}).json()
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline and not service.store.one(
        "SELECT 1 FROM agent_executions WHERE role='RD' AND status='SUCCEEDED'"
    ):
        time.sleep(0.02)
    assert client.post(f"/api/projects/demo/goal-runs/{run['id']}/pause", json={}).status_code == 200
    time.sleep(4)
    assert client.post(f"/api/projects/demo/goal-runs/{run['id']}/resume", json={}).status_code == 200
    seen = wait_for(client, run["id"], lambda r: r["status"] not in ("RUNNING", "VERIFYING"), timeout=150)
    assert seen["status"] == "PASSED", (seen["status"], seen["reason"], seen["detail"])


def test_accepting_the_final_result_needs_the_typed_phrase(client, tmp_path, monkeypatch):
    install_fake_cli(tmp_path, monkeypatch, phases={"P1": {"initial": [{"file": "app.py", "content": FIXED}]}})
    service = client.app.state.bridge
    service.authorize_project_local("demo")
    service.configure_test_command("demo", PYTEST)
    plan = plan_dict(PYTEST, 1)
    plan["final_acceptance"].append({"id": "FM", "statement": "A person is happy",
                                     "verify": {"kind": "manual", "description": "Look"}})
    goal = client.post("/api/projects/demo/goals", json={"requirement": "Build a slug tool"}).json()["id"]
    client.post(f"/api/projects/demo/goals/{goal}/import", json={"plan": plan, "base_version": 0})
    shown = client.get(f"/api/projects/demo/goals/{goal}").json()
    client.post(f"/api/projects/demo/goals/{goal}/approve", json={
        "version": 1, "sha256": shown["plan"]["sha256"], "confirm": f"APPROVE {goal} v1"})
    run = client.post(f"/api/projects/demo/goals/{goal}/run", json={}).json()
    seen = wait_for(client, run["id"], lambda r: r["status"] not in ("RUNNING", "VERIFYING"))
    assert seen["status"] == "WAITING_USER" and seen["reason"] == "FINAL_ACCEPTANCE_NEEDED", seen["detail"]
    preview = client.get(f"/api/projects/demo/goal-runs/{run['id']}/accept-preview").json()
    assert preview["challenge"] == f"ACCEPT {goal}" and preview["criteria"][0]["id"] == "FM"
    assert client.post(f"/api/projects/demo/goal-runs/{run['id']}/accept", json={"confirm": "yes"}
                       ).status_code >= 400
    assert client.get(f"/api/projects/demo/goal-runs/{run['id']}").json()["status"] == "WAITING_USER"
    right = client.post(f"/api/projects/demo/goal-runs/{run['id']}/accept",
                        json={"confirm": preview["challenge"]})
    assert right.status_code == 200 and right.json()["status"] == "PASSED"


# the CLI -----------------------------------------------------------------------------------------------------


def test_the_cli_refuses_to_start_what_is_not_approved_and_names_the_reason(tmp_path, human):
    repo = tmp_path / "repo"
    git_repo(repo)
    runner = CliRunner()
    home = tmp_path / "home"
    base = ["--home", str(home), "--project", "demo"]
    assert runner.invoke(cli_app, ["--home", str(home), "project", "add", str(repo), "--id", "demo"]).exit_code == 0
    assert runner.invoke(cli_app, [*base, "goal", "create", "x y z"]).exit_code == 0
    refused = runner.invoke(cli_app, [*base, "goal", "run", "GOAL-0001"])
    assert refused.exit_code != 0 and "no plan" in (str(refused.exception) + refused.output)
    missing = runner.invoke(cli_app, [*base, "goal", "run-show", "GORUN-nope"])
    assert missing.exit_code != 0 and "not found" in (str(missing.exception) + missing.output)
    for command in ("run-pause", "run-resume", "run-cancel", "run-accept", "run-revise"):
        assert runner.invoke(cli_app, [*base, "goal", command, "GORUN-nope"]).exit_code != 0


def test_the_cli_runs_a_whole_goal_and_shows_it(tmp_path, human, monkeypatch):
    install_fake_cli(tmp_path, monkeypatch, phases=PHASES, pm_fix=FIX)
    repo = tmp_path / "repo"
    git_repo(repo)
    runner = CliRunner()
    home = tmp_path / "home"
    base = ["--home", str(home), "--project", "demo"]

    def run(*args):
        result = runner.invoke(cli_app, [*base, *args])
        assert result.exit_code == 0, (result.output, result.exception)
        return json.loads(result.stdout)

    assert runner.invoke(cli_app, ["--home", str(home), "project", "add", str(repo), "--id", "demo"]).exit_code == 0
    from ccc_mcp.service import CCCMCP

    service = CCCMCP(home)
    service.authorize_project_local("demo")
    service.configure_test_command("demo", PYTEST)
    goal = run("goal", "create", "Build a slug tool")
    plan_file = tmp_path / "plan.json"
    plan_file.write_text(json.dumps(plan_dict(PYTEST, 2)), encoding="utf-8")
    run("goal", "import", goal["id"], "--file", str(plan_file), "--base-version", "0")
    run("goal", "approve", goal["id"])

    done = run("goal", "run", goal["id"], "--wait", "--timeout", "240")
    assert done["status"] == "PASSED", (done["status"], done["reason"], done["detail"])
    shown = run("goal", "run-show", done["id"])
    assert [p["state"] for p in shown["phases"]] == ["PASSED", "PASSED"]
    again = run("goal", "run", goal["id"])
    assert again["id"] == done["id"] and again["already_started"] is True
    assert run("goal", "show", goal["id"])["state"] == "PASSED"
    assert run("goal", "run-recover") == []


# the page --------------------------------------------------------------------------------------------------


I18N = (STATIC / "i18n.js").read_text(encoding="utf-8")
GOALRUN_JS = (STATIC / "goalrun.js").read_text(encoding="utf-8")
GOALS_JS = (STATIC / "goals.js").read_text(encoding="utf-8")


def zh_keys():
    start = I18N.index("const zh = {")
    block = I18N[start:I18N.index("\n  };\n", start)]
    return {m.group(1) for m in re.finditer(r'"((?:[^"\\]|\\.)*)"\s*:\s*"', block)}


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_goal_run_script_parses_and_loads_before_the_goal_page():
    assert subprocess.run(["node", "--check", str(STATIC / "goalrun.js")], capture_output=True,
                          check=False).returncode == 0
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    assert page.index("phases.js") < page.index("goalrun.js") < page.index("goals.js") < page.index("app.js")


def test_every_sentence_the_goal_run_panel_shows_has_a_traditional_chinese_entry():
    literals = {m.group(1) for m in re.finditer(r'"((?:[^"\\]|\\.)*)"', GOALRUN_JS)}
    shown = {text for text in literals if re.fullmatch(r"[A-Z][A-Za-z0-9 ,.'’()\-:?]+", text)}
    ignore = {"RUNNING", "VERIFYING", "WAITING_USER", "PAUSED", "PASSED", "EXHAUSTED", "FAILED",
              "CANCELLED", "READY", "BLOCKED", "STARTING", "NEEDS_HUMAN", "AWAITING_ACCEPTANCE",
              "INTEGRATION", "INTERRUPTED", "NEEDS_PERSON", "POST", "TREE_CHANGED", "RD"}
    assert not sorted(shown - ignore - zh_keys())
    called = set(re.findall(r'(?:\bT|UI\.translate)\("((?:[^"\\]|\\.)*)"\)', GOALRUN_JS))
    assert not sorted(called - zh_keys())
    for key in re.findall(r'(?:title|body|confirmLabel): "((?:[^"\\]|\\.)*)"', GOALRUN_JS + GOALS_JS):
        assert key in zh_keys(), key


def test_every_next_step_sentence_of_a_goal_has_a_traditional_chinese_entry():
    from ccc_mcp.core.goals import _NEXT

    assert not [text for text in _NEXT.values() if text not in zh_keys()]


def test_every_reason_a_goal_run_can_stop_for_has_a_traditional_chinese_sentence():
    assert not [message for message, _ in GOAL_REASONS.values() if message not in zh_keys()]


def test_the_goal_run_panel_never_puts_server_text_into_markup_unescaped():
    assert ".innerHTML" not in GOALRUN_JS and "insertAdjacentHTML" not in GOALRUN_JS
    bare = re.findall(
        r"\$\{\s*(?:run|phase|v|e|d|c|p|r|goal|host|l|b|info)"
        r"(?:\.[A-Za-z_]+|\[[^\]]+\])+\s*\}", GOALRUN_JS)
    assert not bare, bare
