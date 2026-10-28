# 3. 建立第一個專案

本章完成一個最小、可驗證的專案。先完成[安裝與登入](1-install-and-sign-in.md)，並在需要 ChatGPT PM 時完成[連線](2-connect-chatgpt.md)。

## 1. 建立並授權

```bash
ccc-mcp project create hello-demo --write
```

`--write` 會在建立專案的同一步授予持續寫入授權：這是本機明確動作，過程中會有一次互動確認，說明它允許 ccc-mcp 修改**這個專案**並在實作後執行設定的測試命令，不會授權專案以外的路徑。只想先註冊、晚點再授權？省略 `--write`，之後再單獨執行 `ccc-mcp project authorize hello-demo`（這個兩步驟用法仍然可用）。

`project create` 預設建立在使用者目錄的 `ccc-mcpProjects/hello-demo`。想將單一專案放到其他位置：

```bash
ccc-mcp project create hello-demo --path "/Users/你的帳號/Code/hello-demo" --write
```

已存在的專案不要重建，改為註冊：

```bash
ccc-mcp project add "/Users/你的帳號/Code/hello-demo" --id hello-demo --write
```

`project add` 一樣支援 `--write`。不想這時候就授權，直接拿掉 `--write` 即可——在明確要求之前（不論是之後的 `project authorize`、`project add --write`，還是 Web 控制台的「啟用寫入」），ccc-mcp 不會授予任何寫入權限。

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

## ChatGPT-only 15 分鐘 smoke test

不想裝 Codex 或 Claude Code CLI、只想先用 ChatGPT 把整條流程走一遍。開始前可先執行 `ccc-mcp doctor`：它會列出目前是否已裝 Git／Codex／Claude，以及 ChatGPT-only 與最佳模型兩種工作流各自需要什麼（`workflows.chatgpt_only`／`workflows.best_model`），確認「不需要 Codex／Claude Code CLI」這件事不是只靠猜的。

```bash
ccc-mcp project create hello-demo-chatgpt --profile chatgpt-only --write
```

`--profile chatgpt-only` 會在建立專案的同一步把 `SA`、`SD`、`RD`、`SD/RD`、`REVIEW` 全部設為 ChatGPT（已存在的專案改用 `ccc-mcp --project hello-demo-chatgpt config profile chatgpt-only`，效果相同）。用下列指令確認設定確實生效，再往下走：

```bash
ccc-mcp --project hello-demo-chatgpt config show
```

輸出的 `profile` 應該是 `"chatgpt-only"`，`roles` 底下五個角色的 `provider` 都是 `chatgpt`。

依[連接 ChatGPT](2-connect-chatgpt.md)建立並連上 Tunnel（`ccc-mcp connect <tunnel_id>`），確認 ChatGPT 的 MCP 連線工具清單裡能看到 ccc-mcp。選取這個連線後提出：

> 請在 `hello-demo-chatgpt` 建立 `hello.py`，執行時輸出 `Hello, World!`，並加入驗證輸出的測試。

ChatGPT 會自己擔任 SA、SD/RD、REVIEW 三個自動角色（透過 `read_workspace`／`write_task_file`／`submit_agent_report` 等 MCP 工具），不需要背景跑任何 CLI 子行程。回到本機驗證，不要只看對話回覆：

```bash
ccc-mcp --project hello-demo-chatgpt task handoff
ccc-mcp --project hello-demo-chatgpt task status
ccc-mcp --project hello-demo-chatgpt task usage
ccc-mcp --project hello-demo-chatgpt context show
```

- `task handoff`：現在輪到誰、下一步要做什麼最直接的入口。等待的是這個 ChatGPT 對話本身時（`waiting_on: "CHATGPT_HANDOFF_PENDING"`），會直接附上 `chatgpt_prompt`——可直接複製貼進 ChatGPT 對話的提示詞，以及 `expected_report_fields`（提醒該回報的欄位）；其他情況則回報目前實際在等什麼（背景 Agent 執行中、PM 需整理報告、派工中斷需要 resume 等），全部取自既有的 task status／context，不是另外一份會漂移的狀態。
- `task status`／`brief.waiting_on`：`CHATGPT_HANDOFF_PENDING` 代表現在這個 ChatGPT 對話自己要動手，不是背景程序在跑；`brief.tests` 會顯示主機是否已實際執行測試命令、結果是通過或失敗。
- `task usage`：ChatGPT 執行會標示 `"interactive": true`、`invocation.source: "chatgpt_web"`，`cli_invoked: false` 在這裡代表「本來就不呼叫 CLI」，不是失敗；這不是偽裝呼叫，ccc-mcp 從不會假造 CLI 已被呼叫。
- `context show`：確認每一輪的 Context 版本、下一步動作與是否已整理完成。

最後確認主機真的執行過測試命令並通過（見`task status` 的 `brief.tests`，或 `ccc-mcp --project hello-demo-chatgpt task test` 手動補跑一次）；ChatGPT 在對話裡自稱「測試通過」不能取代這個紀錄。

## Web 控制台

比起終端機更想用瀏覽器完成以上步驟？`ccc-mcp ui` 會啟動本機 Web 控制台，可以加入既有資料夾或建立新專案，同樣只需要一個明確的「啟用寫入」步驟（完整揭露後再輸入確認文字），取代 CLI 的 `--write` 旗標——啟動方式見 [README](../../README.md#web-控制台)，信任模型細節見 [7-reliability-and-security.md](7-reliability-and-security.md#web-控制台)。
