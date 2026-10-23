import pytest

from ccc_mcp.adapters.agents.base import parse_report
from ccc_mcp.core.models import ClarificationRequest, ClarificationResponse
from ccc_mcp.infra.errors import BridgeError
from ccc_mcp.worker import run_execution


def dispatch_and_run(
    bridge, task_id, role, report_schema="agent_report", fake_scenario="success",
    clarification_text=None,
):
    result = bridge.dispatch_agent(
        "demo",
        task_id,
        "codex" if role in {"SA", "REVIEW"} else "claude",
        role,
        bridge.latest("demo")["version"],
        "Perform role",
        adapter="fake",
        fake_scenario=fake_scenario,
        report_schema=report_schema,
        clarification_text=clarification_text,
    )
    run_execution(bridge, result["execution_id"])
    return result["execution_id"]


def pending_patch(bridge, execution_id):
    result = next(
        item
        for item in bridge.latest("demo")["snapshot"]["agent_results"]
        if item["execution_id"] == execution_id
    )
    return {
        "base_context_version": bridge.latest("demo")["version"],
        "changes": {"summary": f"PM reviewed {result['role']} evidence."},
        "source_execution_ids": [execution_id],
        "source_task_ids": [result["task_id"]],
        "source_document_refs": [result["source_document_ref"]],
        "update_reason": "PM verified the completed Agent result",
        "semantic_review_ack": True,
    }


def start_task(bridge, requirement="Implement a change"):
    bridge.authorize_project_local("demo")
    task_id = bridge.create_task("demo", requirement)["id"]
    sa = dispatch_and_run(bridge, task_id, "SA")
    return task_id, sa


def advance_after_sa(bridge, task_id, sa_execution, fake_scenario):
    """The real production entry point for the once-per-task gate: PM refines SA's
    result via advance_project, which internally decides (via
    _start_implementation_or_clarification) whether to dispatch Meeting Mode's
    clarification-ask or the normal writable implementation. `fake_scenario` is
    whichever one of those two dispatches is actually about to happen."""
    patch = pending_patch(bridge, sa_execution)
    result = bridge.advance_project(
        "demo", patch, "Approved plan content", adapter="fake", fake_scenario=fake_scenario
    )
    execution_id = result["next_execution"]["execution_id"]
    run_execution(bridge, execution_id)
    return execution_id


def clarification_row(bridge, task_id):
    return bridge.store.one(
        "SELECT * FROM meeting_clarifications WHERE project_id='demo' AND task_id=? "
        "ORDER BY round DESC LIMIT 1",
        (task_id,),
    )


def report_schema_of(bridge, execution_id):
    import json

    row = bridge.store.one("SELECT options FROM agent_executions WHERE id=?", (execution_id,))
    return json.loads(row["options"]).get("report_schema", "agent_report")


@pytest.mark.parametrize(
    "payload",
    [
        '{"blocking_question":"Which entry point?","no_blocking_question":true,"rationale":"Need input"}',
        '{"blocking_question":null,"no_blocking_question":true,"rationale":"   "}',
    ],
)
def test_clarification_request_rejects_ambiguous_or_blank_outcomes(payload):
    with pytest.raises(BridgeError, match="invalid structured report"):
        parse_report(payload, ClarificationRequest)


def test_clarification_request_normalizes_a_question():
    report = parse_report(
        '{"blocking_question":"  Which entry point?  ","no_blocking_question":false,'
        '"rationale":"  It determines the change scope.  "}',
        ClarificationRequest,
    )
    assert report == {
        "blocking_question": "Which entry point?",
        "no_blocking_question": False,
        "rationale": "It determines the change scope.",
    }


@pytest.mark.parametrize("question", ["", "   ", None])
def test_clarification_request_without_a_question_proceeds_without_a_round(question):
    import json

    report = parse_report(
        json.dumps(
            {
                "blocking_question": question,
                # Native CLIs may emit this schema default even when no question exists.
                "no_blocking_question": False,
                "rationale": "The plan is sufficiently specific.",
            }
        ),
        ClarificationRequest,
    )
    assert report["blocking_question"] is None
    assert report["no_blocking_question"] is True


