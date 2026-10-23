# 6. 安裝與使用 Skill

Skill 是提供給所有角色的可攜指引：一份 `SKILL.md` 可附參考檔、腳本或資產。它不是 MCP server，也不會替 agent 自動安裝 CLI 或執行腳本。

## 安裝與管理

```bash
ccc-mcp skill install ./my-skill
ccc-mcp skill install https://example.com/my-skill.zip
ccc-mcp skill list
ccc-mcp skill remove my-skill
```

Skill 全域安裝在 `~/.ccc-mcp/skills/<id>/`，所有專案與角色共用。內容會進入 agent prompt；只安裝信任來源。

## Skill 何時可見

它會出現在 `bootstrap_project`、`get_context` 及每次派工回傳的 `available_skills`。使用與否由當次 agent 自行判斷；它是 guidance，不是強制規則。

Context 太大時，ccc-mcp 會先省略 Skill 全文、保留名稱和說明；若仍超過設定的預算，會拒絕派工而不是默默裁切關鍵 Context。詳見[可靠性與安全邊界](7-reliability-and-security.md)。

## 額外檔案與執行限制

| 安裝來源 | 帶入的檔案 |
| --- | --- |
| 本機資料夾 | 整個資料夾及子目錄 |
| ZIP | 第一個找到的 `SKILL.md` 所在資料夾 |
| 單一 `SKILL.md` URL | 只有該 Markdown 檔 |

只有 Skill 文字會被放進 Context；其他檔案以安裝路徑供 agent 自行讀取。Claude Code adapter 沒有 Bash 工具；Codex 的 shell 也受 sandbox 及 PATH 限制；ChatGPT 角色只能使用 ccc-mcp 提供的 MCP 工具。因此 Skill 中的「執行某指令」不是保證能完成的動作。

Skill 不會把第三方 MCP 一起安裝或暴露給被派工的 agent。若需要圖譜、記憶等 MCP，讓 ChatGPT PM 以自己的 connector 取得資料，再用可驗證的文字與檔案證據交給下游角色。

## 多層 Skill：SKILL.md 導向其他文件

`SKILL.md` 可以把 agent 導向更深的檔案（`SKILL.md` → `references/level2.md` → `references/deeper/level3.md`），但有三條規則決定它行不行：

1. **相對路徑是相對於 Skill 的 `path`，不是專案。** agent 的工作目錄是專案，所以請在 `SKILL.md` 寫明「以下路徑相對於本 skill 在 `available_skills` 的 `path`」。
2. **只有 `SKILL.md` 的文字會被內嵌，更深的檔案要 agent 自己去讀。** Claude Code（`Read,Glob,Grep`）與 Codex（唯讀沙箱）可以讀檔；ChatGPT 角色需要先把 skills 資料夾開成讀取根目錄（`ccc-mcp --project <id> config read-root skills ~/.ccc-mcp/skills`，再用 `read_workspace` 的 `root_id: skills`）。腳本不會被代為執行（見上）。
3. **設了 `context_token_budget` 時，Skill 本文最先被省略**，第 1 層也會跟著消失。

現成的測試在 [`examples/skills/demo-multilevel-skill`](../../examples/skills/demo-multilevel-skill/SKILL.md)：暗號 `MULTILEVEL-OK-9c41` 只存在於最深一層，所以 agent 要讀完三層才說得出來。

```bash
python -m pytest -q tests/test_skills_multilevel.py          # 參照鏈可解析、暗號只在第 3 層（不需 agent）
ccc-mcp skill install examples/skills/demo-multilevel-skill
ccc-mcp --project <id> context show                          # available_skills 內看得到此 skill 與其絕對路徑
```

