# 8. CLI 參數與 ChatGPT 工具用語

先照[建立第一個專案](3-first-project.md)跑完一輪，再用這份文件調整角色與派工方式。以下 `demo-app` 是專案 ID 範例，請換成自己的 ID。

## 角色與參數

先在本機授權一次專案：

```text
ccc-mcp project authorize 專案ID
```

授權後固定走 Agent／Context 證據鏈，不需選模式。ChatGPT 對一般修改需求呼叫 `start_project_request`，後端會建立或沿用內部工作串並派出設定的 SA；PM 以 `advance_project` 提交有來源的 ContextPatch 後，系統自動派出 SD/RD、REVIEW 並完成本輪。實作 Agent 在原專案寫檔；MCP 不提供直接寫檔捷徑。

設定格式（`ROLE`、`PROVIDER` 是要替換的欄位）：

```text
ccc-mcp --project 專案ID config role ROLE PROVIDER [--model 模型ID] [--effort 等級] [--quota-fallback 執行者]
```

| `ROLE`（大小寫照寫） | 工作 | 限制 |
|---|---|---|
| `SA` | 系統分析，讀需求與程式並提出方案 | 唯讀 |
| `SD` | 系統設計 | 專案已授權時自動承接計畫 |
| `RD` | 程式實作 | 專案已授權時自動承接計畫 |
| `SD/RD` | 同一次工作一起設計與實作，本範例使用此角色 | 專案已授權時自動承接計畫 |
| `REVIEW` | 審查成果與問題 | 唯讀 |

五種角色**各自獨立設定**；設定 `SD/RD` 不會同時修改 `SD`、`RD`。每種角色都可選 `codex`、`claude` 或 `chatgpt`，也可全部選同一種。

| 參數 | 意思與可填內容 |
|---|---|
| `--project` | 已註冊的專案 ID，例如 `demo-app`；放在子命令前面 |
| `PROVIDER` | `codex`＝本機 Codex CLI；`claude`＝本機 Claude Code CLI；`chatgpt`＝目前網頁對話接手 |
| `--model` | CLI 模型 ID／別名；省略時不傳模型參數，使用 CLI 預設，不保證和 IDE 選擇相同 |
| `--effort` | 思考等級；可先用 `high` 或 `medium`，仍須模型支援，並非 token 上限 |
| `--quota-fallback` | 額度不足時接手的執行者：`codex`、`claude`、`chatgpt`；省略則不設定自動接手 |
| `project create --path` | 新專案根目錄；Agent 實作在已授權的原專案進行 |
| `project create --profile` | 建立同時套用 `chatgpt-only` 或 `best-model`，省去之後再執行一次 `config profile` |

**每次 `config role` 都會整組替換該角色設定**：要保留的 model、effort、fallback 須一起填，省略會清空。設定只影響後續派工，不會啟動模型或修改歷史紀錄。

## 模型有哪些？

