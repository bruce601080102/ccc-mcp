import time
from pathlib import Path

import pytest
from conftest import approve, dispatch

from ccc_mcp.infra.errors import BridgeError, PermissionDenied, VersionConflict
from ccc_mcp.service import CCCMCP
from ccc_mcp.worker import run_execution


def test_worker_rejects_unregistered_provider(bridge, human):
    """Regression: worker must not silently run an unknown provider as Claude."""
    bridge.create_task("demo", "新增 API", ["API 回傳 200"])
    queued = dispatch(bridge)
    bridge.store.execute(
        "UPDATE agent_executions SET agent='grok',adapter='real' WHERE id=?",
        (queued["execution_id"],),
    )
    run_execution(bridge, queued["execution_id"])
    result = bridge.get_task_result("demo", "TASK-0001")[-1]
    assert result["status"] == "FAILED"
    assert "No adapter registered for provider: grok" in result["error"]


def test_get_task_status_flags_orphaned_queued_execution(bridge, human):
    # spawn_worker is a no-op in tests (see conftest), so dispatch() always
    # leaves the row QUEUED with nothing holding its lock — exactly the shape
    # of a worker subprocess that crashed or never started.
    bridge.create_task("demo", "新增 API", ["API 回傳 200"])
    queued = dispatch(bridge)
    bridge.store.execute(
        "UPDATE agent_executions SET created=? WHERE id=?",
        ("2000-01-01T00:00:00+00:00", queued["execution_id"]),
    )

    status = bridge.get_task_status("demo", "TASK-0001")

    assert status["brief"]["progress"]["orphaned"] is True
    assert "recover_workers" in status["brief"]["progress"]["note"]


def test_get_task_status_does_not_flag_freshly_queued_execution(bridge, human):
    bridge.create_task("demo", "新增 API", ["API 回傳 200"])
    dispatch(bridge)

    status = bridge.get_task_status("demo", "TASK-0001")

    assert status["brief"]["progress"]["orphaned"] is False


def test_get_task_status_does_not_flag_execution_with_live_lock(bridge, human):
    import portalocker

    bridge.create_task("demo", "新增 API", ["API 回傳 200"])
    queued = dispatch(bridge)
    bridge.store.execute(
        "UPDATE agent_executions SET created=? WHERE id=?",
        ("2000-01-01T00:00:00+00:00", queued["execution_id"]),
    )
    lockfile = bridge.home / "locks" / f"{queued['execution_id']}.lock"
    lockfile.parent.mkdir(exist_ok=True)

    with portalocker.Lock(lockfile, timeout=5):
        status = bridge.get_task_status("demo", "TASK-0001")

    assert status["brief"]["progress"]["orphaned"] is False


def test_recover_workers_clears_orphaned_execution(bridge, human):
    bridge.create_task("demo", "新增 API", ["API 回傳 200"])
    queued = dispatch(bridge)

    result = bridge.recover_workers("demo")

    assert result["interrupted_executions"] == [queued["execution_id"]]
    assert bridge.task("demo", "TASK-0001")["state"] == "BLOCKED"


def test_sd_handoff_contains_approved_plan_and_current_version(bridge, human):
    bridge.create_task("demo", "新增 API", ["API 回傳 200"])
    sa = dispatch(bridge)
    run_execution(bridge, sa["execution_id"])
    approve(bridge)
    implementation = dispatch(bridge, "SD/RD")
    run_execution(bridge, implementation["execution_id"])
    result = bridge.get_task_result("demo", "TASK-0001")[-1]
    package = result["package"]
    assert package["context_version"] == 2
    assert package["content"]["approved_plan"]["id"] == "PLAN-A"
    assert "SA-REPORT.md" in package["content"]["reports"]
    assert result["worktree"] != bridge.project("demo")["root"]
    assert result["result"]["comment_check"]["ok"]
    assert not result["result"]["test_evidence"]["executed_by_host"]
    assert bridge.task("demo", "TASK-0001")["state"] == "AWAITING_PM_REVIEW"
    assert Path(result["worktree"]).is_dir()


