from ccc_mcp.infra.errors import BridgeError
from ccc_mcp.worker import run_execution


def dispatch(bridge, role="SA", **kwargs):
    return bridge.dispatch_agent(
        "demo", "TASK-0001", "auto", role, bridge.latest("demo")["version"], "Perform role",
        **kwargs,
    )


def test_cli_not_installed_is_recorded_as_error_not_quota_and_never_falls_back(
    bridge, human, monkeypatch
):
    """Regression (P0-3): CLI-not-installed / not-logged-in / other generic failures
    must be classified separately from quota exhaustion, and must never trigger the
    quota_fallback handoff even when one is configured — only a recognized quota/limit
    message may do that (is_quota_error's existing, deliberately narrow rule set)."""
    monkeypatch.setenv("CCC_MCP_ENABLE_REAL_AGENTS", "1")
    bridge.configure_role("demo", "SA", "codex", quota_fallback="chatgpt")
    bridge.create_task("demo", "Analyze")

    def not_installed(*args):
        raise BridgeError("Native codex executable unavailable; install the official CLI")

    monkeypatch.setattr("ccc_mcp.adapters.agents.codex.CodexAdapter.run", not_installed)
    queued = dispatch(bridge)
    run_execution(bridge, queued["execution_id"])

    results = bridge.get_task_result("demo", "TASK-0001")
    assert len(results) == 1, "a non-quota failure must not dispatch a fallback execution"
    assert results[0]["result"]["failure_kind"] == "ERROR"
    assert results[0]["result"].get("fallback") is None

    brief = bridge.get_task_status("demo", "TASK-0001")["brief"]
    assert brief["failure_kind"] == "ERROR"
    assert brief["fallback"] is None
    assert brief["waiting_on"] == "FAILED"
    assert not bridge.bootstrap_project("demo")["pending_chatgpt_handoffs"]


def test_quota_exhaustion_is_classified_and_surfaced_distinctly_in_the_brief(
    bridge, human, monkeypatch
):
    monkeypatch.setenv("CCC_MCP_ENABLE_REAL_AGENTS", "1")
    bridge.configure_role("demo", "SA", "codex", quota_fallback="chatgpt")
    bridge.create_task("demo", "Analyze")

    def quota(*args):
        raise BridgeError("Usage limit reached")

    monkeypatch.setattr("ccc_mcp.adapters.agents.codex.CodexAdapter.run", quota)
    queued = dispatch(bridge)
    run_execution(bridge, queued["execution_id"])

    results = bridge.get_task_result("demo", "TASK-0001")
    assert len(results) == 2  # the failed primary execution + the dispatched fallback
    brief = bridge.get_task_status("demo", "TASK-0001")["brief"]
    # get_task_status reports the *latest* execution, which is now the chatgpt fallback
    # handoff itself — distinctly flagged so the PM knows to act, not just wait.
    assert brief["agent"] == "chatgpt"
    assert brief["waiting_on"] == "CHATGPT_HANDOFF_PENDING"

    primary = next(r for r in results if r["agent"] == "codex")
    assert primary["result"]["failure_kind"] == "QUOTA_EXHAUSTED"
    assert primary["result"]["fallback"]["attempted"] is True
    assert primary["result"]["fallback"]["dispatched"] is True
