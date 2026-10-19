# 連接真實 SA 與 SD/RD

下列為預設的 Codex SA／Claude SD/RD 組合。角色現在可互換，或全部交給 ChatGPT；模型、思考等級、用量與路徑請看 [設定指南](configuration.md)。只用 ChatGPT 時不需要安裝本機模型 CLI。

兩個角色透過 ccc-mcp 的報告與核准方案交接。ChatGPT PM 依進度決定每次派工，不依賴 Agent 彼此的聊天紀錄。

```text
ChatGPT PM → Codex SA → SA-REPORT.md
          → PM 讀取報告、提出方案
          → 使用者本機核准
          → Claude Code SD/RD（獨立 Worktree）
          → IMPLEMENTATION.md / TEST-RESULT.md
          → PM 檢查、需要時交 Codex Review
```

## 1. 安裝與登入

建議直接在專案根目錄使用整合設定腳本。它會建立或更新 `.venv`、安裝 ccc-mcp、安裝缺少的 CLI、引導登入，並在目前 Git Bash 啟用真實派工：

```bash
# 預設只準備 Codex
source scripts/setup-real-agents.sh

# 只準備 Claude Code
source scripts/setup-real-agents.sh claude

# 同時準備兩個 CLI
source scripts/setup-real-agents.sh all

# 設定完成後直接啟動 Tunnel
source scripts/setup-real-agents.sh codex tunnel_你的實際ID
```

腳本必須用 `source`，才能讓環境變數留在啟動 Tunnel 的同一個終端。若 Node.js／npm 不存在，腳本會嘗試透過 WinGet 安裝 Node.js LTS；安裝後 PATH 尚未刷新時，只需開新的 Git Bash，再執行同一條設定命令。

以下是需要手動排錯時使用的個別安裝方式。

需要各自的官方原生 CLI 及帳戶登入。已有 VS Code Codex 擴充套件的 Windows 安裝，可由設定腳本找到其隨附的 codex.exe；版本更新後重新 source 腳本即可重新尋找。

