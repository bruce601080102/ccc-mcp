# ccc-mcp MCP — 完整開發規格 V2

## 你的角色

你是一名資深 Python 架構師、MCP Protocol Engineer、Multi-Agent Orchestration Engineer。

請在目前工作目錄中直接開發一套可執行、可測試、可擴充的 Python 專案：

**ccc-mcp — Context-Aware Multi-Agent Orchestration MCP**

本專案不只是 MCP Router，而是一套具有持久化共享記憶、上下文優化、角色分工、決策治理及跨 Session 恢復能力的 Multi-Agent 開發協作系統。

請直接實作程式碼，不要只輸出架構建議或虛擬碼。

---

# 一、核心設計理念

## 1. Agent 角色

系統分為三個 AI 角色與一個協調層。

### ChatGPT — PM（Project Manager）

職責：

- 理解使用者需求與業務邏輯。
- 管理需求、驗收條件及業務決策。
- 根據工作狀況判斷要指派給哪個 Agent。
- 閱讀 Agent 執行結果。
- 動態整理、優化、壓縮共用 Context。
- 發現需求衝突、技術風險及未完成事項。
- 維護跨 Session 的工作交接摘要。

ChatGPT 不需要直接處理大量程式碼。

ChatGPT 可以提出方案或建議，但不得假冒使用者進行必要的人工核准。

### Codex — SA（System Analyst）

職責：

- 讀取 Repository。
- 分析系統架構、API、模組關聯。
- 了解程式碼呼叫流程。
- 評估需求的技術可行性。
- 提出修改方案、影響範圍與風險。
- Review Claude Code 的實作。
- 產出結構化 SA Report。

SA 預設為唯讀模式，不得修改業務程式碼。

### Claude Code — SD/RD（System Designer / Developer）

職責：

- 依據已核准的方案進行技術設計。
- 新增功能、修改程式碼、重構。
- 建立測試案例。
- 執行可授權的測試。
- 提供 Git Diff 與實作報告。
- 說明完成項目、未完成項目及技術風險。

SD/RD 不得自行變更 PM 已核准的業務需求。

### ccc-mcp — MCP Orchestrator

職責：

- 儲存共用 Context。
- 提供 Context 讀取與版本化更新。
- 根據 PM 指示分派 Agent。
- 管理 Task、Approval、Git Worktree。
- 管理 Agent 交接資料。
- 控制工具權限。
- 儲存稽核紀錄。
- 支援跨 Session 恢復。

---

# 二、核心工作流程

必須實現以下循環：

```text
User
  ↓
ChatGPT (PM)
  ↓
讀取 Project Context
  ↓
整理需求 / 更新 Context
  ↓
PM 判斷下一個 Agent
  ├── Codex (SA)
  │      ↓
  │   技術分析報告
  │      ↓
  │   寫入共享報告
  │
  └── Claude Code (SD/RD)
         ↓
      程式碼實作與測試
         ↓
      寫入共享報告
  ↓
ChatGPT (PM)
  ↓
讀取新的 Agent 成果
  ↓
Context Optimization
  ↓
更新 Project Context
  ↓
決策：
  ├── 再交給 Codex 分析
  ├── 再交給 Claude Code 實作
  ├── 要求使用者決策
  └── 完成任務
```

所有 Agent 都不能依賴彼此的歷史聊天內容。

**每個 Agent 必須能在全新 Session 中，根據共享 Context 與 Repository 獨立完成當前任務。**

---

# 三、Shared Project Memory（最高優先級）

每個被 ccc-mcp 管理的 Repository，應自動建立：

