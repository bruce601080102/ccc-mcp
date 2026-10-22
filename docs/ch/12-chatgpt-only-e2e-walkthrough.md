# 12. ChatGPT-only 真人端對端驗收範例

本篇是一次**真人 ChatGPT 對話**完成的最小端對端驗收紀錄，目的是讓你能重跑同一條路徑、判斷哪些輸出才算證據，並知道流程卡住時該回到哪一步。它不是模擬測試，也不是「模型說完成」就算通過。

這個範例建立 `hello.py`，再建立會以 `subprocess` 執行該檔案的 `test_hello.py`。實際紀錄在 2026-10-22 完成了 ChatGPT 的 `SA → SD/RD → REVIEW` 接力，主機執行 pytest 後得到 `1 passed`，任務最後為 `COMPLETED`。

## 成功的標準

以下條件必須同時成立：

- 五個角色 `SA`、`SD`、`RD`、`SD/RD`、`REVIEW` 的 provider 都是 `chatgpt`。
- 工作流交接給 `SA`、`SD/RD`、`REVIEW` 的三個階段都留下成功的 interactive execution。
- 主機而非 Agent 實際執行 pytest，且至少收集一個測試。
- `task status` 顯示 `executed_by_host: true`、`passed: true`，且 `brief.verification.host_verification` 是 `passed`。
- REVIEW 已完成，task state 是 `COMPLETED`。

只看到 ChatGPT 說「完成」或 `agent_claim: complete` 都不夠。

## 1. 建立隔離專案並保存設定證據

在已安裝 ccc-mcp 的 Python 環境執行：

```bash
conda activate stock311

ccc-mcp project create chatgpt-e2e-demo --profile chatgpt-only
ccc-mcp project authorize chatgpt-e2e-demo

cd ~/ccc-mcpProjects/chatgpt-e2e-demo
mkdir -p evidence
ccc-mcp --project chatgpt-e2e-demo config show | tee evidence/01-config-show.json
```

`project authorize` 是本機明確同意：只允許 ccc-mcp 在這個專案內、透過已授權 task execution 寫檔。`--profile chatgpt-only` 會把五個角色都設為 ChatGPT；`config show` 的 `profile` 必須是 `chatgpt-only`，每一個 role 的 `provider` 必須是 `chatgpt`。

為讓測試行為不依賴預設值，建議明確設定主機測試命令：

```bash
ccc-mcp --project chatgpt-e2e-demo config test-command "python -m pytest -q"
```

這個命令由主機執行，不會交給 Agent。某些舊版輸出可能顯示 `test_command: null`，但仍使用預設 pytest；為了讓教學與未來版本都可重跑，本篇一律建議顯式設定。

## 2. 連接 ChatGPT，並由它建立 task

依[連接 ChatGPT](2-connect-chatgpt.md)設定 Tunnel，並保持連線程序運作：

```bash
ccc-mcp connect tunnel_你的實際ID
```

在 ChatGPT 選取 ccc-mcp 連線，然後要求它**實際呼叫工具**：

> 請使用 ccc-mcp，在 `chatgpt-e2e-demo` 建立 task：建立 `hello.py`，執行時標準輸出必須為 `Hello, World!`（含換行）；建立可由 pytest 收集的 `test_hello.py`，使用 subprocess 實際執行 `hello.py` 並驗證 stdout 與 return code。請依 SA → SD/RD → REVIEW 完成，不要只給我程式碼或建議；主機測試證據才算通過。

這一步會建立 task 並先派發 interactive 的 SA。`task preflight` 必須在 task 已經存在後才能執行；若在建立 task 前執行而收到 `Project workstream has not started`，這是預期行為，不是環境錯誤。

## 3. 使用 handoff 完成 SA

當 ChatGPT 已建立 task 後，在本機查詢：

```bash
ccc-mcp --project chatgpt-e2e-demo task preflight
ccc-mcp --project chatgpt-e2e-demo task handoff | tee evidence/02-handoff-sa.json
ccc-mcp --project chatgpt-e2e-demo task status
```

成功開始時，`task handoff` 應有：

```jsonc
{
  "state": "ANALYZING",
  "waiting_on": "CHATGPT_HANDOFF_PENDING",
  "role": "SA",
  "agent": "chatgpt",
  "chatgpt_prompt": "...",
  "execution_id": "EXEC-..."
}
```

