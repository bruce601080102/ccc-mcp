# ccc-mcp

讓 ChatGPT 當專案的 PM，將分析交給 Codex、實作交給 Claude Code，再把成果整理成下次可沿用的共用 Context。你只要註冊並授權專案，之後直接提出需求；不用自己管理 Task ID、逐次核准計畫或驗收。

預設流程：**ChatGPT（PM）→ Codex（SA）→ Claude Code（SD/RD）→ Codex（REVIEW）→ ChatGPT 整理 Context**。真正派工、檔案修改和 Context 更新都應留下紀錄，不能只憑聊天回覆判定成功。

## 快速開始（Windows Git Bash／macOS／Linux）

需要 Git、Python 3.11+，以及可登入的 Codex／Claude Code 帳戶。連接 ChatGPT 另需可使用 Tunnel 的 OpenAI Platform 帳戶。以下全部以 `hello-demo` 為例。

### 1. 安裝並登入

Windows 用 Git Bash；macOS／Linux 用系統內建的 bash 或 zsh（macOS 預設終端機就是 zsh，可直接用）。四個系統都執行同樣的指令：

```bash
git clone https://github.com/bruce601080102/ccc-mcp.git
cd ccc-mcp
source scripts/setup-real-agents.sh
ccc-mcp setup all
```

第一步只建立 `.venv`、把 ccc-mcp 本身裝起來（這是唯一需要腳本的地方：`ccc-mcp` 指令此時還不存在）。`ccc-mcp setup all` 接手檢查／安裝、登入 Codex 與 Claude Code，純 Python 執行，不需要先 `source` 任何東西。若安裝 Node.js 後提示重開終端，開新的終端機，在 `ccc-mcp` 目錄重跑 `ccc-mcp setup all` 即可。

`source scripts/setup-real-agents.sh` 必須用 `source`（不能直接執行 `./scripts/...`），且必須在 `ccc-mcp` 目錄下執行；腳本會自動找系統裡 3.11 以上的 Python 建立 venv。macOS 若只裝了 Anaconda 或系統內建的舊版 Python（`python3 --version` 低於 3.11），先用 Homebrew 裝一個新版：`brew install python@3.12`，腳本會自動挑到它，不需要手動切換 PATH。

### 2. 建立並授權專案

```bash
ccc-mcp project create hello-demo
ccc-mcp project authorize hello-demo
```

第二個指令會顯示實際路徑，確認後輸入 `hello-demo`。預設新專案位於使用者目錄的 `ccc-mcpProjects/hello-demo`。授權持續有效，直到你撤銷；它允許工作流程修改此專案，不會授權專案外的檔案。

**想換預設位置？** 兩種情境：

- 只是這一個專案想放別的地方，用 `--path`，不會動到全域預設：

  ```bash
  ccc-mcp project create hello-demo --path "/Users/你的帳號/Code/hello-demo"
  ```

- 以後所有新專案都要改成另一個目錄，用 `config projects-dir`——這是全域設定、不用帶 `--project`，之後每次 `project create`（不手動指定路徑時）都會用這個新位置：

  ```bash
  ccc-mcp config projects-dir "/Users/你的帳號/Code/ccc-mcp-projects"
  ```

  只影響之後新建的專案，已經建好的專案不會被搬動。

若 `hello-demo` 資料夾**已經存在但尚未註冊**，不要再執行 `project create`，改用：

```bash
ccc-mcp project add "C:/Users/你的帳號/Desktop/hello-demo" --id hello-demo
ccc-mcp project authorize hello-demo
```

Windows 路徑也可用引號包住反斜線寫法（例如 `"C:\Users\你的帳號\Desktop\hello-demo"`）；在 Git Bash 使用 `/` 比較容易複製。若 ID 已註冊，直接沿用即可，用 `ccc-mcp project list` 查路徑。

### 3. 連接 ChatGPT

先依[連線指南](docs/chatgpt.md)建立 Tunnel、取得 `tunnel_...` ID 與 runtime API key，再於**同一個終端機**（已 `source` 過 setup 腳本的那個）執行：

