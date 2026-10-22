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

### ChatGPT-only workflow (the chatgpt-only profile)

You can run the entire automatic sequence without Codex or Claude Code by applying the `chatgpt-only` profile in one call:

```bash
ccc-mcp --project hello-demo config profile chatgpt-only
ccc-mcp --project hello-demo config show
```

`config profile chatgpt-only` sets all five role slots (`SA`, `SD`, `RD`, `SD/RD`, and `REVIEW`) to `chatgpt` and clears any Codex/Claude-specific `model`, `effort`, or `quota-fallback` left over — nothing carries over. This includes the standalone `SD`/`RD` slots even though automatic advancement (`advance_project`) only ever dispatches `SA`, `SD/RD`, and `REVIEW`: `config show` should never still look like a Codex/Claude mix after switching. To switch back to the best-model workflow, run:

```bash
ccc-mcp --project hello-demo config profile best-model
```

`best-model` is not a fixed combination — it is resolved **dynamically from whichever native CLIs are actually installed on this machine**. With both Codex and Claude Code installed, it keeps the original split (`SA`/`REVIEW` on Codex, `SD`/`RD`/`SD-RD` on Claude). With only one of them installed (say Claude Code but not Codex, or vice versa), it assigns all five roles to that single CLI instead of forcing a combination that can't actually run. With neither installed, `config profile best-model` is rejected outright — install one of them, or use `chatgpt-only` instead.

`config show` and `config providers` both report a read-only `profile` field, derived live from the current provider of every role against what each profile currently means **on this machine** (never stored separately): `chatgpt-only` when all of them are `chatgpt`, `best-model` when they match this machine's current best-model combination, and `custom` for anything else — including a role config that happens to look like the classic Codex/Claude split but where one of those CLIs isn't actually installed.

`config profile` is just a one-shot shortcut over `config role`; you can still set each role individually:

```bash
ccc-mcp --project hello-demo config role SA chatgpt
ccc-mcp --project hello-demo config role SD/RD chatgpt
ccc-mcp --project hello-demo config role REVIEW chatgpt
```

This is not a degraded or fake execution path: ChatGPT completes the role interactively through ccc-mcp's MCP tools, while the host retains Context, file, and test evidence in the normal way (including automatically running any `test_command` configured under `project authorize`). It avoids using Codex or Claude Code CLI allowances. ChatGPT model and tool availability remains subject to the connected account and plan.

For a ChatGPT interactive execution, `task usage` marks `"interactive": true` and `invocation.source: "chatgpt_web"`; `cli_invoked: false` there means "never invokes a CLI by design," not a failure. Only for a native CLI (Codex/Claude Code) execution does `cli_invoked: false` indicate the CLI was never successfully invoked.

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
