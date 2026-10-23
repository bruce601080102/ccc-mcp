---
name: demo-multilevel-skill
description: 測試用範例：當需求提到「multilevel canary」時使用。這份 SKILL.md 只負責導向第二層文件，再由第二層導向第三層，藉此驗證 agent 能多層讀取 skill 內的其他檔案。
---

# Demo Multilevel Skill（第 1 層）

當需求提到 **multilevel canary** 時才套用這份 skill，其他需求請忽略。

## 路徑規則（重要）

agent 的工作目錄是專案，不是這個 skill 的資料夾。所以下面所有相對路徑，都是相對於
**這個 skill 在 `available_skills` 裡那一筆的 `path` 欄位**（絕對路徑）。請先組出絕對路徑再讀檔。

## 下一步

1. 讀取 `references/level2.md`（第 2 層）。
2. 完全照第 2 層與它指向的文件的指示做；不要自己猜暗號，暗號只存在於最深那一層。