```bash
ccc-mcp connect tunnel_你的實際ID
```

金鑰只在終端的隱藏輸入欄填入，不要貼到聊天。保持這個終端開啟；在 ChatGPT 安裝並選用對應的 ccc-mcp 連線。`connect` 會檢查本機 CLI 與 Tunnel；若檢查失敗，先處理錯誤再開始工作。

### 4. 提出需求，然後在本機驗證

在已選用 ccc-mcp 的 ChatGPT 對話說：

> 請在 hello-demo 建立 `hello.py`，執行時輸出 `Hello, World!`，並加入驗證輸出的測試。

不必在提示詞要求「使用 Codex」或「更新 Context」；這是 ccc-mcp 工作流程的責任。完成後在終端機查實際狀態：

```bash
ccc-mcp --project hello-demo task status
ccc-mcp --project hello-demo task usage
ccc-mcp --project hello-demo context show
python "$HOME/ccc-mcpProjects/hello-demo/hello.py"
```

`task usage` 應有 Codex／Claude 的執行紀錄及 `cli_invoked: true`；若是 `false`、執行失敗或沒有紀錄，就不能說該 Agent 已實際執行。`context show` 可確認新版共用 Context；專案內的 `.ccc-mcp/CONTEXT.md` 是可閱讀的摘要，完整原始報告與執行證據保留在 `.ccc-mcp/tasks/`。最後一行預期輸出 `Hello, World!`；若你用自訂路徑建立專案，請將它換成 `project list` 顯示的實際路徑。聊天回覆中的「測試通過」也應以實際測試結果為準。

## 想調整角色或語言？

預設 SA／REVIEW 使用 Codex，SD／RD／SD/RD 使用 Claude Code。`SA` 分析、`SD/RD` 設計與實作、`REVIEW` 審查；ChatGPT 負責協調及每輪 Context 整理。先查看設定，再依需要改：

```bash
ccc-mcp --project hello-demo config show
ccc-mcp --project hello-demo config role REVIEW codex --effort high --quota-fallback chatgpt
ccc-mcp --project hello-demo config language ch
```

`--model 模型ID` 與 `--effort` 都可省略，交由本機 CLI 使用預設值；它們不會設定 ChatGPT 網頁模型。`--quota-fallback chatgpt` **只在 CLI 額度不足時**備援，不會修復 CLI 未安裝、未登入或一般執行錯誤。每次 `config role` 會整組取代該角色設定，要保留的選項請一起填。語言預設 `zh-TW`（繁體中文），也可設 `ch`（簡體中文）或 `en`（英文）；切換不會翻譯既有紀錄。

也可直接編輯 `hello-demo/.ccc-mcp/settings.json` 的 `roles` 或 `language`；儲存後，下次查詢設定或派工時會驗證並同步到 SQLite，不用重啟。先用 `ccc-mcp --project hello-demo config show` 確認生效值。格式錯誤會停止派工並顯示錯誤；`project_access` 等授權欄位不能靠改檔取得權限，仍須在本機執行 `project authorize`。

只安裝 Codex 時，可把 `SD/RD` 也改成 `codex`；不用本機 CLI 時，可選 `chatgpt`，但這就**不是** Codex／Claude 的真實執行。更多選項見[角色設定](docs/configuration.md)。

不確定目前設定值、或想一次看懂「哪些是我能改的」跟「哪些是系統授權狀態」，用：

```bash
ccc-mcp --project hello-demo config providers
```

這個指令會分開顯示可編輯設定（roles／language／test_command）與唯讀的授權狀態，並列出目前支援的 provider（`ADAPTERS` 註冊了哪些）及各自支援的 effort。**注意**：自動流程（`advance_project`）只會派合併角色 `SD/RD`，從不單獨派 `SD` 或 `RD`；單獨改 `config role SD ...` 不會影響自動派工，只有你自己手動呼叫 `dispatch_agent(role="SD")` 才會用到。未來要新增其他 provider（例如 Grok、地端模型），只需要新寫一個 `adapters/agents/<name>.py` 繼承 `BaseAgentAdapter`、在 `ADAPTERS` 註冊一行，這裡就會自動列出來，不用改其他地方。