def test_clarification_response_rejects_blank_content():
    with pytest.raises(BridgeError, match="invalid structured report"):
        parse_report(
            '{"response_type":"confirmed","content":"  \t\\n  "}', ClarificationResponse
        )


# --- settings validation -----------------------------------------------------


def test_meeting_mode_defaults_off(bridge):
    assert bridge.get_project_settings("demo")["meeting_mode"] == {
        "enabled": False, "min_rounds": 0, "max_rounds": 5, "escalation": "ask_user",
    }


def test_configure_meeting_mode_validates_bounds(bridge):
    bridge.configure_meeting_mode("demo", True, 1, 3)
    assert bridge.get_project_settings("demo")["meeting_mode"] == {
        "enabled": True, "min_rounds": 1, "max_rounds": 3, "escalation": "ask_user",
    }
    with pytest.raises(BridgeError, match="min_rounds <= max_rounds"):
        bridge.configure_meeting_mode("demo", True, 3, 1)
    with pytest.raises(BridgeError, match="<= 5"):
        bridge.configure_meeting_mode("demo", True, 0, 6)
    with pytest.raises(BridgeError, match="<= 5"):
        bridge.configure_meeting_mode("demo", True, 0, -1)


def test_configure_meeting_mode_partial_update_keeps_existing_rounds(bridge):
    bridge.configure_meeting_mode("demo", True, 2, 4)
    bridge.configure_meeting_mode("demo", False)
    assert bridge.get_project_settings("demo")["meeting_mode"] == {
        "enabled": False, "min_rounds": 2, "max_rounds": 4, "escalation": "ask_user",
    }


def test_configure_meeting_mode_escalation_validates_and_is_preserved(bridge):
    bridge.configure_meeting_mode("demo", True, escalation="auto_pm")
    assert bridge.get_project_settings("demo")["meeting_mode"]["escalation"] == "auto_pm"
    # Omitting escalation on a later call keeps the previously configured value.
    bridge.configure_meeting_mode("demo", True, min_rounds=1)
    assert bridge.get_project_settings("demo")["meeting_mode"]["escalation"] == "auto_pm"
    with pytest.raises(BridgeError, match="escalation"):
        bridge.configure_meeting_mode("demo", True, escalation="auto_decide")


# --- Meeting Mode off: zero behavior change ----------------------------------


def test_meeting_mode_off_dispatches_real_implementation_immediately(bridge, human):
    task_id, sa = start_task(bridge)
    impl_execution = advance_after_sa(bridge, task_id, sa, fake_scenario="success")
    assert report_schema_of(bridge, impl_execution) == "agent_report"
    # A real (non-Meeting-Mode) implementation ran and succeeded: awaiting PM review,
    # exactly as before Meeting Mode existed.
    assert bridge.task("demo", task_id)["state"] == "AWAITING_PM_REVIEW"
    assert clarification_row(bridge, task_id) is None
    brief = bridge.get_task_status("demo", task_id)["brief"]
    assert brief["clarification"]["enabled"] is False
    assert brief["clarification"]["rounds_used"] == 0
    assert brief["clarification"]["triggered"] is False
    assert brief["clarification"]["gate"] is None


# --- no_blocking_question: proceeds straight to implementation --------------


