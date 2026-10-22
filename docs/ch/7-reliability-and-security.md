# 7. 可靠性與安全邊界

[返回快速上手](../../README_ZH.md)。本頁說明 `ChatGPT（PM）→ Codex（SA）→ Claude（SD/RD）→ Codex（REVIEW）→ ChatGPT 整理 Context` 這條預設流程裡，與「可驗證、可恢復」直接相關的六個機制。這些機制只在**已 `project authorize` 的持續工作流**（`start_project_request`／`advance_project` 自動接力）生效；逐步人工核准模式（見[安裝與登入](1-install-and-sign-in.md)第 4 節）沒有 `pending_dispatch`。

## 1. 接力中斷後怎麼恢復，而不是重頭來過

`advance_project` 每次執行分兩個階段：先把 PM 整理好的 ContextPatch 提交進共用 Context，再自動派出下一個角色。這兩步**不是同一個不可分割的操作**——如果第二步失敗（worker 行程起不來、暫時性版本衝突、CLI 當掉），Context 已經提交，不能重來一次（重送同一份 Patch 會被拒絕，因為來源報告已經「消化」過了），但下一個角色也還沒真的派出去。

ccc-mcp 把這個「已提交、未派工」的狀態寫進資料庫的 `pending_dispatch`，而且**跟 Context 提交是同一個 SQL transaction**——commit_context_patch 把 `context_versions` 的新版本與 `pending_dispatch` 的指標用同一個 `conn` 寫入，要嘛兩者一起成功，要嘛一起失敗。（初版實作曾經把這兩個寫入拆成先後兩步，中間若真的當機，會留下「Context 已提交、指標沒寫入」的窗口；已改成上述的單一交易，不會再有這個中間狀態。）所以就算整個 MCP 連線斷線、Tunnel 重啟、甚至是寫入指標那一步自己當機，這個狀態也不會丟失或卡在不上不下的地方：

```bash
ccc-mcp --project hello-demo task status
```

```jsonc
{
  "brief": {
    "waiting_on": "DISPATCH_RETRY_NEEDED",
    "pending_dispatch": {
      "source_execution_id": "EXEC-xxxxxxxxxxxxxxxx",
      "role_completed": "SA",
      "context_version": 3,
      "created": "2026-...",
      "next_action": "Context for this round was already committed; call resume_pending_dispatch ..."
    }
  }
}
```

看到 `waiting_on == "DISPATCH_RETRY_NEEDED"`（等同於 `brief.pending_dispatch` 不是 `null`）時，續跑方式是：

```bash
ccc-mcp --project hello-demo task resume
```

對應的 MCP 工具是 `resume_pending_dispatch(project_id, task_id?)`。這個操作**不需要重新提供 ContextPatch**——所有需要的資訊（該派哪個角色、原本的實作指令／計畫內容）在提交 Context 的當下就已經連同 commit 結果一起存進 `pending_dispatch`。PM 也可以直接重打一次原本那次 `advance_project`（同一份 patch），行為是等價的：系統偵測到這個任務有未解決的指標時，會略過重新提交 Context，只續跑派工那一步。

保證：
- **不重複提交 Context**：有未解決的 `pending_dispatch` 時，`advance_project` 會直接拒絕一份指向不同來源執行的新 Patch（`unresolved pending dispatch` 錯誤），逼你先解決掉目前這個。
- **不重複派工**：如果上一次嘗試其實已經成功建立了 Agent 執行，只是在清除指標前當機，resume 會偵測到這個仍在跑的執行並直接採用它，而不是再派第二個。
- **不遺失執行紀錄**：失敗的那次嘗試仍會留在 `get_task_result` 裡（標記 `FAILED`），resume 成功後產生的是另一筆新紀錄，兩筆都查得到。

任務被 `task cancel` 取消時，殘留的 `pending_dispatch` 也會一併清除。

## 2. 主機測試失敗，REVIEW 不能幫你蓋過去

