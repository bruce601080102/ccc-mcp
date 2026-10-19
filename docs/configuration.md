# 專案授權、角色與共用 Context

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

## 角色與模型

可設定 `SA`、`SD`、`RD`、`SD/RD`、`REVIEW`，每個角色可選 `codex`、`claude` 或 `chatgpt`；預設 SA／REVIEW 是 Codex，SD／RD／SD/RD 是 Claude。SA 與 REVIEW 唯讀。可依需要指定模型、思考等級與額度不足時的接手角色：

```bash
ccc-mcp --project my-project config role SA codex --quota-fallback chatgpt
ccc-mcp --project my-project config role SD/RD claude --model sonnet --effort high
ccc-mcp --project my-project config role REVIEW codex --effort high
ccc-mcp --project my-project config show
```

每次 `config role` 都會整組取代該角色的 provider、model、effort、fallback；要保留的選項須一起填寫。省略模型與思考等級時使用 CLI 自己的預設。ChatGPT 網頁模型由對話介面選擇，ccc-mcp 不會冒稱可控制或獨立驗證。`SD`、`RD`、`SD/RD` 是三個獨立角色設定。額度不足接手只適用已辨識的用量限制；登入、參數或程式錯誤不會被當作額度不足。

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
