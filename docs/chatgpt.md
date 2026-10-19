# 連接 ChatGPT 官方網頁

文件核對日期：2026-10-17。官方介面及工作區政策可能改變，以下以連結文件與實際帳戶介面為準。本次交付只完成本機 MCP 與驗證，沒有建立雲端資源或使用帳戶登入。

## Git Bash 快速連線

`ccc-mcp connect` 第一次執行時會自動從官方 release 下載 `tunnel-client`、核對 SHA256，安裝到 `~/.ccc-mcp/tools/tunnel-client/`，不需要事先手動安裝。

1. 開啟 [Platform Tunnels](https://platform.openai.com/settings/organization/tunnels)，建立名稱為 ccc-mcp 的 Tunnel，關聯要使用的 ChatGPT 工作區，複製 `tunnel_...` ID。
2. 在 [Runtime API Keys](https://platform.openai.com/settings/organization/api-keys) 準備 runtime key；該 key 的 principal 需有 Tunnels Read + Use。此流程不需要提供 admin key 給本機 runtime。
3. 在專案根目錄的 Git Bash 執行：

```bash
ccc-mcp connect tunnel_你的實際ID
```

（`bash scripts/connect-chatgpt.sh tunnel_你的實際ID` 仍可用，現在只是轉發到上面這個指令。）

依提示在終端輸入 runtime key，輸入會隱藏；不要貼到對話。腳本僅將 key 放入本次子程序環境，設定檔只保存 `env:CONTROL_PLANE_API_KEY` 引用。腳本會建立獨立 profile、執行 doctor 並在前景啟動 Tunnel，由它啟動 ccc-mcp stdio，不需另外手動執行 `ccc-mcp serve`。保持終端開啟。

4. 在 [ChatGPT Plugins](https://chatgpt.com/plugins) 新增 custom MCP server，名稱 ccc-mcp、Connection 選 Tunnel，選擇同一個 ID。此本機 stdio 端沒有另外實作 OAuth，因此若畫面要求選取 MCP authentication，選擇無驗證；Tunnel 的組織與工作區權限仍必須正確限制存取。依畫面完成建立；建立表單不一定顯示工具清單，不以此判定連線成功或失敗。
5. 建立後找到該 plugin 並安裝，在新對話以 `@ccc-mcp` 選用。輸入「請使用 ccc-mcp 的 bootstrap_project 恢復 project_id 為 ccc-mcp 的專案，說明進度與下一步」，並另外實測讀檔工具；建立成功不代表目前對話已載入新工具。

若第 1 步顯示 Tunnels access required，或 ChatGPT 沒有新增 custom MCP server 選項，先處理該帳戶的權限；不能靠本機指令繞過。若 doctor 未通過，先查看明確錯誤再繼續。尚未從你的帳戶完成此流程前，不視為 ChatGPT 端到端連線完成。

### Tunnel poll 回傳 401 Unauthorized

這表示 OpenAI control plane 拒絕驗證。檢查 runtime Secret Key 是否有效、是否屬於 Tunnel 所關聯的 Platform organization，以及該金鑰身分是否有 Tunnels Read + Use。只憑 401 無法確定是金鑰、組織或權限哪一項有問題；請勿把金鑰貼入對話。

先 Ctrl+C 停止重試，再使用以下命令重新在本機輸入金鑰（將 ID 換成實際值）。`env -u` 只影響這次腳本，不會改掉父層終端既有設定：

```bash
env -u CONTROL_PLANE_API_KEY -u OPENAI_ADMIN_KEY ccc-mcp connect tunnel_你的實際ID
```

腳本會先用 runtime key 執行唯讀的 tunnel metadata 存取檢查，失敗就停止，避免直接進入持續 poll。檢查成功仍不代表已通過 ChatGPT 端到端驗收，還需確認 polling 及實際工具呼叫。

## ChatGPT 說沒有 list_workspace／read_workspace

新版本有註冊 `list_workspace`、`read_workspace`、`write_task_file`、`submit_agent_report`。如果 ChatGPT 能派工卻找不到這些工具，先更新服務與 ChatGPT 端保存的工具清單，而不是再次註冊專案或增加 read-root：

1. 停止舊 Tunnel，重新執行 `ccc-mcp connect tunnel_你的實際ID`（會自動找 codex／claude CLI，不需要先手動 `source` 任何腳本）。
2. 若 plugin 詳細設定有重新整理工具／actions 選項，可用它更新。沒有此入口時，可新增另一個名稱（例如 ccc-mcp-v2）的連線，選擇同一個 Tunnel，完成建立並安裝。建立畫面沒有工具清單時，直接進入下一步，不要求在該畫面確認工具數量。
3. 新開對話，以 `@` 明確選用更新後的 plugin；若新舊連線同時存在，請選新建立的那個。先要求呼叫 `list_workspace(project_id="你的專案ID", root_id="project")`，再用 `read_workspace` 讀實際列出的 README 路徑，回報檔名、SHA256 與少量內容。看到成功的工具呼叫結果才代表本次對話可讀檔；文字宣稱「可以讀」不算驗證。驗證期間不要派工或提交 Context。

若新對話仍表示找不到工具，保留該回覆及其可用工具名稱，繼續核對該對話實際選用的連線與 Tunnel。另一個舊對話的 19／26 等工具數量，不能證明新對話載入了什麼；本機工具註冊成功也不能取代網頁端驗收。

參見 [OpenAI 官方工具更新說明](https://developers.openai.com/api/docs/guides/custom-mcp-server)。

ChatGPT 互動交接期間，應先讀檔並用 `submit_agent_report` 完成該次報告，再由 PM 精修提交 Context。若先提交新版 Context，舊交接就會過期，不能繼續寫檔或提交報告；須保留舊執行紀錄，結束過期執行，再沿用同一任務以目前版本重新交接，不能忽略版本檢查。

## 本機連線探測

```powershell
ccc-mcp init --id my-project
ccc-mcp doctor
python examples/mcp_probe.py --home "$HOME/.ccc-mcp" --project my-project
ccc-mcp serve --transport streamable-http --host 127.0.0.1 --port 8765
```

另一個終端可執行：

```powershell
python examples/mcp_probe.py --url http://127.0.0.1:8765/mcp --project my-project
```

HTTP 預設唯讀。ChatGPT 網頁的執行環境不能直接存取使用者電腦的 127.0.0.1；需設定可達的 HTTPS 端點或官方 Secure MCP Tunnel。[官方連線指南](https://developers.openai.com/plugins/deploy/connect-chatgpt)

## 選項 A：Secure MCP Tunnel

在 Platform tunnel settings 建立/選擇 tunnel，讓它關聯正確的 Platform organization 與 ChatGPT workspace。由官方下載 `tunnel-client`，先執行 `tunnel-client help quickstart`，再依目前版本設定可達的本機 MCP stdio command 或 HTTP URL，執行 doctor 並保持 run 運作。Tunnel 從內部向外連線，不需開放主機入站連接埠；tunnel 的管理與使用權限，以及 ChatGPT 自訂 MCP 權限是分開的。[官方 Tunnel 指南](https://developers.openai.com/api/docs/guides/secure-mcp-tunnels)

本機 stdio command 請使用已安裝 ccc-mcp 的 Python 絕對路徑，例如：

```text
C:/path/to/ccc-mcp/.venv/Scripts/python.exe -m ccc_mcp --home C:/Users/YOUR_NAME/.ccc-mcp serve
```

stdio 具寫入工具，僅交給你信任且已限制組織/工作區存取的 tunnel；MCP 仍無人工核准工具。不要把 runtime API key 貼入對話、命令列參數、Repo 或記錄檔。金鑰由官方客戶端所支援的安全方式在本機設定。

## 選項 B：HTTPS + OAuth

部署 TLS 反向代理到 `http://127.0.0.1:8765/mcp`，由操作者設定符合 MCP 的 OAuth 授權伺服器。ccc-mcp 已提供 RS256/ES256 JWT 資源伺服器驗證（issuer、audience、expiration、subject 與 ccc-mcp scope），不包含登入網站、授權端點、token endpoint、client registration 或 PKCE 服務。

```powershell
$env:CCC_MCP_OAUTH_ISSUER = "https://identity.example.com/"
$env:CCC_MCP_OAUTH_AUDIENCE = "https://bridge.example.com/mcp"
$env:CCC_MCP_OAUTH_JWKS_URL = "https://identity.example.com/.well-known/jwks.json"
ccc-mcp serve --transport streamable-http --host 127.0.0.1 --port 8765 --enable-writes
```

替換為實際受信任 IdP 設定。請讓 token audience 精確等於上述 resource URL，issuer 也需精確相符。反向代理保留 Authorization 標頭並正確處理 Host；SDK 會檢查 DNS rebinding。設定了 OAuth 後，所有 HTTP 工具都需合法 token。

授權伺服器需提供 MCP discovery 與 authorization-code/PKCE，依 ChatGPT 連線顯示的 callback URI 設定，不能假設固定回呼地址。ChatGPT 不以你隨意指定的 API key 取代標準 OAuth；核對 [OpenAI 官方驗證文件](https://developers.openai.com/plugins/build/auth)。

此版本以單一 operator home 作為授權範圍；具 ccc-mcp scope 的使用者可存取該 home 內註冊的專案。需要不同使用者/專案 ACL 時，請分開 home 與服務帳戶或擴充授權檢查。不要讓多租戶共用此 MVP 的全域 scope。

## 在 ChatGPT 加入

目前官方流程是在 ChatGPT Plugins 選擇新增自訂 MCP server，連線方法選 HTTPS URL（含 `/mcp`）或 Tunnel，設定驗證後建立，再安裝該 plugin。在新對話以 `@` 選用並實際呼叫工具；不要求建立表單一定有工具清單。更動工具後可從帳戶介面提供的詳細設定更新 metadata，再以新對話驗證。是否能新增、呼叫寫入工具，取決於帳戶與 workspace 政策，不能由本機測試推定。[官方連線與測試步驟](https://developers.openai.com/plugins/deploy/connect-chatgpt)

代表性呼叫：`bootstrap_project`，參數 `{"project_id":"my-project"}`。驗證它回傳實際版本、任務與決策後，才算該帳戶端到端連線成功。

若帳戶僅可讀，維持 HTTP 唯讀模式，PM 提交的 JSON 透過本機 `context import-patch`、`task plan`、`task dispatch` 等 CLI 完成。若完全沒有 MCP 權限，使用 `context export` 貼入新對話仍可恢復工作。
