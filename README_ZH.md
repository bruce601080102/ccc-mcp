# ccc-mcp

**讓最適合的 coding agent 負責最適合的工作，並讓每一次交接都能驗證。**

ccc-mcp 是給軟體專案使用的本機 MCP 工作流程。ChatGPT 管理需求與共用 Context；Codex 可以負責分析與審查；Claude Code 可以負責實作。若那才是合適選擇，也能讓 ChatGPT 單獨完成所有工作流角色。每一次交接都會保留繼續工作、審查或恢復任務需要的證據。

它適合同時使用多種 AI coding 工具的人：不讓額度耗盡、對話遺失，或模型一句「測試通過」就中止專案。

> [English README](README.md) · [繁體中文文件](docs/ch/1-install-and-sign-in.md)

## 它能做什麼

- 以可設定的 `PM → SA → SD/RD → REVIEW → PM` 角色接力處理工作。
- 保存版本化共用 Context、原始 agent 報告、diff 與主機實際觀察到的測試證據。
- 透過原生 CLI 使用 Codex 和 Claude Code，也可將所有角色指定給 ChatGPT，走 ChatGPT-only workflow。
- 只在辨識為額度耗盡時才啟用 fallback，不會把安裝、登入或一般執行錯誤誤當成額度問題。
- 讓操作者安裝的 `SKILL.md` 指引可供所有角色使用。

## 選擇模式

**最佳模型工作流**：ChatGPT 擔任 PM、Codex 負責分析與審查、Claude Code 負責實作。若你三種工具都有，這是預設模式。

**ChatGPT-only workflow**：用 `config profile chatgpt-only` 把全部五個角色（`SA`、`SD`、`RD`、`SD/RD`、`REVIEW`）都設為 ChatGPT——包含獨立的 `SD`、`RD`，`config show` 或手動派工都不會殘留 Codex／Claude。自動工作流本身實際上仍只會接力派發 `SA → SD/RD → REVIEW`。不需要 Codex 或 Claude Code CLI，也不會消耗它們的 CLI 額度；但可用模型、額度與功能一律依你連接的 ChatGPT 帳戶與方案而定，不要假設額度是無限的。

## 快速開始

需求：Git、Python 3.11 以上、OpenAI Platform Tunnel。最佳模型工作流另需已登入的 Codex 和／或 Claude Code 帳戶。

```bash
git clone https://github.com/bruce601080102/ccc-mcp.git
cd ccc-mcp
source scripts/setup-real-agents.sh
# 只有要使用 Codex 和／或 Claude Code 時才執行：
ccc-mcp setup all

ccc-mcp project create hello-demo
ccc-mcp project authorize hello-demo
```

若要全程使用 ChatGPT，略過 `ccc-mcp setup all`，建立專案後套用 `chatgpt-only` profile：

```bash
ccc-mcp --project hello-demo config profile chatgpt-only
```

依照[連接 ChatGPT](docs/ch/2-connect-chatgpt.md)建立 Tunnel 並選取 ccc-mcp 連線後，在 ChatGPT 提出：

> 請在 `hello-demo` 建立 `hello.py`，執行時輸出 `Hello, World!`，並加入驗證輸出的測試。

在擁有專案的本機驗證結果：

```bash
ccc-mcp --project hello-demo task status
ccc-mcp --project hello-demo task usage
ccc-mcp --project hello-demo context show
```

對原生角色而言，`task usage` 才是 CLI 是否真的被呼叫的依據；ChatGPT 角色本來就是互動式執行，沒有原生 CLI 呼叫：`task usage` 會標示 `"interactive": true` 與 `invocation.source: "chatgpt_web"`，所以這裡的 `cli_invoked: false` 代表「設計上本來就不呼叫」，不是模糊的 CLI 失敗訊號。不論哪種模式，聊天回覆自稱成功都不夠；設定的主機測試命令會在 REVIEW 完成前留下紀錄。

`ccc-mcp --project hello-demo config show`（或 `config providers`）也會顯示唯讀的 `profile` 欄位——`chatgpt-only`、`best-model` 或 `custom`——由目前所有角色的 provider 即時推算。隨時可用 `ccc-mcp --project hello-demo config profile best-model` 切回，這會依本機實際安裝的 CLI 動態決定（兩個都裝＝Codex/Claude 分工；只裝一個＝全部角色用那一個；一個都沒裝則拒絕）。

## 工作流程

```text
ChatGPT（PM）→ Codex（SA）→ Claude Code（SD/RD）→ Codex（REVIEW）
       ↑                                                    │
       └──────── 共用 Context、證據與下一個任務 ────────────┘
```

預設角色是有意見的預設值，不是硬性規定。你可以替角色指定支援的 provider：

```bash
ccc-mcp --project hello-demo config role REVIEW codex --effort high --quota-fallback chatgpt
ccc-mcp --project hello-demo config role SD/RD claude
ccc-mcp --project hello-demo config show
```

完整說明請看[協作工作流程](docs/ch/4-workflow.md)。

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

## 重要邊界

`project authorize` 允許 ccc-mcp 修改該專案並執行設定的測試命令。相依套件安裝可能執行 package script，因此相依變更也必須納入 diff 審閱。Codex 依設定的 sandbox 執行；Claude Code 雖有限制工具政策，但尚未有同等的 OS 強制 sandbox，應以最低權限的 OS 帳戶執行。

不想再用某個專案了？先 `ccc-mcp project revoke <id>`（若目前仍是持續授權狀態），再執行 `ccc-mcp project delete <id>` 永久從 `project list` 移除，之後這個 ID 不能再被使用或重新註冊。它不會刪除磁碟上的檔案，也不會刪除任何歷史 Context／task／證據——確認路徑後請自行刪除專案目錄。完整說明見[可靠性與安全邊界](docs/ch/7-reliability-and-security.md)。

## 開發

```bash
python3.11 -m pytest -q
```

本專案要求 Python 3.11 以上。真實 agent 測試需要本機已安裝且登入的 CLI，預設不會執行；詳見[安裝與登入](docs/ch/1-install-and-sign-in.md)。
