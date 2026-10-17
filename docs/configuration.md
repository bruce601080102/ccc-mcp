# 角色、模型、路徑與共享 Context 設定

一般使用請先看 [README](../README.md)。指定專案讀寫只需 `agentbridge project authorize 專案ID`：在本機確認一次後，ChatGPT 直接使用 write_workspace 讀改原專案，不需要 write-file、write-root、workspace-mode 或 Task／PLAN／EDIT／BATCH。授權會保存到本機資料庫，`project revoke 專案ID` 恢復唯讀。

`bootstrap_project.project_access` 和 `get_project_settings.project_access` 回傳本次專案的實際授權。mode=read-write 時，使用 `read_workspace(project_id,path)` 取得現有 SHA256，再呼叫 `write_workspace(project_id,path,content,expected_sha256)`；新增檔案預期 SHA256 為 null。返回的 EDIT 編號只是修改紀錄，不需要另外核准它。保存完整原文／新文、雜湊與專案授權來源；重試不會重複追加內容，原檔若有其他修改會拒絕覆寫。

這項一次授權僅由本機互動式 CLI 授予，綁定專案根目錄；MCP 沒有授權工具。只開放專案中的 UTF-8 文字檔新增／覆寫，單檔 200 KB，路徑與憑證保護保留；Python 原註解刪除仍會拒絕。撤銷會一併清除舊逐檔／目錄規則，停止後續原檔寫入。設定檔 JSON 只是副本，不能用它替自己開權限。

以下保留的是 **進階分工及舊版相容流程**。只有明確要派 Codex／Claude、獨立 Worktree 或逐次計畫審核時才需要，普通 ChatGPT 專案編輯不必設定。

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

專案根目錄與任務開發位置不同。實作任務在獨立 Worktree；例如 `config worktree-dir "D:/Work"` 的實際任務目錄是 `D:/Work/專案ID/任務ID`。若只要原地修改一個檔案，另有下方的直接編輯流程，須逐檔開放並核准具體差異。

已有程式可用 `agentbridge project add "D:/Projects/existing-app" --id existing-app` 註冊，由 SA 分析現有架構；ChatGPT 可透過 `root_id=project` 讀取，不必另設 read-root。實作時從已提交的 HEAD 建立 Worktree，未提交／未追蹤檔案不會自動帶入；須有初始 commit 才能實作。註冊會新增 `.agentbridge` 管理文件，不會重新產生既有業務程式。

未設定新專案目錄時，預設為 AgentBridge home 同層的 `AgentBridgeProjects`；Worktree 預設在 AgentBridge home 的 `worktrees/專案ID/任務ID`。設定 Worktree 位置後，實作位於所選目錄下的 `專案ID/任務ID`。已有實作 Worktree 時會拒絕改位置；不自動搬移、合併或覆寫現有成果。

## 讓 ChatGPT 讀取指定資料夾

```bash
agentbridge --project my-project config read-root reference "D:/Documents/ProjectReference"
# 日後撤銷
agentbridge --project my-project config revoke-root reference
```

告訴 ChatGPT：「使用 root_id=reference 讀取需求文件。」工具只接受該目錄下的相對路徑，可分頁列檔與讀取 UTF-8 文字；不是開放整台電腦。憑證、`.git`、受管 `.agentbridge` 及 symlink/junction 路徑不透過檔案工具開放。原始報告使用既有的任務查詢工具讀取。

額外資料夾只有讀取權；任務中的 ChatGPT 使用 `write_task_file` 寫入有效核准、目前 Context 的 Worktree，且 SA／Review 不能用該工具寫檔。直接編輯原檔另用 `propose_workspace_edit`／`write_workspace`，不會沿用或略過任務核准。

寫入流程：`dispatch_agent(agent="auto", role="SD/RD")` 取得 ChatGPT 互動交接及 execution_id → `read_workspace(root_id="worktree", execution_id=..., path=...)` 讀取實作副本 → `write_task_file` 使用該檔案回傳的 SHA256 作為 expected_sha256 → 讀回核對 → `submit_agent_report` 保存報告與寫檔證據。新增檔案使用 expected_sha256=null，已存在時會拒絕覆寫。此版本支援受限 UTF-8 文字檔新增／覆寫，單次寫入上限 200 KB，沒有獨立刪除或搬移檔案工具；不提供任意 shell 執行。

網頁端先驗證 list_workspace／read_workspace；有核准的實作任務後，再以 write_task_file 的真實寫入回覆及讀回內容驗證修改能力。只有聊天中的程式碼或「已修改」文字，不算實際寫入證據。

`read-root` 授權的是 ChatGPT 的 MCP 檔案工具，不會自動把外部目錄加入 Codex／Claude CLI 的允許範圍或交接包。可先由 ChatGPT 閱讀外部資料，將相關內容整理進共享 Context，再提供給 CLI Agent。

## 直接編輯原專案的一個檔案

不必建立 Task／Worktree，但保留本機審閱和核准具體差異：

