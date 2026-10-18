# 驗證紀錄

日期：2026-10-18（Asia/Taipei）。此檔僅列實際執行的驗證與限制。

## ccc-mcp 改名、共享上下文精修與精簡回覆（2026-10-18）

- 套件、指令、Python 模組、MCP 名稱、環境變數、腳本、文件與範例改用 ccc-mcp／ccc_mcp／CCC_MCP；在已搬動的資料夾重新安裝 editable 套件並驗證新指令能啟動。
- 舊資料目錄與環境變數保留相容讀取，不改寫歷史報告、資料庫引用或授權根目錄。新專案使用 .ccc-mcp。Tunnel 使用新前綴設定檔，啟動時以目前資料夾組出新的 Python／模組路徑。
- bootstrap／collect_context_updates 回傳 context_locations 與 context_sync。Agent 完整回報先保留為原始證據，PM 使用 source_execution_ids 及原始 SHA256 引用提交 ContextPatch 後，才清除對應待精修狀態；新回報會再次進入待精修。原始來源引用會保留於新版 snapshot 並傳給下一個 Agent。
- 增加任務 brief 與聊天回覆指示：預設六點內摘要，完整報告留檔。這是模型指示，不能強制聊天前端樣式，也不代表背景自動呼叫 ChatGPT 精修。
- 完整測試：171 passed、1 skipped in 103.14s；Ruff、43 個檔案格式檢查、pip check 與兩個 Git Bash 腳本語法檢查通過。新增測試使用臨時專案與 fake adapter，沒有啟動真實模型。symlink 測試因 Windows 權限限制跳過。

## ChatGPT 工具探索相容性修正（2026-10-18）

- 使用者 Tunnel 原始紀錄顯示 server/discover 被 MCP 1.30.0 判為無效 ClientRequest；先前僅驗證 initialize／tools/list，未覆蓋 ChatGPT 重新整理使用的新探索流程。
- 依官方 SDK 遷移指南升級為 MCP 2.3.0，使用 MCPServer、蛇形 Python 型別欄位及新的傳輸設定位置；保留既有 HTTP OAuth 與 Host／Origin 驗證。
- 新增真正 stdio JSON-RPC 回歸：不呼叫 initialize，直接 server/discover → tools/list（包含 write_workspace）→ read_workspace → SHA256 比對寫入 → 讀回核對。寫入僅在臨時測試專案執行。
- 啟動腳本會在 SDK 過舊時提前提示更新依賴，README 補上更新依賴後重啟 Tunnel 的步驟。
- 完整回歸測試：167 passed、1 skipped in 95.87s；Ruff、37 個檔案格式檢查與 pip check 通過。跳過項目仍為 Windows symlink 權限限制。ChatGPT 網頁重新整理仍需使用者重啟常駐 Tunnel 後驗證，不宣稱已完成網頁端驗收。

## 單一專案授權與直接讀寫（2026-10-17）

- 完整自動化測試：166 passed, 1 skipped in 82.06s；Ruff 通過，37 個檔案格式檢查通過。
- 一般入口合併為 project authorize／project revoke。一次本機互動授權後，ChatGPT 使用 write_workspace(path,content,expected_sha256) 直接修改整個註冊專案，不需逐檔設定、Task、PLAN、EDIT 或 BATCH 核准。
- 新增不可覆寫的專案授權／撤銷歷史，讀寫授權綁定專案根目錄；各次寫入保存前後全文、SHA256 及對應授權來源，不冒充逐檔人工審閱。撤銷會停止直接寫入並清除舊逐檔／目錄權限。
- 覆蓋一次授權後多檔修改、重複授權不再要求確認、沒有 Git／Task 的一般編輯、唯讀 SA 任務不變但可另行直接編輯、跨專案拒絕、來源變更、原 Python 註解保留、撤銷與中斷恢復、JSON 投影不能授權，以及不能經由舊 EDIT ID 重放已撤銷授權。
- 真正 stdio MCP 驗證 bootstrap 的 project_access → read_workspace → write_workspace 直接修改原檔，無須建立 Task／PLAN；HTTP 唯讀服務不因專案授權而開放寫入。所有寫入驗證皆在臨時專案執行，沒有替使用者授權 TradeAdvisorv3 或修改其原檔。
- README 重寫為 100 行、註冊／授權／連線／開始工作四步，舊細項指令從一般 CLI help 隱藏但保留相容性；舊版詳細流程標示為進階用途。
- 新的 write_workspace 工具參數需重啟 Tunnel 並更新網頁連線才能使用；未宣稱使用者目前對話已完成載入。symlink 測試仍因本機權限跳過一項。

## 專案範圍、批次核准與 ChatGPT 原地任務（同日，先前驗證）

