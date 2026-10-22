"""Manual integration acceptance against the actually-installed, logged-in Codex and
Claude CLIs — real Codex SA, real Claude SD/RD, real Codex REVIEW, a host-run test
command — with completion decided only by backend state (get_task_status/DB rows),
never by trusting an Agent's own claim of success.

Excluded from the default test run (see pyproject.toml's `addopts`); run explicitly:

    .venv/Scripts/python.exe -m pytest -m real_agents tests/test_real_agents.py

Needs both CLIs installed and logged in (`ccc-mcp setup all`); each test skips itself
if a required CLI cannot be resolved, rather than failing on a machine that doesn't
have them. See README.md's hello-demo walkthrough and docs/en/1-install-and-sign-in.md.
"""

from pathlib import Path

import pytest

from ccc_mcp.adapters.agents.base import native_executable
from ccc_mcp.infra.errors import BridgeError
from ccc_mcp.worker import run_execution

pytestmark = pytest.mark.real_agents


def _require_cli(provider):
    try:
        native_executable(provider)
    except BridgeError:
        pytest.skip(f"{provider} CLI is not installed/logged in on this machine")


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


def test_real_codex_sa_claude_sdrd_codex_review_with_host_tests(bridge, human, monkeypatch):
    _require_cli("codex")
    _require_cli("claude")
    monkeypatch.setenv("CCC_MCP_ENABLE_REAL_AGENTS", "1")

    root = Path(bridge.project("demo")["root"])
    # conftest's bridge fixture already committed app.py with a value() function.
    (root / "test_health.py").write_text(
        "from app import value\ndef test_value():\n    assert value() == 1\n"
    )
    bridge.configure_test_command("demo", ["python", "-m", "pytest", "-q"])
    bridge.authorize_project_local("demo")

    started = bridge.start_project_request(
        "demo",
        "Add a one-line docstring to the value() function in app.py explaining what it "
        "returns. Do not change its behavior.",
    )
    task_id = started["workstream"]["id"]
    sa_execution_id = started["sa_execution"]["execution_id"]
    run_execution(bridge, sa_execution_id)
    sa_result = bridge.get_task_result("demo", task_id)[-1]
    assert sa_result["status"] == "SUCCEEDED", sa_result.get("error")

    implementation = bridge.advance_project(
        "demo",
        pending_patch(bridge, sa_execution_id),
        "Add a one-line docstring to value() in app.py explaining what it returns; "
        "no behavior change.",
    )["next_execution"]
    run_execution(bridge, implementation["execution_id"])
    implementation_result = bridge.get_task_result("demo", task_id)[-1]
    assert implementation_result["status"] == "SUCCEEDED", implementation_result.get("error")

    after_tests = bridge.advance_project("demo", pending_patch(bridge, implementation["execution_id"]))
    assert after_tests["test_evidence"]["executed_by_host"] is True
    assert after_tests["test_evidence"]["passed"] is True, after_tests["test_evidence"]

    review_execution_id = after_tests["next_execution"]["execution_id"]
    run_execution(bridge, review_execution_id)
    review_result = bridge.get_task_result("demo", task_id)[-1]
    assert review_result["status"] == "SUCCEEDED", review_result.get("error")

    completed = bridge.advance_project("demo", pending_patch(bridge, review_execution_id))
    assert completed["completed_workstreams"] == [task_id]
    assert bridge.task("demo", task_id)["state"] == "COMPLETED"
