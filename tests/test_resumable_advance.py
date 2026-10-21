import sys

import pytest

from ccc_mcp.infra.errors import BridgeError
from ccc_mcp.worker import run_execution

NO_MANIFEST_DETECTED = {
    "detected": False,
    "manifest": None,
    "installed": None,
    "venv": False,
    "reason": "No recognized dependency manifest "
    "(requirements.txt/pyproject.toml/package.json) found",
}


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


def dispatch_and_run(bridge, task_id, role):
    result = bridge.dispatch_agent(
        "demo",
        task_id,
        "codex" if role in {"SA", "REVIEW"} else "claude",
        role,
        bridge.latest("demo")["version"],
        "Perform role",
        adapter="fake",
    )
    run_execution(bridge, result["execution_id"])
    return result["execution_id"]


def advance_to_implementation(bridge):
    bridge.authorize_project_local("demo")
    task_id = bridge.create_task("demo", "Implement a change")["id"]
    sa = dispatch_and_run(bridge, task_id, "SA")
    return task_id, sa


def fail_spawn_once(monkeypatch, bridge):
    """The `bridge` fixture already replaces spawn_worker with a no-op lambda at the
    instance level (so dispatch never actually spawns a subprocess in tests); patch it
    again so the *first* call raises (simulating a worker spawn failure) and every call
    after that behaves like the original no-op again."""
    calls = {"n": 0}

    def flaky(execution_id):
        calls["n"] += 1
        if calls["n"] == 1:
            raise RuntimeError("simulated worker spawn failure")

    monkeypatch.setattr(bridge, "spawn_worker", flaky)
    return calls


def test_advance_project_resumes_after_dispatch_failure_without_recommitting_context(
    bridge, human, monkeypatch
):
    """Regression (P0-1): if advance_project's post-commit dispatch step fails (worker
    spawn failure, transient error), Context must already be committed exactly once, a
    pending_dispatch pointer must record the interrupted step, and retrying the exact
    same advance_project call must complete the dispatch without resubmitting the
    ContextPatch or losing the failed execution's record."""
    task_id, sa = advance_to_implementation(bridge)
    fail_spawn_once(monkeypatch, bridge)

    version_before = bridge.latest("demo")["version"]
    patch = pending_patch(bridge, sa)
    with pytest.raises(BridgeError, match="Unable to start worker"):
        bridge.advance_project("demo", patch, "Implement the approved change", adapter="fake")

    # Context was committed exactly once; the failure happened strictly after.
    version_after_failure = bridge.latest("demo")["version"]
    assert version_after_failure == version_before + 1
    assert bridge.context_sync_status("demo")["pending_reports"] == []

    brief = bridge.get_task_status("demo", task_id)["brief"]
    assert brief["waiting_on"] == "DISPATCH_RETRY_NEEDED"
    assert brief["pending_dispatch"]["role_completed"] == "SA"
    assert brief["pending_dispatch"]["source_execution_id"] == sa

    failed_rows = [
        r for r in bridge.get_task_result("demo", task_id)
        if r["role"] == "SD/RD" and r["status"] == "FAILED"
    ]
    assert len(failed_rows) == 1
    assert "Unable to start worker" in failed_rows[0]["error"]

    # Retrying with the exact same patch must resume, not re-decide or re-commit.
    resumed = bridge.advance_project(
        "demo", patch, "Implement the approved change", adapter="fake"
    )
    assert resumed["resumed"] is True
    assert resumed["context"]["context_version"] == version_after_failure
    assert bridge.latest("demo")["version"] == version_after_failure
    assert resumed["next_execution"]["status"] == "QUEUED"
    assert bridge.get_task_status("demo", task_id)["brief"]["pending_dispatch"] is None

    sd_rd_rows = [r for r in bridge.get_task_result("demo", task_id) if r["role"] == "SD/RD"]
    assert len(sd_rd_rows) == 2
    assert {r["status"] for r in sd_rd_rows} == {"FAILED", "QUEUED"}

    run_execution(bridge, resumed["next_execution"]["execution_id"])
    assert bridge.task("demo", task_id)["state"] == "AWAITING_PM_REVIEW"


