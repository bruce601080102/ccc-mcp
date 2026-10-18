import json
from pathlib import Path

import pytest
from conftest import approve, dispatch

from ccc_mcp.config import bridge_home, setting_env
from ccc_mcp.errors import BridgeError, PermissionDenied
from ccc_mcp.service import CCCMCP
from ccc_mcp.worker import run_execution


def test_original_report_then_pm_refinement_then_next_agent(bridge):
    bridge.create_task("demo", "Analyze existing code")
    execution = dispatch(bridge)["execution_id"]
    run_execution(bridge, execution)
    boot = bridge.bootstrap_project("demo")
    assert boot["context_sync"]["needs_pm_refinement"]
    assert boot["context_sync"]["pending_reports"][0]["execution_id"] == execution
    assert boot["context_version"] == 2
    automatic = boot["working_context"]["agent_results"][0]
    assert automatic["execution_id"] == execution
    assert automatic["provider"] == "codex"
    assert automatic["review_status"] == "unreviewed"
    assert bridge.get_task_status("demo", "TASK-0001")["brief"]["summary"]
    source_path = f".ccc-mcp/tasks/TASK-0001/executions/{execution}/SA-REPORT.md"
    original = bridge.source_ref("demo", source_path)
    proposal = bridge.propose_context_patch(
        "demo",
        {
                "base_context_version": 2,
            "changes": {"summary": "PM reviewed the simulation; real analysis remains pending."},
            "source_execution_ids": [execution],
            "source_task_ids": ["TASK-0001"],
            "source_document_refs": [original],
            "update_reason": "Reviewed original evidence",
            "semantic_review_ack": True,
        },
    )
    assert bridge.context_sync_status("demo")["needs_pm_refinement"]
    bridge.commit_context_patch("demo", proposal["patch_id"])
    restored = CCCMCP(bridge.home)
    assert not restored.context_sync_status("demo")["needs_pm_refinement"]
    for role in ("SA", "SD", "RD", "SD/RD"):
        handoff = restored.build_handoff("demo", role)
        assert handoff["context_version"] == 3
        assert handoff["content"]["shared_context"]["summary"].startswith("PM reviewed")
        assert handoff["content"]["shared_context"]["agent_results"] == []
        assert original not in handoff["source_document_refs"]
    assert restored.source_ref("demo", source_path) == original
    updates = restored.collect_context_updates("demo")
    assert updates["reports"][0]["result"]["report"]
    assert Path(updates["context_locations"]["shared_context"]).is_file()
    second = dispatch(bridge)["execution_id"]
    run_execution(bridge, second)
    assert [r["execution_id"] for r in restored.context_sync_status("demo")["pending_reports"]] == [
        second
    ]


