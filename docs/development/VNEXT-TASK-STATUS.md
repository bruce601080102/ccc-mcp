# ccc-mcp vNext 任務進度（VNEXT-TASK-STATUS）

本文件是任務追蹤的固定進度表。每項任務都標示**完成範圍**與**限制**，並分開記錄兩類證據：

- **【本次查證】**：在本次工作階段（2026-11-14）實際執行過的命令與其結果。
- **【歷史報告】**：先前工作階段或交接文件的陳述。本次未重新驗證，只作紀錄，不代表目前狀態。

## 總覽

| 編號 | 任務 | 狀態 | 本次查證 |
|---|---|---|---|
| T01 | 雙核心產品定位與 PRD | 完成 | 未查證（歷史報告） |
| T02 | P0A Token 正規化 | 離線版完成 | 相關測試通過（見 T02） |
| T03 | P0A Evidence Chain | 第一版完成 | 相關測試通過（見 T03） |
| T04 | P0B Architecture Graph | 技術原型完成 | archgraph 測試與 ruff 通過（見 T04） |
| T05 | BUG-RESUME-001 | 修復驗收通過 | 相關測試通過（見 T05） |
| T06 | P0B A/B/C 比較材料 | 完成 | 凍結檢查與材料檢查通過（見 T06） |
| T07 | 評分規格與材料凍結 | 完成（已核准結案） | 測試與凍結檢查通過（見 T07） |
| T08 | 內部驗證（已由使用者調整為單人驗證） | T08-A 單人實際操作：首次操作已完成；T08-UI-001、T08-UI-002 修正已完成，**待使用者最後驗收**。T08-B：**完成** | T08-UI-002 後一致性檢查 357/357 通過（含既有 285 項）；archgraph pytest 175 passed；ruff 通過；freeze v1.2 PASSED |
| T09 | 正式新手比較研究 | 暫緩（非 P1 技術研發的必要前置） | 不適用 |
| T10 | Adaptive Workflow 效率實驗 | 未開始（允許離線實驗設計；不執行真實模型 Benchmark，不開發正式 Router） | 不適用 |
| T11 | P1 正式產品整合 | NO-GO | 不適用 |

## Git 與工作區狀態【本次查證】

- 分支 `main`，HEAD `fd73f98 跨provider的ui介面`。
- 本文件撰寫時**沒有任何 commit、push、reset 或 release**。
- 未提交的修改（必須保留，不得清除）：
  - `src/ccc_mcp/core/tasks.py`：BUG-RESUME-001（T05）。
  - `src/ccc_mcp/infra/telemetry.py`：P0A（T02、T03）。
  - `prototypes/`：P0B 原型與研究材料（T04、T06、T07）。
  - `tests/fixtures/`、`tests/test_measurement_fake_cli.py`、`tests/test_model_identity.py`、`tests/test_resume_orphan_grace.py`、`tests/test_usage_evidence.py`、`tests/test_usage_normalization.py`、`tests/test_usage_raw_verification.py`。
- `git diff --stat`（`tasks.py` 與 `telemetry.py`）：2 個檔案，+547 / −47，與本次工作前一致。

## 任務明細

### T01 雙核心產品定位與 PRD — 完成

- **完成範圍**：【歷史報告】PRD 已完成。
- **限制**：本次未開啟或核對 PRD 原文。引用產品定位時，請以 PRD 原文為準，本文件不重述其內容。
- **本次查證**：無。

### T02 P0A Token 正規化 — 離線版完成

- **完成範圍**：【歷史報告】離線版完成。相關檔案位於未提交的 `src/ccc_mcp/infra/telemetry.py` 與 `tests/test_usage_*.py`、`tests/test_model_identity.py`、`tests/test_measurement_fake_cli.py`、`tests/fixtures/`。
- **限制**
  - 離線版：未呼叫真實 Codex 或 Claude 模型，沒有真實 token 量測結果。
  - 尚未提交，`telemetry.py` 相對 HEAD 有大量差異（歷史報告為 +499 / −34）。
  - 「對應到 T02 或 T03」是依檔名推斷，本次未逐一核對每個測試與任務的對應。
