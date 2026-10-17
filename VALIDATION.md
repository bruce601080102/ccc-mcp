# 驗證紀錄

日期：2026-10-17（Asia/Taipei）。此檔僅列實際執行的驗證與限制。

## 後續整合驗證補充（同日）

- 完整自動化測試：61 passed in 29.67s；本次變動檔案的 Ruff 檢查與格式檢查通過。
- 真實 Codex SA：agentbridge / TASK-0002 / EXEC-0ed64a53a8a54a79 已完成靜態分析，原始報告 needs_revision=false、simulated=false；未執行專案測試。
- 真實 Claude Code 2.1.302：hello-world / TASK-0001 / EXEC-a4a7f1127a2b4515 已建立 hello.py 與 tests/test_hello.py；Claude 當次未執行測試。
- Hello World 後續本機測試：TEST-64cfa0b36bb74e69 的原始 TEST-RESULT.md 記錄 executed_by_host=true、passed=true，實際輸出為 1 passed in 0.06s；此項已讀取原始證據核對。
- Claude 訂閱登入相容性：由不讀取 OAuth 憑證的 --bare 改為 --safe-mode，保留受限工具、空設定來源及空 MCP 清單；錯誤摘要可擷取 stdout 並遮罩。
- Windows Git 換行：已重現並修正 LF/CRLF 造成的誤判；換行差異另行記錄，不放寬實質註解或二進位變更檢查。

以下其餘段落保留初次 MVP 交付時的環境與驗證紀錄，當時尚未安裝或執行真實 Claude。

## 環境

| 項目 | 本次環境 |
|---|---|
| 作業系統 | Windows |
| Python | 3.14.7 |
| Git | 2.52.0.windows.1 |
| SQLite | 3.50.4 |
| MCP Python SDK | 1.30.0（pyproject 限制 <2） |
| Codex CLI | 0.162.0-alpha.17.2；檢查 version/help/exec help/review help |
| Claude Code | 未安裝 |

沒有執行真實 Codex 或 Claude 模型任務、沒有檢查登入憑證、沒有使用模型額度。

## 自動化結果

執行：

```powershell
.\.venv\Scripts\python -m pytest -q
.\.venv\Scripts\python -m pip check
.\.venv\Scripts\ruff check src tests examples
.\.venv\Scripts\ruff format --check src tests examples
```

完整 pytest：**50 passed in 35.71s**；沒有 skipped 或 failed。依賴檢查通過，Ruff 檢查與格式檢查通過。最後一次完整輸出另保存在 [test-results.txt](test-results.txt)。

## 規格驗收對應

| 規格項目 | 實際測試 |
|---|---|
| Context 初始化 | 空目錄註冊、完整文件、重複註冊 |
| SA Handoff | Fake SA、結構化報告、不可變執行原件 |
| PM Optimization | Patch diff、版本遞增、原始證據及決策保留 |
| Version Conflict | 舊版本拒絕、兩個執行緒同時提交只有一個成功 |
| Session Recovery | 全新 service 僅 bootstrap 即取得目標、任務、決策、限制與下一步 |
| SD/RD Handoff | 正確 Context 版本、核准方案、SA 原件、獨立 Worktree |
| Human Approval | 未核准拒絕、版本/hash/Context 不符拒絕、非互動終端拒絕 |
| Review Loop | Fake Review 回報問題，重新派工修訂，歷次成果保留 |
| Context Drift | 架構文件竄改、程式碼來源改變均標示需重新驗證 |
| 安全與復原 | traversal、Windows junction、逾時、取消、Worker lease/restart、非法轉換、原子寫入故障、註解變更、決策不可覆寫 |

額外驗證：真正的 stdio MCP initialize/list_tools/call_tool、真正的 loopback Streamable HTTP 通訊、HTTP 寫入與外部監聽需 OAuth、偽造 JWT 拒絕、Adapter 參數契約（stub）、固定 pytest 命令的 host 成功與失敗證據、CLI 匯入 Patch、假 Agent 派工立即返回並由獨立 Python 程序完成。

人工核准在自動測試中以明確的 test fixture 代替終端操作，production 沒有該繞過參數；範例資料仍停在 PENDING 人工核准。

## 操作與交付驗證

- 已 editable 安裝 `agentbridge-mcp`。
- 已執行 `examples/demo.py prepare`，產生 `.demo/project`、版本 2、Fake SA 原件、PM Patch 與 PLAN-A 待核准請求。
- 已以 `context validate` 確認示範 Context 完整。
- 已執行 `examples/mcp_probe.py --home .demo/hub --project demo`，透過實際 stdio Client 讀取工具與恢復資料。
- 已執行 doctor，確認 Codex 可用、Claude 缺少時其他功能仍正常。
- 可安裝 wheel 位於 `dist/agentbridge_mcp-0.1.0-py3-none-any.whl`。
- 已將 wheel 安裝至隔離目錄，確認從 wheel 匯入程式並成功註冊 MCP 工具。
- 完整來源碼、測試、範例與文件亦打包於 `dist/agentbridge-mcp-0.1.0-source.zip`。
- [共用 Context 範例](examples/shared-context/CONTEXT.md) 與 [Session 交接範例](examples/shared-context/SESSION-HANDOFF.md) 來自實際示範資料。
- 原始使用者規格保存在 [docs/SPEC-V2.md](docs/SPEC-V2.md)。

## 尚未驗證或需外部設定

1. 真實 Codex 模型執行與結構化輸出；本次只核對 CLI help 及使用 stub 測試呼叫參數。
2. 真實 Claude Code 安裝、登入、CLI 相容性、模型實作與測試；Adapter 依官方文件設計且在執行前檢查必要參數。
3. ChatGPT 帳戶實際讀寫權限、plugin 設定及端到端呼叫。
4. 真實 OAuth IdP 的 discovery/授權碼/PKCE/JWKS 整合、TLS 反向代理與 Secure MCP Tunnel；已提供部署說明與 JWT resource-server 實作。
5. Linux/macOS 執行、服務安裝、不同 Python 版本的 CI。
6. 同 OS 帳戶惡意程序的防護、硬體身分證明、真正的多租戶 ACL 與不可信 Repository 的 OS 沙箱，需要外部安全邊界。
7. 非 Python 註解自動解析與語意等價證明；目前保守 BLOCKED 並保留供人工審閱的 Diff。

不會把 Fake 成果宣稱為實際業務功能完成，也不會把本機 MCP 通訊通過宣稱為 ChatGPT 帳戶已連接。
