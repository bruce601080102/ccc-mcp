# SEC-GIT-001 — Git 歷史安全整理：規劃與操作預演

狀態：**規劃與預演，未執行任何歷史改寫。** 本文件只描述方案、風險、驗收條件與需要授權的操作。
日期：2026-11-15

## 0. 目前狀態（查證時點）

| 項目 | 狀態 |
|---|---|
| 本機 `main` | `f7f25c5 feat(archgraph): add versioned task architecture graph prototype`，父提交 `fd73f98` |
| `origin/main`（遠端即時查詢） | `fd73f98`；沒有任何遠端分支包含 `f7f25c5` |
| 本機領先遠端 | 1 個提交（`f7f25c5`） |
| 工作區 | `M` 4 個檔案（`docs/development/VNEXT-TASK-STATUS.md`、T08 語意修正的三個 archgraph 檔案）；未追蹤 `docs/development/evidence/T08-semantic-final-2026-11-14/` |
| 新提交的內容 | 303 個檔案（300 新增、3 修改）；含原型、文件、證據、研究答案鍵、P0A 與 BUG-RESUME-001 |

## 1. 必須保留（不得刪除、不得移動出版本控制以外的位置）

1. **HEAD `f7f25c5` 與 `fd73f98`**：兩者都必須可由 reflog、備份分支與 bundle 找回。
2. **所有已追蹤的工作區修改**（4 個檔案）與**未追蹤檔案**（evidence 目錄）。
3. **T08 語意修正**：`prototypes/archgraph/archgraph/diagram.py`、`prototypes/archgraph/tests/test_novice_feature_view.py`、`prototypes/archgraph/userstudy/verify_diagrams.py`。
4. **研究證據**：`docs/development/evidence/**`（驗證報告、pytest/ruff/freeze 輸出、截圖、diff）。
5. **研究答案與評分資料**：不刪除，只能移至私有保存區（見第 4 節）。
6. **P0A 與 BUG-RESUME-001 的全部內容**：`src/ccc_mcp/infra/telemetry.py`、`src/ccc_mcp/core/tasks.py`，以及對應測試。本計畫不修改其內容。

## 2. 疑似金鑰的風險（不展示完整內容，不外部驗證）

- 位置：`tests/test_usage_evidence.py`，測試名稱 `test_suspect_event_is_withheld_not_stored`，約第 142 行。
- 形式：一個以 `sk-live-` 為前綴的字串，出現在測試輸入的 `modelUsage` 鍵名位置。
- 判讀依據（只依格式與結構，未驗證真偽）：
  - 測試名稱顯示它是用來驗證「可疑內容被扣留、不被儲存」的輸入，因此**很可能是合成的測試資料**。
  - 字元多樣性高於其他夾具（熵約 4.1 bit），無法僅由格式排除真實金鑰的可能。
- 本計畫**不**將它送到任何外部服務驗證，也不在報告中輸出完整內容。
- 作者需要做的判斷：
  1. 在本機確認該字串是否來自真實帳號。
  2. 若為真實金鑰：由作者在供應商後台撤銷並更換；這是作者的外部操作，我們不代為執行。
  3. 若為合成值：維持測試，但建議改為明確的佔位格式（例如 `sk-live-TEST-PLACEHOLDER`），以免後續掃描誤報。這項修改會動到 P0A 測試檔，**需要你明確授權**。
- 注意：該檔案由 P0A 範圍管理，本計畫在未授權前不修改它。

## 3. 乾淨工作區的建立方式

**原則**：不在原儲存庫內部重寫歷史。改為在儲存庫外建立獨立的乾淨工作區，從 `fd73f98` 重新組裝。

步驟（全部在儲存庫外執行，不改變原儲存庫的 refs、index 或工作樹）：

1. **基底**：`git archive fd73f98 | tar -x -C ~/ccc-clean-base`。這是唯讀操作，只寫入 tar 的輸出位置。
2. **新倉庫**：在 `~/ccc-clean` 執行 `git init`，並把基底複製進去。新倉庫與原倉庫完全獨立。
3. **覆蓋**：依第 4 節的分類，從原倉庫 `git archive f7f25c5 -- <路徑>` 取出需保留的檔案，或用 `git show f7f25c5:<路徑>` 寫出單檔。
4. **分批提交**：只在 `~/ccc-clean` 中提交（見第 5 節的提交序列）。作者資訊使用本機設定，不加入任何 AI 署名行。
5. **驗證**：在 `~/ccc-clean` 中逐批執行測試與掃描（第 7 節）。
6. **回寫原倉庫**：這一步**會改變 refs**，必須經你授權（見第 8 節）。

