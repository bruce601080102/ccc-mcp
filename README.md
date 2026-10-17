# AgentBridge

在 ChatGPT 網頁提出需求，讓 Codex 分析、Claude Code 開發；AgentBridge 在你的電腦保存任務、共享 Context 與原始報告。

## 實際做一次：Hello World

這次要做一個 Python 程式，執行後印出 `Hello, world!`，並讓 Agent 一起寫測試。

整篇使用同一個專案 **hello-demo**：

| 項目 | 本次使用的設定 |
|---|---|
| 專案與報告 | `/c/Users/user/Desktop/專案/agentbridge/.demo/hello-demo` |
| 產生的程式 | `/c/Users/user/Desktop/專案/agentbridge/.demo/worktrees/hello-demo/TASK-0001` |
| 分析（SA） | Codex，CLI 預設模型，high 思考等級 |
| 設計與開發（SD/RD） | Claude Sonnet，high 思考等級 |
| 額度不足 | 交給目前網頁對話中的 ChatGPT 接手 |

程式會放在獨立的開發副本（Worktree），所以專案目錄與程式位置不同。目前不支援直接寫回專案根目錄；你可以把上面的 `/c/Users/user/Desktop/專案/agentbridge/.demo` 換成想用的位置。

### 1. 在電腦建立專案

先準備 Python、Git，以及已安裝並登入的 Codex／Claude Code。登入方式見[安裝指南](docs/real-agents.md)。

開啟 **Windows Git Bash**，進入 AgentBridge 原始碼資料夾後執行：

```bash
# 第一次安裝才需要這兩行
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -e ".[dev]"

source scripts/enable-real-agents.sh

agentbridge project create hello-demo --path "/c/Users/user/Desktop/專案/agentbridge/.demo/hello-demo"
agentbridge --project hello-demo config worktree-dir "/c/Users/user/Desktop/專案/agentbridge/.demo/worktrees"

agentbridge --project hello-demo config role SA codex --effort low --quota-fallback chatgpt
agentbridge --project hello-demo config role SD/RD claude --model sonnet --effort low --quota-fallback chatgpt

```

這裡還不會產生 Hello World，只是建立專案並設定分工。新專案目錄必須尚不存在；如果已經做過這個範例，直接接續原任務，不要重建。

`SA` 是分析角色，`SD/RD` 是一起設計與實作；`--model` 選模型，`--effort` 選思考等級，`--quota-fallback` 選額度不足時的接手者。[完整角色與參數表](docs/cli-reference.md)

### 2. 連接 ChatGPT

繼續在同一個 Git Bash 執行，把 `tunnel_你的ID` 換成自己的 Tunnel ID：

```bash
bash scripts/connect-chatgpt.sh tunnel_你的ID
```

第一次還沒有 ID，先完成[連線指南](docs/chatgpt.md)。保持這個終端開著，後面的本機操作使用另一個 Git Bash；不用另外執行 `agentbridge serve`。

### 3. 在 ChatGPT 說要做什麼

在已連接 AgentBridge 的 ChatGPT 網頁對話貼上：

```text
請使用 AgentBridge 恢復 hello-demo，建立一個 Hello World 任務。

需求：
- 新增 hello.py，執行後只輸出 Hello, world! 並換行。
- 新增 tests/test_hello.py，用 subprocess 驗證輸出、退出碼與 stderr。
- 更新 README，說明怎麼執行，不增加第三方業務依賴。

依角色設定使用 agent=auto、adapter=real：
先派 SA 分析，閱讀原始報告並提交精修 Context，
再提出包含 SD/RD 實作的 PLAN-A，等待我本機核准。
如果額度不足，接手已排好的 ChatGPT 工作，不要重複派工。
```

你會看到任務 ID、SA 原始結果，以及等待核准的計畫。以下假設任務是 `TASK-0001`、計畫是 `PLAN-A`；實際不同時，請使用回覆中的 ID。

