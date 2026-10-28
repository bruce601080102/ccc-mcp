# 5. 專案授權、角色與共用 Context

ccc-mcp 只有一條一般修改流程：專案授權後，由 Agent 分析、實作與審查，ChatGPT 擔任 PM 整理共用 Context。使用者不需選擇工作流或工作區模式，也不需管理內部 Task ID、逐次核准計畫或最後接受 Task。

## 一次授權

在本機終端執行：

```bash
ccc-mcp project authorize my-project
ccc-mcp --project my-project config show
```

授權綁定專案根目錄，持續有效，直到執行 `ccc-mcp project revoke my-project`。MCP 不能自行授權。授權後可讀原專案；實作 Agent 在原專案寫入，仍受安全路徑、憑證、註解及寫入證據檢查。`.ccc-mcp/settings.json` 可手動修改 `roles` 與 `language`；下次讀取設定或派工時會驗證並同步 SQLite。`project_access`、寫入範圍與工作區等欄位仍由本機授權及 CLI 管理，改 JSON 不能授權。舊版保存的模式設定不會讓已授權專案繞過 Agent。

對一般修改需求，ChatGPT 使用 MCP 的 `start_project_request(project_id, requirement, acceptance?)`。後端會建立或沿用內部專案工作串，立即派出設定好的 SA。SA 結果以 execution ID 與原始報告保存；PM 核對證據後用 `advance_project` 提交 ContextPatch 與實作計畫，後端會自動派 SD/RD。實作結果經 PM 用同一工具整理後，後端自動派 REVIEW；REVIEW 結果整理後自動完成本輪。未整理報告時，後端拒絕下一角色派工。計畫由既有專案授權自動承接。`write_workspace` 不提供給 MCP，已授權專案也會在服務層拒絕直接寫檔。

CLI 不存在、登入失敗或 Agent 執行失敗時，不會假裝成功或改成 ChatGPT 直接寫檔。可讀 `get_task_status`、`get_task_result`、`get_task_usage` 與 `bootstrap_project.context_sync` 查看真實進度及證據。`QUEUED` 只表示已排入，不表示模型完成；成功報告與原始檔案證據才算執行。

## 程式語言與執行環境版本

在 Web 控制台選擇專案 →「設定」→「專案執行環境」，可指定程式語言、版本，以及本機執行檔。按「檢查本機版本」會顯示實際版本與執行檔路徑；檢查也可預覽尚未儲存的選項。按「儲存執行環境」後，UI、CLI、`.ccc-mcp/settings.json` 與後續各角色的交接資料會使用相同設定。

```bash
ccc-mcp --project my-project config runtime --language python --version 3.9
ccc-mcp --project my-project config runtime --check
# 也能指定本機直譯器或現有 venv 的 Python
ccc-mcp --project my-project config runtime --language python --version 3.9 --executable "C:/Python39/python.exe"
# Node.js 範例；JavaScript／TypeScript 的版本代表 Node.js 版本
ccc-mcp --project my-project config runtime --language javascript --version 22
ccc-mcp --project my-project config test-command "npm test"
# 移除指定，恢復預設行為
ccc-mcp --project my-project config runtime --clear
```

支援 Python、JavaScript、TypeScript、Go、Java、C#（.NET SDK）、Rust、PHP 與 Ruby。版本採數字前綴：`3.9` 允許任何 `3.9.x`，`3.9.13` 則要求該修補版本；留空只指定語言或執行檔。執行檔欄位接受指令名稱或絕對路徑，不接受附加參數。

請先在本機安裝需要的環境；這項設定不會下載語言版本，也不會變更運行 ccc-mcp 本身的 Python。未指定 Python 執行檔時，Windows 會讀取 `py` 的已安裝清單並檢查實際版本，再檢查 PATH；其他語言使用指定執行檔或 PATH，找不到符合版本時會明確阻擋測試。可透過執行檔路徑指定版本管理工具所安裝的環境。

Python 會用選定直譯器建立依設定區分的專案測試 venv，放在 `<home>/venvs/<project>-<設定雜湊>/`；相依套件與主機測試都在該環境執行，不會沿用舊的預設 venv。其他語言須另設測試指令；Node.js 專案會在指定環境執行 npm 安裝，其他工具鏈由測試指令處理建置與相依套件。版本不符的結果會保存於測試證據，不會改用其他版本繼續。Agent 也會收到語言與版本限制。執行中的 agent 結束後才能透過 UI／CLI 改變環境設定。

## 角色與模型

可設定 `SA`、`SD`、`RD`、`SD/RD`、`REVIEW`，每個角色可選 `codex`、`claude` 或 `chatgpt`；預設 SA／REVIEW 是 Codex，SD／RD／SD/RD 是 Claude。SA 與 REVIEW 唯讀。可依需要指定模型、思考等級與額度不足時的接手角色：

```bash
ccc-mcp --project my-project config role SA codex --quota-fallback chatgpt
ccc-mcp --project my-project config role SD/RD claude --model sonnet --effort high
ccc-mcp --project my-project config role REVIEW codex --effort high
ccc-mcp --project my-project config show
```

