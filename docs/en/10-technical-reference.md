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

### Checking the score against ground truth

`ccc-mcp validate-scoring CASES.jsonl [--labels REPORT.json] [--rows]` answers "does a higher score really mean the work was resolved?". Each line of `CASES.jsonl` is one run ccc-mcp observed (`instance_id`, the agent's `diff`, the host-run `test_result`, the agent's `report`, optional `acceptance` and ruff-style `static_findings`); the ground truth is each case's `resolved` field or, with `--labels`, a SWE-bench harness result (`resolved_ids` / `unresolved_ids`). It runs the same feature and scoring code as production and prints AUROC (0.5 = coin flip), accuracy and precision per pass line, and the *false green* list: cases whose `task_status` was `PASSED` but were not resolved. It is rule-based, calls no LLM, and needs no project. Fewer than 30 scored cases, or fewer than 5 of either class, is reported as a smoke test rather than a conclusion. Producing the cases (running ccc-mcp on SWE-bench instances) is a separate step; the harness only judges what it is given. To build `CASES.jsonl` from real runs, use `ccc-mcp --project NAME task export-cases [TASK_ID...] [--out cases.jsonl]`: it is read-only, writes one case per task with a finished implementation round, and takes `instance_id` from the SWE-bench id in the task's requirement text (else the task id). It never includes ground truth.

A known weak spot this exposes, and what was done about it: a round whose only passing tests are ones the agent wrote itself (the `astropy__astropy-13398` shape) used to score 100. For SD/RD roles that case now caps the `test` sub-score at 70, so `quality_score` drops. It can still reach `PASSED` when every other dimension is clean, which `validate-scoring` keeps reporting as a false green.

An implementation round with no diff at all is never `PASSED` (it becomes `NEEDS_VERIFICATION`) and its green tests earn no `test` score, because tests that were already passing before any edit say nothing about the work done.

**First measurement (toy benchmark, not real agents).** `tests/toybench.py` builds 30 cases from 6 one-function tasks, each with a hidden test the patch never sees: correct, correct-with-own-test, wrong-but-visible-tests-green, wrong-with-a-passing-own-test, and broken patches. Result: AUROC 0.67, and all 12 wrong-but-green patches were scored `PASSED` (a 50% false-green rate among `PASSED`). Broken patches were always `FAILED` and scored below every correct one. In other words, host-visible tests catch *broken* work but cannot tell *wrong-but-green* work from correct work; the self-authored-test cap did not change AUROC here. The patches are hand-written, so this tests the scoring rules, not which mistakes real agents make.

### Comparing token cost with a single agent

`ccc-mcp compare-tokens CCC.jsonl DIRECT.jsonl [--ccc-labels R.json] [--direct-labels R.json]` compares ccc-mcp with one agent doing the same tasks directly. Each side is a JSONL of `instance_id`, `tokens_total` and `resolved`; `task export-cases` writes the ccc-mcp side (including `tokens_total` = input + output tokens as the CLI reported them, plus `usage_complete`). The headline figure is *tokens per resolved task*: every token spent, failed attempts included, divided by the tasks resolved, shown beside each side's resolve rate, because cheaper only counts if it still solves the task. With fewer than 20 shared tasks the verdict says it is a smoke test.

**Limit:** only native CLI rounds (Codex, Claude Code) report token usage. ChatGPT web/MCP rounds never invoke a local CLI, so they have none; a task containing such rounds exports `usage_complete: false` and the verdict warns that ccc-mcp's cost is understated. A ChatGPT-only run therefore cannot be measured this way.

For the design rationale and state machine, continue with [Architecture specification](11-architecture-spec.md).
