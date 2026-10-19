# ccc-mcp

讓 ChatGPT 當專案的 PM，將分析交給 Codex、實作交給 Claude Code，再把成果整理成下次可沿用的共用 Context。你只要註冊並授權專案，之後直接提出需求；不用自己管理 Task ID、逐次核准計畫或驗收。

預設流程：**ChatGPT（PM）→ Codex（SA）→ Claude Code（SD/RD）→ Codex（REVIEW）→ ChatGPT 整理 Context**。真正派工、檔案修改和 Context 更新都應留下紀錄，不能只憑聊天回覆判定成功。

## 快速開始（Windows Git Bash）

需要 Git、Python 3.11+，以及可登入的 Codex／Claude Code 帳戶。連接 ChatGPT 另需可使用 Tunnel 的 OpenAI Platform 帳戶。以下全部以 `hello-demo` 為例。

### 1. 安裝並登入

在 Git Bash 執行：

```bash
git clone https://github.com/bruce601080102/ccc-mcp.git
cd ccc-mcp
source scripts/setup-real-agents.sh
ccc-mcp setup all
```

第一步只建立 `.venv`、把 ccc-mcp 本身裝起來（這是唯一需要腳本的地方：`ccc-mcp` 指令此時還不存在）。`ccc-mcp setup all` 接手檢查／安裝、登入 Codex 與 Claude Code，純 Python 執行，不需要先 `source` 任何東西。若安裝 Node.js 後提示重開終端，開新的 Git Bash，在 `ccc-mcp` 目錄重跑 `ccc-mcp setup all` 即可。

### 2. 建立並授權專案

```bash
ccc-mcp project create hello-demo
ccc-mcp project authorize hello-demo
```

第二個指令會顯示實際路徑，確認後輸入 `hello-demo`。預設新專案位於使用者目錄的 `ccc-mcpProjects/hello-demo`。授權持續有效，直到你撤銷；它允許工作流程修改此專案，不會授權專案外的檔案。

若 `hello-demo` 資料夾**已經存在但尚未註冊**，不要再執行 `project create`，改用：

```bash
ccc-mcp project add "C:/Users/你的帳號/Desktop/hello-demo" --id hello-demo
ccc-mcp project authorize hello-demo
```

Windows 路徑也可用引號包住反斜線寫法（例如 `"C:\Users\你的帳號\Desktop\hello-demo"`）；在 Git Bash 使用 `/` 比較容易複製。若 ID 已註冊，直接沿用即可，用 `ccc-mcp project list` 查路徑。

### 3. 連接 ChatGPT

先依[連線指南](docs/chatgpt.md)建立 Tunnel、取得 `tunnel_...` ID 與 runtime API key，再於**同一個 Git Bash 終端**執行：

```bash
ccc-mcp connect tunnel_你的實際ID
```

金鑰只在終端的隱藏輸入欄填入，不要貼到聊天。保持這個終端開啟；在 ChatGPT 安裝並選用對應的 ccc-mcp 連線。`connect` 會檢查本機 CLI 與 Tunnel；若檢查失敗，先處理錯誤再開始工作。

### 4. 提出需求，然後在本機驗證

在已選用 ccc-mcp 的 ChatGPT 對話說：

> 請在 hello-demo 建立 `hello.py`，執行時輸出 `Hello, World!`，並加入驗證輸出的測試。

不必在提示詞要求「使用 Codex」或「更新 Context」；這是 ccc-mcp 工作流程的責任。完成後在 Git Bash 查實際狀態：

```bash
ccc-mcp --project hello-demo task status
ccc-mcp --project hello-demo task usage
ccc-mcp --project hello-demo context show
python "$HOME/ccc-mcpProjects/hello-demo/hello.py"
```

`task usage` 應有 Codex／Claude 的執行紀錄及 `cli_invoked: true`；若是 `false`、執行失敗或沒有紀錄，就不能說該 Agent 已實際執行。`context show` 可確認新版共用 Context；專案內的 `.ccc-mcp/CONTEXT.md` 是可閱讀的摘要，完整原始報告與執行證據保留在 `.ccc-mcp/tasks/`。最後一行預期輸出 `Hello, World!`；若你用自訂路徑建立專案，請將它換成 `project list` 顯示的實際路徑。聊天回覆中的「測試通過」也應以實際測試結果為準。

## 想調整角色或語言？

預設 SA／REVIEW 使用 Codex，SD／RD／SD/RD 使用 Claude Code。`SA` 分析、`SD/RD` 設計與實作、`REVIEW` 審查；ChatGPT 負責協調及每輪 Context 整理。先查看設定，再依需要改：

```bash
ccc-mcp --project hello-demo config show
ccc-mcp --project hello-demo config role REVIEW codex --effort high --quota-fallback chatgpt
ccc-mcp --project hello-demo config language ch
```

`--model 模型ID` 與 `--effort` 都可省略，交由本機 CLI 使用預設值；它們不會設定 ChatGPT 網頁模型。`--quota-fallback chatgpt` **只在 CLI 額度不足時**備援，不會修復 CLI 未安裝、未登入或一般執行錯誤。每次 `config role` 會整組取代該角色設定，要保留的選項請一起填。語言預設 `zh-TW`（繁體中文），也可設 `ch`（簡體中文）或 `en`（英文）；切換不會翻譯既有紀錄。

也可直接編輯 `hello-demo/.ccc-mcp/settings.json` 的 `roles` 或 `language`；儲存後，下次查詢設定或派工時會驗證並同步到 SQLite，不用重啟。先用 `ccc-mcp --project hello-demo config show` 確認生效值。格式錯誤會停止派工並顯示錯誤；`project_access` 等授權欄位不能靠改檔取得權限，仍須在本機執行 `project authorize`。

只安裝 Codex 時，可把 `SD/RD` 也改成 `codex`；不用本機 CLI 時，可選 `chatgpt`，但這就**不是** Codex／Claude 的真實執行。更多選項見[角色設定](docs/configuration.md)。

## 遇到問題

- `Project ID is already registered`：用 `ccc-mcp project list` 找到原專案並沿用，不要刪資料重建。
- ChatGPT 顯示 `Unknown tool`：先執行 `ccc-mcp update --dev`（原始碼目錄）或 `ccc-mcp update`（一般安裝）更新，重啟 `connect`，在 ChatGPT 重新整理連線工具並開新對話；不要把舊工具清單當成目前版本。
- 派工失敗：用 `task status`、`task usage` 查失敗階段；缺少 CLI 或登入失敗不會自動冒充成 ChatGPT 執行。
- `--quota-fallback` 設定沒有自動切換：這個設定是每個專案各自獨立的，確認指令裡的 `--project` 是你實際在用的那個專案。
- 任務狀態一直是 `RUNNING`：真正派工是背景執行，寫程式可能要花數分鐘；`task status` 會顯示已耗時與逾時上限，沒超過就是正常，過一下子再查一次即可。
- 任務狀態變成 `BLOCKED`：不是失敗。系統只能自動驗證 `.py` 檔的註解有沒有被誤刪，改到其他類型的檔案會保守地停下來等你看過，照常讓下一個角色接手即可。

要停止 ccc-mcp 寫入 `hello-demo`：

```bash
ccc-mcp project revoke hello-demo
```

其他細節見[安裝與 Agent](docs/real-agents.md)、[ChatGPT 連線](docs/chatgpt.md)及[CLI 指令](docs/cli-reference.md)。
