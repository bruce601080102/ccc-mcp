# 4. PM、SA、SD/RD 協作操作

此流程的工作記憶是 SQLite 與 `.ccc-mcp`，各 Agent 不讀取彼此聊天紀錄。SA 的方案仍由 PM 整理；其後低風險的 SD/RD 成功報告會自動寫入 Context、執行主機測試並派出 REVIEW，低風險 REVIEW 成功則自動完成。失敗、測試未驗證、需要修訂或決策時才會停給 PM。所有 CLI 全域參數放在子命令之前，例如 `ccc-mcp --project my-project context show`。

## 同一輪 ChatGPT 對話等待工作流

透過 MCP 提出新的修改需求時，`start_project_request` 會派 SA 並先等待進度；`advance_project` 派出下一角色後也會等待。若單次長輪詢到期但 Agent 還在跑，回應的 `workflow.must_continue_in_this_turn` 會是 `true`，ChatGPT 必須在同一輪再次呼叫 `wait_for_task`，不能只回覆「執行中」後結束。SA 完成而需要 PM 整理時，ChatGPT 應在同一輪讀取報告、建立有來源的 ContextPatch／PLAN、呼叫 `advance_project`，再繼續等待 SD/RD、主機測試與 REVIEW。

只有以下情況才應停止等待並回覆使用者：任務已完成；真實失敗／阻擋；或需要使用者提供產品決策、本機核准等新資訊／權限。使用者若只是明確要求「查狀態」，則只回傳快照，不擅自推進。這是 MCP 呼叫端的同輪完成合約；ccc-mcp 不會替 PM 偽造 SA 報告的語意審查，但會把每次應繼續的動作與 `must_continue_in_this_turn` 一起回傳。

## 1. PM 建立需求

```powershell
ccc-mcp init --id my-project --purpose "維護服務並保留 API 相容性"
ccc-mcp task create "新增健康檢查端點" --acceptance "GET /health 回傳 200" --acceptance "既有 API 不變"
```

需求與驗收原件持久保存，Context Patch 沒有更新原件的權限。若需求改變，另建任務並追加決策，保持歷史可追溯。

## 2. PM 指派 SA

MCP 範例：

```json
{
  "project_id": "my-project",
  "task_id": "TASK-0001",
  "agent": "codex",
  "role": "SA",
  "context_version": 1,
  "instruction": "分析端點路由、相關模組、驗收可行性與風險；引用程式碼。",
  "adapter": "fake"
}
```

派工回傳 execution_id，透過 `get_task_status` / `get_task_result` 查詢。Fake 報告明確標記 `simulated: true`；不應把其文字寫成已確認的系統架構。真實 SA 使用 Codex 唯讀沙箱。

SA Context Package 包含需求、驗收、架構與來源驗證、限制、相關決策、任務及 Git revision。SD/RD Package 另外包含核准方案、SA 原始報告及 Repository 位置。PM Package 包含進度、決策、最近 Agent 成果、風險與待決事項。

## 3. PM 整理成果與提案

```powershell
ccc-mcp context collect
ccc-mcp context source .ccc-mcp/tasks/TASK-0001/executions/EXEC-實際ID/SA-REPORT.md
```

使用來源工具回傳的 path/sha256，不要自行猜測雜湊。Patch 例如：

```json
{
  "base_context_version": 1,
  "changes": {
    "summary": "SA 已完成初步分析；風險與完整結論請見原件。",
    "next_action": "審閱 PLAN-A，必要時請 SA 補充分析。"
  },
  "source_task_ids": ["TASK-0001"],
  "source_document_refs": [],
  "update_reason": "PM 根據 SA 成果整理摘要",
  "author_role": "PM",
  "semantic_review_ack": true
}
```

實際使用時將剛取得的來源加入 source_document_refs；架構 facts 也必須帶各自的 source_document_refs。可用 `changes.source_document_refs` 將引用納入後續工作 Context，Patch 頂層引用另保存在版本稽核資料。

先 `propose_context_patch` 取得 diff，再 `commit_context_patch`。CLI 使用 `context propose PATCH.json` / `context commit PATCH-ID`。若版本衝突，重讀 Context 與來源後重新提出 Patch。

原始需求、決策、驗收、未解決 blocker 與約束不靠摘要文字判定是否保留。保護的 invariant 不可任意移除；資料庫任務/決策也不在可寫 Patch 欄位中。

## 4. 使用者審閱精確方案

