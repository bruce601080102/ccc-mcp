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


def test_continuity_handoff_assembles_existing_data_plus_a_live_git_diff(bridge, human):
    """Continuity Handoff MVP (docs/development/ACQE-GAP-ANALYSIS.md §23): the package
    must reuse bootstrap_project/get_task_status as-is and add only a live git
    status/diff -- on a genuinely unfinished task (SA done, RD not yet dispatched),
    while this call must not deadlock on its own project lock (bootstrap_project and
    get_task_status each already take it)."""
    bridge.create_task("demo", "新增 greet 函式")
    approve(bridge)
    queued = dispatch(bridge)
    run_execution(bridge, queued["execution_id"])

    root = Path(bridge.project("demo")["root"])
    (root / "scratch-note.md").write_text("still in progress\n", encoding="utf-8")

    package = bridge.continuity_handoff("demo", "TASK-0001")

    assert "demo" in package and "TASK-0001" in package
    # Something was actually produced by the task so far (Agent self-report section).
    assert "SA" in package
    # The live, uncommitted file shows up -- proof this reads the working tree now,
    # not a past execution's saved evidence snapshot.
    assert "scratch-note.md" in package
    # Host-vs-Agent verification text (verification_summary()'s own wording) is present,
    # not re-derived or re-worded by this method.
    status = bridge.get_task_status("demo", "TASK-0001")["brief"]
    assert status["verification"]["message"] in package
    # Every section is labeled by source, per the feature's own requirement.
    for label in ("Agent", "Context", "git"):
        assert label in package


def test_continuity_handoff_is_a_prompt_for_a_named_target(bridge, human):
    """Prompt-first continuity: the package names the target Agent, carries the
    task's requirement and the approved plan's hash (decision content), and rejects
    an unknown target. It must not claim the target has taken over the work."""
    bridge.create_task("demo", "新增 greet 函式")
    approve(bridge)

    package = bridge.continuity_handoff("demo", "TASK-0001", target="codex")

    assert "Continuation Prompt" in package
    assert "Codex CLI" in package
    assert "requirement: 新增 greet 函式" in package
    plan = bridge.approved_plan("demo", "TASK-0001", require_current_context=False)
    assert plan["hash"] in package
    assert "「已派工」不等於「已完成」" in package
    with pytest.raises(BridgeError):
        bridge.continuity_handoff("demo", "TASK-0001", target="gpt-unknown")


@pytest.mark.parametrize(
    ("target", "label"),
    [("claude", "Claude Code"), ("codex", "Codex CLI"), ("chatgpt", "ChatGPT")],
)
def test_continuation_prompt_is_generated_for_each_target(bridge, human, target, label):
    bridge.create_task("demo", "新增 greet 函式")
    approve(bridge)

    package = bridge.continuity_handoff("demo", "TASK-0001", target=target)

    assert f"# Continuation Prompt — for {label}" in package
    assert "requirement: 新增 greet 函式" in package
    assert "「已派工」不等於「已完成」" in package


def test_approved_plan_body_and_source_path_appear_in_the_handoff(bridge, human):
    bridge.create_task("demo", "新增 greet 函式")
    approve(bridge)

    package = bridge.continuity_handoff("demo", "TASK-0001", target="claude")

    # The plan's actual decision text, not only its hash, must reach the next Agent.
    assert "新增 value API；保留原始註解。" in package
    assert "Plan ID / version: PLAN-A v1" in package
    assert ".ccc-mcp/tasks/TASK-0001/APPROVED-PLAN.md" in package


def test_missing_approved_plan_is_reported_not_invented(bridge, human):
    bridge.create_task("demo", "新增 greet 函式")

    package = bridge.continuity_handoff("demo", "TASK-0001", target="codex")

    assert "沒有 Approved Plan" in package
    assert "(none approved for this task)" in package
    assert "Plan ID / version" not in package


def test_next_action_and_claims_are_separated_from_host_evidence(bridge, human):
    bridge.create_task("demo", "新增 greet 函式")
    approve(bridge)
    queued = dispatch(bridge)
    run_execution(bridge, queued["execution_id"])

    package = bridge.continuity_handoff("demo", "TASK-0001")

    remaining = package.split("## 剩餘工作", 1)[1].split("## 下一個具體動作", 1)[0]
    completed = package.split("## 已完成事項", 1)[1].split("## 主機驗證狀態", 1)[0]
    # Agent self-reports are labeled as such, never presented as completed host facts.
    assert "[Agent 自述 SA/codex]" in completed
    assert "主機證據" not in completed
    # Host has not run the acceptance tests, so the handoff must say it is not verified.
    assert "尚未通過，不算已完成" in package
    assert "驗收條件尚未由主機實際執行並通過" in remaining
    # Plan step completion is not tracked by ccc-mcp and is explicitly left to confirm.
    assert "待確認" in remaining
    # The next action is a concrete ccc-mcp state, not the generic project-level default.
    next_action = package.split("## 下一個具體動作", 1)[1].split("## 必要驗證方式", 1)[0]
    assert "PM_REFINEMENT" in next_action
    assert "直接提出第一個專案需求" not in next_action


