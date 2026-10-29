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

## 它能做什麼

- 以可設定的 `PM → SA → SD/RD → REVIEW → PM` 角色接力處理工作。
- 保存版本化共用 Context、原始 agent 報告、diff 與主機實際觀察到的測試證據。
- 透過原生 CLI 使用 Codex 和 Claude Code，也可將所有角色指定給 ChatGPT，走 ChatGPT-only workflow。
- 只在辨識為額度耗盡時才啟用 fallback，不會把安裝、登入或一般執行錯誤誤當成額度問題。
- 讓操作者安裝的 `SKILL.md` 指引可供所有角色使用。
- 內建本機 Web 控制台（`ccc-mcp ui`），不用 CLI 也能管理專案、追蹤任務進度、編輯設定。

## 選擇模式

**最佳模型工作流**：ChatGPT 擔任 PM、Codex 負責分析與審查、Claude Code 負責實作。若你三種工具都有，這是預設模式。

**ChatGPT-only workflow**：用 `config profile chatgpt-only` 把全部五個角色（`SA`、`SD`、`RD`、`SD/RD`、`REVIEW`）都設為 ChatGPT——包含獨立的 `SD`、`RD`，`config show` 或手動派工都不會殘留 Codex／Claude。自動工作流本身實際上仍只會接力派發 `SA → SD/RD → REVIEW`。不需要 Codex 或 Claude Code CLI，也不會消耗它們的 CLI 額度；但可用模型、額度與功能一律依你連接的 ChatGPT 帳戶與方案而定，不要假設額度是無限的。

## 快速開始

需求：Git、Python 3.11 以上、OpenAI Platform Tunnel。最佳模型工作流另需已登入的 Codex 和／或 Claude Code 帳戶。

```bash
pip install ccc-mcp

# 只有要使用 Codex 和／或 Claude Code 時才執行：
ccc-mcp setup all

ccc-mcp project create hello-demo --write
```

`--write`會在同一步驟建立專案並授予持續寫入授權，過程中會有一次本機確認，清楚說明這代表什麼（ccc-mcp 可以在 `hello-demo` 底下寫入檔案，並在每輪 REVIEW 前自動執行你設定的測試命令）。省略 `--write`則只會註冊唯讀專案，之後再用 `ccc-mcp project authorize hello-demo` 授權——這個兩步驟的舊用法仍然可用，只是不再是唯一路徑。`ccc-mcp project add <既有資料夾>`也支援同樣的 `--write` 旗標。

比起終端機更想用瀏覽器？`ccc-mcp ui` 會啟動本機 Web 控制台（只綁定 `127.0.0.1`），提供同樣的加入／建立／授權／撤銷／刪除流程、唯讀任務儀表板與設定編輯器——詳見下方[Web 控制台](#web-控制台)。

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

## Web 控制台

```bash
ccc-mcp ui
```

在同一個供應 CLI 與 MCP server 的程序裡啟動本機 Web 控制台——不需要另外 clone、建置或啟動前端，即使從原始碼建置也不需要 Node.js（介面是打包進 `ccc-mcp` wheel 的純 HTML/CSS/JS）。只綁定 `127.0.0.1`，啟動後會印出網址並自動開啟瀏覽器；可用 `--port` 指定埠號（若被占用會清楚回報），省略則自動挑一個可用的；`--no-browser` 可停用自動開啟瀏覽器。按 Ctrl+C 即可停止。它讀取與 CLI、MCP server 相同的 `--home` 資料庫，三者的專案與任務資料永遠同步。

控制台涵蓋：列出／加入／建立專案（含上述合併寫入授權流程）、撤銷與刪除專案、任務儀表板（階段、Agent、等待原因、執行紀錄、usage／CLI 呼叫證據、原始結果）、設定編輯器（provider／角色、profile、報告語言、測試命令、Meeting Mode）、Context、Skills，以及 ChatGPT Tunnel 的 **Connect** 頁面。在 **Skills 與 MCP** 頁面，可探索本機 stdio MCP server、檢查其工具、勾選允許此專案使用的工具、以 JSON 參數呼叫已授權工具、移除專案安裝，並查看最近的 MCP 呼叫紀錄。全域安裝會顯示於頁面，但須用 CLI 移除，因為會影響所有專案。側邊欄可切換繁體中文與英文介面；這項瀏覽器偏好與專案的 Agent／報告語言設定分開。Connect 頁面有即時連線狀態與 log，並可選擇「記住這台裝置上的金鑰」（存在 `~/.ccc-mcp/tunnel/credentials.json`，權限僅限本機帳號；CLI 的 `connect --remember`／`--forget` 也提供同樣能力）。每個破壞性操作（啟用寫入、清除歷史、刪除）都需要輸入與終端機提示相同的確認文字——信任模型（以本機一次性 session token 取代終端機的 TTY 檢查，不落地、不寫入紀錄）詳見[可靠性與安全邊界](docs/ch/7-reliability-and-security.md#web-控制台)。

控制台的 **文件**頁內含隨套件安裝的繁中與英文指南。切換介面語言時，目前開啟的指南與連結會立即切換；任務報告、Context 與專案資料仍保留原始語言。

**專案**頁可建立自訂群組，透過下拉選單或拖放移動專案卡片，並調整群組順序；分類只儲存在本機 `<home>/ui/project_groups.json`，不更動專案路徑或設定。**連線**頁可儲存多筆具名稱的設定，同一 Tunnel ID 也能存不同金鑰；選取設定後按「連線」即可切換，但同時間只執行一組 Tunnel 連線。金鑰儲存後不會再回傳給瀏覽器。

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