## 4. 檔案分類：保留、排除、移至私有保存區

| 類別 | 路徑（範例或數量） | 處理 | 理由 |
|---|---|---|---|
| 核心程式 | `src/ccc_mcp/**`（除 telemetry、tasks 外） | 保留（來自 `fd73f98`，不變） | 基底 |
| P0A | `src/ccc_mcp/infra/telemetry.py`；`tests/test_usage_*.py`（3）、`tests/test_model_identity.py`、`tests/test_measurement_fake_cli.py` | 保留（內容不變） | P0A 範圍，不修改 |
| BUG-RESUME-001 | `src/ccc_mcp/core/tasks.py`；`tests/test_resume_orphan_grace.py` | 保留（內容不變） | BUG 範圍，不修改 |
| P0A 夾具 | `tests/fixtures/fake_cli/**`、`tests/fixtures/native_cli/**`（除下列原始擷取） | 保留 | 測試依賴 |
| 原始擷取 | `tests/fixtures/native_cli/codex_clamp_rawcapture_2026-11-10.jsonl` 與其 `.expected.json` | **暫留，待審查**；審查後二選一：去識別化重建，或移至私有區並讓對應測試依條件跳過 | 名稱指向真實擷取；欄位名稱掃描未見身分欄位，但值未逐一檢視。測試依賴它，移除前需要授權 |
| 研究答案鍵 | `prototypes/archgraph/userstudy/materials/*/keys.json`（3） | **移至私有區** | 公開會使研究失效 |
| rater-only 材料 | `userstudy/pilot/rater_only/**`（6）、`pilot/pilot-v1.1/rater_only/**`（6）、`pilot/pilot-v1.2/rater_only/**`（6） | **移至私有區** | 只能給獨立評分者；包含答案與指派表 |
| 凍結清單與封包清單 | `pilot/**/FREEZE_MANIFEST.json`、`pilot/**/delivery/DELIVERY_MANIFEST.json` | 保留，但**需要重新產生公開版清單**（見第 6 節） | 清單的雜湊涵蓋上述私有檔案，移除後會失效 |
| npm 符號連結 | `npm/bin/codex`（mode 120000，指向本機 npm 安裝路徑） | **排除** | 環境產物，不屬於專案；需同時還原 `.gitignore` 的 `npm` 規則 |
| `.gitignore` | 目前新增大量規則，但移除了 `npm` 一行 | **修正**：還原 `npm` 規則，並加入研究答案與 rater-only 的忽略規則 | 避免日後再次誤提交 |
| 截圖 | `docs/development/evidence/**/screenshots/*.png`（共 26 個） | 保留，**提交前需人工檢視** | 只含合成的 notes-cli 畫面；已確認無路徑文字，但未逐張檢視 |
| 證據文字檔 | `docs/development/evidence/**`（JSON、log、diff） | 保留 | 掃描未見 `/Users/`、`/private/tmp`、`claude-501`、`scratchpad` |
| 研究文件 | `prototypes/archgraph/userstudy/*.md`、`pilot_docs/` | 保留，但需修正引用到已移除檔案的路徑 | 文件與私有檔案的連結需更新 |
| 備份產物 | bundle、未提交快照、tar、清單 | **只放在私有區，絕不放入儲存庫** | 含完整歷史與答案 |
| 本機產物 | `.venv/`、`__pycache__/`、`.pytest_cache/`、`.claude/worktrees/` | 排除（已由 .gitignore 或本機環境管理） | 不屬於專案 |

**私有保存區**：`~/ccc-private-archive/SEC-GIT-001/`，權限 `700`，不放在儲存庫內。建議放入加密磁碟映像。內容包括：
- 答案鍵與 rater-only 檔案的原始副本與 SHA-256 清單。
- 原始擷取檔的副本。
- 完整 bundle、未提交快照（`git diff --binary`）、未追蹤清單、`git status --porcelain=v2`、`git for-each-ref`、`git reflog`。
- 原始 `.git/index` 副本。
- npm 符號連結的記錄（路徑字串，不含內容）。