想知道每個角色實際會收到什麼提示文字，不用真的派工也能看：

```bash
ccc-mcp --project hello-demo config prompt SA
ccc-mcp --project hello-demo config prompt REVIEW
```

會印出該角色目前指到的 provider、唯讀或可寫、以及三段實際會送出的文字——Adapter 的操作政策、（Codex 限定）角色附加說明、Context 交接的政策文字。這份輸出直接重用派工時真正呼叫的同一批函式組成，不是另外寫一份說明文件，所以不會跟實際送出的內容不同步。

## Context 交接太大？設定 token 預算

長期運作的專案，交接給 Agent 的 Context 可能累積很多歷史決策／報告。預設不限制大小；要設定上限：

```bash
ccc-mcp --project hello-demo config context-budget 150000
```

超過預算時，ccc-mcp 會壓縮已驗證的歷史清單（決策、待整理的 Agent 結果），只保留最近幾筆並改成一行摘要＋指向 `get_context_history` 的指標；**不會**裁切 `invariants`、來源引用或當前任務的實際報告／核准方案。回傳的 `budget_omitted` 會列出到底省略了什麼，不會讓你猜。清除設定用 `ccc-mcp --project hello-demo config context-budget --clear`。

## 想要「完成」前自動跑測試？

`project authorize`（第 2 步的持續授權）同時代表你同意：在每輪實作完成、REVIEW 派工之前，ccc-mcp 會在本機自動執行你設定的固定測試命令，並把結果存成 `TEST-RESULT.md` 證據。預設**不會**自動跑任何測試，直到你明確設定：

```bash
ccc-mcp --project hello-demo config test-command "pytest -q"
```

沒有設定、或專案尚未 `project authorize` 時，`ccc-mcp --project hello-demo task status` 的 `brief.tests` 會明確顯示 `executed_by_host: false` 及原因，不會被當成「已測試通過」；這與 Agent 本身不執行測試是同一件事——測試永遠是本機實際執行、有紀錄的動作，不是聊天回覆自稱的結果。想清除設定，用 `ccc-mcp --project hello-demo config test-command --clear`。這跟手動的 `ccc-mcp task test`（一次性、需要在終端機確認）是兩條獨立路徑，互不影響。

## 安裝本機 Skill（選用）

Skill 是一份 `SKILL.md`（說明用途與觸發時機，可附帶輔助腳本），裝好之後**不分 Codex、Claude 還是 ChatGPT，每次派工都會自動看到**，由 Agent 自己判斷要不要用：

```bash
ccc-mcp skill install ./my-skill          # 本機資料夾
ccc-mcp skill install https://example.com/my-skill.zip
ccc-mcp skill list
ccc-mcp skill remove my-skill
```

Skill 是全局安裝（`~/.ccc-mcp/skills/<id>/`），裝一次所有專案都能用。從 URL 安裝時內容會直接進入 Agent 的 prompt，只安裝你信任的來源。

**實際生效的時機**：Skill 內容附在 `bootstrap_project`、`get_context`，以及每次派工（`start_project_request`／`advance_project`／`dispatch_agent`）回傳的 `available_skills` 欄位裡。真正執行角色的是誰，就是誰看到——SA/SD/RD/REVIEW 設成 `codex`／`claude` 時由本機 CLI 看到；設成 `chatgpt` 時（`ccc-mcp --project 你的專案ID config show` 可查），就是 ChatGPT 自己要照著做。

**實務上要注意**：Skill 要不要用，完全由 Agent 自己判斷，ccc-mcp 不強制。跟這次任務的程式碼或分析工作直接相關的 skill（例如「一律加型別標註」「commit 訊息要符合某種格式」）比較容易被真的採用，因為會自然融進它本來就要產出的東西；跟任務內容無關的格式性要求（例如「回覆開頭先打招呼」）很容易被模型當成次要資訊忽略，不代表機制壞掉。改完 Skill 內容後記得 `ccc-mcp connect` 重啟才會生效。

### 如果 SKILL.md 還引用了其他檔案

