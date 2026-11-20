"""Stage B through its operator entry points: the console's API and the CLI, and the page's scripts.

Real worker processes and the scripted native CLI (see phase_support.py) stand behind the API
test; the other tests only need the routes' rules.
"""

import json
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest
from phase_support import BUGGY, FIXED, install_fake_cli, plan_dict
from starlette.testclient import TestClient
from typer.testing import CliRunner

from ccc_mcp.core.phase_runner import REASONS
from ccc_mcp.interfaces.cli import app as cli_app
from ccc_mcp.interfaces.web import create_app

TOKEN = "phase-session-token"
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


def ready(client):
    """An approved one-or-two-phase goal in an authorized project with a test command, through the
    same calls the console makes (the operator ceremonies are replaced by the `human` fixture)."""
    service = client.app.state.bridge
    service.authorize_project_local("demo")
    service.configure_test_command("demo", PYTEST)
    goal = client.post("/api/projects/demo/goals", json={"requirement": "Build a slug tool"}).json()
    imported = client.post(f"/api/projects/demo/goals/{goal['id']}/import",
                           json={"plan": plan_dict(PYTEST, 2), "base_version": 0})
    assert imported.status_code == 200, imported.text
    shown = client.get(f"/api/projects/demo/goals/{goal['id']}").json()
    phrase = f"APPROVE {goal['id']} v{shown['plan']['version']}"
    done = client.post(f"/api/projects/demo/goals/{goal['id']}/approve", json={
        "version": shown["plan"]["version"], "sha256": shown["plan"]["sha256"], "confirm": phrase})
    assert done.status_code == 200, done.text
    return goal["id"]


def test_every_phase_route_is_behind_the_session_token(client):
    anonymous = TestClient(client.app)
    for method, path in [
        ("post", "/api/projects/demo/goals/GOAL-0001/phases/P1/start"),
        ("get", "/api/projects/demo/phase-runs/PRUN-x"),
        ("post", "/api/projects/demo/phase-runs/PRUN-x/pause"),
        ("post", "/api/projects/demo/phase-runs/PRUN-x/resume"),
        ("post", "/api/projects/demo/phase-runs/PRUN-x/cancel"),
        ("post", "/api/projects/demo/phase-runs/PRUN-x/accept"),
        ("get", "/api/projects/demo/phase-runs/PRUN-x/accept-preview"),
        ("post", "/api/projects/demo/phase-runs/recover"),
    ]:
        assert getattr(anonymous, method)(path).status_code == 401, (method, path)


def test_the_console_cannot_raise_a_limit_choose_a_command_or_override_a_safety_refusal(client):
    goal = ready(client)
    start = f"/api/projects/demo/goals/{goal}/phases/P1/start"
    for field in ({"adapter": "fake"}, {"budget": {"max_agent_dispatches": 999}},
                  {"test_command": ["true"]}, {"operator_confirmed": True}, {"fake_scenario": "x"}):
        response = client.post(start, json=field)
        assert response.status_code == 400, (field, response.text)
    assert client.post(start, json={"pm_provider": "chatgpt"}).status_code == 400
    # nothing was started by any of those
    assert client.get(f"/api/projects/demo/goals/{goal}").json()["phase_runs"] == []
    for suffix, field in (("resume", {"operator_confirmed": True}), ("resume", {"budget": 5}),
                          ("pause", {"x": 1}), ("cancel", {"x": 1})):
        assert client.post(f"/api/projects/demo/phase-runs/PRUN-nope/{suffix}", json=field).status_code == 400


def test_an_unapproved_goal_and_a_missing_authorization_are_refused_with_a_reason(client):
    goal = client.post("/api/projects/demo/goals", json={"requirement": "x y z"}).json()
    refused = client.post(f"/api/projects/demo/goals/{goal['id']}/phases/P1/start", json={})
    assert refused.status_code >= 400 and "no plan" in refused.text
    service = client.app.state.bridge
    service.configure_test_command("demo", PYTEST)
    client.post(f"/api/projects/demo/goals/{goal['id']}/import",
                json={"plan": plan_dict(PYTEST, 1), "base_version": 0})
    refused = client.post(f"/api/projects/demo/goals/{goal['id']}/phases/P1/start", json={})
    assert refused.status_code >= 400 and "not approved" in refused.text
    assert service.list_tasks("demo") == []


