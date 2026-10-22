# 8. CLI reference

Use `ccc-mcp --help` for the installed version's complete command list. Set `CCC_MCP_LANG=en` before invoking ccc-mcp for English CLI help; this is separate from the project report language.

## Common commands

```bash
ccc-mcp setup all
ccc-mcp connect tunnel_...
ccc-mcp project create <id> [--path <path>]
ccc-mcp project add <path> --id <id>
ccc-mcp project authorize <id>
ccc-mcp --project <id> task status
ccc-mcp --project <id> task usage
ccc-mcp --project <id> task resume
ccc-mcp --project <id> context show
ccc-mcp --project <id> verify
```

Use `config show`, `config providers`, and `config prompt <ROLE>` before changing roles. Role values include `SA`, `SD`, `RD`, `SD/RD`, and `REVIEW`; automatic workflow advancement uses `SD/RD`.

`config profile chatgpt-only` sets all five roles (`SA`, `SD`, `RD`, `SD/RD`, `REVIEW`) to `chatgpt` in one call, clearing any leftover Codex/Claude `model`/`effort`/`quota-fallback` — including the standalone `SD`/`RD` slots that automatic advancement never dispatches, so `config show` never still looks like a Codex/Claude mix. `config profile best-model` switches back, resolved dynamically from whichever native CLIs are actually installed here: both installed keeps the Codex/Claude split, only one installed assigns every role to it, neither installed is rejected. `config show`/`config providers` report the resulting `profile` (`chatgpt-only`, `best-model`, or `custom`) as a read-only, derived field.

For Skill management, see [step 6](6-skills.md). For public MCP contracts rather than CLI usage, see [step 9](9-protocol.md).
