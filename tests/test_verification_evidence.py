"""`brief.verification` (surfaced in `get_task_status`, `task_handoff`, and the
`task show` CLI text) must keep five states distinguishable instead of collapsing
everything into one ambiguous "success": an Agent's own claim, a host-verified pass,
a host-verified failure, no test evidence at all (or none collected), and a REVIEW
round reverted to SD/RD specifically because host evidence overrode it.
"""

import json
import sys

from typer.testing import CliRunner

from ccc_mcp.interfaces.cli import app
from ccc_mcp.worker import run_execution


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


def dispatch_and_run(bridge, task_id, role, agent=None, **kwargs):
    agent = agent or ("codex" if role in {"SA", "REVIEW"} else "claude")
    result = bridge.dispatch_agent(
        "demo", task_id, agent, role, bridge.latest("demo")["version"], "Perform role",
        adapter="fake", **kwargs,
    )
    run_execution(bridge, result["execution_id"])
    return result["execution_id"]


def verification(bridge, task_id):
    return bridge.get_task_status("demo", task_id)["brief"]["verification"]


def test_before_any_report_is_no_report_and_not_executed(bridge, human):
    task_id = bridge.create_task("demo", "Implement a change")["id"]
    v = verification(bridge, task_id)
    assert v["agent_claim"] == "no_report"
    assert v["host_verification"] == "not_executed"
    assert v["reverted_due_to_host_failure"] is False
    assert "不算已驗證" in v["message"]


def test_agent_claim_and_host_pass_are_both_reported(bridge, human):
    bridge.authorize_project_local("demo")
    bridge.configure_test_command("demo", [sys.executable, "-c", "print('ok')"])
    task_id, sa = bridge.create_task("demo", "Implement a change")["id"], None
    sa = dispatch_and_run(bridge, task_id, "SA")
    impl = bridge.advance_project(
        "demo", pending_patch(bridge, sa), "Implement the approved change", adapter="fake"
    )["next_execution"]
    run_execution(bridge, impl["execution_id"])
    bridge.advance_project("demo", pending_patch(bridge, impl["execution_id"]), adapter="fake")

    v = verification(bridge, task_id)
    assert v["agent_claim"] == "no_report"  # latest execution (REVIEW) hasn't run yet
    assert v["host_verification"] == "passed"
    assert "主機已實際執行測試並通過" in v["message"]


def test_agent_claims_complete_but_host_verification_failed(bridge, human):
    """REVIEW itself reports "no revision needed" (agent_claim) while the host test
    evidence from the SD/RD round it is reviewing already shows a failure
    (host_verification) — the two must be reported as separate, distinguishable
    facts, never collapsed into one "it's done" signal."""
    bridge.authorize_project_local("demo")
    bridge.configure_test_command("demo", [sys.executable, "-c", "import sys; sys.exit(1)"])
    task_id = bridge.create_task("demo", "Implement a change")["id"]
    sa = dispatch_and_run(bridge, task_id, "SA")
    impl = bridge.advance_project(
        "demo", pending_patch(bridge, sa), "Implement the approved change", adapter="fake"
    )["next_execution"]
    run_execution(bridge, impl["execution_id"])
    # The SD/RD -> REVIEW transition is what actually runs the host test command.
    reviewed = bridge.advance_project(
        "demo", pending_patch(bridge, impl["execution_id"]), adapter="fake"
    )
    run_execution(bridge, reviewed["next_execution"]["execution_id"])

    v = verification(bridge, task_id)
    assert v["agent_claim"] == "complete"
    assert v["host_verification"] == "failed"
    assert "不算驗收成功" in v["message"]
    assert v["agent_claim"] != v["host_verification"], (
        "an Agent's own claim of success must never be reported as the same thing "
        "as a host-verified pass"
    )
    # Not yet reverted: that only happens once advance_project actually re-dispatches
    # SD/RD because of this mismatch (see the dedicated revert test below).
    assert v["reverted_due_to_host_failure"] is False


def test_no_tests_collected_is_distinct_from_pass_or_fail(bridge, human):
    bridge.authorize_project_local("demo")
    task_id = bridge.create_task("demo", "Implement a change")["id"]
    sa = dispatch_and_run(bridge, task_id, "SA")
    impl = bridge.advance_project(
        "demo", pending_patch(bridge, sa), "Implement the approved change", adapter="fake"
    )["next_execution"]
    run_execution(bridge, impl["execution_id"])
    # No test_command configured: DEFAULT_TEST_COMMAND (pytest -q) runs against the
    # fresh, empty worktree and collects zero tests — distinct from "not executed".
    bridge.advance_project("demo", pending_patch(bridge, impl["execution_id"]), adapter="fake")

    v = verification(bridge, task_id)
    assert v["host_verification"] == "no_tests_collected"
    assert "沒收集到任何測試案例" in v["message"]


