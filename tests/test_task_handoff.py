"""`task_handoff` (CLI: `ccc-mcp task handoff`) answers "whose turn is it right now,
and what do I do about it" from existing durable task/context state only. The
sharpest version of that question is an interactive ChatGPT role: the human operator
at the terminal cannot tell what to paste into the chat without this.
"""

import json
import sys
import threading

import pytest
from typer.testing import CliRunner

from ccc_mcp.core.models import AgentReport
from ccc_mcp.interfaces.cli import app
from ccc_mcp.worker import run_execution


def report(**kwargs):
    return AgentReport(summary="Observed source", next_action="PM refine context", **kwargs).model_dump()


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


def chatgpt_only_task(bridge):
    bridge.configure_profile("demo", "chatgpt-only")
    bridge.authorize_project_local("demo")
    bridge.configure_test_command("demo", [sys.executable, "-c", "print('ok')"])
    started = bridge.start_project_request("demo", "Implement a change")
    return started["workstream"]["id"], started["sa_execution"]["execution_id"]


def test_handoff_for_sa_includes_a_paste_ready_readonly_prompt(bridge, human):
    task_id, sa_execution = chatgpt_only_task(bridge)

    handoff = bridge.task_handoff("demo", task_id)

    assert handoff["waiting_on"] == "CHATGPT_HANDOFF_PENDING"
    assert handoff["role"] == "SA"
    assert handoff["execution_id"] == sa_execution
    assert handoff["instruction"] == "Implement a change"
    assert set(handoff["expected_report_fields"]) >= {
        "summary", "findings", "risks", "next_action", "tests", "needs_revision",
    }
    prompt = handoff["chatgpt_prompt"]
    assert sa_execution in prompt
    assert "submit_agent_report" in prompt
    assert "write_task_file" not in prompt  # SA is read-only
    assert handoff["next_action"]


def test_handoff_for_sd_rd_includes_write_task_file_step(bridge, human):
    task_id, sa_execution = chatgpt_only_task(bridge)
    bridge.submit_agent_report("demo", sa_execution, report(context_updates=["SA analysis"]))
    advanced = bridge.advance_project(
        "demo", pending_patch(bridge, sa_execution), "Implement the approved change"
    )
    implementation_execution = advanced["next_execution"]["execution_id"]

    handoff = bridge.task_handoff("demo", task_id)

    assert handoff["role"] == "SD/RD"
    assert handoff["execution_id"] == implementation_execution
    prompt = handoff["chatgpt_prompt"]
    assert "write_task_file" in prompt
    assert "submit_agent_report" in prompt
    # The implementation role gets three ordered steps (read, write, submit).
    assert "1." in prompt and "2." in prompt and "3." in prompt


def test_handoff_for_review_is_read_only_again(bridge, human):
    task_id, sa_execution = chatgpt_only_task(bridge)
    bridge.submit_agent_report("demo", sa_execution, report())
    advanced = bridge.advance_project(
        "demo", pending_patch(bridge, sa_execution), "Implement the approved change"
    )
    implementation_execution = advanced["next_execution"]["execution_id"]
    bridge.write_task_file("demo", implementation_execution, "hello.py", 'print("Hello")\n')
    bridge.submit_agent_report("demo", implementation_execution, report())
    reviewed = bridge.advance_project("demo", pending_patch(bridge, implementation_execution))
    review_execution = reviewed["next_execution"]["execution_id"]

    handoff = bridge.task_handoff("demo", task_id)

    assert handoff["role"] == "REVIEW"
    assert handoff["execution_id"] == review_execution
    assert "write_task_file" not in handoff["chatgpt_prompt"]


def test_handoff_prompt_follows_project_language(bridge, human):
    task_id, _ = chatgpt_only_task(bridge)
    bridge.configure_language("demo", "en")

    handoff = bridge.task_handoff("demo", task_id)

    assert "take over the SA role" in handoff["chatgpt_prompt"]
    assert "Paste chatgpt_prompt" in handoff["next_action"]


def test_handoff_reuses_pending_dispatch_next_action_verbatim(bridge, human, monkeypatch):
    """Regression: the handoff's next_action for DISPATCH_RETRY_NEEDED must be the
    exact same text get_task_status already exposes, never an independent copy."""
    task_id, sa_execution = chatgpt_only_task(bridge)
    bridge.submit_agent_report("demo", sa_execution, report())

    calls = {"n": 0}
    original = bridge.prepare_chatgpt_execution

    def flaky(execution_id):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated interactive dispatch failure")
        return original(execution_id)

    monkeypatch.setattr(bridge, "prepare_chatgpt_execution", flaky)
    with pytest.raises(RuntimeError):
        bridge.advance_project(
            "demo", pending_patch(bridge, sa_execution), "Implement the approved change"
        )

    brief = bridge.get_task_status("demo", task_id)["brief"]
    assert brief["waiting_on"] == "DISPATCH_RETRY_NEEDED"

    handoff = bridge.task_handoff("demo", task_id)
    assert handoff["waiting_on"] == "DISPATCH_RETRY_NEEDED"
    assert handoff["next_action"] == brief["pending_dispatch"]["next_action"]
    assert handoff["chatgpt_prompt"] is None


