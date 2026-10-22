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

For Skill management, see [step 6](6-skills.md). For public MCP contracts rather than CLI usage, see [step 9](9-protocol.md).