def test_no_blocking_question_skips_straight_to_implementation(bridge, human):
    bridge.configure_meeting_mode("demo", True, 0, 5)
    task_id, sa = start_task(bridge)
    ask_execution = advance_after_sa(bridge, task_id, sa, fake_scenario="no_blocking_question")
    assert report_schema_of(bridge, ask_execution) == "clarification_request"
    # Resolved as IMPLEMENTING (no-op transition) with no round recorded at all.
    assert bridge.task("demo", task_id)["state"] == "IMPLEMENTING"
    assert clarification_row(bridge, task_id) is None
    brief = bridge.get_task_status("demo", task_id)["brief"]
    assert brief["clarification"]["rounds_used"] == 0
    assert brief["clarification"]["current_question"] is None
    # no_blocking_question never inserts a meeting_clarifications row, so the gate
    # never "actually fired" even though Meeting Mode is enabled for the project.
    assert brief["clarification"]["triggered"] is False
    # ...but the check itself visibly ran and found nothing to ask, so "checked, no
    # question" is distinguishable from "Meeting Mode never ran".
    gate = brief["clarification"]["gate"]
    assert gate["status"] == "SUCCEEDED"
    assert gate["outcome"] == "no_blocking_question"
    assert gate["rationale"]
    assert bridge.task_handoff("demo", task_id)["clarification"]["gate"] == gate

    # A successful no-question gate is durable. Re-deriving the next SD/RD dispatch
    # must now choose the writable AgentReport path rather than endlessly asking the
    # same read-only clarification (the real worker does this automatically).
    next_execution = bridge._start_implementation_or_clarification(
        "demo", task_id, "fake", "success"
    )
    assert report_schema_of(bridge, next_execution["execution_id"]) == "agent_report"


# --- a real blocking question, answered confirmed/recommendation ------------


@pytest.mark.parametrize("response_type", ["confirmed", "recommendation"])
def test_blocking_question_answered_directly_resolves_the_round(bridge, human, response_type):
    bridge.configure_meeting_mode("demo", True, 0, 5)
    task_id, sa = start_task(bridge)
    ask_execution = advance_after_sa(bridge, task_id, sa, fake_scenario="ask_question")
    assert report_schema_of(bridge, ask_execution) == "clarification_request"
    assert bridge.task("demo", task_id)["state"] == "AWAITING_SA_CLARIFICATION"
    row = clarification_row(bridge, task_id)
    assert row["status"] == "AWAITING_SA"
    assert row["question"]
    # A real blocking question was asked: the gate has already "actually fired," even
    # before SA answers it.
    assert bridge.get_task_status("demo", task_id)["brief"]["clarification"]["triggered"] is True
    gate = bridge.get_task_status("demo", task_id)["brief"]["clarification"]["gate"]
    assert gate["outcome"] == "question_asked"

    dispatch_and_run(
        bridge, task_id, "SA", report_schema="clarification_response", fake_scenario=response_type
    )

    assert bridge.task("demo", task_id)["state"] == "IMPLEMENTING"
    row = clarification_row(bridge, task_id)
    assert row["status"] == "RESOLVED"
    assert row["response_type"] == response_type
    brief = bridge.get_task_status("demo", task_id)["brief"]
    assert brief["clarification"]["rounds_used"] == 1
    assert brief["clarification"]["current_question"] is None
    assert brief["clarification"]["triggered"] is True
    assert brief["waiting_on"] not in {"PM_CLARIFICATION_DECISION"}


# --- pm_decision_required: escalates and blocks until resolve_clarification -


