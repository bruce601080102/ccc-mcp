# ccc-mcp

ccc 代表 ChatGPT、Codex、Claude。讓 ChatGPT 讀寫你指定的本機專案，保存共享 Context 與修改紀錄。

**註冊專案 → 本機授權一次 → 連接 ChatGPT → 直接說要做什麼。**

## 1. 註冊專案

使用 Windows Git Bash，在 ccc-mcp 資料夾執行：

第一次設定，在 Git Bash 執行一條指令即可。預設安裝／登入 Codex、建立 `.venv`、安裝 ccc-mcp 並啟用真實派工：

```bash
source scripts/setup-real-agents.sh
```

若要同時準備 Codex 與 Claude Code：

```bash
source scripts/setup-real-agents.sh codex
source scripts/setup-real-agents.sh claude
source scripts/setup-real-agents.sh all
```

也可以把 Tunnel ID 放在最後，設定完成後直接啟動連線：

```bash
source scripts/setup-real-agents.sh codex tunnel_你的實際ID
```

接著註冊專案：

```bash
ccc-mcp project add "C:/Users/user/Desktop/專案/TradeAdvisorv3" --id TradeAdvisorv3
```

已註冊的專案不必重建。全程使用 ChatGPT 時，不需要安裝 Codex／Claude Code。
Codex 與 Claude Code 不必兩個都安裝；缺少的 provider 可在角色設定中改用已安裝的 CLI 或 ChatGPT。安裝腳本的參數、手動安裝與故障排除見[真實 Agent 設定指南](docs/real-agents.md)。

## 2. 設定 SA／SD／RD 角色

角色決定正式任務要交給哪個執行者。`PM` 是目前的 ChatGPT 對話，負責協調、審閱與 Context 整理；可設定的工作角色如下：

| 角色 | 工作 | 限制 |
|---|---|---|
| `SA` | 分析需求、架構、風險與驗收方式 | 唯讀 |
| `SD` | 系統設計 | 已授權專案由系統自動承接計畫 |
| `RD` | 程式實作 | 已授權專案由系統自動承接計畫 |
| `SD/RD` | 同一次工作完成設計與實作 | 已授權專案由系統自動承接計畫 |
| `REVIEW` | 獨立審查實作結果 | 唯讀 |

每個角色都能指定 `codex`、`claude` 或 `chatgpt`。安裝 Codex 與 Claude Code 後，推薦先使用：

```bash
ccc-mcp --project TradeAdvisorv3 config role SA codex --quota-fallback chatgpt
ccc-mcp --project TradeAdvisorv3 config role SD/RD claude --quota-fallback chatgpt
ccc-mcp --project TradeAdvisorv3 config role REVIEW codex --quota-fallback chatgpt
ccc-mcp --project TradeAdvisorv3 config show
```

不需設定工作流或工作區模式。執行一次 `ccc-mcp project authorize 專案ID` 後，ChatGPT 的 `start_project_request` 會建立／沿用內部工作串並立即派工設定好的 SA；PM 核對原始報告後用 `advance_project` 提交精簡 Context，後端會自動派出 SD/RD，再以同一工具派出 REVIEW、結束本輪。授權後的實作 Agent 修改原專案；MCP 不提供 ChatGPT 直接寫原檔的 `write_workspace` 捷徑。CLI 缺少、未登入或執行失敗會留下失敗紀錄，不會改由 ChatGPT 假裝 Agent 已執行。

上面的快速設定刻意省略兩個選填參數：`--model`（模型）與 `--effort`（思考等級）。省略時，ccc-mcp 不會指定模型或思考等級，而是使用該本機 CLI 自己的預設值。若要固定 REVIEW 使用的 Codex 模型與思考等級，完整寫法例如：

```bash
ccc-mcp --project TradeAdvisorv3 config role REVIEW codex --model YOUR_CODEX_MODEL --effort high --quota-fallback chatgpt
```

請把 `YOUR_CODEX_MODEL` 換成在 Codex CLI 執行 `/model` 時看到、且帳戶可用的模型 ID。這條指令各部分的意思是：

- `REVIEW`：設定審查角色，仍然只能唯讀。
- `codex`：平常由本機 Codex CLI 執行。
- `--model YOUR_CODEX_MODEL`：指定 Codex CLI 使用的模型；不是 ChatGPT 備援的模型。
- `--effort high`：指定 Codex CLI 的思考等級，模型必須支援該值；這不是 token 上限。
- `--quota-fallback chatgpt`：只有 Codex 回報額度／用量不足時，才交給目前網頁對話中的 ChatGPT。ChatGPT 的模型與思考模式由網頁介面決定，不能由這兩個參數設定。

