import sys

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
    """Authorize, run SA, approve the implementation plan, run SD/RD. Returns
    (task_id, implementation_execution_id) with the implementation report still
    pending PM refinement, ready for the next advance_project call."""
    bridge.authorize_project_local("demo")
    task_id = bridge.create_task("demo", "Implement a change")["id"]
    sa = dispatch_and_run(bridge, task_id, "SA")
    implementation = bridge.advance_project(
        "demo", pending_patch(bridge, sa), "Implement the approved change", adapter="fake"
    )["next_execution"]
    run_execution(bridge, implementation["execution_id"])
    return task_id, implementation["execution_id"]


def test_advance_project_auto_runs_configured_test_command_before_review(bridge, human):
    """Regression (P0-2): project authorize + a configured test_command auto-runs
    before REVIEW is dispatched, and the outcome is visible without digging into raw
    artifacts."""
    bridge.configure_test_command("demo", [sys.executable, "-c", "print('ok')"])
    task_id, implementation_id = advance_to_implementation(bridge)

    result = bridge.advance_project(
        "demo", pending_patch(bridge, implementation_id), adapter="fake"
    )

    assert result["test_evidence"]["executed_by_host"] is True
    assert result["test_evidence"]["passed"] is True
    brief = bridge.get_task_status("demo", task_id)["brief"]
    assert brief["tests"] == {
        "executed_by_host": True, "passed": True, "reason": None,
        "dependencies": NO_MANIFEST_DETECTED, "no_tests_collected": False,
    }


def test_advance_project_defaults_to_pytest_when_no_test_command_is_configured(bridge, human):
    """Regression: `project authorize` alone is now consent enough to auto-run
    DEFAULT_TEST_COMMAND (`python -m pytest -q`) even without an explicit
    `config test-command` — but a repo with no tests yet (pytest exit code 5) must be
    reported as "no tests collected", not as a generic failure and not as "not
    executed"."""
    task_id, implementation_id = advance_to_implementation(bridge)

    result = bridge.advance_project(
        "demo", pending_patch(bridge, implementation_id), adapter="fake"
    )

    assert result["test_evidence"]["executed_by_host"] is True
    assert result["test_evidence"]["passed"] is True
    assert result["test_evidence"]["no_tests_collected"] is True
    brief = bridge.get_task_status("demo", task_id)["brief"]
    assert brief["tests"]["executed_by_host"] is True
    assert brief["tests"]["passed"] is True
    assert brief["tests"]["no_tests_collected"] is True


def test_advance_project_surfaces_a_failing_test_command(bridge, human):
    """Regression (P0-2): a failing configured test command must be reported as
    failed, not swallowed into a generic SUCCEEDED execution result."""
    bridge.configure_test_command("demo", [sys.executable, "-c", "import sys; sys.exit(1)"])
    task_id, implementation_id = advance_to_implementation(bridge)

    result = bridge.advance_project(
        "demo", pending_patch(bridge, implementation_id), adapter="fake"
    )

    assert result["test_evidence"]["executed_by_host"] is True
    assert result["test_evidence"]["passed"] is False
    brief = bridge.get_task_status("demo", task_id)["brief"]
    assert brief["tests"] == {
        "executed_by_host": True, "passed": False, "reason": None,
        "dependencies": NO_MANIFEST_DETECTED, "no_tests_collected": False,
    }