def test_pm_decision_required_escalates_and_blocks(bridge, human):
    bridge.configure_meeting_mode("demo", True, 0, 5)
    task_id, sa = start_task(bridge)
    advance_after_sa(bridge, task_id, sa, fake_scenario="ask_question")
    dispatch_and_run(
        bridge, task_id, "SA", report_schema="clarification_response",
        fake_scenario="pm_decision_required", clarification_text="Needs PM input.",
    )

    assert bridge.task("demo", task_id)["state"] == "AWAITING_PM_DECISION"
    brief = bridge.get_task_status("demo", task_id)["brief"]
    assert brief["waiting_on"] == "PM_CLARIFICATION_DECISION"
    assert brief["clarification"]["triggered"] is True
    pending = brief["clarification"]["pending_pm_decision"]
    assert pending["sa_recommendation"] == "Needs PM input."

    # task_handoff (what wait_for_task/the main ChatGPT loop actually sees) carries
    # the exact same clarification block get_task_status's brief does.
    handoff = bridge.task_handoff("demo", task_id)
    assert handoff["clarification"] == brief["clarification"]
    # Default escalation ("ask_user"): this is a genuine stop requiring the real
    # human's decision, and the next_action must say so, never silently decide for them.
    assert "ask-user" in handoff["next_action"]
    assert "不可由你自行判斷" in handoff["next_action"]

    bridge.configure_meeting_mode("demo", True, escalation="auto_pm")
    auto_handoff = bridge.task_handoff("demo", task_id)
    assert auto_handoff["clarification"]["escalation"] == "auto_pm"
    assert "auto-pm" in auto_handoff["next_action"]
    assert "可直接依自己的判斷決定" in auto_handoff["next_action"]
    assert auto_handoff["next_action"] != handoff["next_action"]

    with pytest.raises(BridgeError, match="Decision content is required"):
        bridge.resolve_clarification("demo", task_id, "")

    result = bridge.resolve_clarification("demo", task_id, "Use option B.")
    assert result["decision_recorded"] is True
    assert bridge.task("demo", task_id)["state"] == "IMPLEMENTING"
    row = clarification_row(bridge, task_id)
    assert row["status"] == "RESOLVED"
    assert row["pm_decision_content"] == "Use option B."
    brief = bridge.get_task_status("demo", task_id)["brief"]
    assert brief["clarification"]["rounds_used"] == 1
    # resolve_clarification already dispatched the next round/implementation.
    assert result["next_execution"]["execution_id"]

    with pytest.raises(BridgeError, match="not awaiting a Meeting Mode PM clarification"):
        bridge.resolve_clarification("demo", task_id, "Too late.")


# --- max_rounds: forced into implementation, never asks again ---------------


def test_max_rounds_forces_implementation_without_asking_again(bridge, human):
    bridge.configure_meeting_mode("demo", True, 0, 1)
    task_id, sa = start_task(bridge)
    advance_after_sa(bridge, task_id, sa, fake_scenario="ask_question")
    dispatch_and_run(
        bridge, task_id, "SA", report_schema="clarification_response", fake_scenario="confirmed"
    )
    assert bridge.get_task_status("demo", task_id)["brief"]["clarification"]["rounds_used"] == 1

    # Budget (max_rounds=1) is exhausted: the next SD/RD dispatch must be the real,
    # writable implementation, never another clarification-ask.
    next_execution = bridge._start_implementation_or_clarification(
        "demo", task_id, "fake", "success"
    )
    assert report_schema_of(bridge, next_execution["execution_id"]) == "agent_report"


# --- min_rounds never deadlocks: a declined check still proceeds --------------


def test_min_rounds_never_blocks_no_blocking_question(bridge, human):
    bridge.configure_meeting_mode("demo", True, 3, 5)
    task_id, sa = start_task(bridge)
    ask_execution = advance_after_sa(bridge, task_id, sa, fake_scenario="no_blocking_question")
    # Succeeded with zero rounds despite min_rounds=3 — advisory, never a gate.
    assert report_schema_of(bridge, ask_execution) == "clarification_request"
    assert bridge.task("demo", task_id)["state"] == "IMPLEMENTING"
    brief = bridge.get_task_status("demo", task_id)["brief"]
    assert brief["clarification"]["rounds_used"] == 0
    assert brief["clarification"]["min_rounds"] == 3
    # The task can still proceed all the way to REVIEW/completion from here.
    impl = dispatch_and_run(bridge, task_id, "SD/RD", fake_scenario="success")
    patch = pending_patch(bridge, impl)
    result = bridge.advance_project("demo", patch, adapter="fake", fake_scenario="success")
    assert result["next_execution"]["execution_id"]


# --- structural read-only enforcement for the clarification-ask -------------


def test_clarification_ask_dispatch_is_never_write_capable(bridge, human):
    bridge.configure_meeting_mode("demo", True, 0, 5)
    task_id, sa = start_task(bridge)
    ask_execution = advance_after_sa(bridge, task_id, sa, fake_scenario="ask_question")
    row = bridge.store.one("SELECT * FROM agent_executions WHERE id=?", (ask_execution,))
    # No worktree was ever prepared for this execution (worker.py's `implementation`
    # flag — and therefore all worktree/project-workspace prep — is false whenever
    # report_schema != "agent_report", regardless of role).
    assert row["worktree"] is None