- **本次查證**：執行 `tests/test_usage_evidence.py`、`test_usage_normalization.py`、`test_usage_raw_verification.py`、`test_model_identity.py`、`test_measurement_fake_cli.py`、`test_resume_orphan_grace.py`，共 73 項，全部通過。全套 `pytest tests`：**985 passed、1 skipped、5 deselected**，exit code 0（約 8 分 16 秒）。

### T03 P0A Evidence Chain — 第一版完成

- **完成範圍**：【歷史報告】第一版完成，對應同一組未提交的 P0A 檔案。
- **限制**
  - 第一版，不是正式產品功能。
  - 尚未提交。
  - 不得刪除既有 ACQE 證據（本任務的約束）。
- **本次查證**：同 T02 的 73 項測試，全部通過。

### T04 P0B Architecture Graph — 技術原型完成

- **完成範圍**：`prototypes/archgraph/`。包含 `gate.py`（核准條件）、`diagram.py`（頁面）、`pipeline.py`（管線）與 `userstudy/`（研究材料）。
- **限制**
  - 只是技術原型，未產品化（見 T11 NO-GO）。
  - 新手視圖的功能卡仍把「尚未 commit」與「未確認」放在同一欄（`views.py`，未修改）。
  - 「超出計畫」阻擋規則會讓已 commit 的任務也被擋（例如 TASK-0001 因 `util.py` 被阻擋），此產品決定尚未核准。
  - 功能層級的歷程未實作，只到檔案層級。
- **本次查證**：`prototypes/archgraph` 的測試為 163 項全部通過；`ruff check` 通過。

### T05 BUG-RESUME-001 — 修復驗收通過

- **完成範圍**：`src/ccc_mcp/core/tasks.py`（`ORPHAN_GRACE_SECONDS = 5`、`worker_spawned_at` 紀錄、`recover_workers` 的寬限判斷）與 `tests/test_resume_orphan_grace.py`（10 項）。`worker.py` 未修改。
- **歷史報告**：重現步驟中，HEAD 在真實 spawn 路徑下失敗，修正後通過；當時全套測試 985 項通過。
- **限制**
  - `worker.py` 在忙碌鎖下靜默返回的風險已依機制確認，但未在 30 次真實執行中觀察到，未修改。
  - grace 依牆鐘時間（wall clock）判斷，測試以本機時鐘注入。
  - Windows 的 spawn 延遲未量測。
  - RUNNING 狀態的程序崩潰未自動偵測。
- **本次查證**：`tests/test_resume_orphan_grace.py` 通過（見 T02 的 73 項）。

### T06 P0B A/B/C 比較材料 — 完成

- **完成範圍**：`prototypes/archgraph/userstudy/materials/`，三個同構案例（notes、bookmarks、todos）各含 A（ccc-mcp 視圖）、B（文字基線）、C（靜態圖）、事實表、答案與題目。
- **限制**
  - B 是 **Claude 根據完整事實表撰寫的固定文字基線**，不是獨立 AI 摘要工具的輸出，也不是人類撰寫。
  - A 頁面的內容可以直接辨識，沒有盲性。
  - bookmarks 與 todos 已列入 pilot-v1.2 的測試者套件，因此已曝光，正式研究不能直接沿用。
  - 未執行任何 AI 摘要（`AI_SUMMARY_RECORD.md` 為空）。
  - 沒有任何人類測試資料。
- **本次查證**：`parity_check`、`key_check`、`fairness_check` 通過（`tests/test_p0b_presentation.py` 內含）；`pilot-v1.2` 的凍結檢查 PASSED（涵蓋 `materials/`）。

### T07 評分規格與材料凍結 — 完成（已核准結案）

