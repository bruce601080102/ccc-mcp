"""A whole workflow of custom roles played by ChatGPT, driven by simulated ChatGPT reports.

No model is involved: the tests act as the ChatGPT conversation by calling the same MCP-facing
service methods it would (submit_agent_report, write_task_file), with reports they compose.
"""

import json

import pytest

from ccc_mcp.core import role_prompt
from ccc_mcp.core.models import AgentReport
from ccc_mcp.infra.errors import PermissionDenied


def report(summary="Done", **kwargs):
    return AgentReport(summary=summary, next_action="Hand over", **kwargs).model_dump()


def make_role(bridge, name, base, text):
    return bridge.create_custom_role(
        "demo", name, base, prompt={"mode": "freeform", "freeform": text},
        runtime={"provider": "chatgpt"})


def active(bridge, task_id):
    return bridge.store.one(
        "SELECT * FROM agent_executions WHERE project_id='demo' AND task_id=? "
        "AND status IN ('QUEUED','RUNNING')", (task_id,))


def all_runs(bridge, task_id):
    return bridge.store.all(
        "SELECT * FROM agent_executions WHERE project_id='demo' AND task_id=? ORDER BY created",
        (task_id,))


def refine(bridge, execution_id, plan="The approved plan"):
    item = next(i for i in bridge.latest("demo")["snapshot"]["agent_results"]
                if i["execution_id"] == execution_id)
    patch = {
        "base_context_version": bridge.latest("demo")["version"],
        "changes": {"summary": "PM reviewed the analyst."},
        "source_execution_ids": [execution_id], "source_task_ids": [item["task_id"]],
        "source_document_refs": [item["source_document_ref"]],
        "update_reason": "PM verified", "semantic_review_ack": True,
    }
    return bridge.advance_project("demo", patch, plan, adapter="real")


@pytest.fixture
def team(bridge, human):
    analyst = make_role(bridge, "Deep Analyst", "SA", "Look for hidden requirements.")
    gate = make_role(bridge, "Strict Gate", "AR", "Hunt for flaws, cite evidence.")
    writer = make_role(bridge, "Spec Maker", "SW", "Write the spec in plain words.")
    saved = bridge.save_workflow("demo", [
        {"role_ref": analyst["id"]}, {"role_ref": gate["id"]}, {"role_ref": writer["id"]},
    ], name="ChatGPT team")
    bridge.configure_workflow("demo", saved["id"])
    bridge.authorize_project_local("demo")
    task_id = bridge.start_project_request("demo", "Add member login")["workstream"]["id"]
    return bridge, task_id, {"analyst": analyst, "gate": gate, "writer": writer}


def test_a_chatgpt_team_runs_from_first_report_to_completion(team):
    bridge, task_id, roles = team
    first = active(bridge, task_id)
    assert (first["agent"], first["adapter"]) == ("chatgpt", "interactive")
    assert first["role_ref"] == roles["analyst"]["id"] and first["step_id"] == "001-s00"

    recorded = bridge.submit_agent_report("demo", first["id"], report("Analysis"))
    assert recorded["status"] == "RECORDED" and recorded["auto_advanced"] is False  # SA needs the PM
    assert bridge.task("demo", task_id)["state"] == "AWAITING_PM_DECISION"
    refine(bridge, first["id"])

    gate = active(bridge, task_id)
    assert (gate["role"], gate["role_ref"], gate["step_id"]) == (
        "AR", roles["gate"]["id"], "001-s01")
    advanced = bridge.submit_agent_report("demo", gate["id"], report("No flaws found"))
    assert advanced["auto_advanced"] is True                       # a gate hands over by itself

    writer = active(bridge, task_id)
    assert (writer["role"], writer["role_ref"], writer["step_id"]) == (
        "SW", roles["writer"]["id"], "001-s02")
    done = bridge.submit_agent_report(
        "demo", writer["id"], report("Spec", document="# Spec\n\nREQ-001 MUST log in."))
    assert done["auto_advanced"] is True
    assert bridge.task("demo", task_id)["state"] == "COMPLETED"

    # who ran, in which step, under which frozen snapshot
    runs = all_runs(bridge, task_id)
    flow = bridge._task_flow("demo", task_id)
    assert [(r["role_ref"], r["step_id"], r["role_snapshot"]) for r in runs] == [
        (s["role_ref"], s["step_id"], s["snapshot_id"]) for s in flow["steps"]]
    assert {r["status"] for r in runs} == {"SUCCEEDED"}
    for run in runs:
        assert role_prompt.extract_operator_text(run["instruction"])
        offered = bridge.execution_prompt("demo", run["id"])
        assert offered["via"] == "chatgpt_handoff" and offered["provider"] == "chatgpt"
    # the gate verdict and the per-step reports carry the identity
    verdicts = bridge.flow_gate_history("demo", task_id)
    assert [(g["role_ref"], g["step_id"], g["verdict"]) for g in verdicts] == [
        (roles["gate"]["id"], "001-s01", "PASS")]
    paths = {r["path"].split("/tasks/", 1)[1] for r in bridge.store.all(
        "SELECT path FROM artifacts WHERE project_id='demo'")}
    assert f"{task_id}/SW-REPORT--{roles['writer']['id']}--001-s02.md" in paths
    assert f"{task_id}/SPEC.md" in paths
    # the approval is still the one given for this exact team
    approval = bridge.store.one("SELECT * FROM approvals WHERE task_id=?", (task_id,))
    pin = bridge.current_roster_pin("demo", task_id)
    assert approval["status"] == "APPROVED"
    assert bridge.approval_roster("demo", approval["id"])["roster_hash"] == pin["roster_hash"]
    assert bridge.roster_matches_approval("demo", task_id, approval["id"])