```text
project-root/
│
├── 現有專案程式碼
│
└── .ccc-mcp/
    ├── CONTEXT.md
    │
    ├── context/
    │   ├── PROJECT.md
    │   ├── ARCHITECTURE.md
    │   ├── DECISIONS.md
    │   ├── CURRENT_STATE.md
    │   └── CONSTRAINTS.md
    │
    ├── tasks/
    │   └── TASK-0001/
    │       ├── REQUIREMENT.md
    │       ├── SA-REPORT.md
    │       ├── APPROVED-PLAN.md
    │       ├── SD-DESIGN.md
    │       ├── IMPLEMENTATION.md
    │       ├── TEST-RESULT.md
    │       └── REVIEW.md
    │
    ├── handoffs/
    │   └── SESSION-HANDOFF.md
    │
    ├── history/
    │   └── context-events.jsonl
    │
    └── config.yaml
```

各文件用途：

### CONTEXT.md

這是最重要的共用入口。

必須包含：

- 專案名稱及目的
- 專案目前狀態
- 當前進行中的任務
- 最近重要決策
- 已知技術限制
- 下一步工作
- 詳細資料所在文件
- Context Version
- Git Revision
- 最後更新時間

此文件由 ChatGPT PM 提議優化，ccc-mcp 驗證後寫入。

不得因為摘要優化而刪除不可遺失的資訊。

### PROJECT.md

保留專案目的、業務需求與驗收準則。

### ARCHITECTURE.md

保留已驗證的實際系統架構。

技術內容必須能追溯至程式碼、Git Commit 或 SA Report。

不得只因 AI 的推測就直接寫成既定事實。

### DECISIONS.md

儲存所有重要決策。

每個決策應有：

- Decision ID
- Task ID
- 決策內容
- 決策原因
- 核准者
- 核准時間
- 狀態：Approved / Rejected / Superseded
- 相關 Context Version

決策紀錄採追加式管理，不可直接覆蓋歷史內容。

### CURRENT_STATE.md

記錄目前任務狀態、阻塞問題與下一個行動。

### CONSTRAINTS.md

保留不可違反的規則，例如：

- API 向後相容性
- 技術版本限制
- 安全性要求
- 不可修改的程式區域
- 原始註解保留規則

### SESSION-HANDOFF.md

專門給新的 ChatGPT Session 使用。

它不是舊對話的逐字備份，而是足以恢復工作狀態的結構化交接文件。

---

# 四、Context Optimizer

建立獨立的 Context Optimization 模組。

## 1. 核心目標

讓 ChatGPT 可以在 Agent 執行任務後，重新整理共用上下文。

例如：

Codex 原始分析報告有 20,000 tokens。

ChatGPT 可整理成：

- 主要結論
- 已驗證事實
- 重要架構資訊
- 技術風險
- 已核准決策
- 尚未解決的問題
- 下一步行動
- 原始文件引用

產生約 1,000～2,000 tokens 的工作摘要。

這只是預設目標，不可為了硬性限制 token 而遺失必要資訊。

## 2. 兩層記憶

設計為：

**Layer 1：Working Context**

供 ChatGPT、Codex、Claude Code 快速理解任務。

包含精簡後的 Context 與任務交接資料。

**Layer 2：Persistent Evidence**

儲存：

- 原始需求
- SA Report
- 已核准方案
- Git Diff
- 實作報告
- Test Report
- Review 結果
- Decision History
- Context Version History

Layer 1 可以更新、濃縮。

Layer 2 不可因為摘要而遭到覆蓋或遺失。

## 3. Optimization Workflow

提供四個步驟：

1. Collect：收集新任務事件、報告與決策。
2. Optimize：由 ChatGPT 提出優化後 Context。
3. Validate：確認版本、來源、必要欄位與衝突。
4. Commit：以原子操作更新新版 Context。

ChatGPT 必須透過 MCP 提交 Context Patch。

ccc-mcp 不應假裝自己具有 ChatGPT 的模型推理能力。

沒有可用的 ChatGPT 寫入 MCP 權限時，提供 CLI 匯入 Patch 的備援方式。

## 4. 版本與衝突控制

每次修改需記錄：

- base_context_version
- new_context_version
- changed_sections
- source_task_ids
- source_document_refs
- update_reason
- author_role
- timestamp

