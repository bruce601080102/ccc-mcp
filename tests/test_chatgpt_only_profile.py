"""chatgpt-only profile: the full SA -> SD/RD -> REVIEW relay driven entirely through
ChatGPT's interactive MCP path (no Codex/Claude Code CLI involved), plus the
resumability and profile-switching guarantees that must keep working once every
automatic role resolves to the chatgpt provider.
"""

import sys
from pathlib import Path

import pytest

from ccc_mcp.core.models import AgentReport
from ccc_mcp.infra.errors import BridgeError
from ccc_mcp.worker import run_execution


def report(**kwargs):
    return AgentReport(
        summary="Observed source", next_action="PM refine context", **kwargs
    ).model_dump()


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


def execution_row(bridge, execution_id):
    return bridge.store.one(
        "SELECT agent,role,adapter FROM agent_executions WHERE id=?", (execution_id,)
    )


def test_chatgpt_only_profile_completes_full_three_role_relay(bridge, human):
    """SA, SD/RD and REVIEW are each dispatched as a real ChatGPT interactive
    execution (agent=chatgpt, adapter=interactive) — never a native CLI subprocess —
    while Context, file evidence and the host-run test command behave exactly as the
    best-model workflow does."""
    bridge.configure_profile("demo", "chatgpt-only")
    bridge.authorize_project_local("demo")
    bridge.configure_test_command("demo", [sys.executable, "-c", "print('ok')"])

    started = bridge.start_project_request("demo", "Implement a change")
    task_id = started["workstream"]["id"]
    sa_execution = started["sa_execution"]["execution_id"]
    assert execution_row(bridge, sa_execution) == {
        "agent": "chatgpt", "role": "SA", "adapter": "interactive",
    }
    bridge.submit_agent_report("demo", sa_execution, report(context_updates=["SA analysis"]))

    advanced = bridge.advance_project(
        "demo", pending_patch(bridge, sa_execution), "Implement the approved change"
    )
    implementation_execution = advanced["next_execution"]["execution_id"]
    assert execution_row(bridge, implementation_execution) == {
        "agent": "chatgpt", "role": "SD/RD", "adapter": "interactive",
    }
    bridge.write_task_file("demo", implementation_execution, "hello.py", 'print("Hello")\n')
    bridge.submit_agent_report(
        "demo", implementation_execution, report(context_updates=["Implemented hello.py"])
    )

    reviewed = bridge.advance_project("demo", pending_patch(bridge, implementation_execution))
    assert reviewed["test_evidence"]["executed_by_host"] is True
    assert reviewed["test_evidence"]["passed"] is True
    review_execution = reviewed["next_execution"]["execution_id"]
    assert execution_row(bridge, review_execution) == {
        "agent": "chatgpt", "role": "REVIEW", "adapter": "interactive",
    }
    bridge.submit_agent_report("demo", review_execution, report())

    completed = bridge.advance_project("demo", pending_patch(bridge, review_execution))
    assert completed["completed_workstreams"] == [task_id]
    assert bridge.task("demo", task_id)["state"] == "COMPLETED"

    usage = bridge.get_task_usage("demo", task_id)
    assert len(usage) == 3
    assert all(item["interactive"] for item in usage)
    assert all(item["invocation"]["cli_invoked"] is False for item in usage)
    assert all(item["invocation"]["source"] == "chatgpt_web" for item in usage)
    assert (
        Path(bridge.project("demo")["root"]) / "hello.py"
    ).read_text(encoding="utf-8") == 'print("Hello")\n'


