# 連接真實 SA 與 SD/RD

兩個角色透過 AgentBridge 的報告與核准方案交接。ChatGPT PM 依進度決定每次派工，不依賴 Agent 彼此的聊天紀錄。

```text
ChatGPT PM → Codex SA → SA-REPORT.md
          → PM 讀取報告、提出方案
          → 使用者本機核准
          → Claude Code SD/RD（獨立 Worktree）
          → IMPLEMENTATION.md / TEST-RESULT.md
          → PM 檢查、需要時交 Codex Review
```

## 1. 安裝與登入

需要各自的官方原生 CLI 及帳戶登入。已有 VS Code Codex 擴充套件的 Windows 安裝，可由設定腳本找到其隨附的 codex.exe；版本更新後重新 source 腳本即可重新尋找。

Claude Code 請依 [官方安裝文件](https://code.claude.com/docs/en/setup) 安裝原生版本；Windows 可使用官方列出的 WinGet 安裝方式：

```bash
winget install Anthropic.ClaudeCode
```

安裝後開新的 Git Bash，執行 `claude auth login` 完成官方登入。`claude --version` 與 `claude auth status --text` 可確認狀態。AgentBridge 不替代兩個 CLI 各自的登入或模型使用權限；Tunnel runtime key 用途是連接 Tunnel。

若 WinGet 顯示已安裝，但 Git Bash 回報 `claude: command not found`，請先於本專案根目錄執行 `source scripts/enable-real-agents.sh`，再執行 `claude --version` 與 `claude auth login`。設定腳本可直接尋找 WinGet 的 Claude 套件目錄，補入目前終端的 PATH；不必重新安裝。

## 2. 在 Git Bash 啟用真實派工並重啟 Tunnel

先在原本連線的終端按 Ctrl+C。接著於專案根目錄執行：

```bash
source scripts/enable-real-agents.sh
bash scripts/connect-chatgpt.sh tunnel_你的實際ID
```

設定腳本只檢查 CLI 路徑與登入狀態，將路徑及 `AGENTBRIDGE_ENABLE_REAL_AGENTS=1` 放入目前終端環境，不執行模型任務。必須使用 **source**，並在同一個終端重新啟動 Tunnel；既有 Server 不會自動取得後來變更的環境變數。

缺少 Claude 時仍可先連接 SA；SD/RD 要等 Claude 安裝及登入完成。實際派工前 Adapter 也會檢查 CLI 必要參數。

Windows 的 Codex Adapter 會明確傳入 `-c windows.sandbox="elevated"`，同時保留 `--ignore-user-config`、`--sandbox read-only` 與 `-a never`。忽略使用者設定時，若沒有指定 Windows 沙箱，可能導致所有讀檔命令都被政策拒絕。這使用 [OpenAI 官方 Windows 沙箱](https://learn.chatgpt.com/docs/windows/windows-sandbox)；本機必須已完成所需的沙箱設定。

受限 CLI 不一定提供目前 IDE 的 MCP 工具，也可能無法讀取沙箱外的技能檔案。SA 應記錄這些限制，繼續使用被允許的專案唯讀查詢；不得重試被拒絕的路徑、變更權限或宣稱完成圖譜驗證。

執行狀態 `SUCCEEDED` 表示 CLI 已正常回傳結構化報告，不代表需求驗收通過。PM 必須讀取報告的 `needs_revision`、`findings` 與證據，再決定下一步；若報告表示無法讀取程式碼，不能當成分析成功。

## 3. 準備被開發專案的 Git 基準

Codex 的唯讀 SA/Review 支援尚未建立 Git 的已註冊專案：Adapter 使用 `--skip-git-repo-check`，並保留 `--sandbox read-only` 與 `-a never`。此選項依 [OpenAI 官方 CLI 文件](https://learn.chatgpt.com/docs/cli/reference) 允許非 Git 目錄；不解除唯讀限制。SD/RD 的 Worktree 仍需要以下 Git 基準。

實作 Worktree 以已提交的 HEAD 建立。先在要開發的 Repository 中審閱檔案與 `.gitignore`，建立初始 commit，確認要提供給 Agent 的程式已提交。不要將 `.env`、秘密金鑰、`.tunnel/` 或工具資料夾納入提交。

在 AgentBridge 本專案第一次建立 Git 基準，可先 `git init`、以 `git add` 加入你已審閱的原始碼，再 `git commit`。若要開發另一個已有 Git 的專案，改用 `agentbridge project add 路徑 --id 專案ID` 註冊該專案。

## 4. 在 ChatGPT 發起真實任務

請建立新的需求任務。先前僅允許 fake 的測試任務不要拿來直接轉成真實開發任務。

SA 的 `dispatch_agent` 參數：

```json
{
  "project_id": "你的專案ID",
  "task_id": "新任務ID",
  "agent": "codex",
  "role": "SA",
  "context_version": 2,
  "instruction": "分析這次需求的實作範圍、風險及驗收，引用相關程式碼；唯讀。",
  "adapter": "real"
}
```

上方為參數示意，將 project/task ID 與 context_version 換成實際值；2 只是版本範例。PM 可先 bootstrap，再建立任務、派工，之後透過 get_task_status/get_task_result 讀取結果。

SA 完成後，PM 用 propose_plan 提出具體方案，request_approval 建立 PENDING 請求。Context 更新完成後，再針對目前版本提出要核准的方案。你本人在本機審閱後執行：

```bash
./.venv/Scripts/agentbridge.exe --project 你的專案ID approve 新任務ID --plan PLAN-A
```

核准後，PM 再派 SD/RD：`agent=claude`、`role=SD/RD`、`adapter=real`，Context Version 必須與核准紀錄相符。AgentBridge 自動建立含 SA 報告與核准方案的交接套件；Claude 在獨立 Worktree 執行。

目前的實作使用獨立 CLI 程序。結果存於 AgentBridge，可由網頁工具或 CLI 查詢；未與目前 VS Code Codex 對話視窗同步。