使用 Optimistic Concurrency Control。

如果兩個 Agent 都基於 Version 12 更新 Context，其中一方已提交 Version 13，另一方不得直接覆蓋。

必須返回 Version Conflict，要求重新讀取或合併。

Markdown 文件的更新必須使用安全原子寫入。

## 5. 防止摘要資訊遺失

建立 Context Invariant Validation。

至少保護：

- 已核准的業務需求
- 尚未完成的驗收條件
- 不可修改的規則
- 目前進行中的任務
- 尚未解決的 Blocking Issues
- 已存在的決策引用

不可只依賴 LLM 自我聲稱「沒有遺漏」。

必須以結構化資料與程式規則進行必要檢查。

對無法自動驗證的語意差異，標示需由 PM 或使用者確認。

---

# 五、跨 Session Recovery

這是核心驗收功能。

建立 MCP Tool：

`bootstrap_project`

輸入：

```json
{
  "project_id": "my-project"
}
```

輸出必須包含：

- project_summary
- context_version
- current_git_revision
- active_tasks
- latest_decisions
- current_blockers
- unresolved_questions
- previous_session_handoff
- suggested_next_action
- source_references

新 ChatGPT Session 即使完全不知道之前的聊天內容，也可以呼叫 `bootstrap_project` 恢復專案狀態。

提供標準啟動指令：

「請使用 ccc-mcp 恢復目前專案的上下文，說明目前進度、最近決策、未完成工作與建議下一步。」

要注意：

- 不可假設 ChatGPT 會自動在每個新 Session 呼叫 MCP。
- 必須由使用者在新 Session 啟用連線並觸發恢復。
- 若 ChatGPT 沒有 MCP 權限，提供 `ccc-mcp context export` 產出可貼入新對話的交接摘要。
- 不依賴 ChatGPT 的個人 Memory 作為唯一儲存來源。

---

# 六、Role-Based Context Handoff

不同 Agent 不應該接收完全相同的大量上下文。

建立 Context Package Builder。

### PM Context Package

包含：

- 業務需求
- 決策摘要
- 目前進度
- SA / SD / RD 最新成果
- 風險
- 待決策事項

### SA Context Package

包含：

- 需求與驗收條件
- 相關架構
- 已知技術限制
- 相關程式碼路徑
- 重要歷史技術決策
- 本次分析任務

### SD/RD Context Package

包含：

- 已核准的需求與方案
- SA 技術分析
- 開發限制
- 預期修改範圍
- 驗收條件
- 測試要求
- Repository 參考位置

每個 Context Package 必須記錄：

- context_version
- task_id
- target_agent
- source_document_refs
- git_revision
- token_budget（選填）

Agent 必須知道自己讀取的是哪個版本的 Context。

建立角色隔離，避免把不相關的歷史資料全部傳給每個 Agent。

---

# 七、MCP Tools

至少實作以下工具。

## Context 類

1. `bootstrap_project`
   - 恢復專案上下文。

2. `get_context`
   - 按角色、任務、範圍讀取 Context。

3. `propose_context_patch`
   - ChatGPT 提交上下文優化建議。

4. `commit_context_patch`
   - 驗證後寫入新版 Context。

5. `get_context_history`
   - 查看歷史版本及差異。

6. `build_handoff`
   - 為下一個 Agent 或新 Session 產生 Context Package。

## Task 類

7. `list_projects`
   - 顯示已註冊專案。

8. `dispatch_agent`
   - 指派 Codex SA 或 Claude Code SD/RD。

9. `get_task_status`
   - 查詢執行狀態。

10. `get_task_result`
    - 取得分析、實作及測試成果。

11. `propose_plan`
    - 提交技術方案供 PM 評估。

12. `review_changes`
    - 交給 Codex 執行唯讀 Review。

## Approval 類

13. `request_approval`
    - 建立等待人工核准的請求。

14. `get_approval_status`
    - 查詢是否已經由授權使用者核准。

