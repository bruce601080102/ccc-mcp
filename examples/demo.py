"""No model calls. Human approval is still performed in the real local CLI."""

import argparse
import json
import subprocess
import time
from pathlib import Path

from agentbridge_mcp.service import AgentBridge


def wait(bridge, execution):
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        row = next(
            r
            for r in bridge.get_task_status("demo", "TASK-0001")["executions"]
            if r["id"] == execution
        )
        if row["status"] not in {"QUEUED", "RUNNING"}:
            if row["status"] != "SUCCEEDED":
                raise SystemExit(row["error"])
            return
        time.sleep(0.1)
    raise SystemExit("Worker timeout; inspect agentbridge task status")


def main():
    parser = argparse.ArgumentParser(description="Fake 協作範例；不自動人工核准")
    parser.add_argument("stage", choices=["prepare", "implement", "recover"])
    parser.add_argument("--directory", type=Path, help="另選新的示範資料夾；不會刪除既有資料")
    args = parser.parse_args()
    folder = (args.directory or Path(__file__).resolve().parent.parent / ".demo").resolve()
    bridge = AgentBridge(folder / "hub")
    if args.stage == "prepare":
        repo = folder / "project"
        if repo.exists():
            if bridge.project("demo")["root"] != str(repo) or bridge.latest("demo")["version"] != 1:
                raise SystemExit(
                    "Demo already prepared; use implement/recover or a new --directory."
                )
        else:
            repo.mkdir(parents=True)
            (repo / "app.py").write_text(
                "# Preserve this original comment.\ndef health():\n    return 'ok'\n",
                encoding="utf-8",
            )
            for command in [
                ["init"],
                ["add", "app.py"],
                [
                    "-c",
                    "user.name=AgentBridge Demo",
                    "-c",
                    "user.email=demo@example.invalid",
                    "commit",
                    "-m",
                    "Demo baseline",
                ],
            ]:
                subprocess.run(
                    ["git", "-c", "core.hooksPath=", "-C", str(repo), *command],
                    check=True,
                    capture_output=True,
                    stdin=subprocess.DEVNULL,
                )
            bridge.register_project(repo, "demo", "展示沒有歷史聊天的跨 Agent 與跨 Session 協作")
        if not bridge.list_tasks("demo"):
            bridge.create_task("demo", "分析健康檢查 API 的修改範圍", ["保留既有註解與相容性"])
        results = bridge.get_task_result("demo", "TASK-0001")
        queued = (
            {"execution_id": results[-1]["id"]}
            if results
            else bridge.dispatch_agent(
                "demo", "TASK-0001", "codex", "SA", 1, "驗證角色交接", adapter="fake"
            )
        )
        wait(bridge, queued["execution_id"])
        ref = bridge.source_ref(
            "demo", f".agentbridge/tasks/TASK-0001/executions/{queued['execution_id']}/SA-REPORT.md"
        )
        proposal = bridge.propose_context_patch(
            "demo",
            {
                "base_context_version": 1,
                "changes": {
                    "summary": "Fake SA 已完成交接測試；不代表實際分析完成。",
                    "source_document_refs": [ref],
                    "next_action": "使用者審閱 PLAN-A 後在本機終端核准。",
                },
                "source_task_ids": ["TASK-0001"],
                "source_document_refs": [ref],
                "update_reason": "PM 整理 Fake SA 成果",
                "semantic_review_ack": True,
            },
        )
        bridge.commit_context_patch("demo", proposal["patch_id"])
        plan = Path(__file__).with_name("PLAN-A.md").read_text(encoding="utf-8")
        bridge.propose_plan("demo", "TASK-0001", "PLAN-A", plan, 2)
        bridge.request_approval("demo", "TASK-0001", "PLAN-A")
        print(
            "Prepared. Review and run: agentbridge --home .demo/hub --project demo approve TASK-0001 --plan PLAN-A"
        )
    elif args.stage == "implement":
        version = bridge.latest("demo")["version"]
        queued = bridge.dispatch_agent(
            "demo",
            "TASK-0001",
            "claude",
            "SD/RD",
            version,
            "依核准方案驗證交接，不修改範例程式",
            adapter="fake",
        )
        wait(bridge, queued["execution_id"])
        print(
            json.dumps(
                bridge.get_task_result("demo", "TASK-0001")[-1], ensure_ascii=False, indent=2
            )
        )
    else:
        print(bridge.export_context("demo"))


if __name__ == "__main__":
    import sys

    sys.stdout.reconfigure(encoding="utf-8")
    main()
