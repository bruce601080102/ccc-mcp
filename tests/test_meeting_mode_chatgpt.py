"""Meeting Mode when ChatGPT performs the roles itself (the chatgpt-only profile).

SD/RD's read-only check and SA's answer become interactive handoffs the connected ChatGPT
conversation completes with submit_clarification_request / submit_clarification_response,
and the exchange continues automatically, exactly as it does for native CLI roles.
"""

import sys

import pytest
from pydantic import ValidationError

from ccc_mcp.infra.errors import BridgeError, PermissionDenied


def report(**kwargs):
    from ccc_mcp.core.models import AgentReport

    return AgentReport(summary="Observed source", next_action="PM refine", **kwargs).model_dump()


def pending_patch(bridge, execution_id):
    item = next(
        r for r in bridge.latest("demo")["snapshot"]["agent_results"]
        if r["execution_id"] == execution_id
    )
    return {
        "base_context_version": bridge.latest("demo")["version"],
        "changes": {"summary": f"PM reviewed {item['role']} evidence."},
        "source_execution_ids": [execution_id],
        "source_task_ids": [item["task_id"]],
        "source_document_refs": [item["source_document_ref"]],
        "update_reason": "PM verified the completed Agent result",
        "semantic_review_ack": True,
    }


def to_ask(bridge, *, min_rounds=0, max_rounds=3, escalation=None):
    """chatgpt-only project with Meeting Mode on, advanced to SD/RD's clarification check."""
    bridge.configure_profile("demo", "chatgpt-only")
    bridge.authorize_project_local("demo")
    bridge.configure_test_command("demo", [sys.executable, "-c", "print('ok')"])
    bridge.configure_meeting_mode("demo", True, min_rounds, max_rounds, escalation)
    started = bridge.start_project_request("demo", "Implement a change")
    task_id, sa = started["workstream"]["id"], started["sa_execution"]["execution_id"]
    bridge.submit_agent_report("demo", sa, report(context_updates=["SA analysis"]))
    advanced = bridge.advance_project("demo", pending_patch(bridge, sa), "Approved plan content")
    return task_id, advanced["next_execution"]["execution_id"]


def handoff(bridge, task_id):
    return bridge.task_handoff("demo", task_id)


def test_sd_rd_check_is_an_interactive_read_only_handoff(bridge, human):
    task_id, ask = to_ask(bridge)
    h = handoff(bridge, task_id)
    assert h["waiting_on"] == "CHATGPT_HANDOFF_PENDING" and h["role"] == "SD/RD"
    assert h["execution_id"] == ask
    assert h["expected_report_fields"] == ["blocking_question", "no_blocking_question", "rationale"]
    assert "submit_clarification_request" in h["chatgpt_prompt"]
    assert "不要使用 submit_agent_report" in h["chatgpt_prompt"]
    assert "write_task_file" not in h["chatgpt_prompt"]
    # Read-only like SA/REVIEW: no worktree was claimed and writes are refused.
    row = bridge.store.one("SELECT worktree FROM agent_executions WHERE id=?", (ask,))
    assert row["worktree"] is None
    with pytest.raises(PermissionDenied, match="read-only"):
        bridge.write_task_file("demo", ask, "x.py", "print(1)")


def test_wrong_submit_tool_points_at_the_right_one(bridge, human):
    task_id, ask = to_ask(bridge)
    with pytest.raises(BridgeError, match="submit_clarification_request"):
        bridge.submit_agent_report("demo", ask, report())
    with pytest.raises(ValidationError):  # a SA-shaped answer is not a SD/RD request
        bridge.submit_clarification("demo", ask, {"response_type": "confirmed", "content": "x"})


