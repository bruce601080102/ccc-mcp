# 9. Public protocol and schemas

ccc-mcp publishes stable JSON schemas for external consumers. The protocol version changes only for incompatible schema changes; adding an optional field is compatible.

Published schemas are in [`docs/schema`](../schema/):

- `context-snapshot.json`
- `context-patch.json`
- `agent-report.json`

The canonical server exposes project bootstrap, context read/history, task start/advance/dispatch, approval, evidence, usage, verification, and configuration tools. A caller should read a complete context package and its `available_skills` before proposing a context change.

Use the CLI schema export command when distributing contracts to another system. The protocol defines the external data contract; it does not grant filesystem authority or replace local project authorization.
