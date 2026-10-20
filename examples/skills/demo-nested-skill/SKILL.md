---
name: demo-nested-skill
description: 範例 skill，純粹用來測試 ccc-mcp 安裝多層資料夾／額外檔案的機制；內容本身沒有任何實際規則，不會真的影響派工結果。
---

# Demo Nested Skill

這份 skill 只是用來驗證安裝機制，不代表任何真實專案規範。它刻意放了兩層巢狀的額外檔案：

- 補充說明：`references/notes.md`（第一層）
- 檢查腳本：`scripts/helpers/check.sh`（第二層，`scripts/` 底下還有一層 `helpers/`）

## 怎麼測試安裝

本機資料夾安裝（會把整個資料夾完整複製）：

```bash
ccc-mcp skill install examples/skills/demo-nested-skill
ccc-mcp skill list
```

或者打包成 zip 再用網路安裝測試同一套邏輯：

```bash
cd examples/skills/demo-nested-skill && zip -r /tmp/demo-nested-skill.zip . && cd -
python3 -m http.server --directory /tmp 8008 &
ccc-mcp skill install http://127.0.0.1:8008/demo-nested-skill.zip
kill %1
```

或者只取單一 `.md`（故意拿掉其他檔案，驗證「單一 .md 網址不會帶走額外檔案」這個已知限制）：

```bash
python3 -m http.server --directory examples/skills/demo-nested-skill 8009 &
ccc-mcp skill install http://127.0.0.1:8009/SKILL.md
kill %1
```

## 裝完後要確認的事

1. 兩個額外檔案都真的被複製進去了：

   ```bash
   ls -R ~/.ccc-mcp/skills/demo-nested-skill/
   ```

   本機資料夾安裝、zip 安裝都應該看到 `references/notes.md` 與 `scripts/helpers/check.sh`；單一 `.md` 安裝則應該**只有** `SKILL.md`，驗證前面 README 寫的限制是真的。

2. `scripts/helpers/check.sh` 裝進去後，有沒有保留可執行權限：

   ```bash
   ls -l ~/.ccc-mcp/skills/demo-nested-skill/scripts/helpers/check.sh
   ```

   這份腳本在本次 repo 裡原本是 `+x`；目前 `ccc-mcp skill install` 的實作不會保留這個位元，所以預期你會看到 `-rw-r--r--`，要自己 `chmod +x` 才能執行。

3. 派工／`bootstrap_project`／`get_context` 回傳的 `available_skills` 裡，能不能看到 `id: demo-nested-skill`、`description` 跟上面 frontmatter 一致。

測完想移除：

```bash
ccc-mcp skill remove demo-nested-skill
```