## 5. 拆分提交序列與依賴風險

建議順序（每一批都必須可單獨測試）：

| 批次 | 內容 | 依賴 | 主要風險 |
|---|---|---|---|
| C0 | `.gitignore` 修正（還原 npm、加入私有規則） | 無 | 低；最先提交，防止後續誤加 |
| C1 | P0A：`telemetry.py`、P0A 測試與夾具 | 基底的 `src/ccc_mcp/adapters/**`、`worker.py` 已依賴 telemetry 的既有符號 | 低；diff 按名稱統計只有新增符號，未刪除既有符號 |
| C2 | BUG-RESUME-001：`tasks.py`、`test_resume_orphan_grace.py` | 無 telemetry 依賴（已確認） | 低；新增 `ORPHAN_GRACE_SECONDS`、`_utc_now`、`_worker_start_base` |
| C3 | Architecture Graph 原型（`prototypes/archgraph/archgraph/**`、`tests/**`、`fixtures/notes_cli/**`） | 不 import `ccc_mcp`（已確認） | 中；原型內的 `build_cases.py` 以倉庫根為參照，需確認只在倉庫外建立輸出 |
| C4 | 研究材料（`userstudy/**` 去除私有檔案） | 依賴 C3；**多個腳本與測試讀取答案鍵** | **高**：`verify_keys`、`key_check`、`parity_check`、`fairness_check`、`freeze`、`build_materials`，以及 `test_p0b_presentation`、`test_p0b_pilot_pack`、`test_p0b_pilot_v11` 都會因找不到答案鍵而失敗 |
| C5 | 文件與證據（`docs/development/**` 去除私有檔案） | 依賴 C3、C4 的檔案名稱 | 中；文件內的路徑與連結需更新 |

**C4 的兩個可行做法**（需要你選擇）：
- (a) 答案鍵路徑改由環境變數指定，指向私有區；未設定時，依賴答案鍵的測試明確跳過並顯示原因。這需要修改研究腳本與測試，但不動 P0A、BUG、Graph Engine 或 Router。
- (b) 公開倉庫只保留研究腳本與不含答案的材料，答案與評分資料完全由私有區供應。評分流程需要以私有區的檔案執行。

**freeze 的處理**：凍結清單的雜湊涵蓋 rater-only 與答案鍵。移除後必須產生公開版清單，並在私有區保存原清單。公開版清單是新版本，不得覆寫 `pilot-v1.2` 的原清單。

**歷史的特點**：`fd73f98` 的樹中沒有任何 `keys.json` 或 `rater_only` 檔案（已查證），因此從 `fd73f98` 重建時，答案鍵**不會**進入新歷史。這是本方案成立的關鍵前提。

## 6. 研究材料的凍結版本（不重新生成）

- `pilot-v1.2` 的測試者封包與資料不重新生成。
- 只新增公開版的凍結清單，記錄「移除私有檔案後」的狀態，並註明它與原清單的差異。
- 原清單與私有檔案保存於私有區，雜湊可由私有區驗證。

## 7. 驗收條件

在乾淨工作區（`~/ccc-clean`）中，全部條件都必須成立才算完成：

1. **安全掃描**
   - 模式掃描與熵檢查（沿用 T08 的掃描規則）對最終樹與所有歷史提交皆無未處理的命中。
   - `git log --all -p` 對答案鍵、rater-only、npm 符號連結的路徑皆無命中。
   - `sk-live-` 字串已依第 2 節處理，並有作者的書面結論。
   - 掃描結果只記錄檔案路徑與計數，不記錄內容。
2. **pytest**
   - 根目錄測試：與原 `f7f25c5` 相比，通過數不減少（先前為 985 passed、1 skipped、5 deselected）。
   - archgraph：176 passed 以上，或依第 5 節的做法（a）明確列出跳過的測試與原因。
   - 每個批次（C1–C5）各自執行對應測試，結果記錄在 evidence。