若只安裝 Codex，可把三個 provider 都改成 `codex`；若不使用本機 CLI，則改成 `chatgpt`。`SD`、`RD` 與 `SD/RD` 是三個獨立設定，不會互相覆蓋。

每次 `config role` 都會整組取代該角色原有的 provider、model、effort 與 fallback；要保留的選項須一起填寫。完整參數與分工流程見[角色與模型設定](docs/configuration.md)及 [CLI 參考](docs/cli-reference.md)。

## 3. 授權整個專案

```bash
ccc-mcp project authorize TradeAdvisorv3
```

終端會顯示實際專案位置。確認後輸入 `TradeAdvisorv3`，即可持續讀寫這個專案的原始檔案。

**授權一次，就是持續同意 ccc-mcp 依工作流修改整個專案。** 不再要求每個 PLAN 另跑 `approve`，成功 REVIEW 經 PM 寫回 Context 後也不再要求 `task accept`。修改前仍會核對 SHA256 並保存證據；憑證、管理資料夾及專案外路徑仍不開放。授權持續到執行 `project revoke`。

專案文件與 Agent 報告預設使用繁體中文。註冊或建立時可選簡體中文 `ch` 或英文 `en`：

```bash
ccc-mcp project add "C:/path/to/project" --id my-project --language en
ccc-mcp project create new-project --language ch
```

既有專案可用 `ccc-mcp --project my-project config language ch` 切換；另可選 `zh-TW` 或 `en`。切換只影響後續產生的文字與文件標題，既有 Context 內容不會自動翻譯。

## 4. 連接 ChatGPT

在 ccc-mcp 資料夾啟動連線，將 ID 換成自己的：

```bash
bash scripts/connect-chatgpt.sh tunnel_你的ID
```

第一次執行若尚未安裝官方 `tunnel-client`，腳本會從 OpenAI 的最新 GitHub release 下載 Windows 版本、核對官方 SHA256 後安裝到 `.tools/tunnel-client/`。也可只安裝而不連線：

```bash
bash scripts/install-tunnel-client.sh
```

保持終端開啟。第一次連線看[連線指南](docs/chatgpt.md)：

1. 在 ChatGPT 建立並安裝 ccc-mcp 自訂 MCP，選擇相同 Tunnel。
2. 新開對話，用 `@` 選用這個連線。
3. 請它實際讀取 TradeAdvisorv3 的 README，回報檔名與內容，確認連線可用。

