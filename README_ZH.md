<p align="center">
  <img src="docs/assets/logo.jpg" alt="ccc-mcp logo" width="120">
</p>

# ccc-mcp

[English](README.md) | **繁體中文**

[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue?style=flat-square)](LICENSE)

### 讓 AI 專家團隊共同完善需求、分工執行、共享可追溯的工作狀態，並用證據評估成果。

ccc-mcp 是本機的 MCP server、CLI 與 Web 控制台。它讓 ChatGPT（以及選用的 Codex 與 Claude Code）以一組分工明確的專家角色加入你的專案：分析師、設計師、審查員、開發者。需求、已核准的計畫、共用 Context、Git 修改與主機測試結果，都會保存成可追溯的工作狀態；任務是否完成，看的是主機蒐集到的證據，而不是 Agent 自己說完成了。

**舉例來說，** 你輸入「幫我做會員管理系統」。由分析師、設計師與審查員組成的工作流，可以在動手寫程式之前，把這句話整理成登入方式、角色與權限、資料模型、例外情況、驗收條件與待確認問題（[逐步說明](#能把需求想得更完整的-ai-團隊)）。那段說明是示意，不是已記錄的實測，也不保證每次都能得到完整的規格。

**三個讓它與眾不同的地方：**

- **結構化的共用 Context，不是聊天摘要。** 一個專案包含多個任務，每個任務都有帶版本的已核准計畫（Approved Plan），附 SHA-256 hash 與核准紀錄。共用 Context 同樣有版本，只能透過附來源的 patch 更新。Agent 的說法、主機證據與即時的 Git 狀態分開保存。[詳細說明](#結構化共用-context)
- **能把需求想得更完整的專家團隊。** 分析師、設計師、對抗式與規格審查員、規格寫手依序接力。審查關卡會把工作退回，Meeting Mode 會先提出澄清問題，角色、順序、嚴格度與重試次數都由你決定。[詳細說明](#能把需求想得更完整的-ai-團隊)
- **先有證據，再有分數。** 主機執行測試，並記錄 Git diff 與靜態檢查。規則式的品質分數與證據覆蓋率由這些證據算出，預覽版的要求清單則顯示你的哪些要求已被確認。這個分數不是 AI 的正確率。[詳細說明](#可解釋的品質分數)

![任務會議室：使用者的需求、分析師的回覆，以及送出修正或要求指定角色重做的控制項](docs/assets/readme/chat-zh.png)

*任務會議室（已發行功能），顯示使用者的需求、分析師的回覆，以及送出修正或要求指定角色重做的控制項。*

**用已發行的 PyPI 版本開始：**

```bash
pip install -U ccc-mcp
ccc-mcp ui
```

[快速開始](#快速開始) · [發行狀態](#發行狀態) · [文件](docs/ch/README.md)

## 功能總覽

| 能力 | 它帶給你什麼 | 狀態 |
| --- | --- | --- |
| [結構化共用 Context](#結構化共用-context) | 有版本的 Context、附 hash 的已核准計畫，以及逐項標明來源的交接包 | 已發行 |
| [AI 專家團隊](#能把需求想得更完整的-ai-團隊) | 19 個角色、7 套內建工作流、自訂工作流、Meeting Mode、審查關卡 | 已發行（Meeting Mode 為選用） |
| [會議室檢視](#會議室) | 每個角色的發言、執行歷程、目前卡點，以及暫停、中止、修正與重做的控制項 | 已發行 |
| [可解釋的品質分數](#可解釋的品質分數) | 規則式分數、證據覆蓋率、狀態、歷史紀錄與重新評分 | 已發行（選用） |
| 要求清單與分數卡 | 每條要求顯示為已確認、沒做到或還沒確認 | 預覽 |
| [主機證據與嚴格審查](#主機證據與嚴格審查) | 主機執行的測試、Git diff、以證據為基礎的審查關卡 | 已發行 |
| [跨對話與跨 Agent 接續](#跨對話與跨-agent-接續) | 新的 ChatGPT 對話、接續 Prompt、額度備援 | 已發行 |
| [Web 控制台與自訂團隊](#web-控制台) | 專案、角色、工作流、任務、成果、Context、Skills 與 MCP 工具 | 已發行 |

## 快速開始

這一節說明的是已發行的 PyPI 版本。標示為預覽的功能不在這個版本中。

1. **安裝並開啟主控台。** 安裝與啟動主控台需要用到終端機，之後大部分操作都在瀏覽器裡進行。

   ```bash
   pip install -U ccc-mcp
   ccc-mcp ui
   ```

   主控台只在 `127.0.0.1` 提供服務，啟動時會印出網址，附帶一次性的本機 token。

2. **加入專案。** 在**所有專案**頁建立專案或加入既有資料夾。也可以用終端機：

   ```bash
   ccc-mcp project create hello-demo --write
   ccc-mcp project add ./my-folder --write
   ```

   `--write` 會要求你在本機確認，之後允許 ccc-mcp 修改該專案並執行它的測試指令。在重要的資料夾上開啟前，請先讀[建立並授權第一個專案](docs/ch/3-first-project.md)。

3. **連接 ChatGPT。** 在 OpenAI Platform 建立 Tunnel，然後在主控台的**連線**頁連接，或執行 `ccc-mcp connect tunnel_你的ID`，並在隱藏的提示輸入 runtime API key。詳見[連接 ChatGPT](docs/ch/2-connect-chatgpt.md)。

4. **開始任務。** 在已連線的 ChatGPT 對話中描述要做的修改。若在終端機操作，`task create` 會記錄需求與驗收條件：

   ```bash
   ccc-mcp --project hello-demo task create "新增 hello 函式" --acceptance "執行後輸出 Hello"
   ```

5. **追蹤進度。** 在**任務**頁打開任務，查看會議室、成果分頁與評估。

**費用與限制，分成四個獨立部分：**

- **ChatGPT 方案。** ChatGPT-only 模式使用你現有的 ChatGPT 方案，[ChatGPT 方案本身仍有使用限制](https://learn.chatgpt.com/docs/pricing)。
- **OpenAI 模型 API。** ChatGPT-only 模式不使用計量計費的 OpenAI 模型 API 金鑰。
- **Tunnel runtime key。** Tunnel 需要在 OpenAI Platform 取得獨立的 runtime API key。它的計費依 OpenAI Platform 的條款，本專案無法決定，使用前請先確認。
- **Codex 與 Claude Code。** 這些選用工具使用各自的帳號與使用限制。

ccc-mcp 並不宣稱比單一 Agent 更省 token。工作流為了驗證而增加了幾輪，每個任務的 token 比較仍在量測中。

## 結構化共用 Context

聊天摘要只是某一個模型記得的內容。ccc-mcp 改為把工作狀態保存成結構化紀錄，每個 Agent 讀的都是同一份紀錄。

| 紀錄 | 怎麼保存 |
| --- | --- |
| **專案** | 一個已註冊的資料夾。可讀的文件放在專案內的 `.ccc-mcp/`（共用的 `CONTEXT.md`、`context/` 與各任務的報告），資料庫放在 ccc-mcp 的 home 目錄。 |
| **任務** | `TASK-0001` 等編號，記錄需求、驗收條件、狀態，以及它目前在等什麼，例如 `AGENT_RUNNING`、`USER_APPROVAL`、`PM_FLOW_DECISION`、`CHATGPT_HANDOFF_PENDING` 或 `PAUSED`。 |
| **已核准計畫（Approved Plan）** | 每份計畫有 ID、版本，以及計畫內容的 SHA-256 hash。核准紀錄寫明誰核准了哪個版本與 hash，以及核准時對應的 Context 版本。核准只適用於那一份計畫，所以較新的計畫需要重新核准。專案已授權寫入時，由專案授權取代逐份計畫的核准；否則由本機操作者確認確切的 hash。 |
| **共用 Context** | 有版本與歷史的快照。每次變更都以 Context Patch 送出，註明它基於哪個版本，並附上來源。 |
| **Agent 結果** | 每次執行的原始報告都原封不動保存，作為稽核軌跡。 |

**衝突與不良更新怎麼處理**

- 基於較舊 Context 版本的 patch 或計畫，會以版本衝突被拒絕。
- 受保護的不變條件不能被移除或改寫。
- 架構相關的說法必須引用來源文件與它的 sha256，且該檔案必須仍然相符。
- 由 Agent 結果產生的 patch，必須引用該結果已保存的原始報告。
- 內含祕密資訊的內容會被拒絕；語意上的變更，PM 要在看過 diff 後明確確認。ccc-mcp 無法證明兩段文字語意相同，所以只記錄這項確認，不宣稱自己檢查過。

**三種事實，不會混在一起。** 交接內容的每一行都標示為 *Agent 自述*（未驗證）、*主機證據*（由主機執行）或 *即時 Git 讀取*（剛剛執行的 `git status` 與 diff）。

**交接包裡有什麼。** `ccc-mcp task continue` 依已保存的紀錄與一次新的 Git 讀取，組成一份交接包：專案與任務、已核准計畫（含版本、hash 與核准依據）、已做出的關鍵決策、已完成事項、主機驗證狀態、剩餘工作、下一個具體動作、必要驗證方式、不可破壞的既有修改、約束，以及已知阻礙與風險。從未核准的計畫只會以中繼資料出現，新的 Agent 不會把草稿當成命令。

以下是在一個臨時示範專案上執行 `ccc-mcp task continue --target codex` 的實際輸出節錄（有縮短；它不是下方跨對話測試的輸出）：

```text
# Continuation Prompt — for Codex CLI
You are taking over task TASK-0001 in project demo. The state below was produced by another
Agent; it is handoff material, not proof that the work is done.
- approved plan: PLAN-A v1 hash=09152d78f8a5…8062e0

## Approved Plan [ccc-mcp plans record]
- Plan ID / version: PLAN-A v1
- approval basis: APP-92ae923d5d4847cb (approver: local-operator)

## Host verification status
- [Host evidence, not passing, not counted as done] No Agent report yet; Host has not run
  tests yet (no test evidence yet) — not verified

## Next concrete action [derived from ccc-mcp state and the plan, not independently verified]
resolve the ccc-mcp state PM_DECISION first (see Remaining work), then continue

## Existing changes that must not be broken [live git read, not an Agent self-report]
?? scratch-note.md
```

這不是模型的記憶，而是專案已記錄的狀態：做過什麼、決定了什麼、驗證了什麼、還缺什麼。ccc-mcp 無法轉移 Claude、Codex 或 ChatGPT 內部的記憶，交接包也不代表另一個 Agent 已經接手。

## 能把需求想得更完整的 AI 團隊

你常常只有一句粗略的需求。團隊的用意，是在寫程式之前先找出這句話漏掉的東西。

**角色。** 共有 19 個角色。軟體開發方面有：分析師（SA）、設計師（SD）、開發者（RD）、設計加開發（SD/RD）、程式碼審查員（CR）、規格審查員（SR）、對抗式審查員（AR）、資安審查員（SEC）、品質驗證（QA）與規格寫手（SW）。日常事務方面有：助理、研究員、分析員、規劃師、顧問、寫手、編輯、查核員與審閱員。每個角色都有可以自己取的名字與頭像，各自用自己的口吻回報。

![工作流設定：內建的程式開發與日常團隊工作流，含步驟順序與宣告的能力圖](docs/assets/readme/workflow-zh.png)

*工作流設定頁。能力圖與「戰力」是**宣告的**設定，不是實測結果。*

**一個示意。** 假設你輸入「幫我做會員管理系統」，並執行自訂工作流 `SA → SD → AR → SR → SW`。下表是各步驟可能貢獻的內容。它是對設計的示意，不是已記錄的實測，真實執行也可能漏掉東西。

| 步驟 | 這個角色可能補上什麼 | 發現問題時 |
| --- | --- | --- |
| **SA** 分析師 | 把需求拆成註冊與登入方式、使用者角色、要保存的會員資料，以及假設與待確認問題的清單。 | |
| **SD** 設計師 | 提出資料模型、權限模型與主要介面，並說明取捨。 | |
| **AR** 對抗式審查員 | 找出缺漏，例如連續登入失敗時沒有規則、刪除會員後資料怎麼處理。每項發現都必須附證據，沒有證據的會被捨棄。 | 把設計退回給 SD，或請 PM 決定。 |
| **SR** 規格審查員 | 檢查有沒有互相矛盾、遺漏，以及設計能不能真的做出來。 | 退回給產出者。 |
| **SW** 規格寫手 | 寫成一份規格：編號的需求（`REQ-001`，MUST／SHOULD／MAY）、介面、Given／When／Then 驗收條件，以及列出而不是猜測的待確認問題。 | |

在後面接上 `RD → CR → QA`，同一個團隊就會繼續實作並檢查成果。

**Meeting Mode**（選用）。在 SD/RD 寫任何程式之前，先做一次唯讀的檢查，讓它向 SA 提出澄清問題。SA 回答，或把問題升級給 PM，依你的設定，PM 會去問你，或自己決定。這個流程輪數有限（最多 5 輪），完整紀錄都會保存（`ccc-mcp task meeting`）。

**審查關卡。** 對抗式審查有一般、嚴格、極限三種強度。只有附了證據、且達到該強度的發現才能阻擋。`[blocker]` 會交給 PM；較輕的發現則把工作退回給產出者。退回次數限制在 1 到 5 次，用完後由你或 PM 決定。

**打造你自己的團隊。** 用任意角色組成最多 12 步的工作流（每個專案最多 20 套），設定新任務由誰挑工作流（固定、由 PM 挑，或依任務文字自動挑），並替每個角色指定 provider、model 與 effort。規格寫手還不在任何內建工作流裡，請在**設定 → 工作流**加入。日常團隊跑在 ChatGPT 上，所以能上網查資料；由 ChatGPT 擔任某一步時，交出報告後下一步立刻開始。

## 會議室

任務頁打開時是一場對話，而不是一份 log。

- **每個角色用自己的身分發言。** 每則回覆都依工作流的順序保存，附該角色的名字與頭像。
- **你看得到狀態。** 任務會顯示目前在哪一步、為什麼在等，例如某個關卡需要決定，並附上觸發它的發現。
- **你可以介入。** 可以暫停、繼續或中止任務，送出修正，或要求指定的角色重做它的步驟。那個角色會帶著你的備註重新執行，之後的每一步也會再跑一次。
- **歷程會被保存。** 時間軸列出需求、每次執行、每次主機測試結果與你的介入。

## 可解釋的品質分數

ccc-mcp 裡有三種不同的東西，請不要把其中一種當成另一種。

| | 它是什麼 | 來源 |
| --- | --- | --- |
| **A. 能力圖** | 角色被*設定*成擁有的能力：研究、分析、規劃、創作、查核、風險，各 0 到 5 分。 | 在**設定**中宣告。不是實測，也不計入任何分數。 |
| **B. 品質分數** | 以規則估算「手上的證據看起來有多好」。 | 由主機測試、diff、靜態檢查與審查結果算出。已發行，選用。 |
| **C. 要求清單** | 你的要求中，有多少被已記錄的證據確認。 | 在成果頁依證據核對。**預覽。** |

品質分數不是 AI 的正確率，也不代表程式碼正確。

**分數怎麼算**（`config evaluation-engine on`；不會額外呼叫模型）：

- `quality_score` 是只取「有真實證據的子分數」的加權平均，使用該角色的權重，並對這些子分數重新正規化。沒有證據的子分數是*缺漏*，不是 0 分，也不是滿分。
- `evidence_coverage`（證據覆蓋率）是「有證據的子分數」的權重總和，除以該角色的權重總和。它說明這次判斷有多少是建立在證據上。

| 子分數 | 權重（SD/RD） | 怎麼算 |
| --- | --- | --- |
| `test` | 0.40 | 主機執行測試的通過率。只在該輪確實有修改時才計入；若通過的測試都是 Agent 自己寫的，最高 70 分。 |
| `requirement` | 0.25 | 驗收項目中，至少有一個詞出現在已保存文字裡的比例。這是文字比對，不是證明。 |
| `code_quality` | 0.15 | 100 分，每個 `ruff` 發現扣 10 分。 |
| `security` | 0.10 | 沒有資安發現是 100 分，否則每個發現扣 50 分。 |
| `review` | 0.10 | 100 分；審查員要求修改則為 40 分。沒有審查就是缺漏。 |

權重因角色而異（例如 SA 的 `requirement` 佔 70%、`review` 佔 30%），也可以調整。

**一個計算範例。** 用真實的評分程式碼，以虛構的輸入算出，不是來自某個已記錄的任務。一次 SD/RD 的執行：主機測試全過、有兩個 `ruff` 發現、驗收文字符合一半：

| | test | requirement | code_quality | security | review | 品質分數 | 證據覆蓋率 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 測試有執行 | 100 | 50 | 80 | 100 | 無 | **82.78** | **90%** |
| 同一輪，但測試沒有執行 | 無 | 50 | 80 | 100 | 無 | **69.0** | **50%** |

分數看起來不錯時，證據覆蓋率仍可能很低。最明顯的是分析師：它的 `review` 子分數永遠不會有值，所以即使 `requirement` 滿分，品質分數是 100，證據覆蓋率卻只有 70%。請把兩個數字一起看。

**狀態是另外決定的。** `PASSED`、`FAILED`、`NEEDS_VERIFICATION` 或 `INSUFFICIENT_EVIDENCE` 由硬性規則決定，再高的分數也無法覆蓋：驗收測試失敗就是 `FAILED`；主機沒有執行測試，或根本沒有任何修改，是 `NEEDS_VERIFICATION`；證據覆蓋率低於 60% 是 `INSUFFICIENT_EVIDENCE`。

**在哪裡看得到。** 任務頁會列出每次評估的分數、覆蓋率、狀態、各項子分數與權重、版本編號，以及是什麼觸發了它。`task evaluations` 列出歷史，`task reevaluate` 依已保存的證據重新評分。已發行的評分器涵蓋 SA、SD、RD、SD/RD 與 CR 角色。

**它做不到什麼。** 在 30 個手寫修補的小型基準測試中，主機看得到的測試能抓出壞掉的成果，但分不出「錯誤但測試全綠」與「正確」的成果（AUROC 0.67）。`ccc-mcp validate-scoring` 讓你在自己的案例上拿真實結果檢驗分數。見[技術參考](docs/ch/10-technical-reference.md#評估引擎)。

### 成果頁（預覽）

![預覽：已完成任務的成果頁，含要求清單與保存的品質分數](docs/assets/readme/results-zh.png)

*預覽，來自目前的 vNext 候選版。已發行的 PyPI 版本還沒有這一頁。*

預覽版的成果頁把保存的分數與你的要求清單並列。每條要求只有在已記錄的證據符合與它對應的規則時才顯示**已確認**，例如有一個與它綁定的測試通過。當證據與這一條要求相矛盾時顯示**沒做到**，這不代表整個任務失敗；證據缺少或需要人工核對時顯示**還沒確認**。文字命中或 Agent 說「完成了」，都不會確認任何東西。

一次已記錄的驗收執行（TASK-0001，本機專案）說明了為什麼需要兩種檢視。主機執行了 `python3.14 -m unittest -q`，2 項測試通過。保存的品質分數是 **89 分（滿分 100）**，但 **4 條要求中只有 2 條**已確認：`multiply` 函式已經在程式裡，但還沒有測試明確對應到它，「保持不變」的要求則需要人工檢查。保存的分數維持任務完成時的樣子，要求清單則依目前的紀錄重新核對。那次執行中 PM 的推進步驟是人工操作的，所以不是完全無人值守的工作流，原始紀錄也沒有發布在這個 repository 中。

## 主機證據與嚴格審查

Agent 說自己做完了，不算證據。

- **主機執行的測試。** 開發步驟之後，設定好的測試指令由主機執行，不是 Agent 報告裡寫的。結果與對應的紀錄會顯示在成果分頁。
- **Git diff。** 可能改動檔案的每一步，都會把 diff 隨任務保存。逐檔的檢視器是預覽功能。
- **靜態檢查。** `ruff` 的發現會計入 `code_quality` 與 `security` 子分數。
- **Agent 報告與主機結果分開。** 任務檢視不會把它們合成一個「成功」：Agent 的說法與主機的判定分別顯示。如果審查員說「不需要修改」，但主機測試失敗或沒有收集到任何測試，主機證據會推翻它，工作退回給開發者。
- **有次數限制的審查關卡。** 關卡不通過時，工作會退回給產出者，次數有限，之後交由你或 PM 決定。沒有證據的發現會被捨棄。
- **不同情況有不同狀態。** 證據支持（`PASSED`）、證據相矛盾（`FAILED`）、證據不足（`INSUFFICIENT_EVIDENCE`），或主機尚未驗證（`NEEDS_VERIFICATION`）。

主機測試通過，不代表每條要求都正確；不是每一條自然語言要求都能自動核對。沒有對應核對方式的要求，會保持為還沒確認，等人工確認。

## 跨對話與跨 Agent 接續

接續工作有三種不同的方式：

- **用新的 ChatGPT 對話，透過 MCP。** 新的對話讀取已保存的任務狀態、計畫與 Context。
- **接續 Prompt。** `task continue --target claude|codex|chatgpt` 會產生[結構化共用 Context](#結構化共用-context) 一節所說的交接包，由你貼到另一個對話。不會替你自動切換，產生 Prompt 也不代表下一個 Agent 已經接手；它仍要讀取 Prompt、在同一個 repository 工作，並通過主機的測試。主控台的任務交接面板會顯示同一份內容。
- **額度備援。** 當某個角色的 provider 回報額度用盡，ccc-mcp 可以把同一個任務重新派工一次，給你用 `--quota-fallback` 設定的備援 provider。它不適用於找不到 CLI、登入失敗或一般錯誤，也不會連鎖派工。派工不等於完成：備援可能停在 `CHATGPT_HANDOFF_PENDING`，等待有人接手，也不會繞過已過期的核准。

任務搬移時可能處於任何狀態：未完成、等待 PM 決策，或等待主機驗證。交接內容會寫明是哪一種。

**目前不支援：**

- 一鍵或完全自動的 provider 切換，或在所有情況下都無人值守地切換。
- 轉移 Claude、Codex 或 ChatGPT 內部的對話記憶。只會轉移已記錄的任務狀態。
- 沒有共用 repository 的情況下接續。
- 切換正在執行中的 Agent。
- 在專案要求核准時跳過核准。
- 證明 token 較少，或 Benchmark 成績更好。

### 真實的跨對話測試

這是在一個小型 Python 專案 `continuity-session-test` 上的一次已記錄測試。兩個 AI 對話都在同一個 repository 中工作。

| 步驟 | 發生了什麼 |
| --- | --- |
| 1. Claude Code（對話 A） | 實作 `slugify`，`slugify` 的測試通過。`count_words` 仍保留 `NotImplementedError`。 |
| 2. 交接 | 執行 `ccc-mcp --project <id> task continue --target codex`，產生接續 Prompt。 |
| 3. Codex（新對話 B） | 讀取 Prompt、共用 Context 與 repository。在使用者明確授權第二階段後，實作 `count_words`，並保留 Claude 對 `slugify` 的修改。 |
| 4. 本機檢查 | 使用者執行 `python -m pytest -q`：**2 passed**。`git diff --check` 通過。沒有修改既有測試。沒有自行 commit 或 push。 |

**怎麼讀這次測試。** 兩個對話沒有共享聊天記錄。交接來自產生的接續 Prompt，加上共用 Context。第二階段只在明確授權後才進行。`2 passed` 是使用者在本機執行的 pytest，不是 ccc-mcp 正式的主機 TEST-RESULT，ccc-mcp 自己的 PM 關卡也沒有自動完成。**這沒有說明什麼：** 不代表每個專案或每次切換都會這樣順利，不代表新的 Agent 記得前一個對話，也不代表 token 或時間因此減少。

## Web 控制台

```bash
ccc-mcp ui
```

控制台與 CLI、MCP server 在同一個程序中執行，不需要另外建置前端，也不需要 Node.js。只綁定 `127.0.0.1`，會印出網址並自動開啟瀏覽器。可用 `--port` 指定埠號，或用 `--no-browser` 停用自動開啟。它讀取與 CLI、MCP server 相同的 `--home` 資料庫。

| 頁面 | 可以做什麼 |
| --- | --- |
| **工作戰情中心** | 查看任務活動、卡點與 Agent 評估紀錄。可選擇啟用 Claude 額度同步，讀取 Claude Code 狀態列提供的官方剩餘百分比。帳戶的歷史 Token 總量另外列出，無法歸因到單一專案。 |
| **所有專案** | 建立專案、加入資料夾、整理群組、匯入或匯出專案備份，並管理寫入授權。 |
| **設定** | 調整 profile、provider 與角色（名字、頭像、model、effort、額度備援）、工作流與審查關卡、報告語言、測試指令與 Meeting Mode。 |
| **任務** | 打開任務：會議室、成果分頁、評估、技術紀錄與交接面板。 |
| **脈絡** | 查看共用 Context 與它的版本。 |
| **連線** | 用 Tunnel ID 與 runtime API key 連接 ChatGPT，查看狀態與 log。保存的金鑰不會回傳給瀏覽器。 |
| **全域 Skills** | 勾選要安裝給所有專案與角色的本機 Claude Code 或 Codex Skills。 |
| **Skills 與 MCP** | 查看共用 Skills；替單一專案探索、安裝與管理本機 stdio MCP server 及其工具。 |
| **文件** | 閱讀隨套件安裝的繁中與英文指南。 |

Claude 額度同步是選用功能。它會在 Claude Code 的使用者 `statusLine` 設定中加入一個小型包裝程式，保留原本的狀態列，並只保存回報的百分比、重置時間與觀測時間。它不會呼叫 Claude 的模型，也不會讀取登入憑證。停用後會還原原本的設定。見[可靠性與安全邊界](docs/ch/7-reliability-and-security.md#10-web-控制台)。

只用 ChatGPT 時，在專案**設定**中選擇 `chatgpt-only` profile，或執行 `ccc-mcp --project hello-demo config profile chatgpt-only`。要使用 Codex 或 Claude Code 這類原生角色，才需要執行 `ccc-mcp setup all` 並完成各自的登入。CLI 操作同一份專案資料，完整命令見 [CLI 參考](docs/ch/8-cli-reference.md)。

## 發行狀態

這份 README 同時描述兩種不同的狀態。依照指令操作前，請先確認你使用的是哪一種。內容已對照 PyPI 上已發行的 ccc-mcp 0.4.0 wheel（2026-11-19）與 vNext 候選版檢查過。

| 能力 | PyPI 已發行版本（`pip install ccc-mcp`） | vNext 預覽（未發布） |
| --- | --- | --- |
| 角色、工作流、審查關卡、自訂工作流 | 有 | 有 |
| Meeting Mode（選用） | 有 | 有 |
| 會議室檢視，含暫停、中止、修正與重做 | 有 | 有 |
| 有版本的 Context、已核准計畫與 Context Patch | 有 | 有 |
| 接續 Prompt（`task continue --target`）與交接面板 | 有 | 有 |
| 額度備援（`--quota-fallback`） | 有 | 有 |
| 成果分頁的主機測試證據 | 有 | 有 |
| 品質分數、證據覆蓋率與評估歷史（選用） | 有 | 有 |
| Skills、MCP 工具與 Claude 額度同步 | 有 | 有 |
| 逐檔的 diff 檢視器 | 沒有 | 預覽 |
| 要求清單與分數卡 | 沒有 | 預覽 |
| 驗收契約，每個任務自動建立 | 沒有 | 預覽 |
| 證據規則：已確認、沒做到、還沒確認 | 沒有 | 預覽 |
| 不需要 ChatGPT 對話、自己跑完工作流的 Bot | 沒有 | 沒有（未來規劃） |

vNext 預覽是下一版的本機建置，只在這個 repository 內驗收過，版本號尚未決定，其中的內容也都還沒有發布。

從原始碼安裝開發預覽：

```bash
git clone https://github.com/bruce601080102/ccc-mcp.git
cd ccc-mcp
pip install -e .
```

## 實際成果：三題選定的 SWE-bench Verified 案例

只使用 **ChatGPT-only**，ccc-mcp 經過多輪修復，解出三題選定的 [SWE-bench Verified](https://www.swebench.com/) Astropy 題目。各題在本機執行官方評測 harness，報告都顯示 `resolved: true`：

| 題目 | Harness 報告 |
| --- | --- |
| `astropy__astropy-12907` | [report.json](benchmarks/swebench-verified/12907-report.json) |
| `astropy__astropy-13236` | [report.json](benchmarks/swebench-verified/13236-report.json) |
| `astropy__astropy-13398` | [report.json](benchmarks/swebench-verified/13398-report.json) |

[查看完整證據與方法](benchmarks/swebench-verified/README.md)，包含評測 run ID 與 ChatGPT 開發對話。這是**三題選定案例**，不是完整 Benchmark 分數，不代表全部 500 題 Verified 的結果，也不是官方排行榜提交。原始日誌保存在本機，沒有放在這個 repository 中。

## 觀看 Demo

**較早的一版主控台錄影（1080p、每秒 60 格、有聲音、63 秒）。** 它是在目前的成果頁之前錄製的，不包含要求清單或分數卡。

https://github.com/user-attachments/assets/eb7512b3-7f67-4a2e-aa7a-9e4b06a9a7d2

[Google Drive 也保留原始錄影](https://drive.google.com/drive/folders/15vn8wYrZ55JL5Aetb1IR6YGm7fK1i81m)。這段影片沒有展示跨對話交接，那次測試見[真實的跨對話測試](#真實的跨對話測試)。

## 安全邊界

持續寫入授權（由 `--write`，或主控台的「啟用寫入」開啟）允許 ccc-mcp 修改該專案，並執行它設定的測試指令。安裝相依套件可能執行 package script，因此相依套件的變更也要納入 diff 審閱。Codex 依照它設定的 sandbox 執行。Claude Code 有受限的工具政策，但沒有同等的作業系統強制 sandbox，因此請用權限最低的作業系統帳號執行它。

想從 `project list` 移除專案、同時保留資料夾，請用 `ccc-mcp project remove <id>`。`project delete` 則不同：它永久註銷這個 ID，並保留歷史紀錄。完整保證見[可靠性與安全邊界](docs/ch/7-reliability-and-security.md)。

## 文件

完整設定建議依序閱讀編號文件；英文與繁中的檔名和順序一致。

| 步驟 | 主題 |
| --- | --- |
| 1 | 安裝與登入 |
| 2 | 連接 ChatGPT |
| 3 | 建立並授權第一個專案 |
| 4 | 執行與恢復工作流程 |
| 5 | 設定 provider、角色、Context 與測試 |
| 6 | 安裝與使用 Skill |
| 7 | 了解可靠性與安全邊界 |
| 8–11 | CLI 參考、公開協議、技術參考與架構 |

- [繁體中文文件目錄](docs/ch/README.md)
- [English README](README.md)
- [English documentation index](docs/en/README.md)

## 未來規劃

> 以下是目標，不是現在能用的功能，也沒有預定的發布日期。

**能自己跑完工作流的 Bot。** 現在由一個 ChatGPT 對話擔任專案經理並推動工作流；Codex 和 Claude Code 可以接手個別角色，但任務仍要由 ChatGPT 對話發起並帶領。

下一個目標是做出不需要 ChatGPT 對話的 Bot 型入口：直接串接 Codex、Claude 或其他模型，並像現在組合工作流一樣把它們串成流程。角色流程與關卡、聊天室、能力圖，以及以主機實際測試結果作為證據的設計，預計原樣沿用。

## 開發

```bash
python3.11 -m pytest -q
```

本專案要求 Python 3.11 以上。真實 Agent 測試是選用的，需要本機已安裝並登入的 CLI；詳見[安裝與登入](docs/ch/1-install-and-sign-in.md)。

## 發版

發版時間與版本號都由你決定。推送或建立 Pull Request 後不會自動執行任何流程。準備發版時，從 `main` 手動執行 **Release**，在 GitHub 表單輸入版號（不加 `v`）。Release 會跑完整的 Python 與跨平台測試矩陣、建置並安裝 wheel、核對 wheel 與 sdist，為一致的原始碼 commit 建立 tag，再發布到 GitHub Releases 與 PyPI。任何一項檢查失敗，就不會建立 tag，也不會發布。它不會改動 `main` 的版本檔。
