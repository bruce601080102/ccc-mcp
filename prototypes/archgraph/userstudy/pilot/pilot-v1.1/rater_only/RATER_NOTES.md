# 評分者說明（僅供評分者與作者使用，不發給測試者）— pilot-v1.1

## 評分流程

1. 清單題的名稱先統一成標準名稱（見 `COORDINATOR_GUIDE.md` 第 8 節），每個改寫記在 `normalization_log.md`。
2. 對每份作答表執行 `python score.py keys_<case>.json <作答表>`，保留輸出。
3. 逐條判定 `claim_code`：TRUE、FALSE、UNSUPPORTED。預試只需一位非作者評分者；正式研究需兩位獨立評分者並計算 Cohen's kappa（≥ 0.8）。
4. 不一致由第三人裁決，並記錄理由。

## 計分項目（與 `score.py` 一致）

| 項目 | 規則 |
|---|---|
| Q01、Q03–Q08、Q11、Q12 | F1。空白（null）為未作答，0 分，計為不確定；寫「無」為明確答案 |
| Q02 | 與標準完全相同得 1 分 |
| Q09 | 每格 1 分。只接受「是」「否」（或 JSON true / false）。「不確定」與空白計為不確定；其他文字列入 `invalid` |
| Q10 答案 | 只有「不能」得分；留白為 blank，`不確定` 為 abstain，其他文字為 invalid |
| Q10 阻擋原因 | 勾選集合必須與標準完全相同；勾選「尚未 commit」一律算錯 |
| Q10 待執行動作 | 勾選集合必須與標準完全相同；勾選「需要重跑測試」或「不需要任何動作」一律算錯 |
| `q10_all_correct` | 三個子分數全部正確才為 true |
| `q10_error_subscores` | 狀態為 wrong、blank 或 invalid 的子分數數量（abstain 不計） |
| `q09_accuracy` | Q09 正確格數 ÷ 21 |

## 標準答案的來源

標準答案由 `build_materials.py` 從 pipeline 輸出產生，並已由 `verify_keys.py` 以 git、已核准計畫、主機測試重跑與 import 證據獨立核對。**但評分者仍需在預試前親自核對一次**，因為核對程式與標準答案由同一團隊撰寫。

## 注意

- 評分者不可是作者。
- 不要因為某題全體都錯，就認為題目有問題；先確認材料的證據位置。
- 「尚未 commit 不是阻擋項」是本方案的定義（見 `archgraph/gate.py`），不是評分者可以自行修改的規則。
- 「測試通過不是可合併的理由」同樣是定義。
- 條件 B 是 Claude 根據完整事實表撰寫的固定文字基線，不是獨立 AI 摘要工具的輸出。
