"""The console's team settings reach an actual MCP-started Goal and its worker processes."""

import asyncio
import json
import os
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from phase_support import FIXED, cli_calls, install_fake_cli, make_slug_project, plan_dict
from starlette.testclient import TestClient

from ccc_mcp.interfaces.web import create_app

pytestmark = pytest.mark.integration
COMMAND = [sys.executable, "-m", "pytest", "-q"]


async def call(session, tool, **arguments):
    result = await session.call_tool(tool, arguments)
    assert not result.is_error, result.content
    return result.structured_content


async def test_saved_team_drives_mcp_goal_and_survives_later_edits(bridge, human, monkeypatch, tmp_path):
    make_slug_project(bridge, tmp_path)
    bridge.authorize_project_local("demo")
    bridge.configure_test_command("demo", COMMAND)
    _, log = install_fake_cli(tmp_path, monkeypatch, phases={"P1": {"initial": [{"file": "app.py", "content": FIXED}]}})
    client = TestClient(create_app(bridge.home, token="team-test"), headers={"X-CCC-UI-Token": "team-test"})
    base = "/api/projects/demo"
    def rev():
        return client.get(f"{base}/settings").json()["team_revision"]

    role = client.post(f"{base}/roles", json={
        "name": "規格把關員", "base_role": "SR", "instructions": "OLD TEAM INSTRUCTION",
        "runtime": {"provider": "codex", "model": "review-v1"},
    })
    assert role.status_code == 200, role.text
    role_id = role.json()["id"]
    steps = [{"role": "SA"}, {"role": "SD"}, {"role": "RD"},
             {"role_ref": role_id}, {"role": "REVIEW"}]
    flow = client.post(f"{base}/workflows", json={
        "name": "驗收團隊", "steps": steps, "revision": rev(), "select": True,
    })
    assert flow.status_code == 200, flow.text
    flow_id = flow.json()["id"]
    settings = client.post(f"{base}/config/team", json={
        "revision": rev(), "changes": {"roles": {
            role: {"provider": "codex"} for role in ("SA", "SD", "RD", "REVIEW")
        }},
    })
    assert settings.status_code == 200, settings.text
    stale_revision = rev()
    invalid = client.post(f"{base}/config/team", json={
        "revision": stale_revision,
        "changes": {"workflow": "standard", "roles": {"RD": {"provider": "missing"}}}})
    assert invalid.status_code == 400
    assert client.get(f"{base}/settings").json()["editable"]["workflow"] == flow_id

    plan = plan_dict(COMMAND, 1)
    del plan["phases"][0]["workflow"]
    params = StdioServerParameters(command=sys.executable, args=[
        "-m", "ccc_mcp", "--home", str(bridge.home), "serve", "--enable-writes",
    ], env=os.environ.copy())
    async with stdio_client(params) as (reader, writer), ClientSession(reader, writer) as session:
        await session.initialize()
        old = await call(session, "start_goal", project_id="demo", title="原有工作",
                         requirement="Implement slug normalization for legacy documents", plan=plan)
        original_id = old["id"]
        old_plan = bridge.get_goal("demo", original_id)["plan"]
        assert old_plan["plan"]["phases"][0]["workflow"] == flow_id
        bridge.approve_goal_local("demo", original_id, old_plan["version"], old_plan["sha256"])

        edited = client.post(f"{base}/roles/{role_id}/update", json={
            "expected_version": 1, "instructions": "NEW TEAM INSTRUCTION",
            "runtime": {"provider": "codex", "model": "review-v2"},
        })
        assert edited.status_code == 200, edited.text
        changed = client.post(f"{base}/workflows", json={
            "id": flow_id, "steps": [*steps[:3], steps[4], steps[3]], "revision": rev(),
        })
        assert changed.status_code == 200, changed.text
        stale = client.post(f"{base}/config/team", json={
            "revision": stale_revision, "changes": {"workflow": "content"},
        })
        assert stale.status_code == 409
        await call(session, "control_goal", project_id="demo", goal_id=original_id, action="start")
        for _ in range(240):
            current = bridge.get_goal("demo", original_id)
            if current["state"] not in {"RUNNING", "VERIFYING"}:
                break
            await asyncio.sleep(0.25)
        assert current["state"] == "PASSED", current.get("goal_run")
        recorded = await call(session, "get_task_result", project_id="demo",
                              task_id=f"{original_id}-P1")
        executions = recorded["result"]
        assert sorted(e["role"] for e in executions) == sorted(["SA", "SD", "RD", "SR", "REVIEW"])
        calls = cli_calls(log)
        assert [entry["role"] for entry in calls] == ["SA", "SD", "RD", "SR", "REVIEW"]
        assert "OLD TEAM INSTRUCTION" in calls[3]["instruction"]
        assert "review-v1" in calls[3]["argv"]

        new = await call(session, "start_goal", project_id="demo", title="下一項工作",
                         requirement="Add slug validation for incoming article titles", plan=plan)
        new_plan = bridge.get_goal("demo", new["id"])["plan"]
        bridge.approve_goal_local("demo", new["id"], new_plan["version"], new_plan["sha256"])
        deleted = client.post(f"{base}/workflows/{flow_id}/delete", json={"revision": rev()})
        assert deleted.status_code == 200, deleted.text
        await call(session, "control_goal", project_id="demo", goal_id=new["id"], action="start")
        for _ in range(240):
            current = bridge.get_goal("demo", new["id"])
            if current["state"] not in {"RUNNING", "VERIFYING"}:
                break
            await asyncio.sleep(0.25)
        assert current["state"] == "PASSED", current.get("goal_run")
        calls = cli_calls(log)
        assert [entry["role"] for entry in calls[5:]] == ["SA", "SD", "RD", "REVIEW", "SR"]
        assert "NEW TEAM INSTRUCTION" in calls[-1]["instruction"]
        assert "review-v2" in calls[-1]["argv"]

    if folder := os.environ.get("CCC_TEAM_EVIDENCE"):
        evidence = {"provider": "Scripted Codex CLI", "goals": [original_id, new["id"]],
                    "goal_results": ["PASSED", "PASSED"], "executions": [
                        {"role": row["role"], "model": next((row["argv"][i + 1]
                         for i, arg in enumerate(row["argv"]) if arg == "--model"), None),
                         "old_prompt": "OLD TEAM INSTRUCTION" in row["instruction"],
                         "new_prompt": "NEW TEAM INSTRUCTION" in row["instruction"]}
                        for row in calls]}
        Path(folder, "mcp-goal-executions.json").write_text(
            json.dumps(evidence, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