> **這一節只適用於「逐步人工核准模式」**（專案還沒執行 `project authorize`、停留在唯讀模式）。已授權的專案是預設、建議的路徑：PM 以 `advance_project` 自動派出 SD/RD、REVIEW，不需要手動建立 PLAN、不需要在終端機輸入 Task ID 核准，見[建立第一個專案](3-first-project.md)。

先將預期修改範圍、限制、驗收與測試計畫寫入本機 PLAN-A.md：

```powershell
ccc-mcp task plan TASK-0001 PLAN-A PLAN-A.md --version 2
ccc-mcp task request-approval TASK-0001 PLAN-A
ccc-mcp approve TASK-0001 --plan PLAN-A
```

核准需操作者本人在終端輸入 Task ID。PM/MCP 呼叫只能建立 PENDING 請求，沒有 `approved=true` 的核准 API。方案 hash、方案版本、Context 版本與本機 OS principal 均留存。

若不同意：

```powershell
ccc-mcp reject TASK-0001 --plan PLAN-A --reason "驗收範圍尚未釐清"
```

重要業務決策可以在本機追加：

```powershell
ccc-mcp task decision TASK-0001 "保留既有回應格式" "避免破壞舊版呼叫端"
ccc-mcp task decision TASK-0001 "新決策內容" "取代原因" --status Superseded --supersedes DEC-實際ID
```

決策追加不會把舊列改掉。SQLite trigger 禁止更新/刪除既有決策與 Context 歷史。當前版本的方案變動或 Context 更新均會使舊核准不再適用，必須再次審閱。

## 5. SD/RD 在獨立 Worktree 工作

> **這一節同樣只適用於「逐步人工核准模式」。** Worktree 隔離與最後的 `task accept` 驗收，都是未授權專案才使用的流程；已執行 `project authorize` 的專案會自動把 `workspace_mode` 設為 `project`，實作 Agent 直接在原專案修改，REVIEW 通過後自動完成本輪，不經過這裡的核准與驗收步驟。

```powershell
ccc-mcp task dispatch TASK-0001 claude SD/RD "依 PLAN-A 實作並保留原始註解" --version 2 --adapter fake
ccc-mcp task status TASK-0001
ccc-mcp task result TASK-0001
```

每個 task 使用固定的獨立 Worktree；修訂任務沿用該 Worktree，不會丟失先前修改。Diff 是 Git 實際輸出，新增檔案另列 untracked_files 與可讀內容。模型的 TestClaim 僅是宣稱；由本機操作者授權的 `task test` 才是 host 測試證據。

```powershell
ccc-mcp task test TASK-0001
ccc-mcp task review TASK-0001 --version 2 --adapter fake
```

Review 有問題可回到 NEEDS_REVISION，PM 再指派 SD/RD；也可請 SA 再分析。若變更超出原方案，先提出新版本並重新核准。Python 原始註解被改動會 BLOCKED；其他語言或無法判定的修改也要求人工確認，不會自動完成任務。

最後由 PM 與使用者檢查結果：

```powershell
ccc-mcp task accept TASK-0001
ccc-mcp context export --out session-handoff.txt
```

本工具不合併 Worktree、不推送、不部署。合併前請人工審閱 Diff、註解與實際測試證據。

## 5a. Meeting Mode：SD/RD 動工前先問一輪（預設關閉）

```powershell
ccc-mcp --project my-project config meeting-mode on --min-rounds 1 --max-rounds 3 --escalation ask-user
```

開啟後，PLAN-A 核准、SD/RD 第一次真正動工前會插入一輪最多 5 次來回的澄清對話：SD/RD 可以先提出剛好一個阻斷性問題（這個階段完全唯讀，不能寫檔），由 SA 以 `confirmed`、`recommendation` 或 `pm_decision_required` 回覆。SD/RD 也可以直接回報 `no_blocking_question`，立即進入真正可寫檔的實作派工——但若設定了 `min_rounds`，在輪數達標前 SD/RD 會被要求「必須提問」，並接續下一輪；若 SD/RD 仍拒絕提問，流程不會卡死，`clarification.gate.min_rounds_unmet` 會標示未達標。輪次計數在重試、handoff、resume 之間永遠不會被重置，且這個澄清關卡每個任務只會觸發一次：之後若 REVIEW 要求修訂、重新派工 SD/RD，不會再問一次。

