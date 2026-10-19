from pathlib import Path

import pytest

from ccc_mcp.infra.errors import BridgeError
from ccc_mcp.worker import run_execution


def refine(bridge, execution_id, summary):
    item = next(
        result
        for result in bridge.latest("demo")["snapshot"]["agent_results"]
        if result["execution_id"] == execution_id
    )
    proposal = bridge.propose_context_patch(
        "demo",
        {
            "base_context_version": bridge.latest("demo")["version"],
            "changes": {"summary": summary, "next_action": "繼續目前專案工作串。"},
            "source_execution_ids": [execution_id],
            "source_task_ids": [item["task_id"]],
            "source_document_refs": [item["source_document_ref"]],
            "update_reason": "PM 每輪整理並壓縮共用上下文",
            "semantic_review_ack": True,
        },
    )
    return bridge.commit_context_patch("demo", proposal["patch_id"])


def dispatch(bridge, task_id, role):
    result = bridge.dispatch_agent(
        "demo",
        task_id,
        "codex" if role in {"SA", "REVIEW"} else "claude",
        role,
        bridge.latest("demo")["version"],
        "執行目前專案工作",
        adapter="fake",
    )
    run_execution(bridge, result["execution_id"])
    return result["execution_id"]


def pending_patch(bridge, execution_id):
    result = next(
        item for item in bridge.latest("demo")["snapshot"]["agent_results"]
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


def test_project_advance_automatically_dispatches_claude_then_codex(bridge, human):
    bridge.authorize_project_local("demo")
    task_id = bridge.create_task("demo", "Implement a change")["id"]
    sa = dispatch(bridge, task_id, "SA")
    with pytest.raises(BridgeError, match="reviewed implementation plan"):
        bridge.advance_project("demo", pending_patch(bridge, sa), adapter="fake")
    implementation = bridge.advance_project(
        "demo", pending_patch(bridge, sa), "Implement the approved change", adapter="fake"
    )["next_execution"]
    assert bridge.store.one(
        "SELECT agent,role FROM agent_executions WHERE id=?", (implementation["execution_id"],)
    ) == {"agent": "claude", "role": "SD/RD"}
    run_execution(bridge, implementation["execution_id"])
    review = bridge.advance_project(
        "demo", pending_patch(bridge, implementation["execution_id"]), adapter="fake"
    )["next_execution"]
    assert bridge.store.one(
        "SELECT agent,role FROM agent_executions WHERE id=?", (review["execution_id"],)
    ) == {"agent": "codex", "role": "REVIEW"}
    run_execution(bridge, review["execution_id"])
    completed = bridge.advance_project(
        "demo", pending_patch(bridge, review["execution_id"]), adapter="fake"
    )
    assert completed["completed_workstreams"] == [task_id]
    assert bridge.task("demo", task_id)["state"] == "COMPLETED"


def test_review_revision_automatically_returns_to_implementation(bridge, human):
    bridge.authorize_project_local("demo")
    task_id = bridge.create_task("demo", "Implement a change")["id"]
    sa = dispatch(bridge, task_id, "SA")
    implementation = bridge.advance_project(
        "demo", pending_patch(bridge, sa), "Implement the change", adapter="fake"
    )["next_execution"]
    run_execution(bridge, implementation["execution_id"])
    review = bridge.advance_project(
        "demo", pending_patch(bridge, implementation["execution_id"]),
        adapter="fake", fake_scenario="revision"
    )["next_execution"]
    run_execution(bridge, review["execution_id"])
    assert bridge.task("demo", task_id)["state"] == "NEEDS_REVISION"
    revision = bridge.advance_project(
        "demo", pending_patch(bridge, review["execution_id"]), adapter="fake"
    )["next_execution"]
    saved = bridge.store.one(
        "SELECT agent,role FROM agent_executions WHERE id=?", (revision["execution_id"],)
    )
    assert saved == {"agent": "claude", "role": "SD/RD"}


def test_project_authorization_drives_one_continuous_workstream(bridge, human):
    bridge.authorize_project_local("demo")

    first = bridge.create_task("demo", "加入第一個功能", ["功能可用"])
    task_id = first["id"]
    assert task_id == "TASK-0001"
    assert first["workstream"] is True

    sa = dispatch(bridge, task_id, "SA")
    refine(bridge, sa, "SA 結果已由 PM 整理。")
    assert bridge.latest("demo")["snapshot"]["agent_results"] == []

    plan = bridge.propose_plan(
        "demo", task_id, "PLAN-A", "實作並驗證第一個功能。", bridge.latest("demo")["version"]
    )
    assert plan["status"] == "APPROVED"
    assert bridge.task("demo", task_id)["state"] == "APPROVED"
    assert bridge.get_project_settings("demo")["project_access"]["requires_plan_approval"] is False

    implementation = dispatch(bridge, task_id, "SD/RD")
    refine(bridge, implementation, "實作結果已由 PM 整理。")
    review = dispatch(bridge, task_id, "REVIEW")
    committed = refine(bridge, review, "審查通過，本輪完成。")
    assert committed["completed_workstreams"] == [task_id]
    assert bridge.task("demo", task_id)["state"] == "COMPLETED"
    assert bridge.context_sync_status("demo")["pending_reports"] == []

    second = bridge.create_task("demo", "繼續加入第二個功能", ["沿用既有上下文"])
    assert second["id"] == task_id
    assert second["reused"] is True
    assert second["state"] == "CREATED"
    assert len(bridge.list_tasks("demo")) == 1
    requirements = bridge.store.all(
        "SELECT path FROM artifacts WHERE project_id='demo' AND path LIKE '%/requirements/%'"
    )
    assert len(requirements) == 2


def test_language_defaults_to_chinese_and_can_be_english(bridge):
    assert bridge.get_project_settings("demo")["language"] == "zh-TW"
    assert "## 專案工作串" in (
        Path(bridge.project("demo")["root"]) / ".ccc-mcp/CONTEXT.md"
    ).read_text(encoding="utf-8")

    bridge.configure_language("demo", "en")
    package = bridge.build_handoff("demo", "PM")
    assert package["language"] == "en"
    assert package["content"]["language"] == "en"
    assert "## Project workstream" in (
        Path(bridge.project("demo")["root"]) / ".ccc-mcp/CONTEXT.md"
    ).read_text(encoding="utf-8")


def test_simplified_chinese_language_covers_project_and_agent_outputs(bridge, tmp_path):
    from typer.testing import CliRunner

    from ccc_mcp.adapters.agents.base import BaseAgentAdapter
    from ccc_mcp.interfaces.cli import app

    root = Path(bridge.project("demo")["root"])
    configured = CliRunner().invoke(
        app, ["--home", str(bridge.home), "--project", "demo", "config", "language", "ch"]
    )
    assert configured.exit_code == 0, configured.output
    assert bridge.get_project_settings("demo")["language"] == "ch"
    context = (root / ".ccc-mcp/CONTEXT.md").read_text(encoding="utf-8")
    assert "## 当前状态" in context
    assert "## 项目工作流" in context
    assert "## 证据位置" in context
    package = bridge.build_handoff("demo", "PM")
    assert package["language"] == "ch"
    assert "简体中文" in package["policy"]
    assert "简体中文" in BaseAgentAdapter.prompt(None, package, "检查")
    task = bridge.create_task("demo", "增加功能", ["返回结果"])
    requirement = root / ".ccc-mcp/tasks" / task["id"] / "REQUIREMENT.md"
    assert "## 验收条件" in requirement.read_text(encoding="utf-8")

    new_root = tmp_path / "simplified"
    new_root.mkdir()
    bridge.register_project(new_root, "simplified", language="ch")
    assert bridge.get_project_settings("simplified")["language"] == "ch"
    assert "协作开发与维护此项目" in (new_root / ".ccc-mcp/CONTEXT.md").read_text(encoding="utf-8")

    created = bridge.create_project("new-ch", tmp_path / "new-ch", language="ch")
    assert bridge.get_project_settings(created["id"])["language"] == "ch"
    assert "由 ccc-mcp 管理的项目" in (Path(created["root"]) / "README.md").read_text(
        encoding="utf-8"
    )
