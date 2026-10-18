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
| `SD` | 系統設計 | 執行前須核准計畫 |
| `RD` | 程式實作 | 執行前須核准計畫 |
| `SD/RD` | 同一次工作完成設計與實作 | 執行前須核准計畫 |
| `REVIEW` | 獨立審查實作結果 | 唯讀 |

每個角色都能指定 `codex`、`claude` 或 `chatgpt`。安裝 Codex 與 Claude Code 後，推薦先使用：

```bash
ccc-mcp --project TradeAdvisorv3 config role SA codex --quota-fallback chatgpt
ccc-mcp --project TradeAdvisorv3 config role SD/RD claude --quota-fallback chatgpt
ccc-mcp --project TradeAdvisorv3 config role REVIEW codex --quota-fallback chatgpt
ccc-mcp --project TradeAdvisorv3 config show
```

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

**授權一次，就能直接修改整個專案的原始檔案，無須逐檔操作。** 修改前會核對 SHA256，保存前後版本；憑證、管理資料夾及專案外路徑仍不開放。

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

更新 ccc-mcp 後先執行 `./.venv/Scripts/python.exe -m pip install -e ".[dev]"` 更新依賴，再重啟 Tunnel。到 [ChatGPT Plugins](https://chatgpt.com/plugins) 開啟正在使用的 ccc-mcp 連線，按 **重新整理工具**，新開對話並用 `@` 選同一連線。若有工具開關，確認 `write_workspace` 已啟用；建立表單不一定顯示工具清單。[官方更新步驟](https://developers.openai.com/plugins/deploy/connect-chatgpt#refresh-metadata)

## 5. 直接開始工作

在 ChatGPT 說：

```text
請使用 ccc-mcp 恢復 TradeAdvisorv3。
我要修改：＿＿＿。

請閱讀相關程式，實際修改原專案檔案，
完成後讀回確認，列出修改內容及測試狀態，並整理共享 Context。
```

ChatGPT 會先確認專案的讀寫授權，再透過讀寫工具完成修改。你不用記工具名稱、指定角色或建立工作區。

檔案直接寫回原專案。程式與測試仍須在本機執行；未執行的測試不能宣稱通過。

如果它仍說「只能修改 Worktree」，請它重新恢復專案、查看 `project_access`，使用 `write_workspace`。普通專案編輯不使用唯讀 SA 任務的 `write_task_file`。

## 共用上下文與精簡回覆

資料放在**你註冊的專案**裡：

- `.ccc-mcp/CONTEXT.md`：目前的共用上下文入口。
- `.ccc-mcp/context/`：架構、決策、進度與限制。
- `.ccc-mcp/tasks/任務編號/executions/執行編號/`：Agent 完整原始回答及 `CONTEXT-UPDATES.md` 建議。

流程是 **Codex／Claude 回報 → ChatGPT 審閱、精修並提交新版 Context → 下一個 Agent 取得新版**。原始報告保留，精簡不會覆蓋證據。

Agent 完成後，若 `context_sync.needs_pm_refinement=true`，代表 ChatGPT 還沒完成整理；不是背景自動呼叫 ChatGPT。`bootstrap_project` 會回傳實際資料位置與待整理報告。SQLite 保存正式版本，Markdown 是可閱讀的投影。

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

## 撤銷授權

```bash
ccc-mcp project revoke TradeAdvisorv3
```

專案恢復唯讀。授權只在本機操作，ChatGPT 不能替自己開權限。

需要 Codex／Claude 分工或正式任務審核時，再看[進階設定](docs/configuration.md)；這些不是普通讀寫專案的必要步驟。