def test_a_chatgpt_gate_that_blocks_sends_the_same_custom_step_back(team):
    bridge, task_id, roles = team
    first = active(bridge, task_id)
    bridge.submit_agent_report("demo", first["id"], report("Analysis"))
    refine(bridge, first["id"])
    gate = active(bridge, task_id)
    verdict = bridge.submit_agent_report("demo", gate["id"], report(
        "Found a problem", needs_revision=True,
        findings=["[major] No lockout rule | evidence: spec.md:3"]))
    assert verdict["auto_advanced"] is True

    redo = active(bridge, task_id)
    assert redo["id"] != first["id"]
    assert (redo["role_ref"], redo["step_id"], redo["role_snapshot"]) == (
        first["role_ref"], first["step_id"], first["role_snapshot"])     # the same step again
    assert "No lockout rule" in redo["instruction"]
    assert role_prompt.extract_operator_text(redo["instruction"]) == (
        "Look for hidden requirements.")
    history = bridge.flow_gate_history("demo", task_id)
    assert [(g["role_ref"], g["step_id"], g["verdict"]) for g in history] == [
        (roles["gate"]["id"], "001-s01", "NEEDS_REVISION")]
    assert bridge.task("demo", task_id)["state"] != "COMPLETED"
    assert bridge.get_task_status("demo", task_id)["brief"]["waiting_on"] == "CHATGPT_HANDOFF_PENDING"


def test_editing_a_role_while_chatgpt_works_does_not_change_the_next_step(team):
    bridge, task_id, roles = team
    first = active(bridge, task_id)
    bridge.submit_agent_report("demo", first["id"], report("Analysis"))
    bridge.update_custom_role(
        "demo", roles["gate"]["id"],
        {"prompt": {"freeform": "A completely different gate."}}, expected_version=1)
    refine(bridge, first["id"])
    gate = active(bridge, task_id)
    assert role_prompt.extract_operator_text(gate["instruction"]) == (
        "Hunt for flaws, cite evidence.")
    assert bridge.role_snapshot("demo", gate["role_snapshot"])["content"]["version"] == 1


def test_a_custom_role_played_by_chatgpt_is_as_read_only_as_its_base(team):
    bridge, task_id, roles = team
    first = active(bridge, task_id)                              # a custom analyst (SA)
    with pytest.raises(PermissionDenied, match="read-only regardless of provider"):
        bridge.write_task_file("demo", first["id"], "probe.txt", "x")
    bridge.submit_agent_report("demo", first["id"], report("Analysis"))
    refine(bridge, first["id"])
    gate = active(bridge, task_id)                               # a custom adversarial gate
    with pytest.raises(PermissionDenied, match="read-only regardless of provider"):
        bridge.write_task_file("demo", gate["id"], "probe.txt", "x")
    bridge.submit_agent_report("demo", gate["id"], report("Fine"))
    writer = active(bridge, task_id)                             # a custom specification writer
    with pytest.raises(PermissionDenied, match="read-only regardless of provider"):
        bridge.write_task_file("demo", writer["id"], "probe.txt", "x")
    assert writer["worktree"] is None and not json.loads(writer["options"]).get("read_only")


def test_a_report_for_another_execution_cannot_move_a_custom_step_forward(team):
    bridge, task_id, roles = team
    first = active(bridge, task_id)
    bridge.submit_agent_report("demo", first["id"], report("Analysis"))
    with pytest.raises(PermissionDenied, match="no longer active"):
        bridge.submit_agent_report("demo", first["id"], report("Again"))   # a replay
