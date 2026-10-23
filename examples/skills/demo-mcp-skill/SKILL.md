---
name: demo-mcp-skill
description: 測試用範例：當需求提到「mcp canary」時使用。要求 agent 呼叫本機 codebase-memory MCP 的 list_projects，用來驗證 MCP 類型的 skill 在 ccc-mcp 派出的 agent 裡能不能用。
---

# Demo MCP Skill

當需求提到 **mcp canary** 時才套用這份 skill，其他需求請忽略。

## 做什麼

1. 如果你有 `codebase-memory-mcp` 的 MCP 工具（工具名稱類似 `mcp__codebase-memory-mcp__list_projects`），
   呼叫 `list_projects`，把回傳的**第一個專案名稱**原文寫進報告的 `summary`。
2. 如果你**沒有**任何 MCP 工具，不要假裝、不要猜專案名稱，也不要用其他工具代替；
   在 `summary` 原文寫 `MCP-UNAVAILABLE`，並列出你實際擁有的工具名稱。
