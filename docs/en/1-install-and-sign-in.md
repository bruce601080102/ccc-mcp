# 1. Install and sign in

ccc-mcp has two setup routes. **Most people only need Route A.**

## Route A (primary): ChatGPT-only — no Codex or Claude Code required

Requirements: Git and Python 3.11 or later. ChatGPT-only also needs an OpenAI Platform Tunnel; set that up in [step 2](2-connect-chatgpt.md).

```bash
pip install ccc-mcp
ccc-mcp ui
```

Then, in the UI:

1. Create or add a project, and authorize it for write access.
2. Connect ChatGPT (see [step 2](2-connect-chatgpt.md)): enter the Tunnel ID and runtime API key in the console.
3. In Settings, select the `chatgpt-only` profile.

You do not need to run `ccc-mcp setup`, and you do not need a Codex or Claude Code account for this route.

Continue with [Connect ChatGPT](2-connect-chatgpt.md).

---

## Route B (advanced): Codex / Claude Code, optional

> Skip this section unless you want to assign some roles to the native Codex or Claude Code CLI instead of ChatGPT. It is never required for `chatgpt-only`.

Requirements: Git, Python 3.11 or later, and a Codex and/or Claude Code account.

### Install ccc-mcp

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

### Choose the agents you need

```bash
ccc-mcp setup codex
ccc-mcp setup claude
ccc-mcp setup all
```

The default role assignment for this route uses Codex for SA and REVIEW, and Claude Code for SD/RD. You can later assign all roles to one of them, or switch back to ChatGPT interactively; see [configuration](5-configuration.md).

Prefer a browser? The Settings page in `ccc-mcp ui` can install, sign in to, and uninstall Codex
and Claude Code too — no terminal needed. The sign-in flow's own output streams into the page,
and any link it prints is a clickable link: open it in a browser on another device when the
computer running `ccc-mcp ui` has no browser of its own (a remote or headless host, for
example), finish signing in there, and come back to the Settings page to confirm it worked.

## Real-agent test suite

Normal tests do not require real accounts. Tests marked `real_agents` require installed, authenticated CLIs (Route B) and are deliberately opt-in:

```bash
python3.11 -m pytest -m real_agents
```

Continue with [Connect ChatGPT](2-connect-chatgpt.md).
