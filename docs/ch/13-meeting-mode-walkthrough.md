# 13. Meeting Mode 端對端走查

這是一份可重複執行的步驟，用來端對端驗證 Meeting Mode 的 SD/RD↔SA 澄清對話，過程中 PM 仍然全程由 ChatGPT 擔任。在正式依賴 Meeting Mode 之前先照這份文件跑一次，並以下面這些步驟的實際輸出——而不是單純一句聊天回覆——當作證據。

**範圍限制：** Meeting Mode 的澄清對話目前還不支援互動式 ChatGPT 角色（見[可靠性與安全邊界](7-reliability-and-security.md#9-meeting-modesdrd-動工前的澄清對話三個保證)）——`dispatch_agent` 會直接拒絕並給出明確錯誤訊息。因此這份走查會把 `SA` 和 `SD/RD` 設成原生 CLI provider（Codex 和／或 Claude Code），不是 `chatgpt`；只有 PM 維持 ChatGPT，跟預設的 best-model 工作流一樣。未來版本可能補上這個限制。

## 先看一次真實執行

這是用 `meeting-test` 專案實際跑過這份流程的 ChatGPT 對話：[分享的對話](https://chatgpt.com/share/6adb059b-3eb8-83e8-96f2-84965ea20c56)。自己動手前，可以先看連線的 ChatGPT 在 Meeting Mode 執行期間會顯示什麼。ChatGPT 在聊天裡轉述的內容可能不完整，完整的 SA／SD 問答請在本機用 `ccc-mcp --project <id> task meeting` 查看（見[第 6 步](#6-讀取完整的澄清紀錄)）。

## 成功的標準

- `config show` 顯示 `meeting_mode.enabled: true`，`min_rounds`／`max_rounds`／`escalation` 跟你設定的一致。
- 這個任務 SD/RD 的第一次派工是唯讀的澄清檢查——`task status` 的 `brief.clarification.current_question` 有內容，task state 是 `AWAITING_SA_CLARIFICATION`，且還沒發生任何 worktree 建立或寫檔。這時 `brief.clarification.triggered` 也會變成 `true`——這才是告訴你關卡「真的觸發了」的欄位，`enabled: true` 只代表已經設定、不代表真的問過問題。
- SA 回答了這個問題（`confirmed`、`recommendation`，或升級為 `pm_decision_required`）；`brief.clarification.rounds_used` 只有在這一輪真正結束後才會增加。
- 真正可寫入的 SD/RD 實作派工，一定是在這一輪結束之後才會開始（或是走 `no_blocking_question`／`max_rounds` 用盡直接跳過澄清）。
- `task status`／`task show` 看得到這個任務完整的 `clarification.history`；`task handoff` 的輸出也帶著同一個區塊——這正是已連線 ChatGPT 對話的 `wait_for_task` 迴圈每一輪實際看到的內容。

## 1. 建立一個開啟 Meeting Mode 的專案

```bash
ccc-mcp project create meeting-mode-demo
ccc-mcp project authorize meeting-mode-demo
ccc-mcp --project meeting-mode-demo config role SA codex
ccc-mcp --project meeting-mode-demo config role SD/RD claude
ccc-mcp --project meeting-mode-demo config role REVIEW codex
ccc-mcp --project meeting-mode-demo config meeting-mode on --min-rounds 1 --max-rounds 3 --escalation ask-user
ccc-mcp --project meeting-mode-demo config show
```

確認 `config show` 的 `meeting_mode` 是 `{"enabled": true, "min_rounds": 1, "max_rounds": 3, "escalation": "ask_user"}`。

## 2. 連接 ChatGPT，提出一個故意講不清楚的需求

依[連接 ChatGPT](2-connect-chatgpt.md)設定好 Tunnel 並保持該終端機執行，然後對 ChatGPT 說（故意講得模糊，讓它比較可能提出澄清問題）：

> 請用 ccc-mcp 在 `meeting-mode-demo` 開一個專案需求：幫這個專案加一個小工具函式。完成 SA 之後，讓 SD/RD 跟 REVIEW 接力跑到完成。

## 3. 觀察澄清對話，而不是直接看到一般的實作派工

```bash
ccc-mcp --project meeting-mode-demo task status
```

一旦 SA 的方案核准、Meeting Mode 的關卡觸發，`task status` 的 `brief` 應該會顯示：

```jsonc
{
  "state": "AWAITING_SA_CLARIFICATION",
  "waiting_on": "AGENT_RUNNING",
  "role": "SA",
  "clarification": {
    "enabled": true,
    "min_rounds": 1,
    "max_rounds": 3,
    "escalation": "ask_user",
    "triggered": true,
    "rounds_used": 0,
    "current_question": "..."
  }
}
```

`clarification.triggered: true` 就是這次關卡真的觸發的訊號——只有真的存在一個阻斷性問題時才會是 `true`，光是 `enabled: true` 不算。如果 SD/RD 回報的是 `no_blocking_question`，`state` 會停在 `IMPLEMENTING`，真正的實作派工已經開始，`clarification.triggered` 會維持 `false`（連同 `rounds_used: 0`、`current_question: null`）；這也是一次合法、成功的執行（`min_rounds` 從不會因此擋住它）。

## 4. SA 回答後，實作會自動接續

`confirmed`／`recommendation` 這兩種回覆不需要任何 PM 動作——SA 的回答會自動派工，一旦這一輪結束，ccc-mcp 會自動派出下一輪澄清（如果 `rounds_used < max_rounds`）或是真正可寫入的 SD/RD 實作。持續查詢：

```bash
ccc-mcp --project meeting-mode-demo task status
```

直到 `brief.clarification.rounds_used` 顯示這一輪已結束，且 `brief.role`／`brief.execution_status` 顯示真正的 SD/RD 實作正在執行。

### 如果 SA 用 `pm_decision_required` 升級

`task status` 的 `brief.waiting_on` 會變成 `PM_CLARIFICATION_DECISION`，`brief.clarification.pending_pm_decision` 會帶著問題跟 SA 的建議。由誰決定取決於 `brief.clarification.escalation`：

- **`ask_user`（預設）。** 這是真正需要操作者決定的情況——`task handoff` 的 `next_action` 會明確這樣寫，而且 `wait_for_task` 的 `must_continue_in_this_turn` 在這裡會是 `false`，確認 ChatGPT 不該自己決定。請目前連線的 ChatGPT 對話把問題呈現給你，並等你的實際回覆：

  > 請用 ccc-mcp 的 resolve_clarification 工具對這個任務做決策，引用上面 SA 的建議。
- **`auto_pm`。** ChatGPT 可以用自己的判斷以 PM 身分立即決定，並在同一輪呼叫 `resolve_clarification`（`must_continue_in_this_turn` 會是 `true`）；之後仍應告知你決定了什麼、理由是什麼。

無論哪一種，`resolve_clarification` 都會立刻派出下一輪或真正的實作；這一步不需要另外呼叫 `advance_project`。

## 5. 讓 REVIEW 跟主機測試把任務跑完

從這裡開始，任務就跟其他任何一次接力一樣繼續：`task handoff`／`task status` 會依序顯示 SD/RD 實作、主機測試、REVIEW。這部分請參考[第 4 步](4-workflow.md)跟[第 12 步](12-chatgpt-only-e2e-walkthrough.md)（把其中的角色換成上面設定的原生 CLI provider；PM 仍然是 ChatGPT）。

## 6. 讀取完整的澄清紀錄

```bash
ccc-mcp --project meeting-mode-demo task meeting      # 易讀、不截斷的 SA／SD 對話紀錄
ccc-mcp --project meeting-mode-demo task status
ccc-mcp --project meeting-mode-demo task show
```

`brief.clarification.history` 會依序列出這個任務的每一輪（問題、是誰回答、`response_type`，以及任何 PM 決策）——這才是完整、可追溯的紀錄；一句「我問過了，沒問題」的聊天回覆從來不能取代它。

## 失敗時怎麼辦

如果派工被拒絕並顯示「does not yet support the interactive ChatGPT role」，代表還有角色設成 `chatgpt`——回去檢查 `config show`，把 `SA`／`SD/RD` 改成原生 CLI provider。如果澄清對話過程中 `task status` 顯示 `waiting_on: "DISPATCH_RETRY_NEEDED"`，照平常一樣執行 `ccc-mcp --project meeting-mode-demo task resume` 即可——輪次計數不會因此遺失或重複計算。

延伸閱讀：[可靠性與安全邊界](7-reliability-and-security.md#9-meeting-modesdrd-動工前的澄清對話三個保證)。
