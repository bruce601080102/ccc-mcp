# 角色、模型、路徑與共享 Context 設定

以下在 Git Bash 執行，先 `source scripts/enable-real-agents.sh`。設定保存在本機資料庫，專案內的 `.agentbridge/settings.json` 是可閱讀的副本；請使用 CLI 修改。

## 指定每個角色由誰做

可設定的角色：`SA`、`SD`、`RD`、`SD/RD`、`REVIEW`。每個角色可選 `codex`、`claude`、`chatgpt`；相同供應商可擔任全部角色。

```bash
agentbridge --project my-project config show
agentbridge --project my-project config role SA claude --model sonnet --effort high
agentbridge --project my-project config role SD/RD codex --model YOUR_CODEX_MODEL --effort high
```

將 `YOUR_CODEX_MODEL` 換成你帳戶可用的模型 ID。SA／Review 不論選誰都維持唯讀；SD／RD 不論選誰都要先有本機計畫核准。`SD`、`RD`、`SD/RD` 是各自獨立的設定。

全部交給目前對話中的 ChatGPT：

```bash
for role in SA SD RD SD/RD REVIEW; do
  agentbridge --project my-project config role "$role" chatgpt
done
```

派工時使用 `agent=auto` 才會按角色設定選擇供應商；明確指定 `agent=codex/claude/chatgpt` 會覆蓋供應商。每次派工會保存當次設定，後來改設定不會修改歷史紀錄。

## 模型與思考等級

Codex 使用 `--model` 與 `-c model_reasoning_effort=...`；Claude 使用 `--model` 與 `--effort`。也可在單次 `dispatch_agent` 提供 `model`、`effort` 覆蓋角色設定。

未設定時使用 CLI 自己的預設值，不保證與 VS Code／其他聊天視窗選擇相同。Codex 忽略使用者 config，Claude 隔離設定來源；模型支援哪些思考等級，仍由實際 CLI／帳戶檢查，不支援時會明確失敗。

ChatGPT 網頁的模型與思考選項由你在目前對話介面選擇；AgentBridge 不會假裝能從 MCP 切換或獨立驗證它。

官方參數：[Codex 設定](https://learn.chatgpt.com/docs/config-file/config-reference)、[Claude CLI](https://code.claude.com/docs/en/cli-reference)。

## 額度不足時轉交 ChatGPT

```bash
agentbridge --project my-project config role SA codex --quota-fallback chatgpt
agentbridge --project my-project config role SD/RD claude --quota-fallback chatgpt
```

只在辨識到額度／用量限制的錯誤時自動建立接手執行，保留先前失敗報告與 Worktree。一般登入、參數、測試錯誤不會當成額度不足。自動接手最多一次；Context 或核准已過期時不會略過檢查。

目前對話中的 ChatGPT 接著從 `bootstrap_project.pending_chatgpt_handoffs` 取得工作，呼叫 `list_workspace`／`read_workspace`；實作角色可用 `write_task_file`，完成後呼叫 `submit_agent_report`。寫既有檔案須提供剛讀到的 `expected_sha256`，避免覆寫其他修改。

這是目前對話接手，不是本機啟動另一個 ChatGPT。網頁對話若已停止，請說「請恢復 my-project，接手已排好的 ChatGPT 工作」；不要重複建立派工。ChatGPT 接手不需本機安裝 Codex 或 Claude，也不會產生 CLI 用量紀錄。

取消接手工作可用 `agentbridge --project my-project task cancel TASK-0001`。

## 新專案與程式碼位置

```bash
# 設定未來新專案的預設目錄，不搬移既有專案
agentbridge config projects-dir "D:/AgentBridgeProjects"
agentbridge project create new-project

# 單次指定新專案位置；目錄必須尚不存在
agentbridge project create another-project --path "D:/Projects/another-project"

# 在首次實作前，指定該專案 Worktree 的存放位置
agentbridge --project new-project config worktree-dir "D:/AgentBridgeWorktrees"
```

`project create` 建立 README、gitignore 與 Git 初始 commit，不產生業務程式。ChatGPT 的 `create_managed_project` 工具只能在本機設定的預設目錄下建立新專案，不能指定任意電腦路徑。

未設定新專案目錄時，預設為 AgentBridge home 同層的 `AgentBridgeProjects`；Worktree 預設在 AgentBridge home 的 `worktrees/專案ID/任務ID`。設定 Worktree 位置後，實作位於所選目錄下的 `專案ID/任務ID`。已有實作 Worktree 時會拒絕改位置；不自動搬移、合併或覆寫現有成果。

## 讓 ChatGPT 讀取指定資料夾

```bash
agentbridge --project my-project config read-root reference "D:/Documents/ProjectReference"
# 日後撤銷
agentbridge --project my-project config revoke-root reference
```

告訴 ChatGPT：「使用 root_id=reference 讀取需求文件。」工具只接受該目錄下的相對路徑，可分頁列檔與讀取 UTF-8 文字；不是開放整台電腦。憑證、`.git`、受管 `.agentbridge` 及 symlink/junction 路徑不透過檔案工具開放。原始報告使用既有的任務查詢工具讀取。

額外資料夾只有讀取權；ChatGPT 寫檔限於有效核准、目前 Context 的任務 Worktree，且 SA／Review 不能寫檔。

## 執行與 Token 證據

```bash
agentbridge --project my-project task usage TASK-0001
```

新的 CLI 執行在 `result.invocation` 及歷次 `executions/EXEC-.../INVOCATION.json` 記錄：

- 實際 executable、CLI version、PID、開始／結束時間、退出碼、工作目錄與啟動參數。
- 當次要求的 provider、model、effort，以及 CLI 有回傳時的實際模型名稱。
- CLI 原生回傳的 input/output/cache/reasoning Token 欄位及原生費用估值，不採用 AgentReport 自稱的用量。
- stdout/stderr 的 SHA256；不把整份過程輸出或思考內容存入稽核檔。

Codex 從 `turn.completed.usage` 取得用量；Claude 從結果的 `usage`、`modelUsage`、`total_cost_usd` 取得可用資料。未回傳的資訊標示 unavailable／未提供，不填成零，也不推算剩餘額度。CLI 費用估值不等於訂閱實際帳單。

ChatGPT 網頁執行標示 `source=chatgpt_web`、`cli_invoked=false`，Token 用量不可從此 MCP 取得。舊執行不補造當時未保存的紀錄。官方輸出：[Codex JSONL](https://learn.chatgpt.com/docs/non-interactive-mode)、[Claude 結構化輸出](https://code.claude.com/docs/en/headless)。

## 共用 Context 的完整循環

```text
PM 已精修的 Context vN
  → SA／SD／RD 取得角色交接包（含完整 shared_context）
  → Agent 回傳原始報告與 context_updates 建議
  → AgentBridge 保存 CONTEXT-UPDATES.md 與原始引用
  → ChatGPT PM collect_context_updates，閱讀來源後精修
  → propose_context_patch → commit_context_patch，產生 vN+1
  → 下一輪 Codex／Claude／ChatGPT 都取得 vN+1
```

Agent 的原始更新是待 PM 審閱的資料，不直接覆寫正式 Context。精修保留原始報告、引用與版本歷史；程式不會自行呼叫 ChatGPT 摘要。PM 要先提交精修版，再針對新版本提出與核准後續實作計畫。

## 套用這次更新

重新啟動 Tunnel，並在 ChatGPT 重新整理 AgentBridge 的工具清單，才能使用新增的讀寫與接手工具。既有任務、原始報告、核准與專案位置保留。
