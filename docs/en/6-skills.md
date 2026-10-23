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

## MCP servers: let dispatched agents call a local MCP live

By default agents dispatched by ccc-mcp run without any MCP (`--strict-mcp-config` with an empty list for Claude Code, `--ignore-user-config` for Codex). To give a project's agents a local stdio MCP server, such as codebase-memory-mcp, install it from the terminal:

```bash
ccc-mcp mcp list                                     # servers your MCP clients define (Claude Code/Desktop, Codex, VS Code, Cursor) + what this project installed
ccc-mcp --project <id> skill install mcp codebase-memory
ccc-mcp --project <id> skill install mcp codebase-memory --tools search_graph,get_architecture   # enable only some tools
ccc-mcp --project <id> mcp remove codebase-memory
```

Install first checks that the command exists and completes a real MCP handshake, prints the tools it exposes (with their read-only hints), and records the command's **absolute path**. If the command only resolves inside a conda/venv environment (for example it works after `conda activate stock311` but not in `base`), run the install from the activated shell, or pass `--command /full/path`; because the path is recorded, `ccc-mcp connect` and workers started later do not need that environment activated. The name can be the client's name for the server or the command name with or without `-mcp`.

What this changes, and what it does not:

- **Claude Code roles only.** An installed server is passed with `--mcp-config` and its enabled tools are allowed with `--allowedTools`; nothing else is. Codex roles do not get it: Codex did not load stdio MCP servers in this non-interactive workflow in testing, so install lists each role as `live` or `not supported yet`. Use the snapshot workaround below for those roles.
- **Applies to every Claude role of the project (SA, SD/RD, REVIEW).** That includes read-only roles: an MCP tool can do whatever the server lets it do, regardless of the role's read-only file tools. Check the printed tool list (a tool with `read_only: false`, for example one that deletes an index) and use `--tools` to enable only what you accept.
- **Claude runs without `--safe-mode` for that project**, because `--safe-mode` also disables every MCP server. The surfaces it closed are closed individually: user/project/local settings and hooks (`--setting-sources ""`), skills (`--disable-slash-commands`), and `CLAUDE.md` plus auto-memory (environment variables). Projects with no installed MCP are unchanged and keep `--safe-mode`.
- **Only the local CLI can install.** There is no MCP tool for it and the project's `settings.json` is not an import source for it, so neither ChatGPT nor a repository file can add a command that dispatch would execute. Environment variables from a client's config are never copied.
- Remote (HTTP) servers are not supported; the command must start a stdio server.

[`examples/skills/demo-mcp-skill`](../../examples/skills/demo-mcp-skill/SKILL.md) is a canary: ask for "mcp canary"; an agent with the MCP reports a real project name, one without it must report `MCP-UNAVAILABLE` and its actual tool list.

### Workaround for Codex roles: snapshot an MCP's output into a Skill

Codex roles cannot call the MCP (see above), but any role can read files. `skill install` can run its CLI once on your machine and save the results under a Skill's `references/`; the small `SKILL.md` only lists them, so agents use the multi-level mechanism above and read just what they need.

```bash
ccc-mcp --project <id> skill install codebase-memory:   # the project's root
ccc-mcp skill install codebase-memory:/path/to/repo             # or name any repo explicitly
```

If the repo is not yet indexed by codebase-memory-mcp (it can only answer about repos it has parsed into its graph database), the command indexes it first and reports `indexed_now: true`; that writes to the tool's own cache. The binary is found via `--cbm /full/path`, then `PATH`, then the macOS cache its launcher downloads to. It is a point-in-time snapshot, not a live index: run the same command again after the code changes. Skills are global and every installed Skill's `SKILL.md` text is sent to every role in every project, so the generated `description` names the repo and tells agents to ignore it elsewhere. To verify, ask for "snapshot canary: report the Function count in the architecture snapshot" and compare the reply with `Function` in `~/.ccc-mcp/skills/codebase-snapshot-<name>/references/architecture.md`.
