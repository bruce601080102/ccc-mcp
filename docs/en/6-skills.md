# 6. Install and use skills

A Skill is portable `SKILL.md` guidance, optionally with reference files, scripts, or assets. It is not an MCP server and it does not automatically install a CLI or execute a script.

```bash
ccc-mcp skill install ./my-skill
ccc-mcp skill install https://example.com/my-skill.zip
ccc-mcp skill list
ccc-mcp skill remove my-skill
```

Skills live globally at `~/.ccc-mcp/skills/<id>/` and are shown to every role through `available_skills`. Agents may choose whether a Skill is relevant; a Skill is guidance, not an enforced policy. Install only trusted sources because its text enters an agent prompt.

| Source | Files retained |
| --- | --- |
| Local directory | Directory and all descendants |
| ZIP | Directory containing the first discovered `SKILL.md` |
| Single `SKILL.md` URL | Markdown file only |

Only the Skill text is embedded in Context; other files remain at their installed paths. Claude Code has no Bash tool in this workflow. Codex shell use remains subject to its sandbox and PATH. ChatGPT roles only have ccc-mcp MCP tools. Consequently, a Skill's instruction to run a command is not a promise that it can run.

On a constrained context budget, Skill bodies are the first optional material omitted. See [reliability](7-reliability-and-security.md).
