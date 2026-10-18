# PM、SA、SD/RD 協作操作

此流程的工作記憶是 SQLite 與 `.ccc-mcp`，各 Agent 不讀取彼此聊天紀錄。所有 CLI 全域參數放在子命令之前，例如 `ccc-mcp --project my-project context show`。

## 1. PM 建立需求

```powershell
ccc-mcp init --id my-project --purpose "維護服務並保留 API 相容性"
ccc-mcp task create "新增健康檢查端點" --acceptance "GET /health 回傳 200" --acceptance "既有 API 不變"
```

需求與驗收原件持久保存，Context Patch 沒有更新原件的權限。若需求改變，另建任務並追加決策，保持歷史可追溯。

## 2. PM 指派 SA

MCP 範例：

```json
{
  "project_id": "my-project",
  "task_id": "TASK-0001",
  "agent": "codex",
  "role": "SA",
  "context_version": 1,
  "instruction": "分析端點路由、相關模組、驗收可行性與風險；引用程式碼。",
  "adapter": "fake"
}
```

派工回傳 execution_id，透過 `get_task_status` / `get_task_result` 查詢。Fake 報告明確標記 `simulated: true`；不應把其文字寫成已確認的系統架構。真實 SA 使用 Codex 唯讀沙箱。

SA Context Package 包含需求、驗收、架構與來源驗證、限制、相關決策、任務及 Git revision。SD/RD Package 另外包含核准方案、SA 原始報告及 Repository 位置。PM Package 包含進度、決策、最近 Agent 成果、風險與待決事項。

## 3. PM 整理成果與提案

```powershell
ccc-mcp context collect
ccc-mcp context source .ccc-mcp/tasks/TASK-0001/executions/EXEC-實際ID/SA-REPORT.md
```

使用來源工具回傳的 path/sha256，不要自行猜測雜湊。Patch 例如：

```json
{
  "base_context_version": 1,
  "changes": {
    "summary": "SA 已完成初步分析；風險與完整結論請見原件。",
    "next_action": "審閱 PLAN-A，必要時請 SA 補充分析。"
  },
  "source_task_ids": ["TASK-0001"],
  "source_document_refs": [],
  "update_reason": "PM 根據 SA 成果整理摘要",
  "author_role": "PM",
  "semantic_review_ack": true
}
```

實際使用時將剛取得的來源加入 source_document_refs；架構 facts 也必須帶各自的 source_document_refs。可用 `changes.source_document_refs` 將引用納入後續工作 Context，Patch 頂層引用另保存在版本稽核資料。

先 `propose_context_patch` 取得 diff，再 `commit_context_patch`。CLI 使用 `context propose PATCH.json` / `context commit PATCH-ID`。若版本衝突，重讀 Context 與來源後重新提出 Patch。

原始需求、決策、驗收、未解決 blocker 與約束不靠摘要文字判定是否保留。保護的 invariant 不可任意移除；資料庫任務/決策也不在可寫 Patch 欄位中。

## 4. 使用者審閱精確方案

先將預期修改範圍、限制、驗收與測試計畫寫入本機 PLAN-A.md：

```powershell
ccc-mcp task plan TASK-0001 PLAN-A PLAN-A.md --version 2
ccc-mcp task request-approval TASK-0001 PLAN-A
ccc-mcp approve TASK-0001 --plan PLAN-A
```

核准需操作者本人在終端輸入 Task ID。PM/MCP 呼叫只能建立 PENDING 請求，沒有 `approved=true` 的核准 API。方案 hash、方案版本、Context 版本與本機 OS principal 均留存。

若不同意：

```powershell
ccc-mcp reject TASK-0001 --plan PLAN-A --reason "驗收範圍尚未釐清"
```

重要業務決策可以在本機追加：

```powershell
ccc-mcp task decision TASK-0001 "保留既有回應格式" "避免破壞舊版呼叫端"
ccc-mcp task decision TASK-0001 "新決策內容" "取代原因" --status Superseded --supersedes DEC-實際ID
```

決策追加不會把舊列改掉。SQLite trigger 禁止更新/刪除既有決策與 Context 歷史。當前版本的方案變動或 Context 更新均會使舊核准不再適用，必須再次審閱。

## 5. SD/RD 在獨立 Worktree 工作

```powershell
ccc-mcp task dispatch TASK-0001 claude SD/RD "依 PLAN-A 實作並保留原始註解" --version 2 --adapter fake
ccc-mcp task status TASK-0001
ccc-mcp task result TASK-0001
```

每個 task 使用固定的獨立 Worktree；修訂任務沿用該 Worktree，不會丟失先前修改。Diff 是 Git 實際輸出，新增檔案另列 untracked_files 與可讀內容。模型的 TestClaim 僅是宣稱；由本機操作者授權的 `task test` 才是 host 測試證據。

```powershell
ccc-mcp task test TASK-0001
ccc-mcp task review TASK-0001 --version 2 --adapter fake
```

Review 有問題可回到 NEEDS_REVISION，PM 再指派 SD/RD；也可請 SA 再分析。若變更超出原方案，先提出新版本並重新核准。Python 原始註解被改動會 BLOCKED；其他語言或無法判定的修改也要求人工確認，不會自動完成任務。

最後由 PM 與使用者檢查結果：

```powershell
ccc-mcp task accept TASK-0001
ccc-mcp context export --out session-handoff.txt
```

本工具不合併 Worktree、不推送、不部署。合併前請人工審閱 Diff、註解與實際測試證據。

## 6. 全新 Session

啟用 ccc-mcp 連線並提出：

> 請使用 ccc-mcp 恢復目前專案的上下文，說明目前進度、最近決策、未完成工作與建議下一步。project_id 是 my-project。

只需 `bootstrap_project` 即能取得完整恢復入口。沒有 MCP 時貼入 `context export`。兩種方式都不要求歷史聊天、ChatGPT 個人 Memory 或先前模型 Session ID。