def test_no_target_keeps_the_plain_handoff_and_leaves_changes_intact(bridge, human):
    bridge.create_task("demo", "新增 greet 函式")
    approve(bridge)
    root = Path(bridge.project("demo")["root"])
    (root / "scratch-note.md").write_text("keep me\n", encoding="utf-8")

    package = bridge.continuity_handoff("demo", "TASK-0001")

    assert package.startswith("# Continuity Handoff — demo / TASK-0001")
    assert "Continuation Prompt" not in package
    assert "scratch-note.md" in package
    assert (root / "scratch-note.md").read_text(encoding="utf-8") == "keep me\n"


def _propose(bridge, plan, content):
    version = bridge.latest("demo")["version"]
    bridge.propose_plan("demo", "TASK-0001", plan, content, version)


def test_approved_plan_is_labeled_approved_with_its_approval_basis(bridge, human):
    bridge.create_task("demo", "新增 greet 函式")
    approve(bridge)

    package = bridge.continuity_handoff("demo", "TASK-0001", target="claude")

    assert "## 已核准計畫" in package
    assert "核准依據" in package
    assert "approved plan: PLAN-A v1 hash=" in package
    assert "新增 value API；保留原始註解。" in package


def test_newer_unapproved_draft_is_never_presented_as_approved(bridge, human):
    bridge.create_task("demo", "新增 greet 函式")
    approve(bridge)
    _propose(bridge, "PLAN-A", "DRAFT-ONLY：改用全新的實作方式。")

    package = bridge.continuity_handoff("demo", "TASK-0001", target="codex")

    assert "## 已核准計畫" not in package
    assert "approved plan: PLAN-A" not in package
    assert "plan status: draft" in package
    assert "NOT an approved implementation order" in package
    assert "未核准草稿，不是實作授權" in package
    # The earlier approved version is superseded by the newer draft, so it is not a basis.
    assert "已被此草稿取代" in package
    # Neither the draft's text nor the superseded text is handed over as an order.
    assert "DRAFT-ONLY" not in package
    assert "新增 value API；保留原始註解。" not in package
    assert "請 PM／使用者核准最新計畫後才可實作" in package


def test_pending_approval_request_is_a_draft_not_an_approval(bridge, human):
    bridge.create_task("demo", "新增 greet 函式")
    _propose(bridge, "PLAN-A", "新增 value API；保留原始註解。")
    bridge.request_approval("demo", "TASK-0001", "PLAN-A")

    package = bridge.continuity_handoff("demo", "TASK-0001")

    assert "## 已核准計畫" not in package
    assert "未核准草稿，不是實作授權" in package
    assert "新增 value API；保留原始註解。" not in package


def test_unconfirmed_approval_status_is_not_claimed_as_approved(bridge, human):
    bridge.create_task("demo", "新增 greet 函式")
    _propose(bridge, "PLAN-A", "新增 value API；保留原始註解。")
    plan = bridge.plan("demo", "TASK-0001", "PLAN-A")
    bridge.store.execute(
        "INSERT INTO approvals VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (
            "APP-unknown-status", "demo", "TASK-0001", "PLAN-A", plan["version"],
            plan["hash"], plan["context_version"], "UNKNOWN_STATUS", "test", "2026-01-01", None,
        ),
    )

    package = bridge.continuity_handoff("demo", "TASK-0001", target="claude")

    assert "核准狀態未確認" in package
    assert "approved plan:" not in package
    assert "## 已核准計畫" not in package
    assert "新增 value API；保留原始註解。" not in package


def test_no_plan_at_all_is_reported_plainly(bridge, human):
    bridge.create_task("demo", "新增 greet 函式")

    package = bridge.continuity_handoff("demo", "TASK-0001", target="chatgpt")

    assert "(none approved for this task)" in package
    assert "沒有 Approved Plan" in package
    assert "## 已核准計畫" not in package


def test_read_write_authorization_plan_is_labeled_with_its_real_basis(bridge, human):
    bridge.authorize_project_local("demo")
    bridge.create_task("demo", "新增 greet 函式")
    _propose(bridge, "PLAN-A", "新增 value API；保留原始註解。")

    package = bridge.continuity_handoff("demo", "TASK-0001", target="claude")

    # Under project authorization the proposal is recorded as APPROVED at once, so the
    # label must state that basis rather than imply an individual human review.
    assert "## 已核准計畫" in package
    assert "專案授權 read-write 模式自動核准" in package
    assert "新增 value API；保留原始註解。" in package