`AgentReport.needs_revision` 是 Agent 自己的判斷；主機實際執行 `test_command` 的結果（`TEST-RESULT.md`／`brief.tests`）是獨立證據。過去只要 REVIEW 回報 `needs_revision: false`，`commit_context_patch` 就會把任務標成 `COMPLETED`——就算同一輪主機測試其實是失敗的。

現在 `commit_context_patch` 在判斷「這次 REVIEW 是不是真的完成了」時，會額外確認主機測試沒有失敗；只要測試是 `executed_by_host: true` 且 `passed: false`，就算 REVIEW 本身說沒問題，任務也不會被標成 `COMPLETED`，接力會自動退回 SD/RD，並在指令裡清楚寫明「是主機測試失敗，不是 REVIEW 自己發現問題」：

```bash
ccc-mcp --project hello-demo task status
```
```jsonc
{
  "brief": {
    "tests": {"executed_by_host": true, "passed": false, "reason": null},
    "waiting_on": "AGENT_RUNNING"   // 已自動重新派出 SD/RD
  }
}
```

`brief.tests` 永遠反映主機最後一次實際執行的結果，不會被任何 Agent 的自述覆蓋；「未執行測試」（`executed_by_host: false`，見 `reason`）跟「執行且失敗」是兩種不同狀態，不會混在一起判斷完成與否。

`brief.verification`（`task status`／`task handoff` 都會回報，`task show` 會直接印出對應文字）把這件事講得更直白，分成兩個獨立欄位，從不合併成一個「成功」：`agent_claim`（Agent 自己說完成還是需要修訂，或根本還沒有報告）與 `host_verification`（`passed`／`failed`／`no_tests_collected`／`not_executed`，主機實際驗證的結果）。當這一輪是因為上述機制被退回 SD/RD（主機測試結果覆蓋了 REVIEW 的『無需修訂』），`reverted_due_to_host_failure` 會是 `true`，跟 REVIEW 自己主動要求修訂的一般情況區分開：

```jsonc
{
  "brief": {
    "verification": {
      "agent_claim": "complete",
      "host_verification": "failed",
      "reverted_due_to_host_failure": false,
      "message": "Agent 自稱已完成；主機已實際執行測試，結果失敗——不算驗收成功"
    }
  }
}
```

不論 `agent_claim` 寫什麼，`message` 永遠不會把它說成「驗收成功」——那個詞只留給 `host_verification: "passed"`。

## 3. Fallback 與失敗原因分開記錄

每一次 Agent 執行失敗，`brief.failure_kind` 會標示原因類別：

- `QUOTA_EXHAUSTED`：訊息符合已知的額度／速率限制模式（`is_quota_error`），才會觸發 `--quota-fallback` 設定的接手（通常是轉交目前這個 ChatGPT 對話）。
- `ERROR`：CLI 未安裝、未登入、參數錯誤等任何其他失敗——**不會**觸發 fallback，就算有設定 `--quota-fallback` 也一樣。

`is_quota_error` 的判斷規則沒有被放寬；本次只是把既有的分類結果補進 `brief`，讓 PM／操作者不用去翻原始 JSON 就能看到：

```jsonc
{"brief": {"failure_kind": "ERROR", "fallback": null}}
```
```jsonc
{"brief": {"failure_kind": "QUOTA_EXHAUSTED", "fallback": {"attempted": true, "dispatched": true, "target": "chatgpt", "fallback_execution_id": "EXEC-..."}}}
```

`waiting_on` 也新增了 `CHATGPT_HANDOFF_PENDING`：當最新一次執行是「轉交目前 ChatGPT 對話接手」的互動式執行時會顯示這個值，跟一般背景 Agent 在跑的 `AGENT_RUNNING` 區分開——前者代表**這個對話自己要動手**（呼叫 `write_task_file`／`submit_agent_report`），不是等某個背景程序跑完。

## 4. Context 預算：超過就拒絕派工，不悄悄截斷

見[設定角色、Context 與測試](5-configuration.md)。預算裁剪的順序固定是：

