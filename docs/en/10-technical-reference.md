# 10. Technical reference

## Project records

ccc-mcp stores readable project summaries under `.ccc-mcp/` and preserves raw task reports and evidence under `.ccc-mcp/tasks/`. The authoritative store retains context versions, task state, executions, approvals, and the evidence references used to create a context patch.

## Workspaces

Implementation roles normally use a task worktree. Project workspace mode is supported when direct work in the project is required, but it is exclusive: concurrent implementation work cannot safely write into the same project workspace.

## Context

The handoff package contains the current context, relevant task information, evidence, reports, role instructions, and global Skills. A patch cites source execution IDs and verified repository references. Optimistic context versions prevent two PM sessions from committing contradictory histories.

## Observability

`task usage` reports the provider, model settings, native CLI invocation status, and available usage data. It is intentionally distinct from a subscription invoice or a remaining-quota guarantee. Use `verify` to detect evidence drift or failed hash checks.

## Evaluation engine

`config evaluation-engine on` turns on an opt-in scorer that reads a completed execution's already-saved evidence — the host-run test result, the execution's diff, and `ruff` static analysis — and computes two numbers with no additional LLM call:

- `quality_score` = the weighted average of only the sub-scores (`test`, `requirement`, `code_quality`, `security`, `review`) that have real evidence, using the role's configured weights, re-normalized over just those available dimensions. A sub-score with no evidence is `null` and simply drops out of the average — it is never treated as zero or as a perfect score.
- `evidence_coverage` = the sum of the weights of the available dimensions, divided by the sum of all weights defined for that role. It answers "how much of this role's scoring budget did we actually have evidence for," independent of how that available evidence scored.

Example: SA's weight profile is `{requirement: 0.70, review: 0.30}`, and SA's `review` sub-score is always `null` by design — there is no `CONFIRMED` disagreement type that implicates SA yet, so a number there would be fabricated. With `requirement = 100`, `quality_score = 100` (the weighted average of the one available dimension) and `evidence_coverage = 0.70` (0.70 of 1.00 total weight), even though the overall picture is only 70% covered.

`task_status` (`PASSED`/`TASK_FAILED`/`NEEDS_VERIFICATION`/`INSUFFICIENT_EVIDENCE`) is decided separately by a small set of hard rules that the weighted score can never override — a failing acceptance test, an unexecuted test suite, or thin overall evidence coverage can never read as `PASSED`. `task evaluate`/`task evaluations`/`task reevaluate` and the web console's task page expose this; `reputation` reports a Beta-Bernoulli reliability estimate per agent/model/role/task-type across every project, with explicit uncertainty while sample sizes are small.

For the design rationale and state machine, continue with [Architecture specification](11-architecture-spec.md).
