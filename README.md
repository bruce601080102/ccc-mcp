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

## What it does

- Routes work through configurable `PM → SA → SD/RD → REVIEW → PM` roles.
- Preserves versioned shared context, raw agent reports, diffs, and host-observed test evidence.
- Uses Codex and Claude Code through their native CLIs, or assigns every role to ChatGPT for a ChatGPT-only workflow.
- Falls back only for recognised quota exhaustion, never for a normal install, login, or execution error.
- Shares operator-installed `SKILL.md` guidance across all roles.

## Choose your mode

**Best-model workflow** uses ChatGPT as PM, Codex for analysis/review, and Claude Code for implementation. It is the default when you have all three tools.

**ChatGPT-only workflow** sets all five role slots (`SA`, `SD`, `RD`, `SD/RD`, and `REVIEW`) to ChatGPT via `config profile chatgpt-only` — including the standalone `SD`/`RD` slots, so neither `config show` nor a manual dispatch is left showing a leftover Codex/Claude provider. The automatic workflow itself still only ever dispatches `SA → SD/RD → REVIEW`. It needs no Codex or Claude Code CLI, so it does not consume their CLI allowances. Available models, quotas, and tool access always follow your connected ChatGPT account and plan — never assume unlimited usage.

## Quick start

Prerequisites: Git, Python 3.11+, and an OpenAI Platform Tunnel. The best-model workflow also needs signed-in Codex and/or Claude Code accounts.

```bash
git clone https://github.com/bruce601080102/ccc-mcp.git
cd ccc-mcp
source scripts/setup-real-agents.sh
# Run this only when using Codex and/or Claude Code:
ccc-mcp setup all

ccc-mcp project create hello-demo
ccc-mcp project authorize hello-demo
```

For a ChatGPT-only workflow, omit `ccc-mcp setup all`, then apply the `chatgpt-only` profile after creating the project:

```bash
ccc-mcp --project hello-demo config profile chatgpt-only
```

Connect the local server to ChatGPT with the Tunnel instructions in [Chinese](docs/ch/2-connect-chatgpt.md) or [English](docs/en/2-connect-chatgpt.md). Then ask ChatGPT, with the ccc-mcp connection selected:

> Create `hello.py` in `hello-demo`; it should print `Hello, World!`, with a test that verifies the output.

Verify the work on the machine that owns the project:

```bash
ccc-mcp --project hello-demo task status
ccc-mcp --project hello-demo task usage
ccc-mcp --project hello-demo context show
```

For a native role, `task usage` is the source of truth for whether its CLI was actually invoked. A ChatGPT role is intentionally interactive and has no native CLI invocation: `task usage` marks it `"interactive": true` with `invocation.source: "chatgpt_web"`, so `cli_invoked: false` there reads as "by design," never as an ambiguous CLI failure. In either mode, a reported success is not sufficient: the configured host test command is recorded before review can complete the task.

`ccc-mcp --project hello-demo config show` (or `config providers`) also reports a read-only `profile` field — `chatgpt-only`, `best-model`, or `custom` — derived live from every role's current provider. Switch back at any time with `ccc-mcp --project hello-demo config profile best-model`, which resolves dynamically from whichever native CLIs are actually installed (both → the Codex/Claude split; only one → every role on it; neither → rejected).

## How the workflow works

```text
ChatGPT (PM) → Codex (SA) → Claude Code (SD/RD) → Codex (REVIEW)
       ↑                                                    │
       └──────── shared context + evidence + next task ─────┘
```

The defaults are deliberately opinionated, not mandatory. Assign any supported provider to a role:

```bash
ccc-mcp --project hello-demo config role REVIEW codex --effort high --quota-fallback chatgpt
ccc-mcp --project hello-demo config role SD/RD claude
ccc-mcp --project hello-demo config show
```

For the complete role and workflow model, see [the workflow guide](docs/en/4-workflow.md) or the [Traditional Chinese guide](docs/ch/4-workflow.md).

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

`project authorize` permits ccc-mcp to modify that project and run its configured test command. Dependency installation can execute package scripts, so review dependency changes as part of the diff. Codex runs in its configured sandbox. Claude Code has a restricted tool policy, but does **not** yet have an equivalent OS-enforced sandbox; use a least-privileged OS account for it.

Done with a project? `ccc-mcp project revoke <id>` (if still authorized) then `ccc-mcp project delete <id>` permanently removes it from `project list` and blocks the ID from ever being used or re-registered again. It never deletes files on disk or any historical Context/task/evidence — delete the project directory yourself once you've confirmed its path. See [reliability and security](docs/en/7-reliability-and-security.md) for the full guarantee.

## Development

```bash
python3.11 -m pytest -q
```

The repository requires Python 3.11 or newer. Real-agent tests are opt-in because they need locally installed, authenticated CLIs; see [Chinese](docs/ch/1-install-and-sign-in.md) or [English](docs/en/1-install-and-sign-in.md).
