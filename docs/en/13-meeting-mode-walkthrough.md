# 13. Meeting Mode end-to-end walkthrough

A reproducible procedure for verifying Meeting Mode's SD/RD<->SA clarification round end-to-end, with ChatGPT still acting as PM throughout. Follow it once against a real project before relying on Meeting Mode, and treat the steps below — not a chat reply alone — as the evidence.

**Scope limitation:** Meeting Mode's clarification exchange does not yet support the interactive ChatGPT role (see [reliability and security](7-reliability-and-security.md#meeting-mode)) — `dispatch_agent` refuses it outright with a clear error. This walkthrough therefore configures `SA` and `SD/RD` to a native CLI provider (Codex and/or Claude Code), not `chatgpt`; only PM stays ChatGPT, exactly as in the default best-model workflow. A future version may lift this limitation.

## What counts as success

- `config show` reports `meeting_mode.enabled: true` with the `min_rounds`/`max_rounds` you configured.
- SD/RD's first dispatch for the task is a read-only clarification check — `task status`'s `brief.clarification.current_question` is populated and the task state is `AWAITING_SA_CLARIFICATION`, with no worktree/file write having happened yet.
- SA answers the question (`confirmed`, `recommendation`, or escalates with `pm_decision_required`); `brief.clarification.rounds_used` increments only once the round is actually resolved.
- The real, writable SD/RD implementation only ever runs after that round resolves (or after `no_blocking_question`/`max_rounds` skips straight to it).
- `task status`/`task show` show the full `clarification.history` for the task.

## 1. Create a project with Meeting Mode enabled

```bash
ccc-mcp project create meeting-mode-demo
ccc-mcp project authorize meeting-mode-demo
ccc-mcp --project meeting-mode-demo config role SA codex
ccc-mcp --project meeting-mode-demo config role SD/RD claude
ccc-mcp --project meeting-mode-demo config role REVIEW codex
ccc-mcp --project meeting-mode-demo config meeting-mode on --min-rounds 1 --max-rounds 3
ccc-mcp --project meeting-mode-demo config show
```

Confirm `config show`'s `meeting_mode` reports `{"enabled": true, "min_rounds": 1, "max_rounds": 3}`.

## 2. Connect ChatGPT and submit a deliberately underspecified request

Connect per [Connect ChatGPT](2-connect-chatgpt.md), keep that terminal running, then ask (deliberately vague, to make a clarification question likely):

> Use ccc-mcp to start a project request in `meeting-mode-demo`: add a small utility function to this project. Complete SA, then let SD/RD and REVIEW run to completion.

## 3. Watch the clarification round instead of a normal implementation dispatch

```bash
ccc-mcp --project meeting-mode-demo task status
```

Once SA's plan is approved and Meeting Mode's gate fires, `task status`'s `brief` should show:

```jsonc
{
  "state": "AWAITING_SA_CLARIFICATION",
  "waiting_on": "AGENT_RUNNING",
  "role": "SA",
  "clarification": {
    "enabled": true,
    "min_rounds": 1,
    "max_rounds": 3,
    "rounds_used": 0,
    "current_question": "..."
  }
}
```

If SD/RD instead reported `no_blocking_question`, `state` stays `IMPLEMENTING` and the real implementation dispatch has already started — `clarification.rounds_used` stays `0` and `current_question` is `null`; that is also a valid, successful run (min_rounds never blocks it).

## 4. SA answers; implementation proceeds automatically

No PM action is needed for `confirmed`/`recommendation` — SA's answer dispatches automatically, and once it resolves, ccc-mcp automatically dispatches either another clarification round (if `rounds_used < max_rounds`) or the real, writable SD/RD implementation. Keep polling:

```bash
ccc-mcp --project meeting-mode-demo task status
```

until `brief.clarification.rounds_used` reflects a resolved round and `brief.role`/`brief.execution_status` show a real SD/RD implementation running.

### If SA escalates with `pm_decision_required`

`task status`'s `brief.waiting_on` becomes `PM_CLARIFICATION_DECISION`, and `brief.clarification.pending_pm_decision` carries the question and SA's recommendation. This is the one case that needs a PM action — ask the connected ChatGPT conversation to call `resolve_clarification` with a decision:

> Use ccc-mcp's resolve_clarification tool on this task with your decision, citing SA's recommendation above.

`resolve_clarification` immediately dispatches the next round or the real implementation; no separate `advance_project` call is needed for this step.

## 5. Let REVIEW and host testing complete the task

From here the task proceeds exactly like any other: `task handoff`/`task status` show SD/RD's implementation, then host-run tests, then REVIEW. See [step 4](4-workflow.md) and [step 12](12-chatgpt-only-e2e-walkthrough.md) for that part of the loop (substituting the native-CLI roles configured above for `SA`/`SD/RD`/`REVIEW`; PM stays ChatGPT).

## 6. Read the full clarification record

```bash
ccc-mcp --project meeting-mode-demo task status
ccc-mcp --project meeting-mode-demo task show
```

`brief.clarification.history` lists every round for the task (question, who answered, `response_type`, and any PM decision), in order — this is the complete, traceable record; a chat reply summarizing "I asked and it was fine" is never a substitute for it.

## What to do on failure

If a dispatch is rejected with "does not yet support the interactive ChatGPT role," a role is still configured to `chatgpt` — re-check `config show` and assign `SA`/`SD/RD` to a native CLI provider. If `task status` shows `waiting_on: "DISPATCH_RETRY_NEEDED"` at any point in the clarification round, run `ccc-mcp --project meeting-mode-demo task resume` exactly as for any other interrupted dispatch — the round count is never lost or duplicated by this.

Further reading: [reliability and security](7-reliability-and-security.md#meeting-mode).