1. **先裁 Skill 全文**：`available_skills` 是「操作者裝的選用指引」，裁剪時只清空 `content`（並標記 `content_omitted: true`），`id`／`name`／`description`／`path` 一律保留，所以 Agent 仍然知道有這個 Skill、用途是什麼，只是沒看到全文。大 Skill 先裁。
2. 還不夠才裁歷史紀錄（`decisions`／`technical_decisions`／`agent_results_awaiting_refinement`，只留最近 5 筆並改成指向 `get_context_history` 的指標）。
3. `invariants`、來源引用（`source_document_refs`）、當前任務的 `reports`／`task`／已核准方案**永遠不裁**——這些是這次派工的直接證據，不是可壓縮的歷史。

裁完之後如果仍然超過預算（`budget_exceeded: true`），`dispatch_agent` 會直接拒絕派工（`BridgeError`），而不是送出一份超額、可能已經漏掉東西的內容：

```
Context handoff exceeds context_token_budget (1498 estimated tokens > 1000) even after
trimming Skill text and compressible history; raise context_token_budget, remove Skills,
or reduce decision/report history, then dispatch again. Already omitted: [...]
```

這個硬性拒絕**只發生在 `dispatch_agent`（真的要派工時）**；`build_handoff`／`get_context` 這類唯讀查詢工具仍然會回傳裁剪後的內容讓你檢查，不會因為超額而整個拒絕回應。

## 5. 自動偵測並安裝相依套件

真正會卡住本機測試的常見情況，不是程式邏輯錯，而是**環境根本沒裝套件**——SD/RD 在實作時於 `requirements.txt`／`package.json` 加了新套件，但執行 `test_command` 的環境從來沒人幫它 `pip install`，結果測試在收集階段就 `ImportError`，看起來很像程式壞了，其實只是沒裝東西。

`execute_test_command`（`run_configured_test_command` 與手動 `ccc-mcp task test` 共用的同一條路徑）現在會在真正執行 `test_command` **之前**，先在同一個工作目錄（worktree 或 `workspace_mode=project` 時的原專案目錄）依序偵測：

| 偵測到的檔案 | 執行的安裝指令 |
|---|---|
| `requirements.txt` | `python -m pip install -q -r requirements.txt` |
| `pyproject.toml`（且沒有 `requirements.txt`） | `python -m pip install -q -e .` |
| `package-lock.json` | `npm ci` |
| `package.json`（沒有 lockfile） | `npm install` |
| 以上皆無 | 不執行任何安裝指令，直接跑 `test_command`（跟改動前完全一樣） |

**Python 專案會自動建立並重複使用一個專屬 venv**，不需要你自己 `config test-command` 指到某個 venv 的絕對路徑：第一次偵測到 `requirements.txt`／`pyproject.toml` 時，ccc-mcp 會在 `~/.ccc-mcp/venvs/<project_id>/` 建立一個獨立虛擬環境（跟你自己本機開發用的 `.venv`完全分開，不用猜名稱或位置），把套件裝進這個 venv，之後每輪都重複使用、不會重建。接著執行 `test_command` 時，即使你只設定 `"pytest -q"` 或 `"python -m pytest -q"` 這種不帶路徑的指令，也會自動解析到這個 venv 的直譯器——不需要手動改成絕對路徑。Node/npm 專案沒有對應的獨立環境機制：`npm install`／`npm ci` 本來就是裝進專案自己的 `node_modules`，跟著程式碼放在一起。

> **實作細節（Windows 專用的坑）**：單純把 venv 的 Scripts 目錄加進子行程的 `PATH` 環境變數**不夠**——Windows 的 `CreateProcess` 在解析不帶路徑的執行檔名稱（例如 `"python"`）時，用的是**呼叫端**行程當下的 `PATH`，不是即將交給子行程的那份環境變數，所以光改 `env["PATH"]` 不會讓 `"python"` 真的指向 venv（這不是臆測，是本次開發時自己的測試先抓到的）。實際做法是把 `test_command` 的第一個元素直接改寫成 venv 內對應執行檔的絕對路徑，`env["PATH"]` 只用來讓指令本身再往下開的子行程（例如 pytest 自己呼叫的子程序）也能繼續在這個 venv 裡解析套件。

