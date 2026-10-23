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
        "enabled": False, "min_rounds": 0, "max_rounds": 5,
    }


def test_configure_meeting_mode_validates_bounds(bridge):
    bridge.configure_meeting_mode("demo", True, 1, 3)
    assert bridge.get_project_settings("demo")["meeting_mode"] == {
        "enabled": True, "min_rounds": 1, "max_rounds": 3,
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
        "enabled": False, "min_rounds": 2, "max_rounds": 4,
    }


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
    pending = brief["clarification"]["pending_pm_decision"]
    assert pending["sa_recommendation"] == "Needs PM input."

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


# --- min_rounds is advisory only: never blocks NO_BLOCKING_QUESTION ---------


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


def test_dispatch_agent_rejects_clarification_schema_for_chatgpt(bridge, human):
    bridge.authorize_project_local("demo")
    bridge.configure_role("demo", "SD/RD", "chatgpt")
    task_id = bridge.create_task("demo", "Implement a change")["id"]
    with pytest.raises(BridgeError, match="does not yet support the interactive ChatGPT role"):
        bridge.dispatch_agent(
            "demo", task_id, "auto", "SD/RD", bridge.latest("demo")["version"], "x",
            adapter="real", report_schema="clarification_request",
        )


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
