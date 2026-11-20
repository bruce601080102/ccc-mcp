# 16. 執行一個已核准的 Phase，含自動修正迴圈

Goal 的計畫核准後，你可以執行其中**一個 Phase**。這個 Phase 會變成現有工作流引擎裡的一個一般 Task（你的 Agent Studio 角色與 Provider、Review Gate、Host Test 都照舊）。當 Host Test 或審查失敗，**Delivery PM** 會讀取證據並寫出結構化的修正規格；開發者角色帶著它被送回去重做；Host Test 再跑一次。你不必再把回覆在 ChatGPT、Codex、Claude 之間手動轉貼。

**手動啟動的 Phase 完成就停。** 不會替你開始下一個 Phase；要讓所有 Phase 依序執行，請改為啟動整個 Goal（[第 17 章](17-goal-run.md)）。沒有常駐行程：有事發生時才會喚醒一個短暫的行程，處理完就結束。

在主控台的 Goal 頁面使用（**執行這個 Phase**），或用 `ccc-mcp goal phase-start GOAL-0001 P1 [--wait]`。英文版：[docs/en/16-phase-runner.md](../en/16-phase-runner.md)。

## 開始之前必須成立的條件

* 計畫版本**已核准**（由人針對該版本與其雜湊核准）。執行會記下版本與雜湊，且只依據它行動；Phase 還開著時，修改計畫、重新規劃或取消 Goal 都會被拒絕。
* 它依賴的 Phase 都**已通過**，而且沒有其他 Phase 還開著。
* 專案已**授權寫入**（`ccc-mcp project authorize`）。核准 Goal 不等於授權專案；Task 自己的計畫核准仍是現有規則：由專案授權代為核准，Goal 核准不能取代。
* 專案有**明確設定的測試指令**。主機只執行這個指令。計畫裡寫的指令一律不會被執行；`host_test` 條件只有在指令與它完全相同時才算被驗證，其餘條件都需要人工驗收。
* 工作流需是含開發者步驟的步驟式工作流（例如 Standard），且沒有角色需要 ChatGPT 瀏覽器分頁。Classic 會被拒絕：它無法把失敗的步驟交回去。

## 過程

1. 依據已核准的範圍、交付內容與驗收條件，為這個 Phase 建立一個 ID 固定的 Task（`GOAL-0001-P1`，不是 `TASK-NNNN`，也不會被重用）。
2. 現有引擎執行：分析師、設計師、開發者。分析師的報告只成為已核准 Phase 的*備註*；Phase 文字與驗收條件不會被改寫。
3. 主機執行測試指令。通過才會進行審查。失敗時，不會再叫審查者重複說一次，而是交給 Delivery PM。
4. Delivery PM（透過與規劃相同的 Adapter 執行的唯讀 Codex 或 Claude）收到：需求、Phase 與其驗收條件、計畫版本與雜湊、完整測試輸出、git diff、審查者的意見、先前的嘗試與修正、剩餘預算。它回答一份**修正規格**：什麼失敗、各項原因（`confirmed` 必須引用證據中確實存在的文字，否則是 `hypothesis`）、要修改的位置、不得修改的內容、每個已核准條件各一項完成條件，以及是否需要重做分析。
5. 開發者角色（`restart_from_step`）帶著該規格被送回，另加 ccc-mcp 固定附上的文字：驗收條件不變、主機會跑同樣的測試、不得修改測試、測試設定、CI 檔、`.git`、`.ccc-mcp`，不得 commit/push/發佈。
6. 只有在 Task 通過自己的 Review Gate、主機看到設定的測試指令通過、沒有動到受保護的檔案、且每個條件都有著落時，Phase 才是 **PASSED**。含有指令無法判斷之條件的 Phase 會**等待你驗收**（`ccc-mcp goal phase-accept`，或在主控台輸入確認字串），而不是直接通過。

## 上限與計數單位

| 單位 | 意義 | 預設 |
|---|---|---|
| 嘗試（Attempt） | 對該 Phase 的 Task 派一次開發者（RD／SD/RD）。第一次是第 1 次；每次修正是下一次。同角色因額度而換 Provider 不算新的嘗試。 | 每個 Phase 3 次 |
| Agent 派工 | 該 Goal 的每一次提供者執行：Task 的每次執行（任何角色，含備援）加上每次 Delivery PM 分析。 | 每個 Goal 20 次 |
| 時間 | 該執行處於工作中的時間；等待人工不計。 | 每個 Goal 60 分鐘 |
| 重試 vs 新 Task | 重試是同一個 Task 內的新嘗試。一個 Phase 不會有第二個 Task。 | |