**安全邊界**：套件安裝指令是 ccc-mcp 自動決定要不要跑、但**安裝目標清單是 Agent 寫入的檔案內容**——`setup.py`、npm 的 `postinstall` script 都可以在安裝當下執行任意程式碼。這跟 `test_command` 本身（操作者固定設定、Agent 完全無法變更）的信任模型不同，是本次新增、刻意沿用 `project authorize` 同意範圍的風險：等於同意自動安裝 Agent 寫入清單裡的任何套件。審閱 Diff 時請把 `requirements.txt`／`package.json` 的異動當成跟程式碼本身一樣需要檢查的內容。把安裝隔離在專屬 venv，至少避免了污染 ccc-mcp 自己執行所在的那個環境。

安裝結果記錄在測試證據的 `dependencies` 欄位，`brief.tests.dependencies` 可查（`venv: true` 代表這次用了專屬 venv）：

```jsonc
{"detected": true, "manifest": "requirements.txt", "installed": false, "venv": true, "reason": "Dependency install exited 1"}
```

安裝失敗視為**真正的測試失敗**（`executed_by_host: true`、`passed: false`），不是「未執行」——因此套用跟第 2 節一樣的保護：就算 REVIEW 自己說沒問題，任務也不會因為套件裝不起來就被標成完成。

## 6. `project authorize` 預設就會自動跑測試，不需要另外設定 `test_command`

原本即使已經 `project authorize`，也要再額外 `config test-command` 設定一次才會自動跑測試——這是刻意的雙重確認（會執行 Agent 寫的測試碼，是別於一般改檔的額外風險）。現在改成：`project authorize` 本身就是足夠的同意，沒有另外設定時，預設跑 `python -m pytest -q`；只有想換成別的命令才需要 `config test-command`。

**新專案還沒寫任何測試時怎麼辦？** `pytest -q` 在一個測試檔都沒有時會回傳退出碼 5（「沒收集到測試」），這跟「測試執行但失敗」（退出碼 1）語意不同。ccc-mcp 會辨識這個狀況，回報成：

```jsonc
{"executed_by_host": true, "passed": true, "no_tests_collected": true}
```

`passed: true` 只代表「pytest 本身沒有爆錯」，不代表「有東西被驗證過」——依照最初的要求「測試失敗或未驗證時，不可對外宣稱完整驗收成功」，這個狀態**一樣會擋住 REVIEW 自動結案**：`commit_context_patch` 把它跟第 2 節的主機測試失敗用同一個條件處理（`_host_tests_unverified`，與 `_host_test_failed` 並列），REVIEW 說沒問題也一樣會被退回 SD/RD，指令會明確寫「這輪什麼都沒被驗證，請補測試再提交」。`brief.tests.no_tests_collected` 讓這個狀態保持可見，不會被誤認為「已經有測試且通過」。這個退出碼判斷只在實際執行的命令看起來是 `pytest` 時生效，不會誤判其他自訂 `test_command` 的退出碼。

## 7. 專案根目錄被刪掉後，不會永久卡死

`project_access_events`、`context_versions`、`decisions` 都是刻意設計成不可刪除（append-only，刪除會直接觸發 SQLite trigger 擋下來）。這代表根目錄被使用者在 ccc-mcp 外手動 `rm -rf` 刪掉時，ccc-mcp 不會、也不能把這個專案的歷史憑空清掉。

在這種情況下，下一次任何操作碰到這個專案（`self.project(project_id)`）時，ccc-mcp 會自動重建一個空目錄並重新 `git init` 一次初始 commit，而不是讓這個 ID 永久卡在「註冊了但用不了」的狀態。原始檔案與 Git 歷史無法復原，但 Context、任務、決策全部還留在 ccc-mcp 自己的 SQLite 裡，不受影響：

