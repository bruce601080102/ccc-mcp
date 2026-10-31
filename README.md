# ccc-mcp

**Let ChatGPT Chat write, test, and review code on your own computer.**

[![CI](https://img.shields.io/github/actions/workflow/status/bruce601080102/ccc-mcp/release.yaml?branch=main&style=flat-square&logo=githubactions&logoColor=white&label=CI)](https://github.com/bruce601080102/ccc-mcp/actions/workflows/release.yaml)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/downloads/)
[![Platforms](https://img.shields.io/badge/Platforms-macOS%20%7C%20Linux%20%7C%20Windows-555555?style=flat-square)](docs/en/1-install-and-sign-in.md)
[![MCP](https://img.shields.io/badge/Protocol-MCP-6E56CF?style=flat-square)](https://modelcontextprotocol.io/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue?style=flat-square)](LICENSE)

[![ChatGPT](https://img.shields.io/badge/ChatGPT-Core%20engine-10A37F?style=flat-square)](docs/en/2-connect-chatgpt.md)
[![Codex](https://img.shields.io/badge/Codex-Optional-000000?style=flat-square)](docs/en/4-workflow.md)
[![Claude Code](https://img.shields.io/badge/Claude%20Code-Optional-D97757?style=flat-square)](docs/en/4-workflow.md)

ccc-mcp connects ChatGPT Chat to a local coding workspace through MCP. Ask in ChatGPT; it can work on an authorized repository, make changes, run host-side tests, and show the diff and results. Use your existing ChatGPT plan instead of a separately metered model API. **ChatGPT-only is the primary workflow.** Codex and Claude Code are optional providers for teams that want to split roles across tools.

> [繁體中文 README](README_ZH.md) · [English documentation](docs/en/1-install-and-sign-in.md)

## Start with the UI

Install ccc-mcp, then open its local control center:

```bash
pip install ccc-mcp
ccc-mcp ui
```

Requires Git and Python 3.11+. In the UI:

1. **Projects:** create a project or add an existing folder, then enable write access for the project.
2. **Connect:** follow [Connect ChatGPT](docs/en/2-connect-chatgpt.md) to create an OpenAI Platform Tunnel. Enter its Tunnel ID and runtime API key in the console.
3. **Settings:** select the `chatgpt-only` profile. The **Test command** can be left blank: ccc-mcp installs declared Python dependencies and chooses tests from changed test files when possible. Set a command when you need a specific scope.
4. **ChatGPT:** select the ccc-mcp connection and ask it to make a change. Inspect **Tasks** and **Context** in the UI to see the actual edits, test results, and next step.

The UI runs on local `127.0.0.1` and stops with Ctrl+C. [See every console page](#web-console).

**About tokens and keys:** This workflow uses your existing ChatGPT plan instead of an OpenAI *model* API key and its per-token API billing. [ChatGPT plan limits](https://learn.chatgpt.com/docs/pricing) still apply. The Tunnel connection uses its own runtime API key; [connection setup](docs/en/2-connect-chatgpt.md) explains that key.

## What makes ccc-mcp useful

| Capability | What you get |
| --- | --- |
| **ChatGPT Chat as the coding agent** | ChatGPT can read and edit an authorized local project, then continue the work in the same conversation. No Codex or Claude Code installation is required for `chatgpt-only`. |
| **Skills, including MCP-oriented Skills** | Install reusable `SKILL.md` guidance, including nested supporting files. Choose global Skills in the UI so every project and role can use them. A Skill can guide an agent to use an MCP tool after that server is installed. |
| **MCP servers and tools** | Discover local MCP servers, install one for a project, select which tools the project may use, and call them from ChatGPT. For example, `codebase-memory-mcp` can provide repository structure and code relationships. Skills guide the workflow; MCP tools supply live capabilities. |
| **A recoverable workflow** | Run `SA → SD/RD → REVIEW` with versioned shared Context and saved execution records. Resume a task in another chat from the project’s `.ccc-mcp` data. |
| **Host-side verification** | Install declared dependencies, run pytest, and retain host-observed results, exit codes when available, diffs, reports, and review evidence. |
| **One local control center** | Manage projects, permissions, connections, global Skills, per-project MCP tools, tasks, and Context from `ccc-mcp ui`. The CLI remains available for scripts and advanced use. |

## Proof: three selected SWE-bench Verified cases

Using **ChatGPT-only**, ccc-mcp resolved three selected [SWE-bench Verified](https://www.swebench.com/) Astropy instances after iterative repair. Each has a local run of the official evaluation harness reporting `resolved: true`:

| Instance | Harness report |
| --- | --- |
| `astropy__astropy-12907` | [report.json](benchmarks/swebench-verified/12907-report.json) |
| `astropy__astropy-13236` | [report.json](benchmarks/swebench-verified/13236-report.json) |
| `astropy__astropy-13398` | [report.json](benchmarks/swebench-verified/13398-report.json) |

[Read the evidence and methodology](benchmarks/swebench-verified/README.md), including run IDs and ChatGPT conversations. These are **3 selected cases**, not a full-benchmark score or an official leaderboard submission.

## ChatGPT first; other agents are optional

With the `chatgpt-only` profile selected, ChatGPT handles the project manager and `SA → SD/RD → REVIEW` roles. The workflow records each handoff so the next chat can pick up the saved state.

```text
ChatGPT Chat → local project → tests, diff, review
       ↑                            │
       └──── saved Context and next step ────┘
```

If you want a multi-agent workflow, assign supported roles to Codex or Claude Code through their native CLIs. Configure providers, effort, quota fallback, test command, and report language in **Settings**. See the [workflow guide](docs/en/4-workflow.md).

## Web console

```bash
ccc-mcp ui
```

Starts a local web console in the same process that already serves the CLI and MCP server — no separate frontend to clone, build, or run, and no Node.js required even if you build from source (the UI ships as plain HTML/CSS/JS bundled into the `ccc-mcp` wheel). It binds `127.0.0.1` only, prints its URL, and opens your browser; pass `--port` to pin a port (ccc-mcp reports clearly if that port is taken) or leave it out to pick a free one automatically, and `--no-browser` to skip the auto-open. Press Ctrl+C to stop it. It reads the same `--home` database the CLI and MCP server use, so projects and tasks are always in sync across all three.

The console's main pages:

| Page | What you can do |
| --- | --- |
| **Projects** | Create projects, add existing folders, and organize groups; enable or revoke write access after adding a project. |
| **Settings** | Set the profile, providers and roles, report language, test command, and Meeting Mode. |
| **Tasks / Context** | Inspect stages, waiting reasons, execution history, usage and CLI invocation evidence, raw results, and shared context. |
| **Connect** | Connect ChatGPT with an OpenAI Platform Tunnel ID and runtime API key; inspect status and logs. |
| **Global Skills** | Select local Claude Code or Codex Skills to install for every project and role; inspect or remove installed Skills. |
| **Skills & MCP** | View shared Skills; discover, install, and manage local stdio MCP servers and their tools for one project. |
| **Documentation** | Read the bundled English and Traditional Chinese guides inside the console. |

In **Skills & MCP**, inspect tools, choose which ones this project may use, invoke enabled tools with JSON arguments, and view recent MCP calls. Globally installed MCP servers also appear there, but still require the CLI to remove. The sidebar's interface language is separate from each project's report language. **Connect** can remember a runtime API key on this device (`~/.ccc-mcp/tunnel/credentials.json`, readable only by your OS account); stored keys are never returned to the browser. Enabling write, clearing history, and deleting require typed confirmation. See [reliability and security](docs/en/7-reliability-and-security.md#web-console) for details.

On **Projects**, create named groups, move cards with the dropdown or drag-and-drop, and reorder groups. This only changes local console organization (`<home>/ui/project_groups.json`), not project paths or settings. **Connect** can save multiple named profiles, including different keys for the same Tunnel ID; select a saved profile and click Connect to use it. One Tunnel connection runs at a time.

## CLI: automation and advanced use

If you prefer a terminal or write scripts, the CLI operates on the same project data. `ccc-mcp setup all` and signing in to Codex or Claude Code are only needed if you plan to use those **optional** native roles. For example, to create a project entirely from the CLI:

```bash
ccc-mcp project create hello-demo --write
ccc-mcp --project hello-demo task status
ccc-mcp --project hello-demo task usage
ccc-mcp --project hello-demo context show
```

`--write` asks for local confirmation while creating the project, authorizing ccc-mcp to write there and run its configured test command. For an existing folder, use `ccc-mcp project add <folder> --write`. If you only use ChatGPT, skip `setup all` and select `chatgpt-only` in the project's **Settings**, or run `ccc-mcp --project hello-demo config profile chatgpt-only`.

For native roles, `task usage` shows whether the CLI was actually invoked. ChatGPT roles are interactive: `task usage` marks them `"interactive": true` with `invocation.source: "chatgpt_web"`, so `cli_invoked: false` is expected. See the [CLI reference](docs/en/8-cli-reference.md) for all commands.

## Documentation

Read the numbered documents in order for a full setup. The two language trees use the same filenames and sequence.

| Step | Topic |
| --- | --- |
| 1 | Install and sign in |
| 2 | Connect ChatGPT |
| 3 | Create and authorize a first project |
| 4 | Run and resume the workflow |
| 5 | Configure providers, roles, context, and tests |
| 6 | Install and use skills |
| 7 | Understand reliability and security boundaries |
| 8–11 | CLI reference, public protocol, technical reference, and architecture |

- [Traditional Chinese README](README_ZH.md)
- [English documentation index](docs/en/README.md)

## Important boundary

Continuous write authorization — granted via `project authorize`, `project add --write`/`project create --write`, or the web console's "Enable write" — permits ccc-mcp to modify that project and run its configured test command. Dependency installation can execute package scripts, so review dependency changes as part of the diff. Codex runs in its configured sandbox. Claude Code has a restricted tool policy, but does **not** yet have an equivalent OS-enforced sandbox; use a least-privileged OS account for it.

To remove a project from `project list` while keeping its folder, use `ccc-mcp project remove <id>` (or the web console's **Remove, keep folder** action). This archives ccc-mcp history and lets you register the same ID again. Add `--delete-files` only when you also want to permanently delete the project's directory; the CLI and web console require a separate typed confirmation. Running executions must finish or be cancelled first. `project delete` is different: it permanently retires the ID and preserves historical records and files. See [reliability and security](docs/en/7-reliability-and-security.md) for the full guarantee.

## Development

```bash
python3.11 -m pytest -q
```

The repository requires Python 3.11 or newer. Real-agent tests are opt-in because they need locally installed, authenticated CLIs; see [Chinese](docs/ch/1-install-and-sign-in.md) or [English](docs/en/1-install-and-sign-in.md).
