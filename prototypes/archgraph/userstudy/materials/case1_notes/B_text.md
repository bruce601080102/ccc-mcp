# 條件 B：文字摘要（固定文字基線）

> 來源標示：這是 **Claude 根據完整事實表（`facts.json`）撰寫的固定文字基線**。它不是獨立 AI 摘要工具的輸出，也不是人類撰寫。內容在預試與正式研究期間固定不變。若正式研究改用人工撰寫或已核准的模型輸出，必須另建版本並重新標示來源。

專案：notes-cli，共三個 Task。

## TASK-0001：建立命令列筆記工具（已 commit：dac5645）

- 主機測試：通過（在 commit dac5645 上）。
- 新增功能：新增筆記、列出筆記、搜尋筆記、本機儲存（JSON 檔）。
- 新增檔案：`notes/cli.py`、`notes/models.py`、`notes/search.py`、`notes/storage.py`、`notes/util.py`、`notes/__init__.py`、`.gitignore`、`tests/test_search.py`。
- 超出已核准計畫的檔案：`notes/util.py`。
- 待確認：無。

## TASK-0002：新增標籤功能（已 commit：bcb9d2e）

- 主機測試：通過（在 commit bcb9d2e 上）。
- 新增功能：標籤功能（新增 `notes/tags.py`、`tests/test_tags.py`）。
- 修改功能：新增筆記、列出筆記、搜尋筆記（經 `notes/cli.py` 修改）；搜尋筆記的實作 `notes/search.py` 也有修改。`notes/models.py` 有修改，屬標籤功能的計畫檔案。
- 超出已核准計畫的檔案：無。
- 待確認：無。

## TASK-0003：JSON 儲存改為 SQLite，保留舊 JSON 匯入（尚未 commit，工作區）

- 主機測試：通過，但只在工作區副本上執行；此 Task 的變更尚未 commit。
- 新增檔案：`notes/migrate.py`、`notes/sqlite_store.py`、`notes/schema.sql`、`tests/test_migrate.py`。
- 刪除檔案：`notes/storage.py`（原 JSON 儲存）。
- 修改檔案：`notes/cli.py`、`notes/models.py`。
- 功能變化：新增「匯入舊 JSON」；「本機儲存（SQLite）」由「本機儲存（JSON 檔）」改名修改而來（不是新增）；新增筆記、列出筆記、搜尋筆記、標籤功能有修改。
- 超出已核准計畫的檔案：`notes/models.py`、`notes/schema.sql`。
- 待確認：1 組新舊對應尚未人工確認，即 `notes/storage.py::NotesStore.__init__` 與 `notes/migrate.py::JsonImporter.__init__`。
- 排除：`.env`（敏感檔名）、`notes/local_settings.py`（疑似祕密）；兩者都未讀取內容。

## 依賴關係（有程式碼證據）

- `notes/cli.py` 依賴 `notes/search.py`。
- `tests/test_search.py` 依賴 `notes/search.py`。

## 結論

- TASK-0003 的變更全部尚未 commit。
- 主機測試通過，但只代表工作區副本通過；它不代表已 commit 或已驗收。
- 有超出計畫的檔案，也有 1 組待確認對應，因此 TASK-0003 不應直接視為完成並合併。
