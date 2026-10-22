# 10. Technical reference

## Project records

ccc-mcp stores readable project summaries under `.ccc-mcp/` and preserves raw task reports and evidence under `.ccc-mcp/tasks/`. The authoritative store retains context versions, task state, executions, approvals, and the evidence references used to create a context patch.

## Workspaces

Implementation roles normally use a task worktree. Project workspace mode is supported when direct work in the project is required, but it is exclusive: concurrent implementation work cannot safely write into the same project workspace.

## Context

The handoff package contains the current context, relevant task information, evidence, reports, role instructions, and global Skills. A patch cites source execution IDs and verified repository references. Optimistic context versions prevent two PM sessions from committing contradictory histories.

## Observability

`task usage` reports the provider, model settings, native CLI invocation status, and available usage data. It is intentionally distinct from a subscription invoice or a remaining-quota guarantee. Use `verify` to detect evidence drift or failed hash checks.

For the design rationale and state machine, continue with [Architecture specification](11-architecture-spec.md).