def test_advance_project_rejects_a_different_patch_while_a_dispatch_is_pending(
    bridge, human, monkeypatch
):
    """A caller must resume the unresolved dispatch, not silently move on with a
    different completed execution while a round's dispatch is unresolved."""
    task_id, sa = advance_to_implementation(bridge)
    fail_spawn_once(monkeypatch, bridge)
    patch = pending_patch(bridge, sa)
    with pytest.raises(BridgeError, match="Unable to start worker"):
        bridge.advance_project("demo", patch, "Implement the approved change", adapter="fake")

    # A second, unrelated SUCCEEDED execution (independently dispatched REVIEW) so the
    # rejection is specifically about the unresolved pointer, not a missing execution.
    other = dispatch_and_run(bridge, task_id, "REVIEW")
    with pytest.raises(BridgeError, match="unresolved pending dispatch"):
        bridge.advance_project(
            "demo",
            {**patch, "source_execution_ids": [other]},
            adapter="fake",
        )


def test_resume_pending_dispatch_tool_needs_no_original_patch(bridge, human, monkeypatch):
    """The dedicated resume tool must work from the durable pointer alone — the exact
    scenario where a fresh ChatGPT PM session no longer has the original ContextPatch."""
    task_id, sa = advance_to_implementation(bridge)
    calls = fail_spawn_once(monkeypatch, bridge)
    with pytest.raises(BridgeError, match="Unable to start worker"):
        bridge.advance_project(
            "demo", pending_patch(bridge, sa), "Implement the approved change", adapter="fake"
        )
    assert calls["n"] == 1
    assert bridge.get_task_status("demo", task_id)["brief"]["pending_dispatch"] is not None

    # The second spawn attempt (via resume) succeeds, since fail_spawn_once only fails once.
    result = bridge.resume_pending_dispatch("demo", task_id)
    assert result["resumed"] is True
    assert result["next_execution"]["status"] == "QUEUED"
    assert calls["n"] == 2
    assert bridge.get_task_status("demo", task_id)["brief"]["pending_dispatch"] is None


def test_resume_pending_dispatch_is_a_noop_without_a_pending_round(bridge, human):
    task_id, sa = advance_to_implementation(bridge)
    result = bridge.resume_pending_dispatch("demo", task_id)
    assert result == {
        "resumed": False,
        "task_id": task_id,
        "reason": "No pending dispatch is recorded for this task; nothing to resume.",
    }


def test_cancel_task_clears_a_pending_dispatch_pointer(bridge, human, monkeypatch):
    task_id, sa = advance_to_implementation(bridge)
    fail_spawn_once(monkeypatch, bridge)
    with pytest.raises(BridgeError):
        bridge.advance_project(
            "demo", pending_patch(bridge, sa), "Implement the approved change", adapter="fake"
        )
    assert bridge.get_task_status("demo", task_id)["brief"]["pending_dispatch"] is not None

    bridge.cancel_task("demo", task_id)
    assert bridge._pending_dispatch_row("demo", task_id) is None


def test_host_test_failure_overrides_an_optimistic_review_report(bridge, human):
    """Regression (P0-2): a host-executed test failure must not be overridable by the
    REVIEW Agent's own self-report — commit_context_patch must not auto-complete the
    task, and the automatic routing must send it back to SD/RD instead, even though the
    (fake) REVIEW Agent itself reported needs_revision=False."""
    bridge.configure_test_command("demo", [sys.executable, "-c", "import sys; sys.exit(1)"])
    task_id, sa = advance_to_implementation(bridge)
    implementation = bridge.advance_project(
        "demo", pending_patch(bridge, sa), "Implement the approved change", adapter="fake"
    )["next_execution"]
    run_execution(bridge, implementation["execution_id"])

    after_tests = bridge.advance_project(
        "demo", pending_patch(bridge, implementation["execution_id"]), adapter="fake"
    )
    assert after_tests["test_evidence"]["executed_by_host"] is True
    assert after_tests["test_evidence"]["passed"] is False
    review_execution_id = after_tests["next_execution"]["execution_id"]
    run_execution(bridge, review_execution_id)  # fake adapter: needs_revision=False

    result = bridge.advance_project(
        "demo", pending_patch(bridge, review_execution_id), adapter="fake"
    )

    assert bridge.task("demo", task_id)["state"] != "COMPLETED"
    assert "completed_workstreams" not in result
    assert bridge.store.one(
        "SELECT role FROM agent_executions WHERE id=?",
        (result["next_execution"]["execution_id"],),
    )["role"] == "SD/RD"
    brief = bridge.get_task_status("demo", task_id)["brief"]
    assert brief["tests"] == {
        "executed_by_host": True, "passed": False, "reason": None,
        "dependencies": NO_MANIFEST_DETECTED, "no_tests_collected": False,
    }
    assert brief["waiting_on"] == "AGENT_RUNNING"
