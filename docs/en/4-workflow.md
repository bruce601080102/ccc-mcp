# 4. Run and resume the workflow

The default path is:

```text
ChatGPT (PM) → Codex (SA) → Claude Code (SD/RD) → host tests → Codex (REVIEW)
```

The PM creates a requirement and turns the SA report into a versioned context patch and plan. Thereafter, a successful low-risk SD/RD report is automatically written into Context, followed by host-run tests and REVIEW; a successful low-risk REVIEW report completes the task. Failures, unverified tests, revision requests, and decisions remain pending for PM review. Only observed host test evidence can complete a task.

## Keep waiting in the same ChatGPT turn

For a new MCP-driven change, `start_project_request` dispatches SA and waits for progress; `advance_project` also waits after dispatching the next role. If one long-poll expires while an Agent is still running, `workflow.must_continue_in_this_turn` is `true`: ChatGPT must call `wait_for_task` again in the same turn, not answer “still running” and stop. When SA finishes, ChatGPT performs the provenance-backed PM refinement and plan in that same turn, calls `advance_project`, then keeps waiting through SD/RD, host tests, and REVIEW.

The loop may stop only at completion, a genuine failure/blocker, or a decision/approval requiring new user information or authority. An explicit status-only request is the exception and must not advance the task. ccc-mcp does not fabricate the PM's semantic review of an SA report; it returns the actionable handoff and `must_continue_in_this_turn` so the calling ChatGPT can make that judgment before continuing.

## Work safely through the MCP tools

1. Start with `bootstrap_project` and `get_context`.
2. Call `start_project_request` with the requirement and acceptance criteria.
3. Read the completed agent report and cited evidence when `context_sync.needs_pm_refinement` is true.
4. Use `propose_context_patch`, then `advance_project` only for SA planning or an exceptional result that needs a PM decision.
5. Inspect `task status`, `task usage`, and `context show` locally when needed.

The workflow is intentionally resumable. If a context patch committed but the following dispatch failed, status reports `DISPATCH_RETRY_NEEDED`; run:

```bash
ccc-mcp --project hello-demo task resume
```

This resumes the recorded next action rather than creating a new task or losing the completed role's report.

## Meeting Mode: an optional clarification round before SD/RD writes anything

Off by default. `ccc-mcp --project hello-demo config meeting-mode on --min-rounds 1 --max-rounds 3 --escalation ask-user` inserts a bounded round between the approved plan and SD/RD's first implementation attempt: SD/RD may raise exactly one blocking question (read-only — it cannot write files while this is open), SA answers `confirmed`, `recommendation`, or `pm_decision_required`. SD/RD can also report `no_blocking_question` and go straight to implementation; but if `min_rounds` is set, until that many rounds are done SD/RD is told it MUST ask and the next round follows; if SD/RD still declines, the workflow does not deadlock and `clarification.gate.min_rounds_unmet` reports it. The round count is capped at 5, persists across any retry or resume, and the gate only ever fires once per task — a later REVIEW-requested revision never re-triggers it.

`task status`/`task handoff`'s `clarification.gate` proves the gate ran: it is `null` until SD/RD's read-only pre-implementation check is dispatched, then reports its `status`, `outcome` (`question_asked` or `no_blocking_question`) and SD/RD's own `rationale` — so "checked, nothing to ask" is distinguishable from "Meeting Mode never ran". `clarification.triggered` is only `true` once SD/RD has actually asked a real blocking question for the task — never merely because `meeting_mode.enabled` is `true`. Every `wait_for_task`/`start_project_request`/`advance_project`/`dispatch_agent` result carries this (under `workflow.handoff.clarification`), so the connected ChatGPT conversation is expected to tell the operator when the gate actually fires and summarize the SA/SD exchange from `clarification.history`, not just mention it once the task completes.

