"""Task conversation uses durable evidence and control gates, including old tasks."""

import threading

import pytest
from conftest import approve, dispatch

from ccc_mcp.adapters.test_runner import record_test_evidence
from ccc_mcp.infra.errors import PermissionDenied
from ccc_mcp.worker import run_execution


def test_old_task_rebuilds_timeline_without_new_interventions(bridge):
    bridge.create_task("demo", "Inspect <script>bad()</script>")
    execution = dispatch(bridge)
    run_execution(bridge, execution["execution_id"])

    first = bridge.task_timeline("demo", "TASK-0001")
    second = bridge.task_timeline("demo", "TASK-0001")
    assert first == second
    assert all(e["project_id"] == "demo" and e["task_id"] == "TASK-0001"
               and isinstance(e["metadata"], dict) for e in first["events"])
    assert [e["event_type"] for e in first["events"]] == [
        "task_created", "agent_started", "agent_completed"
    ]
    assert "<script>" in first["events"][0]["summary"]
    assert first["events"][-1]["detail"]["report"]["summary"]


def test_normal_agent_test_review_sequence_is_visible(bridge, human):
    bridge.create_task("demo", "Fix parser")
    sa = dispatch(bridge)
    run_execution(bridge, sa["execution_id"])
    approve(bridge)
    development = dispatch(bridge, "SD/RD")
    run_execution(bridge, development["execution_id"])
    assert any(
        f"/executions/{development['execution_id']}/TEST-RESULT.md" in row["path"]
        for row in bridge.task_artifacts("demo", "TASK-0001")
    )
    record_test_evidence(bridge, "demo", "TASK-0001", {
        "executed_by_host": True, "passed": True, "command": ["pytest"], "exit_code": 0,
    })
    review = dispatch(bridge, "REVIEW")
    run_execution(bridge, review["execution_id"])

    events = bridge.task_timeline("demo", "TASK-0001")["events"]
    assert [e["role"] for e in events if e["event_type"] == "agent_completed"] == [
        "SA", "SD/RD", "REVIEW"
    ]
    host = next(e for e in events if e["event_type"] == "host_test_completed"
                and e["detail"].get("executed_by_host"))
    assert host["status"] == "VERIFIED"
    assert host["detail"]["exit_code"] == 0
    assert sum(e["event_type"] == "host_test_completed" for e in events) == 1


def test_pause_blocks_queued_worker_and_resumes_same_execution(bridge):
    bridge.create_task("demo", "Inspect")
    execution = dispatch(bridge)
    assert bridge.pause_task("demo", "TASK-0001")["status"] == "PAUSED"
    run_execution(bridge, execution["execution_id"])
    assert bridge.get_task_result("demo", "TASK-0001")[-1]["status"] == "QUEUED"
    assert bridge.recover_workers("demo")["interrupted_executions"] == []
    assert bridge.get_task_result("demo", "TASK-0001")[-1]["status"] == "QUEUED"
    with pytest.raises(PermissionDenied, match="paused"):
        dispatch(bridge)
    assert bridge.resume_task("demo", "TASK-0001")["waiting_on"] == "AGENT_RUNNING"
    run_execution(bridge, execution["execution_id"])
    assert bridge.get_task_result("demo", "TASK-0001")[-1]["status"] == "SUCCEEDED"
    assert [e["event_type"] for e in bridge.task_timeline("demo", "TASK-0001")["events"]].count(
        "pause_requested"
    ) == 1
    assert [e["event_type"] for e in bridge.task_timeline("demo", "TASK-0001")["events"]].count(
        "task_paused"
    ) == 1


def test_stop_after_sa_prevents_implementation_and_keeps_reason(bridge, human):
    bridge.create_task("demo", "Inspect")
    execution = dispatch(bridge)
    run_execution(bridge, execution["execution_id"])
    approve(bridge)

    stopped = bridge.cancel_task("demo", "TASK-0001", "Wrong function")
    assert stopped["status"] == "CANCELLED"
    assert stopped["stop_reason"] == "Wrong function"
    with pytest.raises(PermissionDenied, match="stop"):
        dispatch(bridge, "SD/RD")
    assert bridge.cancel_task("demo", "TASK-0001") == stopped
    assert [e["event_type"] for e in bridge.task_timeline("demo", "TASK-0001")["events"]].count(
        "cancel_requested"
    ) == 1


