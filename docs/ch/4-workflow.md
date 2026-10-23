# 4. PM、SA、SD/RD 協作操作

此流程的工作記憶是 SQLite 與 `.ccc-mcp`，各 Agent 不讀取彼此聊天紀錄。SA 的方案仍由 PM 整理；其後低風險的 SD/RD 成功報告會自動寫入 Context、執行主機測試並派出 REVIEW，低風險 REVIEW 成功則自動完成。失敗、測試未驗證、需要修訂或決策時才會停給 PM。所有 CLI 全域參數放在子命令之前，例如 `ccc-mcp --project my-project context show`。

## 同一輪 ChatGPT 對話等待工作流

透過 MCP 提出新的修改需求時，`start_project_request` 會派 SA 並先等待進度；`advance_project` 派出下一角色後也會等待。若單次長輪詢到期但 Agent 還在跑，回應的 `workflow.must_continue_in_this_turn` 會是 `true`，ChatGPT 必須在同一輪再次呼叫 `wait_for_task`，不能只回覆「執行中」後結束。SA 完成而需要 PM 整理時，ChatGPT 應在同一輪讀取報告、建立有來源的 ContextPatch／PLAN、呼叫 `advance_project`，再繼續等待 SD/RD、主機測試與 REVIEW。

只有以下情況才應停止等待並回覆使用者：任務已完成；真實失敗／阻擋；或需要使用者提供產品決策、本機核准等新資訊／權限。使用者若只是明確要求「查狀態」，則只回傳快照，不擅自推進。這是 MCP 呼叫端的同輪完成合約；ccc-mcp 不會替 PM 偽造 SA 報告的語意審查，但會把每次應繼續的動作與 `must_continue_in_this_turn` 一起回傳。

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

## 5a. Meeting Mode：SD/RD 動工前先問一輪（預設關閉）

```powershell
ccc-mcp --project my-project config meeting-mode on --min-rounds 1 --max-rounds 3 --escalation ask-user
```

開啟後，PLAN-A 核准、SD/RD 第一次真正動工前會插入一輪最多 5 次來回的澄清對話：SD/RD 可以先提出剛好一個阻斷性問題（這個階段完全唯讀，不能寫檔），由 SA 以 `confirmed`、`recommendation` 或 `pm_decision_required` 回覆。SD/RD 也可以直接回報 `no_blocking_question`，立即進入真正可寫檔的實作派工——但若設定了 `min_rounds`，在輪數達標前 SD/RD 會被要求「必須提問」，並接續下一輪；若 SD/RD 仍拒絕提問，流程不會卡死，`clarification.gate.min_rounds_unmet` 會標示未達標。輪次計數在重試、handoff、resume 之間永遠不會被重置，且這個澄清關卡每個任務只會觸發一次：之後若 REVIEW 要求修訂、重新派工 SD/RD，不會再問一次。

`task status`/`task handoff` 的 `clarification.gate` 用來證明關卡有跑過：SD/RD 的唯讀實作前檢查派工之前是 `null`，之後會顯示 `status`、`outcome`（`question_asked` 或 `no_blocking_question`）與 SD/RD 自己的 `rationale`——所以「檢查過、沒有問題要問」和「Meeting Mode 根本沒跑」可以區分。`clarification.triggered` 只有在 SD/RD 真的問出一個阻斷性問題後才會是 `true`——光是 `meeting_mode.enabled` 為 `true` 並不算。每一次 `wait_for_task`/`start_project_request`/`advance_project`/`dispatch_agent` 的回應（`workflow.handoff.clarification`）都帶著這個欄位，所以已連線的 ChatGPT 對話應該在關卡真正觸發時主動告知操作者，並從 `clarification.history` 摘要 SA/SD 的來回內容，而不是等任務完成才提一句。

`--escalation` 決定 SA/SD 討論未果（`pm_decision_required`）時由誰決定：`ask_user`（預設）要求 ChatGPT 把問題與 SA 的建議呈現給真人操作者，等待其在對話中的實際回覆後才能呼叫 `resolve_clarification`——此時 `workflow.must_continue_in_this_turn` 會是 `false`，確認這是真正需要停下來的情況。`auto_pm` 則允許 ChatGPT 在同一輪以 PM 身分立即自行判斷決定（`must_continue_in_this_turn` 為 `true`），之後仍應把決定內容與理由告知操作者。這項設定是透過指令引導、而非程式碼強制執行——ccc-mcp 無法從後端驗證「是否真的詢問了真人」——詳見[可靠性與安全邊界](7-reliability-and-security.md#meeting-mode)的完整保證範圍。

## 6. 全新 Session

啟用 ccc-mcp 連線並提出：

> 請使用 ccc-mcp 恢復目前專案的上下文，說明目前進度、最近決策、未完成工作與建議下一步。project_id 是 my-project。

只需 `bootstrap_project` 即能取得完整恢復入口。沒有 MCP 時貼入 `context export`。兩種方式都不要求歷史聊天、ChatGPT 個人 Memory 或先前模型 Session ID。
