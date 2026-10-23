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

### Set this up beforehand: ChatGPT cannot do it

Nothing here is automatic. Until an operator has done the steps below, ChatGPT (and every dispatched agent) has no access to the MCP and will fall back to reading source files; ChatGPT cannot install or index anything itself, and the tools it can call never include a standalone "codebase-memory" tool.

1. **The tool must run in your terminal.** For codebase-memory-mcp, `codebase-memory-mcp --version` must work (activate its conda/venv first if that is where it lives).
2. **Prepare the tool for the project.** codebase-memory-mcp can only answer about repositories it has indexed: `codebase-memory-mcp cli index_repository '{"repo_path":"/path/to/project"}'`.
3. **Install it for the scope you want**, in that same shell: `ccc-mcp --project <id> skill install mcp codebase-memory` (one project) or without `--project` (every project). Enable only the tools you accept with `--tools`.
4. **Restart `ccc-mcp connect`, then refresh the connection's tools in ChatGPT** (or start a new connection/conversation); ChatGPT caches the tool list.
5. **Check:** ask ChatGPT to list its ccc-mcp tools (`call_installed_mcp_tool` must appear), then to use the MCP, and confirm with `ccc-mcp mcp calls`. `bootstrap_project` also reports `installed_mcp`, and tells ChatGPT to ask you for this setup when nothing is installed.

The commands for steps 3 and 5, and for managing installs afterwards:

```bash
ccc-mcp skill install mcp codebase-memory                  # no --project = GLOBAL: every project's agents get it
ccc-mcp --project <id> skill install mcp codebase-memory   # only this project
ccc-mcp skill install mcp codebase-memory --tools search_graph,get_architecture   # enable only some tools
ccc-mcp mcp list                                           # global installs + each project's own, and servers your MCP clients define (Claude Code/Desktop, Codex, VS Code, Cursor)
ccc-mcp --project <id> mcp list                            # what that project's agents actually get (each tagged global/project)
ccc-mcp mcp calls                                          # evidence of MCP calls ChatGPT made through ccc-mcp (arguments summary + outcome, never the content)
ccc-mcp mcp remove codebase-memory                         # removes the global install
ccc-mcp --project <id> mcp remove codebase-memory          # removes that project's own install
```

Scope follows the same rule everywhere: only an explicit `--project` narrows it; the current directory is never used to guess, because a global install grants the tools to every project. When a project installs a server with the same name as a global one, the project's entry (including its enabled-tool list) replaces the global one for that project. A project cannot remove a global install; remove it without `--project`.

Install first checks that the command exists and completes a real MCP handshake, prints the tools it exposes (with their read-only hints), and records the command's **absolute path**. If the command only resolves inside a conda/venv environment (for example it works after `conda activate stock311` but not in `base`), run the install from the activated shell, or pass `--command /full/path`; because the path is recorded, `ccc-mcp connect` and workers started later do not need that environment activated. The name can be the client's name for the server or the command name with or without `-mcp`.

What this changes, and what it does not:

- **All three providers.** For Claude the server is passed with `--mcp-config` and only its enabled tools are allowed (`--allowedTools`). For Codex it is passed as `-c mcp_servers.<name>.*` overrides with `enabled_tools`, plus `required=true` (the server may take seconds to start, as codebase-memory-mcp does, and Codex otherwise begins the turn without its tools) and automatic approval (the run uses `-a never`, under which Codex refuses any MCP call that needs approval). Both were verified against a real codebase-memory-mcp. ChatGPT (the PM conversation and any `chatgpt` role) is not a process ccc-mcp launches, so it cannot be handed a config; it reaches the same servers through two ccc-mcp tools instead, `list_installed_mcp_tools` and `call_installed_mcp_tool`. ccc-mcp runs the call itself, only for a server+tool pair the local CLI enabled; nothing in the request can add a server, a command or a tool. The server stays running between calls (one process per project, closed after 5 idle minutes), so only the first call pays a slow start. Results are untrusted data. Because there is one install and one enabled-tool list, Claude, Codex and ChatGPT all use the same MCP the same way.
- **Applies to every role of the project (SA, SD/RD, REVIEW, and the ChatGPT PM).** That includes read-only roles: an MCP tool can do whatever the server lets it do, regardless of the role's read-only file tools. Check the printed tool list (a tool with `read_only: false`, for example one that deletes an index) and use `--tools` to enable only what you accept.
- **Claude runs without `--safe-mode` for that project**, because `--safe-mode` also disables every MCP server. The surfaces it closed are closed individually: user/project/local settings and hooks (`--setting-sources ""`), skills (`--disable-slash-commands`), and `CLAUDE.md` plus auto-memory (environment variables). Projects with no installed MCP are unchanged and keep `--safe-mode`.
- **Only the local CLI can install.** There is no MCP tool for it and the project's `settings.json` is not an import source for it, so neither ChatGPT nor a repository file can add a command that dispatch would execute. Environment variables from a client's config are never copied.
- Remote (HTTP) servers are not supported; the command must start a stdio server.

[`examples/skills/demo-mcp-skill`](../../examples/skills/demo-mcp-skill/SKILL.md) is a canary: ask for "mcp canary"; an agent with the MCP reports a real project name, one without it must report `MCP-UNAVAILABLE` and its actual tool list.

What it looks like in practice: a real ChatGPT conversation using an installed MCP through ccc-mcp: [shared conversation](https://chatgpt.com/share/6adb5730-df44-83e8-aa53-78a5e9504920). Even in a conversation like that, confirm the calls really happened with `ccc-mcp mcp calls`.

### Alternative: snapshot an MCP's output into a Skill

If you would rather not give agents live access, or want a frozen view, any role can read files. `skill install` can run its CLI once on your machine and save the results under a Skill's `references/`; the small `SKILL.md` only lists them, so agents use the multi-level mechanism above and read just what they need.

```bash
ccc-mcp --project <id> skill install codebase-memory:   # the project's root
ccc-mcp skill install codebase-memory:/path/to/repo             # or name any repo explicitly
```

If the repo is not yet indexed by codebase-memory-mcp (it can only answer about repos it has parsed into its graph database), the command indexes it first and reports `indexed_now: true`; that writes to the tool's own cache. The binary is found via `--cbm /full/path`, then `PATH`, then the macOS cache its launcher downloads to. It is a point-in-time snapshot, not a live index: run the same command again after the code changes. Skills are global and every installed Skill's `SKILL.md` text is sent to every role in every project, so the generated `description` names the repo and tells agents to ignore it elsewhere. To verify, ask for "snapshot canary: report the Function count in the architecture snapshot" and compare the reply with `Function` in `~/.ccc-mcp/skills/codebase-snapshot-<name>/references/architecture.md`.
