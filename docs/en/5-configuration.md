# 5. Configure roles, context, and tests

Inspect settings before changing them:

```bash
ccc-mcp --project hello-demo config show
ccc-mcp --project hello-demo config providers
```

## Roles and fallback

```bash
ccc-mcp --project hello-demo config role REVIEW codex --effort high --quota-fallback chatgpt
ccc-mcp --project hello-demo config role SD/RD claude
ccc-mcp --project hello-demo config language en
```

Each `config role` command replaces that role's complete setting, so repeat options you want to retain. Automatic advancement uses the combined `SD/RD` role, not standalone `SD` or `RD` settings.

`--quota-fallback` only activates after a classified quota exhaustion. It does not mask an absent CLI, sign-in failure, or ordinary agent error.

### ChatGPT-only workflow

You can run the entire automatic sequence without Codex or Claude Code by assigning its three automatic roles to ChatGPT:

```bash
ccc-mcp --project hello-demo config role SA chatgpt
ccc-mcp --project hello-demo config role SD/RD chatgpt
ccc-mcp --project hello-demo config role REVIEW chatgpt
```

This is not a degraded or fake execution path: ChatGPT completes the role interactively through ccc-mcp's MCP tools, while the host retains Context, file, and test evidence in the normal way. It avoids using Codex or Claude Code CLI allowances. ChatGPT model and tool availability remains subject to the connected account and plan.

## Context and prompts

Use a context budget for long-running projects:

```bash
ccc-mcp --project hello-demo config context-budget 150000
ccc-mcp --project hello-demo config context-budget --clear
ccc-mcp --project hello-demo config prompt SA
```

ccc-mcp first omits optional Skill bodies and then compresses older verified history. It never silently truncates current reports, invariants, or cited evidence; dispatch is rejected if the package remains too large.

## Tests

```bash
ccc-mcp --project hello-demo config test-command "pytest -q"
ccc-mcp --project hello-demo config test-command --clear
```

Project authorization enables automatic host-side testing before review. Python projects use a project-specific virtual environment; dependency files may be installed before testing, which means their install scripts are inside the authorization boundary.
