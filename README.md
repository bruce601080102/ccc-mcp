# AgentBridge

讓 ChatGPT 讀取、修改你指定的本機專案，保存共享 Context 與修改紀錄。

**註冊專案 → 本機授權一次 → 連接 ChatGPT → 直接說要做什麼。**

## 1. 註冊專案

使用 Windows Git Bash，在 AgentBridge 資料夾執行：

```bash
# 第一次安裝；已安裝可跳過前兩行
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -e ".[dev]"

source scripts/enable-real-agents.sh

agentbridge project add "C:/Users/user/Desktop/專案/TradeAdvisorv3" --id TradeAdvisorv3
```

已註冊的專案不必重建。全程使用 ChatGPT 時，不需要安裝 Codex／Claude Code。

## 2. 授權整個專案

```bash
agentbridge project authorize TradeAdvisorv3
```

終端會顯示實際專案位置。確認後輸入 `TradeAdvisorv3`，即可持續讀寫這個專案的原始檔案。

**授權一次，就能直接修改整個專案的原始檔案，無須逐檔操作。** 修改前會核對 SHA256，保存前後版本；憑證、管理資料夾及專案外路徑仍不開放。

## 3. 連接 ChatGPT

在 AgentBridge 資料夾啟動連線，將 ID 換成自己的：

```bash
bash scripts/connect-chatgpt.sh tunnel_你的ID
```

保持終端開啟。第一次連線看[連線指南](docs/chatgpt.md)：

1. 在 ChatGPT 建立並安裝 AgentBridge 自訂 MCP，選擇相同 Tunnel。
2. 新開對話，用 `@` 選用這個連線；如果建立了 AgentBridge-v2，就選 v2。
3. 請它實際讀取 TradeAdvisorv3 的 README，回報檔名與內容，確認連線可用。

更新 AgentBridge 後先執行 `./.venv/Scripts/python.exe -m pip install -e ".[dev]"` 更新依賴，再重啟 Tunnel。到 [ChatGPT Plugins](https://chatgpt.com/plugins) 開啟正在使用的 AgentBridge 連線，按 **重新整理工具**，新開對話並用 `@` 選同一連線。若有工具開關，確認 `write_workspace` 已啟用；建立表單不一定顯示工具清單。[官方更新步驟](https://developers.openai.com/plugins/deploy/connect-chatgpt#refresh-metadata)

## 4. 直接開始工作

在 ChatGPT 說：

```text
請使用 AgentBridge 恢復 TradeAdvisorv3。
我要修改：＿＿＿。

請閱讀相關程式，實際修改原專案檔案，
完成後讀回確認，列出修改內容及測試狀態，並整理共享 Context。
```

ChatGPT 會先確認專案的讀寫授權，再透過讀寫工具完成修改。你不用記工具名稱、指定角色或建立工作區。

檔案直接寫回原專案。程式與測試仍須在本機執行；未執行的測試不能宣稱通過。

如果它仍說「只能修改 Worktree」，請它重新恢復專案、查看 `project_access`，使用 `write_workspace`。普通專案編輯不使用唯讀 SA 任務的 `write_task_file`。

## 新專案範例：Hello World

如果是全新的專案，第一步改用建立指令，再授權：

```bash
agentbridge project create hello-demo --path "C:/AgentBridgeDemo/hello-demo"
agentbridge project authorize hello-demo
```

在 ChatGPT 說：

```text
請使用 AgentBridge，在 hello-demo 新增 hello.py，
執行後輸出 Hello, world! 並換行，同時新增驗證輸出的測試。
請直接寫入原專案，完成後告訴我檔案位置及本機測試命令。
```

完成後，在本機執行：

```bash
python "C:/AgentBridgeDemo/hello-demo/hello.py"
```

預期看到 `Hello, world!`。如果 hello-demo 已註冊，沿用原專案即可，不必再次建立。

## 撤銷授權

```bash
agentbridge project revoke TradeAdvisorv3
```

專案恢復唯讀。授權只在本機操作，ChatGPT 不能替自己開權限。

需要 Codex／Claude 分工或正式任務審核時，再看[進階設定](docs/configuration.md)；這些不是普通讀寫專案的必要步驟。