三種安裝方式，對「SKILL.md 以外的檔案」處理不一樣：

| 安裝方式 | 額外檔案（腳本、`references/`、`assets/`…） |
|---|---|
| `ccc-mcp skill install ./資料夾` | 整個資料夾底下全部帶走，含多層子目錄 |
| `ccc-mcp skill install https://.../xxx.zip` | 只帶走**找到的第一個 `SKILL.md` 所在資料夾**底下的檔案；該資料夾以外（例如整包 repo 根目錄共用的東西）不會跟著裝 |
| `ccc-mcp skill install https://.../SKILL.md`（單一檔案） | 只有這份文字，**其他檔案完全不會跟著裝**；SKILL.md 裡任何「見 `xxx.md`」「執行 `scripts/xxx`」之類的引用都會變成裝不到 |

如果 repo 是多個 skill 放在一起的 monorepo（同一包裡有不只一個 `SKILL.md`），用整包 `.zip` 安裝會抓到哪一個是不確定的——先 `git clone` 下來，直接把 `ccc-mcp skill install` 指向你要的那一個子資料夾最保險。不確定裝有沒有裝全，直接看實際裝到哪裡：`ls -R ~/.ccc-mcp/skills/<skill-id>/`。

### 如果 SKILL.md 要求執行 CLI 指令

Skill 的文字內容（連同它的絕對安裝路徑）會被整個序列化成 JSON，透過 stdin 餵給 CLI 當 prompt——**只有文字進了 prompt，skill 資料夾裡的其他檔案本身並沒有跟著塞進 context**，Agent 要自己依路徑去找。而這個路徑在 `~/.ccc-mcp/skills/...`，跟這次任務的 Git worktree 是不同地方，能不能讀到、能不能執行，要看派給誰：

- **Claude Code**（SA/REVIEW 或 SD/RD 皆同）：adapter 直接把可用工具鎖定在 `Read,Glob,Grep`（唯讀角色）或 `Read,Edit,Write,Glob,Grep`（實作角色），**完全沒有 Bash 工具**。Skill 寫「請執行 xxx」，Claude Code 連嘗試都辦不到，這是刻意的安全限制。
- **Codex**：`codex exec` 本身有 shell 能力，但受 sandbox 限制——SA/REVIEW 是 `read-only`（能跑指令、不能寫檔），SD/RD 是 `workspace-write`（寫入侷限在那次任務的 worktree）。即使如此，**那個 CLI 工具必須早就裝在跑 worker 的那台機器、且在 PATH 裡**；`ccc-mcp skill install` 只負責複製檔案，不會幫你 `npm install`／`pip install`／加 PATH。另外目前裝進去的額外檔案**不會保留執行權限**（即使 zip 裡原本是 `+x`），Codex 真要跑 skill 自帶的腳本，得自己先 `chmod +x`。
- **`chatgpt` 角色**：沒有 shell，只有 ccc-mcp 自己的 `read_workspace`／`write_workspace` 等 MCP 工具，一樣不能跑任意 CLI。

### 不支援：MCP 類型的工具（CodeGraph、Memory MCP 之類）

Skill 只能是文字＋檔案，**不是** MCP server，`ccc-mcp skill install` 沒有也不會支援裝這種東西。更進一步：就算你自己平常的 Codex／Claude Code CLI 已經接了 CodeGraph 或 Memory MCP，ccc-mcp 派工時也完全看不到——Claude adapter 直接傳 `--mcp-config '{"mcpServers":{}}'`（空清單），Codex adapter 傳 `--ignore-user-config`（跳過你平常登記這些 MCP server 的 `~/.codex/config.toml`）。這是故意的信任邊界：被派工的 Agent 讀的是不可信的 repo 內容，不該連帶繼承你個人環境裡設定的強力工具。

如果你想用這類工具，目前唯一的路是讓 **ChatGPT（PM）自己接**（這是 ChatGPT 自己的 connector 設定，跟 ccc-mcp 無關，要唯讀就在那邊只開讀取 scope），查到的東西整理成文字後，透過 `instruction` 參數轉交給下游的 Codex／Claude——`instruction` 是自由文字，沒有格式限制。如果想讓這個結論變成長期保留在共用 Context 的「既定事實」（而不只是這次派工用一次），走 `propose_context_patch` 時必須附上專案裡真實檔案的路徑＋SHA256 當佐證（`verify_refs` 會重新讀檔比對雜湊），不能只憑「CodeGraph 說的」就寫成定論。