# --- the gate only ever fires once per task ----------------------------------


def test_gate_never_retriggers_after_a_review_requested_revision(bridge, human):
    bridge.configure_meeting_mode("demo", True, 0, 5)
    task_id, sa = start_task(bridge)
    advance_after_sa(bridge, task_id, sa, fake_scenario="no_blocking_question")
    impl = dispatch_and_run(bridge, task_id, "SD/RD", fake_scenario="success")

    # fake_scenario="revision" is REVIEW's own scenario: it will report
    # needs_revision=True once run, so the task reaches NEEDS_REVISION.
    patch = pending_patch(bridge, impl)
    review_result = bridge.advance_project("demo", patch, adapter="fake", fake_scenario="revision")
    review_execution = review_result["next_execution"]["execution_id"]
    assert report_schema_of(bridge, review_execution) == "agent_report"
    run_execution(bridge, review_execution)
    assert bridge.task("demo", task_id)["state"] == "NEEDS_REVISION"

    # PM refines REVIEW's result; _complete_pending_dispatch's REVIEW branch
    # (_review_outcome) decides the next SD/RD dispatch — it never goes through
    # _start_implementation_or_clarification, so this must still be a normal,
    # writable implementation dispatch, never another clarification-ask, even
    # though Meeting Mode is enabled and this is the second SD/RD dispatch overall.
    revise_patch = pending_patch(bridge, review_execution)
    result = bridge.advance_project("demo", revise_patch, adapter="fake", fake_scenario="success")
    assert report_schema_of(bridge, result["next_execution"]["execution_id"]) == "agent_report"


# --- meeting_mode_notice: a ready-to-quote line inside every handoff/wait result ---


def test_notice_is_absent_when_meeting_mode_is_off(bridge, human):
    task_id, _ = start_task(bridge)
    assert bridge.task_handoff("demo", task_id)["meeting_mode_notice"] is None
    assert bridge.wait_for_task("demo", task_id, 0)["meeting_mode_notice"] is None


def test_notice_says_enabled_before_the_gate_has_run(bridge, human):
    bridge.configure_meeting_mode("demo", True, 1, 3)
    task_id, _ = start_task(bridge)
    notice = bridge.task_handoff("demo", task_id)["meeting_mode_notice"]
    assert "Meeting Mode 已啟用" in notice["text"]
    assert "你決定" in notice["text"]
    assert "first line" in notice["rule"]


def test_notice_reports_a_no_question_check_with_rationale(bridge, human):
    bridge.configure_meeting_mode("demo", True, 1, 3)
    task_id, sa = start_task(bridge)
    advance_after_sa(bridge, task_id, sa, fake_scenario="no_blocking_question")
    waited = bridge.wait_for_task("demo", task_id, 0)
    text = waited["meeting_mode_notice"]["text"]
    assert "沒有要問的" in text
    assert "\n" not in text and len(text) < 200  # one short line
    assert waited["handoff"]["meeting_mode_notice"]["text"] == text


def test_notice_summarizes_the_sa_sd_exchange_once_triggered(bridge, human):
    bridge.configure_meeting_mode("demo", True, 0, 5)
    task_id, sa = start_task(bridge)
    advance_after_sa(bridge, task_id, sa, fake_scenario="ask_question")
    dispatch_and_run(
        bridge, task_id, "SA", report_schema="clarification_response",
        fake_scenario="pm_decision_required", clarification_text="Needs PM input.",
    )
    text = bridge.task_handoff("demo", task_id)["meeting_mode_notice"]["text"]
    assert "Meeting Mode 已觸發" in text
    assert "SD/RD 問" in text
    assert "Needs PM input." in text


# --- min_rounds is enforced: SD/RD owes rounds until it has asked that many ---


