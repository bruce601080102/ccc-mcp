# 1. Install and sign in

Requirements: Git, Python 3.11 or later, and a Codex and/or Claude Code account. ChatGPT PM support additionally needs an OpenAI Platform Tunnel; set that up in [step 2](2-connect-chatgpt.md).

## Install ccc-mcp

```bash
pip install ccc-mcp
ccc-mcp setup all
```

`ccc-mcp setup all` then checks, installs when necessary, and signs in to Codex and Claude Code. Use PowerShell or Git Bash on Windows, or bash/zsh on macOS and Linux.

If a Node.js installation asks you to reopen the terminal, reopen it and run `ccc-mcp setup all` again.

To install the current source checkout for development instead:

```bash
git clone https://github.com/bruce601080102/ccc-mcp.git
cd ccc-mcp
source scripts/setup-real-agents.sh
```

## Choose the agents you need

```bash
ccc-mcp setup codex
ccc-mcp setup claude
ccc-mcp setup all
```

The default workflow uses Codex for SA and REVIEW, and Claude Code for SD/RD. You can later assign all roles to Codex or use ChatGPT interactively; see [configuration](5-configuration.md).

Prefer a browser? The Connect page in `ccc-mcp ui` can install, sign in to, and uninstall Codex
and Claude Code too — no terminal needed. The sign-in flow's own output streams into the page,
and any link it prints is a clickable link: open it in a browser on another device when the
computer running `ccc-mcp ui` has no browser of its own (a remote or headless host, for
example), finish signing in there, and come back to the Connect page to confirm it worked.

## Real-agent test suite

Normal tests do not require real accounts. Tests marked `real_agents` require installed, authenticated CLIs and are deliberately opt-in:

```bash
python3.11 -m pytest -m real_agents
```

Continue with [Connect ChatGPT](2-connect-chatgpt.md).
