from pathlib import Path

import pytest
from conftest import dispatch

from ccc_mcp.config import bridge_home, setting_env
from ccc_mcp.errors import BridgeError
from ccc_mcp.service import CCCMCP
from ccc_mcp.worker import run_execution


def test_original_report_then_pm_refinement_then_next_agent(bridge):
    bridge.create_task("demo", "Analyze existing code")
    execution = dispatch(bridge)["execution_id"]
    run_execution(bridge, execution)
    boot = bridge.bootstrap_project("demo")
    assert boot["context_sync"]["needs_pm_refinement"]
    assert boot["context_sync"]["pending_reports"][0]["execution_id"] == execution
    assert boot["context_version"] == 1
    assert bridge.get_task_status("demo", "TASK-0001")["brief"]["summary"]
    source_path = f".ccc-mcp/tasks/TASK-0001/executions/{execution}/SA-REPORT.md"
    original = bridge.source_ref("demo", source_path)
    proposal = bridge.propose_context_patch(
        "demo",
        {
            "base_context_version": 1,
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
        assert handoff["context_version"] == 2
        assert handoff["content"]["shared_context"]["summary"].startswith("PM reviewed")
        assert original in handoff["source_document_refs"]
    assert restored.source_ref("demo", source_path) == original
    updates = restored.collect_context_updates("demo")
    assert updates["reports"][0]["result"]["report"]
    assert Path(updates["context_locations"]["shared_context"]).is_file()
    second = dispatch(bridge)["execution_id"]
    run_execution(bridge, second)
    assert [r["execution_id"] for r in restored.context_sync_status("demo")["pending_reports"]] == [
        second
    ]


def test_refinement_rejects_missing_or_unrelated_original(bridge):
    bridge.create_task("demo", "Analyze existing code")
    execution = dispatch(bridge)["execution_id"]
    run_execution(bridge, execution)
    patch = {
        "base_context_version": 1,
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