`--escalation` decides who resolves a `pm_decision_required` round (SA/SD/RD couldn't agree): `ask_user` (default) requires ChatGPT to present the question and SA's recommendation to the real operator in chat and wait for their actual reply before calling `resolve_clarification` — `workflow.must_continue_in_this_turn` is `false` for this case, confirming it is a genuine stop. `auto_pm` lets ChatGPT decide immediately using its own judgement as PM in the same turn (`must_continue_in_this_turn` is `true`), after which it should still tell the operator what it decided and why. This is instruction-driven, not code-enforced — see [reliability and security](7-reliability-and-security.md#meeting-mode) for that boundary and the full set of guarantees.

See [reliability and security](7-reliability-and-security.md) for test gates, quota fallback, and recovery guarantees.

## Workflows and review gates

A new task runs the workflow the project selected when the task starts (`config workflow classic|full`, or Settings → Workflow). A task keeps that workflow for its whole life; changing the setting later never moves work in flight.

| Workflow | Steps |
|---|---|
| `standard` (default for projects added with the CLI or the console) | SA → SD → RD → CR. Design and development are separate steps: SD produces a design report (read-only), then RD implements it. |
| `classic` (default for projects that never chose) | SA → SD/RD → CR. SD and RD in one combined step; its behaviour is unchanged. Meeting Mode only applies here. |
| `full` | SA → SD → AR → SR → SEC → RD → CR → SEC → QA |

The roles: PM (the ChatGPT conversation that starts and accepts the work), SA system analyst, SD system designer (design only, never edits files), RD developer, CR code reviewer (stored as `REVIEW`), SR specification reviewer, AR adversarial reviewer, SEC security reviewer, QA quality assurance, SW specification writer. SA, SD, RD and SW **produce**; CR, SR, AR, SEC and QA are **gates** that judge. Each role has its own provider (`config role`) and a chat-room name (`config role-name`, default the abbreviation).

**A gate sends work back.** When a gate finds blocking problems, the nearest earlier producer redoes the work with the findings, and every later step runs again, so no reviewer judges a stale result. A gate may send work back at most `max_rounds` times (`config review-gate --max-rounds`, 1–5, default 2). After that the task stops with `waiting_on: PM_FLOW_DECISION` instead of looping; the PM calls `resolve_flow_gate` with `proceed` (accept and continue), `retry` (send it back once more) or `stop`. `--escalation ask-user` (default) makes ChatGPT put the decision to the real user; `auto-pm` lets ChatGPT decide and report why.

**AR cannot hold the workflow up with unsupported claims.** Every AR finding is written as `[blocker|major|minor|assumption] claim | evidence: ...`. A finding with no evidence is discarded. Strictness (`--ar normal|strict|extreme`) sets which severities can block: normal blocks on `major` and above, strict also on `minor`, extreme also on `assumption`. A `blocker` means the approach itself is unsound: it goes straight to the PM (`REJECT`) rather than back to the producer. The verdict is computed by ccc-mcp from the findings, not taken from the model. Other gates trust the reviewer's own `needs_revision`; CR and QA also fail whenever host-run tests failed or verified nothing.

Each gate verdict is stored (`flow_gate_results`) and a gate's failed rounds are counted from those rows, so the limit survives a crash or a resumed dispatch. Gate steps that end in `PASS` advance automatically; SA still needs the PM's plan, as before.

### Everyday roles and team templates

For work that is not software there are nine more roles, shown by what they do instead of an abbreviation: `PA` personal assistant, `RS` researcher, `AN` analyst, `PL` planner, `CS` consultant, `WR` writer, `ED` editor, `VC` fact checker and `RV` reviewer (the strict reviewer is the existing `AR`). PA, RS, AN, PL, CS, WR and ED **produce**: each hands over a document (saved as `PA-DOCUMENT.md`, `RS-DOCUMENT.md`, ...) that every later step reads. VC and RV are **gates** that send work back to the nearest earlier producer, like the software reviewers. None of them ever changes files, and their answers are written in plain language in the language of the request.

Four team templates are built in, listed under "Everyday teams" in Settings → Workflow:

| Template | Steps | For |
|---|---|---|
| `life` | PA → RS → AN → PL → VC | trips, shopping comparisons, everyday decisions |
| `business` | PA → RS → AN → CS → WR → RV | proposals, market research, business analysis |
| `content` | PA → RS → WR → ED → VC | articles, posts, reports, stories |
| `decision` | PA → RS → AN → CS → AR | big choices, option comparison, risk checks |

Pick one with `config workflow life`, or let `config workflow-selection auto` choose from the request (trip, budget, proposal, article, "should I..." and similar words; anything that mentions code stays on a software workflow). You can mix any roles in your own workflow, and the first step may be any producer, so `PA → WR → ED` is a valid "letter" workflow. The task page shows an everyday task's documents on its Results tab, newest first, and hides the diff and test cards, which only apply when a step changes code.

**Providers.** The everyday roles default to ChatGPT, not Codex or Claude Code: the CLI agents here can only read the project folder, so a researcher or fact checker running on them cannot look anything up. With ChatGPT each step is done by the ChatGPT conversation itself, which can browse, and the PM advances the workflow after each step. A role can still be pointed at Codex or Claude Code in Settings → Roles when its job needs no outside information (for example WR or ED working from the researcher's notes).

### The specification writer (SW)

SW turns what the discussion concluded (the requirement, SA's analysis, SD's design, and what the reviewers raised and how it was settled) into one specification written for an AI to implement from: purpose and non-goals, a glossary, decisions with their reasons and the alternatives rejected, numbered testable requirements (`REQ-001`, MUST/SHOULD/MAY), exact interfaces and data shapes, Given/When/Then acceptance criteria, and open questions listed rather than guessed. The document is saved as `SPEC.md` (the report itself as `SW-REPORT.md`) and handed to SD, RD and every reviewer that runs after it; the console shows it on the task's Results tab. SW only reads the repository; it never changes files. It is a producer, so a gate after it sends work back to it. No built-in workflow includes SW yet: add it in Settings → Workflow, for example `SA → AR → SR → SW → RD → CR`, so the spec is written after the discussion has settled.

### Your own workflows, and who picks one

Settings → Workflow (or `config workflow-save --steps SD,AR,RD,REVIEW [--name N]`) lets you build a workflow from any ordered list of roles, up to 12 steps. The first step must make something (SA, SD, RD or SD/RD) so a reviewer always has something to judge and a producer to send work back to. A workflow you save is named `工作流 2`, then `工作流 3` (`Workflow 2`, `Workflow 3` when the project language is English), and you can rename it; the two built-in workflows cannot be changed, only copied. A project holds at most 20 of its own. Deleting one never disturbs a task already running it, and if it was the selected workflow new tasks go back to `classic`.

A workflow with no SA, for example `SD → AR → RD → CR`, uses your requirement and acceptance criteria as the approved plan, so the PM goes straight to SD.

`config workflow-selection fixed|pm|auto` decides who picks a new task's workflow:

| Mode | Behaviour |
|---|---|
| `fixed` (default) | Always the selected workflow. |
| `pm` | ChatGPT calls `list_workflows`, chooses the best fit per task and passes its id as `workflow` to `start_project_request`; with no choice the selected workflow runs. |
| `auto` | ccc-mcp picks from the task text. Security work (login, password, token, permission, payment…) needs a workflow with SEC; a large or risky change (refactor, migrate, architecture, schema…) needs SD and AR; a small edit (typo, rename, docs…) gets the shortest workflow; anything else keeps the selected one. Among workflows that qualify the shortest wins. |

The chosen workflow, how it was chosen and why are stored with the task (`task status` → `brief.workflow`). A `workflow` argument is ignored unless the mode is `pm`.

## Task detail 2.0: conversation and user intervention

The local UI now opens each task on a work conversation. It rebuilds events from existing `task_events`, `agent_executions`, and saved per-run `TEST-RESULT.md` evidence, then includes persisted user interventions. Older tasks need no migration. Agent summaries come from saved reports; a test is described as verified only when the host actually ran it. Results and Technical record retain the original reports, diff, evaluation, Context, and handoff.

Pause persists a dispatch gate. A queued worker waits for resume; an already running agent or host test may settle at a safe point, shown as **Pausing** until then. Resume releases the gate and reuses the same queued execution. When the next step belongs to PM or an interactive ChatGPT role, the UI shows the saved handoff; it cannot launch ChatGPT itself.

Stop records a reason and time, requests cancellation of active executions, and blocks further dispatch. It shows **Stopping** while an operation settles and **Stopped** afterward. It does not roll back edited files. The composer stores a correction on the task and pauses dispatch by default. Pending corrections appear in `task_handoff` and the next Agent handoff, then are marked delivered so they are not sent twice. The composer is a workflow control, not a separate LLM chat.