def test_no_human_approval_prevents_implementation(bridge):
    bridge.create_task("demo", "新增 API")
    with pytest.raises(PermissionDenied):
        dispatch(bridge, "RD")
    assert bridge.task("demo", "TASK-0001")["state"] == "CREATED"


def test_approval_bound_to_plan_version_and_context(bridge, human):
    bridge.create_task("demo", "需求")
    approve(bridge)
    bridge.propose_plan("demo", "TASK-0001", "PLAN-A", "新的方案內容", 1)
    with pytest.raises(PermissionDenied):
        dispatch(bridge, "SD")
    bridge.request_approval("demo", "TASK-0001", "PLAN-A")
    bridge.approve_local("demo", "TASK-0001", "PLAN-A")
    patch = bridge.propose_context_patch(
        "demo",
        {
            "base_context_version": 1,
            "changes": {"summary": "v2"},
            "update_reason": "update",
            "semantic_review_ack": True,
        },
    )
    bridge.commit_context_patch("demo", patch["patch_id"])
    with pytest.raises(PermissionDenied):
        dispatch(bridge, "SD")
    with pytest.raises(VersionConflict):
        bridge.dispatch_agent("demo", "TASK-0001", "codex", "SA", 1, "old context", adapter="fake")


def test_review_revision_loop(bridge, human):
    bridge.create_task("demo", "需求")
    approve(bridge)
    first = dispatch(bridge, "RD")
    run_execution(bridge, first["execution_id"])
    review = dispatch(bridge, "REVIEW", fake_scenario="revision")
    run_execution(bridge, review["execution_id"])
    assert bridge.task("demo", "TASK-0001")["state"] == "NEEDS_REVISION"
    second = dispatch(bridge, "RD")
    run_execution(bridge, second["execution_id"])
    assert bridge.task("demo", "TASK-0001")["state"] == "AWAITING_PM_REVIEW"
    originals = bridge.store.all(
        "SELECT path FROM artifacts WHERE project_id='demo' AND path LIKE '%/executions/%/IMPLEMENTATION.md'"
    )
    assert len(originals) == 2


def test_implementation_can_request_revision_without_illegal_transition(bridge, human):
    bridge.create_task("demo", "需求")
    approve(bridge)
    implementation = dispatch(bridge, "RD", fake_scenario="revision")

    run_execution(bridge, implementation["execution_id"])

    execution = bridge.store.one(
        "SELECT status,error FROM agent_executions WHERE id=?",
        (implementation["execution_id"],),
    )
    assert execution == {"status": "SUCCEEDED", "error": None}
    assert bridge.task("demo", "TASK-0001")["state"] == "NEEDS_REVISION"


def test_sa_can_be_dispatched_repeatedly_without_fixed_pipeline(bridge):
    bridge.create_task("demo", "分析")
    for _ in range(2):
        queued = dispatch(bridge)
        run_execution(bridge, queued["execution_id"])
        assert bridge.task("demo", "TASK-0001")["state"] == "AWAITING_PM_DECISION"


def test_agent_timeout_and_error(bridge):
    bridge.create_task("demo", "分析")
    queued = dispatch(bridge, timeout=0.05, fake_delay=0.2)
    run_execution(bridge, queued["execution_id"])
    assert bridge.task("demo", "TASK-0001")["state"] == "FAILED"
    assert bridge.get_task_result("demo", "TASK-0001")[-1]["result"]["failure_kind"] == "TIMEOUT"
    again = dispatch(bridge, fake_scenario="error")
    run_execution(bridge, again["execution_id"])
    assert "Simulated agent failure" in bridge.get_task_result("demo", "TASK-0001")[-1]["error"]


def test_cancel_before_execution(bridge):
    bridge.create_task("demo", "分析")
    queued = dispatch(bridge)
    bridge.cancel_task("demo", "TASK-0001")
    run_execution(bridge, queued["execution_id"])
    assert bridge.task("demo", "TASK-0001")["state"] == "CANCELLED"
    assert bridge.get_task_result("demo", "TASK-0001")[-1]["status"] == "CANCELLED"