def test_agent_required_mode_enforces_refinement_before_implementation(bridge, human):
    bridge.authorize_project_local("demo")
    bridge.create_task("demo", "Analyze and implement a change")
    analysis_execution = dispatch(bridge, role="SA")["execution_id"]
    run_execution(bridge, analysis_execution)

    with pytest.raises(PermissionDenied, match="unrefined reports"):
        dispatch(bridge, role="SD/RD")

    source_path = (
        f".ccc-mcp/tasks/TASK-0001/executions/{analysis_execution}/SA-REPORT.md"
    )
    proposal = bridge.propose_context_patch(
        "demo",
        {
            "base_context_version": bridge.latest("demo")["version"],
            "changes": {
                "summary": "PM reviewed the SA evidence and prepared implementation context."
            },
            "source_execution_ids": [analysis_execution],
            "source_task_ids": ["TASK-0001"],
            "source_document_refs": [bridge.source_ref("demo", source_path)],
            "update_reason": "Promote agent findings into shared context",
            "semantic_review_ack": True,
        },
    )
    bridge.commit_context_patch("demo", proposal["patch_id"])
    assert not bridge.context_sync_status("demo")["needs_pm_refinement"]

    bridge.propose_plan(
        "demo", "TASK-0001", "PLAN-A", "Implement the change", bridge.latest("demo")["version"]
    )
    implementation = dispatch(bridge, role="SD/RD")
    stored = bridge.store.one(
        "SELECT role, context_version FROM agent_executions WHERE id=?",
        (implementation["execution_id"],),
    )
    assert stored["role"] == "SD/RD"
    assert stored["context_version"] == bridge.latest("demo")["version"]

    run_execution(bridge, implementation["execution_id"])
    with pytest.raises(PermissionDenied, match="unrefined reports"):
        bridge.accept_local("demo", "TASK-0001")

    result = next(
        item
        for item in bridge.latest("demo")["snapshot"]["agent_results"]
        if item["execution_id"] == implementation["execution_id"]
    )
    final_patch = bridge.propose_context_patch(
        "demo",
        {
            "base_context_version": bridge.latest("demo")["version"],
            "changes": {"summary": "PM reviewed implementation and test evidence."},
            "source_execution_ids": [implementation["execution_id"]],
            "source_task_ids": ["TASK-0001"],
            "source_document_refs": [result["source_document_ref"]],
            "update_reason": "Promote implementation result into shared context",
            "semantic_review_ack": True,
        },
    )
    bridge.commit_context_patch("demo", final_patch["patch_id"])
    assert bridge.accept_local("demo", "TASK-0001")["state"] == "COMPLETED"


def test_refinement_rejects_missing_or_unrelated_original(bridge):
    bridge.create_task("demo", "Analyze existing code")
    execution = dispatch(bridge)["execution_id"]
    run_execution(bridge, execution)
    patch = {
        "base_context_version": bridge.latest("demo")["version"],
        "changes": {"summary": "Reviewed"},
        "source_execution_ids": [execution],
        "update_reason": "Review",
        "semantic_review_ack": True,
    }
    with pytest.raises(BridgeError, match="immutable original"):
        bridge.propose_context_patch("demo", patch)
    patch["source_document_refs"] = [bridge.source_ref("demo", "app.py")]
    with pytest.raises(BridgeError, match="immutable original"):
        bridge.propose_context_patch("demo", patch)
    patch["source_execution_ids"] = ["EXEC-missing"]
    with pytest.raises(BridgeError, match="saved agent report"):
        bridge.propose_context_patch("demo", patch)


def test_invalid_architecture_fact_is_an_actionable_domain_error(bridge):
    with pytest.raises(BridgeError, match="architecture items must be objects"):
        bridge.propose_context_patch(
            "demo",
            {
                "base_context_version": 1,
                "changes": {"architecture": ["GET /hello-words2 returns text"]},
                "update_reason": "Record route",
                "semantic_review_ack": True,
            },
        )


def test_review_uses_the_approved_plan_after_pm_context_refinement(bridge, human):
    bridge.create_task("demo", "Implement and review")
    approve(bridge)
    implementation = dispatch(bridge, role="SD/RD")
    run_execution(bridge, implementation["execution_id"])
    result = bridge.latest("demo")["snapshot"]["agent_results"][0]
    patch = bridge.propose_context_patch(
        "demo",
        {
            "base_context_version": 2,
            "changes": {"summary": "PM recorded implementation evidence."},
            "source_execution_ids": [implementation["execution_id"]],
            "source_task_ids": ["TASK-0001"],
            "source_document_refs": [result["source_document_ref"]],
            "update_reason": "Record post-implementation state",
            "semantic_review_ack": True,
        },
    )
    bridge.commit_context_patch("demo", patch["patch_id"])

    review = bridge.dispatch_agent(
        "demo",
        "TASK-0001",
        "codex",
        "REVIEW",
        3,
        "Review the approved implementation",
        adapter="fake",
    )
    package = json.loads(
        bridge.store.one(
            "SELECT package FROM agent_executions WHERE id=?", (review["execution_id"],)
        )["package"]
    )
    assert package["content"]["approved_plan"]["id"] == "PLAN-A"


