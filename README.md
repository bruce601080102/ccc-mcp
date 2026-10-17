# AgentBridge

在 ChatGPT 提需求，依設定交給 Codex、Claude 或 ChatGPT 分析與開發。任務、Context、原始報告與用量紀錄保存在本機。

## 安裝與連線

Windows Git Bash，在本專案目錄執行；Codex／Claude 按需安裝並登入。

```bash
python -m venv .venv
./.venv/Scripts/python.exe -m pip install -e ".[dev]"
source scripts/enable-real-agents.sh
bash scripts/connect-chatgpt.sh tunnel_你的ID
```

首次 Tunnel 設定看 [連線指南](docs/chatgpt.md)。保持終端開啟；其他 Git Bash 終端先 source 同一腳本，便可使用 `agentbridge` 指令。

## 怎麼用

註冊已有的專案，或建立一個新專案：

```bash
agentbridge project add "D:/你的專案" --id my-project
# 新專案改用：agentbridge project create my-project --path "D:/新專案"
```

在 ChatGPT 說：

> 請恢復 my-project。我的需求是：＿＿＿。依專案角色設定分工，使用 agent=auto；提出計畫等我核准。完成後讀取原始報告、統整共享 Context，回報檔案位置與實際測試結果。

審閱計畫後，在本機執行（ID 以實際回覆為準）：

```bash
agentbridge --project my-project approve TASK-0001 --plan PLAN-A
```

再告訴 ChatGPT「已核准，請繼續」。成果符合需求後，依序測試、驗收：

```bash
agentbridge --project my-project task test TASK-0001
agentbridge --project my-project task accept TASK-0001
```

## 設定與查看成果

- [設定角色、模型、思考等級、額度接手及資料夾](docs/configuration.md)
- 查執行：`agentbridge --project my-project task result TASK-0001`；新的 CLI 執行會保存路徑、版本、PID、參數與可取得的 Token 用量。
- 查檔案：報告在目標專案的 `.agentbridge/tasks/`；程式在回報的 Worktree。原專案不會自動合併或部署。
- [安裝與登入問題](docs/real-agents.md) · [詳細指令](docs/reference.md) · [驗證紀錄](VALIDATION.md)
