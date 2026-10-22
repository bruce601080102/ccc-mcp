# 11. Architecture specification

ccc-mcp is a local orchestration layer, not a replacement coding model. Its central contract is that handoffs must be durable and evidence-backed.

## Components

- **MCP server / service:** validates authority, exposes tools, builds context packages, and owns durable state.
- **Task hub:** maintains the task state machine, approvals, resumable dispatch pointers, and configured role resolution.
- **Agent adapters:** invoke native Codex or Claude Code CLIs with role-specific policies; ChatGPT is an interactive MCP-backed role.
- **Worker:** executes an agent task, captures reports, native CLI audit information, worktree diffs, and quota fallback outcomes.
- **Context store:** versions shared project knowledge and verifies cited source references before a patch becomes canonical.
- **Host test runner:** executes the configured test command and records its output as evidence independent of any agent claim.

## Design rules

1. A role cannot advance on an uncommitted or stale Context version.
2. A reviewer cannot override host-observed failed or absent tests.
3. A failed dispatch must remain resumable without duplicating a role execution.
4. Fallback is specific to classified quota exhaustion and runs at most once per original execution.
5. Project authorization is explicit, scoped, and does not extend to arbitrary filesystem access.

The implementation-oriented Chinese design record remains in [中文架構規格](../ch/11-architecture-spec.md). When changing behavior, update this concise architecture contract and the public schemas together.