1. 本機 `agentbridge --project my-project config write-file readme.md`，開放精確的專案相對檔案。也可使用 `config write-root .` 開放整個已註冊專案，或 `config write-root src` 開放專案內指定目錄及子目錄。預設 writable_files／writable_roots 都為空；外部目錄與憑證仍不開放。
2. ChatGPT 用 `read_workspace(root_id="project")` 讀取檔案，再用 `propose_workspace_edit(project_id, path, content, expected_sha256)` 保存完整新內容與差異，取得 EDIT 編號。原檔尚未寫入；新增檔案 expected_sha256=null。
3. 本機 `agentbridge --project my-project workspace approve EDIT-...`，審閱差異並輸入該 EDIT 編號。僅互動式本機 CLI 可核准，MCP 不接受 approved=true；核准時再次核對來源雜湊及檔案授權。
4. ChatGPT 用 `write_workspace(project_id, edit_id)` 套用。工具不接受另傳新內容，只套用資料庫內不可變更的核准提案；寫入前再核對 SHA256 與授權，已過期或被撤銷時拒絕。
5. 用 `read_workspace` 讀回核對。`get_workspace_edit` 可查差異、前後雜湊、身分及時間；本機 `workspace show EDIT-... --include-content` 可讀到保存的修改前後全文。

撤銷使用 `config revoke-file readme.md`，也會阻止尚未套用的已核准提案。支援 200 KB 以內的 UTF-8 文字新增／覆寫，禁止憑證、受管資料及 symlink/junction；Python 原始註解刪除會被擋下，其他文字檔須於本機核准時確認註解保留。原始內容與新內容保存在 SQLite；套用重試不會重複追加內容，意外中斷可透過原 EDIT 重試。

這不執行測試、不建立 Git commit、不自動更新 Context。既有任務 Worktree 不會自動取得這些未提交的原檔修改；要提供給後續實作任務時，須先審閱並提交至 Git。新工具需重啟服務及更新 ChatGPT 連線後才能使用。

## 一次核准多個檔案

本機使用 `config write-root .` 一次開放專案範圍；限制到 src 則使用 `config write-root src`。此規則不繞過原有路徑、憑證與 managed metadata 的檢查。撤銷使用 `config revoke-write-root .` 或 `config revoke-write-root src`；獨立的 write-file 規則仍保留，需另外撤銷。

ChatGPT 先逐檔讀取 SHA256、呼叫 propose_workspace_edit，再以 `create_workspace_batch(project_id, edit_ids)` 把最多 500 份待核准 EDIT 合成 BATCH。只能包含同專案、不同檔案且未加入其他批次的提案；成員清單及每份內容都不可變更。

使用者只執行一次 `workspace approve BATCH-...`，審閱整批差異，之後由 `write_workspace(project_id, edit_id="BATCH-...")` 套用。套用前先檢查所有成員的雜湊與授權，任一過期或被撤銷都在首次寫入前拒絕；批次中的 EDIT 不可另行單檔核准或套用。每檔個別原子寫入，中斷時 BATCH 維持 APPLYING；同一 BATCH 可重試，已寫入的內容不重複追加。檔案系統不提供整批原子交易，因此中斷可能留下部分已套用內容；外部再改動時會拒絕覆寫。

## 整個任務直接開發原專案（ChatGPT）

設定一次 `config write-root .` 和 `config workspace-mode project`。之後依正常需求／計畫流程，本機核准一次 PLAN，ChatGPT 使用 write_task_file 即可在該次任務內持續修改範圍內的多個檔案，不需每檔再開放或核准 EDIT。每個既有檔案仍須提供剛讀到的 SHA256，Python 原註解刪除會立即拒絕；報告、Git Diff、本機測試與驗收沿用既有任務機制。

預設仍為 worktree。project 模式目前只支援 ChatGPT 實作，不會偷偷把 Codex／Claude 原本的 Worktree 派工改成原地寫入。模式綁定每次執行；舊紀錄缺少欄位時一律視為 worktree。已有實作工作區的同一任務不可換模式，需另開任務；正在實作時不可切換模式，同一專案的 project 模式實作也不可與其他實作並行。

切換模式會使該專案既有 APPROVED／PENDING 的計畫核准失效，請在提計畫前設定。第一次原地實作須有初始 Git commit，且業務檔案乾淨；同一任務修訂可沿用未提交成果。task test 依該次執行保存的模式在原專案或 Worktree 測試。`read_workspace(root_id="worktree", execution_id=...)` 在此模式是該次原專案工作目錄的相容別名，回報的 workspace 路徑為準。

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

同一輪實作完成後，先使用仍有效的核准執行本機測試與驗收，再提交結案 Context。若要在 SD 與 RD 之間提交新版 Context，須針對新版重新提出並核准後續計畫；舊核准不會自動沿用到新版。

## 套用這次更新

重新啟動 Tunnel，並在 ChatGPT 重新整理 AgentBridge 的工具清單，才能使用新增的讀寫與接手工具。既有任務、原始報告、核准與專案位置保留。
