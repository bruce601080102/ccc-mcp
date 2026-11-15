# P0B 使用者測試材料（userstudy）

這個資料夾是 P0B 公平比較的**材料與檢查工具**。目前沒有招募受試者，也沒有執行任何真實模型呼叫。

完整方案見 [STUDY_PROTOCOL.md](STUDY_PROTOCOL.md)。

## 檔案

| 檔案 | 用途 |
|---|---|
| `build_cases.py` | 用同一生成器建立三個同構案例（以 pipeline 產生），輸出到指定的暫存目錄 |
| `build_materials.py` | 從 pipeline 輸出產生 `facts.json`、`keys.json`、C 組靜態圖，並複製 A 組視圖 |
| `build_questions.py` | 產生給受試者的 `questions.md`（不含答案） |
| `parity_check.py` | 檢查每條標準答案的證據是否存在於 A、B、C 三組材料中 |
| `key_check.py` | 檢查標準答案彼此之間、以及與事實表是否一致（內部一致性，不取代人工預試） |
| `verify_keys.py` | 以 git、已核准計畫、主機測試重跑與 import 證據獨立核對標準答案 |
| `fairness_check.py` | 可找到性、只替換領域詞、領域詞污染檢查 |
| `build_pilot.py` | 產生 `pilot/tester_packet/`（發給測試者）與 `pilot/rater_only/`（僅評分者） |
| `score.py` | 依標準答案評分一份作答表 |
| `pilot_docs/` | 操作說明、計時與錯誤紀錄表、回報格式、評分者說明 |
| `AI_SUMMARY_*` | 正式研究用 AI 摘要的提示詞、產生方式與紀錄（尚未執行） |
| `materials/<case>/` | 每個案例的材料與標準答案 |

## 重建

以下指令假設在 ccc-mcp 專案根目錄的 `.venv` 中執行，暫存目錄必須在 ccc-mcp 之外。

```bash
cd prototypes/archgraph/userstudy
PY=../../../.venv/bin/python
$PY build_cases.py /path/outside/ccc-mcp/cases
$PY build_materials.py /path/outside/ccc-mcp/cases
$PY build_questions.py
$PY build_pilot.py
$PY parity_check.py
$PY key_check.py
$PY fairness_check.py
$PY verify_keys.py /path/outside/ccc-mcp/cases
```

## 狀態

- 三案例已建立，TASK 狀態一致。
- 材料存在性、內部一致性、可找到性、答案核對全部通過（見上列指令）。
- 離線自動化測試：`prototypes/archgraph/tests/test_p0b_presentation.py`。
- 尚未內部預試，尚未招募新手，尚未執行任何模型呼叫。
- B 組為手寫基線，尚未替換為人工撰寫或已核准模型輸出。

