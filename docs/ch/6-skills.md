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

Skill 以上面的 `skill` 指令管理；Web 控制台沒有 Skills 頁面。移除會影響之後所有專案的交接，來源資料夾不會刪除。

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
