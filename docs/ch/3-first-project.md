# 3. 建立第一個專案

本章完成一個最小、可驗證的專案。先完成[安裝與登入](1-install-and-sign-in.md)，並在需要 ChatGPT PM 時完成[連線](2-connect-chatgpt.md)。

## 1. 建立並授權

```bash
ccc-mcp project create hello-demo
ccc-mcp project authorize hello-demo
```

`project create` 預設建立在使用者目錄的 `ccc-mcpProjects/hello-demo`。想將單一專案放到其他位置：

```bash
ccc-mcp project create hello-demo --path "/Users/你的帳號/Code/hello-demo"
```

已存在的專案不要重建，改為註冊：

```bash
ccc-mcp project add "/Users/你的帳號/Code/hello-demo" --id hello-demo
ccc-mcp project authorize hello-demo
```

授權是本機明確動作：它允許 ccc-mcp 修改**這個專案**，並在實作後執行設定的測試命令；不會授權專案以外的路徑。

## 2. 提出第一個需求

在已選取 ccc-mcp 連線的 ChatGPT 對話提出：

> 請在 hello-demo 建立 `hello.py`，執行時輸出 `Hello, World!`，並加入驗證輸出的測試。

預設接力是 ChatGPT（PM）→ Codex（SA）→ Claude Code（SD/RD）→ Codex（REVIEW）→ ChatGPT（Context 整理）。不必每次重新要求它使用某個角色；角色與接力由設定及工作流程決定。

## 3. 在本機驗證，而非只看聊天回覆

```bash
ccc-mcp --project hello-demo task status
ccc-mcp --project hello-demo task usage
ccc-mcp --project hello-demo context show
```

- `task status`：工作目前在哪一個角色、是否需要重試或修訂。
- `task usage`：確認 Codex／Claude CLI 是否真被呼叫；`cli_invoked: false` 不能算原生 agent 已執行。
- `context show`：檢查下一輪會接到的共用 Context。

最後自行執行專案的功能和測試。主機記錄的測試證據才是完成條件，模型在對話中宣稱成功不會覆蓋它。

下一步：[4. 執行與恢復工作流程](4-workflow.md)。
