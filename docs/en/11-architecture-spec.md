# 11. Architecture specification

ccc-mcp is a local orchestration layer, not a replacement coding model. Its central contract is that handoffs must be durable and evidence-backed.

## Components

- **MCP server / service:** validates authority, exposes tools, builds context packages, and owns durable state.
- **Task hub:** maintains the task state machine, approvals, resumable dispatch pointers, and configured role resolution.
- **Agent adapters:** invoke native Codex or Claude Code CLIs with role-specific policies; ChatGPT is an interactive MCP-backed role.
- **Worker:** executes an agent task, captures reports, native CLI audit information, worktree diffs, and quota fallback outcomes.
- **Context store:** versions shared project knowledge and verifies cited source references before a patch becomes canonical.
- **Host test runner:** executes the configured test command and records its output as evidence independent of any agent claim.
- **Web console (`ccc-mcp ui`):** a fourth thin interface, alongside the CLI and MCP server, over the same service — a local-only HTTP API plus a static, zero-build frontend bundled into the wheel. It authenticates every request with a per-launch session token instead of the CLI's TTY check (see `authorize_project_web`/`deregister_project_web`/`clear_project_history_web`), and never binds beyond `127.0.0.1`.

## Design rules

1. A role cannot advance on an uncommitted or stale Context version.
2. A reviewer cannot override host-observed failed or absent tests.
3. A failed dispatch must remain resumable without duplicating a role execution.
4. Fallback is specific to classified quota exhaustion and runs at most once per original execution.
5. Project authorization is explicit, scoped, and does not extend to arbitrary filesystem access.
6. An existing read-only project is never upgraded to read-write as a side effect of another action (viewing it, registering a sibling project, a background sync); enabling write is always its own explicit, disclosed step, in every interface.

The implementation-oriented Chinese design record remains in [中文架構規格](../ch/11-architecture-spec.md). When changing behavior, update this concise architecture contract and the public schemas together.