def test_the_console_runs_a_phase_through_real_workers_and_shows_the_story(client, tmp_path, monkeypatch):
    install_fake_cli(tmp_path, monkeypatch, initial=[{"file": "app.py", "content": BUGGY}], pm_fix=FIX)
    goal = ready(client)
    started = client.post(f"/api/projects/demo/goals/{goal}/phases/P1/start", json={})
    assert started.status_code == 200, started.text
    run = started.json()
    assert run["task_id"] == f"{goal}-P1" and run["status"] in ("STARTING", "RUNNING")

    deadline = time.monotonic() + 150
    while time.monotonic() < deadline:
        seen = client.get(f"/api/projects/demo/phase-runs/{run['id']}").json()
        if seen["status"] not in ("STARTING", "RUNNING"):
            break
        time.sleep(0.5)
    assert seen["status"] == "PASSED", (seen["status"], seen["reason"], seen["detail"])
    assert [a["status"] for a in seen["attempts"]] == ["FAILED", "PASSED"]
    assert seen["repairs"][0]["spec"]["message"] and seen["host_test"]["passed"] is True
    listing = client.get(f"/api/projects/demo/goals/{goal}").json()
    assert [r["phase_id"] for r in listing["phase_runs"]] == ["P1"]      # phase 2 was not started
    assert listing["phase_runs"][0]["budget"]["attempts"] == {"used": 2, "max": 3, "left": 1}
    # a run that ended cannot be paused or resumed from the console
    assert client.post(f"/api/projects/demo/phase-runs/{run['id']}/pause", json={}).status_code >= 400
    assert FIXED