```bash
ccc-mcp --project hello-demo config show   # 看起來像唯讀，但背後會先自動修復
ccc-mcp --project hello-demo context show  # 會看到 SYSTEM 自動記錄的這次修復
```

這個修復**不是悄悄發生**：每次修復都會額外產生一筆 `author_role: "SYSTEM"` 的 Context 版本，更新 `status`／`next_action` 說明「偵測到根目錄遺失、已自動重建、原始內容無法復原」，往後任何人讀 `context show`／`bootstrap_project`／`task status` 都看得到，不會以為專案一直都是這個狀態。重複呼叫是安全的：目錄已存在時不會重複 `git init` 或重複寫入 Context。

這跟「根目錄被竄改」（例如換成指向別處的符號連結）是兩種不同狀況：竄改會被直接拒絕（`Registered project root moved or is unavailable`），**不會**被當成「遺失」而自動修復——自動修復只處理「單純不存在」，不會信任一個存在但指向別處的路徑。

## 8. 不想再用某個專案了：`project delete` 永久註銷

上一節講的是「根目錄不見了但這個 ID 還要繼續用」；如果是反過來——**這個 ID 以後都不會再用了**，例如一個做完的 demo 或測試專案——`ccc-mcp project delete <id>` 提供一個明確、不可復原的註銷動作：

```bash
ccc-mcp project revoke demo-app   # 若目前是 read-write 持續授權，必須先撤銷
ccc-mcp project delete demo-app   # 互動確認，需輸入 DELETE demo-app
```

它做的事跟「根目錄遺失自動修復」是同一個不可刪除 append-only 原則的另一面，而不是矛盾：

- 新增一筆 `project_deletions` 紀錄（跟 `project_access_events` 同樣風格：append-only、有 trigger 擋 UPDATE/DELETE），一個 project_id 只要出現過一筆就永久視為已註銷。
- `ccc-mcp project list` 之後不會再顯示它。
- 幾乎所有操作的入口 `ContextHub.project()` 會直接拒絕已註銷的 project_id，訊息明確寫「已被永久註銷」——不會變成「註冊了但重建一個空殼」那種狀態。
- 這個 ID **永遠不能重新註冊**（`project create`／`project add` 都會擋，並提示換一個 ID），避免未來同名新專案意外繼承舊專案的審計紀錄。
- **不會刪除磁碟上的任何檔案**（這部分請自行用 Finder／檔案總管處理），**也不會刪除任何一筆 Context／task／決策／證據資料**——全部照舊留在 ccc-mcp 自己的 SQLite 裡，只是被永久封存、無法再透過任何指令存取。

安全防線：

- 若專案目前是 `read-write` 持續授權狀態，會先擋下來要求 `project revoke`。
- 若有 `QUEUED`／`RUNNING` 的執行中任務，也會擋下來，避免留下孤兒執行。
- 跟 `authorize`／`revoke` 一樣，需要互動式本機終端機確認，且要求輸入 `DELETE <id>` 這個明確字串才算確認（比一般確認多一層,呼應它不可復原的風險)。
- 只透過 CLI 提供，**沒有對應的 MCP 工具**——跟 `authorize`／`revoke` 一致，因為這個確認儀式本來就需要互動式終端機，ChatGPT 無法代為執行。

## 測試涵蓋