MVP 可先使用本機 CLI 完成人工核准：

`ccc-mcp approve TASK-0001 --plan PLAN-A`

注意：不可單純因為 ChatGPT 呼叫 MCP 並傳入 `approved=true` 就視為真正的人類核准。

必須使用經驗證的使用者身分及可稽核的核准機制。

---

# 八、PM Agent Routing

ChatGPT 擔任決策者，ccc-mcp 負責執行。

路由規則：

| 任務類型 | Agent |
|---|---|
| 需求理解 | ChatGPT PM |
| 業務流程討論 | ChatGPT PM |
| 架構分析 | Codex SA |
| 程式碼依賴追蹤 | Codex SA |
| 技術可行性評估 | Codex SA |
| 程式碼修改 | Claude Code SD/RD |
| 新增功能 | Claude Code SD/RD |
| 重構與測試 | Claude Code SD/RD |
| 獨立 Code Review | Codex SA |
| 任務是否完成 | ChatGPT PM + 使用者驗收 |

提供 `dispatch_agent`，不需要分散建立多個硬編碼派工入口。

輸入示例：

```json
{
  "project_id": "my-project",
  "task_id": "TASK-0001",
  "agent": "codex",
  "role": "SA",
  "context_version": 12,
  "instruction": "分析新增交易風控 API 的影響範圍"
}
```

ccc-mcp 自動生成 Context Package，再呼叫相應 Agent Adapter。

若 Context Version 已過期，必須拒絕或要求重新確認。

不要假設只有 SA → SD/RD 一條固定流程。

系統必須允許：

- SA → PM → SA
- SA → PM → SD/RD
- SD/RD → PM → SA
- SD/RD → PM → SD/RD
- Review → PM → SD/RD

---

# 九、Python Agent Adapter

設計：

- `BaseAgentAdapter`
- `CodexAdapter`
- `ClaudeCodeAdapter`
- `FakeAgentAdapter`

實際 Agent 透過官方 CLI 非互動模式執行。

- Codex CLI 用於分析與 Review。
- Claude Code CLI 用於設計、實作與測試。

請先檢查目前環境的 CLI `--help`、版本及支援參數，不要臆測命令格式。

使用 Python `subprocess`，且不使用 `shell=True` 拼接使用者輸入。

ccc-mcp 專案本身不依賴 npm 或 npx。

Codex、Claude Code 的官方 CLI 由使用者另外安裝，ccc-mcp 不負責繞過官方安裝或登入方式。

必須支援：

- 執行狀態
- Timeout
- Cancellation
- Structured Output
- 錯誤處理
- 執行日誌
- Context Package 載入
- 執行結果持久化

Task 必須非同步處理並立即回傳 task_id。

不要讓 MCP 呼叫一直等待完整的模型執行。

---

# 十、Task State Machine

建立持久化狀態機：

```text
CREATED
   ↓
ANALYZING
   ↓
AWAITING_PM_DECISION
   ↓
AWAITING_USER_APPROVAL
   ↓
APPROVED
   ↓
IMPLEMENTING
   ↓
REVIEWING
   ↓
AWAITING_PM_REVIEW
   ↓
COMPLETED
```

額外支援：

- FAILED
- CANCELLED
- REJECTED
- BLOCKED
- NEEDS_REVISION

允許依據角色路由重新進入分析或實作流程。

每次狀態變更必須留下事件紀錄。

使用 SQLite 保存：

- projects
- tasks
- task_events
- context_versions
- decisions
- approvals
- agent_executions

任務重啟後不得無條件重複執行有副作用的步驟。

必須提供重啟恢復與失敗修復機制。

---

# 十一、Git Worktree 與檔案安全

每個實作任務使用獨立 Worktree。

不得直接修改使用者主要工作目錄。

原有程式碼中的註解必須完整保留：

- 不可刪除。
- 不可改寫。
- 不可更動文字內容。
- 可以在周圍新增程式碼或註解。