`task status`/`task handoff` 的 `clarification.gate` 用來證明關卡有跑過：SD/RD 的唯讀實作前檢查派工之前是 `null`，之後會顯示 `status`、`outcome`（`question_asked` 或 `no_blocking_question`）與 SD/RD 自己的 `rationale`——所以「檢查過、沒有問題要問」和「Meeting Mode 根本沒跑」可以區分。`clarification.triggered` 只有在 SD/RD 真的問出一個阻斷性問題後才會是 `true`——光是 `meeting_mode.enabled` 為 `true` 並不算。每一次 `wait_for_task`/`start_project_request`/`advance_project`/`dispatch_agent` 的回應（`workflow.handoff.clarification`）都帶著這個欄位，所以已連線的 ChatGPT 對話應該在關卡真正觸發時主動告知操作者，並從 `clarification.history` 摘要 SA/SD 的來回內容，而不是等任務完成才提一句。

`--escalation` 決定 SA/SD 討論未果（`pm_decision_required`）時由誰決定：`ask_user`（預設）要求 ChatGPT 把問題與 SA 的建議呈現給真人操作者，等待其在對話中的實際回覆後才能呼叫 `resolve_clarification`——此時 `workflow.must_continue_in_this_turn` 會是 `false`，確認這是真正需要停下來的情況。`auto_pm` 則允許 ChatGPT 在同一輪以 PM 身分立即自行判斷決定（`must_continue_in_this_turn` 為 `true`），之後仍應把決定內容與理由告知操作者。這項設定是透過指令引導、而非程式碼強制執行——ccc-mcp 無法從後端驗證「是否真的詢問了真人」——詳見[可靠性與安全邊界](7-reliability-and-security.md#meeting-mode)的完整保證範圍。

## 6. 全新 Session

啟用 ccc-mcp 連線並提出：

> 請使用 ccc-mcp 恢復目前專案的上下文，說明目前進度、最近決策、未完成工作與建議下一步。project_id 是 my-project。

只需 `bootstrap_project` 即能取得完整恢復入口。沒有 MCP 時貼入 `context export`。兩種方式都不要求歷史聊天、ChatGPT 個人 Memory 或先前模型 Session ID。

## 工作流與審查關卡

新任務使用「任務開始當下」專案所選的工作流（`config workflow classic|full`，或設定 → 工作流）。任務一旦開始就固定使用該工作流，之後改設定不會影響進行中的任務。

| 工作流 | 步驟 |
|---|---|
| `standard`（用 CLI 或控制台新增的專案預設） | SA → SD → RD → CR。設計與開發是分開的兩步：SD 先產出設計報告（唯讀），再由 RD 實作。 |
| `classic`（從沒選過的專案預設） | SA → SD/RD → CR。SD 與 RD 合成一步，行為不變。Meeting Mode 只適用於這條。 |
| `full` | SA → SD → AR → SR → SEC → RD → CR → SEC → QA |

角色：PM（發起並驗收的 ChatGPT 對話）、SA 系統分析、SD 系統設計（只做設計、不改檔案）、RD 開發、CR 程式碼審查（內部代號 `REVIEW`）、SR 規格審查、AR 對抗式審查、SEC 資安審查、QA 品質驗證、SW 規格寫手。SA、SD、RD、SW 是**產出**步驟；CR、SR、AR、SEC、QA 是**關卡**，負責評判。每個角色可各自設定 provider（`config role`），也能取聊天室名稱（`config role-name`，預設是縮寫）。

**關卡會退回。** 關卡發現阻擋問題時，由它前面最近的產出步驟依發現重做，之後的每一步都重新跑，所以不會有審查者評判過期的成果。一個關卡最多退回 `max_rounds` 次（`config review-gate --max-rounds`，1–5，預設 2）。超過後任務會停在 `waiting_on: PM_FLOW_DECISION`，不再循環；PM 呼叫 `resolve_flow_gate`，選 `proceed`（接受並繼續）、`retry`（再退回一次）或 `stop`。`--escalation ask-user`（預設）要求 ChatGPT 把決定交給真人使用者；`auto-pm` 則讓 ChatGPT 自行決定並說明理由。

**AR 不能用沒有證據的主張卡住流程。** AR 的每個發現都寫成 `[blocker|major|minor|assumption] 主張 | evidence: ...`，沒有證據的發現會被捨棄。強度（`--ar normal|strict|extreme`）決定哪些嚴重度能擋下流程：normal 擋 `major` 以上，strict 連 `minor` 也擋，extreme 連 `assumption` 也擋。`blocker` 表示整個做法有根本問題：直接交給 PM（`REJECT`），不退回產出步驟。判定由 ccc-mcp 依發現計算，不採信模型自己的結論。其他關卡信任審查者自己的 `needs_revision`；CR 與 QA 在 host 執行的測試失敗或沒驗證到任何東西時，也一律不通過。

每個關卡的判定都會保存（`flow_gate_results`），關卡已退回幾次是從這些紀錄算出來的，所以上限在當機或續派後仍然有效。結果為 `PASS` 的關卡會自動前進；SA 仍如以往需要 PM 提供方案。

### 規格寫手（SW）

SW 把討論得出的結論（需求、SA 的分析、SD 的設計，以及審查者提出的問題與解決方式）整理成一份「讓 AI 讀得懂、能照著實作」的規格書：目的與不做的事、名詞表、每個決定及其理由與被否決的替代方案、編號且可測試的需求（`REQ-001`，用 MUST/SHOULD/MAY）、精確的介面與資料格式、Given/When/Then 驗收條件，以及未解決的疑問（列出來，不亂猜）。文件存成 `SPEC.md`（報告本身是 `SW-REPORT.md`），之後的 SD、RD 和所有審查者都會拿到；控制台在任務的「執行成果」頁會顯示它。SW 只讀專案，不會改檔案。它是產出步驟，所以排在它後面的關卡退回時會退給它。目前沒有內建工作流包含 SW：到設定 → 工作流自己加，例如 `SA → AR → SR → SW → RD → CR`，讓規格在討論定案之後才寫。

### 自訂工作流，以及誰來挑

設定 → 工作流（或 `config workflow-save --steps SD,AR,RD,REVIEW [--name 名稱]`）可以用任意角色順序組出自己的工作流，最多 12 步。第一步必須是會產出東西的角色（SA、SD、RD 或 SD/RD），這樣審查者才有東西可審、也有人可以退回。存下來的工作流會自動命名為「工作流 2」「工作流 3」…（專案語言為英文時是 `Workflow 2`），之後可以改名；兩條內建工作流不能修改，只能另存一份。一個專案最多 20 條自訂工作流。刪除工作流不會影響正在跑它的任務；如果刪的是目前選的那條，新任務改回 `classic`。

沒有 SA 的工作流（例如 `SD → AR → RD → CR`）會直接拿你的需求與驗收條件當作核准的計畫，PM 一開始就直接對 SD。

`config workflow-selection fixed|pm|auto` 決定誰來挑新任務的工作流：

| 模式 | 行為 |
|---|---|
| `fixed`（預設） | 固定用選定的那條。 |
| `pm` | ChatGPT 先呼叫 `list_workflows`，依任務挑最合適的，並把 id 當作 `workflow` 傳給 `start_project_request`；沒挑就用選定的那條。 |
| `auto` | ccc-mcp 依任務文字自動挑。安全相關（登入、密碼、token、權限、付款…）需要含 SEC 的工作流；大型或有風險的改動（重構、遷移、架構、schema…）需要 SD 與 AR；小修改（錯字、改名、文件…）用最短的；其他維持選定的那條。符合條件的裡面選步驟最少的。 |

選了哪條、怎麼選的、為什麼，都會和任務一起保存（`task status` → `brief.workflow`）。只有 `pm` 模式才會採用 `workflow` 參數，其他模式會忽略。

## 任務詳情 2.0：工作對話與使用者介入

本機 Web 控制台的任務詳情預設顯示「工作對話」。它從既有 `task_events`、`agent_executions` 和每次保存的 `TEST-RESULT.md` 重建事件，再合併使用者介入紀錄；舊任務無需遷移即可閱讀。Agent 摘要採用已保存的原始報告，主機測試只採用 host 執行的證據。原始報告、Diff、評估與 Context 可在「執行成果」及「技術紀錄」展開查看。

「暫停工作」會持久化派工閘門。已派出但尚未開始的 worker 會等待恢復；已在執行的 Agent 或主機測試可能在安全點完成，畫面會顯示「暫停中」，完成後才顯示「已暫停」。按「繼續工作」會解除閘門並接續同一個已排隊的執行；若下一步需要 PM/ChatGPT，畫面會顯示保存的 handoff 指引，Web UI 不會自行啟動 ChatGPT。

「停止任務」會保存原因和時間、標記執行取消，並禁止新的派工。執行仍在收尾時顯示「停止中」，結束後才顯示「已停止」。停止不會還原已修改的檔案。對話輸入框會將修正意見保存至該任務，預設同時暫停後續派工；`task_handoff` 和下一次 Agent handoff 會帶入尚未交付的意見，交付後不再重複帶入。輸入框只控制原工作流，不是另一個 LLM 聊天室。
