import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from conftest import approve, dispatch

from ccc_mcp.core.context import DOCUMENTS, TASK_DOCUMENTS
from ccc_mcp.infra.errors import BridgeError, VersionConflict
from ccc_mcp.service import CCCMCP
from ccc_mcp.worker import run_execution


def patch(bridge, **changes):
    return {
        "base_context_version": bridge.latest("demo")["version"],
        "changes": changes,
        "update_reason": "PM reviewed new evidence",
        "semantic_review_ack": True,
    }


def test_initialization_empty_repository(tmp_path):
    repo = tmp_path / "empty"
    repo.mkdir()
    bridge = CCCMCP(tmp_path / "home")
    bridge.register_project(repo, "demo", "新的專案目標")
    for name in ["CONTEXT.md", "config.yaml", "history/context-events.jsonl", *DOCUMENTS.values()]:
        assert (repo / ".ccc-mcp" / name).is_file()
    assert bridge.latest("demo")["version"] == 1
    assert bridge.validate_context("demo")["valid"]
    assert bridge.register_project(repo, "demo")["id"] == "demo"


def test_sa_report_and_optimization_preserves_evidence_and_decisions(bridge, human):
    bridge.create_task("demo", "新增 API", ["保留舊 API"])
    for doc in TASK_DOCUMENTS:
        assert (Path(bridge.project("demo")["root"]) / ".ccc-mcp/tasks/TASK-0001" / doc).exists()
    queued = dispatch(bridge)
    run_execution(bridge, queued["execution_id"])
    result = bridge.get_task_result("demo", "TASK-0001")[-1]
    assert result["result"]["report"]["simulated"]
    source_path = f".ccc-mcp/tasks/TASK-0001/executions/{queued['execution_id']}/SA-REPORT.md"
    ref = bridge.source_ref("demo", source_path)
    approve(bridge)
    old_decisions = bridge.get_approval_status("demo", "TASK-0001")
    proposal = bridge.propose_context_patch(
        "demo",
        patch(
            bridge,
            summary="SA 已提供模擬分析；需真實程式碼驗證。",
            source_document_refs=[ref],
            architecture=[
                {"text": "Fake handoff passed (simulation)", "source_document_refs": [ref]}
            ],
        ),
    )
    assert proposal["diff"]
    assert bridge.commit_context_patch("demo", proposal["patch_id"])["context_version"] == 3
    assert bridge.commit_context_patch("demo", proposal["patch_id"])["idempotent"]
    assert bridge.source_ref("demo", source_path) == ref
    assert bridge.get_approval_status("demo", "TASK-0001") == old_decisions
    boot = bridge.bootstrap_project("demo")
    assert "Approved PLAN-A" in boot["latest_decisions"][0]["content"]
    assert boot["protocol_version"] == "1"
    assert bridge.validate_context("demo")["valid"]
    assert len(bridge.get_context_history("demo")) == 3


def test_version_conflict(bridge):
    payload = patch(bridge, summary="new")
    a = bridge.propose_context_patch("demo", payload)
    b = bridge.propose_context_patch("demo", payload)
    bridge.commit_context_patch("demo", a["patch_id"])
    with pytest.raises(VersionConflict):
        bridge.commit_context_patch("demo", b["patch_id"])


def test_concurrent_context_commits(bridge):
    proposals = [
        bridge.propose_context_patch("demo", patch(bridge, summary=str(i))) for i in range(2)
    ]

    def commit(proposal):
        try:
            bridge.commit_context_patch("demo", proposal["patch_id"])
            return "ok"
        except VersionConflict:
            return "conflict"

    with ThreadPoolExecutor(2) as pool:
        assert sorted(pool.map(commit, proposals)) == ["conflict", "ok"]
    assert bridge.latest("demo")["version"] == 2
    assert bridge.validate_context("demo")["valid"]


def test_recovery_with_no_chat_history(bridge, human):
    bridge.create_task("demo", "需求 A", ["驗收 A"])
    queued = dispatch(bridge)
    run_execution(bridge, queued["execution_id"])
    approve(bridge)
    restarted = CCCMCP(bridge.home)
    boot = restarted.bootstrap_project("demo")
    required = {
        "project_summary",
        "context_version",
        "current_git_revision",
        "active_tasks",
        "latest_decisions",
        "current_blockers",
        "unresolved_questions",
        "previous_session_handoff",
        "suggested_next_action",
        "source_references",
    }
    assert required <= boot.keys()
    assert boot["active_tasks"][0]["requirement"] == "需求 A"
    assert boot["latest_decisions"] and boot["constraints"] and boot["suggested_next_action"]
    assert "RULE-COMMENTS" in restarted.export_context("demo")
    assert restarted.get_context("demo", "PM")["content"]["summary"]
    assert "summary" not in restarted.get_context("demo", "SA")["content"]
    assert restarted.build_handoff("demo", token_budget=1)["budget_exceeded"]


