# 輸出協議

給未來要讀 ccc-mcp 狀態的外部消費者（例如規劃中的 `ccc-mcp-ui`）看的穩定介面說明。這份文件只是**描述現有輸出**，不是新發明一套格式——ccc-mcp 現有的 MCP 工具與 CLI 輸出本來就已經是結構化 JSON。

## 哪些東西算協議、哪些不算

**算**：

- 所有 MCP 工具的回傳值（`bootstrap_project`、`get_context`、`get_task_status`、`get_task_result` 等）。
- CLI 指令經由 `output()`（[interfaces/cli.py](../src/ccc_mcp/interfaces/cli.py)）印出的 JSON，也就是絕大多數指令——例如 `ccc-mcp task status`、`ccc-mcp context show`。
- `docs/schema/*.json`：下面列的三個核心形狀的正式 JSON Schema。

**不算**（可以隨時改，不保證相容）：

- SQLite 資料庫 schema（`storage.py` 裡的 `CREATE TABLE`）。
- `src/ccc_mcp` 內部模組結構與檔案路徑。
- `ccc-mcp task show`（給人看的純文字摘要）與 `ccc-mcp context export`（匯出 Markdown 投影）——這兩個是給終端機使用者看的，不是給程式解析的。

## Protocol version

`public_project_settings`（`get_project_settings` MCP 工具／`ccc-mcp config show`）與 `bootstrap_project` 的回傳值都帶一個頂層欄位：

```json
"protocol_version": "1"
```

只有在下面三個 schema 有不相容變更（刪欄位、改型別、改語意）時才會升版；新增可省略的欄位不算破壞性變更，不會升版。對應的常數是 [`core/models.py`](../src/ccc_mcp/core/models.py) 的 `PROTOCOL_VERSION`。

## 核心 Schema

| 檔案 | 對應 Pydantic model | 用途 |
|---|---|---|
| [`schema/agent-report.json`](schema/agent-report.json) | `AgentReport` | Codex／Claude／ChatGPT 每次執行完回報的結構，`get_task_result` 裡每筆 execution 的 `result.report` 就是這個形狀。 |
| [`schema/context-snapshot.json`](schema/context-snapshot.json) | `ContextSnapshot` | 專案目前共用 Context 的完整內容，`get_context`／`bootstrap_project` 的 `working_context` 欄位。 |
| [`schema/context-patch.json`](schema/context-patch.json) | `ContextPatch` | PM 提交 Context 變更時的格式，`propose_context_patch` 工具的輸入。 |

這三份檔案不是手寫的，是從 [`core/models.py`](../src/ccc_mcp/core/models.py) 的 Pydantic model 直接產生，跟 ccc-mcp 內部實際驗證資料用的是同一份定義。改了對應的 model 之後，重新產生：

```bash
ccc-mcp schema export docs/schema
```

`tests/test_protocol_schema.py` 會比對重新產生的結果跟 commit 進 repo 的檔案是否一致，忘記重新產生會讓測試失敗。

## 建議的讀取方式

有 MCP 連線時優先用 MCP 工具；本機批次處理或沒有 MCP 連線時，CLI 的 JSON 輸出是等價的替代方案（兩者背後是同一份 Python 邏輯）。一般會用到的組合：

- 專案現況、最近決策、待辦阻礙：`bootstrap_project`（MCP）或 `ccc-mcp context show`（CLI）。
- 某個任務目前狀態、是否卡住、還剩多久逾時：`get_task_status`（MCP）或 `ccc-mcp task status`（CLI）。
- 某次執行的完整原始回報與證據：`get_task_result`（MCP）或 `ccc-mcp task result`（CLI）。
- 角色、provider、quota fallback 等設定：`get_project_settings`（MCP）或 `ccc-mcp config show`（CLI）。

不要直接開 `.ccc-mcp/` 底下的 SQLite 檔或平行解析 Markdown 投影檔；那些是實作細節，格式與欄位都可能在不升版的情況下改變。