def test_stop_running_agent_blocks_next_stage(bridge):
    bridge.create_task("demo", "Inspect")
    execution = dispatch(bridge, fake_delay=1)
    entered = threading.Event()
    release = threading.Event()
    original = bridge.store.one

    def watched(query, args=()):
        value = original(query, args)
        if "SELECT cancel_requested FROM agent_executions" in query:
            entered.set()
            release.wait(3)
        return value

    # A controlled cancellation point simulates an agent that needs time to stop.
    bridge.store.one = watched
    thread = threading.Thread(target=run_execution, args=(bridge, execution["execution_id"]))
    thread.start()
    assert entered.wait(3)
    assert bridge.cancel_task("demo", "TASK-0001")["status"] == "STOPPING"
    release.set()
    thread.join(5)
    assert not thread.is_alive()
    assert bridge.get_task_status("demo", "TASK-0001")["brief"]["control"]["status"] == "CANCELLED"
    with pytest.raises(PermissionDenied, match="stop"):
        dispatch(bridge)


def test_pause_running_agent_settles_before_paused(bridge):
    bridge.create_task("demo", "Inspect")
    execution = dispatch(bridge, fake_delay=1)
    entered = threading.Event()
    release = threading.Event()
    original = bridge.store.one

    def watched(query, args=()):
        value = original(query, args)
        if "SELECT cancel_requested FROM agent_executions" in query:
            entered.set()
            release.wait(3)
        return value

    bridge.store.one = watched
    thread = threading.Thread(target=run_execution, args=(bridge, execution["execution_id"]))
    thread.start()
    assert entered.wait(3)
    assert bridge.pause_task("demo", "TASK-0001")["status"] == "PAUSING"
    release.set()
    thread.join(5)
    assert not thread.is_alive()
    assert bridge.get_task_status("demo", "TASK-0001")["brief"]["control"]["status"] == "PAUSED"
    kinds = [e["event_type"] for e in bridge.task_timeline("demo", "TASK-0001")["events"]]
    assert kinds.count("task_paused") == 1


def test_user_feedback_is_persisted_and_delivered_once(bridge):
    bridge.create_task("demo", "Inspect")
    bridge.add_task_message("demo", "TASK-0001", "Modify the existing function", pause=False)
    handoff = bridge.task_handoff("demo", "TASK-0001")
    assert handoff["pending_user_messages"][0]["content"] == "Modify the existing function"
    execution = dispatch(bridge)
    package = bridge.get_task_result("demo", "TASK-0001")[-1]["package"]
    assert package["content"]["pending_user_messages"][0]["content"] == "Modify the existing function"
    run_execution(bridge, execution["execution_id"])
    assert bridge.task_handoff("demo", "TASK-0001")["pending_user_messages"] == []
    message = next(e for e in bridge.task_timeline("demo", "TASK-0001")["events"]
                   if e["event_type"] == "user_message")
    assert message["delivered_execution_id"] == execution["execution_id"]


def test_resume_delivers_correction_to_paused_queued_execution_once(bridge):
    bridge.create_task("demo", "Inspect")
    execution = dispatch(bridge)
    bridge.add_task_message("demo", "TASK-0001", "Use the existing parser")
    assert bridge.task_handoff("demo", "TASK-0001")["pending_user_messages"][0][
        "content"
    ] == "Use the existing parser"

    bridge.resume_task("demo", "TASK-0001")
    bridge.resume_task("demo", "TASK-0001")
    package = bridge.get_task_result("demo", "TASK-0001")[-1]["package"]
    assert [m["content"] for m in package["content"]["pending_user_messages"]] == [
        "Use the existing parser"
    ]
    assert bridge.task_handoff("demo", "TASK-0001")["pending_user_messages"] == []
    run_execution(bridge, execution["execution_id"])
    assert bridge.get_task_result("demo", "TASK-0001")[-1]["status"] == "SUCCEEDED"


def test_stop_during_host_test_waits_for_test_to_settle(bridge, human, monkeypatch):
    bridge.authorize_project_local("demo")
    bridge.create_task("demo", "Inspect")
    started = threading.Event()
    release = threading.Event()

    def test_command(*args, **kwargs):
        started.set()
        assert release.wait(5)
        return {"executed_by_host": True, "passed": True, "command": ["pytest"]}

    monkeypatch.setattr("ccc_mcp.adapters.test_runner.execute_test_command", test_command)
    thread = threading.Thread(target=bridge.run_configured_test_command, args=("demo", "TASK-0001"))
    thread.start()
    assert started.wait(3)
    assert bridge.cancel_task("demo", "TASK-0001")["status"] == "STOPPING"
    release.set()
    thread.join(5)
    assert not thread.is_alive()
    assert bridge.get_task_status("demo", "TASK-0001")["brief"]["control"]["status"] == "CANCELLED"