def test_chatgpt_only_profile_reverts_to_sd_rd_on_host_test_failure(bridge, human):
    """Even though REVIEW itself (as ChatGPT) claims no revision is needed, a host-
    observed test failure must still take precedence and route the round back to
    SD/RD — the same guarantee the best-model workflow has, now under chatgpt-only."""
    bridge.configure_profile("demo", "chatgpt-only")
    bridge.authorize_project_local("demo")
    bridge.configure_test_command("demo", [sys.executable, "-c", "import sys; sys.exit(1)"])

    started = bridge.start_project_request("demo", "Implement a change")
    task_id = started["workstream"]["id"]
    sa_execution = started["sa_execution"]["execution_id"]
    bridge.submit_agent_report("demo", sa_execution, report())

    advanced = bridge.advance_project(
        "demo", pending_patch(bridge, sa_execution), "Implement the approved change"
    )
    implementation_execution = advanced["next_execution"]["execution_id"]
    bridge.write_task_file("demo", implementation_execution, "hello.py", 'print("Hello")\n')
    bridge.submit_agent_report("demo", implementation_execution, report())

    reviewed = bridge.advance_project("demo", pending_patch(bridge, implementation_execution))
    assert reviewed["test_evidence"]["executed_by_host"] is True
    assert reviewed["test_evidence"]["passed"] is False
    review_execution = reviewed["next_execution"]["execution_id"]
    assert execution_row(bridge, review_execution)["role"] == "REVIEW"
    bridge.submit_agent_report("demo", review_execution, report(needs_revision=False))

    result = bridge.advance_project("demo", pending_patch(bridge, review_execution))

    assert bridge.task("demo", task_id)["state"] != "COMPLETED"
    assert "completed_workstreams" not in result
    assert execution_row(bridge, result["next_execution"]["execution_id"]) == {
        "agent": "chatgpt", "role": "SD/RD", "adapter": "interactive",
    }
    # Saving the next interactive report must not erase the failed host test
    # evidence before the PM advances the handoff and reruns that test.
    bridge.submit_agent_report("demo", result["next_execution"]["execution_id"], report())
    status = bridge.get_task_status("demo", task_id)
    assert status["brief"]["tests"]["executed_by_host"] is True
    assert status["brief"]["tests"]["passed"] is False


def test_chatgpt_only_profile_dispatch_is_resumable_after_a_worker_spawn_failure(
    bridge, human, monkeypatch
):
    """pending_dispatch/resume_pending_dispatch resumability is adapter-agnostic; this
    confirms it still works once every automatic role resolves to chatgpt. The failure
    is injected at the dispatch step itself (adapter="fake", same mechanism as the
    existing best-model regression test), not ChatGPT's own interactive hand-off."""
    bridge.configure_profile("demo", "chatgpt-only")
    bridge.authorize_project_local("demo")
    bridge.configure_test_command("demo", [sys.executable, "-c", "print('ok')"])
    task_id = bridge.create_task("demo", "Implement a change")["id"]

    sa = bridge.dispatch_agent(
        "demo", task_id, "auto", "SA", bridge.latest("demo")["version"], "Analyze",
        adapter="fake",
    )
    assert execution_row(bridge, sa["execution_id"])["agent"] == "chatgpt"
    run_execution(bridge, sa["execution_id"])

    calls = {"n": 0}
    original_spawn = bridge.spawn_worker

    def flaky(execution_id):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated worker spawn failure")
        return original_spawn(execution_id)

    monkeypatch.setattr(bridge, "spawn_worker", flaky)

    patch = pending_patch(bridge, sa["execution_id"])
    with pytest.raises(BridgeError, match="Unable to start worker"):
        bridge.advance_project("demo", patch, "Implement the approved change", adapter="fake")

    brief = bridge.get_task_status("demo", task_id)["brief"]
    assert brief["waiting_on"] == "DISPATCH_RETRY_NEEDED"
    assert brief["pending_dispatch"]["role_completed"] == "SA"

    resumed = bridge.resume_pending_dispatch("demo", task_id)
    assert resumed["resumed"] is True
    assert resumed["next_execution"]["status"] == "QUEUED"
    assert calls["n"] == 2
    assert execution_row(bridge, resumed["next_execution"]["execution_id"]) == {
        "agent": "chatgpt", "role": "SD/RD", "adapter": "fake",
    }
    assert bridge.get_task_status("demo", task_id)["brief"]["pending_dispatch"] is None


def test_switching_back_to_best_model_profile_dispatches_native_providers_again(
    bridge, human, monkeypatch
):
    """Switching the profile back is not just a settings change: the very next
    automatic dispatch must actually resolve to the native Codex/Claude providers
    again, with no leftover chatgpt-only provider on any of the three roles."""
    monkeypatch.setattr(
        "ccc_mcp.core.configuration.native_executable",
        lambda executable: f"/fake/bin/{executable}",
    )
    bridge.configure_profile("demo", "chatgpt-only")
    bridge.authorize_project_local("demo")
    bridge.configure_profile("demo", "best-model")
    assert bridge.public_project_settings("demo")["profile"] == "best-model"

    started = bridge.start_project_request("demo", "Implement a change")
    sa_execution = started["sa_execution"]["execution_id"]
    assert execution_row(bridge, sa_execution) == {
        "agent": "codex", "role": "SA", "adapter": "real",
    }