- 完整自動化測試：146 passed, 1 skipped in 70.25s；Ruff 通過，35 個檔案格式檢查通過。
- 新增 write-root／revoke-write-root，一次開放整個專案或子目錄；既有憑證、管理資料與越界限制保留，獨立 write-file 規則仍相容。
- 覆蓋多檔 BATCH 只觸發一次本機確認、單檔成員不可分開核准／套用、整批寫入前檢查雜湊及撤銷、重複提案／重試，以及部分寫入中斷後恢復。批次是逐檔原子寫入，並非整批檔案系統交易。
- 覆蓋 ChatGPT project 模式的一次計畫核准可修改多個原專案檔案；來源雜湊、註解保留、撤銷及唯讀限制保留，不建立 Worktree。原地任務起始須 Git 基準與乾淨業務檔案，後續同任務修訂可接續未提交成果。
- 覆蓋模式切換使舊核准失效、活動實作禁止切換、原地實作排他、執行模式固定，以及原地模式不會默默開放原生 Codex／Claude CLI 寫根目錄。原生 CLI 實作保留 Worktree。
- 設定與修改測試皆使用臨時專案，沒有替使用者開放 TradeAdvisorv3 整個專案，也沒有修改其業務檔案或啟動真實模型。symlink 測試仍因主機權限跳過一項。
- README 改為專案／任務的一次核准示例，單檔精確授權保留為可選流程。新增設定需重啟服務、更新網頁連線後驗證，並未宣稱目前網頁已載入。

## 指定原檔直接編輯驗證（同日，先前驗證）

- 完整自動化測試：127 passed, 1 skipped in 56.36s；Ruff 檢查通過，34 個檔案格式檢查通過。
- 新增逐檔 write-file／revoke-file、不可變更 EDIT 差異、本機互動式核准、原專案直接套用及前後版本保存。原有 Task／Worktree 核准仍保留。
- 覆蓋未授權、未核准、原檔過期、審閱時原檔變更、核准後撤銷、跨專案、憑證／路徑越界、二進位／超量內容、Python 原註解保留、重複提案／套用及寫入中斷恢復。
- 透過真正 MCP stdio 連線驗證 read_workspace → propose_workspace_edit → 未核准拒絕 → 測試用本機核准 → write_workspace → 讀回雜湊一致。核准替身僅用於臨時測試專案，不會呼叫真實模型或修改 TradeAdvisorv3 原檔。
- Windows 主機不允許建立 symlink，因此一項 symlink 重導測試跳過；路徑檢查沿用現有拒絕 symlink/junction 的 safe_path。不能將跳過視為已完成該主機的重導驗證。
- 文件補上 ChatGPT 連線安裝、每個新對話選用、實際讀檔驗證，以及原 readme.md 追加 test 的直接編輯範例；已檢查 UTF-8、程式碼區塊及本機連結。
- 網頁端新增直接編輯工具仍需重啟 Tunnel、更新連線並在新對話實測；本機／stdio 驗證不代表使用者網頁端已完成載入與核准。

## 可設定角色與 ChatGPT 接手驗證（同日，先前驗證）

- 完整測試：99 passed in 52.81s；Ruff 檢查與格式檢查通過。
- 覆蓋五種角色與三種供應商組合、Codex／Claude 模型及思考參數、依角色維持唯讀／實作權限。
- 覆蓋 ChatGPT 核准後寫檔、SHA256 比對、禁止越界與憑證路徑、Context 過期拒絕寫入、取消與重新載入接手工作。
- 額度錯誤以測試替身驗證：保留失敗紀錄並建立 ChatGPT 接手；登入錯誤不觸發接手，Context 過期不略過驗證。
- 透過真正 MCP stdio 連線驗證 ChatGPT 讀檔與提交報告；本機 Python 子程序驗證 PID、程式路徑、時間、退出碼與原生用量解析的保存機制，不呼叫真實模型。
- 已安裝 CLI 的 help 確認 Codex --model/--config/--sandbox、Claude --model/--effort；本次未額外消耗模型額度。不同模型／帳戶的實際支援仍由 CLI 執行時驗證。
- 實讀既有 ccc-mcp Context v3：SA／SD／RD 取得相同的正式 shared_context；測試覆蓋 Agent context_updates → PM 精修提交 → 下一輪取得新版本。
- 現有 ccc-mcp 與 hello-world 根目錄保留；新專案位置、Worktree 位置及額外讀取目錄可由本機設定。README 已縮為 49 行。
- 舊執行沒有回填當時未保存的 Token／PID；ChatGPT 網頁模型與 Token 用量未宣稱可由 MCP 獨立觀測。

## 後續整合驗證補充（同日）

- 完整自動化測試：61 passed in 29.67s；本次變動檔案的 Ruff 檢查與格式檢查通過。
- 真實 Codex SA：ccc-mcp / TASK-0002 / EXEC-0ed64a53a8a54a79 已完成靜態分析，原始報告 needs_revision=false、simulated=false；未執行專案測試。
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

- 已 editable 安裝 `ccc-mcp-mcp`。
- 已執行 `examples/demo.py prepare`，產生 `.demo/project`、版本 2、Fake SA 原件、PM Patch 與 PLAN-A 待核准請求。
- 已以 `context validate` 確認示範 Context 完整。
- 已執行 `examples/mcp_probe.py --home .demo/hub --project demo`，透過實際 stdio Client 讀取工具與恢復資料。
- 已執行 doctor，確認 Codex 可用、Claude 缺少時其他功能仍正常。
- 可安裝 wheel 位於 `dist/ccc_mcp-0.1.0-py3-none-any.whl`。
- 已將 wheel 安裝至隔離目錄，確認從 wheel 匯入程式並成功註冊 MCP 工具。
- 完整來源碼、測試、範例與文件亦打包於 `dist/ccc-mcp-mcp-0.1.0-source.zip`。
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