## 安全邊界：Claude 角色目前沒有等同 Codex 的 OS 沙箱

Codex 角色的邊界是作業系統強制的：`--sandbox read-only`（SA／REVIEW）或 `workspace-write`（SD/RD），寫入實際被系統侷限在那次任務的 worktree，即使 Codex 自己想寫到別的地方也做不到。Claude 角色目前**沒有等價的強制邊界**：Adapter 把可用工具鎖在 `Read,Edit,Write,Glob,Grep`、工作目錄設成該次任務的 worktree（[claude.py](src/ccc_mcp/adapters/agents/claude.py)），但這是「CLI 工具自己遵守 `cwd`」這個慣例，不是作業系統層級強制的沙箱——沒有實測到真的能越界寫出 worktree 外，但也沒有證據證明不能，這是已知、尚未補強的風險，不是「已驗證安全」。

ccc-mcp 自己這一層能強制、也已經用回歸測試釘住的，是 project_id／task_id／檔案路徑一律經過嚴格格式驗證（[identifier](src/ccc_mcp/core/context.py:36)）與路徑穿越防護（[safe_path](src/ccc_mcp/infra/security.py:15)），確保派工時解析出的 worktree 路徑本身不會被偽造的 ID 牽到專案目錄之外；但這不等於「Claude CLI 執行期間不會自己寫到 cwd 以外」。縱深防禦建議：以最低權限的系統帳號跑 ccc-mcp 與其 worker，不要用你平常登入、擁有完整個人檔案存取權的帳號。

幫 Claude 角色補上等價的 OS 級隔離（例如 Windows Sandbox 或容器化 worktree）是已知的後續項目，需要另外決定要用哪種機制，不在目前版本範圍內。

## 整合驗收：不要只信模型自己說「測試通過」

本機已裝好、登入 Codex 與 Claude CLI 時，有一支會真的跑完整條流程（Codex SA → Claude SD/RD → 主機執行測試 → Codex REVIEW）、且只憑後端狀態判斷有沒有完成的腳本：

```bash
.venv/Scripts/python.exe -m pytest -m real_agents tests/test_real_agents.py -v
```

細節與「尚未 `project authorize`」時手動逐步核准的操作方式，見 [docs/real-agents.md](docs/real-agents.md)。

## 遇到問題

- `Project ID is already registered`：用 `ccc-mcp project list` 找到原專案並沿用，不要刪資料重建。
- ChatGPT 顯示 `Unknown tool`：先執行 `ccc-mcp update --dev`（原始碼目錄）或 `ccc-mcp update`（一般安裝）更新，重啟 `connect`，在 ChatGPT 重新整理連線工具並開新對話；不要把舊工具清單當成目前版本。
- 派工失敗：用 `task status`、`task usage` 查失敗階段；缺少 CLI 或登入失敗不會自動冒充成 ChatGPT 執行。
- `--quota-fallback` 設定沒有自動切換：這個設定是每個專案各自獨立的，確認指令裡的 `--project` 是你實際在用的那個專案。
- 任務狀態一直是 `RUNNING`：真正派工是背景執行，寫程式可能要花數分鐘；`task status` 會顯示已耗時與逾時上限，沒超過就是正常，過一下子再查一次即可。
- 任務狀態變成 `BLOCKED`：不是失敗。系統只能自動驗證 `.py` 檔的註解有沒有被誤刪，改到其他類型的檔案會保守地停下來等你看過，照常讓下一個角色接手即可。

要停止 ccc-mcp 寫入 `hello-demo`：

```bash
ccc-mcp project revoke hello-demo
```

其他細節見[安裝與 Agent](docs/real-agents.md)、[ChatGPT 連線](docs/chatgpt.md)及[CLI 指令](docs/cli-reference.md)。