def test_question_then_answer_then_real_implementation_all_as_chatgpt(bridge, human):
    task_id, ask = to_ask(bridge)
    sent = bridge.submit_clarification(
        "demo", ask,
        {"blocking_question": "Where should the file go?", "no_blocking_question": False,
         "rationale": "Plan does not say"},
    )
    # SA's answer was dispatched automatically, again as an interactive ChatGPT handoff.
    answer = sent["next_execution"]["execution_id"]
    h = handoff(bridge, task_id)
    assert h["role"] == "SA" and h["execution_id"] == answer
    assert h["expected_report_fields"] == ["response_type", "content"]
    assert "submit_clarification_response" in h["chatgpt_prompt"]
    assert "Where should the file go?" in h["instruction"]
    assert bridge.task("demo", task_id)["state"] == "AWAITING_SA_CLARIFICATION"

    done = bridge.submit_clarification(
        "demo", answer, {"response_type": "confirmed", "content": "Repository root."}
    )
    impl = done["next_execution"]["execution_id"]
    h = handoff(bridge, task_id)
    assert h["role"] == "SD/RD" and h["execution_id"] == impl
    assert h["expected_report_fields"][0] == "summary"  # the real, writable implementation
    assert "write_task_file" in h["chatgpt_prompt"]

    clar = bridge.get_task_status("demo", task_id)["brief"]["clarification"]
    assert clar["triggered"] is True and clar["rounds_used"] == 1
    assert clar["history"][0]["response_content"] == "Repository root."
    assert "Meeting Mode 已觸發" in handoff(bridge, task_id)["meeting_mode_notice"]["text"]


def test_no_blocking_question_goes_straight_to_implementation(bridge, human):
    task_id, ask = to_ask(bridge)
    sent = bridge.submit_clarification(
        "demo", ask, {"blocking_question": None, "no_blocking_question": True, "rationale": "Clear"}
    )
    assert handoff(bridge, task_id)["execution_id"] == sent["next_execution"]["execution_id"]
    assert handoff(bridge, task_id)["expected_report_fields"][0] == "summary"
    gate = bridge.get_task_status("demo", task_id)["brief"]["clarification"]["gate"]
    assert gate["outcome"] == "no_blocking_question"


def test_pm_decision_required_stops_for_the_decision_policy(bridge, human):
    task_id, ask = to_ask(bridge)
    sent = bridge.submit_clarification(
        "demo", ask, {"blocking_question": "Which DB?", "no_blocking_question": False, "rationale": "r"}
    )
    bridge.submit_clarification(
        "demo", sent["next_execution"]["execution_id"],
        {"response_type": "pm_decision_required", "content": "Needs a human call."},
    )
    h = handoff(bridge, task_id)
    assert h["waiting_on"] == "PM_CLARIFICATION_DECISION"
    assert "ask-user" in h["next_action"]
    bridge.resolve_clarification("demo", task_id, "Use SQLite.")
    assert handoff(bridge, task_id)["role"] == "SD/RD"


def test_min_rounds_makes_a_chatgpt_sd_rd_ask_again_until_met(bridge, human):
    task_id, ask = to_ask(bridge, min_rounds=2)
    assert "MUST raise exactly one question" in handoff(bridge, task_id)["instruction"]
    for round_number in (1, 2):
        sent = bridge.submit_clarification(
            "demo", ask,
            {"blocking_question": f"Q{round_number}?", "no_blocking_question": False, "rationale": "r"},
        )
        done = bridge.submit_clarification(
            "demo", sent["next_execution"]["execution_id"],
            {"response_type": "confirmed", "content": f"A{round_number}"},
        )
        ask = done["next_execution"]["execution_id"]
    # Two rounds are done, so the next handoff is the real implementation.
    assert handoff(bridge, task_id)["expected_report_fields"][0] == "summary"
    assert bridge.get_task_status("demo", task_id)["brief"]["clarification"]["rounds_used"] == 2


def test_agent_report_executions_are_unaffected(bridge, human):
    bridge.configure_profile("demo", "chatgpt-only")
    bridge.authorize_project_local("demo")
    started = bridge.start_project_request("demo", "Implement a change")
    sa = started["sa_execution"]["execution_id"]
    with pytest.raises(BridgeError, match="expects an AgentReport"):
        bridge.submit_clarification(
            "demo", sa, {"blocking_question": None, "no_blocking_question": True, "rationale": "r"}
        )
    assert bridge.submit_agent_report("demo", sa, report())["status"] == "RECORDED"


def test_handoff_prompt_mentions_installed_mcp_for_chatgpt(bridge, human, tmp_path):
    script = tmp_path / "srv.py"
    script.write_text(
        "from mcp.server import MCPServer\ns = MCPServer('m')\n"
        "@s.tool()\ndef ping() -> str:\n    return 'pong'\ns.run(transport='stdio')\n",
        encoding="utf-8",
    )
    bridge.install_mcp_server("demo", "m", command=sys.executable, args=[str(script)])
    task_id, _ = to_ask(bridge)
    prompt = handoff(bridge, task_id)["chatgpt_prompt"]
    assert "call_installed_mcp_tool" in prompt and "（m）" in prompt