def test_build_handoff_compresses_history_over_budget_without_touching_invariants(bridge):
    """Regression (P1-4): a token_budget previously only set an advisory
    `budget_exceeded` flag and never actually shrank the package — real dispatch never
    even passed one. Now it compresses bounded-history lists and reports what it cut,
    while invariants/source_document_refs/reports are never touched."""
    from ccc_mcp.adapters.storage import now

    task_id = bridge.create_task("demo", "需求")["id"]
    for i in range(20):
        bridge.store.execute(
            "INSERT INTO decisions VALUES(?,?,?,?,?,?,?,?,?)",
            (f"DEC-test-{i:02d}", "demo", task_id, f"decision {i}", "reason", "tester", "Approved", 1, now()),
        )
    full = bridge.build_handoff("demo", "PM")
    assert len(full["content"]["decisions"]) == 20
    assert full["budget_omitted"] == []

    trimmed = bridge.build_handoff("demo", "PM", token_budget=50)
    assert trimmed["budget_exceeded"] is True
    assert len(trimmed["content"]["decisions"]) == 5
    assert any("decisions" in note for note in trimmed["budget_omitted"])
    # Protected sections must survive trimming untouched.
    assert trimmed["content"]["invariants"] == full["content"]["invariants"]
    assert trimmed["source_document_refs"] == full["source_document_refs"]


def test_build_handoff_without_a_budget_never_trims(bridge):
    from ccc_mcp.adapters.storage import now

    task_id = bridge.create_task("demo", "需求")["id"]
    for i in range(20):
        bridge.store.execute(
            "INSERT INTO decisions VALUES(?,?,?,?,?,?,?,?,?)",
            (f"DEC-test-{i:02d}", "demo", task_id, f"decision {i}", "reason", "tester", "Approved", 1, now()),
        )
    package = bridge.build_handoff("demo", "PM")
    assert len(package["content"]["decisions"]) == 20
    assert package["budget_exceeded"] is False
    assert package["budget_omitted"] == []


def test_invariants_and_semantic_ack(bridge):
    with pytest.raises(BridgeError, match="invariant"):
        bridge.propose_context_patch("demo", patch(bridge, invariants=[]))
    with pytest.raises(BridgeError, match="Semantic"):
        bridge.propose_context_patch(
            "demo", patch(bridge, summary="new") | {"semantic_review_ack": False}
        )
    with pytest.raises(BridgeError, match="immutable"):
        bridge.propose_context_patch("demo", patch(bridge, purpose="replace approved purpose"))


def test_architecture_source_drift(bridge):
    ref = bridge.source_ref("demo", "app.py")
    proposal = bridge.propose_context_patch(
        "demo",
        patch(bridge, architecture=[{"text": "value returns 1", "source_document_refs": [ref]}]),
    )
    bridge.commit_context_patch("demo", proposal["patch_id"])
    root = Path(bridge.project("demo")["root"])
    (root / "app.py").write_text("# original comment\ndef value():\n    return 2\n")
    drift = bridge.validate_context("demo")
    assert not drift["valid"]
    assert any(i["path"] == "app.py" for i in drift["issues"])
    architecture = root / ".ccc-mcp/context/ARCHITECTURE.md"
    architecture.write_text("fabricated architecture")
    assert any(
        "ARCHITECTURE" in i["path"]
        for i in bridge.bootstrap_project("demo")["validation"]["issues"]
    )
    bridge.recover_context("demo")
    # Rebuilding Markdown cannot make stale source hashes true.
    assert not bridge.validate_context("demo")["valid"]


def test_atomic_projection_crash_recovers_from_committed_db(bridge, monkeypatch):
    import ccc_mcp.core.context as context_module

    original = context_module.atomic_write
    proposal = bridge.propose_context_patch("demo", patch(bridge, summary="after crash"))

    def failure(*args):
        raise OSError("simulated disk failure")

    monkeypatch.setattr(context_module, "atomic_write", failure)
    with pytest.raises(OSError):
        bridge.commit_context_patch("demo", proposal["patch_id"])
    assert bridge.latest("demo")["version"] == 2
    monkeypatch.setattr(context_module, "atomic_write", original)
    assert not bridge.validate_context("demo")["valid"]
    bridge.recover_context("demo")
    assert bridge.validate_context("demo")["valid"]
    history = Path(bridge.project("demo")["root"]) / ".ccc-mcp/history/context-events.jsonl"
    assert [
        json.loads(line)["new_context_version"] for line in history.read_text().splitlines()
    ] == [1, 2]


def test_missing_or_stale_sources_rejected(bridge):
    ref = bridge.source_ref("demo", "app.py")
    ref["sha256"] = "0" * 64
    with pytest.raises(BridgeError, match="Evidence changed"):
        bridge.propose_context_patch("demo", patch(bridge, source_document_refs=[ref]))


def test_task_and_decision_invariants_are_outside_optimizer_control(bridge, human):
    bridge.create_task("demo", "不可遺失需求", ["不可遺失驗收"])
    approve(bridge)
    proposed = bridge.propose_context_patch("demo", patch(bridge, summary="extremely compressed"))
    bridge.commit_context_patch("demo", proposed["patch_id"])
    exported = bridge.export_context("demo")
    assert all(text in exported for text in ["不可遺失需求", "不可遺失驗收", "Approved PLAN-A"])