計數取自資料庫，所以當機也不會讓計數出錯；並且**在 `dispatch_agent` 強制執行**，因此引擎自己的串接、修正派工，或有人直接要求，都受同樣限制。恢復執行不會增加任何額度。

**沒有進展。** 若某次嘗試的失敗指紋（失敗的測試與錯誤，忽略時間、位址與路徑）與上一次相同，而且沒有實質改變（沒有檔案變更，或 diff 完全相同），執行會以 `NO_PROGRESS` 停下，而不是用掉下一次嘗試。

## 會讓它停下的一切情況

它會停下、說明原因、然後等待。以下任一情況之後都不會自行重試，也沒有任何狀態會落入看不見的迴圈。

| 狀態 | 原因 |
|---|---|
| `EXHAUSTED` | 嘗試、Agent 派工或時間用完（最終狀態；其 Task 會被停止） |
| `NEEDS_HUMAN` | 沒有進展；Delivery PM 要求人工、逾時、額度用完、無法連線或回覆無效；某次 Agent 執行失敗、逾時、額度用完、無法啟動或被中斷；Host Test 可能還在執行或被中斷；測試環境被阻擋；受保護的檔案被修改；任務在等待決策 |
| `AWAITING_ACCEPTANCE` | 測試與審查通過；剩下只有你能判斷的條件 |
| `PAUSED`／`CANCELLED` | 你做的 |

每個都有原因代碼、一句白話說明與稽核事件。恢復（`ccc-mcp goal phase-resume RUN [--note ...]`）會從停下的地方繼續；備註會交給下一次 Delivery PM 分析。

## 模型不能做的事

修正規格沒有任何欄位可以改需求、驗收條件、測試指令、角色、Provider 或權限，多餘欄位會被拒絕。規格在以下情況會被**整份拒絕**（不會有任何東西送給開發者）：指定了與專案不同的測試指令；少了、多了或改了已核准的條件；指向受保護路徑（`.git`、`.ccc-mcp`、`.github`、`pytest.ini`、`tox.ini`、`setup.cfg`、`pyproject.toml`、`conftest.py`、CI 檔）；沒有任何原因；含有機密。被標為 `confirmed` 但引用的文字不在證據中的原因，會被記錄為 `hypothesis`，並告知開發者。開發者自己修改受保護檔案會讓執行停下（`PROTECTED_PATH`），即使之後測試通過；要繼續需要 `--acknowledge-protected-paths`。

## 重啟與恢復

狀態在 SQLite（`goal_phase_runs`、`goal_phase_attempts`、`goal_phase_repairs` 與 `goal_events`；屬於歷史的部分只增不改）。重啟後可以看到上次使用的 Prompt 雜湊、Provider、Task 與修正輪次。同一個 Phase 重複啟動，或從兩處同時啟動，只會得到同一個執行與同一個 Task。同一時間只有一個行程推進一個執行（檔案鎖）；忙碌時收到的喚醒不會遺失。啟動到一半中斷會被補完而不是重做；中斷的 Delivery PM 呼叫會被記為已中斷並只重問一次（且計入額度）；已記錄但尚未送出的修正只會送出一次，不會再問 PM。

`ccc-mcp goal phase-recover`（或在執行看似沒有動靜時按**重新接手**）會接手行程已中斷的執行。它先套用 Host Test 的恢復規則：已停止的 worker 會被關閉為已中斷且不會重放；無法證明已結束的 Host Test **不會再次執行**，執行會等你處理，只有 CLI 的 `--confirm-host-tests-stopped` 能覆寫。主控台與 MCP 沒有這個覆寫。

## 誠實的限制

* 證據等級：本儲存庫的測試用真實的 Adapter、worker 與真實的 Host Test，搭配**模型的腳本化替身**。沒有用過任何真實模型、訂閱或額度。不要把修正迴圈在真實模型上會成功視為已證實。
* 一次一個 Phase。沒有任何東西會啟動下一個 Phase，也沒有常駐執行器。
* Windows 尚未驗證；網路檔案系統與兩台機器共用同一目錄未涵蓋；舊版 ccc-mcp 混用時不受 Host Test 租約保護。
* worker 被強制終止後殘留的 Delivery PM CLI 行程是唯讀的，會在自己的逾時後結束；不會替你停止它。
* 受保護路徑清單是擋住明顯作弊方式的護欄，不是沙箱。請檢視 diff。
* 若機器在執行工作時休眠，牆鐘時間仍會繼續計算。
* Phase 執行不支援 Classic 工作流，也不支援需要 ChatGPT 瀏覽器角色的專案。
* 同一個 Goal 中已結束的 Phase 不能再次執行（重跑需要新的 Goal）。
