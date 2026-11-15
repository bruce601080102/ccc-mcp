# notes-cli 操作示範：TASK-0001 → TASK-0003

這是 P0B 原型的操作步驟。所有指令都已實際執行並核對結果一致。示範專案 `notes-cli` 是虛構的，建立在一個暫存目錄中，不會寫入 ccc-mcp 的 Repository。

畫面只驗證**正確性與完整性**。本文件不包含新手使用者測試，也不作為使用性的結論。

## 0. 前置

```bash
cd /path/to/ccc-mcp/prototypes/archgraph
```

下面以 `PY` 代表 ccc-mcp 專案的 Python（`.venv/bin/python`）。

## 1. 建立示範 repo（只在暫存目錄中）

```bash
PY=/path/to/ccc-mcp/.venv/bin/python
W=/tmp/archgraph-walkthrough
$PY fixtures/notes_cli/make_notes_cli.py "$W/notes-cli"
```

輸出會列出 `C1`（TASK-0001 的 commit）與 `C2`（TASK-0002 的 commit）。TASK-0003 刻意不 commit，保留暫存、未暫存、未追蹤與刪除的狀態。

建立時會拒絕把示範 repo 放在 ccc-mcp 之內。

## 2. TASK-0001：從零建立

```bash
C1=$(python3 -c "import json;print(json.load(open('$W/notes-cli.refs.json'))['C1'])")
$PY -m archgraph run --repo "$W/notes-cli" --db "$W/archgraph.sqlite" --out "$W/out/TASK-0001" \
  --step empty --step "commit:$C1" \
  --plan fixtures/notes_cli/plans/plan1.json \
  --test-argv '["python","-m","pytest","-q","-p","no:cacheprovider"]'
```

預期結果：`commit_state=COMMITTED`，`structure_state=FLAGGED_OUT_OF_SCOPE`（`notes/util.py` 未規劃），`host_test_state=PASSED`。

## 3. TASK-0002：新增標籤功能（標色）

```bash
C2=$(python3 -c "import json;print(json.load(open('$W/notes-cli.refs.json'))['C2'])")
$PY -m archgraph run --repo "$W/notes-cli" --db "$W/archgraph.sqlite" --out "$W/out/TASK-0002" \
  --step "commit:$C1" --step "commit:$C2" \
  --plan fixtures/notes_cli/plans/plan2.json \
  --test-argv '["python","-m","pytest","-q","-p","no:cacheprovider"]'
```

預期結果：`commit_state=COMMITTED`，`structure_state=MATCHED`，`host_test_state=PASSED`。標籤模組為「新增」，修改的模組為「修改」，測試為「影響」（推論，非宣告）。

## 4. TASK-0003：JSON → SQLite（未 Commit，含排除與超出計畫）

```bash
$PY -m archgraph run --repo "$W/notes-cli" --db "$W/archgraph.sqlite" --out "$W/out/TASK-0003" \
  --step "commit:$C1" --step "commit:$C2" --step worktree \
  --plan fixtures/notes_cli/plans/plan3.json \
  --confirmations fixtures/notes_cli/confirmations3.json \
  --test-argv '["python","-m","pytest","-q","-p","no:cacheprovider"]'
```

預期結果：`commit_state=UNCOMMITTED`，`structure_state=BLOCKED_BY_SCOPE`（`notes/models.py`、`notes/schema.sql` 超出計畫），`host_test_state=PASSED`（於工作區副本執行）。`.env` 與 `notes/local_settings.py` 已排除，未讀取內容。

`confirmations3.json` 只確認 `JsonImporter.read` 與 `NotesStore.load_json` 的對應。`JsonImporter.__init__` 與 `NotesStore.__init__` 的對應**未確認**，因為它們位於不同的類別，故保持待確認。

## 5. 打開架構圖

每個任務輸出一份自我包含的 HTML，沒有任何外部資源：

```bash
open "$W/out/TASK-0001/architecture.html"
open "$W/out/TASK-0002/architecture.html"
open "$W/out/TASK-0003/architecture.html"
```

頁面上方的標籤可切換三個視圖，也可以用網址後綴直接開啟：

- `#planned`：規劃（Planned），依已核准計畫比對實際狀態
- `#engineer`：工程視圖，列出檔案、符號、來源行號、身分事件與依賴對帳

點擊任一節點，上方的說明區會顯示實際狀態、計畫對照、標記與待確認原因。

## 6. 逐項檢查清單

| 任務 | 應看到 | 驗證方式 |
|---|---|---|
| TASK-0001 | 全部為「新增」；`notes/util.py` 為「未規劃」 | 工程視圖的符號表 |
| TASK-0002 | 標籤模組為「新增」；cli、search、models 為「修改」；測試為「影響」 | 節點顏色、文字標記與邊框樣式 |
| TASK-0003 | JSON 儲存節點為「移除（歷史保留）」；匯入為「新增」且帶「待確認」雙框線；cli 帶「暫存≠工作區」；models 與 schema 帶「超出計畫」；`.env` 與 `local_settings.py` 為「已排除」 | 頁首標示 `commit: UNCOMMITTED`、`結構: BLOCKED_BY_SCOPE`、`Host Test: PASSED` |

## 7. 機器可讀的輸出

每個任務目錄下還有：`architecture.json`（同一份畫面資料）、`delta.json`（TaskDelta：commit、結構、host test 三個結論分開記錄）、`engineer.json`、`novice.json`、`history.json`、`snapshot.json`（只含路徑、blob 雜湊與狀態，不含內容）。

## 8. 這份示範**沒有**證明什麼

- 沒有經過新手使用者測試。畫面正確性與完整性已由測試驗證，使用性尚未驗證。
- 身分對應的閾值（0.8 與 0.5）仍是起始值，尚未以真實專案校正。
- 只支援 Python 原始碼的符號與 import 依賴。
- 工作區的 host test 在副本上執行，缺少被排除的檔案，因此依賴被排除檔案的測試會失敗。
