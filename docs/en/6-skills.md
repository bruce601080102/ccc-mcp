# 6. Install and use skills

A Skill is portable `SKILL.md` guidance, optionally with reference files, scripts, or assets. It is not an MCP server and it does not automatically install a CLI or execute a script.

```bash
ccc-mcp skill install ./my-skill
ccc-mcp skill install https://example.com/my-skill.zip
ccc-mcp skill list
ccc-mcp skill remove my-skill
```

Skills live globally at `~/.ccc-mcp/skills/<id>/` and are shown to every role through `available_skills`. Agents may choose whether a Skill is relevant; a Skill is guidance, not an enforced policy. Install only trusted sources because its text enters an agent prompt.

Skills are managed with the `skill` commands above; the Web console has no Skills page. Removal affects future handoffs for every project and leaves the source directory untouched.

| Source | Files retained |
| --- | --- |
| Local directory | Directory and all descendants |
| ZIP | Directory containing the first discovered `SKILL.md` |
| Single `SKILL.md` URL | Markdown file only |

Only the Skill text is embedded in Context; other files remain at their installed paths. Claude Code has no Bash tool in this workflow. Codex shell use remains subject to its sandbox and PATH. ChatGPT roles only have ccc-mcp MCP tools. Consequently, a Skill's instruction to run a command is not a promise that it can run.

On a constrained context budget, Skill bodies are the first optional material omitted. See [reliability](7-reliability-and-security.md).

## Multi-level Skills: SKILL.md pointing at other documents

`SKILL.md` can hand the agent off to deeper files (`SKILL.md` -> `references/level2.md` -> `references/deeper/level3.md`), but three rules decide whether that works:

1. **Relative paths are relative to the Skill's `path`, not the project.** Agents run inside the project, so say so in `SKILL.md`: "paths below are relative to this skill's `path` in `available_skills`".
2. **Only `SKILL.md`'s text is embedded; deeper files must be read by the agent.** Claude Code (`Read,Glob,Grep`) and Codex (read-only sandbox) can read files; a ChatGPT role needs the skills folder granted as a read root (`ccc-mcp --project <id> config read-root skills ~/.ccc-mcp/skills`, then `read_workspace` with `root_id: skills`). Scripts are never run for you (see above).
3. **Bodies are trimmed first under `context_token_budget`**, which also removes level 1.

A ready-made test lives in [`examples/skills/demo-multilevel-skill`](../../examples/skills/demo-multilevel-skill/SKILL.md): its canary `MULTILEVEL-OK-9c41` exists only in the deepest file, so an agent can report it only by reading all three levels.

```bash
python -m pytest -q tests/test_skills_multilevel.py          # chain resolves; canary only at level 3 (no agent needed)
ccc-mcp skill install examples/skills/demo-multilevel-skill
ccc-mcp --project <id> context show                          # skill + its absolute path appear under available_skills
```

A real ChatGPT conversation that ran this test and passed: [shared conversation](https://chatgpt.com/share/6adb126e-2154-83ec-9364-d5a10e06024f). The agent read all three levels and reported the canary.

What a passing reply from ChatGPT looks like (excerpt, translated from the original Traditional Chinese): the file exists with the expected content read back through `read_workspace`, SA read all three levels, the canary `MULTILEVEL-OK-9c41` is reported, SD/RD implemented, REVIEW passed, and the task is `COMPLETED` with no push, merge, or deploy.

Then, with a real run, ask for: "multilevel canary: create hello.txt containing Hello". Pass only if `multilevel_canary.txt` contains the canary and `ccc-mcp --project <id> task result` shows it in the role's `summary`.