- **完成範圍**
  - 評分器 `score.py`（pilot-v1.2）：Q10 三個子分數全部正確才算整題正確；「不確定」得 0 分，不是明確錯誤主張，棄答率獨立計算；Q10 錯誤理解率 = 錯誤子分數數量 ÷ 全部應答子分數數量（留白與無效答案計為錯誤）；危險誤判同時報告全體比例、明確決策比例與棄答率。
  - 完成檢查 `completion.py`：三個區塊的作答表、有效時間（HH:MM:SS，結束不早於開始）、求助次數、必要欄位與紀錄表齊全；個別留白允許，但不得得分。
  - 彙整 `aggregate.py`：每位測試者×區塊的指標表，依條件做描述性摘要。
  - B 來源描述修正為「Claude 根據完整事實表撰寫的固定文字基線」（三個案例）。
  - 版本：`pilot-v1`（歷史，未交付）、`pilot-v1.1`（已取代，未交付）、`pilot-v1.2`（現行，凍結檢查通過）。`pilot-v1.2` 的測試者套件與 `pilot-v1.1` 逐位元組相同，變動只在評分與彙整規則及說明文件。
- **限制**
  - 「不確定」的計分解讀、危險誤判三種比例的分母，都是依你的指定規則實作；完成定義中的「必要紀錄」以紀錄表存在且表頭未改為準（列數可為 0）。
  - 所有驗證使用的作答表皆為**工具資料**（由標準答案產生於測試暫存資料夾），不是測試者資料。
  - `pilot-v1` 的凍結清單中，`score.py`、`build_pilot.py`、`freeze.py` 與三份 `B_text.md` 已與現況不同（刻意的版本變更），原件保存於 `prototypes/archgraph/userstudy/pilot/archive/pilot-v1/`，以 v1 清單雜湊驗證通過。`pilot-v1.1` 的腳本同樣保存於 `pilot/archive/pilot-v1.1/scripts/`。
- **本次查證**
  - `prototypes/archgraph` 測試 163 項通過。
  - `ruff check` 通過。
  - `freeze.py check`（`pilot-v1.2`）PASSED。
  - 測試者套件中不含答案、評分規則、`rater_only` 內容（文字掃描與 zip 掃描均無洩漏）。

### T08 內部驗證 — 已調整為單人驗證（2026-11-14）

使用者說明：目前是個人開發專案，只有作者本人能進行實際操作測試。不再招募兩位非作者，外部受試者不作為繼續開發的阻擋條件。

**T08-A 單人實際操作**（待作者本人操作）
- 指引：`docs/development/T08-SOLO-OPERATION.md`（四項檢查：TASK-0001 核准條件、TASK-0003 三狀態分開、待確認對應可追溯、計畫與實際區分）。
- 圖的產生方式：`build_cases.py <資料夾>`，輸出在 ccc-mcp 之外。
- **Claude 不代填結果，也不宣稱測試已完成。**
- 狀態：未開始（等待作者操作與回報）。

**T08-B 自動化技術驗證**（完成）
- 工具：`prototypes/archgraph/userstudy/verify_diagrams.py`。檢查三個架構圖（新手分頁、計畫分頁、工程視圖 `engineer.json`）與 Git Diff、TaskDelta、PlannedGraph、ActualGraph、歷程、主機測試重跑的一致性，外加 `verify_keys` 的獨立 Git 與 import 核對。
- 結果：三個案例、三個 Task，**285/285 項通過**（報告：`docs/development/evidence/T08-B-2026-11-14/verify_diagrams_report.json`）。
- 驗證器自身的負向測試：刻意破壞一份架構圖後，驗證器能抓到（閘門狀態與計畫分頁節點各一項失敗）。
- 限制：這是機器一致性檢查，**不是人類使用者研究，也不是 AI 審查結果的代替品**。檢查的是圖與證據是否一致，不是圖是否容易理解。
- 過程中的修正：驗證器第一版的計畫分頁檢查只比對計畫檔案，漏掉計畫依賴的端點（plan 的 `planned_dependencies`）；已修正為「計畫檔案 ∪ 計畫依賴端點」。另一項歷程檢查假設最後一步一定是工作區，已修正為依報告的 Task 狀態判斷。兩項修正都依據計畫檔案內容與圖的設計，不是為了讓結果通過。
- 發現（需要你決定，不屬於本次修改）：plan1 與 plan3 的計畫依賴指向 `notes/util.py`，但該檔案未列入計畫檔案，因此 TASK-0001 因超出計畫而 BLOCKED。這是計畫內容本身的問題，或是閘門規則的問題，需由你判斷。