def test_worker_restart_blocks_without_replaying(bridge):
    bridge.create_task("demo", "分析")
    queued = dispatch(bridge)
    bridge.store.execute(
        "UPDATE agent_executions SET status='RUNNING' WHERE id=?", (queued["execution_id"],)
    )
    fresh = CCCMCP(bridge.home)
    assert fresh.recover_workers("demo")["interrupted_executions"] == [queued["execution_id"]]
    assert fresh.task("demo", "TASK-0001")["state"] == "BLOCKED"
    run_execution(fresh, queued["execution_id"])
    assert fresh.get_task_result("demo", "TASK-0001")[-1]["status"] == "FAILED"


def test_duplicate_dispatch_and_illegal_transition(bridge):
    bridge.create_task("demo", "分析")
    with pytest.raises(BridgeError, match="Illegal"):
        with bridge.store.transaction() as conn:
            bridge._transition(conn, "demo", "TASK-0001", "COMPLETED", "invalid")
    dispatch(bridge)
    with pytest.raises(BridgeError, match="active"):
        dispatch(bridge)


def test_asynchronous_detached_worker_returns_before_result(bridge, monkeypatch):
    bridge.create_task("demo", "分析")
    monkeypatch.setattr(bridge, "spawn_worker", CCCMCP.spawn_worker.__get__(bridge))
    started = time.monotonic()
    queued = dispatch(bridge, fake_delay=1)
    assert time.monotonic() - started < 1
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        status = bridge.get_task_result("demo", "TASK-0001")[-1]["status"]
        if status not in {"QUEUED", "RUNNING"}:
            break
        time.sleep(0.1)
    assert status == "SUCCEEDED"
    assert queued["task_id"] == "TASK-0001"


def test_accept_requires_pm_review_and_terminal(bridge, human):
    bridge.create_task("demo", "需求")
    with pytest.raises(BridgeError):
        bridge.accept_local("demo", "TASK-0001")
    approve(bridge)
    queued = dispatch(bridge, "RD")
    run_execution(bridge, queued["execution_id"])
    assert bridge.accept_local("demo", "TASK-0001")["state"] == "COMPLETED"
    assert bridge.get_task_status("demo", "TASK-0001")["events"][-1]["new_state"] == "COMPLETED"


def test_append_decisions_and_reject_plan(bridge, human):
    bridge.create_task("demo", "需求")
    first = bridge.record_decision_local("demo", "TASK-0001", "Keep API", "Compatibility")
    bridge.record_decision_local(
        "demo",
        "TASK-0001",
        "Replace decision",
        "New requirements",
        status="Superseded",
        supersedes=first["decision_id"],
    )
    bridge.propose_plan("demo", "TASK-0001", "PLAN-A", "Plan", 1)
    bridge.request_approval("demo", "TASK-0001", "PLAN-A")
    bridge.reject_local("demo", "TASK-0001", "PLAN-A", "Incomplete acceptance criteria")
    assert bridge.task("demo", "TASK-0001")["state"] == "REJECTED"
    assert len(bridge.bootstrap_project("demo")["latest_decisions"]) == 3
    assert bridge.get_approval_status("demo", "TASK-0001")[0]["status"] == "REJECTED"


def test_observed_test_results_persist(bridge, human):
    from ccc_mcp.adapters.test_runner import run_tests_local

    bridge.create_task("demo", "需求")
    approve(bridge)
    queued = dispatch(bridge, "RD")
    run_execution(bridge, queued["execution_id"])
    worktree = Path(bridge.get_task_result("demo", "TASK-0001")[-1]["worktree"])
    (worktree / "test_health.py").write_text(
        "from app import value\ndef test_value():\n    assert value() == 1\n"
    )
    result = run_tests_local(bridge, "demo", "TASK-0001")
    assert result["passed"] and result["executed_by_host"]
    artifact = bridge.store.one(
        "SELECT content FROM artifacts WHERE project_id='demo' "
        "AND path='.ccc-mcp/tasks/TASK-0001/TEST-RESULT.md'"
    )
    assert '"executed_by_host":true' in artifact["content"]
    # Change the source size so a same-second edit invalidates Python's bytecode cache.
    (worktree / "test_health.py").write_text(
        "from app import value\ndef test_value():\n    assert value() == 999\n"
    )
    failed = run_tests_local(bridge, "demo", "TASK-0001")
    assert not failed["passed"] and "FAILED" in failed["stdout"]
