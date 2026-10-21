# ccc-mcp 詳細參考

[返回快速上手](../README.md)。以下指令都在 ccc-mcp 專案根目錄執行。

角色／模型／思考等級、ChatGPT 接手、指定讀取目錄與執行稽核的最新操作，請看 [設定指南](configuration.md)。本頁保留完整 CLI 與協作背景。

以 Python 建立的 Context Hub 與 MCP 協調層，讓 ChatGPT PM、Codex SA、Claude Code SD/RD 能在沒有彼此聊天歷史的情況下，透過版本化記憶、原始證據與角色交接持續協作。

目前為可執行的單機 MVP。核心、Fake Agent、stdio 與 Streamable HTTP 均有自動化測試；真實模型執行與 ChatGPT 帳戶連線需使用者完成官方安裝、登入及權限設定。

## 安裝（Windows PowerShell）

需要 Python 3.11+ 與 Git。本次驗證環境為 Windows、Python 3.14.7、Git 2.52.0、MCP Python SDK 1.30.0。

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -e ".[dev]"
# 可不啟用虛擬環境，直接使用完整路徑
.\.venv\Scripts\ccc-mcp --help
.\.venv\Scripts\ccc-mcp doctor
.\.venv\Scripts\python -m pytest -q
```

Linux/macOS 改用 `.venv/bin/python` 及 `.venv/bin/ccc-mcp`。本次未在這兩個平台實測。ccc-mcp 不需要 npm 或 npx。

若要重現本次 Windows 依賴版本，可先 `python -m pip install -r requirements-windows.lock`，再安裝本專案。這份 lock 不適用於其他平台。

下列命令假設已啟用虛擬環境，或已將 `.venv/Scripts` 加入 PATH：

```powershell
.\.venv\Scripts\Activate.ps1
ccc-mcp init --id my-project --purpose "專案目標與原始業務需求"
ccc-mcp project add .
ccc-mcp project list
ccc-mcp context show
ccc-mcp context validate
ccc-mcp context history
```

若 PowerShell 不允許啟用腳本，繼續使用可執行檔完整路徑即可。`init` 與 `project add` 都會註冊及初始化專案，重複註冊相同路徑不會覆寫資料。中文目錄名稱可搭配 `--id` 指定穩定的 ASCII 專案 ID。

初始目錄與唯讀 SA/Review 不需要 Git；Codex Adapter 使用 `--skip-git-repo-check` 並保留唯讀限制。**實作任務必須已有 Git 初始 commit**。請自行審閱並提交要提供給 Agent 的基準版本。Worktree 以 HEAD 建立，不包含主要工作目錄尚未提交的修改。

## 記憶與證據

```text
被管理的 Repository/
└── .ccc-mcp/
    ├── CONTEXT.md
    ├── config.yaml
    ├── context/
    │   ├── PROJECT.md
    │   ├── ARCHITECTURE.md
    │   ├── DECISIONS.md
    │   ├── CURRENT_STATE.md
    │   └── CONSTRAINTS.md
    ├── tasks/TASK-0001/
    │   ├── REQUIREMENT.md
    │   ├── SA-REPORT.md
    │   ├── APPROVED-PLAN.md
    │   ├── SD-DESIGN.md
    │   ├── IMPLEMENTATION.md
    │   ├── TEST-RESULT.md
    │   ├── REVIEW.md
    │   └── executions/EXEC-…/  # 每次執行的原始成果，不覆寫
    ├── handoffs/SESSION-HANDOFF.md
    └── history/context-events.jsonl
