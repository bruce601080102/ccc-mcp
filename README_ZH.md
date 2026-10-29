# ccc-mcp

**讓最適合的 coding agent 負責最適合的工作，並讓每一次交接都能驗證。**

[![CI](https://img.shields.io/github/actions/workflow/status/bruce601080102/ccc-mcp/release.yaml?branch=main&style=flat-square&logo=githubactions&logoColor=white&label=CI)](https://github.com/bruce601080102/ccc-mcp/actions/workflows/release.yaml)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/downloads/)
[![Platforms](https://img.shields.io/badge/Platforms-macOS%20%7C%20Linux%20%7C%20Windows-555555?style=flat-square)](docs/ch/1-install-and-sign-in.md)
[![MCP](https://img.shields.io/badge/Protocol-MCP-6E56CF?style=flat-square)](https://modelcontextprotocol.io/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue?style=flat-square)](LICENSE)

[![ChatGPT](https://img.shields.io/badge/ChatGPT-PM-10A37F?style=flat-square)](docs/ch/2-connect-chatgpt.md)
[![Codex](https://img.shields.io/badge/Codex-SA%20%C2%B7%20REVIEW-000000?style=flat-square)](docs/ch/4-workflow.md)
[![Claude Code](https://img.shields.io/badge/Claude%20Code-SD%2FRD-D97757?style=flat-square)](docs/ch/4-workflow.md)

ccc-mcp 是給軟體專案使用的本機 MCP 工作流程。ChatGPT 管理需求與共用 Context；Codex 可以負責分析與審查；Claude Code 可以負責實作。若那才是合適選擇，也能讓 ChatGPT 單獨完成所有工作流角色。每一次交接都會保留繼續工作、審查或恢復任務需要的證據。

它適合同時使用多種 AI coding 工具的人：不讓額度耗盡、對話遺失，或模型一句「測試通過」就中止專案。

> [English README](README.md) · [繁體中文文件](docs/ch/1-install-and-sign-in.md)

## 快速開始：開啟控制台

需求：Git、Python 3.11 以上。要與 ChatGPT 連線時，還需要 [OpenAI Platform Tunnel](docs/ch/2-connect-chatgpt.md)。

```bash
pip install ccc-mcp
ccc-mcp ui
```

瀏覽器會自動開啟本機控制台。接著在介面中：

1. 從 **所有專案**建立 `hello-demo`，或加入既有資料夾。建立後到專案頁按「啟用寫入」並輸入確認文字；加入既有資料夾時也可直接勾選寫入授權。
2. 在專案的 **設定**選擇工作流 profile 與角色。**測試指令**可留空：ccc-mcp 會安裝專案宣告的相依套件，優先測試本次修改的 Python 測試檔；若沒有修改測試檔，或 pytest 設定也有變動，則由專案的 pytest 設定決定測試範圍。只有要覆寫這個規則時才需填指令。只用 ChatGPT 時選 `chatgpt-only`；使用 Codex／Claude Code 時先完成各自的原生 CLI 安裝與登入。
3. 依照[連接 ChatGPT](docs/ch/2-connect-chatgpt.md)建立 Tunnel，再到 **連線**頁輸入 Tunnel ID 與 runtime API key，按「連線」。
4. 在 ChatGPT 選取 ccc-mcp 連線，提出：「請在 `hello-demo` 建立 `hello.py`，執行時輸出 `Hello, World!`，並加入驗證輸出的測試。」回到控制台的 **任務**、**脈絡**頁查看進度與證據。

控制台只在本機 `127.0.0.1` 提供服務；按 Ctrl+C 即可停止。更多操作見下方[Web 控制台](#web-控制台)。

## 它能做什麼

- 以可設定的 `PM → SA → SD/RD → REVIEW → PM` 角色接力處理工作。
- 保存版本化共用 Context、原始 agent 報告、diff 與主機實際觀察到的測試證據。
- 透過原生 CLI 使用 Codex 和 Claude Code，也可將所有角色指定給 ChatGPT，走 ChatGPT-only workflow。
- 只在辨識為額度耗盡時才啟用 fallback，不會把安裝、登入或一般執行錯誤誤當成額度問題。
- 讓操作者安裝的 `SKILL.md` 指引可供所有角色使用。
- 透過本機 Web 控制台管理專案、追蹤任務、設定角色與連線，並管理 Skills 和 MCP 工具。

## 選擇模式

**最佳模型工作流**：ChatGPT 擔任 PM、Codex 負責分析與審查、Claude Code 負責實作。若你三種工具都有，這是預設模式。

**ChatGPT-only workflow**：在專案 **設定**選 `chatgpt-only`，就會把全部五個角色（`SA`、`SD`、`RD`、`SD/RD`、`REVIEW`）都設為 ChatGPT。自動工作流實際接力派發 `SA → SD/RD → REVIEW`。這個模式不需要 Codex 或 Claude Code CLI；可用模型、額度與功能依你連接的 ChatGPT 帳戶與方案而定。

## 工作流程

```text
ChatGPT（PM）→ Codex（SA）→ Claude Code（SD/RD）→ Codex（REVIEW）
       ↑                                                    │
       └──────── 共用 Context、證據與下一個任務 ────────────┘
```

預設角色可以調整。在專案的 **設定**頁，替各角色指定支援的 provider、effort 與額度 fallback；也可以設定測試命令與報告語言。

完整說明請看[協作工作流程](docs/ch/4-workflow.md)。

## Web 控制台

```bash
ccc-mcp ui
```

在同一個供應 CLI 與 MCP server 的程序裡啟動本機 Web 控制台——不需要另外 clone、建置或啟動前端，即使從原始碼建置也不需要 Node.js（介面是打包進 `ccc-mcp` wheel 的純 HTML/CSS/JS）。只綁定 `127.0.0.1`，啟動後會印出網址並自動開啟瀏覽器；可用 `--port` 指定埠號（若被占用會清楚回報），省略則自動挑一個可用的；`--no-browser` 可停用自動開啟瀏覽器。按 Ctrl+C 即可停止。它讀取與 CLI、MCP server 相同的 `--home` 資料庫，三者的專案與任務資料永遠同步。

控制台的主要頁面：

| 頁面 | 可做的事 |
| --- | --- |
| **所有專案** | 建立專案、加入既有資料夾、整理群組；新增後可啟用或撤銷寫入授權。 |
| **設定** | 調整 profile、provider／角色、報告語言、測試命令與 Meeting Mode。 |
| **任務／脈絡** | 查看階段、等待原因、執行紀錄、usage／CLI 呼叫證據、原始結果與共用 Context。 |
| **連線** | 使用 OpenAI Platform Tunnel 的 ID 與 runtime API key 連到 ChatGPT，查看連線狀態與 log。 |
| **全域 Skills** | 勾選要安裝給所有專案及角色的本機 Claude Code／Codex Skills，並查看或移除已安裝項目。 |
| **Skills 與 MCP** | 查看共用 Skills；替單一專案探索、安裝及管理本機 stdio MCP server 與其可用工具。 |
| **文件** | 在控制台內閱讀隨套件安裝的繁中與英文指南。 |

在 **Skills 與 MCP** 頁可檢查工具、勾選此專案允許使用的工具、以 JSON 參數呼叫已授權工具，並查看最近的 MCP 呼叫紀錄。全域安裝的 MCP server 也會顯示於此，但仍須用 CLI 移除。側邊欄可切換介面語言，這項瀏覽器偏好與專案報告語言分開。**連線**頁可選擇將金鑰記住在這台裝置上（`~/.ccc-mcp/tunnel/credentials.json`，只開放本機帳號讀取）；金鑰儲存後不會再回傳給瀏覽器。啟用寫入、清除歷史和刪除等操作都需要輸入確認文字；詳見[可靠性與安全邊界](docs/ch/7-reliability-and-security.md#web-控制台)。

**專案**頁可建立自訂群組，透過下拉選單或拖放移動專案卡片，並調整群組順序；分類只儲存在本機 `<home>/ui/project_groups.json`，不更動專案路徑或設定。**連線**頁可儲存多筆具名稱的設定，同一 Tunnel ID 也能存不同金鑰；選取設定後按「連線」即可切換，但同時間只執行一組 Tunnel 連線。

## CLI：自動化與進階操作

偏好終端機或要寫腳本時，可用 CLI 操作同一份專案資料。使用 Codex／Claude Code 原生角色前，先執行 `ccc-mcp setup all` 並完成各自的登入。例如，從頭以 CLI 建立專案：

```bash
ccc-mcp setup all
ccc-mcp project create hello-demo --write
ccc-mcp --project hello-demo task status
ccc-mcp --project hello-demo task usage
ccc-mcp --project hello-demo context show
```

`--write` 會在建立專案時要求本機確認，授予 ccc-mcp 持續寫入專案並執行設定的測試命令的權限。加入既有資料夾也可用 `ccc-mcp project add <資料夾> --write`。若只用 ChatGPT，可略過 `setup all`，並在專案 **設定**選 `chatgpt-only`，或執行 `ccc-mcp --project hello-demo config profile chatgpt-only`。

對原生角色而言，`task usage` 是確認 CLI 是否真的被呼叫的依據。ChatGPT 角色是互動式執行，`task usage` 會標示 `"interactive": true` 與 `invocation.source: "chatgpt_web"`，其 `cli_invoked: false` 是預期結果。[CLI 參考](docs/ch/8-cli-reference.md)列出完整命令。

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

持續寫入授權——不論是透過 `project authorize`、`project add --write`／`project create --write`，或 Web 控制台的「啟用寫入」——都允許 ccc-mcp 修改該專案並執行設定的測試命令。相依套件安裝可能執行 package script，因此相依變更也必須納入 diff 審閱。Codex 依設定的 sandbox 執行；Claude Code 雖有限制工具政策，但尚未有同等的 OS 強制 sandbox，應以最低權限的 OS 帳戶執行。

只想從 `project list` 移除專案？執行 `ccc-mcp project remove <id>`，預設保留原資料夾，封存 ccc-mcp 歷史後仍可重用該 ID。確定連原資料夾也要永久刪除時，才加上 `--delete-files`；兩種模式會要求不同的確認文字，執行中的 Agent 任務必須先結束或取消。`project delete` 是另一種操作：永久註銷 ID、保留原資料夾及歷史證據，不可重用。完整說明見[可靠性與安全邊界](docs/ch/7-reliability-and-security.md)。

## 開發

```bash
python3.11 -m pytest -q
```

本專案要求 Python 3.11 以上。真實 agent 測試需要本機已安裝且登入的 CLI，預設不會執行；詳見[安裝與登入](docs/ch/1-install-and-sign-in.md)。