- [tests/test_resumable_advance.py](../tests/test_resumable_advance.py)：派工失敗後的恢復、拒絕不相關的 Patch、`resume_pending_dispatch`、`task cancel` 清理指標、主機測試失敗覆蓋 REVIEW 自述、未收集到測試一樣擋住自動結案、Context 提交與 `pending_dispatch` 指標確實寫在同一個交易裡。
- [tests/test_context_budget_enforcement.py](../tests/test_context_budget_enforcement.py)：Skill 裁剪順序、預算仍超標時硬性拒絕、裁剪後成功派工、沒設預算時維持原樣。
- [tests/test_fallback_classification.py](../tests/test_fallback_classification.py)：CLI 未安裝類錯誤不觸發 fallback、額度不足時的分類與 `CHATGPT_HANDOFF_PENDING`。
- [tests/test_dependency_install.py](../tests/test_dependency_install.py)：各種 manifest 的偵測優先順序、安裝成功／失敗／執行檔不存在、安裝失敗會擋住 REVIEW 自動完成、專屬 venv 的建立與重複使用、`_resolve_against_venv` 的 Windows 解析修正、`execute_test_command` 端到端驗證 venv 真的生效。偵測邏輯是純函式測試；安裝成功/失敗路徑用可控的假指令測試（不會真的呼叫網路上的 pip／npm）；venv 建立與 PATH 解析則是真實建立虛擬環境測試（無需網路，只用 `ensurepip` 內附的 wheel），避免測試依賴網路又不失去端到端真實性。
- [tests/test_test_evidence.py](../tests/test_test_evidence.py)：`project authorize` 預設自動跑 `pytest -q`、`no_tests_collected` 的正確回報。
- [tests/test_project_root_recovery.py](../tests/test_project_root_recovery.py)：根目錄被手動刪除後自動重建並重新 `git init`、修復是可重複呼叫且不重複記錄、既有設定（例如 chatgpt-only profile）在修復後保持不變、CLI `config show` 的真實重現、符號連結竄改仍會被拒絕而不是被修復。
- [tests/test_project_deletion.py](../tests/test_project_deletion.py)：`project delete` 需要互動確認、註銷後從 `project list` 消失且所有操作一律拒絕、ID 永久不能重新註冊（`project create`／`project add` 兩條路徑都擋）、`read-write` 授權與執行中任務分別擋下註銷、`project_deletions` 本身的 append-only 保證、CLI 端到端重現。
- [tests/test_verification_evidence.py](../tests/test_verification_evidence.py)：`brief.verification` 的五種可分辨狀態（尚無報告／Agent 自稱完成／自稱需修訂、主機已驗證通過／失敗／沒收集到測試／尚未執行、REVIEW 因主機證據被退回跟 REVIEW 自己要求修訂是兩種不同旗標）、CLI `task show`／`task status` 的呈現。
- [tests/test_task_handoff.py](../tests/test_task_handoff.py)：SA／SD-RD／REVIEW 三種角色的互動式提示詞內容、resume/PM 整理/背景執行等狀態重用既有欄位、CLI `task handoff` 的真實重現。
- [tests/test_task_preflight.py](../tests/test_task_preflight.py)：分級 `checks[]`／`summary` 輸出與原始三項環境檢查並存、授權 blocker、五個角色 provider 是否可用且只有 `next_role` 缺失才算 blocker（其餘只到 warning，並隨任務進度換成下一個角色）、`next_role` 隨 SA→SD/RD→REVIEW 推進、任務完成後 `next_role` 為 null 且不再出現角色檢查、ChatGPT 角色缺 Tunnel 依是否為 next_role 分別是 blocker／warning、Tunnel 已安裝但無法確認連線仍只到 warning、`profile` 欄位對照 chatgpt-only／custom、worktree 模式下 `test_command.cwd` 一定是這個 task 自己的 worktree（即使還沒建立也不會被偷換成 project root）、只有 worktree 不存在時才會跟用來預覽依賴套件的 `inspection_path`（project root）不同、project 模式下兩者都是 project root、worktree 建立後兩者相同、執行中/中斷派工/PM 整理/本機核准四種工作流狀態、Skills 可讀性、Context 預算超額（重用 `build_handoff` 本身的計算）、`summary` 與 `checks` 數量一致，全程確認從不實際安裝、跑測試或動 Git。

以上全部跑在 `FakeAgentAdapter`，不需要真的安裝 Codex／Claude。真實 CLI 的整合驗收腳本（`tests/test_real_agents.py`，`-m real_agents`）目前涵蓋正常接力與測試證據，**尚未涵蓋本頁描述的中斷／恢復與 fallback 分類場景**——這部分仍只在 Fake 測試中驗證過，真實 CLI 環境下的行為未另外實測。
