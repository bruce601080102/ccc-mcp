# 13. Meeting Mode end-to-end walkthrough

A reproducible procedure for verifying Meeting Mode's SD/RD<->SA clarification round end-to-end, with ChatGPT still acting as PM throughout. Follow it once against a real project before relying on Meeting Mode, and treat the steps below — not a chat reply alone — as the evidence.

**Roles:** this walkthrough configures `SA` and `SD/RD` to a native CLI provider (Codex and/or Claude Code) and keeps ChatGPT as PM, as in the default best-model workflow. Meeting Mode also works when ChatGPT performs `SA` and/or `SD/RD` itself (for example the `chatgpt-only` profile): each side then arrives as a read-only `CHATGPT_HANDOFF_PENDING` handoff, SD/RD's check is submitted with `submit_clarification_request` and SA's answer with `submit_clarification_response` (never `submit_agent_report`), and the exchange continues automatically. Note that in that case one ChatGPT conversation is playing both sides, so it is a structured self-check recorded in the same history, not two independent opinions.

## See a real run first

A real ChatGPT conversation that ran this procedure against the `meeting-test` project: [shared conversation](https://chatgpt.com/share/6adb059b-3eb8-83e8-96f2-84965ea20c56). Use it to see what the connected ChatGPT shows while Meeting Mode runs before you try it yourself. What ChatGPT relays in chat can be incomplete, so read the full SA/SD exchange locally with `ccc-mcp --project <id> task meeting` (see [step 6](#6-read-the-full-clarification-record)).

## What counts as success

- `config show` reports `meeting_mode.enabled: true` with the `min_rounds`/`max_rounds`/`escalation` you configured.
- SD/RD's first dispatch for the task is a read-only clarification check — `task status`'s `brief.clarification.current_question` is populated and the task state is `AWAITING_SA_CLARIFICATION`, with no worktree/file write having happened yet. `brief.clarification.triggered` flips to `true` at this point too — this is the one field that tells you the gate actually fired, as opposed to `enabled: true` just meaning it's configured.
- SA answers the question (`confirmed`, `recommendation`, or escalates with `pm_decision_required`); `brief.clarification.rounds_used` increments only once the round is actually resolved.
- The real, writable SD/RD implementation only ever runs after that round resolves (or after `no_blocking_question`/`max_rounds` skips straight to it).
- `task status`/`task show` show the full `clarification.history` for the task; the same block is also in `task handoff`'s output, which is what the connected ChatGPT conversation's `wait_for_task` loop actually sees on every iteration.

## 1. Create a project with Meeting Mode enabled

```bash
ccc-mcp project create meeting-mode-demo
ccc-mcp project authorize meeting-mode-demo
ccc-mcp --project meeting-mode-demo config role SA codex
ccc-mcp --project meeting-mode-demo config role SD/RD claude
ccc-mcp --project meeting-mode-demo config role REVIEW codex
ccc-mcp --project meeting-mode-demo config meeting-mode on --min-rounds 1 --max-rounds 3 --escalation ask-user
ccc-mcp --project meeting-mode-demo config show
```

Confirm `config show`'s `meeting_mode` reports `{"enabled": true, "min_rounds": 1, "max_rounds": 3, "escalation": "ask_user"}`.

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
    "escalation": "ask_user",
    "triggered": true,
    "rounds_used": 0,
    "current_question": "..."
  }
}
```

`clarification.triggered: true` is the signal that the gate actually fired this time — it is only ever `true` once a real blocking question exists, never merely because `enabled` is `true`. If SD/RD instead reported `no_blocking_question`, `state` stays `IMPLEMENTING`, the real implementation dispatch has already started, and `clarification.triggered` stays `false` (along with `rounds_used: 0` and `current_question: null`) — with `min_rounds` set that is flagged as `gate.min_rounds_unmet: true` rather than blocked.

## 4. SA answers; implementation proceeds automatically

No PM action is needed for `confirmed`/`recommendation` — SA's answer dispatches automatically, and once it resolves, ccc-mcp automatically dispatches either another clarification round (if `rounds_used < max_rounds`) or the real, writable SD/RD implementation. Keep polling:

```bash
ccc-mcp --project meeting-mode-demo task status
```

until `brief.clarification.rounds_used` reflects a resolved round and `brief.role`/`brief.execution_status` show a real SD/RD implementation running.

### If SA escalates with `pm_decision_required`

`task status`'s `brief.waiting_on` becomes `PM_CLARIFICATION_DECISION`, and `brief.clarification.pending_pm_decision` carries the question and SA's recommendation. Who decides depends on `brief.clarification.escalation`:

- **`ask_user` (default).** This is a genuine stop requiring the real operator's decision — `task handoff`'s `next_action` says so explicitly, and `wait_for_task`'s `must_continue_in_this_turn` is `false` here, confirming ChatGPT should not decide this on its own. Ask the connected ChatGPT conversation to put the question and SA's recommendation in front of you and wait for your actual reply:

  > Use ccc-mcp's resolve_clarification tool on this task with your decision, citing SA's recommendation above.
- **`auto_pm`.** ChatGPT may decide immediately using its own judgement as PM and call `resolve_clarification` in the same turn (`must_continue_in_this_turn` is `true`); it should still tell you what it decided and why afterward.

Either way, `resolve_clarification` immediately dispatches the next round or the real implementation; no separate `advance_project` call is needed for this step.

## 5. Let REVIEW and host testing complete the task

From here the task proceeds exactly like any other: `task handoff`/`task status` show SD/RD's implementation, then host-run tests, then REVIEW. See [step 4](4-workflow.md) and [step 12](12-chatgpt-only-e2e-walkthrough.md) for that part of the loop (substituting the native-CLI roles configured above for `SA`/`SD/RD`/`REVIEW`; PM stays ChatGPT).

## 6. Read the full clarification record

```bash
ccc-mcp --project meeting-mode-demo task meeting      # readable, unclipped SA/SD transcript
ccc-mcp --project meeting-mode-demo task status
ccc-mcp --project meeting-mode-demo task show
```

`brief.clarification.history` lists every round for the task (question, who answered, `response_type`, and any PM decision), in order — this is the complete, traceable record; a chat reply summarizing "I asked and it was fine" is never a substitute for it.

## What to do on failure

If ChatGPT submits with the wrong tool, the error names the right one (`submit_clarification_request` / `submit_clarification_response` for Meeting Mode handoffs, `submit_agent_report` otherwise). If `task status` shows `waiting_on: "DISPATCH_RETRY_NEEDED"` at any point in the clarification round, run `ccc-mcp --project meeting-mode-demo task resume` exactly as for any other interrupted dispatch — the round count is never lost or duplicated by this.

Further reading: [reliability and security](7-reliability-and-security.md#meeting-mode).
