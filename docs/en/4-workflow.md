# 4. Run and resume the workflow

The default path is:

```text
ChatGPT (PM) → Codex (SA) → Claude Code (SD/RD) → Codex (REVIEW) → ChatGPT (PM)
```

The PM creates a requirement, dispatches SA, turns the report into a versioned context patch and plan, dispatches SD/RD, records host-run tests, then sends REVIEW. A review that requests changes returns the task to SD/RD; only observed test evidence can complete it.

## Work safely through the MCP tools

1. Start with `bootstrap_project` and `get_context`.
2. Call `start_project_request` with the requirement and acceptance criteria.
3. Read the completed agent report and cited evidence.
4. Use `propose_context_patch`, then `advance_project` to commit PM decisions and dispatch the next role.
5. Inspect `task status`, `task usage`, and `context show` locally when needed.

The workflow is intentionally resumable. If a context patch committed but the following dispatch failed, status reports `DISPATCH_RETRY_NEEDED`; run:

```bash
ccc-mcp --project hello-demo task resume
```

This resumes the recorded next action rather than creating a new task or losing the completed role's report.

See [reliability and security](7-reliability-and-security.md) for test gates, quota fallback, and recovery guarantees.