3. **Ruff**：`ruff check` 對 `src`、`tests`、`prototypes/archgraph` 全部通過。
4. **Freeze**：公開版清單的 `freeze.py check` 通過；原清單在私有區驗證通過。
5. **Git Diff**
   - `git diff --stat fd73f98..HEAD`（在乾淨工作區）的檔案集合，等於原 `f7f25c5` 的檔案集合，扣除第 4 節列為排除或私有的檔案，並加上 `.gitignore` 的修改。
   - 逐檔比對：每個保留的檔案，其 SHA-256 與 `f7f25c5` 中的版本相同，除了已明確列出的例外。
6. **可還原性**：從備份 bundle 還原的副本，HEAD 為 `f7f25c5`，且工作樹乾淨。

## 8. 會修改 Git refs、index、工作樹或歷史的操作

| 類別 | 操作（範例） | 影響 | 本計畫的狀態 |
|---|---|---|---|
| **唯讀** | `status`、`log`、`show`、`diff`、`ls-tree`、`cat-file`、`reflog show`、`rev-parse`、`check-ignore`、`ls-remote`（網路唯讀） | 無 | 已執行 |
| **只寫入倉庫外的檔案** | `git bundle create`、`git bundle verify`、`git archive … \| tar -x`、`git format-patch -o <外部路徑>`、`git diff --binary > <外部路徑>` | 只寫外部檔案 | 已在 scratch 預演 |
| **建立新倉庫（不影響原倉庫）** | `git init ~/ccc-clean`、`git clone <bundle> <外部路徑>` | 新倉庫，原倉庫不變 | 已在 scratch 預演還原 |
| **修改 index** | `git add`、`git rm --cached`、`git restore --staged`、`git update-index`、`git stash` | 改變暫存區 | **不執行**，除非授權 |
| **修改工作樹** | `git checkout`、`git restore`、`git switch`、`git clean`、`git reset --hard`、`git stash` | 覆寫或刪除未提交內容 | **禁止**（會直接危及未提交內容） |
| **修改 refs** | `git branch`（建立或刪除）、`git tag`、`git commit`（會移動 HEAD 與分支）、`git update-ref`、`git worktree add`、`git fetch`（更新遠端追蹤參照） | 改變分支與標籤 | **需授權** |
| **修改歷史** | `git commit --amend`、`git rebase`、`git cherry-pick`、`git reset` 到較舊的提交、`git filter-repo`、`git filter-branch`、`git replace` | 改寫提交 | **需授權**；本計畫的主要路線是建立新歷史，不改寫 `main` |
| **垃圾回收** | `git gc`、`git prune`、`git reflog expire` | 可能刪除不可達物件 | **禁止**，直到備份與還原已驗證 |
| **遠端** | `git push`、`git push --force`、`git push --delete`、`git remote set-url` | 影響遠端 | **禁止**，直到第 7 節全部通過並獲授權 |

## 9. 備份與還原方案（確保未提交內容不遺失）

**備份（在任何會改寫 refs 或工作樹的操作之前）**

1. 建立私有區目錄，並寫入備份清單。
2. `git bundle create <私有區>/ccc-mcp-all.bundle --all`，再以 `git bundle verify` 驗證。
3. `git diff --binary HEAD > <私有區>/uncommitted-tracked.patch`。
4. `git ls-files -o --exclude-standard > <私有區>/untracked-list.txt`，並把這些檔案與 4 個已修改檔案一併複製進 tar。
5. `git status --porcelain=v2`、`git for-each-ref`、`git reflog`、`.git/index` 副本。
6. 對所有備份產物計算 SHA-256，寫入 `checksums.txt`。
7. 在原倉庫建立備份分支 `backup/pre-sec-git-001` 指向 `f7f25c5`（**這會改變 refs，需授權**）。在授權前，reflog 與 bundle 即為備份。

**還原**

- 歷史還原：從 bundle `git clone` 到新目錄，確認 HEAD 為 `f7f25c5`。若 `main` 被改寫，以 `backup/pre-sec-git-001` 或 reflog 的 `f7f25c5` 還原分支（需授權）。
- 工作樹還原：從 tar 複製回原位，並以 `checksums.txt` 驗證。在驗證通過前，不覆蓋原檔案。
- 排練：在第 8 節的操作前，於 scratch 完成一次還原演練（本次已完成 bundle 的驗證與還原演練，見第 10 節）。

