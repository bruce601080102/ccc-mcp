# 條件 B：文字摘要（手寫基線）

> 來源標示：這是**手寫基線**，撰寫者為 Claude，依 `facts.json` 撰寫；不是 AI 摘要工具（模型 API）的輸出，也不是人類撰寫。正式比較前，應改為人工撰寫，或改為已核准的模型輸出並重新標示來源。

專案：bookmarks-cli，共三個 Task。

## TASK-0001：建立命令列書籤工具（已 commit：dac5645）

- 主機測試：通過（在 commit dac5645 上）。
- 新增功能：新增書籤、列出書籤、搜尋書籤、本機儲存（JSON 檔）。
- 新增檔案：`bookmarks/cli.py`、`bookmarks/models.py`、`bookmarks/search.py`、`bookmarks/storage.py`、`bookmarks/util.py`、`bookmarks/__init__.py`、`.gitignore`、`tests/test_search.py`。
- 超出已核准計畫的檔案：`bookmarks/util.py`。
- 待確認：無。

## TASK-0002：新增標籤功能（已 commit：bcb9d2e）

- 主機測試：通過（在 commit bcb9d2e 上）。
- 新增功能：標籤功能（新增 `bookmarks/tags.py`、`tests/test_tags.py`）。
- 修改功能：新增書籤、列出書籤、搜尋書籤（經 `bookmarks/cli.py` 修改）；搜尋書籤的實作 `bookmarks/search.py` 也有修改。`bookmarks/models.py` 有修改，屬標籤功能的計畫檔案。
- 超出已核准計畫的檔案：無。
- 待確認：無。

## TASK-0003：JSON 儲存改為 SQLite，保留舊 JSON 匯入（尚未 commit，工作區）

- 主機測試：通過，但只在工作區副本上執行；此 Task 的變更尚未 commit。
- 新增檔案：`bookmarks/migrate.py`、`bookmarks/sqlite_store.py`、`bookmarks/schema.sql`、`tests/test_migrate.py`。
- 刪除檔案：`bookmarks/storage.py`（原 JSON 儲存）。
- 修改檔案：`bookmarks/cli.py`、`bookmarks/models.py`。
- 功能變化：新增「匯入舊 JSON」；「本機儲存（SQLite）」由「本機儲存（JSON 檔）」改名修改而來（不是新增）；新增書籤、列出書籤、搜尋書籤、標籤功能有修改。
- 超出已核准計畫的檔案：`bookmarks/models.py`、`bookmarks/schema.sql`。
- 待確認：1 組新舊對應尚未人工確認，即 `bookmarks/storage.py::BookmarkStore.__init__` 與 `bookmarks/migrate.py::JsonImporter.__init__`。
- 排除：`.env`（敏感檔名）、`bookmarks/local_settings.py`（疑似祕密）；兩者都未讀取內容。

## 依賴關係（有程式碼證據）

- `bookmarks/cli.py` 依賴 `bookmarks/search.py`。
- `tests/test_search.py` 依賴 `bookmarks/search.py`。

## 結論

- TASK-0003 的變更全部尚未 commit。
- 主機測試通過，但只代表工作區副本通過；它不代表已 commit 或已驗收。
- 有超出計畫的檔案，也有 1 組待確認對應，因此 TASK-0003 不應直接視為完成並合併。