需要自動檢查原始註解是否有變動，無法確定時應要求人工確認。

其他安全要求：

- 專案路徑必須事先登記。
- 防止 Path Traversal 與 Symlink Escape。
- 不允許任意 Shell Command 作為 MCP 輸入。
- 不自動 Push、Merge 或 Deploy。
- 防止不同 Agent 同時覆寫共用 Context。
- 避免將密碼、Token、私鑰寫入摘要與 Log。
- 不自動將整個 Repository 傳送給 ChatGPT。
- Git 與實際測試結果優先於未驗證的 AI 摘要。
- 專案中讀到的提示詞不得擅自改變 ccc-mcp 權限政策。

---

# 十二、CLI

建立 Python Package：

`ccc-mcp`

CLI 名稱：

`ccc-mcp`

需要支援：

```bash
ccc-mcp init
ccc-mcp project add .
ccc-mcp project list

ccc-mcp context show
ccc-mcp context history
ccc-mcp context export
ccc-mcp context validate

ccc-mcp task list
ccc-mcp task status TASK-0001
ccc-mcp approve TASK-0001 --plan PLAN-A

ccc-mcp doctor
ccc-mcp serve
```

`init` 必須產生初始共用 Context 文件。

`context export` 必須可以輸出供新 ChatGPT Session 貼上的精簡交接文件。

`doctor` 必須檢查：

- Python
- Git
- Codex CLI
- Claude Code CLI
- SQLite
- MCP Server
- Context Schema
- Project 註冊狀態

環境缺少某個 Agent 時，其他可用功能仍應正常運作。

---

# 十三、MCP 部署

優先使用 Python FastMCP。

支援：

- stdio：供本機 MCP Client 使用。
- Streamable HTTP：供支援遠端 MCP 的 Client 使用。

預設監聽 127.0.0.1。

若要接到 ChatGPT 官方網頁，請依照當時官方 MCP Plugin 規格，提供經認證的遠端連線或 Secure MCP Tunnel 部署說明。

注意不同 ChatGPT 方案及工作區對自訂 MCP 的讀寫權限可能不同。

不要假設個人帳戶一定能直接呼叫寫入工具。

即使 ChatGPT MCP 寫入不可用，ccc-mcp 也應能透過本機 CLI 與其他支援的 MCP Client 完整操作。

---

# 十四、專案結構

建議：

```text
ccc-mcp/
├── pyproject.toml
├── README.md
├── .env.example
├── src/
│   └── ccc_mcp/
│       ├── main.py
│       ├── server.py
│       ├── cli.py
│       ├── config.py
│       │
│       ├── context/
│       │   ├── manager.py
│       │   ├── optimizer.py
│       │   ├── validator.py
│       │   ├── versioning.py
│       │   ├── bootstrap.py
│       │   └── package_builder.py
│       │
│       ├── agents/
│       │   ├── base.py
│       │   ├── codex.py
│       │   ├── claude.py
│       │   └── fake.py
│       │
│       ├── orchestration/
│       ├── tasks/
│       ├── storage/
│       ├── security/
│       ├── git/
│       ├── models/
│       └── mcp_tools/
│
├── tests/
└── examples/
```

結構可合理調整，但不得為了抽象化而製造過多不必要的類別。

---

# 十五、測試與驗收

使用 pytest。

優先使用 Fake Agent，不消耗真實模型額度。

必須包含以下測試：

### Test 1：Context Initialization

註冊空白測試 Repository。

成功產生 `.ccc-mcp/` 和所有必要文件。

### Test 2：SA Handoff

建立需求並指派 Fake Codex。

產生 SA Report。

確認 Context 可引用 SA Report 的技術結論。

### Test 3：PM Context Optimization

模擬 ChatGPT 提交 Context Patch。

確認：

- 內容更新。
- 版本遞增。
- 原始 SA Report 保留。
- 決策資訊沒有遺失。
- Context 引用正確。