Codex CLI 可依 [OpenAI 官方安裝方式](https://developers.openai.com/cookbook/examples/codex/using_goals_in_codex) 使用 npm 安裝或更新：

```bash
npm install -g @openai/codex@latest
codex --version
codex login
codex login status
```

若 Git Bash 找不到 `npm`，請先安裝 Node.js，再開新的 Git Bash。若設定腳本已顯示 `Codex found; finish login with: codex login`，代表 CLI 已存在，不必重裝，只要執行 `codex login`。

Claude Code 請依 [Anthropic 官方安裝文件](https://code.claude.com/docs/en/getting-started) 安裝。若操作終端是 Git Bash，使用 npm 版本最直接；需要 Node.js 22 以上：

```bash
npm install -g @anthropic-ai/claude-code
claude --version
claude
```

安裝後開新的 Git Bash，執行 `claude` 並依瀏覽器提示完成登入；`claude auth status --text` 可進一步確認狀態。ccc-mcp 不替代兩個 CLI 各自的登入或模型使用權限；Tunnel runtime key 用途是連接 Tunnel。

不想使用 npm 時，也可在 Windows PowerShell 或 Git Bash 呼叫 Windows 的 WinGet 作為備選：

```bash
winget.exe install --id Anthropic.ClaudeCode --exact --source winget
```

若 WinGet 顯示已安裝，但 Git Bash 回報 `claude: command not found`，請先於本專案根目錄執行 `source scripts/enable-real-agents.sh`，再執行 `claude --version` 與 `claude auth login`。設定腳本可直接尋找 WinGet 的 Claude 套件目錄，補入目前終端的 PATH；不必重新安裝。

若同時啟用了 Conda，npm 產生的 Git Bash `claude` shell shim 可能誤用 Anaconda 的 `cygpath`，形成 `anaconda3/Library/c/Users/...` 之類的錯誤路徑。設定腳本會優先尋找 npm 套件內真正的 `claude.exe` 並加入 PATH，繞過該 shim；重新執行 `source scripts/setup-real-agents.sh claude` 即可，不必重裝套件。

## 2. 在 Git Bash 重啟 Tunnel

先在原本連線的終端按 Ctrl+C。接著於專案根目錄執行：

```bash
bash scripts/connect-chatgpt.sh tunnel_你的實際ID
```

連線腳本會自動尋找原生 Codex／Claude CLI，將路徑傳給 Tunnel 與 Worker；不必先手動 `source`。它只檢查路徑與登入狀態，本身不執行模型任務。已授權專案不需要額外的 real-agent 環境開關，但仍需安裝並登入角色所用的 CLI。更新程式後須重啟 Tunnel，讓既有 Server 載入新版本。

缺少 Claude 時仍可先連接 SA；SD/RD 要等 Claude 安裝及登入完成。實際派工前 Adapter 也會檢查 CLI 必要參數。

Claude Adapter 使用 `--safe-mode` 保留官方訂閱登入，同時停用自訂載入；另外停用使用者／專案設定來源並指定空的 MCP 清單。工具仍限於 Read/Edit/Write/Glob/Grep。不要改回 `--bare` 來搭配 `claude auth login`：依 [官方程式化執行文件](https://code.claude.com/docs/en/headless)，bare 模式不讀取訂閱 OAuth 憑證。CLI 非零退出時，Adapter 會擷取並遮罩 stderr 或 stdout 的錯誤摘要。

Windows 的 Codex Adapter 會明確傳入 `-c windows.sandbox="elevated"`，同時保留 `--ignore-user-config`、`--sandbox read-only` 與 `-a never`。忽略使用者設定時，若沒有指定 Windows 沙箱，可能導致所有讀檔命令都被政策拒絕。這使用 [OpenAI 官方 Windows 沙箱](https://learn.chatgpt.com/docs/windows/windows-sandbox)；本機必須已完成所需的沙箱設定。

受限 CLI 不一定提供目前 IDE 的 MCP 工具，也可能無法讀取沙箱外的技能檔案。SA 應記錄這些限制，繼續使用被允許的專案唯讀查詢；不得重試被拒絕的路徑、變更權限或宣稱完成圖譜驗證。

執行狀態 `SUCCEEDED` 表示 CLI 已正常回傳結構化報告，不代表需求驗收通過。PM 必須讀取報告的 `needs_revision`、`findings` 與證據，再決定下一步；若報告表示無法讀取程式碼，不能當成分析成功。

Windows Git 的 LF/CRLF 自動轉換不應被誤判為註解修改。Worker 將僅換行不同的 UTF-8 文字檔列在 `line_ending_only_files`，其餘實質變更仍照原本規則檢查；二進位或無法解碼為 UTF-8 的內容不做這項放寬。

## 3. 準備被開發專案的 Git 基準

Codex 的唯讀 SA/Review 支援尚未建立 Git 的已註冊專案：Adapter 使用 `--skip-git-repo-check`，並保留 `--sandbox read-only` 與 `-a never`。此選項依 [OpenAI 官方 CLI 文件](https://learn.chatgpt.com/docs/cli/reference) 允許非 Git 目錄；不解除唯讀限制。SD/RD 的 Worktree 仍需要以下 Git 基準。

實作 Worktree 以已提交的 HEAD 建立。先在要開發的 Repository 中審閱檔案與 `.gitignore`，建立初始 commit，確認要提供給 Agent 的程式已提交。不要將 `.env`、秘密金鑰、`.tunnel/` 或工具資料夾納入提交。

在 ccc-mcp 本專案第一次建立 Git 基準，可先 `git init`、以 `git add` 加入你已審閱的原始碼，再 `git commit`。若要開發另一個已有 Git 的專案，改用 `ccc-mcp project add 路徑 --id 專案ID` 註冊該專案。

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
./.venv/Scripts/ccc-mcp.exe --project 你的專案ID approve 新任務ID --plan PLAN-A
```

核准後，PM 再派 SD/RD：`agent=claude`、`role=SD/RD`、`adapter=real`，Context Version 必須與核准紀錄相符。ccc-mcp 自動建立含 SA 報告與核准方案的交接套件；Claude 在獨立 Worktree 執行。

目前的實作使用獨立 CLI 程序。結果存於 ccc-mcp，可由網頁工具或 CLI 查詢；未與目前 VS Code Codex 對話視窗同步。

## 操作與核准規則

真實 SD/RD 派工前必須有本機人工核准。相同計畫與 Context Version 的有效核准可供重試使用，不需要每次派工重複核准；計畫內容、計畫版本或 Context Version 改變時，原核准不再適用。

`bootstrap_project` 用來恢復專案，不是必須逐字寫在每則聊天訊息中的指令，也不是 dispatch 的唯一前置讀取方式。PM 必須取得正確專案與目前 Context Version；新對話或切換專案時適合先 bootstrap。使用者可以直接說「請繼續 hello-world 已核准的任務，完成後回報」，讓 PM 選擇適當工具。