**T08-UI-001 新手架構圖修正（2026-11-14，依使用者首次畫面操作）**

- 範圍：只修改 `prototypes/archgraph/` 的新手呈現層。Graph Engine、Identity、TaskDelta、核准閘門（gate）、P0A、P0B 的研究材料與 BUG-RESUME-001 皆未修改。
- 修正項目與對應：
  1. 新手分頁改為**功能架構**為主視覺：每個核准功能一個節點，標示新增、修改、間接受影響、已移除、需確認、未變動；原始檔案與符號移到右側面板的「程式細節（展開）」。
  2. 頁首改為六張狀態卡片（Git、計畫比對、主機測試、本 Task 核准判定、需注意、目前狀態），取代會換行過長的標籤列。
  3. 新手視圖使用自然語言標籤；原始技術狀態（commit_state、structure_state、host_test、gate.status）移到工程視圖「原始技術狀態」表與 JSON。
  4. 核准判定明確標示為**本 Task**，並說明原因（例如「超出計畫 1 個檔案」）。前序 Task 未解事項（TASK-0001 的 `notes/util.py`）在 TASK-0002 的判定旁以「另有前序未解事項」標示，且展開區列出。閘門未放寬。
  5. 待確認對應的連結仍然存在，點擊會定位到對應的功能節點（storage.py → 本機儲存（SQLite）、migrate.py → 匯入舊 JSON、schema.sql → 未歸屬功能的節點）。
- 驗收結果（本次查證）：
  - TASK-0001：新手分頁直接顯示 4 個新增功能與超出計畫的未歸屬檔案節點，沒有隱藏。
  - TASK-0002：新增 1、修改 3、未變動 1，以符號與文字區分。
  - TASK-0003：功能卡片與「演進：本機儲存（JSON 檔） → 本機儲存（SQLite）」同時顯示，並標示「原名」。
  - 三個狀態分開顯示；Planned 與 Actual 分屬不同分頁，並以標籤區分。
  - 瀏覽器實測（本機 HTTP）：1440、1280、1024、768 px 皆無水平溢出（scrollWidth = clientWidth）；點擊三條待確認連結定位正確；控制台無頁面錯誤。
- 限制與需要你判斷的事項：
  - 功能之間的箭頭由檔案層級的依賴推導。`notes/cli.py` 同時含三個功能的入口，因此功能箭頭可能高估依賴；檔案層級證據列於展開區。
  - `notes/models.py` 因為是「標籤功能」計畫成員而歸屬到標籤功能，但該檔案實際是 Note 資料結構。這是計畫內容的問題，不是畫面的問題，需要你決定是否修正計畫。
  - `notes/util.py` 在計畫依賴中被指向、卻不在計畫檔案中，因此 TASK-0001 被阻擋（已在 T08-B 記錄）。原型沒有接受機制，仍需你決定。
  - 結構檢查不等於新手能理解。圖是否容易讀，仍需要你本人再次查看畫面判斷。
- 證據：`docs/development/evidence/T08-UI-001-2026-11-14/`，包含修改前後截圖（`screenshots/`）、archgraph before/after diff、`verify_diagrams` 報告與輸出、pytest、ruff、freeze v1.2 檢查、git 狀態與 tracked 檔案的 diff stat。

**T08-UI-002 最終語意與響應式修正（2026-11-14，依使用者上傳的新版 HTML 與獨立瀏覽器檢查）**