def test_full_pipeline_reaches_completed_with_host_verified_test_evidence(bridge, human):
    """Regression (P1-5): a single mechanically-checkable acceptance path — create,
    authorize, SA, advance, SD/RD, host-run configured test command, REVIEW, advance —
    reaches COMPLETED using only backend state (get_task_status/DB rows), never an
    Agent's own claim of success. Runs on FakeAgentAdapter so it needs no installed
    Codex/Claude CLI; see tests/test_real_agents.py for the real-CLI equivalent."""
    bridge.configure_test_command("demo", [sys.executable, "-c", "print('ok')"])
    task_id, implementation_id = advance_to_implementation(bridge)

    after_tests = bridge.advance_project(
        "demo", pending_patch(bridge, implementation_id), adapter="fake"
    )
    assert after_tests["test_evidence"]["executed_by_host"] is True
    assert after_tests["test_evidence"]["passed"] is True
    review_execution_id = after_tests["next_execution"]["execution_id"]
    run_execution(bridge, review_execution_id)
    assert bridge.task("demo", task_id)["state"] == "AWAITING_PM_REVIEW"

    completed = bridge.advance_project(
        "demo", pending_patch(bridge, review_execution_id), adapter="fake"
    )

    assert completed["completed_workstreams"] == [task_id]
    assert bridge.task("demo", task_id)["state"] == "COMPLETED"
    assert bridge.get_task_status("demo", task_id)["brief"]["waiting_on"] == "COMPLETED"
    assert bridge.get_task_status("demo", task_id)["brief"]["tests"] == {
        "executed_by_host": True, "passed": True, "reason": None,
        "dependencies": NO_MANIFEST_DETECTED, "no_tests_collected": False,
    }
    assert bridge.context_sync_status("demo")["pending_reports"] == []


def test_low_risk_delivery_and_review_auto_advance_without_pm(bridge, human):
    """A normal real-worker result may skip PM refinement, but only after retaining
    immutable evidence and running the host test command.  ``allow_fake`` gives this
    backend test the same guarded path without invoking installed CLIs."""
    bridge.configure_test_command("demo", [sys.executable, "-c", "print('ok')"])
    task_id, implementation_id = advance_to_implementation(bridge)

    after_implementation = bridge.auto_advance_low_risk(implementation_id, allow_fake=True)

    assert after_implementation["advanced"] is True, after_implementation
    assert after_implementation["test_evidence"]["passed"] is True
    assert bridge.latest("demo")["metadata"]["author_role"] == "SYSTEM"
    review_id = after_implementation["next_execution"]["execution_id"]
    run_execution(bridge, review_id)

    after_review = bridge.auto_advance_low_risk(review_id, allow_fake=True)

    assert after_review["advanced"] is True
    assert after_review["completed_workstreams"] == [task_id]
    assert bridge.task("demo", task_id)["state"] == "COMPLETED"
    assert bridge.context_sync_status("demo")["pending_reports"] == []


def test_low_risk_auto_advance_stops_for_agent_revision_request(bridge, human):
    task_id, implementation_id = advance_to_implementation(bridge)
    bridge.store.execute(
        "UPDATE agent_executions SET result=? WHERE id=?",
        ('{"report":{"summary":"x","next_action":"x","needs_revision":true}}', implementation_id),
    )

    result = bridge.auto_advance_low_risk(implementation_id, allow_fake=True)

    assert result == {"advanced": False, "reason": "Agent requested revision or a decision"}
    assert bridge.task("demo", task_id)["state"] == "AWAITING_PM_REVIEW"


def test_low_risk_auto_advance_stops_after_review_when_host_tests_fail(bridge, human):
    bridge.configure_test_command("demo", [sys.executable, "-c", "import sys; sys.exit(1)"])
    task_id, implementation_id = advance_to_implementation(bridge)

    after_implementation = bridge.auto_advance_low_risk(implementation_id, allow_fake=True)

    assert after_implementation["advanced"] is True
    assert after_implementation["test_evidence"]["passed"] is False
    review_id = after_implementation["next_execution"]["execution_id"]
    run_execution(bridge, review_id)

    after_review = bridge.auto_advance_low_risk(review_id, allow_fake=True)

    assert after_review == {"advanced": False, "reason": "Host test evidence requires PM attention"}
    assert bridge.task("demo", task_id)["state"] == "AWAITING_PM_REVIEW"


def test_test_evidence_not_auto_run_without_project_authorization(bridge, human):
    """Regression (P0-2): the consent is project authorize itself — a configured
    test_command on a non-continuously-authorized project must not auto-run."""
    bridge.configure_test_command("demo", [sys.executable, "-c", "print('ok')"])
    task_id = bridge.create_task("demo", "新增 API", ["API 回傳 200"])["id"]

    result = bridge.run_configured_test_command("demo", task_id)

    assert result == {
        "executed_by_host": False,
        "reason": "Project is not continuously authorized; run project authorize",
    }