更新 ccc-mcp 後先執行 `./.venv/Scripts/python.exe -m pip install -e ".[dev]"` 更新依賴，再重啟 Tunnel。到 [ChatGPT Plugins](https://chatgpt.com/plugins) 開啟正在使用的 ccc-mcp 連線，按 **重新整理工具**，新開對話並用 `@` 選同一連線。確認工具清單有 `start_project_request`，而沒有 `write_workspace`。[官方更新步驟](https://developers.openai.com/plugins/deploy/connect-chatgpt#refresh-metadata)

若舊對話仍呼叫 `create_task`，伺服器會相容地將它轉為 `start_project_request` 並自動派出 SA；這不是兩次派工，也不需要再呼叫 `dispatch_agent`。但舊工具清單沒有 `advance_project`，後續仍須依上一步重啟 Tunnel、重新整理工具，並在新對話使用更新後的連線。若出現 `Unknown tool: create_task`，代表執行中的 Tunnel 尚未載入此版伺服器。

## 5. 直接開始工作

在 ChatGPT 直接提出需求，例如：

```text
請把 TradeAdvisorv3 的首頁新增健康檢查 API。
```

已授權專案固定使用「專案工作串 → SA → PM 整理 Context → SD/RD → REVIEW → PM 結案 Context」的證據鏈。使用者直接提出修改需求即可；不需管理 Task ID、逐次 approve、accept 或選擇模式。`start_project_request` 會自動派出 SA。每次真正的 Agent 執行都留下 execution ID、CLI 稽核與原始報告；未有證據不能宣稱呼叫了 Codex／Claude。

檔案直接寫回原專案。程式與測試仍須在本機執行；未執行的測試不能宣稱通過。

若舊對話仍能看到 `write_workspace`，表示連到舊版 Tunnel 或工具清單尚未重新整理；請重啟服務並更新連線工具。已授權專案的實作工作區固定是原專案，只有核准計畫後的實作執行能寫入。

## 共用上下文與精簡回覆

資料放在**你註冊的專案**裡：

- `.ccc-mcp/CONTEXT.md`：目前的共用上下文入口。
- `.ccc-mcp/context/`：架構、決策、進度與限制。
- `.ccc-mcp/tasks/內部工作串編號/executions/執行編號/`：Agent 完整原始回答及 `CONTEXT-UPDATES.md` 建議；編號只供稽核，不要求使用者管理。

流程是 **Codex／Claude 回報 → ccc-mcp 保存不可變原始報告 → PM 整理 → ccc-mcp 把精簡結果寫入新版 Context → 下一個 Agent 自動取得新版**。不需要依賴提示詞觸發接力。完整原始報告與 SHA256 來源永久保留，但已由 PM 消化的 `agent_results` 會自動從最新工作 Context 移除，避免每輪重複消耗 token。

Agent 完成後 Context Version 會自動增加；最新 `shared_context.agent_results` 只保留尚待 PM 整理的結果。若 `context_sync.needs_pm_refinement=true`，後端會阻止下一個非 SA Agent 派工。PM 提交引用原始 execution 與 SHA256 的 ContextPatch 後，後端自動移除已消化結果；舊 Context 版本與原始報告不會刪除。SQLite 保存正式版本，Markdown 是精簡且可閱讀的投影。

若舊版曾成功直接寫檔但 Context 尚未補登，執行 `ccc-mcp --project 專案ID context recover`，會依不可變寫檔紀錄補登；重跑不會重複增加版本。若真的要確認 Codex／Claude 有執行，需查看 `.ccc-mcp/tasks/.../executions/` 的原始報告與 Context 的 Agent result；只有寫檔 ID 沒有 execution ID 就是沒有執行 Agent。

聊天視窗預設只呈現六點以內：結果、執行角色、修改、測試、Context 版本與下一步。需要細節時再說「展開完整報告」。這是給 ChatGPT 的工具指示，實際呈現仍由聊天模型決定。

舊版既有資料會沿用原目錄，避免歷史引用失效；新專案使用 `.ccc-mcp`。首次改名或搬動本工具資料夾後，重新執行安裝指令、重啟 Tunnel，並更新 ChatGPT 連線工具。ChatGPT 中既有連線的顯示名稱需在介面自行改為 `ccc-mcp`。

## 新專案範例：Hello World

如果是全新的專案，第一步改用建立指令，再授權：

```bash
ccc-mcp project create hello-demo --path "C:/ccc-mcpDemo/hello-demo"
ccc-mcp project authorize hello-demo
```

在 ChatGPT 說：

```text
請使用 ccc-mcp，在 hello-demo 新增 hello.py，
執行後輸出 Hello, world! 並換行，同時新增驗證輸出的測試。
請直接寫入原專案，完成後告訴我檔案位置及本機測試命令。
```

完成後，在本機執行：

```bash
python "C:/ccc-mcpDemo/hello-demo/hello.py"
```

預期看到 `Hello, world!`。如果 hello-demo 已註冊，沿用原專案即可，不必再次建立。

## 常見狀況

**ChatGPT 說工具不存在（`Unknown tool: ...`）**：通常是 ChatGPT 端的工具清單沒跟著新版 ccc-mcp 更新，不是伺服器壞了。到 [ChatGPT Plugins](https://chatgpt.com/plugins) 按「重新整理工具」；如果重新整理後問題還在，解除安裝這個連線、用同一個 Tunnel ID 重新安裝一次，再開新對話測試。

**設定 `config role ... --quota-fallback` 之後額度用盡沒有自動切換**：這個設定是每個專案各自獨立的，例如 `ccc-mcp --project A config role SA codex --quota-fallback chatgpt` 只對專案 `A` 生效，不會套用到其他專案。請確認指令裡的 `--project` 是你實際在用的那個專案。

**任務看起來卡住（`RUNNING` 一直沒變）**：真正派工給 Codex／Claude 是背景執行，實際寫程式可能要花數分鐘，這段時間 ChatGPT 無法提前得知結果，只能重新呼叫查詢工具確認。只要還沒超過設定的逾時秒數（`get_task_status` 會顯示已耗時與逾時上限）就是正常現象，過一下子再請它查一次即可。

**任務狀態變成 `BLOCKED`**：不是失敗。系統只能自動驗證 `.py` 檔案的註解有沒有被誤刪，改到其他類型的檔案（例如 `README.md`）會保守地停下來、等 PM 看過內容再繼續，照常請 PM 整理 Context、派下一個角色即可。

## 撤銷授權

```bash
ccc-mcp project revoke TradeAdvisorv3
```

專案恢復唯讀。授權只在本機操作，ChatGPT 不能替自己開權限。

需要 Codex／Claude 分工或正式任務審核時，再看[進階設定](docs/configuration.md)；這些不是普通讀寫專案的必要步驟。