def test_handoff_reuses_pm_refinement_workflow_text(bridge, human):
    task_id, sa_execution = chatgpt_only_task(bridge)
    bridge.submit_agent_report("demo", sa_execution, report())

    handoff = bridge.task_handoff("demo", task_id)

    assert handoff["waiting_on"] == "PM_REFINEMENT"
    assert handoff["next_action"] == bridge.context_sync_status("demo")["workflow"]


def test_handoff_for_background_agent_reuses_progress_note(bridge):
    """A non-interactive (native CLI) execution has nothing to paste; the note comes
    straight from get_task_status's own progress tracking. (The `bridge` fixture
    replaces spawn_worker with a no-op, so this QUEUED row never actually runs —
    which is exactly the "still waiting on a background agent" state being tested.)
    """
    task_id = bridge.create_task("demo", "Implement a change")["id"]
    bridge.dispatch_agent(
        "demo", task_id, "codex", "SA", bridge.latest("demo")["version"], "Analyze",
        adapter="fake",
    )

    handoff = bridge.task_handoff("demo", task_id)

    assert handoff["waiting_on"] == "AGENT_RUNNING"
    assert handoff["chatgpt_prompt"] is None
    assert handoff["next_action"] == bridge.get_task_status("demo", task_id)["brief"]["progress"]["note"]


def test_wait_for_task_blocks_until_background_agent_finishes(bridge):
    task_id = bridge.create_task("demo", "Implement a change")["id"]
    execution = bridge.dispatch_agent(
        "demo",
        task_id,
        "codex",
        "SA",
        bridge.latest("demo")["version"],
        "Analyze",
        adapter="fake",
    )
    worker = threading.Thread(
        target=run_execution, args=(bridge, execution["execution_id"]), daemon=True
    )
    worker.start()

    waited = bridge.wait_for_task("demo", task_id, timeout_seconds=2, poll_seconds=0.05)
    worker.join(timeout=2)

    assert waited["timed_out"] is False
    assert waited["handoff"]["waiting_on"] == "PM_REFINEMENT"
    assert waited["must_continue_in_this_turn"] is True


def test_wait_for_task_timeout_requires_same_turn_to_keep_waiting(bridge):
    task_id = bridge.create_task("demo", "Implement a change")["id"]
    bridge.dispatch_agent(
        "demo",
        task_id,
        "codex",
        "SA",
        bridge.latest("demo")["version"],
        "Analyze",
        adapter="fake",
    )

    waited = bridge.wait_for_task("demo", task_id, timeout_seconds=0.05, poll_seconds=0.05)

    assert waited["timed_out"] is True
    assert waited["handoff"]["waiting_on"] == "AGENT_RUNNING"
    assert waited["must_continue_in_this_turn"] is True


def test_wait_for_task_stays_through_implementation_tests_and_review(bridge, human, monkeypatch):
    bridge.authorize_project_local("demo")
    bridge.configure_test_command("demo", [sys.executable, "-c", "print('ok')"])
    original_auto_advance = bridge.auto_advance_low_risk
    monkeypatch.setattr(
        bridge,
        "auto_advance_low_risk",
        lambda execution_id: original_auto_advance(execution_id, allow_fake=True),
    )
    workers = []

    def spawn(execution_id):
        worker = threading.Thread(
            target=run_execution, args=(bridge, execution_id), daemon=True
        )
        workers.append(worker)
        worker.start()

    monkeypatch.setattr(bridge, "spawn_worker", spawn)
    task_id = bridge.create_task("demo", "Implement a change")["id"]
    sa = bridge.dispatch_agent(
        "demo",
        task_id,
        "codex",
        "SA",
        bridge.latest("demo")["version"],
        "Analyze",
        adapter="fake",
    )
    after_sa = bridge.wait_for_task("demo", task_id, timeout_seconds=2, poll_seconds=0.05)
    assert after_sa["handoff"]["waiting_on"] == "PM_REFINEMENT"

    advanced = bridge.advance_project(
        "demo",
        pending_patch(bridge, sa["execution_id"]),
        "Implement the approved change",
        adapter="fake",
    )
    assert advanced["next_execution"]["status"] == "QUEUED"

    completed = bridge.wait_for_task("demo", task_id, timeout_seconds=5, poll_seconds=0.05)
    for worker in workers:
        worker.join(timeout=2)

    assert completed["timed_out"] is False
    assert completed["terminal"] is True
    assert completed["must_continue_in_this_turn"] is False
    assert completed["handoff"]["waiting_on"] == "COMPLETED"


def test_handoff_defaults_to_the_project_workstream_task(bridge, human):
    task_id, _ = chatgpt_only_task(bridge)
    assert bridge.task_handoff("demo")["task_id"] == task_id


def test_cli_task_handoff_round_trip(bridge, human):
    task_id, sa_execution = chatgpt_only_task(bridge)
    result = CliRunner().invoke(
        app, ["--home", str(bridge.home), "--project", "demo", "task", "handoff", task_id]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["execution_id"] == sa_execution
    assert payload["chatgpt_prompt"]