### Test 4：Context Version Conflict

兩次修改基於相同舊版本。

第一個成功後，第二個必須因版本衝突遭拒。

### Test 5：Session Recovery

建立任務、決策、SA Report。

模擬全新的 ChatGPT Session，不提供任何歷史對話。

只呼叫 `bootstrap_project`。

確認可恢復：

- 專案目標
- 進行中的任務
- 最近決策
- 下一步行動
- 重要限制

### Test 6：SD/RD Handoff

Fake Claude 接收到已核准的 SA 方案與正確版本的 Context Package。

確認不依賴 Codex 的聊天歷史。

### Test 7：Human Approval

沒有核准紀錄時，禁止實作。

核准方案版本不一致時，也必須拒絕。

### Test 8：Review Loop

Fake Codex Review 回報問題。

PM 可將任務重新指派 Claude Code 修改。

### Test 9：Context Drift

故意讓 ARCHITECTURE.md 與實際程式碼不一致。

系統必須標示需重新驗證，不能直接把舊摘要當成技術事實。

### Test 10：安全與復原

涵蓋：

- Path Traversal
- Agent Timeout
- 任務取消
- Worker 重啟
- 非法狀態轉換
- Context 原子寫入
- 註解變更檢查
- 無權限更新決策

---

# 十六、開發順序

請依照以下順序開發。

**Phase 1 — Context Hub**

- Python Package
- CLI
- Shared Context 初始化
- Context Schema
- Context 讀寫
- 版本管理
- Context History

**Phase 2 — Session Recovery**

- bootstrap_project
- SESSION-HANDOFF.md
- Context Export
- Role-Based Context Package

**Phase 3 — Context Optimizer**

- Context Patch
- Version Conflict
- Context Validation
- 原始紀錄保護
- Context Drift Detection 基礎版

**Phase 4 — Agent Orchestration**

- Task Manager
- Codex Adapter
- Claude Code Adapter
- Agent Routing
- Handoff Workflow
- Human Approval

**Phase 5 — Git + MCP + End-to-End Testing**

- Git Worktree
- MCP Tools
- Security Controls
- 完整 Fake Agent 測試
- Windows 使用說明
- ChatGPT MCP 連線文件

先完成核心功能，不要提前做 Web UI、Redis、Kubernetes 或分散式微服務。

---

# 十七、重要開發要求

1. 不要只產出 Markdown 設計文件，必須實際建立 Python 程式。
2. 不得破壞目前工作目錄內已有的程式或註解。
3. 所有功能必須提供可執行測試。
4. 不可假造 CLI 登入或測試成功結果。
5. 沒有安裝 Claude Code 時，使用 Fake Adapter 繼續測試。
6. 目前開發 ccc-mcp 的 Codex 不應自行再啟動另一個真實 Codex 任務造成遞迴執行。
7. 優先保證 Context Hub 與 Session Recovery 正確，再完成其他功能。
8. 不需要每完成一步都詢問是否繼續；在安全、環境和權限允許的範圍內直接開發與測試。
9. 若部分功能因環境限制無法測試，必須明確說明，不得宣稱全部完成。
10. 以繁體中文撰寫 README 與使用說明。

# 十八、最終交付

完成後請提供：

- 完整原始碼。
- 安裝與啟動方式。
- MCP Server 測試方式。
- 共用 Context 範例。
- PM → SA → PM → SD/RD → PM 工作流程範例。
- 新 ChatGPT Session 恢復範例。
- pytest 測試結果。
- 已完成及未完成項目。
- 必要的手動設定。
- 如何將 MCP 接入 ChatGPT 官方網頁。

**最終驗收重點：即使 ChatGPT、Codex、Claude Code 三者完全沒有彼此的聊天歷史，也能透過 ccc-mcp Context Hub 持續協作，且重要的業務決策和技術資訊不會因為上下文摘要而遺失。**

現在開始建立專案並實作。