## 10. 本次預演結果（不改變原倉庫）

| 預演項目 | 結果 |
|---|---|
| `git bundle create --all` 寫入 scratch | 完成，60 MB |
| `git bundle verify` | exit 0 |
| 未提交快照（patch、未追蹤清單、status、refs、reflog、index 副本） | 完成，未追蹤 15 個項目 |
| `git archive fd73f98` 解開到 scratch | 完成；基底樹不含 `keys.json` 或 `rater_only` 檔案（查證為 0） |
| 從 bundle 還原至新 clone | HEAD 為 `f7f25c5`，工作樹乾淨 |
| 原倉庫狀態（預演後） | HEAD `f7f25c5`，工作區 5 項（未變） |

注意：`git status` 可能更新 index 的 stat 快取，但不改變內容。

## 11. 各角色審查（自我審查，非獨立子代理）

| 角色 | 判定 | 主要意見 |
|---|---|---|
| PM | REVISE | 需要先決定 C4 的答案鍵處理方式（a 或 b），並決定 `sk-live-` 字串的處理授權 |
| SA | REVISE | 研究材料與 ccc_mcp 之間只有文件與檔案名稱依賴；P0A 與 BUG 之間無依賴，可獨立提交；但 freeze 清單與多個測試依賴答案鍵，需同步重構的範圍需界定 |
| SD | ACCEPT（附條件） | 乾淨工作區的建立不影響原倉庫；C4 的變更需要限制在研究腳本與其測試 |
| AR | REJECT（未授權前） | 任何改寫操作都可能遺失未提交內容；`sk-live-` 字串未判定真偽前不應推送 |
| SR | REJECT（未授權前） | 私有區的存在與權限尚未建立；原始擷取檔仍待審查；截圖需人工檢視 |
| QA | ACCEPT（附條件） | 驗收條件可量化；但目前只完成還原演練，尚未完成任何乾淨重建的測試 |
| 市場分析師 | REJECT（對外公開） | 在第 7 節全部通過前，不得宣稱儲存庫已安全或可公開；答案鍵公開即使研究失效 |

## 12. 需要你明確授權的操作

1. 建立私有保存區，並把答案鍵、rater-only、原始擷取、完整備份移入該區。
2. 在 `~/ccc-clean` 進行乾淨重建與分批提交（不影響原倉庫，但會產生新的提交歷史）。
3. 在原倉庫建立 `backup/pre-sec-git-001`（改變 refs）。
4. 選擇 C4 的做法：(a) 環境變數指定答案鍵路徑，或 (b) 完全由私有區供應。
5. 處理 `tests/fixtures/native_cli/codex_clamp_rawcapture_2026-11-10.*`：去識別化重建，或移至私有區。
6. 修改 `tests/test_usage_evidence.py` 中的 `sk-live-` 佔位字串（動到 P0A 測試檔）。
7. 將乾淨重建的歷史回寫到原倉庫的 `main`（改寫歷史，需要強制更新 refs）。
8. 任何推送（需先完成第 7 節全部驗收）。

## 13. 主要風險摘要

- **未提交內容遺失**：若在沒有備份的情況下執行 `reset --hard`、`clean -fd`、`checkout` 或 `stash`，會直接刪除未提交內容。本計畫禁止這些操作，直到備份已驗證。
- **答案鍵外洩**：目前 `f7f25c5` 已在本機，若推送會公開；已推送後無法僅靠刪除檔案收回，必須改寫歷史並通知任何已取得的副本。
- **金鑰**：若 `sk-live-` 字串為真實金鑰，應由作者在供應商處撤銷。改寫歷史不能使已外流的金鑰失效。
- **研究效度**：答案鍵一旦公開，正式研究的標準答案即失效，需要重新生成研究材料（目前明確禁止）。
- **測試覆蓋下降**：若採做法 (a) 或 (b)，依賴答案鍵的測試會跳過，需明確記錄跳過的數量與原因。
- **歷史整理的範圍**：`fd73f98` 之後的整個原型與證據會集中在新提交，若要細分，需要 C0–C5 的批次驗證，工作量較大。

## 14. 後續 Task

- SEC-GIT-001 執行：待你授權第 12 節的項目後開始。
- T10：只允許離線實驗設計，不執行真實模型 Benchmark。