不必背固定 Prompt。說「恢復 hello-demo」就能請 ChatGPT 載入專案；`agent=auto` 表示依你設定的角色選執行者，`real` 表示真實執行。若仍在執行，說「查詢 hello-demo 的 TASK-0001 進度，不要重新派工」。

### 4. 在電腦核准，讓 Claude 開始實作

**另開 Git Bash，先進入 AgentBridge 原始碼資料夾**，執行：

```bash
source scripts/enable-real-agents.sh
cd "/c/Users/user/Desktop/專案/agentbridge/.demo/hello-demo"

agentbridge --project hello-demo approve TASK-0001 --plan PLAN-A
```

你已在 hello-demo 目錄，CLI 會自動辨識專案，不用每次寫 `--project hello-demo`。閱讀計畫並依終端提示確認，再回 ChatGPT 貼上：

```text
hello-demo 的 TASK-0001 / PLAN-A 已完成本機核准。
請確認核准有效，以 agent=auto、role=SD/RD 繼續實作，不重跑 SA。
完成後讀取原始報告，提供程式位置與用量。
先不要提交新版 Context，等待本機測試與驗收。
```

### 5. 找到程式、跑測試

等實作結束，在第二個 Git Bash 執行。以下明確指定 hello-demo，從 AgentBridge 根目錄執行也不會查到另一個專案：

```bash
agentbridge --project hello-demo task show TASK-0001
python "/c/Users/user/Desktop/專案/agentbridge/.demo/worktrees/hello-demo/TASK-0001/hello.py"
agentbridge --project hello-demo task test TASK-0001
```

`show` 一次查看狀態、成果摘要、Worktree 與可取得的 token 用量；只有一個任務時可省略編號。`test` 會在本機確認後，使用 AgentBridge 安裝環境的 pytest 執行任務 Worktree 中的測試。

預期程式輸出：

```text
Hello, world!
```

測試結果應顯示通過。若失敗，把結果交給 ChatGPT 修正，先不要驗收。需要查 CLI 路徑、PID、模型與完整用量紀錄時，執行 `agentbridge --project hello-demo task usage TASK-0001`；未取得的資料會標示未知。不同專案可以都有 TASK-0001，省略 --project 時會依目前目錄選專案；Hello World 能直接執行，不代表 CLI 已選到它的專案。

### 6. 驗收並保存共享 Context

測試通過、成果符合需求後，在第二個 Git Bash 執行：

```bash
agentbridge --project hello-demo task accept TASK-0001
```

接著告訴 ChatGPT：

```text
hello-demo 的 TASK-0001 已完成本機測試與驗收。
請讀取測試證據，統整成果並提交精修 Context，保存程式位置與下一步。
```

之後開新對話，只要說「恢復 hello-demo，告訴我目前成果與下一步」，就能讀取保存的 Context。程式仍在上述 Worktree；驗收不會自動合併、push 或部署。請保留專案和 Worktree 資料夾。

## 換成自己的專案

- **已有程式**：用 `agentbridge project add "D:/Projects/my-app" --id my-app` 註冊，再請 ChatGPT「恢復 my-app，先分析現有架構」。開發前須有 Git commit；Worktree 只帶入已提交的程式。
- **有外部需求文件**：資料夾已存在時，執行 `agentbridge --project hello-demo config read-root specs "/c/Users/user/Desktop/專案/agentbridge/.demo/specs"`，再告訴 ChatGPT「讀取 specs 的需求文件」。這供 ChatGPT 的檔案工具閱讀，不會自動開放 Codex／Claude 的外部存取。
- **換角色或模型**：看[角色與參數表](docs/cli-reference.md)；路徑、全部交給 ChatGPT、額度接手及共享 Context 詳見[進階設定](docs/configuration.md)。

設定需透過 CLI 修改，`.env.example` 不會自動載入。更新 AgentBridge 程式後，請重啟 Tunnel 並重新整理 ChatGPT 工具清單。

[連線指南](docs/chatgpt.md) · [安裝與登入](docs/real-agents.md) · [詳細指令](docs/reference.md) · [驗證紀錄](VALIDATION.md)