- **Codex**：依 CLI 版本、登入方式與帳號而異。可自行開啟 `codex`，輸入 `/model` 查看選單，再把要用的模型 ID 填入 `--model`。本範例省略模型，先用 CLI 預設。見 [OpenAI 官方模型說明](https://learn.chatgpt.com/docs/models)。
- **Claude Code**：常用別名為 `sonnet`、`opus`、`haiku`，也可使用完整模型 ID。可開啟 `claude`，輸入 `/model` 查看帳號選項；別名對應版本可能更新。見 [Claude 官方模型說明](https://code.claude.com/docs/en/model-config)。
- **ChatGPT**：在網頁選擇模型與思考模式，不能透過這裡的 `--model`／`--effort` 切換。

以下是 ccc-mcp 目前接受的 effort 值，**不代表你的 CLI／模型全部支援**。不支援時會失敗，不會當成額度不足而自動接手。

| 執行者 | ccc-mcp 接受的 `--effort` |
|---|---|
| Codex | `minimal`、`low`、`medium`、`high`、`xhigh`、`max`、`ultra` |
| Claude | `low`、`medium`、`high`、`xhigh`、`max`、`ultracode` |

全部交給 ChatGPT 最簡單的方式是套用 `chatgpt-only` profile，會一次把全部五個角色（`SA`、`SD`、`RD`、`SD/RD`、`REVIEW`）設好且不殘留 Codex／Claude 的 model/effort/fallback；自動派工只會用到其中 `SA`、`SD/RD`、`REVIEW` 三個，但獨立的 `SD`、`RD` 也會一起設定，`config show` 才不會看起來還混著 Claude：

```bash
ccc-mcp --project demo-app config profile chatgpt-only
```

`config role` 逐一設定也仍然有效（只有 `SA`、`SD/RD`、`REVIEW` 會被自動派工用到）：

```bash
ccc-mcp --project demo-app config role SA chatgpt
ccc-mcp --project demo-app config role SD/RD chatgpt
ccc-mcp --project demo-app config role REVIEW chatgpt
```

`ccc-mcp --project demo-app config show` 會回報目前的 `profile`（`chatgpt-only`／`best-model`／`custom`）；要切回最佳模型工作流，執行 `ccc-mcp --project demo-app config profile best-model`——這個 `best-model` 會依本機實際裝了哪些 CLI 動態決定（兩個都裝＝原本的 Codex/Claude 分工；只裝一個＝全部角色用那一個；一個都沒裝會直接拒絕），不是固定寫死的組合。

## 現在輪到誰、下一步該貼什麼？

```bash
ccc-mcp --project demo-app task handoff
```

唯讀，純粹從既有的 `task status`／Context／`pending_chatgpt_handoffs` 推算，不會另外保存一份可能跟它們脫鉤的狀態。正在等的是這個 ChatGPT 對話自己動手（`waiting_on: "CHATGPT_HANDOFF_PENDING"`）時，會多附上：

- `chatgpt_prompt`：可以直接複製貼進任一個已選取 ccc-mcp 連線的 ChatGPT 對話的提示詞，內容依角色是唯讀（SA／REVIEW）還是讀寫（SD/RD）而不同。
- `expected_report_fields`：提醒該角色完成後 `submit_agent_report` 要帶的欄位（`summary`、`findings`、`risks`、`next_action`、`tests`、`needs_revision` 等）。

其他情況（背景 Agent 執行中、PM 需要用 `advance_project` 整理報告、派工中斷需要 `task resume`、等待本機核准方案等）會直接回報目前真正在等什麼，文字跟 `task status`／`context sync` 既有欄位一致，不會另外編一套說法。

## ChatGPT 需要固定 Prompt 嗎？

**不需要固定咒語或逐字照抄**，你可以用自然語言描述需求。範例裡的詞語有以下作用：

| 詞語 | 實際作用 | 是否要手打？ |
|---|---|---|
| `project_id=demo-app` | 選擇專案，任務編號只在該專案內有意義 | 請明確說出專案 ID，避免選錯 |
| `bootstrap_project` | 工具名稱，載入目前 Context、任務與待接手工作 | 不必打工具名，說「恢復 demo-app」也可以；由 ChatGPT 呼叫 |
| `agent=auto` | 派工工具參數，依角色設定選擇執行者 | 工具預設 auto；明說可避免 ChatGPT 自行指定其他執行者 |
| `role=SA`／`SD/RD` | 本次派工的角色 | 可說「先分析，再一起設計與實作」；工具須使用正確角色值 |
| `adapter=real` | 真實執行流程；`fake` 只模擬 | 工具預設 real；fake 結果不代表真實開發 |
| `Context Version` | 派工使用的共享上下文版本 | 不必背數字，由 ChatGPT 查詢目前版本 |
| `PLAN-A`、`TASK-0001` | 內部稽核 ID | 已授權專案會重用同一工作串；一般使用者不必輸入或管理 |

ChatGPT 透過 MCP 工具操作，不是看到單字就必然執行。請確認回覆有實際任務／執行 ID 與原始結果；若只提供一般建議，可說「請實際呼叫 ccc-mcp 工具」。

## 專案語言

新專案預設為 `zh-TW`（繁體中文）。初始化時可用 `--language ch`（簡體中文）或 `--language en`（英文），既有專案可切換：

```bash
ccc-mcp --project demo-app config language zh-TW
ccc-mcp --project demo-app config language ch
ccc-mcp --project demo-app config language en
```

這會控制後續產生的共用 Context、投影文件、交接政策與 Agent 報告語言；已儲存的內容不會自動翻譯。