```

SQLite 正式資料位於 `$HOME/.ccc-mcp/bridge.sqlite3`，包含 projects、tasks、task_events、context_versions、decisions、approvals、agent_executions，以及方案、Patch、原始文件。`CCC_MCP_HOME` 或全域 `--home PATH` 可改變位置。Worktree 位於該 home 的 `worktrees/PROJECT/TASK`。

Layer 1 是工作摘要與角色交接；Layer 2 是原始需求、每次報告、Diff、決策與完整 Context 版本。更新摘要不會刪除 Layer 2。最新版報告名稱是可重建的投影，歷次原件保留於 `executions/` 與 SQLite。PROJECT/CURRENT_STATE/DECISIONS 等文件會加入資料庫中的需求、驗收與決策，不會因 PM 縮短 summary 而消失。

Markdown 透過暫存檔、fsync、os.replace 原子更新；專案檔案鎖與 SQLite transaction 控制併發。多份 Markdown 並非同一個檔案系統交易，若資料庫提交後發生斷電，使用：

```powershell
ccc-mcp context validate
ccc-mcp context recover
```

`recover` 從資料庫重建文件；來源程式碼已改變的警示仍需重新分析。備份請同時保留 Repository、ccc-mcp home、Worktree；運行中資料庫請用 SQLite backup API，或停止 Worker 後再複製，勿只複製單一 WAL 模式的 db 檔。

## PM → SA → PM → SD/RD → PM

完整可操作範例見 [協作流程](workflow.md)；快速示範不使用任何真實模型：

```powershell
python examples/demo.py prepare
# 在互動終端審閱顯示的方案，手動輸入 TASK-0001 核准
ccc-mcp --home .demo/hub --project demo approve TASK-0001 --plan PLAN-A
python examples/demo.py implement
python examples/demo.py recover
```

`prepare` 僅在 `.demo/project` 建立新的範例 Git 儲存庫、Fake SA 成果、PM Patch 及待核准方案。`implement` 使用 Fake Claude，驗證獨立 Worktree 與交接，**不會假裝產生真實功能或執行測試**。這個範例的人工核准沒有測試捷徑。

任務指令：

```powershell
ccc-mcp task create "新增健康檢查 API" --acceptance "既有 API 不變"
ccc-mcp task dispatch TASK-0001 codex SA "分析相關程式碼與風險" --version 1 --adapter fake
ccc-mcp task list
ccc-mcp task status TASK-0001
ccc-mcp task result TASK-0001
# 由 PM 寫入 PLAN-A.md，版本必須對應當前 Context
ccc-mcp task plan TASK-0001 PLAN-A PLAN-A.md --version 1
ccc-mcp task request-approval TASK-0001 PLAN-A
ccc-mcp approve TASK-0001 --plan PLAN-A
ccc-mcp task dispatch TASK-0001 claude SD/RD "依核准方案實作" --version 1 --adapter fake
ccc-mcp task review TASK-0001 --version 1 --adapter fake
ccc-mcp task cancel TASK-0001
ccc-mcp task recover
```

每次派工立即回傳 task_id/execution_id，由獨立 Python Worker 執行。狀態查詢不需保持原本 MCP 連線。逾時與取消會終止 Agent 子程序樹；Windows Job Object 在 Worker 異常結束時清理其 Agent 子程序。`task recover` 將沒有 Worker lease 的未完成執行標為 BLOCKED，**不自動重播**；先檢查 Worktree 與事件後，再以新 execution 明確派工。仍有有效 lease 的 Worker 不會被復原操作干擾。

SA 可以反覆分析，SD/RD、SA、Review 也可依狀態交替。COMPLETED/CANCELLED/REJECTED 為終止狀態；新需求另開任務。最後需由 `ccc-mcp task accept TASK-0001` 在本機互動終端驗收。狀態轉換均有事件紀錄。

## Context Optimization

Agent 成功回報時，ccc-mcp 會在同一交易中保存不可變原始報告，追加一筆 `unreviewed` 的 `shared_context.agent_results`，並自動增加 Context Version。下一個 Agent 的 handoff 會直接包含這筆結果；此流程不呼叫 ChatGPT，也不依賴提示詞。

需要把原始結果提升成正式 summary、architecture 或其他語意欄位時，PM 才執行以下選擇性精修：

1. `context collect` / MCP `collect_context_updates` 收集事件、成果與決策。
2. PM 閱讀來源，產生 ContextPatch。
3. `propose_context_patch` 驗證版本、來源雜湊、必要欄位、不可遺失資訊，回傳 diff。
4. `commit_context_patch` 再次驗證並提交。

JSON Patch 範例見 [examples/context-patch.json](../examples/context-patch.json)：

```powershell
ccc-mcp context source .ccc-mcp/tasks/TASK-0001/executions/EXEC-實際ID/SA-REPORT.md
ccc-mcp context propose examples/context-patch.json
ccc-mcp context commit PATCH-實際ID
# MCP 寫入不可用時，可用同一 JSON 一次匯入
ccc-mcp context import-patch examples/context-patch.json
```

`semantic_review_ack` 是 PM 審閱語意差異的紀錄，不是人工實作核准。雜湊只能證明引用的檔案版本，無法證明 AI 的技術解讀正確。架構陳述必須有來源；程式碼/文件內容或原始報告漂移時會提示重新驗證。尚未完成的需求、驗收與限制由結構化 invariant 及不可被 Patch 修改的任務資料保護。現有 invariant 不能直接刪除或改寫；完成狀態另記錄於 Task/Decision，避免摘要無聲移除規則。

兩個 Patch 都基於版本 1 時，第一個提交得到版本 2，第二個會收到 Version Conflict。方案及人工核准亦綁定 Context 版本；Context 更新後必須重新提出目前版本的方案並核准。`token_budget` 超出時會先嘗試裁剪（Skill 全文優先、再壓縮歷史紀錄），絕不截斷必要資訊（`invariants`、來源引用、當前任務報告／核准方案）；裁完仍超出時，`dispatch_agent` 會直接拒絕派工而不是送出超額內容，唯讀查詢工具（`build_handoff`／`get_context`）則仍回傳裁剪後內容供檢查。細節見 [reliability.md](reliability.md)。

## 新 ChatGPT Session 恢復

在新對話啟用連線並明確提出：

> 請使用 ccc-mcp 恢復目前專案的上下文，說明目前進度、最近決策、未完成工作與建議下一步。專案 ID：my-project。

PM 呼叫 `bootstrap_project({"project_id":"my-project"})`，取得目標、版本、Git revision、任務、決策、阻塞、未決問題、限制、交接與下一步。不假設每個新對話會自行呼叫 MCP，也不依賴個人 Memory。

沒有 MCP 時：

```powershell
ccc-mcp context export --out session-handoff.txt
```

將輸出貼入新對話即可。輸出保留必要資料，因此大型專案可能超過一般摘要長度；可先以角色與 task 限定 `context show`，勿硬裁切驗收條件。

## MCP Server 與測試

```powershell
ccc-mcp serve
ccc-mcp serve --transport streamable-http --host 127.0.0.1 --port 8765
python examples/mcp_probe.py --home "$HOME/.ccc-mcp" --project my-project
python examples/mcp_probe.py --url http://127.0.0.1:8765/mcp --project my-project
```

stdio 提供規格要求的 14 個工具，加上建立任務、取消、來源雜湊、驗證與蒐集證據等工具。HTTP 預設只提供讀取工具；啟用寫入需 `--enable-writes` 與 OAuth JWT 設定。MCP 沒有 register_project、approve、accept 或任意 shell command 工具。安全操作先透過本機 CLI 完成。

任意支援 stdio 的 MCP Client 可使用以下設定，請將路徑換成實際的**絕對路徑**：

```json
{
  "mcpServers": {
    "ccc-mcp": {
      "command": "C:/path/to/ccc-mcp/.venv/Scripts/python.exe",
      "args": ["-m", "ccc_mcp", "serve"],
      "env": {"CCC_MCP_HOME": "C:/Users/YOUR_NAME/.ccc-mcp"}
    }
  }
}
```

ChatGPT 官方網頁的 HTTPS / Secure MCP Tunnel、OAuth 與工作區權限操作請見 [ChatGPT 連線指南](chatgpt.md)，依 [OpenAI 官方連線文件](https://developers.openai.com/plugins/deploy/connect-chatgpt) 核對。首次設定請依上述指南完成。

## 真實 Agent、人工核准與安全邊界

從已連線的 ChatGPT 接上真實 Codex SA 與 Claude Code SD/RD，請依 [真實 Agent 設定指南](real-agents.md) 操作。`ccc-mcp connect` 會自動尋找已安裝的 codex／claude CLI，不需要另外設定 PATH；重新執行 `ccc-mcp connect tunnel_你的實際ID` 即可重啟 Tunnel。

Codex 與 Claude Code 必須由使用者依官方方式另外安裝並登入。`doctor` 僅查安裝及版本，不宣稱已登入。Adapter 執行前再次檢查 CLI help；缺少必要參數會明確失敗。

```powershell
$env:CCC_MCP_ENABLE_REAL_AGENTS = "1"
ccc-mcp task dispatch TASK-0001 codex SA "分析需求" --version 1
```

Codex 依角色使用 `read-only` 或 `workspace-write` sandbox 與 JSON Schema。Claude 使用 print/JSON、`--safe-mode` 保留訂閱登入並停用自訂載入，另以空設定來源與空 MCP 清單隔離專案設定；SA/Review 僅 Read/Glob/Grep，實作角色另有 Edit/Write。角色可由設定選擇供應商，真實執行結果與用量應依個別任務的原始報告及 INVOCATION.json 判定。

實際測試另以 `ccc-mcp task test TASK-0001` 由本機操作者確認後，在 Worktree 執行固定的 `python -m pytest -q`。Agent 聲稱的 tests 與 host 實際測試證據分開保存。Repo 測試本身可執行程式碼，請先審閱後授權。

核准時會顯示方案、SHA256、版本與 OS principal，要求互動終端重新輸入 Task ID，並記錄稽核資料。方案內容或版本不符時拒絕實作。一般決策可用 `task decision` 追加 Approved/Rejected/Superseded，`--supersedes DEC-ID` 保留歷史；`reject TASK --plan PLAN-ID --reason "原因"` 可拒絕待核准任務。

此 MVP 的信任邊界是**同一個受信任本機操作者**。它不是能抵禦同 OS 帳戶惡意程序的人工身分驗證系統：持有同帳戶 shell/資料庫權限的程序仍可模擬終端或改 DB。多使用者部署應使用獨立操作者帳戶、ACL、外部核准服務或硬體簽章。Worktree 與 CLI 工具限制亦不等於完整 OS 沙箱；要防範惡意 Repository，應在隔離 VM/容器中執行真實 Agent 與測試，不提供主機憑證或資料庫寫入權限。

其他已實作控制包括路徑註冊、traversal/symlink/junction 拒絕、受管檔案鎖、日誌遮罩及常見秘密字串拒絕。秘密偵測為啟發式，無法保證識別任意格式的憑證。Python 註解使用 tokenize 精確比較文字與數量；其他變動檔案保守標示需人工確認，狀態 BLOCKED。程式不自動 push、merge 或 deploy，也不自動將整個 Repository 傳給 ChatGPT。

## 交付範圍

已實作：Context Hub、版本歷史與衝突、Session 恢復、角色交接、PM Patch、來源漂移檢查、獨立 Worker、狀態機、Fake/CLI Adapter、人工核准、決策稽核、Worktree、註解檢查、CLI、MCP 兩種傳輸、JWT 資源伺服器與測試。驗證紀錄見 [VALIDATION.md](../VALIDATION.md)。

各次真實 Codex/Claude 與 Tunnel 驗證見 [VALIDATION.md](../VALIDATION.md)；其他環境仍需確認帳戶權限、OAuth 部署、隔離執行環境與非 Python 註解的人工審閱。沒有 Web UI、Redis、Kubernetes 或分散式排程。

原始碼依功能放在 `context.py`、`tasks.py`、`storage.py`、`agents/`、`worker.py`、`git.py`、`security.py`、`server.py` 與 `cli.py`，讓 CLI、MCP 與 Worker 共用相同規則。
