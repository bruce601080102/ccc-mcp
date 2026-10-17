# AgentBridge MCP

在 ChatGPT 提需求，讓 Codex 分析、Claude Code 開發。AgentBridge 負責保存任務、計畫、報告與交接資料。

```text
你提需求 → ChatGPT 整理方案 → 你核准 → Claude 開發 → 本機測試與驗收
                   ↑
             需要分析時交給 Codex
```

以下以 **Windows Git Bash** 操作，指令都在 **AgentBridge 專案根目錄**執行。已安裝並連線者，直接看「日常用法」。

## 1. 安裝（只做一次）

準備 Python 3.11+、Git，以及已安裝並登入的 Codex CLI。已有 VS Code Codex 擴充套件時，設定腳本可找到其中的原生 CLI。

```bash
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -e ".[dev]"
./.venv/Scripts/agentbridge.exe doctor
```

安裝並登入 Claude Code：

```bash
winget install Anthropic.ClaudeCode
source scripts/enable-real-agents.sh
claude auth login
source scripts/enable-real-agents.sh
```

腳本會檢查登入、補上 CLI 路徑並啟用真實派工。若提示 Codex 未登入，先執行 `codex login`，再重新 source 腳本。遇到問題看 [真實 Agent 設定](docs/real-agents.md)。

指令使用完整路徑，因此不必另外啟用 `.venv`，也不需要直接輸入 `agentbridge`。

## 2. 註冊要開發的專案（每個專案一次）

將路徑與 `my-project` 換成你的專案：

```bash
./.venv/Scripts/agentbridge.exe project add "C:/path/to/project" --id my-project
```

Claude 實作需要目標專案已有 Git commit；Worktree 從已提交的程式建立，不含尚未提交的修改。只做唯讀 SA 分析時不需要 Git。

## 3. 連接 ChatGPT

首次先依 [ChatGPT 連線指南](docs/chatgpt.md) 建立 Tunnel，並在 ChatGPT 加入 AgentBridge。之後在同一個終端執行：

```bash
source scripts/enable-real-agents.sh
bash scripts/connect-chatgpt.sh tunnel_你的實際ID
```

依提示在本機輸入金鑰。看到 `tunnel-client started` 後保持終端開啟；腳本會啟動 AgentBridge，不必另外執行 `serve`。

## 4. 日常用法

**先在 ChatGPT 說需求：**

> 請使用 AgentBridge 管理 my-project。我的需求是：＿＿＿。需要時交給真實 Codex SA 分析，再提出 PLAN-A 等我核准。

**計畫看過後，在另一個 Git Bash 終端核准：**

```bash
./.venv/Scripts/agentbridge.exe --project my-project approve TASK-0001 --plan PLAN-A
```

專案 ID、任務 ID 與計畫 ID 以 ChatGPT 回覆為準。終端會顯示計畫，要求你輸入任務 ID 確認。

**再告訴 ChatGPT 開始開發：**

> 已完成本機核准，請交給真實 Claude SD/RD 實作。完成後讀取原始報告，列出修改檔案、Worktree 路徑及測試狀態。

**實作完成後執行測試：**

```bash
./.venv/Scripts/agentbridge.exe --project my-project task test TASK-0001
```

這會在 Worktree 執行 pytest，依本機提示確認後保存真實測試結果。確認成果符合需求後，再標記完成：

```bash
./.venv/Scripts/agentbridge.exe --project my-project task accept TASK-0001
```

有效的同版本核准可供失敗重試使用；計畫或 Context Version 變更後才需重新核准。

你不用背 `bootstrap_project` 等工具名稱。新對話或切換專案時，直接說：

> 請恢復 my-project 的進度，讀取最新報告並告訴我下一步。先不要重複派工。

## 5. 怎麼確認用了 Codex 或 Claude？

查詢本機執行紀錄：

```bash
./.venv/Scripts/agentbridge.exe --project my-project task result TASK-0001
```

| 欄位 | 看什麼 |
| --- | --- |
| `agent` / `role` | `codex / SA` 是分析；`claude / SD/RD` 是開發 |
| `adapter` | `real` 使用本機 CLI；`fake` 是模擬 |
| `report.simulated` | 正常真實報告為 `false`，仍需一起核對 Adapter 與原始成果 |
| `report.needs_revision` | `true` 表示報告指出還需修正，不能只看 `SUCCEEDED` |
| `test_evidence` | 模型的測試描述與本機實際執行證據分開保存 |

同一任務可能有多次執行，請核對 Execution ID；報告中的 `simulated` 只是輔助欄位，需搭配本機派工紀錄與成果確認。

執行中也可在 Windows 工作管理員看到 `codex.exe` 或 `claude.exe`。它們是獨立背景程序，結果不會新增到你目前的 VS Code Codex 對話。簡單任務可以直接交 Claude，不一定兩個 Agent 都會使用。

## 6. 程式與報告在哪裡？

- **報告**：被管理專案內的 `.agentbridge/tasks/任務ID/`；`executions/` 保存歷次原件。
- **實作程式**：預設在 `~/.agentbridge/worktrees/專案ID/任務ID/`，實際路徑以執行結果為準。
- **測試結果**：任務目錄的 `TEST-RESULT.md`，讀取 `passed` 與實際輸出。

程式在獨立 Worktree，原專案不會自動合併，也不會自動 push 或部署。同一個 `TASK-0001` 可以出現在不同專案，查詢時要帶對 `--project`。

## 詳細文件

- [ChatGPT 首次連線與故障排除](docs/chatgpt.md)
- [真實 Codex／Claude 設定](docs/real-agents.md)
- [完整指令、MCP 設定與進階參考](docs/reference.md)
- [協作流程範例](docs/workflow.md) · [驗證紀錄](VALIDATION.md) · [原始規格](docs/SPEC-V2.md)