def test_min_rounds_requires_another_round_until_met(bridge, human):
    bridge.configure_meeting_mode("demo", True, 2, 5)
    task_id, sa = start_task(bridge)
    first = advance_after_sa(bridge, task_id, sa, fake_scenario="ask_question")
    instruction = bridge.store.one(
        "SELECT instruction FROM agent_executions WHERE id=?", (first,)
    )["instruction"]
    assert "MUST raise exactly one question" in instruction
    dispatch_and_run(
        bridge, task_id, "SA", report_schema="clarification_response", fake_scenario="confirmed"
    )

    # 1 of 2 required rounds done: the next SD/RD dispatch is another read-only ask.
    second = bridge._start_implementation_or_clarification("demo", task_id, "fake", "ask_question")
    assert report_schema_of(bridge, second["execution_id"]) == "clarification_request"
    run_execution(bridge, second["execution_id"])
    dispatch_and_run(
        bridge, task_id, "SA", report_schema="clarification_response", fake_scenario="confirmed"
    )

    # 2 of 2 done: now (and only now) the writable implementation follows.
    third = bridge._start_implementation_or_clarification("demo", task_id, "fake", "success")
    assert report_schema_of(bridge, third["execution_id"]) == "agent_report"
    assert bridge.get_task_status("demo", task_id)["brief"]["clarification"]["rounds_used"] == 2


def test_min_rounds_unmet_is_reported_when_sd_rd_still_declines(bridge, human):
    bridge.configure_meeting_mode("demo", True, 2, 5)
    task_id, sa = start_task(bridge)
    advance_after_sa(bridge, task_id, sa, fake_scenario="no_blocking_question")
    clarification = bridge.get_task_status("demo", task_id)["brief"]["clarification"]
    assert clarification["gate"]["min_rounds_unmet"] is True
    assert "min_rounds" in bridge.task_handoff("demo", task_id)["meeting_mode_notice"]["text"]
    # A declined check is final: the workflow proceeds to real implementation.
    nxt = bridge._start_implementation_or_clarification("demo", task_id, "fake", "success")
    assert report_schema_of(bridge, nxt["execution_id"]) == "agent_report"


def test_meeting_transcript_is_complete_and_unclipped(bridge, human):
    bridge.configure_meeting_mode("demo", True, 0, 5)
    task_id, sa = start_task(bridge)
    advance_after_sa(bridge, task_id, sa, fake_scenario="ask_question")
    dispatch_and_run(
        bridge, task_id, "SA", report_schema="clarification_response",
        fake_scenario="recommendation", clarification_text="X" * 500,
    )
    transcript = bridge.meeting_transcript("demo", task_id)
    assert "X" * 500 in transcript["markdown"]  # never clipped, unlike the notice
    assert "SD/RD →" in transcript["markdown"] and "SA (recommendation) →" in transcript["markdown"]
    assert "get_meeting_transcript" in bridge.task_handoff("demo", task_id)["meeting_mode_notice"]["text"]


# --- a direct dispatch_agent for SD/RD cannot skip the gate ------------------


def test_direct_dispatch_agent_for_sd_rd_runs_the_owed_gate_first(bridge, human):
    bridge.configure_meeting_mode("demo", True, 2, 3)
    task_id, sa = start_task(bridge)
    advance_after_sa(bridge, task_id, sa, fake_scenario="ask_question")
    dispatch_and_run(
        bridge, task_id, "SA", report_schema="clarification_response", fake_scenario="confirmed"
    )
    # 1 of 2 required rounds is done. A PM that bypasses advance_project and calls
    # dispatch_agent for SD/RD directly must still get the owed check, not real code.
    result = bridge.dispatch_agent(
        "demo", task_id, "auto", "SD/RD", bridge.latest("demo")["version"], "Implement it",
        adapter="fake", fake_scenario="success",
    )
    assert result["meeting_mode_redirect"]
    assert report_schema_of(bridge, result["execution_id"]) == "clarification_request"


def test_direct_dispatch_agent_is_untouched_when_meeting_mode_is_off(bridge, human):
    task_id, sa = start_task(bridge)
    advance_after_sa(bridge, task_id, sa, fake_scenario="success")
    # Normal path: no redirect key, plain implementation dispatch semantics unchanged.
    task = bridge.task("demo", task_id)
    assert task["state"] in {"AWAITING_PM_REVIEW", "IMPLEMENTING"}
