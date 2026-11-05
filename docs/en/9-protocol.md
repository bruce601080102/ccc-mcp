# 9. Public protocol and schemas

ccc-mcp publishes stable JSON schemas for external consumers. The protocol version changes only for incompatible schema changes; adding an optional field is compatible.

Published schemas are in [`docs/schema`](../schema/):

- `context-snapshot.json`
- `context-patch.json`
- `agent-report.json`

The canonical server exposes project bootstrap, context read/history, task start/advance/dispatch, approval, evidence, usage, verification, and configuration tools. A caller should read a complete context package and its `available_skills` before proposing a context change.

Use the CLI schema export command when distributing contracts to another system. The protocol defines the external data contract; it does not grant filesystem authority or replace local project authorization.

## Local task conversation API

`GET /api/projects/{project_id}/tasks/{task_id}/timeline` returns durable, ordered events with stable `event_id` values, rebuilt from task, execution, host-test, and intervention records. `GET .../artifacts` returns saved originals for this task. `POST .../pause`, `POST .../resume`, `POST .../stop` (optional `reason`), `POST .../delete` (only for completed, cancelled, rejected or failed tasks; the task leaves every list, its audit records stay and its ID is never issued again), and `POST .../messages` (`content`, optional `pause`, default `true`) control workflow. All routes require the local UI session token and a registered project/task. Existing CLI and MCP calls remain available. `get_task_status.brief.control` reports `ACTIVE`, `PAUSING`, `PAUSED`, `STOPPING`, or `CANCELLED`; `task_handoff.pending_user_messages` exposes corrections awaiting delivery.
