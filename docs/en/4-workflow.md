# 4. Run and resume the workflow

The default path is:

```text
ChatGPT (PM) → Codex (SA) → Claude Code (SD/RD) → host tests → Codex (REVIEW)
```

The PM creates a requirement and turns the SA report into a versioned context patch and plan. Thereafter, a successful low-risk SD/RD report is automatically written into Context, followed by host-run tests and REVIEW; a successful low-risk REVIEW report completes the task. Failures, unverified tests, revision requests, and decisions remain pending for PM review. Only observed host test evidence can complete a task.

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

Off by default. `ccc-mcp --project hello-demo config meeting-mode on --min-rounds 1 --max-rounds 3` inserts a bounded round between the approved plan and SD/RD's first implementation attempt: SD/RD may raise exactly one blocking question (read-only — it cannot write files while this is open), SA answers `confirmed`, `recommendation`, or `pm_decision_required`, and only `pm_decision_required` ever needs a human/PM action (`resolve_clarification`). SD/RD can also report `no_blocking_question` and go straight to implementation; `min_rounds` is shown in `task status` but never blocks that. The round count is capped at 5, persists across any retry or resume, and the gate only ever fires once per task — a later REVIEW-requested revision never re-triggers it. See [reliability and security](7-reliability-and-security.md#meeting-mode) for the full guarantee.

See [reliability and security](7-reliability-and-security.md) for test gates, quota fallback, and recovery guarantees.