實際執行並通過的 ChatGPT 對話：[分享的對話](https://chatgpt.com/share/6adb126e-2154-83ec-9364-d5a10e06024f)。agent 讀完三層並回報了暗號。

通過時 ChatGPT 的回覆大致如下（節錄）：

```
`hello1.txt` 已成功建立並驗證內容
* 專案： `meeting-test`
* 檔案： `hello1.txt`，內容： `Hello1`
* SA： 已讀取三層 multilevel skill
* SD/RD： 已執行實作
* REVIEW： 已通過，任務狀態：COMPLETED
* 檔案內容經 ccc-mcp `read_workspace` 實際讀取確認
* 三層 Skill 驗證通過，暗號 `MULTILEVEL-OK-9c41`
* 原有檔案未遭覆蓋；未執行 push、merge 或 deploy
```

再用真實流程提需求：「multilevel canary：建立 hello.txt，內容為 Hello」。通過條件：`multilevel_canary.txt` 內有暗號，且 `ccc-mcp --project <id> task result` 的該角色 `summary` 也有暗號。

## MCP 伺服器：讓派出的 agent 即時呼叫本機 MCP

預設情況下，ccc-mcp 派出的 agent 不帶任何 MCP（Claude Code 用 `--strict-mcp-config` 加空清單，Codex 用 `--ignore-user-config`）。要讓某個專案的 agent 用本機的 stdio MCP 伺服器（例如 codebase-memory-mcp），請在終端機安裝：

### 必須事先設定：ChatGPT 自己做不到

這裡沒有任何自動化。在操作者完成下面步驟之前，ChatGPT（以及所有被派出的 agent）都用不到這個 MCP，只會改讀原始碼；ChatGPT 無法自己安裝或建立索引，而且它能呼叫的工具裡也永遠不會有一個獨立的「codebase-memory」工具。

1. **這個工具必須在你的終端機能執行。** 以 codebase-memory-mcp 為例，`codebase-memory-mcp --version` 要能成功（它若在 conda/venv 裡，請先啟用）。
2. **替專案做好前置準備。** codebase-memory-mcp 只能回答它已索引過的 repo：`codebase-memory-mcp cli index_repository '{"repo_path":"/path/to/project"}'`。
3. **在同一個終端機安裝到你要的範圍**：`ccc-mcp --project <id> skill install mcp codebase-memory`（單一專案）或不加 `--project`（所有專案）。用 `--tools` 只啟用你能接受的工具。
4. **重新啟動 `ccc-mcp connect`，再到 ChatGPT 重新整理這個連線的工具清單**（或開新連線／新對話）；ChatGPT 會快取工具清單。
5. **檢查：** 請 ChatGPT 列出它的 ccc-mcp 工具（必須看到 `call_installed_mcp_tool`），再請它使用 MCP，並用 `ccc-mcp mcp calls` 確認。`bootstrap_project` 也會回報 `installed_mcp`，在沒有安裝時會要 ChatGPT 告訴你需要做這些設定。

第 3、5 步與日後管理安裝會用到的指令：

```bash
ccc-mcp skill install mcp codebase-memory                  # 沒有 --project ＝ 全域：所有專案的 agent 都拿得到
ccc-mcp --project <id> skill install mcp codebase-memory   # 只給這個專案
ccc-mcp skill install mcp codebase-memory --tools search_graph,get_architecture   # 只啟用部分工具
ccc-mcp mcp list                                           # 全域安裝與各專案自己的安裝，以及你各個 MCP 客戶端（Claude Code/Desktop、Codex、VS Code、Cursor）定義的伺服器
ccc-mcp --project <id> mcp list                            # 該專案的 agent 實際拿到的（每個標示 global／project）
ccc-mcp mcp calls                                          # ChatGPT 經由 ccc-mcp 做過的 MCP 呼叫證據（參數摘要與結果狀態，不含回傳內容）
ccc-mcp mcp remove codebase-memory                         # 移除全域安裝
ccc-mcp --project <id> mcp remove codebase-memory          # 移除該專案自己的安裝
```

範圍規則在每個指令都一樣：只有明確寫出的 `--project` 才會縮小範圍，絕不從目前所在目錄猜測，因為全域安裝等於把工具授權給所有專案。專案安裝了與全域同名的伺服器時，對該專案而言以專案的為準（包含它自己啟用的工具清單）。專案無法移除全域安裝；請不加 `--project` 來移除。

安裝時會先確認指令存在、完成真正的 MCP 握手，列出它提供的工具（附唯讀標示），並記下指令的**完整路徑**。如果這個指令只有在 conda/venv 環境裡才找得到（例如 `conda activate stock311` 之後才行、`base` 不行），請在已啟用該環境的終端機執行安裝，或用 `--command /完整路徑`；因為路徑已被記下，之後啟動的 `ccc-mcp connect` 與 worker 不需要再啟用該環境。名稱可以是客戶端對該伺服器的命名，或指令名稱（加不加 `-mcp` 皆可）。

這會改變什麼、不會改變什麼：

- **三種 provider 都支援。** Claude 以 `--mcp-config` 傳入並只放行啟用的工具（`--allowedTools`）；Codex 以 `-c mcp_servers.<名稱>.*` 覆寫傳入，搭配 `enabled_tools`，再加上 `required=true`（伺服器可能要數秒才啟動，例如 codebase-memory-mcp，否則 Codex 會在沒有工具的情況下開始這一輪）與自動核准（這個流程使用 `-a never`，Codex 會拒絕任何需要核准的 MCP 呼叫）。兩者都已用真實的 codebase-memory-mcp 驗證。ChatGPT（PM 對話以及任何 `chatgpt` 角色）不是 ccc-mcp 啟動的程序，沒辦法交給它一份設定，所以改由 ccc-mcp 的兩個工具轉接：`list_installed_mcp_tools` 與 `call_installed_mcp_tool`。呼叫由 ccc-mcp 自己執行，且只限本機 CLI 啟用過的伺服器＋工具組合；請求內容無法新增伺服器、指令或工具。伺服器在呼叫之間會維持執行（每個專案一個程序，閒置 5 分鐘後關閉），所以只有第一次呼叫要等啟動。回傳內容視為不可信的資料。因為只有一份安裝與一份啟用工具清單，Claude、Codex、ChatGPT 三者用的是同一個 MCP、同一種方式。
- **套用到該專案所有角色（SA、SD/RD、REVIEW，以及 ChatGPT PM）。** 包含唯讀角色：MCP 工具能做什麼取決於該伺服器，與角色的唯讀檔案工具無關。請檢視安裝時列出的工具清單（`read_only: false` 的工具，例如會刪除索引的），並用 `--tools` 只啟用你能接受的。
- **該專案的 Claude 不再帶 `--safe-mode`**，因為 `--safe-mode` 會連 MCP 伺服器一起關掉。它原本關閉的項目改成逐項關閉：設定與 hooks（`--setting-sources ""`）、skills（`--disable-slash-commands`）、`CLAUDE.md` 與自動記憶（環境變數）。沒有安裝 MCP 的專案不受影響，仍使用 `--safe-mode`。
- **只有本機 CLI 能安裝。** 沒有對應的 MCP 工具，專案的 `settings.json` 也不是這個設定的匯入來源，所以 ChatGPT 或儲存庫裡的檔案都無法加入一個會在派工時被執行的指令。客戶端設定裡的環境變數一律不會被複製。
- 不支援遠端（HTTP）伺服器；指令必須啟動 stdio 伺服器。

[`examples/skills/demo-mcp-skill`](../../examples/skills/demo-mcp-skill/SKILL.md) 是 canary：提需求時說「mcp canary」；有 MCP 的 agent 會回報真實的專案名稱，沒有的必須回報 `MCP-UNAVAILABLE` 並列出它實際擁有的工具。

實際畫面長這樣：ChatGPT 透過 ccc-mcp 使用已安裝 MCP 的真實對話：[分享的對話](https://chatgpt.com/share/6adb5730-df44-83e8-aa53-78a5e9504920)。即使看到這樣的對話，也請用 `ccc-mcp mcp calls` 確認呼叫真的發生過。

### 替代做法：把 MCP 的輸出存成 Skill 快照

如果你不想讓 agent 即時存取，或想要一份固定不變的視圖，任何角色都可以讀檔案。`skill install` 可以在你的機器上執行它的 CLI 一次，把結果存進 Skill 的 `references/`；`SKILL.md` 本身很短，只列出這些檔案，所以 agent 會用上面的多層機制，只讀需要的部分。

```bash
ccc-mcp --project <id> skill install codebase-memory:   # 使用該專案的根目錄
ccc-mcp skill install codebase-memory:/path/to/repo             # 或明確指定任一 repo
```

若該 repo 還沒被 codebase-memory-mcp 索引（它只能回答已解析進圖資料庫的 repo），指令會先自動建立索引並回報 `indexed_now: true`，這會寫入該工具自己的快取。執行檔的尋找順序：`--cbm /完整路徑`、`PATH`、macOS 上它的啟動器下載的快取目錄。這是「當下的快照」，不是即時索引：程式碼變動後再執行一次同一個指令。Skill 是全域的，每個已安裝 Skill 的 `SKILL.md` 文字都會送給每個專案的每個角色，所以產生出來的 `description` 會寫明是哪個 repo，並告訴 agent 其他專案請忽略。驗證方式：提需求「snapshot canary：回報 architecture 快照裡的 Function 數量」，再跟 `~/.ccc-mcp/skills/codebase-snapshot-<name>/references/architecture.md` 裡的 `Function` 數字比對。