def test_review_revert_due_to_host_failure_is_flagged_distinctly(bridge, human):
    """The REVIEW round itself reports needs_revision=False (the fake adapter's
    default "success" scenario), but host-observed test failure must still be
    surfaced as the reason the round went back to SD/RD — not conflated with a
    REVIEW-initiated revision request."""
    bridge.authorize_project_local("demo")
    bridge.configure_test_command("demo", [sys.executable, "-c", "import sys; sys.exit(1)"])
    task_id = bridge.create_task("demo", "Implement a change")["id"]
    sa = dispatch_and_run(bridge, task_id, "SA")
    impl = bridge.advance_project(
        "demo", pending_patch(bridge, sa), "Implement the approved change", adapter="fake"
    )["next_execution"]
    run_execution(bridge, impl["execution_id"])
    reviewed = bridge.advance_project(
        "demo", pending_patch(bridge, impl["execution_id"]), adapter="fake"
    )
    review_execution = reviewed["next_execution"]["execution_id"]
    run_execution(bridge, review_execution)  # fake REVIEW: needs_revision=False

    result = bridge.advance_project(
        "demo", pending_patch(bridge, review_execution), adapter="fake"
    )
    assert bridge.store.one(
        "SELECT role FROM agent_executions WHERE id=?", (result["next_execution"]["execution_id"],)
    )["role"] == "SD/RD"

    v = verification(bridge, task_id)
    assert v["reverted_due_to_host_failure"] is True
    assert "不是 REVIEW 自己發現問題" in v["message"]


def test_review_requesting_its_own_revision_is_not_flagged_as_a_host_revert(bridge, human):
    """A completely different reason for returning to SD/RD — REVIEW itself asking
    for changes, with host tests passing — must not be mislabeled as a host-evidence
    override."""
    bridge.authorize_project_local("demo")
    bridge.configure_test_command("demo", [sys.executable, "-c", "print('ok')"])
    task_id = bridge.create_task("demo", "Implement a change")["id"]
    sa = dispatch_and_run(bridge, task_id, "SA")
    impl = bridge.advance_project(
        "demo", pending_patch(bridge, sa), "Implement the approved change", adapter="fake"
    )["next_execution"]
    run_execution(bridge, impl["execution_id"])
    reviewed = bridge.advance_project(
        "demo", pending_patch(bridge, impl["execution_id"]), adapter="fake",
        fake_scenario="revision",
    )
    review_execution = reviewed["next_execution"]["execution_id"]
    run_execution(bridge, review_execution)
    result = bridge.advance_project(
        "demo", pending_patch(bridge, review_execution), adapter="fake"
    )
    assert bridge.store.one(
        "SELECT role FROM agent_executions WHERE id=?", (result["next_execution"]["execution_id"],)
    )["role"] == "SD/RD"

    v = verification(bridge, task_id)
    assert v["reverted_due_to_host_failure"] is False


def test_cli_task_show_separates_agent_claim_from_host_verification(bridge, human):
    bridge.authorize_project_local("demo")
    bridge.configure_test_command("demo", [sys.executable, "-c", "import sys; sys.exit(1)"])
    task_id = bridge.create_task("demo", "Implement a change")["id"]
    sa = dispatch_and_run(bridge, task_id, "SA")
    impl = bridge.advance_project(
        "demo", pending_patch(bridge, sa), "Implement the approved change", adapter="fake"
    )["next_execution"]
    run_execution(bridge, impl["execution_id"])
    bridge.advance_project("demo", pending_patch(bridge, impl["execution_id"]), adapter="fake")

    result = CliRunner().invoke(
        app, ["--home", str(bridge.home), "--project", "demo", "task", "show", task_id]
    )
    assert result.exit_code == 0, result.output
    assert "驗收狀態" in result.output
    assert "不算驗收成功" in result.output
    assert "Agent 自稱完成不等於驗收成功" in result.output


def test_cli_task_status_json_includes_verification_block(bridge, human):
    task_id = bridge.create_task("demo", "Implement a change")["id"]
    result = CliRunner().invoke(
        app, ["--home", str(bridge.home), "--project", "demo", "task", "status", task_id]
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert set(payload["brief"]["verification"]) == {
        "agent_claim", "host_verification", "reverted_due_to_host_failure", "message",
    }
