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