每次 `config role` 都會整組取代該角色的 provider、model、effort、fallback；要保留的選項須一起填寫。省略模型與思考等級時使用 CLI 自己的預設。ChatGPT 網頁模型由對話介面選擇，ccc-mcp 不會冒稱可控制或獨立驗證。`SD`、`RD`、`SD/RD` 是三個獨立角色設定。額度不足接手只適用已辨識的用量限制；登入、參數或程式錯誤不會被當作額度不足。

### 全程使用 ChatGPT（chatgpt-only profile）

不安裝 Codex 或 Claude Code CLI 也能讓三個自動角色都由 ChatGPT 執行。一次套用：

```bash
ccc-mcp --project my-project config profile chatgpt-only
ccc-mcp --project my-project config show
```

`config profile chatgpt-only` 會把全部五個角色（`SA`、`SD`、`RD`、`SD/RD`、`REVIEW`）都設為 `chatgpt`，並清除原本設定的 Codex／Claude 專屬 `model`、`effort` 與 `quota-fallback`，不會殘留——即使自動流程（`advance_project`）實際上只會用到 `SA`、`SD/RD`、`REVIEW` 這三個，獨立的 `SD`、`RD` 也會一起設定，`config show` 才不會看起來還混著 Codex／Claude。要切回最佳模型工作流，執行：

```bash
ccc-mcp --project my-project config profile best-model
```

`best-model` 不是固定的組合，而是**依本機實際安裝的 Codex／Claude Code CLI 動態決定**：兩個都裝了，維持原本 `SA`／`REVIEW` 用 Codex、`SD`／`RD`／`SD-RD` 用 Claude 的分工；只裝了其中一個（例如只有 Claude Code、沒有 Codex，或反過來），就把全部五個角色都設成那一個，而不是硬塞一個其實跑不起來的組合；兩個都沒裝則 `config profile best-model` 會直接拒絕，請先安裝其中一個，或改用 `chatgpt-only`。

`config show` 與 `config providers` 都會多一個唯讀的 `profile` 欄位，由目前所有角色的 provider、對照「這台機器現在算什麼」即時推算，從不另外保存：全部都是 `chatgpt` 時顯示 `chatgpt-only`；符合這台機器當下的 best-model 組合時顯示 `best-model`；其餘（包含「剛好是 Codex/Claude 分工，但其中一個根本沒裝」這種其實跑不起來的情況）都顯示 `custom`。

`config profile` 只是 `config role` 的整組捷徑，仍可用 `config role` 個別調整：

```bash
ccc-mcp --project my-project config role SA chatgpt
ccc-mcp --project my-project config role SD/RD chatgpt
ccc-mcp --project my-project config role REVIEW chatgpt
```

這不是 fake 或降級流程：ChatGPT 會透過 ccc-mcp 的 MCP 工具互動式完成角色，主機仍照一般流程保留 Context、檔案與測試證據（包含 `project authorize` 設定的 `test_command` 自動執行）。它不消耗 Codex／Claude Code CLI 額度；ChatGPT 的可用模型與工具額度仍依連線帳戶和方案而定。

對 ChatGPT 互動式執行，`task usage` 會明確標示 `"interactive": true` 與 `invocation.source: "chatgpt_web"`，`cli_invoked: false` 在這裡代表「本來就不會呼叫 CLI」，不是失敗訊號；只有原生 CLI（Codex／Claude Code）執行時 `cli_invoked: false` 才代表 CLI 沒有被成功呼叫。

Windows 上 Claude Code 若由 npm 安裝，ccc-mcp 會尋找官方套件中的 `claude.exe`，不透過可能受 PowerShell 執行原則限制的 `.ps1` 或 shell 包裝腳本。仍需先完成各 CLI 登入。對已授權專案，real-agent 派工不再需要額外設定環境開關；更新後須重啟 Tunnel，讓服務讀到新程式與工具清單。

## Context 與證據

`.ccc-mcp/CONTEXT.md` 是精簡的目前上下文；原始 Agent 報告、呼叫證據、差異與歷史版本保留在 `.ccc-mcp/tasks/` 與本機 SQLite。PM 的 ContextPatch 引用原始 execution 與來源 SHA256；提交後已消化的 `agent_results` 從最新工作 Context 移除，但原始資料與舊版本不刪。可以用下列命令檢查：

```bash
ccc-mcp --project my-project context validate
ccc-mcp --project my-project context history
ccc-mcp --project my-project task usage
```

若只有直接寫檔 ID、沒有 Agent execution 與原始報告，不能宣稱使用了 Codex／Claude。舊版直接寫檔的歷史仍保留；`ccc-mcp --project my-project context recover` 可補登舊紀錄，但不會憑空生成 Agent 回應。

## 語言與額外參考資料

新專案預設 `zh-TW`（繁體中文），也支援 `ch`（簡體中文）與 `en`（英文）。建立專案時用 `--language`，既有專案用：

```bash
ccc-mcp --project my-project config language ch
```

設定影響後續產生的 Context、交接與報告語言，不會自動翻譯既有內容。要讓 ChatGPT 讀取專案外的參考資料，可單獨授權唯讀目錄：

```bash
ccc-mcp --project my-project config read-root reference "D:/Documents/ProjectReference"
```

額外目錄只有讀取權，不會自動加入 Codex／Claude CLI 的可寫範圍。專案根目錄外、憑證、`.git` 與 `.ccc-mcp` 管理檔案不透過一般檔案工具開放。