- 範圍：只修改 `prototypes/archgraph/` 的呈現層與檢查腳本。Graph Engine、Identity、TaskDelta、核准閘門、P0A、BUG-RESUME-001 與 Adaptive Router 皆未修改。
- 修正項目與結果：
  1. **狀態不再混用。** 功能節點的 `unconfirmed` 欄位已拆為三個獨立欄位：`git`（程式碼是否已 commit）、`scope`（超出計畫、未規劃的程式變更）、`identity_pending`（待確認對應的組數）。驗證（主機測試）是整個 Task 的結果，只留在頁首，不屬於單一功能。新手視圖與展開區都不再出現「未確認：尚未 commit」。`build_materials.py` 的 `a_uncommitted_change` 改讀 `git`，判定結果相同。
  2. **箭頭的證據語意。** 只有當 import 的兩端檔案各只屬於一個功能時，才是**直接證據**（實線）。若任一端檔案屬於多個功能（例如 `notes/cli.py` 同時含三個功能的入口），該關係只是**推測性功能關係**（虛線），頁面明確寫明「不代表已確認的功能依賴」。計畫外的直接依賴以紅色實線表示。展開區逐條列出依據的檔案 import。
  3. **768px 與 1024px 優先顯示功能架構。** 窄螢幕（<1100px）下，圖在最前，待確認與超出計畫的警示在圖後，節點詳情在最後。1440px 維持詳情在右側。
- 驗收結果（本次查證）：
  - 三個 Task 已重新產生；頁面中不再出現「未確認」字樣。
  - 瀏覽器實測 768px：圖在警示與詳情之前；`scrollWidth = clientWidth`，無水平溢出。
  - 瀏覽器實測 1024px：圖在最前，詳情在圖後，無水平溢出。
  - 瀏覽器實測 1440px：詳情在右側，無水平溢出。
  - 三條待確認連結仍正確定位：`storage.py` → 本機儲存（SQLite）、`migrate.py` → 匯入舊 JSON、`schema.sql` → 未歸屬功能的節點。
  - 既有 285 項與新增檢查共 357 項全部通過；archgraph pytest 175 passed；ruff 通過；freeze v1.2 PASSED。
- 限制與需要你判斷的事項：
  - 推測性功能關係在 TASK-0003 中佔多數，因為許多功能共用 `cli.py` 等檔案。這是規則的忠實結果，但會讓圖看起來偏虛。是否要改為只顯示直接證據、推測關係收在展開區，請你決定。
  - 功能層級的依賴仍只由檔案 import 推導，沒有符號層級的呼叫證據。
  - `pilot-v1.2` 的研究材料（條件 A）建立於 UI-001 之前，已不反映目前的渲染。本次未重新產生研究材料，也未開啟新的研究版本。
  - 這些是結構與一致性檢查，不等於真人新手研究結論。
- 證據：`docs/development/evidence/T08-UI-002-2026-11-14/`，包含修改前（UI-001）與修改後截圖（1440、1024、768 px）、archgraph diff、驗證報告與輸出、pytest、ruff、freeze 檢查與 git 狀態。

**證據（本次查證）**
- `docs/development/evidence/T08-B-2026-11-14/`：驗證報告、archgraph pytest 輸出（164 passed，exit 0）、ruff 輸出（通過）、git status、git diff stat、tracked 檔案的 diff 副本（僅作留存，未 commit）。

### T09 正式新手比較研究 — 暫緩

- 使用者決定暫緩；非 P1 技術研發的必要前置條件。
- 不宣稱一般新手已驗證，也不宣稱架構圖優於 AI 摘要。

### T10 Adaptive Workflow 效率實驗 — 未開始

- 允許獨立進行離線實驗設計。
- 不執行真實模型 Benchmark，不開發正式 Router。

### T11 P1 正式產品整合 — NO-GO

- 不得開始正式產品整合。P0B 架構圖仍是技術原型，見 T04 的限制。

## 歷史紀錄（已被取代）

- 2026-11-14 前段：T08 原為「兩位非作者內部預試，待交付（阻擋）」，阻擋原因為沒有 P1、P2 與獨立評分者的收件對象。依使用者最新指示，已改為單人驗證（T08-A、T08-B），此阻擋不再適用。
- `pilot/pilot-v1.2/`（測試者套件與交付包）保留為歷史材料，未交付。

## 約束（持續有效）

- 不 commit、push、reset、release。
- 不修改 P0A、P0B、BUG-RESUME-001 的未提交修改。
- 不修改正式 README。
- 不修改 Adaptive Router。
- 不虛構測試者、作答資料或測試結果。