def test_accepting_criteria_a_command_cannot_judge_needs_the_typed_phrase(client, tmp_path, monkeypatch):
    install_fake_cli(tmp_path, monkeypatch, initial=[{"file": "app.py", "content": FIXED}])
    service = client.app.state.bridge
    service.authorize_project_local("demo")
    service.configure_test_command("demo", PYTEST)
    plan = plan_dict(PYTEST, 1, manual_in=1)
    goal = client.post("/api/projects/demo/goals", json={"requirement": "Build a slug tool"}).json()["id"]
    client.post(f"/api/projects/demo/goals/{goal}/import", json={"plan": plan, "base_version": 0})
    shown = client.get(f"/api/projects/demo/goals/{goal}").json()
    client.post(f"/api/projects/demo/goals/{goal}/approve", json={
        "version": 1, "sha256": shown["plan"]["sha256"], "confirm": f"APPROVE {goal} v1"})
    run = client.post(f"/api/projects/demo/goals/{goal}/phases/P1/start", json={}).json()
    deadline = time.monotonic() + 150
    while time.monotonic() < deadline:
        seen = client.get(f"/api/projects/demo/phase-runs/{run['id']}").json()
        if seen["status"] not in ("STARTING", "RUNNING"):
            break
        time.sleep(0.5)
    assert seen["status"] == "AWAITING_ACCEPTANCE", (seen["status"], seen["reason"])
    preview = client.get(f"/api/projects/demo/phase-runs/{run['id']}/accept-preview").json()
    assert preview["challenge"] == f"ACCEPT {goal} P1" and preview["criteria"][0]["id"] == "M1"
    wrong = client.post(f"/api/projects/demo/phase-runs/{run['id']}/accept", json={"confirm": "yes"})
    assert wrong.status_code >= 400
    assert client.get(f"/api/projects/demo/phase-runs/{run['id']}").json()["status"] == "AWAITING_ACCEPTANCE"
    right = client.post(f"/api/projects/demo/phase-runs/{run['id']}/accept",
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
    refused = runner.invoke(cli_app, [*base, "goal", "phase-start", "GOAL-0001", "P1"])
    assert refused.exit_code != 0 and "no plan" in (str(refused.exception) + refused.output)
    missing = runner.invoke(cli_app, [*base, "goal", "phase-show", "PRUN-nope"])
    assert missing.exit_code != 0 and "not found" in (str(missing.exception) + missing.output)
    for command in ("phase-pause", "phase-resume", "phase-cancel"):
        assert runner.invoke(cli_app, [*base, "goal", command, "PRUN-nope"]).exit_code != 0


def test_the_cli_starts_shows_and_stops_a_phase(tmp_path, human, monkeypatch):
    install_fake_cli(tmp_path, monkeypatch, initial=[{"file": "app.py", "content": FIXED}])
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

    started = run("goal", "phase-start", goal["id"], "P1", "--wait", "--timeout", "150")
    assert started["status"] == "PASSED", (started["status"], started["reason"], started["detail"])
    shown = run("goal", "phase-show", started["id"])
    assert shown["task_id"] == f"{goal['id']}-P1" and shown["budget"]["attempts"]["used"] == 1
    again = run("goal", "phase-start", goal["id"], "P1")
    assert again["id"] == started["id"] and again["already_started"] is True
    assert [r["phase_id"] for r in run("goal", "show", goal["id"])["phase_runs"]] == ["P1"]
    assert run("goal", "phase-recover") == []


# the page --------------------------------------------------------------------------------------------------


I18N = (STATIC / "i18n.js").read_text(encoding="utf-8")
PHASES_JS = (STATIC / "phases.js").read_text(encoding="utf-8")


def zh_keys():
    start = I18N.index("const zh = {")
    block = I18N[start:I18N.index("\n  };\n", start)]
    return {m.group(1) for m in re.finditer(r'"((?:[^"\\]|\\.)*)"\s*:\s*"', block)}


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_phase_script_parses_and_loads_before_the_goal_page():
    assert subprocess.run(["node", "--check", str(STATIC / "phases.js")], capture_output=True,
                          check=False).returncode == 0
    page = (STATIC / "index.html").read_text(encoding="utf-8")
    assert page.index("phases.js") < page.index("goals.js") < page.index("app.js")


def test_every_sentence_the_phase_panel_shows_has_a_traditional_chinese_entry():
    literals = {m.group(1) for m in re.finditer(r'"((?:[^"\\]|\\.)*)"', PHASES_JS)}
    shown = {text for text in literals if re.fullmatch(r"[A-Z][A-Za-z0-9 ,.'’()\-:?]+", text)}
    ignore = {"PM", "DEV", "PASS", "NEEDS_REVISION", "REJECT", "STARTING", "RUNNING", "PAUSED",
              "NEEDS_HUMAN", "AWAITING_ACCEPTANCE", "PASSED", "EXHAUSTED", "CANCELLED", "REPAIR",
              "PROTECTED_PATH", "RD", "INTERRUPTED", "SD/RD", "POST", "READY"}
    assert not sorted(shown - ignore - zh_keys())
    # strings passed through the translator are checked wherever they start, lowercase included
    called = set(re.findall(r'(?:\bT|UI\.translate)\("((?:[^"\\]|\\.)*)"\)', PHASES_JS))
    assert not sorted(called - zh_keys())


def test_every_reason_a_run_can_stop_for_has_a_traditional_chinese_sentence():
    assert not [message for message, _ in REASONS.values() if message not in zh_keys()]


def test_the_phase_panel_never_puts_server_text_into_markup_unescaped():
    assert ".innerHTML" not in PHASES_JS and "insertAdjacentHTML" not in PHASES_JS
    bare = re.findall(
        r"\$\{\s*(?:run|att|repair|cause|item|spec|evidence|e|g|r|x|c|dev|info|b|host)"
        r"(?:\.[A-Za-z_]+|\[[^\]]+\])+\s*\}", PHASES_JS)
    assert not bare, bare


def test_the_page_never_claims_the_next_phase_runs_by_itself():
    assert "never started automatically" in PHASES_JS
    assert "Stage C" not in PHASES_JS and "automatically starts" not in PHASES_JS