def test_existing_metadata_and_reports_survive_brand_change(bridge):
    root = Path(bridge.project("demo")["root"])
    source, target = root / ".ccc-mcp", root / ".agentbridge"
    assert source.resolve().parent == target.resolve().parent == root.resolve()
    source.rename(target)
    bridge.create_task("demo", "Legacy project analysis")
    execution = dispatch(bridge)["execution_id"]
    run_execution(bridge, execution)
    restored = CCCMCP(bridge.home)
    boot = restored.bootstrap_project("demo")
    assert boot["validation"]["valid"]
    assert Path(boot["context_locations"]["shared_context"]).parent == target
    ref = restored.source_ref(
        "demo", f".agentbridge/tasks/TASK-0001/executions/{execution}/SA-REPORT.md"
    )
    assert ref["sha256"]
    assert not source.exists()
    with pytest.raises(BridgeError):
        restored.read_workspace("demo", ".agentbridge/config.yaml")


def test_agent_results_automatically_flow_from_codex_to_claude_without_prompt(bridge):
    bridge.create_task("demo", "Codex analyzes shared context")
    codex_execution = bridge.dispatch_agent(
        "demo",
        "TASK-0001",
        "codex",
        "SA",
        bridge.latest("demo")["version"],
        "Analyze",
        adapter="fake",
    )["execution_id"]
    run_execution(bridge, codex_execution)

    after_codex = bridge.latest("demo")
    assert after_codex["version"] == 2
    assert after_codex["snapshot"]["agent_results"][0]["provider"] == "codex"

    bridge.create_task("demo", "Claude reads Codex shared result")
    claude_execution = bridge.dispatch_agent(
        "demo",
        "TASK-0002",
        "claude",
        "SA",
        after_codex["version"],
        "Analyze independently",
        adapter="fake",
    )["execution_id"]
    stored = bridge.store.one("SELECT package FROM agent_executions WHERE id=?", (claude_execution,))
    package = json.loads(stored["package"])
    inherited = package["content"]["shared_context"]["agent_results"]
    assert inherited[0]["execution_id"] == codex_execution
    assert inherited[0]["provider"] == "codex"

    run_execution(bridge, claude_execution)
    final = bridge.latest("demo")
    assert final["version"] == 3
    assert [item["provider"] for item in final["snapshot"]["agent_results"]] == [
        "codex",
        "claude",
    ]


def test_legacy_context_snapshot_gets_agent_results_default(bridge):
    current = bridge.latest("demo")
    legacy = current["snapshot"]
    legacy.pop("agent_results")
    bridge.store.execute(
        "INSERT INTO context_versions VALUES(?,?,?,?,?)",
        (
            "demo",
            2,
            json.dumps(legacy),
            json.dumps({"git_revision": current["metadata"]["git_revision"]}),
            "2026-01-01T00:00:00+00:00",
        ),
    )

    restored = CCCMCP(bridge.home)
    assert restored.latest("demo")["snapshot"]["agent_results"] == []
    assert restored.build_handoff("demo", "SA")["content"]["shared_context"]["agent_results"] == []


def test_existing_home_and_environment_compatibility(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    for name in ("CCC_MCP_HOME", "AGENTBRIDGE_HOME"):
        monkeypatch.delenv(name, raising=False)
    legacy = tmp_path / ".agentbridge"
    legacy.mkdir()
    assert bridge_home() == legacy
    current = tmp_path / ".ccc-mcp"
    current.mkdir()
    assert bridge_home() == current
    monkeypatch.setenv("AGENTBRIDGE_ENABLE_REAL_AGENTS", "1")
    assert setting_env("ENABLE_REAL_AGENTS") == "1"
    monkeypatch.setenv("CCC_MCP_ENABLE_REAL_AGENTS", "0")
    assert setting_env("ENABLE_REAL_AGENTS") == "0"
