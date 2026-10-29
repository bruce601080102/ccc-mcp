# ccc-mcp

**Let the right coding agent do the right job—and keep the handoff verifiable.**

[![CI](https://img.shields.io/github/actions/workflow/status/bruce601080102/ccc-mcp/release.yaml?branch=main&style=flat-square&logo=githubactions&logoColor=white&label=CI)](https://github.com/bruce601080102/ccc-mcp/actions/workflows/release.yaml)
[![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776AB?style=flat-square&logo=python&logoColor=white)](https://www.python.org/downloads/)
[![Platforms](https://img.shields.io/badge/Platforms-macOS%20%7C%20Linux%20%7C%20Windows-555555?style=flat-square)](docs/en/1-install-and-sign-in.md)
[![MCP](https://img.shields.io/badge/Protocol-MCP-6E56CF?style=flat-square)](https://modelcontextprotocol.io/)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue?style=flat-square)](LICENSE)

[![ChatGPT](https://img.shields.io/badge/ChatGPT-PM-10A37F?style=flat-square)](docs/en/2-connect-chatgpt.md)
[![Codex](https://img.shields.io/badge/Codex-SA%20%C2%B7%20REVIEW-000000?style=flat-square)](docs/en/4-workflow.md)
[![Claude Code](https://img.shields.io/badge/Claude%20Code-SD%2FRD-D97757?style=flat-square)](docs/en/4-workflow.md)

ccc-mcp is a local MCP workflow for software projects. ChatGPT manages the request and shared context; Codex can analyse and review; Claude Code can implement. Or, when that is the better fit, ChatGPT can run every workflow role itself. Each handoff retains the evidence needed to continue, review, or recover the work.

It is designed for people who use more than one AI coding tool and do not want a quota limit, lost chat history, or an unverified "tests passed" claim to end a project.

> [Traditional Chinese README](README_ZH.md) · [English documentation](docs/en/1-install-and-sign-in.md)

## Quick start: open the console

Requirements: Git and Python 3.11+. Connecting ChatGPT also requires an [OpenAI Platform Tunnel](docs/en/2-connect-chatgpt.md).

```bash
pip install ccc-mcp
ccc-mcp ui
```

Your browser opens the local console. Then, in the UI:

1. Open **Projects** to create `hello-demo` or add an existing folder. After creating a project, open it and choose **Enable write**, then type the confirmation text. When adding an existing folder, you can select write authorization in the add dialog.
2. In the project's **Settings**, choose a workflow profile, roles, and test command. Choose `chatgpt-only` if you only use ChatGPT. To use Codex or Claude Code, first install and sign in to their native CLIs.
3. Follow [Connect ChatGPT](docs/en/2-connect-chatgpt.md) to create a Tunnel. On the console's **Connect** page, enter its Tunnel ID and runtime API key, then click **Connect**.
4. Select the ccc-mcp connection in ChatGPT and ask: “Create `hello.py` in `hello-demo`; it should print `Hello, World!`, with a test that verifies the output.” Return to **Tasks** and **Context** in the console to inspect progress and evidence.

The console serves only on local `127.0.0.1`; press Ctrl+C to stop it. See [Web console](#web-console) for its other features.

## What it does

- Routes work through configurable `PM → SA → SD/RD → REVIEW → PM` roles.
- Preserves versioned shared context, raw agent reports, diffs, and host-observed test evidence.
- Uses Codex and Claude Code through their native CLIs, or assigns every role to ChatGPT for a ChatGPT-only workflow.
- Falls back only for recognised quota exhaustion, never for a normal install, login, or execution error.
- Shares operator-installed `SKILL.md` guidance across all roles.
- Uses a local web console to manage projects, track tasks, configure roles and connections, and manage Skills and MCP tools.

## Choose your mode

**Best-model workflow** uses ChatGPT as PM, Codex for analysis/review, and Claude Code for implementation. It is the default when you have all three tools.

**ChatGPT-only workflow** sets all five role slots (`SA`, `SD`, `RD`, `SD/RD`, and `REVIEW`) to ChatGPT when you select `chatgpt-only` in the project's **Settings**. The automatic workflow dispatches `SA → SD/RD → REVIEW`. This mode needs no Codex or Claude Code CLI; available models, quotas, and tool access depend on your connected ChatGPT account and plan.

## How the workflow works

```text
ChatGPT (PM) → Codex (SA) → Claude Code (SD/RD) → Codex (REVIEW)
       ↑                                                    │
       └──────── shared context + evidence + next task ─────┘
```

You can change the default roles. In the project's **Settings**, assign a supported provider, effort, and quota fallback to each role; you can also set the test command and report language.

For the complete role and workflow model, see [the workflow guide](docs/en/4-workflow.md) or the [Traditional Chinese guide](docs/ch/4-workflow.md).

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

If you prefer a terminal or write scripts, the CLI operates on the same project data. Before using native Codex or Claude Code roles, run `ccc-mcp setup all` and sign in to those tools. For example, to create a project entirely from the CLI:

```bash
ccc-mcp setup all
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