把輸出的 `chatgpt_prompt` 貼回**已選取 ccc-mcp 連線**的 ChatGPT 對話。SA 是唯讀角色：它讀取 context 與檔案後，必須使用該 execution ID 呼叫 `submit_agent_report`。它不應寫 `hello.py`，也不應自行聲稱已執行主機測試。

## 4. 完成 SD/RD、主機測試與 REVIEW

SA report 完成後，PM 會整理 Context、建立或自動核准目前計畫，然後派發 SD/RD。重複查詢：

```bash
ccc-mcp --project chatgpt-e2e-demo task handoff
ccc-mcp --project chatgpt-e2e-demo task status
```

當 handoff 的 role 是 `SD/RD`，將新的 `chatgpt_prompt` 貼給 ChatGPT。SD/RD 必須透過 `write_task_file` 寫入 `hello.py` 與 `test_hello.py`，最後提交 report。它不能以聊天文字取代檔案寫入或 report。

SD/RD report 被 PM 整理後，ccc-mcp 會在派發 REVIEW 前由**主機**執行設定的 pytest 命令。接著 `task handoff` 會顯示 REVIEW；將其 prompt 交給 ChatGPT，讓它以唯讀方式查核檔案和證據，然後提交 REVIEW report。

同一輪真人紀錄中，事件順序是：

```text
CREATED → ANALYZING (ChatGPT / SA)
→ AWAITING_PM_DECISION → APPROVED
→ IMPLEMENTING (ChatGPT / SD/RD)
→ AWAITING_PM_REVIEW → REVIEWING (ChatGPT / REVIEW)
→ AWAITING_PM_REVIEW → COMPLETED
```

不要假設某一步會自動完成；每次 ChatGPT 回覆後都以 `task status` 或 `task handoff` 為準。

## 5. 讀取最終驗收證據

```bash
ccc-mcp --project chatgpt-e2e-demo task status | tee evidence/03-final-status.json
ccc-mcp --project chatgpt-e2e-demo task show
```

這次真人驗收的關鍵輸出如下（execution ID、使用者名稱與絕對路徑應視需要遮蔽）：

```jsonc
{
  "state": "COMPLETED",
  "brief": {
    "tests": {
      "executed_by_host": true,
      "passed": true,
      "no_tests_collected": false
    },
    "verification": {
      "agent_claim": "complete",
      "host_verification": "passed",
      "reverted_due_to_host_failure": false
    }
  }
}
```

`task show` 也應列出三個 interactive execution：ChatGPT / SA、ChatGPT / SD/RD、ChatGPT / REVIEW，且 SD/RD 的程式位置是此專案根目錄（持續授權的 project workspace）。

可選擇在人類確認後手動重跑一次測試，取得完整 stdout、exit code 與實際 command：

```bash
ccc-mcp --project chatgpt-e2e-demo task test TASK-0001
```

這不是讓 task 從失敗變通過的替代手段；主要通過證據應已在 REVIEW 前由主機自動留下。手動重跑適合做最後的人為複核或截圖。

## 6. 失敗時怎麼處理

若 `brief.tests.no_tests_collected` 是 `true`，或 `host_verification` 是 `failed`，**不要**把結果放進 README 當成功案例。ccc-mcp 應將流程退回 SD/RD。執行：

```bash
ccc-mcp --project chatgpt-e2e-demo task handoff
```

把新的 SD/RD prompt 交給 ChatGPT，要求它修正或新增可被 pytest 收集的測試，提交 report 後讓工作流重新跑到 REVIEW。只有新的主機測試通過後，`host_verification` 才會變成 `passed`。

## README 截圖清單

所有截圖都必須來自**同一次已完成的 task**，並移除 Tunnel ID、Secret Key、authorization ID、個人帳號、內部路徑與不想公開的 requirement。建議：

1. `01-profile.png`：`config show`，五個角色皆為 ChatGPT，`profile` 是 `chatgpt-only`。
2. `02-handoff.png`：`task handoff`，顯示 `CHATGPT_HANDOFF_PENDING`、角色與交接 prompt。
3. `03-host-tests-passed.png`：最終 `task status` 或 `task show`，顯示 `COMPLETED`、`executed_by_host: true`、`passed: true`、`host_verification: passed`。

README 可保留第一張和第三張；本篇與 `evidence/` 中的原始 JSON 才是完整可追溯紀錄。不要把 ChatGPT 分享連結或單一聊天截圖當成唯一驗收證據。

延伸閱讀：[架構規格](11-architecture-spec.md)。
