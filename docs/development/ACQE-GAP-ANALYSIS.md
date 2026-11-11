# ACQE (Adaptive Collaboration & Quality Engine) — Phase 0 Gap Analysis

**Status:** Phase 0 only — research and design, no implementation. Per the governing spec's
own instruction, this phase stops here and does not proceed to Phase 1 without review.

**Method:** four parallel read-only research passes against the current `main` checkout
covered (1) workflow auto-selection / role prompts / AR findings / review-gate mechanics,
(2) provider resolution / evaluation engine / usage tracking, (3) context handoff /
evidence integrity / existing single-agent paths, and (4) Skills/MCP permission
boundaries / existing benchmark infrastructure. File:line citations below come from those
passes; the highest-stakes ones (protocol `findings` field, `_AR_BRIEF`, `_flow_failed_rounds`,
`evaluation_engine/models.py`'s `Evidence`/`disagreement()` vocabulary) were independently
re-verified by direct `grep`/`Read` before writing this document. The rest were not
individually re-verified — see §7 "Unresolved concerns" for what that means for Phase 1.

---

## 1. Executive summary

Most of what ACQE asks for already exists in some form. The spec's own instruction —
"不得重複實作已有功能" — is achievable for roughly two-thirds of the requirements by
**extending** existing mechanisms rather than building new ones:

| Area | Verdict |
|---|---|
| Task profiling (REQ-001) | **PARTIAL** — `choose_workflow()` already does rule-based, explainable classification; missing the explicit dimension breakdown and Unknown handling |
| Direct execution (REQ-002a) | **MISSING** — no 1-role flow exists; shortest is 3 roles |
| Handoff execution (REQ-002b) | **EXISTS** — provider is already re-resolved live per dispatch; cross-session handoff already works |
| Challenge execution (REQ-002c) | **EXISTS** — this is just the AR role, already read-only and evidence-bound |
| Review Lens (REQ-003) | **MISSING, but a direct precedent exists** — `_AR_BRIEF` (strictness-keyed prompt injection) is the template to copy |
| Structured Findings (REQ-004) | **MISSING, high compatibility risk** — `findings: list[str]` is part of the public protocol schema; a typed replacement would break it |
| Failure-driven iteration (REQ-005) | **MOSTLY EXISTS** — round budget, feedback injection, and "host evidence overrides reviewer text" all already exist |
| Adaptive model strategy (REQ-006) | **PARTIAL** — effort tiers already exist per role/provider and are reusable; "escalate on quality" has no existing path (good — nothing to accidentally break) |
| Budget awareness (REQ-007) | **PARTIAL** — rounds/time/tokens are already recorded per-execution; no unified view, no human-intervention aggregate |
| Stop ineffective collaboration (REQ-008) | **MISSING** — round-count ceiling exists; same-finding-repeat detection does not |
| Fair A/B/C/D experiment (REQ-009) | **PARTIAL** — a real 2-arm harness exists (`benchmarks/toy-agents/`) and should be extended, not replaced; both existing arms are hardcoded to one model |
| Acceptance metrics (REQ-010) | **PARTIAL** — 4 of 9 metrics already exist, 1 is partial, 4 are genuinely new |
| Evidence transparency (REQ-011) | **EXISTS** — `Evidence.status` (`VERIFIED`/`CLAIMED`/`UNVERIFIED`/`FAILED`) is already exactly this distinction |
| Collaboration benefit UI (REQ-012) | **PARTIAL** — the underlying data mostly exists; needs incremental UI, not new data model (except the REQ-010 gaps above) |

The single biggest compatibility risk is REQ-004: the spec asks for structured findings
"在不破壞既有 AR Finding 與 Gate 判定規則的前提下" (without breaking the existing AR
finding/gate rules), but `findings: list[str]` is part of `PROTOCOL_VERSION`'s public
schema. The only non-breaking path is extending the *string convention* and/or recording
richer data out-of-band, not changing the field's type.

---

## 2. Existing capability inventory (by requirement)

### REQ-001 — Task Profiling

`choose_workflow(requirement, catalog, default_id)` — `src/ccc_mcp/core/flows.py:244-276` —
is pure regex/keyword matching, **no LLM call**:

1. If the text doesn't match `_CODE` keywords (bug/api/function/repo/refactor/database/
   test/deploy, L219-222) but matches an `_EVERYDAY` pattern (life/business/content/
   decision keyword sets, L223-241), it picks that everyday flow.
2. Otherwise it builds a `needs` role set: `_SECURITY` keywords (L203-207) → needs `SEC`;
   `_BIG` keywords like refactor/migrate/architect (L208-212) → needs `{SD, AR}`.
3. If `needs` is non-empty, it picks the **shortest** catalog workflow whose role set
   covers `needs` (L266-270), falling back to the best partial match.
4. Else, if `_SMALL` keywords (typo/rename/docs, L213-216) and the text is under 160
   characters, it picks the shortest workflow overall.
5. Else it returns `default_id` (the project's configured workflow) with reason "no
   special requirement detected."

It already returns a **reason string** alongside the pick — this is the right shape for
an explainable decision, just not yet decomposed into REQ-001's eight dimensions.

Called only when `workflow_selection == "auto"`, via `FlowHub._resolve_task_workflow`
(`flow_engine.py:72-89`); `"fixed"` always uses the project's selected workflow, `"pm"`
requires the caller (ChatGPT) to name one explicitly.

**Gap:** no Complexity/Risk/Verifiability/Budget/Required-Review dimensions are computed
or persisted; on no match it silently falls back to `default_id` rather than ever
surfacing "Unknown" — this is a direct conflict with REQ-001's "無法判斷的任務應標示
Unknown，不能強行認定為 Low Risk" requirement, discussed further in §6.

### REQ-002 — Adaptive Execution

**Direct — missing.** The shortest `BUILTIN_FLOWS` entry is `classic` (SA → SD/RD →
REVIEW, 3 roles, `flows.py:41-47`); there is no 1-role flow. `dispatch_agent(role=...)`
does already accept any single role directly, independent of flow-engine sequencing, so
a "Direct" pattern is achievable today as a documented usage convention — but there is no
formal single-role workflow entry, and the spec's framing ("優先單 Agent...不強制 SA、
SD、RD、CR 全部參與") implies a named, selectable mode, not just an undocumented escape
hatch.

**Handoff — already exists, structurally.** `dispatch_agent()` (`core/tasks.py:1686`)
calls `resolve_agent_settings(project_id, role, agent, model, effort)` and re-reads
`settings["provider"]` **live, on every single dispatch** (`tasks.py:1706-1707`) — so if a
user reconfigures a role's provider between two steps of the same task, the next
dispatch of that role automatically uses the new provider. Combined with
`task_handoff()` (`tasks.py:2154`) and `chatgpt_handoff_prompt()` (`tasks.py:356`), which
already build a cross-session, cross-provider continuation package from durable task
state, REQ-002's "Handoff" strategy is close to a pure documentation/UX exercise, not new
plumbing.

**Challenge — already exists, as the AR role.** `roles.py:73`: `"AR": ("AR",
"Adversarial Reviewer", "Challenges assumptions, hunts for flaws, demands evidence")` —
near-verbatim match. `READ_ONLY_ROLES` (`roles.py:32`) includes `AR`, confirming it
cannot modify code — structurally separate from the implementing role, satisfying "can
choose review-only, not modify" today. `full`'s step sequence (`flows.py:52-67`) already
runs AR as a blocking, evidence-bound gate before RD.

**Explicit spec constraint honored:** "如果現有 Workflow 已能達成，應直接重用；不得為
了三個名稱再建立一套平行 State Machine." Given the above, only "Direct" needs new
workflow-engine surface area; Handoff and Challenge need documentation and (optionally) a
thin, named convenience wrapper — not new state machines.

### REQ-003 — Context-Aware Review (Review Lens)

No Lens concept exists today. But the mechanism to add one without new state machinery
already exists and is in active use for a near-identical purpose:

`_AR_BRIEF` (`flows.py:376-389`) is a dict keyed by strictness (`normal`/`strict`/
`extreme`) holding a different reviewing-posture paragraph for each. `step_instruction()`
(`flows.py:476+`) assembles the actual dispatch text by conditionally appending pieces to
a list: `ROLE_BRIEFS[role]` always first; `_AR_BRIEF[strictness]` when `role == "AR"`;
a generic "block only with evidence" line plus the finding-format instruction and
remaining-round count when `role in GATE_ROLES`; and prior-round feedback text when this
is a retry.

A `_LENS_BRIEF` dict keyed by lens name, appended the same conditional way and selected
per task (initially via the same keyword heuristic `choose_workflow()` already uses),
would reuse this exact assembly pattern — no new role, no new state machine, a small,
contained diff.

Two scoping gaps are real, though, if Lens selection is meant to also gate *tool access*
(not just prompt framing):

- **Skills are global and role-agnostic** (`adapters/skills.py:6`, `available_skills()`
  at L167). `core/context.py:1334` and `:1381` both embed the full installed-skill set
  into every handoff package unconditionally — there is no per-role or per-lens
  filtering today. A Lens *could* be authored as a Skill with zero new storage, but
  restricting which roles see which Skill would require new filtering logic at those two
  call sites.
- **MCP tool access is project-wide, not role-scoped.** `install_mcp_server()`
  (`configuration.py:939`) sets `allowed_tools` per project (or globally); enforcement at
  call time (`configuration.py:1013-1028`, `call_installed_mcp_tool`) only checks project
  membership. A Lens wanting exclusive access to a specific tool (e.g. a scanner only for
  the Security lens) has no existing enforcement point.

**Recommendation:** ship the Phase 2 prototype as prompt-injection only (the
`_LENS_BRIEF` pattern), explicitly deferring Skill/MCP per-lens scoping — this keeps the
blast radius small and avoids touching the handoff-assembly hot path that every task
already depends on.

### REQ-004 — Structured Findings

`AgentReport.findings: list[str]` is defined **twice** in `core/models.py` (L60, L138) —
plain strings, not a typed Finding object, and part of the public protocol
(`PROTOCOL_VERSION = "1"`, `models.py:7`; mirrored in `docs/schema/agent-report.json`).
Structure today comes from a parsing convention, not a schema:

- `_FINDING = r"^\s*\[(blocker|major|minor|assumption)\]\s*(.+)$"` (`flows.py:305`)
- `_EVIDENCE = r"evidence\s*[:：]\s*(\S.{6,})"` (`flows.py:306`)
- `parse_findings()` splits each string into `(severity, claim, evidence-or-None)`.
- `judge_adversarial()` (`flows.py:325-348`): a finding only counts as "supported" if it
  has **both** a severity tag and an evidence match; only supported findings at/above the
  strictness threshold can block; `[blocker]` forces `REJECT` (escalate), lesser blockers
  bounce the work back. Unsupported findings are silently discarded.
- Non-AR gates (`judge_gate()`, `flows.py:351-367`) just trust the role's own
  `needs_revision` boolean — **no evidence requirement at all today for CR/SR/SEC/QA.**

Separately, `evaluation_engine/models.py` already has vocabulary that overlaps
REQ-004's proposal, though it is **not** structurally the same thing (see §7 for why this
matters):

- `Evidence.status ∈ {VERIFIED, CLAIMED, UNVERIFIED, FAILED}` (L10-16) — host-observed vs.
  agent-self-report vs. never-checked vs. a verification attempt that itself errored.
- `disagreement()` (L54-63, a plain-dict factory, **not a Pydantic model** — the file's
  own docstring says "Plain dicts + string constants, not pydantic models") with
  `resolution_status ∈ {CONFIRMED, DISMISSED, NEEDS_VERIFICATION}` (L27-29) plus `kind`,
  `description`, `roles`, `evidence_refs`.

**Compatibility risk (high):** replacing `findings: list[str]` with a typed Finding
object would be a breaking `PROTOCOL_VERSION` change — every existing `AgentReport`
producer (Codex/Claude/ChatGPT prompts, any external integration) would need to change
what it emits. The only non-breaking paths are: (a) extend the *string grammar* (a
richer bracket/pipe convention, still parsed by an extended regex into the existing
triple, with an optional trailing `lens:`/`status:` tag), and/or (b) record the richer
structured data **out-of-band**, keyed to the same execution/finding text, reusing
`disagreement()`'s shape (extended with a `lens` field) rather than inventing a parallel
schema.

### REQ-005 — Failure-Driven Iteration

Most of this already exists:

- `_flow_failed_rounds(project_id, task_id, index)` (`flow_engine.py:331-338`) counts
  existing `flow_gate_results` rows for that step within the current task — this **is**
  the per-gate retry/round budget tracker the spec would otherwise ask to be built.
- `_flow_gate_failed()` (`flow_engine.py:340-360`): a `REJECT` verdict escalates
  immediately (fundamental flaw); `failed >= limit` (the project's `review_gate.max_rounds`,
  hard-capped at 5, `flows.py:28`) escalates; no earlier producer step to bounce to
  escalates; otherwise it bounces with `feedback_text(gate)` injected into the next
  dispatch's `step_instruction(..., feedback=...)` — each retry already states why it is
  happening.
- `VERIFYING_GATES = {"REVIEW", "QA"}` get an extra rule (`flow_engine.py:305-318`): a
  gate's own PASS verdict is overridden back to `NEEDS_REVISION` if host tests actually
  failed or were never verified — host evidence already outranks the reviewer's own text,
  which is exactly REQ-005's "不得因節省成本而忽略 Host 測試失敗."
- Every gate verdict is persisted via `INSERT` (`flow_engine.py:320-328`, never
  recomputed from memory) — decisions are already rebuilt from durable rows only, so a
  crash/retry reaches the same decision (a guarantee already documented elsewhere in this
  codebase).

**Gap:** there is no comparison of finding *content* between rounds — the stop condition
is purely the round-count ceiling. REQ-008's "相同 Finding 重複出現，沒有新證據" rule does
not exist. The data needed already exists (`flow_gate_results.detail`, JSON, one row per
round) — this is a new read-and-compare function layered on top of
`_flow_failed_rounds`/`_flow_gate_failed`, not new storage.

### REQ-006 — Adaptive Model Strategy

- **`quota_fallback` is strictly error-triggered, never quality-triggered**, confirmed at
  `worker.py:209-249`: only on a dispatch *exception* does it call `is_quota_error()`
  (`infra/telemetry.py:119-134`, a marker-string match against the real CLI's own error
  output, e.g. `insufficient_quota`, `usage_limit_reached`, `rate_limit_error`). It
  refuses to chain a fallback-of-a-fallback (`worker.py:234-239`) and refuses a fallback
  equal to the primary (`worker.py:228-233`). There is no existing path where "the result
  looked bad" triggers a fallback — good news: REQ-006's constraint ("一般任務品質不佳，
  不能被偽裝成 quota exhaustion") is already structurally satisfied, and any new
  "escalate on quality" mechanism must use a **distinct** field/path, never touch
  `quota_fallback`.
- **Effort tiers already exist and are reusable as-is**: `EFFORTS = {"codex": {...},
  "claude": {...}, "chatgpt": {}}` (`configuration.py:48-52`), settable per role today via
  `configure_role`. An "adaptive model strategy" that wants cheap-for-simple,
  strong-for-risky is very plausibly implementable as *a policy that picks an existing
  `effort` value per task*, not a new model-tier abstraction.
- `default_provider(role)` (`roles.py:186-191`): general/everyday roles default to
  `"chatgpt"`; engineering roles default to `"codex"` (read-only roles) or `"claude"`
  (builder roles) — **never** `"chatgpt"` by default. (Independently confirmed earlier in
  this project's work this same session via a live API check against a freshly created
  project.) Any "Direct" strategy design that assumes a free/already-connected ChatGPT
  session is always the cheap default option for engineering roles would be wrong.
- **ChatGPT-only token usage is structurally unmeasurable, and the codebase already knows
  it.** `core/tasks.py:640-650`: dashboard usage aggregation explicitly skips
  `agent == "chatgpt" and adapter == "interactive"` executions *before* any token-field
  aggregation (`chatgpt_executions += 1; continue`) — there is no local CLI stdout to
  parse for a ChatGPT Web turn. Corroborated at `tasks.py:3069-3097`
  (`"source": "chatgpt_web"`) and in this session's own earlier `doctor` output review
  ("ChatGPT 執行一律互動式...不會偽裝成已呼叫 CLI"). **Any ACQE budget/cost feature must
  mark ChatGPT-only usage `Unknown`, never estimate it** — this is an existing, tested
  invariant that must not regress.

### REQ-007 — Budget-Aware Workflow

Individually, the pieces already exist: `max_rounds` (1-5, `flows.py:28`), per-execution
`elapsed_seconds` (`tasks.py:1957`, `:2455`), and per-execution token usage for
`real`/non-ChatGPT-interactive executions (`tasks.py:630-632`). What's missing is a
**unified view** combining them, and a human-intervention count: pause/resume/correction
events already exist as individual timeline event types (confirmed in this session's
earlier UI work on the Task Detail page), but nothing aggregates them into a single
"how many times did a person have to step in" number.

**Gap is aggregation, not data collection** — a new read-only budget-status function over
existing fields, plus one new aggregate query for human interventions. No new storage.

### REQ-008 — Stop Ineffective Collaboration

- Round-count ceiling: **exists** (§REQ-005 above).
- Same-finding-repeated-without-new-evidence: **missing**, confirmed gap, data already
  available (`flow_gate_results.detail`).
- "Review lacks necessary data/tools" and "new review can't further narrow unknown risk":
  **not found by the research passes; presumed missing.** Neither matches anything in the
  gate/escalation code reviewed. Flagged as **unconfirmed** rather than a verified gap —
  worth one more targeted search before Phase 2 scoping, since a related concept might
  exist under different naming (e.g. tied to `context_sync_status` or approval gating)
  that the research passes did not specifically look for.
- "Budget insufficient for next verification": depends on REQ-007's not-yet-built unified
  budget view — new logic on top of new (aggregated) data.

### REQ-009 — Fair Comparison Experiment

`benchmarks/toy-agents/run.py` (313 lines, read in full) is a real, working two-arm
harness — not a stub:

- **CLI:** `run --out DIR [tasks...]`, `prepare FOLDER [--single]`, `judge FOLDER`. No
  model/provider flag today.
- **Arms:** exactly two, **both hardcoded to Codex.** `run_direct` (L126) calls
  `native_executable("codex")` directly; `run_ccc` (L157) calls
  `service.configure_role("demo", role, "codex")` for a **hardcoded** 3-role loop (SA,
  SD/RD, REVIEW) — not driven by `workflow_selection=auto` or any `BUILTIN_FLOWS` entry.
  Neither function accepts a model/provider parameter today.
- **Tasks:** 6 small Python bugs already defined in `tests/toybench.py::TASKS` — `clamp`,
  `slugify`, `average`, `first_word`, `parse_bool`, `dedupe` — each with buggy code,
  agent-visible tests, and a separate hidden ground-truth test. This already closely
  matches REQ-009's "Case C" category. **Not confirmed:** whether any of these 6 are
  bug-free control cases for measuring false-positive rate, which REQ-009 explicitly
  wants ("額外設計沒有預埋缺陷的正常案例"). Needs a direct check before Phase 1.
- **Metrics captured** (`case_from()`, L103): `resolved` (hidden-test pass/fail), `diff`,
  `test_result.stdout`, `tokens`/`tokens_total`/`usage_complete`, `seconds`, and for the
  ccc arm, `rounds` and `task_state`. `report.findings` is **always `[]`** — none of
  REQ-010's finding-quality metrics are captured here today.
- **Extending to Arm C** ("low-model + review-only, no full ACQE"): a real, contained
  code change — `run_ccc`'s role loop is hardcoded, not generalized over arbitrary
  workflows.
- **Extending to Arm D** (different/higher-capability model): moderate —
  `configure_role` already accepts a `model` parameter per the existing role-settings
  shape, so threading a `--model` flag through both arms is plausible without
  restructuring; `run_direct`'s raw `codex exec` call needs a model flag added too.
- **Cost accounting:** `quota()` (L197) reads Codex account rate-limits once before and
  once after the *whole batch*, not per case — no per-arm cost attribution exists beyond
  the parsed token counts already in `case_from()`.

`benchmarks/swebench-verified/README.md`: 3 hand-selected Astropy instances,
ChatGPT-only, solved "after multiple repair and evaluation iterations" (not single-shot),
and the README itself states it is not a one-shot score, not reproducible by a third
party, and not an official leaderboard submission. **This cannot satisfy REQ-009's
fairness bar** (fixed test set, same model across arms, reproducibility) — it is a
capability demo, not comparison infrastructure.

`evaluation_engine/benchmark.py` (lines 1-45, confirmed by a separate research pass) already
implements a two-arm comparison (tokens-per-resolved-task, resolve rate) consuming
JSONL produced by `ccc-mcp task export-cases` — the same JSONL shape `toy-agents/run.py`
produces. **REQ-009's 4-arm design should extend this existing file format and report
generator, not invent a new one.**

### REQ-010 — Acceptance Metrics

| Metric | Status | Where |
|---|---|---|
| Task Success Rate | **EXISTS** | `reputation_tracker.py` (Beta-Bernoulli per agent/model/role/task_type, only counts `PASSED`/`FAILED`, explicitly excludes `NEEDS_VERIFICATION`/`INSUFFICIENT_EVIDENCE` so an unverified round is never counted as a success or failure) |
| Model Usage | **EXISTS** | `tasks.py:630-632` (per-provider token fields), `benchmark.py` (`tokens_total`) |
| End-to-End Duration | **EXISTS** | `elapsed_seconds`, `tasks.py:1957`, `:2455` |
| Evidence Completeness | **EXISTS** | `evidence_coverage` in evaluation reports |
| Valid Findings | **MISSING** | would extend `disagreement()`/a new Finding record, see REQ-004 |
| False Positive Rate | **MISSING** | needs `DISMISSED`-after-verification tracking over time; nothing tracks this longitudinally today |
| Fix Success Rate | **MISSING** | no finding→fix→re-verified linkage exists, though the raw pieces (evidence, re-evaluation versions) do |
| Regression Rate | **MISSING** | nothing compares pre/post-fix test sets for newly-broken tests |
| Human Interventions | **PARTIAL** | individual events exist in the task timeline; no aggregated count metric is computed anywhere today |

`reputation_tracker.py` already delivers a model-comparable success-rate signal keyed by
`(agent, model, role, task_type)` — reuse for REQ-009/010, don't rebuild.

### REQ-011 — Evidence Transparency

Already exists almost exactly as specified: `Evidence.status ∈ {VERIFIED, CLAIMED,
UNVERIFIED, FAILED}` (`evaluation_engine/models.py:10-16`) is precisely the
"AI-self-claim vs. host-verified vs. never-checked vs. verification-itself-failed"
distinction REQ-011 asks for, and it is host-computed (the module's own docstring:
"nothing here is parsed from untrusted Agent-structured output... it is all
host-computed"). No new data model needed for this requirement.

### REQ-012 — Collaboration Benefit Display

UI-facing; not deeply researched in Phase 0 (out of scope for the four research passes,
which targeted backend mechanisms). The underlying data this requirement wants to show
(role/provider actually used, which review triggered a fix, before/after test results,
duration, observable usage, verification limits) is covered by the items above marked
EXISTS/PARTIAL. This session's earlier, unrelated UI work already added a "run by the
host, never the agent's own word" caption to the Task Detail page's test-evidence card —
a direct precedent for how REQ-012's "驗證限制" disclosure could be surfaced without new
data plumbing. The metrics marked MISSING in REQ-010 (valid findings, false positives,
fix success, regression) cannot be displayed until they exist.

---

## 3. Duplication risks

Building any of the following from scratch instead of extending what exists would be
redundant and would fragment the product's evidence model:

1. **A new typed "Finding" record that isn't `disagreement()` extended.** Two overlapping
   vocabularies for "is this claim actually confirmed" would confuse both the UI and
   future evaluation logic. (See §7 for why `disagreement()` is not a perfect fit either —
   extend it carefully, don't just alias it.)
2. **A new per-gate retry/round counter.** `_flow_failed_rounds` already is one, backed by
   durable rows.
3. **A new model-capability-tier concept.** `EFFORTS` already is one, per provider, per
   role, already configurable.
4. **A new per-agent/model success-rate tracker.** `reputation_tracker.py` already is one.
5. **A new benchmark report/comparison format.** `evaluation_engine/benchmark.py` and the
   JSONL shape from `task export-cases` already are one; `toy-agents/run.py` already
   consumes it.
6. **A second, parallel task state machine for "Direct/Handoff/Challenge."** The spec
   itself forbids this; §2's REQ-002 analysis shows it is both unneeded and avoidable.

---

## 4. Minimal architecture design (for Phase 1/2 scoping — not implemented here)

Ordered by what each piece depends on:

1. **`profile_task()`** (new, `core/flows.py`) — a pure function reusing
   `choose_workflow()`'s existing keyword signal extraction, additionally emitting the
   REQ-001 dimensions (`domain`, `complexity`, `risk`, `verifiability`,
   `required_review`) plus an explicit `unknown: set[str]` for anything it can't
   classify. Zero new LLM calls (matches the spec's own stated preference). Does **not**
   change `choose_workflow()`'s existing return value or behavior for any input that
   currently resolves — purely additive, called alongside it.
2. **One new minimal "direct" flow entry** (or a formalized, named single-role dispatch
   convenience) for REQ-002's Direct strategy. Smallest safe option: a single-step
   `BUILTIN_FLOWS` entry reusing all existing dispatch/evidence/gate machinery unchanged.
3. **`_LENS_BRIEF`** (new dict, `core/flows.py`, mirrors `_AR_BRIEF`) plus a lens-selection
   step reusing `profile_task()`'s signals, user-overridable. Prompt-injection only for
   the prototype — no Skills/MCP scoping yet (see §2 REQ-003).
4. **Extended finding grammar** (`core/flows.py`'s `_FINDING`/`_EVIDENCE` regexes) to
   optionally carry a lens tag and a verification-status token, still parsed into the
   existing `(severity, claim, evidence)` triple — the protocol field stays
   `list[str]`, unchanged.
5. **Extend `disagreement()`** (`evaluation_engine/models.py`) with an optional `lens`
   field and reconcile its 3-state `resolution_status` against ACQE's proposed 4-state
   `verification_status` (Confirmed/Supported/Hypothesis/Unknown) — these are not
   identical today; §7 flags this as needing a product decision, not a Phase-0-level
   engineering call.
6. **Same-finding-repeat check** (new function, `core/flow_engine.py`, layered on
   `_flow_gate_failed`) comparing the current round's parsed blocking claims against the
   previous round's (`flow_gate_results.detail`) — if unchanged and no new
   `evidence_refs`, escalate instead of bouncing again. No new storage.
7. **Adaptive-model policy function** (new, likely `core/configuration.py` or a new small
   module) mapping a task profile to a suggested `effort` per role, applied only within
   user-configured allowed providers/budget. Never touches `quota_fallback`. Always
   treats ChatGPT-only usage as `Unknown`.
8. **Unified budget-status view** (new read-only function over existing `max_rounds`,
   `elapsed_seconds`, token fields) plus one new human-intervention aggregate query.
9. **Benchmark harness extension** (`benchmarks/toy-agents/run.py` +
   `tests/toybench.py`): generalize `run_ccc`'s hardcoded role loop to run an arbitrary
   workflow; add a `--model`/`--provider` flag threaded through both existing arms; add
   Arm C (review-only) and Arm D (different/higher-capability model); expand the task set
   toward the spec's 12+ cases — **the FastAPI-SSE and DB/API cases (Case A/B) are not
   represented at all today and need genuinely new, hand-built fixtures**, not just new
   JSON entries. Reuse `evaluation_engine/benchmark.py`'s existing report format.
10. **UI increments** (REQ-012) — additive to the Task Detail page, blocked on items 5–9
    for the metrics that don't exist yet; the "existing" metrics (role/provider used,
    duration, evidence) can be surfaced immediately with no backend change.

---

## 5. Compatibility risks

- **`findings: list[str]` is part of `PROTOCOL_VERSION`'s public schema** — any change to
  its *type* is a breaking protocol change affecting every existing report producer.
  Only additive, string-convention changes are safe.
- **Skills are currently global/unscoped by design** (confirmed via the module's own
  docstring intent: every role sees every skill and decides relevance itself).
  Introducing Lens-based filtering changes what an agent sees in its handoff package —
  must default to "no filtering" (today's behavior) to avoid silently hiding guidance
  from existing users mid-task if this ships half-finished.
- **Any new `BUILTIN_FLOWS` entry must not collide with custom workflow IDs** (cap:
  `MAX_CUSTOM_WORKFLOWS = 20`) and must not change `choose_workflow()`'s output for any
  input that currently resolves successfully — needs characterization tests capturing
  today's outputs *before* touching the function, since REQ-001 explicitly forbids
  changing existing auto-selection rules "無故."
- **`quota_fallback` semantics are explicitly protected by the spec** — any new
  escalate-on-quality-issue mechanism must use a distinctly named new field/path, never
  reuse or alias it.
- **`default_provider()` defaults engineering roles to codex/claude, not chatgpt** —
  already confirmed this session. A "Direct" strategy assuming ChatGPT is always the
  free/available default for engineering roles would be wrong; it must read the
  project's actual configured profile.
- **ChatGPT-only usage must stay `Unknown`, never estimated** — an already-tested
  invariant (`tasks.py:640-650`) that any new budget/cost feature must not regress.

---

## 6. A product decision Phase 0 cannot make alone

`choose_workflow()`'s silent fallback to `default_id` on no match is exactly the
behavior REQ-001 says should instead surface "Unknown." Changing it is simultaneously:

- **Required** for literal REQ-001 compliance ("無法判斷的任務應標示 Unknown，不能強行
  認定為 Low Risk"), and
- **A behavior change to a function every existing "auto"-mode task already depends on**
  in production, which REQ-001 elsewhere forbids changing "無故."

Recommendation: do not touch `choose_workflow()` itself. Add `profile_task()` as a
**separate, additive** signal (per §4 item 1) that can surface "Unknown" on its own
dimensions without altering what `choose_workflow()` returns for workflow selection. This
satisfies both constraints simultaneously, but it is a design choice someone should
explicitly sign off on before Phase 2, not an automatic consequence of "the code already
works this way."

---

## 7. Three-perspective self-review

### 7.1 Senior architect

The recommended design (§4) is almost entirely additive, which is the right shape for a
product that already has significant working machinery: it reuses `_AR_BRIEF`'s pattern
for Lens, `_flow_failed_rounds` for round tracking, `disagreement()` instead of a new
Finding type, `EFFORTS` instead of a new model-tier concept, and
`evaluation_engine/benchmark.py`'s format instead of a new report generator. This
directly honors "不建立第二套工作流狀態機" and "不得重複實作已有功能."

The one place genuinely new complexity gets introduced is Skill/MCP per-lens scoping —
correctly deferred out of the Phase 2 prototype in §4 item 3, since it touches a
handoff-assembly path every task depends on. If that deferral slips and someone builds it
anyway to make Lens "feel complete," that is the single highest-risk piece of this whole
plan and deserves its own focused review when it's actually proposed.

### 7.2 Strict reviewer

Three things in this document should not be taken as settled fact without one more pass:

1. **Citations were produced by four parallel research agents, not personally verified
   line-by-line by me.** I independently re-checked the highest-stakes ones (the
   protocol `findings` field, `_AR_BRIEF`, `_flow_failed_rounds`/`_flow_gate_failed`, and
   `evaluation_engine/models.py`'s `VERIFIED`/`CLAIMED`/`disagreement()` vocabulary) and
   all matched exactly as reported. The rest — in particular the `worker.py`
   `quota_fallback` line numbers, `tasks.py`'s usage-aggregation line numbers, and the
   full `benchmarks/toy-agents/run.py` read — were not independently re-verified by me.
   Spot-check before Phase 2 implementation begins.
2. **`Evidence.status` and `disagreement().resolution_status` are not the same taxonomy
   as REQ-004's proposed `verification_status`.** `Evidence.status` answers "how was this
   one fact obtained" (host-observed vs. self-reported vs. untried vs. errored).
   `resolution_status` is specifically for *disagreements between agents* and has only 3
   states (`CONFIRMED`/`DISMISSED`/`NEEDS_VERIFICATION` — confirmed directly from source,
   §2 REQ-004), not the 4 states the spec wants (`Confirmed`/`Supported`/`Hypothesis`/
   `Unknown`). A single AR finding with no disagreement between agents involved at all
   doesn't obviously fit `disagreement()`'s intended trigger today. **Before committing
   to "extend `disagreement()`" as the Phase 2 approach, confirm whether it's only ever
   created when two agents actually disagree, or whether a solo finding can go through
   it too** — this document assumed the latter is feasible but did not verify it.
3. **The "zero new LLM call cost" framing (§8/below) is only true if Lens selection stays
   keyword-based**, the same mechanism `choose_workflow()` already uses. That is also
   likely to produce noticeably worse lens-fit than an LLM-based selector would. There is
   a real tension between REQ-001's stated preference ("避免額外呼叫 LLM") and getting a
   lens-selection quality good enough to make the rest of the experiment meaningful —
   this document does not resolve that tension, it only states it.

### 7.3 Cost & product analyst

- The spec's REQ-009/010 design (fixed test set, same model across arms, absolute cost
  reported alongside pass/fail, explicit "exploratory only" labeling when underpowered)
  is appropriately rigorous and guards against self-serving conclusions. But Phase 0
  stops short of a falsifiable success threshold. Recommend Phase 1 explicitly state one
  — e.g., "ACQE must reduce Regression Rate by at least X percentage points at equal or
  lower token cost, on the fixed case set, to be considered validated" — *before* any
  Phase 2 engineering effort, per the spec's own §10 "實驗決策" principle.
- **Expanding the benchmark from 6 to 12+ cases is not "write more JSON."** The two
  categories the spec explicitly wants (FastAPI SSE stream-interruption/Redis-lock-TTL
  races, and DB/API non-atomic-write/idempotency bugs) are meaningfully harder to author
  correctly — they need real, reproducible, hidden-test-validated fixtures, which is
  itself non-trivial engineering effort that this document has not scoped or estimated.
  Flag as a real line item for Phase 1/2 planning, not an afterthought of Phase 2.
- **Both existing benchmark arms are hardcoded to Codex.** A meaningful A/B/C/D
  comparison needs reliable access to at least two genuinely different capability-tier
  models under the same harness, and REQ-006 itself says ChatGPT-only cannot be assumed
  to have controllable model/usage data. **It is not clear from this repository alone
  what Arm D ("High-model Direct, 參考基準") would actually run against**, or whether
  budget is approved for its real API cost. This blocks Phase 1 from starting
  meaningfully, not just a detail to resolve during Phase 2 engineering — it needs a
  human decision (which models are actually available and funded for this experiment).

---

## 8. Test plan (for Phase 1/2, not run in Phase 0)

- **Characterization tests for `choose_workflow()`** — snapshot current outputs across a
  representative input set *before* adding `profile_task()` alongside it, to guarantee no
  accidental behavior change.
- **Unit tests for `profile_task()`**, including explicit coverage of the `unknown` set
  for inputs that don't match any existing heuristic.
- **Unit tests for `_LENS_BRIEF` injection**, mirroring however `_AR_BRIEF`'s own
  strictness injection is tested today (check `tests/test_flows.py` or equivalent before
  Phase 2 — not confirmed to exist by this Phase 0 pass).
- **Unit tests for the same-finding-repeat stop logic**, including the case where new
  evidence *does* appear between rounds and a retry should still proceed.
- **Protocol compatibility test** — existing `AgentReport`/`ContextPatch` fixtures from
  `docs/schema/` must still validate after any additive model changes.
- **Integration test via `FakeAgentAdapter`** driving a full task through a modified gate
  to confirm bounce/escalate decisions are unchanged for inputs that pass today — a
  regression safety net, not a new testing pattern (this project already uses
  `FakeAgentAdapter` extensively for exactly this purpose).
- **Benchmark harness dry-run** — extended `run.py` with `--model`/new-arm flags, run
  against `FakeAgentAdapter` rather than real Codex/Claude, to verify wiring without
  spending real quota.
- **Explicit regression test for the ChatGPT-Unknown invariant** — assert
  chatgpt/interactive executions never produce a non-null token estimate anywhere in the
  new budget/cost code paths.

---

## 9. Expected new model-call cost

- **Phase 0 (this document):** zero — pure research and analysis.
- **`profile_task()`:** zero additional LLM calls — rule-based, like `choose_workflow()`
  today, matching REQ-001's own stated preference.
- **Review Lens selection:** zero additional calls *if kept rule-based* (same keyword
  heuristic family as workflow selection). An LLM-based lens selector would add one call
  per task and is explicitly not recommended for the Phase 2 prototype (see §7.2 point 3
  for the open tension this creates).
- **Same-finding-repeat detection:** zero additional calls — pure text/data comparison.
- **Adaptive model strategy:** zero additional calls — it changes *which* existing
  provider/effort a dispatch uses, which shifts cost within the existing call, rather
  than adding a new call.
- **Benchmark harness extension (Arms C/D):** **real, intentional new cost** — each new
  arm run spends real quota against Codex/Claude (or, if available, ChatGPT). This is
  expected for REQ-009 and should remain an explicit, opt-in CLI invocation (as the
  harness already is today), never triggered automatically from production dispatch.

**Net assessment:** the Phase 1→2 prototype work as scoped in §4 should add **zero** new
LLM calls to the production dispatch path. All new cost is confined to the benchmark
harness, which is already a separate, manually-invoked tool.

---

## 10. Unresolved concerns carried into Phase 1

> **Update:** items 1 and 4 below were resolved by product decision on review of this
> document — see §11 for the decisions and for recommended (not yet final) dispositions
> of items 2, 3, 5, 6 and 7. The numbering here is kept as originally written for
> traceability; treat §11 as authoritative where the two disagree.

1. ~~Whether `choose_workflow()`'s silent-fallback behavior should change~~ — **RESOLVED,
   see §11 Decision 1.**
2. Whether `disagreement()`'s actual trigger conditions allow a solo (non-disagreement)
   finding to pass through it, which the recommended Phase 2 design for REQ-004 depends
   on (§7.2 point 2) — needs direct verification before committing to that design.
3. The tension between "no new LLM calls" (REQ-001's stated preference, honored in §9)
   and lens-selection quality good enough to produce a meaningful REQ-009 experimental
   result (§7.2 point 3) — needs a decision on whether the Phase 2 prototype is allowed a
   small, explicit LLM-based lens-selection budget as an experiment variable itself.
4. ~~Which models are actually available and funded for the REQ-009 Arm D~~ — **RESOLVED,
   see §11 Decision 2: Arm D is deferred and does not block Phase 1.**
5. Whether any of the 6 existing `toybench.py` tasks are bug-free control cases for
   false-positive measurement, and whether the FastAPI-SSE / DB-API fixture authoring
   effort (§7.3) has been scoped/budgeted as its own line item rather than folded into
   "write 12 cases."
6. A concrete, falsifiable Phase 1 success threshold (§7.3) has not been set anywhere —
   without one, REQ-010's metrics can be collected but "did ACQE actually help" has no
   agreed answer even after the experiment runs.
7. Several research citations in this document (see §7.2 point 1) were not personally
   re-verified by the document's author and should be spot-checked before Phase 2
   implementation begins.

---

## 11. Product decisions (post-review) and revised Phase 1 plan

This section records the product owner's decisions after reviewing §§1–10, and
**supersedes** the original Phase roadmap in the governing spec's own §9
("Phase 0 → Phase 5") and the arm design in REQ-009/§4/§9 wherever they conflict. Nothing
in this section authorizes touching `core/*` production code this round — see §11.5.

### 11.1 Decision 1 — Workflow Unknown & Fallback

- `choose_workflow()`'s existing fallback-to-`default_id` behavior is **kept as-is**. It
  is not removed, and its output for any input that resolves today must not change.
- ACQE must distinguish **"classification result"** from **"final workflow actually
  used."** A task's classification is a separate, additive record (per §4 item 1's
  `profile_task()`) alongside — never instead of — `choose_workflow()`'s own return value.
- When classification is unreliable, record all of: `classification=unknown`,
  `fallback_used=true`, the workflow actually selected (which is whatever
  `choose_workflow()`/`_resolve_task_workflow` already returned), and a human-readable
  fallback reason. This is explainability, not a behavior change.
- **Phase 1 scope for this decision: none** — Phase 1 (§11.3) is benchmark-harness-only
  and does not touch `profile_task()` or `choose_workflow()`. This decision governs
  Phase 2+ design, recorded here so it isn't re-litigated later.
- **Explicitly out of scope for "Unknown" handling:** a user (or ChatGPT PM) explicitly
  naming an invalid workflow ID. Confirmed from source — `_resolve_task_workflow`
  (`flow_engine.py:72-89`, specifically line 81, `flow_definition(requested, custom) #
  rejects an unknown id`) **already hard-rejects** an unrecognized explicit workflow ID
  today; it does not silently fall back. Recommendation: **keep this existing
  hard-rejection behavior unchanged.** An explicit, wrong ID is a caller bug (e.g. a typo)
  and is a categorically different situation from "the task's intent genuinely couldn't be
  classified" — collapsing the two into one fallback path would hide real errors. No
  further design work needed for this sub-case; it is already correct and already
  separate from the Unknown-classification feature by construction.

### 11.2 Decision 2 — Benchmark Arm D

- Arm D ("High-model Direct," reference baseline) **does not block Phase 1.**
- Phase 1 completes Arms A, B, and C0 first (see §11.3 for what these are now called),
  using the **same model**, same initial repository/environment, and reproducible
  conditions across all three.
- Reuse the existing Codex-based `benchmarks/toy-agents/run.py` harness; prefer minimal
  changes. **A cross-provider benchmark runner is explicitly not needed yet.**
- Arm D is deferred until model selection and capability pairing (i.e., which two models
  actually represent a meaningful "low" vs. "high" capability gap, and that both are
  available/funded) are confirmed.
- If no qualifying high-capability model is configured when Arm D would otherwise run,
  the harness must record a literal `NOT_CONFIGURED` status for that arm — **never
  fabricate or estimate a comparison result in its place.**

### 11.3 Recommended dispositions for the remaining five concerns

| # | Concern | Recommended decision | Blocks Phase 1? |
|---|---|---|---|
| 2 | `disagreement()` trigger conditions unconfirmed for solo findings | **Effectively resolved by the product's own Phase 2 scoping** (see the governing instruction's point 2: Review Lens findings must use an experiment-only record, and `disagreement()` must not be used as Finding storage "除非已確認其用途及觸發條件完全相容"). Recommendation: treat this as permanently deferred, not merely delayed — build Phase 2's Finding record as a new, small, additive structure from the start, and only consider migrating it onto `disagreement()` later if a future review confirms full compatibility. Do not spend Phase 1/2 time investigating `disagreement()`'s trigger conditions; it is off the critical path. | No — irrelevant to a benchmark-only Phase 1. |
| 3 | Tension: "no new LLM calls" vs. lens-selection quality | Recommendation: **the Phase 2 prototype does not auto-select lenses at all.** Each of the 3 experimental lenses (Architecture/Concurrency/Data Integrity) is applied deterministically by test-case category — the case fixtures are *designed* to need a specific lens, so the prototype can test lens *effectiveness* without first needing to solve lens *selection*. This sidesteps the tension entirely for Phase 2/3; real adaptive lens-selection (rule-based or LLM-based) is explicitly deferred to a later phase, and only worth revisiting if Phase 3 shows lenses add value at all. | No — Phase 2+ concern only. |
| 5 | Bug-free control cases + new fixture authoring cost unscoped | Recommendation: **split into two tracked items.** (a) Validate the harness mechanically using the existing 6 `toybench.py` cases — no new authoring needed, can start immediately (§11.4). (b) Author new cases (architecture/concurrency/data-integrity-flavored defects, plus genuinely normal/no-defect cases for false-positive measurement) as its own, separately estimated work item required before Phase 3 (the actual A/B/C0/C1 comparison) can run meaningfully — explicitly not a "few hours" task, per §7.3's original cost flag. | Partially — blocks Phase 3 (the comparison), **not** Phase 1 (building the harness itself). |
| 6 | No falsifiable Phase 1/3 success threshold | Recommendation: adopt the governing instruction's own closing question as the literal threshold: **Arm C1 (ccc-mcp + Review Lens) must show a measurable improvement over Arm B_review (single agent + one *actual review* pass) — not over Arm A, and not over Arm B_retry (test-guided retry, a different and lesser claim) — at comparable cost, on the fixed case set, before Phase 4 considers expanding ACQE.** "Measurable improvement" means at minimum: higher fix success rate or lower regression rate, without a proportionally larger token/time cost. Absent that, the documented conclusion must be "no demonstrated advantage over a single extra review pass," not silence. **Correction (round 2, see §11.6):** the original wording of this row named "Arm B" without distinguishing retry from review; B_retry's results must never be used to evaluate this threshold — only B_review can, once it exists. | Should be written down now (Phase 1), evaluated in Phase 3, binding for Phase 4 — does not block starting Phase 1 engineering. |
| 7 | Unverified citations | Recommendation: no dedicated verification pass — spot-check a citation opportunistically whenever Phase 1/2 work actually touches that file (e.g., confirm `worker.py`'s `quota_fallback` lines the first time anything near it is edited). Low stakes, procedural. | No. |

### 11.4 Revised phase plan (supersedes the governing spec's original Phase 0–5)

| Phase | Content |
|---|---|
| 0 | Gap analysis — **done** (this document). |
| 1 | Benchmark harness extension + baseline runs. Minimal changes to `benchmarks/toy-agents/run.py`; validate against the existing 6 `toybench.py` cases. No Review Lens work, no `core/*` changes. See §11.5 (round 1) and §11.6–11.8 (round 2 correction). |
| 2 | Minimal Review Lens prototype: 3 experimental lenses via `_AR_BRIEF`-style prompt injection, deterministic per-case-category selection (§11.3 item 3), experiment-only Finding records (§11.3 item 2). New case fixtures (§11.3 item 5b) authored before or during this phase, ahead of Phase 3. `B_review` (§11.6) is also built in this phase, not Phase 1 — it needs an actual critique step, which is review-engine work, not harness plumbing. |
| 3 | Run and compare Arms A / B_retry / B_review / C0 / C1 on the full fixed case set (old + new fixtures); report against the §11.3 item 6 threshold using **B_review**, not B_retry. |
| 4 | Go/no-go decision on expanding ACQE, based strictly on Phase 3's evidence — no expansion without a demonstrated C1 > B_review result. |

Arm naming, fixed for the rest of this initiative — **corrected in round 2 (§11.6)**:

- **A** — single agent, direct (existing `run_direct`).
- **B_retry** — single agent, plus one bounded *test-guided retry* on a failing visible
  test — no inspection step, nothing looks at the implementation beyond re-running the
  same test suite. Implemented this round as `run_b_retry()` (renamed from this
  document's original, imprecise "Arm B"/`run_review()` — see §11.6 for why the rename
  was necessary). **Must never be reported as evidence about review quality.**
- **B_review** — single agent, plus one *actual* review pass: an explicit critique step
  that runs regardless of whether the visible tests already passed, producing findings
  that are only counted once host-verified (never taken on the agent's own word). Defined
  in §11.6; **not implemented anywhere yet** — Phase 2 work.
- **C0** — existing ccc-mcp multi-role workflow, no Review Lens (existing `run_ccc`).
- **C1** — ccc-mcp workflow + Review Lens (Phase 2, not built yet).
- **D** — high-capability-model reference baseline (deferred per Decision 2; record
  `NOT_CONFIGURED` until model pairing is confirmed).

Fairness controls that must hold across A/B_retry/B_review/C0/C1 for any Phase 3
comparison to be valid: same model version, same starting repository/commit, same tool
permissions, same agent-visible tests, same hidden acceptance tests, same review-round
budget. Metrics collected per run, beyond pass/fail: wall-clock duration, observable
model/token usage, valid findings (B_review/C0/C1 only — A/B_retry have no structured
finding mechanism to count), false positives (B_review/C0/C1 only, same reason), and
human interventions (expected to be zero for all arms in an automated benchmark run —
tracked in case that assumption turns out false). See §11.7 for the as-built verification
of these controls against the current harness.

### 11.5 Phase 1 implementation, round 1 — what that round actually changed

> **Round 2 note:** everything below originally referred to this arm as "Arm B" /
> `run_review()` / the `review` CLI arm. That naming was corrected to `B_retry` /
> `run_b_retry()` / the `b_retry` arm in round 2 (§11.6) because it measures test-guided
> retry, not review — the text below has been updated in place to use the corrected
> names throughout, so it describes the harness as it exists now, not as first written.

**Scope:** `benchmarks/toy-agents/run.py` and `tests/toybench.py` only. No changes to
anything under `src/ccc_mcp/core/`, `src/ccc_mcp/evaluation_engine/`, or the web console.
No Review Lens code. No workflow-engine changes. This satisfies the governing
instruction's "先完成最小 Benchmark Harness 擴充，不得同時開發 Review Lens 或重構整個
Workflow" and "不要為了符合新規格，而改壞 ccc-mcp 原本已經具備且正常運作的功能."

**Files modified (round 1):**
- `benchmarks/toy-agents/run.py` — added `run_b_retry()` implementing Arm B_retry (one
  `codex exec` attempt, host runs the agent-visible test, and on failure exactly one
  bounded follow-up `codex exec` call given the failure's `stdout` as feedback, then the
  host re-runs the visible test — mirrors `run_direct`'s single-process style, adds one
  bounded retry, no multi-role machinery, **no inspection step** — see §11.6 for why that
  last point matters), plus a `_merge_tokens()` helper that sums token usage across
  B_retry's rounds (not just the last one, since REQ-009 wants total cost reported).
  `case_from()` itself was **not modified** — B_retry's extra fields (`rounds_used`,
  `passed_per_round`) are passed through its existing `extra` parameter, which already
  supported additive fields (this is exactly how `run_ccc` already adds its own `rounds`/
  `task_state` fields) — so no existing field was touched. The `run` command's arm
  selection was generalized from a hardcoded two-arm loop into an `ARMS` dict +
  `parse_arms()`/`--arms` flag, **defaulting to `direct,ccc`** — byte-identical to the
  pre-B_retry behavior unless a caller explicitly opts into `--arms direct,b_retry,ccc`
  (or any other combination). No new CLI sub-command was added: B_retry is just a third
  selectable arm of the existing `run` command, consistent with how `direct`/`ccc`
  already work.
- `tests/toybench.py` — **unchanged.** No new tasks added this round (per §11.3 item 5,
  new fixture authoring is its own later item, tracked as a Phase 1→3 blocker, not part of
  this harness-plumbing change).
- `tests/test_toy_agents_run.py` — **new.** See below.

**Tests (round 1, renamed in round 2 — see §11.7 for the full current count):**
- Pure unit tests of the token-summation helper.
- Control-flow tests exercising `run_b_retry()`'s real behavior (round counting,
  retry-on-failure, hard stop at the bound) against a real generated toy repo and real
  `pytest` runs; only the `codex exec` subprocess call itself is stubbed (writes a
  fixed/broken source variant instead of truly calling Codex), so no Codex quota is spent
  and the test needs no installed CLI (`native_executable` is also stubbed).
- A base-shape regression test for `case_from()`.
- CLI arm-selection parsing/validation tests.
- One `@pytest.mark.real_agents`-marked opt-in smoke test against real Codex (excluded
  from the default run; see §11.7 for whether it was actually run).

**Compatibility risks:**
- `case_from()` already feeds `evaluation_engine/benchmark.py`'s report generator via its
  JSONL output; any field rename or removal would silently break that consumer. New
  fields must be additive only.
- `run.py`'s existing `run`/`prepare`/`judge` CLI contract (documented in the original
  Phase 0 research as the only interface, no model/provider flag today) must keep working
  for existing callers; the new `b_retry` arm is additive.
- None of this touches `core/*`, so none of the compatibility risks listed in §5 apply to
  this round's actual change — flagged here only to confirm that explicitly.

---

### 11.6 Round-2 correction — `B_retry` vs. `B_review` are not the same arm

A review of round 1's delivery surfaced a real semantic bug in the benchmark's design,
not just its naming: **what was implemented and called "Arm B" is Test-Guided Retry, not
a review.**

**What `run_review()` (round 1) actually did, precisely:** one `codex exec` attempt; the
host runs the agent-visible `pytest` suite; if it fails, one bounded follow-up `codex
exec` call is given that failure's `stdout` as feedback and tries again; if it already
passes, the arm stops immediately. **Nothing ever inspects the implementation for a
problem the visible tests don't already check — on any round, pass or fail.** A real
defect outside what the visible tests cover (exactly the kind of defect the toy
benchmark's own hidden tests are designed to catch — see `tests/toybench.py`'s
`partial`/`overfit` patch kinds) is invisible to this arm on every round. This is
confirmed both by reading the implementation and by a new regression test,
`test_run_b_retry_does_not_retry_once_visible_tests_pass_even_with_a_real_defect` (§11.7),
which feeds the arm clamp's `"partial"` patch — passes both visible tests, still fails a
hidden one — and confirms the arm stops at round 1 regardless, reporting `resolved:
false` with no retry ever attempted.

**Disposition, per the governing decision:**

1. The implementation is **kept, unmodified in behavior**, and **renamed**:
   `run_review()` → `run_b_retry()`, CLI arm `review` → `b_retry`, output file
   `review.jsonl` → `b_retry.jsonl`. No existing test was deleted — all were renamed and
   kept, plus new tests were added (§11.7).
2. `B_retry`'s experimental meaning is now explicit everywhere it appears (module
   docstring, function docstring, CLI help text, this document): **Test-Guided Retry** —
   it answers "does one bounded retry gated on the existing visible test suite help, over
   a single unreviewed attempt." It does **not** answer anything about review
   effectiveness, and must never be cited as if it did (§11.3 item 6 correction, §11.4).
3. **`B_review` is hereby defined** as a distinct arm, not implemented this round:
   - A single agent completes an implementation attempt, exactly as in `B_retry`'s first
     round.
   - **Regardless of whether the agent-visible tests pass**, the agent is then given an
     explicit, separate review turn — a distinct prompt asking it to critique its own (or
     a frozen) implementation for problems, in the same spirit as this product's existing
     AR/CR role prompts (reuse `_AR_BRIEF`'s pattern per §4 item 3's original Review Lens
     recommendation — `B_review` and the Phase 2 Review Lens prototype should share this
     mechanism, not duplicate it).
   - The review step must produce **actual findings** — specific claims about what could
     be wrong, in the same spirit as this codebase's existing `[severity] claim |
     evidence: ...` convention (`core/flows.py`'s `_FINDING`/`_EVIDENCE`, §2 REQ-004) —
     not generic "consider adding more error handling"-style text. A review turn that
     only re-runs `pytest` a second time, or only produces vague prose, does not count as
     `B_review`; it would just be an unlabeled second instance of `B_retry`.
   - Whether to then attempt a fix is decided from the review's findings plus remaining
     budget — not automatically.
   - **A finding is never counted as a confirmed defect from the agent's self-report
     alone.** Per REQ-004's existing `Confirmed`/`Supported`/`Hypothesis`/`Unknown`
     framing and this codebase's existing `Evidence.status` distinction
     (`VERIFIED`/`CLAIMED`, §2 REQ-011), a `B_review` finding only becomes "confirmed" if
     the host can actually verify it — e.g. it corresponds to a new, host-run test that
     genuinely fails, or a reproducible repro script, not merely text the agent wrote.
4. **`B_review` may be built in a later phase** (Phase 2, alongside the Review Lens
   prototype it shares machinery with — §11.4's revised phase table). It is explicitly
   **not** part of this round's "minimal benchmark harness expansion," which stays
   test-guided-retry-only per the governing instruction's "不得同時開發 Review Lens."
5. **The Phase 1 report (§11.8) does not, and must not, present `B_retry`'s results as
   evidence about review effectiveness.** Any future Phase 3 report comparing arms must
   use `B_review`, once built, for that specific question; `B_retry` remains a valid,
   separate comparison point ("does bounded retry help at all") but answers a narrower
   and different question than "does review help."

---

### 11.7 Benchmark correctness verification (round 2)

Checked against the current `benchmarks/toy-agents/run.py`, each item the governing
instruction asked about:

| # | Question | Finding |
|---|---|---|
| a | Do all arms start from the same clean repository? | **Yes, by content.** `make_repo(base, name, label)` is called independently by `run_direct`/`run_b_retry`/`run_ccc`, each producing its own fresh directory (`{name}-{label}`) seeded from the identical `TASKS[name]["buggy"]` source and `TASKS[name]["visible"]` tests, then `git init`+committed as the baseline. Arms run in **separate working directories with identical starting content**, not a single shared directory reused in place — this was already true before this round and is unchanged. Documented explicitly in code comments now; no behavior change needed. |
| b | Is the hidden acceptance test isolated from agent-visible information? | **Yes, confirmed.** `judge()` (called only from `case_from()`, which every arm calls only *after* its agent process has already finished) writes the hidden test file, runs it, then deletes it in a `finally` block. The agent process for any arm never has the hidden file on disk at any point during its own execution. Unchanged this round. |
| c | Does each arm record the actual model version, execution config, and resource usage? | **Partial gap, confirmed, not fixed this round.** Token usage: yes, recorded (see item d). Execution config (sandbox flags, `--ignore-user-config` etc.): implicit in the fixed CLI invocation, not recorded as data. **Model identity: no, for any arm, and this is a pre-existing limitation outside this round's scope** — `infra/telemetry.py`'s `usage_from_output()` only populates `reported_models` for the `claude` provider branch; the `codex` branch never extracts a model identifier from Codex's `--json` stdout, so neither `run_direct`, `run_b_retry`, nor `run_ccc` has ever recorded which model actually served a request. Because all three existing/current arms share the exact same unmodified codex-exec invocation style (no explicit `--model` override anywhere), they are at least *consistent* with each other today — REQ-009's "same model version across arms" holds by construction, even though the actual version string is unrecorded. **Not fixed this round**, deliberately: fixing it would mean either touching `src/ccc_mcp/infra/telemetry.py` (out of scope — "不要...改壞...核心" and not benchmark-only) or adding ad-hoc version capture to only the new `b_retry` arm, which would make cross-arm comparison *less* fair, not more, by recording config for one arm and not the others. Recommended as its own small, symmetric follow-up (capture `codex --version` for all three arms at once) — tracked here, not done. |
| d | Is all execution cost accumulated after a retry, not just the last round's? | **Yes, correct, verified by test.** `run_b_retry()`'s `_merge_tokens()` sums every round's token usage (not just the final round) before passing it to `case_from()`; `test_run_b_retry_retries_once_then_succeeds` asserts the two-round total equals the sum of both rounds' usage, not either round alone. |
| e | Do `--arms`' default execution result, output format, order, and exit code stay compatible with the old version? | **Yes, verified by test.** Default `--arms` value is still `"direct,ccc"`; `parse_arms("direct,ccc") == ["direct", "ccc"]` (same order as the old hardcoded tuple); `run_direct`/`run_ccc`/`case_from` are byte-for-byte unmodified, so their output format is unchanged; the success exit code path (implicit 0) is unchanged since nothing new runs on the default path. The only *new* exit behavior is on invalid `--arms` input (below), which did not exist as a validated input before this round (previously `--arms` didn't exist at all). |
| f | Are duplicate, blank, and unknown `--arms` values handled explicitly? | **Fixed this round, was previously unhandled for duplicates/all-blank.** `parse_arms()` now: drops individually blank entries from stray commas (`"direct,,ccc,"` → `["direct", "ccc"]`, no error — these carry no meaning); hard-errors (`sys.exit`) on an **all**-blank/empty `--arms` value (previously would have silently produced an empty arm list and done nothing, with no error — a worse failure mode, now fixed); hard-errors on an **unknown** arm name (already present in round 1); and **newly** hard-errors on a **duplicate** arm name (e.g. `"direct,direct,ccc"` — round 1 did not check this and would have silently run `direct` twice per task, doubling its Codex spend without warning). All four behaviors are covered by new tests (§11.8). |

---

### 11.8 Round-2 delivery report

**Scope honored:** this round changed only the benchmark definition (`B_retry` rename +
`B_review` specification) and the harness's own correctness (`parse_arms` hardening). No
Review Lens, no Workflow Engine/State Machine changes, nothing under `src/ccc_mcp/core/`
touched, `direct`/`ccc` default behavior unchanged (verified by test and by not modifying
`run_direct`/`run_ccc`/`case_from` at all).

**Files actually changed this round:**
- `benchmarks/toy-agents/run.py` — renamed `run_review`→`run_b_retry`, arm key
  `review`→`b_retry`, output file `review.jsonl`→`b_retry.jsonl`; rewrote the module and
  function docstrings to state explicitly that this arm is Test-Guided Retry, not review;
  hardened `parse_arms()` to reject all-blank input and duplicate arm names (new), in
  addition to the unknown-arm rejection it already had.
- `tests/test_toy_agents_run.py` — renamed tests to match (`run_review`→`run_b_retry`
  throughout); added `test_run_b_retry_does_not_retry_once_visible_tests_pass_even_with_a_real_defect`
  (the regression test proving B_retry cannot catch a visible-tests-passing defect);
  added `test_parse_arms_drops_blank_entries_from_stray_commas`,
  `test_parse_arms_rejects_all_blank_input`, `test_parse_arms_rejects_duplicate_arms`,
  and `test_arms_dict_keys_match_what_parse_arms_validates_against`; tightened
  `test_case_from_base_shape_is_unchanged` from a superset check to an exact-equality
  check (now meaningful, since `case_from` truly was never touched); renamed the opt-in
  smoke test to `test_run_b_retry_against_real_codex_smoke`. No test was deleted.
- `docs/development/ACQE-GAP-ANALYSIS.md` — this section and the naming corrections
  throughout §11.3/§11.4/§11.5.

**Full pytest results (this round, `pytest tests/ -q`, actually run and read in full —
not estimated):**

| Outcome | Count |
|---|---|
| Passed | 853 |
| Failed | 0 |
| Skipped | 1 |
| Deselected | 2 (one pre-existing `real_agents`-marked test elsewhere in the suite, plus this round's own `test_run_b_retry_against_real_codex_smoke` — both excluded by `pyproject.toml`'s default `-m "not real_agents"`) |

Run time: 478.81s (~8 minutes). (The toy-agents suite itself contributes 14 passed / 1
deselected of that total — see `tests/test_toy_agents_run.py`, independently verified by
running that file alone.)

**`ruff check .` (whole repository):** all checks passed, no new warnings.

**Real Codex integration test — NOT run, explicitly unverified.** This round did **not**
invoke `pytest -m real_agents`, and did not run `benchmarks/toy-agents/run.py` against a
real, installed Codex CLI. Everything reported above was verified through the stubbed
unit tests only (real `pytest`/`git` subprocesses against a real generated toy repo, but
a faked `codex exec` call). `test_run_b_retry_against_real_codex_smoke` exists
specifically to close this gap and is marked for a human to run deliberately
(`pytest -m real_agents tests/test_toy_agents_run.py -v`) before trusting the harness
with a real, quota-spending batch — this has not been done as part of delivering this
round.

**Phase 1 acceptance — status:**

| Criterion | Status |
|---|---|
| `run.py run --out DIR --arms direct,b_retry,ccc [tasks...]` runs all three arms and produces `direct.jsonl`/`b_retry.jsonl`/`ccc.jsonl` | ✅ Implemented; not run against real Codex (see above) |
| `run.py run --out DIR [tasks...]` (no `--arms`) behaves identically to before `b_retry` existed | ✅ Verified by test |
| `B_retry` is clearly, irreversibly distinguished from `B_review` in code, docs, and this report | ✅ |
| `B_review` is fully specified (even though unbuilt) with explicit no-self-report / host-verification requirements | ✅ Specified in §11.6; **not implemented** |
| Benchmark correctness checklist (§11.7, items a–f) | ✅ Verified/fixed, with item **c (model/config recording) an acknowledged, unfixed, pre-existing gap** — tracked, not silently ignored |
| All existing + new unit tests pass; no real Codex/Claude spend in the default test run | ✅ |
| `ruff check` passes | ✅ |
| Nothing under `src/ccc_mcp/` touched | ✅ |
| Real Codex integration run | ❌ **Not performed this round — explicitly unverified, not claimed otherwise** |
| New fixture authoring (architecture/concurrency/data-integrity cases, no-defect controls) | ❌ Not started — tracked in §11.3 item 5 as a Phase 1→3 blocker, unchanged by this round |

This round is complete for the scope the governing instruction set ("本輪先修正
Benchmark 定義及必要測試"). What remains before Phase 1 can be called fully done: a
human-run real-Codex verification pass, and the new case-fixture authoring work — neither
was in this round's scope, both are already tracked above and in §11.3/§11.4.

---

## 12. Round 3 — Phase 1 Benchmark Reliability & Reproducibility

A new, independent task on top of round 2's `B_retry` fix: the benchmark's semantics
were corrected, but it still wasn't recording enough about *how* each run actually
happened to trust a future A/B/C0/C1 comparison. This round adds that recording, verifies
the harness's isolation/fairness properties with new tests (not just prose claims), and
designs (without building) `B_review`.

**Scope discipline, same as round 2:** nothing under `src/ccc_mcp/core/` was touched; no
Review Lens; no Adaptive Router; `direct`/`ccc` default behavior and existing output
fields are both unchanged (verified by test); `B_review` is designed in §12.4 only, not
implemented.

### 12.1 Task 1 — experiment environment recording

Every case produced by `run_direct`/`run_b_retry`/`run_ccc` now carries a `run_envelope`
field (additive, via `case_from()`'s existing `extra` parameter — `case_from()` itself is
still never modified) with:

| Field | Source | Honest when unavailable? |
|---|---|---|
| `provider` | hardcoded `"codex"` (what this harness actually uses) | — |
| `requested_model` | explicit `--model` override if one is ever passed; for `ccc`, read live from `agent_executions.options.agent_settings.model` (the *real* resolved per-dispatch settings `core/tasks.py`'s `dispatch_agent` persists, not a guess) | `None` today — no arm sets one |
| `observed_model` | parsed from the CLI's own `--json` output, if present | `None` — confirmed Codex's `--json` output never exposes it (see §11.7 item c); never backfilled from `requested_model` |
| `model_verification_status` | `"confirmed"` only if `observed_model` was actually extracted; `"requested_unconfirmed"` if a model was asked for but not confirmed; `"unknown"` otherwise | — this is the field that stops "requested" from ever being read as "observed" |
| `cli_version` | `codex --version`, cached per process | `None` if the CLI can't be invoked |
| `requested_effort` | same source as `requested_model` | `None` today |
| `repo_commit` | `git rev-parse HEAD` of the arm's own freshly-made repo | — always available; `make_repo` always leaves exactly one commit |
| `case_id` / `arm_id` / `run_id` | `name` / the arm's key in `ARMS` / one UUID generated once per `run.py run` invocation and shared across every case it produces that run | — |
| `stages` | one dict per distinct agent call (`direct`: 1; `b_retry`: one per round, with `visible_tests_passed`; `ccc`: one per role dispatch, with `execution_id`/`status`, elapsed from `agent_executions.created`/`finished`) | per-stage `seconds` is `None` if timestamps are missing/unparseable, never guessed |
| `budget` | the arm's actual caps (`direct`: 1 call; `b_retry`: `max_follow_ups+1` calls; `ccc`: `max_steps` dispatches + the project's real `review_gate.max_rounds`, read live, not hardcoded) | — |
| `tool_permissions` | the arm's actual sandbox/approval flags (`direct`/`b_retry`: the literal `--sandbox workspace-write -a never` used; `ccc`: `authorize_project_local`'s read-write grant) plus an honest "no Skills/MCP installed in this toy benchmark" note | — |

Total token usage already summed across all rounds for `b_retry` (round 2); unchanged.
`seconds` (the existing top-level field) stays the *whole-case* wall-clock time;
`run_envelope.stages[*].seconds` is the new per-stage breakdown underneath it — both are
now present, matching the instruction's "每個階段與整個任務的執行時間."

**What this does not fix (reported honestly, not silently patched):** actual Codex model
identity remains unconfirmable for any arm — this is a `infra/telemetry.py` limitation
outside this round's scope (§11.7 item c), unchanged. `requested_model`/`requested_effort`
are genuinely `None` today because no arm asks for a specific one; `model_verification_status`
will correctly read `"unknown"` for every case this harness produces until that changes.

### 12.2 Task 2 — isolation and fairness, verified by test (not just read)

| # | Question | Verified by |
|---|---|---|
| 1 | Same initial version, independent working directory per arm | `test_each_arm_gets_its_own_independent_working_directory` — confirms `make_repo` gives each arm a distinct path with identical starting content |
| 2 | One arm's edits can't pollute another's | Same test — mutates one arm's copy, confirms the other is untouched (structurally guaranteed: separate directories, never a shared one) |
| 3 | Hidden test unreadable by the agent | `test_hidden_test_file_does_not_exist_while_run_b_retry_is_working` — asserts the hidden test file is absent from disk at the exact moment each codex call runs, on every round |
| 4 | Hidden-test failure never leaks into retry feedback | `test_retry_feedback_prompt_is_built_only_from_the_visible_test_output` — pins that the retry prompt contains the *visible* suite's own failing test name and never the hidden suite's |
| 5 | All agent calls/tests/retries have recorded time and observable usage | §12.1's `stages`/`run_envelope` — `test_run_b_retry_envelope_has_one_stage_per_round_with_a_shared_run_id` confirms one stage per round, each with a `seconds` value |
| 6 | Different arms' tool permissions and fix budget are clearly shown | §12.1's `budget`/`tool_permissions` fields — one honest record per case, not left to be inferred from reading the harness source |

No item here required refactoring the harness; all were either already true (1-3,
confirmed by new tests rather than left as prose) or closed by the additive `run_envelope`
work in §12.1 (4-6, and the test-guided-retry-specific isolation in item 4 was already
closed by round 2's `B_retry` correction, re-verified here with a dedicated test).

### 12.3 Task 3 — real Codex smoke test

**Run, with explicit approval, after this document's round-3 content was first written.**
Command, raw console log, and the full case record are preserved at
`benchmarks/toy-agents/smoke-test-evidence/2026-11-10-clamp-b_retry.*` (not left in a
temp directory). Outcome: `codex exec` fixed the planted `clamp` bug correctly on its
first attempt (no retry round needed), 22 seconds, 66,659 total tokens, both the
agent-visible and the separate hidden acceptance test passed, exit code 0. Task 3 is
**verified**, not mocked-and-called-done — see §13.1 for what its `run_envelope` data
revealed about cross-arm consistency.

### 12.4 Task 4 — `B_review` minimal design (not implemented)

Design only, meeting every constraint the governing instruction set:

**Stages** (reusing `run_b_retry`'s subprocess/pytest/`run_envelope` machinery, not
duplicating it):

1. **Implement** — one `codex exec` call, identical prompt/sandbox to `B_retry`'s first
   round. (Could literally share that one stage function with `B_retry` — the two arms
   only diverge after this point.)
2. **Review — unconditional.** Regardless of whether the visible tests just passed, run
   a **second, separate** `codex exec` call with a distinct review prompt: shows the
   implementation diff and the *visible* test result (pass or fail — host-verified data,
   safe to share); explicitly asks the agent to find failure conditions, cite concrete
   evidence, and where possible give a reproducible counterexample input — reusing this
   codebase's own existing `[severity] claim | evidence: ...` finding grammar
   (`core/flows.py`'s `_FINDING`/`_EVIDENCE`/`_FORMAT`, §2 REQ-004) instead of inventing a
   new one; explicitly permits "no blocking problem found" as a valid outcome (mirrors
   this product's own AR convention of never forcing a finding). **Never given the hidden
   test** — same isolation guarantee as `B_retry`'s retry stage (§12.2 item 3/4), and
   testable the same way.
   - Recommend running this stage with `--sandbox read-only` instead of
     `workspace-write`: a reviewer that cannot edit files structurally cannot blur into
     "a second implementation attempt," mirroring this codebase's own `READ_ONLY_ROLES`
     design for its real AR role.
3. **Parse findings** using the existing `_FINDING`/`_EVIDENCE` regex grammar (reuse, not
   a new parser). A review turn that produces no `[severity] claim | evidence: ...` lines
   yields zero findings — not an error, not a fabricated one.
4. **Decide whether to fix**, from parsed findings plus remaining budget — not
   automatically. Only `blocker`/`major` findings that include cited evidence justify a
   fix attempt (mirrors `core/flows.py`'s own `judge_adversarial()` threshold, §2 REQ-004
   — proven, reusable logic for "which findings matter enough to act on").
5. **Fix** (only if step 4 says so) — one more `codex exec` call, given the review's
   findings as feedback, same mechanism as `B_retry`'s retry prompt. Then re-run the
   visible test suite once, for the record (not as the stopping condition — review drives
   this arm, not the test).
6. **Host verification of findings, two honest tiers:**
   - **Minimal (recommended for the first `B_review` prototype):** a finding only counts
     as having *mattered* if, after step 5's fix, the task's `resolved` outcome (already
     computed post-hoc by the existing `judge()`/hidden test, exactly as every other arm
     is scored) actually flips from failing to passing. This reuses 100% existing
     infrastructure and requires no new verification engine.
   - **Fuller (a later, separately-scoped increment, not proposed for the first build):**
     attempt to auto-construct a small host-run check from a finding's own cited
     counterexample and actually run it against the pre-fix implementation. This is
     real, non-trivial engineering (an auto-test-generator, however narrow) and is
     flagged here as a cost item, not quietly assumed — consistent with §7.3's original
     warning about underestimating fixture/verification engineering effort.
   - Either tier: a finding is `"hypothesis"` until host-verified, never promoted to
     `"confirmed"` from the agent's own text, and a review turn's output is never written
     into any record as if a human or the host had said it (REQ-004/REQ-011's existing
     Claimed-vs-Verified distinction, reused, not reinvented).
7. **Cost accounting** — identical shape to `B_retry`/`C0`'s `run_envelope`: `stages`
   covers implement + review + (optional) fix, each with its own timing and token usage,
   summed via the existing `_merge_tokens()` (no new accounting mechanism); `budget`
   reports `max_agent_calls` (2 or 3, implement+review(+fix)) the same way `B_retry`
   already does.
8. **Comparability with C1** — use the identical `run_envelope`/`model_record` shape so a
   Phase 3 report can line arms up; because `B_review` counts "agent calls" while
   `C0`/`C1` count "role dispatches," **the fair cross-arm comparison axis is total token
   usage and/or total wall-clock seconds, not matching budget field names 1:1** — this is
   called out explicitly so Phase 3's report doesn't claim a false apples-to-apples
   reading of differently-shaped budget caps.

**Not decided here, flagged for whoever builds this (Phase 2, alongside the Review Lens
prototype it should share the review-prompt-injection mechanism with, §4 item 3):**
exact wording of the review prompt; whether `read-only` sandbox is actually compatible
with the real Codex CLI's exec flags as used here (untested); which of the two
verification tiers in point 6 the first build actually ships.

### 12.5 Round-3 delivery — status

See the conversation turn that produced this round for the full report (git diff
summary, pytest/ruff results, Task 3's outcome, remaining fairness gaps, and the
Phase-1 go/no-go recommendation) — repeating it here would duplicate, and risk drifting
from, the authoritative numbers reported there.

---

## 13. Round 4 — model consistency, cost reliability, B_review validation design

The real smoke test (§12.3) proved one execution path works; it did not establish that a
future A/B/C0 comparison would be fair. This round checks that specifically, investigates
what the smoke test's own cost numbers actually mean, and tightens `B_review`'s design
with concrete validation cases and a corrected per-finding confirmation rule.

**Scope discipline, unchanged:** nothing under `src/ccc_mcp/core/` touched; no Review
Lens; no Adaptive Router; `direct`/`ccc` defaults and existing output fields unchanged
(the new `--model`/`--effort` flags default to `None`, producing byte-identical argv to
before they existed — verified by test); no high-cost batch self-started (the only real
Codex spend this round was the single, pre-approved §12.3 smoke test).

### 13.1 Model selection consistency — a real gap found, and fixed

**Checked by reading the actual dispatch code, not assumed:**

- `direct`/`b_retry` never passed an explicit model (raw `codex exec`, no `--model` flag)
  and `ccc` never configured one either (`configure_role("demo", role, "codex")` with no
  `model=` argument) — confirmed in round 2/3. **All three arms were already consistent
  with each other on model selection specifically**: none requested one, all used
  whatever the local CLI/account's own default resolves to.
- **A different, real inconsistency was found while checking this**, between how `ccc`
  invokes Codex versus how `direct`/`b_retry` do: ccc-mcp's real adapter
  (`adapters/agents/codex.py:79-97`) always passes `--output-schema <file>
  --output-last-message <file>`, constraining the model to schema-validated structured
  output and parsing its final report from that file. `direct`/`b_retry` pass neither
  flag — they take whatever free-form text Codex produces and never attempt to parse a
  real report from it (`case_from()`'s `report` field is a hardcoded
  `{"summary": f"Fixed {name}", ...}`, not derived from anything Codex actually said).
  **This is a genuine methodological confound for any future A/B/C0/C1 comparison**:
  schema-constrained generation is a different task shape than free-form generation, and
  may measurably affect a model's performance independent of the "how many roles"
  question the comparison is supposed to isolate. Documented in
  `_SANDBOX_TOOL_PERMISSIONS["structured_output"]` (now visible in every `direct`/
  `b_retry` case's `run_envelope`) rather than silently left for someone to discover
  later. **Not resolved this round** — adding `--output-schema` to `direct`/`b_retry`
  would itself be a non-trivial, scope-expanding change (needs a schema file, a parser,
  and a decision about what "the report" means for a single free-standing `codex exec`
  call with no ccc-mcp task underneath it) and risks conflating "model consistency" with
  "building part of a new output-parsing pipeline." Flagged as a concrete Phase 1→2 item.
- **Token accounting methodology confirmed consistent across all three arms** by reading
  `evaluation_engine/evaluation_service.py`'s `_task_tokens()` (used by `ccc`, via
  `export_scoring_cases`): it sums the same per-execution `usage.tokens` dicts
  (`input_tokens`/`cached_input_tokens`/`output_tokens`/`reasoning_output_tokens`) that
  `infra/telemetry.py`'s `usage_from_output()` produces — the exact function
  `direct`/`b_retry` call directly — and computes `tokens_total` with the identical
  `input_tokens + output_tokens` formula. This was not previously verified; it now is.

**Fixed this round — minimal model pinning**, so a future experiment *can* request the
same model/effort everywhere, without changing what happens when nothing is requested:

- `codex_exec_argv(repo, model=None, effort=None)` — new, shared by `direct`/`b_retry` —
  inserts `--model <x>` / `-c model_reasoning_effort=<x>` at the identical position and
  with the identical flag syntax `adapters/agents/codex.py`'s real adapter already uses
  (not a new convention).
- `run_ccc(..., model=None, effort=None)` — passes the same values into
  `configure_role(..., model=model, effort=effort)`, i.e. through ccc-mcp's own real
  config path rather than a parallel mechanism.
- `run.py run --model X --effort Y` — new, optional CLI flags, threaded to every arm
  uniformly. Default `None` for both, verified by test to produce byte-identical argv to
  before this flag existed.
- The existing `requested_model`/`requested_effort`/`model_verification_status`
  distinction (round 3) is unchanged: pinning a request **never** fabricates an
  observation. A pinned run still reports `model_verification_status:
  "requested_unconfirmed"`, not `"confirmed"` — Codex's `--json` output still doesn't
  expose which model actually served the request (§11.7 item c, unchanged, still not
  fixed, still honestly reported rather than assumed).

### 13.2 Cost and timing data — provenance and reliability

The smoke test recorded, for one `codex exec` call: `input_tokens: 66370`,
`cached_input_tokens: 61568`, `output_tokens: 289`, `22s`. Checked precisely what these
mean:

- **Source**: `usage_from_output("codex", stdout)` (`infra/telemetry.py:20-51`) sums the
  `usage` object of every `turn.completed` line in Codex's own `--json` exec stream for
  that **one process invocation**. A single `codex exec` call can itself involve several
  internal turns (the agent reading files, running the sandboxed test, editing, etc.);
  the reported numbers are the **sum across all of them**, not a single raw LLM call —
  so yes, this includes whatever system/tool-call overhead Codex's own agent loop
  generates internally, not just "the one prompt the harness sent." The harness has no
  visibility into, and does not separately report, how many internal turns composed that
  sum (a concrete gap — see §13.5).
- **`cached_input_tokens` is a sub-count of `input_tokens`, not additive.** In the smoke
  test, 61,568 of the 66,370 input tokens (~93%) were cache hits. `tokens_total` (=
  `input_tokens + output_tokens` = 66,659) therefore does **not** double-count the cached
  portion — but treating all 66,659 as equally-priced tokens would be wrong in the other
  direction: real-provider pricing typically bills cached input tokens at a steep
  discount versus fresh ones (commonly 50–90% cheaper), so **`tokens_total` is a
  reasonable, consistent relative metric for comparing arms against each other, but is
  explicitly not a dollar-cost estimate** without separately weighting the cached and
  fresh portions by their actual (provider- and time-dependent) prices, which this
  harness does not have and should not guess.
- **`reasoning_output_tokens` is captured in the `tokens` dict but excluded from
  `tokens_total`'s sum** (only `input_tokens + output_tokens`, confirmed in both
  `case_from()` and `_task_tokens()`). In this smoke test it was `0`, so it didn't matter
  here — but on a task where the model does substantial hidden reasoning, `tokens_total`
  would **understate** real output generation (and real cost) versus what the provider
  actually billed. A consistent gap across all three arms (same formula everywhere), not
  arm-specific, but worth fixing in the formula itself before any cost-sensitive
  conclusion is drawn.
- **Duration (`seconds`)** is wall-clock time around the whole `subprocess.run()` call —
  includes process startup, model latency/"thinking" time, sandboxed tool execution
  (file reads, the agent's own test runs inside its sandbox), and network round-trips.
  It is not isolated model-inference time, and isn't claimed to be.
- **Consistent cost/time statistic across arms, as designed (§12.1, unchanged this
  round):** every arm reports the same `tokens` field shape (confirmed same source
  formula, this section) and the same per-stage `seconds` breakdown via `run_envelope`.
  The one remaining asymmetry is **what "a stage" costs beyond tokens/wall-clock** —
  `ccc`'s stages are full role dispatches through ccc-mcp's real worker/evidence/gate
  machinery (more overhead per call, by design, since that machinery is the product
  being evaluated), while `direct`/`b_retry`'s stages are bare `codex exec` calls. This
  is an expected, not a hidden, difference — flagged explicitly rather than implied to be
  equivalent.

**Recommendation adopted:** report `tokens_total` as a *relative* comparison metric
only, always alongside the full `tokens` breakdown (never collapsed to a single number
in a Phase 3 report) and alongside `cli_version`/`model_verification_status`'s own
caveats, with an explicit statement that no dollar-cost figure is claimed anywhere in
this benchmark's output.

### 13.3 `B_review` validation cases (design; §12.4's plan still not implemented)

Four cases, each pinned to a deterministic, known ground truth so the review mechanism's
own correctness can be scored against it — these are tests *of the reviewer*, not
production runs:

| Case | Fixture | Expected `B_review` behavior | A wrong answer looks like |
|---|---|---|---|
| **Visible passes, real bug present** | `clamp`'s `partial` patch (passes both visible tests; fails the hidden upper-bound-with-negative-range case — already used this round in `test_run_b_retry_does_not_retry_once_visible_tests_pass_even_with_a_real_defect`) | Because step 2 of §12.4's design runs *unconditionally*, the review turn must still fire even though visible tests passed, and should produce at least one finding at `hypothesis`/`supported` status describing the untested upper-bound condition | Zero findings (review skipped because tests passed — exactly the `B_retry` failure mode this arm exists to not have) |
| **Visible fails, real bug present** | `clamp`'s `broken` patch (fails a visible test) | A finding citing the actual visible-test failure as evidence should reach at least `supported`, since host-verified evidence (the failing visible test itself) is already available to cite without needing new verification | A finding with no cited evidence, or one that misidentifies the defect |
| **No bug — false-positive control** | `clamp`'s `fixed` patch (correct, passes everything) | Zero blocking findings. Non-blocking/informational commentary is acceptable; a fabricated `blocker`/`major` finding here is a direct measurement of the reviewer's false-positive rate on a case where none should exist | Any `blocker`/`major` finding — this case exists specifically to catch a reviewer that invents problems to seem thorough |
| **Architectural risk, needs more evidence** | A deliberately ambiguous case *not yet built* (not one of the existing small pure-function toy tasks) — e.g. a function that passes all given tests but relies on an unstated invariant a caller could violate, or holds state across calls in a way that's fine today but risky under reuse this fixture's own tests never exercise | A finding at `hypothesis` (or `supported` if it cites real code evidence) — but **never** `confirmed`, since by this case's own design nothing here can be host-verified without information the review deliberately doesn't have | `confirmed` status on this case is itself a scoring bug in the harness or a calibration failure in the reviewer — this is the case specifically designed to catch that |

**Corrected confirmation rule (this round's fix to §12.4's "minimal tier"):** the
`12.4` design originally proposed inferring a finding's correctness from whether the
*overall task's* hidden-test outcome flipped after a fix. **That's wrong when a review
turn produces more than one finding**: an overall pass doesn't tell you which finding (if
any) was the one that mattered, so it cannot license marking *every* finding `confirmed`.
Corrected rule: only the specific finding(s) whose cited evidence is actually
reproducible against host-run state may reach `confirmed`; an overall pass with multiple
findings, where only one evidently caused the fix, should leave the others at whatever
status their own evidence supports — **never upgraded by association**. For the toy
benchmark specifically, with single, small, single-defect fixtures, this is tractable
(there's exactly one plausible finding that matters per fixture); it is explicitly flagged
as not yet a general solution for multi-finding reviews on real-world tasks, which is a
harder problem out of scope for this round. **Hidden tests remain withheld from the
reviewer in all four cases** — same isolation mechanism and the same kind of test as
`B_retry`'s already-verified guarantee (§12.2 item 3), to be re-applied to `B_review`'s
review stage when it's built.

### 13.4 The core question — proving review found something, not just spent a call

**The governing instruction's own framing is the right design constraint; answering it
needs a control `B_retry`/`B_review` alone cannot provide.** Comparing `B_review`'s
resolved-rate against `B_retry`'s doesn't isolate *why* any difference exists — `B_review`
makes one more agent call than a `B_retry` run that stopped after round 1, so a naive
comparison conflates "review specifically helped" with "one more call of any kind
helped." Proposed design, for Phase 2/3 to adopt, not built this round:

1. **A token/call-matched placebo control**, call it `B_retry2`: the same call budget
   as `B_review` (implement + one more call), but the second call is a blind "try again,
   possibly differently" prompt — no review framing, no finding format, no read-only
   sandbox, nothing. If `B_review`'s fix success rate / regression rate is **not**
   measurably better than `B_retry2`'s at the same budget, that specific result says the
   extra call helped for reasons unrelated to reviewing — an important, honest negative
   result, not a failure to hide.
2. **§13.3's four validation cases**, scored independently of any downstream fix: do the
   review turn's own findings correctly match known ground truth (recall on cases 1-2,
   false-positive rate on case 3, correct non-overconfidence on case 4)? This measures
   whether the *review step's output* tracks reality, regardless of whether a fix
   happens to follow — a check `B_retry2` has no equivalent of, since it never produces
   findings at all.
3. **Only both together answer the question.** (1) alone could show an improvement from
   noise or prompt-length effects, not genuine review. (2) alone proves the findings are
   calibrated but not that acting on them changes outcomes. Together: a real review
   effect requires both — findings that track ground truth (2), that lead to
   measurably better outcomes than an equally-expensive blind retry (1).

This answers the round's explicit closing question with a concrete, falsifiable design
rather than a promise: Phase 3 should not claim "review works" from `C1 > B_retry` alone,
and should not claim it from `B_review`'s existence alone either — it needs `B_review`
beating its own `B_retry2` control, backed by §13.3's finding-calibration scores.

### 13.5 Known limits carried forward (not resolved this round)

- `direct`/`b_retry` vs. `ccc`'s structured-output (`--output-schema`) difference
  (§13.1) — a real, now-documented methodological confound, not fixed.
- Model identity remains unconfirmable for any arm (§11.7 item c, §13.1) — pinning a
  request (new this round) does not change this; `model_verification_status` stays
  honest about it.
- No per-internal-turn breakdown of a single `codex exec` call's token/time usage
  (§13.2) — the harness only sees the summed total Codex's CLI itself reports.
  `reasoning_output_tokens` excluded from `tokens_total`'s formula (§13.2) — consistent
  across arms, but understates true cost/output on reasoning-heavy tasks.
- `B_review` itself: still a design, not code (§12.4, refined §13.3) — building it,
  the §13.4 `B_retry2` control, and the §13.3 case 4 fixture are all separate, not-yet-
  started work.
- New fixture authoring (architecture/concurrency/data-integrity cases, §11.3 item 5)
  remains not started.

---

## 14. Round 5 — minimal `B_review` prototype (B0/B1/B2) and four product decisions

The product owner reviewed round 4 and issued four decisions before authorizing
prototype-level implementation. This round implements exactly what they license —
B0/B1/B2 — and nothing past it (no Adaptive Router, no Review Lens beyond the two
prompt variants B1/B2 need, no `src/ccc_mcp/core/` changes).

### 14.1 Decision 1 — two experiment kinds, not one unified harness

**`--output-schema` stays on the `ccc` arm.** Removing it to make arms "fair" was
rejected by the product owner as itself a risk: it's load-bearing for ccc-mcp's real
role-handoff protocol, and stripping it would test something ccc-mcp doesn't actually do
in production. Decision 1 instead formalizes two separate experiment kinds, each
internally fair on its own terms:

1. **End-to-End Product Comparison** — `direct` / `b_retry` / `ccc` (Arms A/B_retry/C0),
   unchanged from rounds 1-4. Each keeps its tool's native mechanism. Known
   confounder, now stated as policy rather than left implicit: `ccc`'s structured
   output vs. `direct`/`b_retry`'s free-form output is a real methodological
   difference (§13.1); **any report using these three arms must attribute a result
   difference to "the whole tool," never specifically to "more roles," without also
   controlling for this.**
2. **Controlled Review Experiment** — B0/B1/B2 (this round), purpose-built to pin
   everything *except* review strategy: same model (round 4's `--model`/`--effort`),
   same frozen starting code (§14.2), same sandbox/tool permissions, same review-call
   budget, same finding format (both reuse `core/flows.py`'s existing grammar). This is
   the harness that can actually isolate "did reviewing help," which Decision 1's own
   End-to-End arms structurally cannot.

### 14.2 Decision 2 — B0/B1/B2, implemented

New section in `benchmarks/toy-agents/run.py` (`run_frozen_implementation`, `run_b0`,
`run_review_arm`, `run_controlled_review_experiment`, plus `general_review_prompt`/
`contextual_review_prompt`/`extract_agent_text`/`host_verify_findings`), and a new CLI
command, `review-experiment` (separate from `run`, so `direct`/`ccc`'s default behavior
and the existing `ARMS` dict are completely untouched — verified by the unchanged
passing status of every round 1-4 test).

- **Frozen initial output**: `run_frozen_implementation()` makes exactly one `codex
  exec` call, commits the result, and every arm clones that one commit
  (`shutil.copytree`, `_clone_repo`) rather than regenerating — B0/B1/B2 are
  guaranteed to start from byte-identical code (verified by
  `test_controlled_review_experiment_shares_one_frozen_implementation`).
- **B0**: the frozen output, scored as-is, zero extra calls — the no-review baseline.
- **B1/B2 review unconditionally**, even when the frozen output already passes visible
  tests (`test_review_runs_even_when_the_frozen_implementation_already_passes_visible_tests`)
  — the behavior `B_retry` explicitly lacks, by design (§11.6).
- **B1 vs. B2 differ only in prompt content**: `general_review_prompt()` and
  `contextual_review_prompt()` share every sentence except one — B2 adds a single line
  naming that task's own known risk area (`TASK_REVIEW_FOCUS`). Same sandbox
  (`read-only`), same instructions, same finding-format requirement, same budget.
  Verified by `test_b1_and_b2_prompts_differ_only_by_the_contextual_focus_sentence`, not
  merely asserted.
- **Reviewer is read-only** (`codex_exec_argv(..., sandbox="read-only")`) and never sees
  the hidden test, re-verified for this new arm specifically by
  `test_review_arm_never_exposes_the_hidden_test_to_the_reviewer` — the diff and the
  *visible* test's own output (host-verified data) are the only task-specific content in
  the review prompt.
- **Token/time recorded per stage, never assumed equal**: `run_envelope.stages` for
  B1/B2 is `[implement_frozen, review, (fix)]`, each with its own `seconds` and its own
  entry in the token sum — the shared implement stage's cost appears identically in B0,
  B1, and B2's records (by construction, since it's the same stage object), so it cancels
  out of any B1-vs-B2 cost comparison automatically.
- **Findings recorded separately from Host verification**: `case["findings"]` is the
  list from `host_verify_findings()` — `{"severity", "claim", "evidence",
  "verification_status"}` per finding — never merged into or mistaken for the task's own
  `resolved` field.
- **Bounded fix**: only `blocker`/`major` findings whose `verification_status` is
  `confirmed` or `supported` justify a fix attempt, capped at `max_fix_attempts` (default
  1, `--max-fix-attempts 0` disables fixing entirely while still reviewing) — verified by
  `test_review_arm_attempts_a_bounded_fix_only_for_blocking_findings`.

### 14.3 Decision 3 — token statistics, checked against raw data, not re-asserted

Round 4's claims were checked against a **second real Codex call**, this one capturing
the complete raw `--json` stream (not just the parsed usage), preserved at
`benchmarks/toy-agents/smoke-test-evidence/2026-11-10-clamp-raw-codex-stream.jsonl`
(local temp-directory path redacted before committing; no keys/credentials/personal data
present — checked before adding it, per the governing instruction's explicit requirement).

- **`input_tokens` vs. `cached_input_tokens`**: the raw data (`input_tokens: 66628`,
  `cached_input_tokens: 61696`) is consistent with `cached_input_tokens` being a
  sub-count of `input_tokens`, not an additive pool — `cached_input_tokens <
  input_tokens` in both real captures obtained so far. **This is the standard OpenAI/
  Codex API convention, not something `ccc-mcp`'s own code asserts or validates** —
  `usage_from_output()` sums whatever keys Codex reports, with no interpretation of their
  relationship. Stated as "consistent with, not proven by, this harness's own code,"
  per the instruction not to assert more than the data supports.
- **`reasoning_output_tokens` vs. `output_tokens`**: **genuinely unresolved — marked
  Unknown, not guessed.** `reasoning_output_tokens` was `0` in both real captures
  obtained so far (`clamp` never triggers extended reasoning), so there is no data point
  that distinguishes "additive" from "subset" for this pair. `tokens_total`'s formula
  (`input_tokens + output_tokens`) is **not changed this round** — the instruction
  explicitly says not to add reasoning tokens into the total without verification, and
  verification isn't possible from the data obtained so far.
- **How multiple `turn.completed` events accumulate**: **both real captures show exactly
  one `turn.completed` event per `codex exec` call** — correcting round 4's §13.2, which
  assumed (without checking) that multiple internal turns get summed. For these simple
  toy tasks, Codex's CLI itself reports one cumulative usage figure per invocation; there
  is only one event for `usage_from_output()`'s loop to sum, so no double-counting
  across turns is possible to check further with the data in hand — moot for this task
  set, not necessarily moot for a longer, harder task that might produce several.
- **No double-counting of the same stat event**: confirmed by code inspection
  (`usage_from_output()` sums each `turn.completed` line exactly once, in stream order,
  with no re-processing) and consistent with the single-event captures.
- **A field the parser doesn't capture**: the raw stream contains
  `cache_write_input_tokens` (value `0` in both captures), which `usage_from_output()`
  does not sum into `tokens` at all — a real, minor completeness gap, now documented
  (README in the evidence folder) rather than silently absent. Not fixed this round
  (touching `infra/telemetry.py` is outside this round's `src/ccc_mcp/` prohibition).
- **Still true from round 4, re-affirmed**: `tokens_total` is a same-formula, same-source,
  cross-arm-consistent **relative** metric only — never a dollar-cost estimate. No
  monetary figure is computed or implied anywhere in this harness's output.
- **One new fairness note this round surfaces**: cache hit rate is a property of the
  *account's* session/cache state at the time of the call, not purely of the task — both
  real captures on this same local account showed ~93% cache hits. A benchmark run on a
  cold account (or a different account entirely) could show a very different
  fresh-vs-cached split for the identical task, which would change the *relative* cost
  comparison's apparent magnitude even though nothing about the task changed. Flagged as
  a reproducibility caveat for any future report, not resolved here.

### 14.4 Decision 4 — the four validation cases, now backed by passing tests

| Case (§13.3) | Fixture used | Verified by |
|---|---|---|
| Visible passes, real bug present | `clamp`'s `partial` | `test_host_verify_findings_stays_supported_when_the_probe_does_not_target_that_defect` — confirms the harness is honest about what it *can't* verify (own_good doesn't target `partial`'s actual negative-range defect), rather than claiming more than it can check |
| Visible fails, real bug present | `clamp`'s `broken` | `test_host_verify_findings_confirms_a_single_evidenced_finding_when_the_probe_fails` and the end-to-end `test_review_arm_attempts_a_bounded_fix_only_for_blocking_findings` (review → confirmed finding → fix → `resolved: true`) |
| No bug — false-positive control | `clamp`'s `fixed` | `test_host_verify_findings_no_false_confirm_on_a_correct_implementation` — a fabricated finding with fake "evidence" is never auto-confirmed against a correct implementation |
| Architectural risk, needs more evidence | Not a real toy-task fixture (§13.3 flagged this as needing new fixture authoring, still true) | `test_host_verify_findings_stays_supported_with_multiple_evidenced_findings` stands in for the calibration property this case is meant to test — ambiguous/multi-claim situations stay at `supported`, never blanket-upgraded to `confirmed` |

All four are exercised through stubbed `codex exec` calls (zero additional real Codex
spend this round beyond the one bounded raw-capture call in §14.3) — 12 new tests in
`tests/test_toy_agents_run.py` covering `host_verify_findings`, `extract_agent_text`
(including against the real captured stream, not only synthetic fixtures), and the full
B0/B1/B2 orchestration.

### 14.5 Answering the round's closing question, now with a working prototype

The question — "how to prove review found something, not just added a call" — now has
real machinery behind its answer (§13.4's design): `B0` isolates the no-review baseline,
`B1`/`B2` isolate review strategy from everything else via the frozen-implementation +
identical-budget construction, and `host_verify_findings()` prevents the single most
likely way this experiment could lie to itself (an overall pass getting misread as proof
every finding was right). **`B_retry2` (round 4's budget-matched blind-retry
placebo) is not built this round** — the product owner's Decision 2 explicitly demoted
it to a secondary, cost-focused comparator rather than the primary evidence; B1 vs. B2
(general vs. contextual review, both reviewing) is now the prototype's actual primary
comparison, per the round's stated core goal ("情境化審查能否比一般審查找出更多真正存在的
問題").

### 14.6 What round 5 does not do

- No real Codex run of `review-experiment` itself — the two real calls this round made
  were both single, bounded, previously-described diagnostic calls (the original
  smoke test's arm, and §14.3's raw-stream capture), not a run of the new B0/B1/B2 flow.
  Running B0/B1/B2 for real costs at minimum 3 calls per task (1 implement + 2 reviews,
  more with fixes); per the governing instruction's "真實 Codex 實驗必須保留呼叫與成本
  上限," this needs its own explicit command-and-cap proposal before it's run — see the
  conversation turn that delivered this round for whether that was subsequently
  proposed/approved.
- No Case 4 (architectural risk) *integration* fixture — still only validated at the
  `host_verify_findings()` calibration level (§14.4's table), not as a full toy-task with
  its own hidden test.
- No Review Lens beyond B1/B2's two prompt variants; no Adaptive Router — both
  explicitly out of scope per this round's instruction.
- §13.1's `--output-schema` asymmetry between `ccc` and `direct`/`b_retry` remains
  unresolved (now policy per Decision 1, not a bug to fix).
- `reasoning_output_tokens` vs. `output_tokens` semantics remain genuinely Unknown —
  not resolved by this round's additional data point.

## 15. Codex continuation of Claude's ACQE prototype (2026-11-10)

This section records a real Claude Code → Codex handoff, not an independent benchmark
result. It supersedes §14.2's task-specific B2 focus and §14.4/§14.6's statement that
the fourth fixture has not been built. Earlier sections remain as historical records.

### 15.1 Recovered state and persistence

- The repository arrived with Claude's uncommitted ACQE changes intact. Codex first
  checked git status and diff and preserved all pre-existing work. The harness, tests,
  this document, and smoke-test-evidence/ contained the Phase 0–round 5 implementation
  and evidence. Unrelated UI/onboarding edits were left alone.
- The durable handoff was the repository. The local .ccc-mcp/CONTEXT.md and
  .ccc-mcp/context/{CURRENT_STATE,DECISIONS}.md still contain only initialization
  material, with no ACQE decision or Agent result. The ccc-mcp tunnel was unavailable
  during this handoff, so it could not supply a richer live Shared Context. The
  codebase-memory index had been inspected in the preceding Codex turn, but on-disk
  code and git diff were authoritative for uncommitted changes.
- Claude's "36 passed, 1 deselected" and Ruff report were treated as claims and
  independently rechecked. Reconstructing the exact scope of the handoff required
  git diff and this document; Shared Context alone was insufficient. No ACQE
  component was regenerated from an older commit.
- A discrepancy in the prior handoff was found: §14 called B2's task-specific focus
  contextual, but TASK_REVIEW_FOCUS named edge cases more specific than public issues.
  It also treated a multi-finding unit test as a substitute for the missing fourth
  architectural-risk fixture. Both are corrected below.

### 15.2 P0 — B2 prompt isolation

TASK_REVIEW_FOCUS was removed. B2 selects a generic Architecture, Concurrency, or
Data Integrity lens from the public ISSUES[name] text only. Selection never reads
TASKS, hidden tests, own_good, or private metadata. The lens asks general review
questions and separates observed failures from risks requiring more evidence. B1
retains its general prompt; both arms retain identical sandbox, budget, diff,
visible-test output, and finding grammar. Tests poison hidden tests and private
metadata with a sentinel and check both direct prompt construction and stubbed
B0/B1/B2 dispatch; neither review prompt contains that sentinel.

### 15.3 P1 — Finding verification

The former host_verify_findings() could promote a single evidenced Finding whenever
one task-level own_good test failed, even if the Finding described another issue.
It now requires the Finding to cite a fixture-owned exact witness and describe the
matching claim category. The host runs that witness's own_good test against the
reviewed code and the known-good reference. Only one unambiguous matching Finding
can receive confirmed; its record names the host probe and witness. A probe that
also fails on the reference is rejected as unrelated or defective. Other evidenced
Findings remain supported; claims without evidence remain hypothesis. Plain
"no issues found" prose is not counted as a Finding in this experiment. The
overall resolved field remains separate and cannot upgrade any Finding.

This is deliberately narrow fixture-specific confirmation. It is not general
natural-language proof of an agent's whole claim. Findings without an exact host
witness remain supported even when they could be correct.

### 15.4 P2 — architectural-risk control

REVIEW_ONLY_TASKS adds architecture_risk: a path-keyed JSON cache whose documented
current callers do not modify a source between reads. Its visible and withheld
acceptance tests pass. The cache has a plausible future lifecycle/invalidation risk,
but no present defect is established. A stubbed full B0/B1/B2 run verifies that an
assumption Finding stays supported, a reviewer may report no confirmed defect, and
neither arm is forced to make a fix. Existing no-defect clamp controls remain.
REVIEW_ONLY_TASKS is outside TASKS, preserving the older run command's six-case
default and direct,ccc arm default.

### 15.5 P4 — original proposed single-case comparison (not run; blocked by §16)

The handoff originally proposed one clamp case in a fresh output directory:

    python benchmarks/toy-agents/run.py review-experiment clamp --out ~/acqe-b012-clamp --model gpt-6.1-sol --effort medium --max-fix-attempts 1

The §16 preflight found that Clamp's hidden answer is committed in HEAD. The
current CLI refuses this command before making a Codex call; do not use it as a
formal comparison until a genuinely private case replaces the published one.

The model name is a proposed pin and must be confirmed as available to the
operator's local Codex account before running. The maximum is five Codex calls:
one frozen implementation, one B1 review, one B2 review, and at most one fix per
review arm. The harness rejects a max-fix-attempts value outside 0 or 1. Each
Codex call has a 900-second subprocess timeout. Each run_envelope records run ID,
case, arm, baseline commit, requested model/effort, CLI version, permissions,
per-stage wall time and token counts. tokens_total is reported input plus output
tokens, not a currency estimate. A missing observed model remains
requested_unconfirmed. The frozen implementation is copied to B0/B1/B2.
Reviewer prompts contain only the public issue-derived generic lens, repository
diff, and host-visible test output; withheld tests and confirmation probes are
introduced by the host only after review. Stop on a nonzero Codex process exit,
timeout, hidden-answer sentinel in preflight prompt tests, or failed regression
gate. Prompt and temporary-repository isolation are tested. Filesystem-level
isolation from the original harness source was not established during that
handoff; see §16 for the later OS preflight and remaining No-Go conditions. No
real review-experiment run was performed during this handoff.

### 15.6 P3 — independently observed regression results

Commands, exit codes, and observed counts from this handoff:

| Command | Exit | Result |
|---|---:|---|
| .venv/bin/python -m pytest -q tests/test_toy_agents_run.py | 0 | 42 passed, 1 deselected (final targeted run) |
| .venv/bin/ruff check . | 0 | All checks passed |
| git diff --check | 0 | No whitespace errors |
| .venv/bin/python -m pytest -q (restricted sandbox, before the final targeted-test edit) | 1 | 874 passed, 6 failed, 2 deselected; five failures were local socket/process/default-home permissions, one was an assertion read before the per-stage token expectation was updated |
| CCC_MCP_HOME=/private/tmp/acqe-pytest-home .venv/bin/python -m pytest -q [the five environment-restricted test node IDs] (local socket/process permission) | 0 | 5 passed |
| CCC_MCP_HOME=/private/tmp/acqe-pytest-home-full-20261110 .venv/bin/python -m pytest -q (local socket/process permission) | 0 | 882 passed, 2 deselected, 3 deprecation warnings in 502.87 s |

The deselected tests are real-agent tests excluded by pyproject.toml's default
pytest marker. The successful full suite includes existing direct/ccc CLI
compatibility tests. The three warnings come from Python 3.14's forkpty
deprecation in Claude usage tests; they did not fail the run. The B0/B1/B2
comparison itself remains unrun.

## 16. Codex benchmark preflight (2026-11-10; no model experiment run)

This is a preflight of the existing prototype, not an independent benchmark score.
The original `run` command still defaults to `direct,ccc`; the controlled
`review-experiment` command now requires an explicit model, effort, empty Host
output directory, and an OS isolation runner. The latter refuses to run outside
macOS `sandbox-exec`. Each Codex subprocess gets a temporary `HOME` and
`CODEX_HOME` containing links only to the local Codex auth and model-cache files,
plus an allowlisted environment (`PATH`, `HOME`, `CODEX_HOME`, `TMPDIR`, `LANG`).
The child runs with `--no-daemon`, `--ignore-user-config`, `--ephemeral`, and
the requested Codex sandbox mode. The public repo contains no MCP or Skill
configuration, and the command does not enable web search. Codex still has its
ordinary local code-reading/editing tools inside the allowed repo. Its outer
OS profile denies the original `/Users` tree,
unrelated `/private/tmp` locations, `/Volumes` and `/Network` mounts, the Host
private directory, sibling Arm repositories, and the Docker socket;
only the current public case repo is passed as `--cd`. The Host output directory
must reside under the denied original HOME.

The Host was macOS ARM with the developer HOME at `/Users/bruce`; the generated
case repos and private Fixture shadow live under separate paths in a new
`/private/tmp/acqe-isolated-*` directory. System runtime paths remain readable
to start Node/Codex and Python. The existing `/Volumes/Openscreen` mount is
denied to the child. Docker was available, but the cached Python container did
not contain the native Codex CLI, so the tested execution boundary is the
macOS OS sandbox rather than a claim of container isolation. Host variables
other than the five named above, including the Codex app tool pipe, are not
inherited. The temporary Codex home has no MCP configuration or Shared Context
material; the original `.ccc-mcp` path is denied by the OS profile. The outer
profile also denies localhost TCP and local Unix-socket connections, so a
child cannot use a local Shared Context service as a file-access bypass; external network needed
for the Codex API remains available.

The Host now runs visible and hidden pytest subprocesses under separate OS
profiles as well. Agent-authored code executed by Host pytest cannot read the
original Fixture, other Arm repos, or the Host private directory. Host test
profiles deny network access. Hidden tests and `own_good` probes run in disposable
shadow copies under `host-private`; pytest bytecode and probe files are deleted
with those copies, so judging B0 or B1 cannot leave private files in a repo that
B1/B2 can inspect. A sandboxed pytest infrastructure error fails the experiment
instead of becoming a false task verdict.

An actual no-model-call canary was run through the same macOS profile: the
public `mod.py` was readable, while the Host private canary by absolute path,
`../../host-private` traversal, a symlink from the public repo, the original
`tests/toybench.py`, `.ccc-mcp/CONTEXT.md`, and a sibling Arm's `mod.py` were all
unreadable. The same private and sibling paths were also unreadable through
the macOS `/System/Volumes/Data` APFS alias. Running the canary with an intentionally weak `(allow default)`
profile raised `OS isolation canary failed`. Codex `login status` inside the
restricted environment exited 0 and reported `Logged in using ChatGPT`.
Actual connection attempts from the sandbox to Host-created localhost TCP and
Unix-socket listeners were both denied.
Agent and sandboxed Host-test processes were also denied write access to the
directory holding future sandbox profiles.
Sandboxed visible pytest passed 2 tests; sandboxed hidden pytest failed its
expected buggy-case test; no hidden filename or bytecode remained in the public
repo. These are OS file access observations, not prompt-only claims.

This is a same-UID macOS sandbox, not an independent container or OS account.
The canaries cover the named file paths and local service routes; they do not
prove that every Mach IPC or process-memory path is unavailable. A broad
`process-info*` denial was tried but prevented the Python canary itself from
starting, so it was not retained. A stronger separate-user/container boundary
would be needed for a fully adversarial Agent threat model.

An independent data-isolation failure was found in the committed source:
`git show HEAD:tests/toybench.py` contains the exact Clamp `hidden`,
`own_good`, and `fixed` strings (and corresponding fields for the other
original toy tasks). The local `origin/main` tracking ref contains the same
three Clamp strings. A child that can retrieve repository history could learn
those answers regardless of its local filesystem sandbox. The formal
`review-experiment` CLI now parses the committed Fixture's literal values and
rejects any selected case whose would-be private test, confirmation probe, or
standard implementation is already in HEAD, before quota lookup or any Codex
call. The review-only `architecture_risk` case is absent from committed HEAD,
but it is not a substitute for a defect-bearing single-case comparison. A
fresh genuinely Host-private acceptance case is required for the intended
B0/B1/B2 comparison.

Codex CLI is `codex-cli 0.163.0` and is logged in. The local
`models_cache.json` fetched on 2026-11-10 lists `gpt-6.1-sol` with
`supported_in_api: true` and the `medium` reasoning level. However, the cache
reports client version `0.164.0`, different from the installed CLI. The `codex
exec --json` stream saved from the previous real Clamp smoke test contains no
served-model field. Thus model availability is locally advertised, but server
acceptance and served-model identity have **not** been independently verified in
this preflight. A single minimal probe would spend one Codex call and must be
separately authorized before use; no B0/B1/B2 call was made here. Even a
successful probe would prove the requested model was accepted, while
`observed_model` would remain null unless a trustworthy provider signal becomes
available. Do not silently substitute another model.

The proposed separate server-acceptance probe (not executed here) is one
`codex exec` call, read-only, within the same OS isolation, with a 60-second
timeout and no retry:

    .venv/bin/python - <<'PY'
    import importlib.util
    import sys
    from pathlib import Path
    sys.path.insert(0, str(Path('src').resolve()))
    spec = importlib.util.spec_from_file_location('acqe_run', 'benchmarks/toy-agents/run.py')
    run = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(run)
    with run.ACQEIsolation('gpt-6.1-sol', 'medium') as isolation:
        repo = run.make_repo(isolation.base, 'architecture_risk', 'model-probe')
        result = isolation.run_codex(repo, 'Reply exactly OK. Do not use tools.', 'read-only', timeout=60)
        print('exit:', result.returncode)
        print('usage:', run.usage_from_output('codex', result.stdout))
    PY

A successful exit shows that this account/CLI request was accepted by the
server; it still does not expose a trustworthy served-model identifier.

The existing controlled runner uses one Frozen Initial Output commit copied to
B0/B1/B2. Unit tests assert the same commit hash. B1/B2 use the same runner,
model, effort, review sandbox, visible tests, and one-review/one-fix maximum;
only B2's public-Issue-derived generic Review Lens differs. Finding confirmation
is recorded independently of `resolved`. Every Codex stage records time and
available token usage, with a 900-second per-call timeout and a hard maximum of
five calls for one case (one frozen implementation, two reviews, up to two
fixes). A failing call, invalid test verdict, or failed isolation canary stops
the experiment. Stage wall time includes some Host work around the call; it is
not a pure model latency measure.

The CLI now saves a `git bundle` of the public frozen repository, its commit
and SHA-256, hashes of the harness and Host fixture, and the prompt, raw Codex
stream, exit status, and wall time for every call in the Host-only output
directory. These let an auditor reconstruct each Arm's public starting code
and inputs independently. Model generation remains stochastic, so replay
means rerunning from the same frozen input and comparing outcomes, not
promising byte-identical responses.

**Go / No-Go: No-Go for the formal comparison at this preflight.** The intended
Clamp case is blocked by the committed-answer check. The actual served model
remains unobservable with this CLI output and has not been server-probed.
The same-UID sandbox also leaves process-memory/Mach isolation unproven.
Resolve the published Fixture problem, resolve or explicitly scope the OS
boundary, and accept the
`requested_unconfirmed` model-identity limitation, before authorizing the full
comparison.

Observed no-model-call gates from this preflight:

| Command | Exit | Result |
|---|---:|---|
| `CCC_MCP_HOME=/private/tmp/acqe-preflight-pytest-home-final-20261110 .venv/bin/python -m pytest -q` | 0 | 885 passed, 3 deselected, 3 Python 3.14 `forkpty` deprecation warnings; completed before the final committed-Fixture guard test was added |
| `.venv/bin/python -m pytest -q tests/test_toy_agents_run.py` | 0 | 46 passed, 2 deselected, including the committed-Fixture guard and original `direct,ccc` default test |
| `.venv/bin/python -m pytest -q tests/test_toy_agents_run.py -m real_agents -k os_isolation_canary_without_model_call` | 0 | 1 passed, 47 deselected; actual OS file, APFS alias, localhost TCP, Unix socket, profile-write, login and sandboxed pytest probes; **no model call** |
| `.venv/bin/ruff check .` | 0 | All checks passed |
| `git diff --check` | 0 | No whitespace errors |
| `.venv/bin/python benchmarks/toy-agents/run.py review-experiment clamp --out /Users/bruce/acqe-preflight-refuse-20261110 --model gpt-6.1-sol --effort medium --max-fix-attempts 1` | 1 (expected) | `Private Fixture for clamp has committed ['hidden', 'own_good', 'fixed'] in HEAD`; no output directory, quota lookup, or Agent call |

The default full suite excludes real-agent tests by marker. The final
committed-Fixture check was run in the later targeted suite rather than
claiming it was included in the 885-test full-suite run. No formal
B0/B1/B2 model experiment was run.

## 17. Phase 2 private Fixture and container evaluation boundary (2026-11-10)

This section records an engineering preflight, not a Benchmark result. The
published Clamp and original toy tasks remain Smoke Tests; the formal private
case set is new. `benchmarks/toy-agents/acqe_public_cases.py` contains only
four public issues, starting implementations, and visible tests. The Host-only
Fixture is stored outside every Git worktree at
`~/.local/share/ccc-mcp/acqe-phase2/private_fixture.json` (parent mode 0700,
file mode 0600). It contains the withheld acceptance tests, reference
implementations, confirmation probes, and defect labels. The loader rejects a
Fixture inside any Git repository, a symlink path, a file owned by another
user, permissive file modes, or a mismatched case schema. The Fixture is read
by the Host orchestrator only and is never copied into an Agent case repo,
prompt, environment variable, Docker image, or Agent log. The final run
manifest records its SHA-256, not its contents.
The current Host-only Fixture SHA-256 is
`5f909e18751ab5ea35f96f276f95719361024b6af9981d0e593bf468e8fcfb24`;
an evaluator must verify this hash and keep the file outside Git to reproduce
the same acceptance verdicts.

The public cases cover a boundary transition, concurrent state, transactional
data integrity, and a normal no-defect control. In networkless Host scorer
containers, all four visible suites passed. The withheld acceptance suites
failed on three starting implementations and passed on their Host-only
references; the normal control passed in both states. The concurrent starting
case failed 20/20 repeated Host runs and its reference passed 5/5. The actual
private assertions and labels are intentionally omitted from this document.

### 17.1 Execution boundary and threat model

The Host orchestrator loads the private Fixture and creates a fresh public
Git repo for each case. One official Codex 0.163.0 Linux package, pinned by
SHA-256 `582646c6bf0561eb0de540f7fc85b9121b0091a826bc6e96830b7f1722122182`,
was built into the local `ccc-acqe-codex:0.163.0` image. The image contains no
Fixture, original repository, or credentials. For each Agent call, Docker
mounts only the current public case repo at `/work`, with its `.git` metadata
mounted read-only. The implementation/fix mount is writable; B1/B2 reviews
get a read-only mount. The container root filesystem is read-only, capabilities
are dropped, no-new-privileges is set, memory/PID/CPU limits apply, and the
Docker socket is absent. Agent-authored Git hooks cannot modify the read-only
metadata; Host Git also disables hooks. Host cloning preserves symlinks
instead of following them into private paths.

The Agent joins a fresh internal Docker network with no direct Host or
internet route. A separate proxy container joins that internal network and
the outbound bridge, carries no credentials or Host-private mounts, and
accepts HTTPS CONNECT only to named OpenAI/ChatGPT endpoints on port 443
after excluding local/private resolved addresses. The default Docker bridge
was explicitly rejected as unsafe: a canary connected from it to a
Host-created localhost listener. On the internal network, direct external
traffic timed out and `host.docker.internal` did not resolve. The proxy
returned 403 for Host/loopback destinations and 200 for the allowlisted API
destinations. The Agent has no Shared Context configuration or access to the
original repository history.

After each Agent call exits and the frozen implementation is committed, the
Host runs visible tests and withheld acceptance in a separate, networkless,
read-only Python scorer container. Withheld tests live only in a disposable
shadow copy under the Host-private directory; the scorer never receives the
full private Fixture JSON. Its output is reduced to a Host verdict and is not
fed into Agent prompts. A Host test infrastructure error fails closed. Real
file canaries confirmed that the Agent could read its public module but not
the Host-private canary, external private Fixture, original `.git`, or a
symlink to the Host-private canary. Write canaries confirmed the two mount
modes and read-only `.git`. Forced per-call and whole-Case timeouts removed
the active Agent container; context exit removes the proxy and network.

The container's Codex CLI necessarily receives read-only auth and model-cache
mounts to contact the model service. These are the only nonpublic Agent mounts;
they do not contain Benchmark answers. A malicious Agent tool could still
attempt to read those credentials, so credential secrecy is a separate
security limitation of this CLI architecture. The success claim here is
isolation of private Benchmark answers and Host scoring, not protection of
the transport credential from arbitrary model-generated code.

### 17.2 Model and budget

One read-only, no-retry model compatibility probe was executed inside the
isolated container with a 60-second call timeout. It exited 0 after 12.84
seconds and the native CLI reported 14,115 input tokens, 12,288 cached input
tokens, and 5 output tokens. This confirms that this account/CLI accepted the
requested `gpt-6.1-sol` and `medium` parameters for that call. The raw Codex
JSON events still carry no independently observed served-model field, so
`observed_model` remains null and the status is `requested_unconfirmed`.
The local model cache client version remains 0.164.0 while both Host and
container CLIs are 0.163.0; the successful probe establishes compatibility
for this request, not a general guarantee of future availability. No second
model probe and no B0/B1/B2 comparison were run.

One private Case defaults to a hard 1,800-second wall-time deadline, including
Agent calls and Host scoring, implemented with a process alarm plus remaining
time passed to each blocking container call. At most five model calls are
possible: one frozen implementation, one B1 and one B2 review, and at most
one fix per review Arm. Every model call has its own 900-second maximum, and
the runner refuses a sixth call. On timeout it force-removes the Agent
container and cleans up the proxy/network; call records retain elapsed time,
exit status, input/output/cached-input tokens, and raw CLI events. B0/B1/B2
share the same Frozen Initial Output commit. Token use is measured, not capped
by a CLI token-budget flag.

### 17.3 Verification and current decision

The private Finding probe confirmed one specifically evidenced Finding,
left an unrelated evidenced Finding Supported, and kept final task success
separate (`resolved=false`). Private-case B0/B1/B2 mock runs used one Frozen
commit for all Arms, kept B1/B2 read-only, did not copy withheld assertions
into prompts, and did not force a finding or fix on the normal control.

The private answer-isolation and Host-scoring checks passed under the stated
answer-leak threat model. Overall Phase 2 acceptance is **No-Go** against
the strict public-only Agent environment and observed-model requirements:
the transport credential is readable by Agent tools, and the served model
identity is not independently observable. A credential broker or isolated
execution account and a trustworthy served-model attestation are needed before
formal comparison. The full formal comparison remains **not run**. The
prepared single-case command,
which was **not** executed here, is:

    .venv/bin/python benchmarks/toy-agents/run.py review-experiment offset_bucket --private-fixture /Users/bruce/.local/share/ccc-mcp/acqe-phase2/private_fixture.json --out /Users/bruce/acqe-phase2-offset-b012-20261110 --model gpt-6.1-sol --effort medium --max-fix-attempts 1 --max-case-seconds 1800

The output directory must be new or empty and remain under the Host HOME.
This command can make at most five model calls and two total fix attempts,
with a 900-second per-call and 1,800-second complete-Case deadline. Raw CLI
usage and cached-input tokens are saved per call. No hard token ceiling is
available in this CLI path. Final regression counts are recorded below after
the final test run.

Final verification on 2026-11-10 (all exit code 0):

- `CCC_MCP_HOME=/private/tmp/acqe-phase2-pytest-final-20261110 .venv/bin/python -m pytest -q`: 891 passed, 5 deselected, 3 forkpty deprecation warnings, 536.34 seconds.
- `.venv/bin/python -m pytest -q tests/test_toy_agents_run.py tests/test_acqe_private_phase2.py`: 51 passed, 4 deselected.
- `ACQE_PRIVATE_FIXTURE=/Users/bruce/.local/share/ccc-mcp/acqe-phase2/private_fixture.json .venv/bin/python -m pytest -q -m real_agents tests/test_acqe_private_phase2.py`: 2 passed, 5 deselected; no model calls.
- `.venv/bin/ruff check .` and `git diff --check`: passed.

## 18. Claude continuation — bounded credential-scope mitigation for P0 (2026-11-10)

This section records a Codex → Claude handoff continuing §17's Phase 2 work, not a
new Benchmark result. It addresses the §17.3 No-Go reason "the transport credential
is readable by Agent tools" with a minimal-increment mitigation, and is explicit
about what it does **not** fix.

### 18.1 Recovered state

Git status/diff were checked first; all of Codex's uncommitted ACQE work (`run.py`,
`acqe_docker.py`, `acqe_public_cases.py`, `docker/`, `tests/test_acqe_private_phase2.py`,
`tests/test_toy_agents_run.py`, `smoke-test-evidence/`, this document) was intact and
untouched. The Host-only Fixture at `~/.local/share/ccc-mcp/acqe-phase2/private_fixture.json`
still hashes to the SHA-256 recorded in §17 (`5f909e18751ab5ea35f96f276f95719361024b6af9981d0e593bf468e8fcfb24`),
confirming no tampering. No unrelated UI/onboarding edits were touched. The ccc-mcp
MCP tunnel was not reachable from this session either; git history, the working
tree, and this document (not a richer Shared Context) were the actual recovery
path — see §18.6.

### 18.2 What P0 actually is

`ACQEDockerIsolation._agent_argv` (before this change) bind-mounted the Host's real
`~/.codex/auth.json` read-only into every Agent container at
`/home/acqe/.codex/auth.json`. That file is a ChatGPT-login OAuth bundle:
`tokens.id_token`, `tokens.access_token`, `tokens.refresh_token`, `tokens.account_id`.
Inspection of this Host's own file (values never printed or persisted) showed the
`access_token` JWT carries `scp: [openid, profile, email, offline_access,
api.connectors.read, api.connectors.invoke]` and an `exp` roughly 8 days out from
mint time; `refresh_token` has no expiry and can mint new access tokens
indefinitely until the account owner revokes it. Because the Agent container runs
model-directed code as root with `--dangerously-bypass-approvals-and-sandbox`, any
tool call the model makes could `cat` this file in full. The egress proxy
(`docker/egress_proxy.py`) is a blind `CONNECT` byte-forwarder with no TLS
termination, so it cannot inject a credential on the Agent's behalf — the
container's own Codex CLI process must hold *some* bearer material to authenticate
its own outbound request.

### 18.3 What changed

`ACQEDockerIsolation` (`benchmarks/toy-agents/acqe_docker.py`) no longer bind-mounts
`self.auth` (the real file) into any container, for any invocation, including the
canary and `codex login status` calls. A new `_scoped_auth_file()` context manager
reads the real file on the Host, writes a 0600 copy under the run's private root
with `tokens.refresh_token` replaced by the sentinel `revoked-for-isolated-agent-
container`, mounts that copy instead, and deletes it in `finally` as soon as the one
container invocation that used it exits. `_run_agent_container` now wraps every
`docker run` in this context manager, so the invariant is: **the Host's real
`auth.json` is read by this module's own process only; no container, ever, sees a
mount whose source is that path.** `_canary()` gained a fifth probe that reads the
mounted file from inside the container and asserts `tokens.refresh_token` equals
the sentinel — not the real value, which this probe never has in the first place —
failing closed (refusing the Agent call) otherwise. `run_codex()`'s per-call record
and `permissions` now carry a `credential_scope`/`credential_mount` field so every
Case's saved evidence states this explicitly instead of staying silent on it.
`models_cache.json` (model slugs/efforts, not a credential) is unchanged.

This is a **minimal increment**, not a new proxy platform: no new network service,
no new process, no change to the egress allowlist or the container's network
topology. It changes only what one `--mount` source resolves to.

The scope here is the Docker Agent-container boundary (`acqe_docker.py`), matching
this task's own wording ("Agent 容器內的工具"). The older macOS `sandbox-exec` path
(`ACQEIsolation` in `run.py`, §16) symlinks the same real `auth.json` directly into
its temporary HOME and shares the identical exposure pattern, but it is left
unchanged here: it is only reachable from `review-experiment` without
`--private-fixture`, and every one of today's public `PUBLIC_CASES`/`REVIEW_TASKS`
still has its answer committed in `HEAD`, so the existing committed-Fixture guard
(§16) refuses it before any Agent call regardless. It cannot currently run a
genuinely private comparison, so it carries no present P0 risk worth the churn of
touching an already-verified preflight path; it would need the same treatment if it
is ever wired to a private Fixture.

### 18.4 What this does **not** fix (explicit limitation)

A root process inside the Agent container can still read the scoped file's
`tokens.access_token` — the still-valid, still-scoped (`profile`, `email`, ChatGPT
Plus `connectors.read`/`invoke`) credential Codex CLI needs to make that one call.
That access token's claims are inside a signed JWT and cannot be redacted without
invalidating the token itself. If a container leaked it (e.g. by having the model
echo file contents into a comment that lands in the frozen commit, which the Host
then stores in `run_envelope`/call records on disk), whoever held it could call the
ChatGPT backend as this account for up to the token's remaining lifetime —
materially less than before (bounded, non-renewable, time-boxed to the access
token's own `exp`) but **not zero**, and not what "Agent 執行的工具命令無法取得原始憑證"
asks for literally.

Full elimination would mean the container never holds any bearer material at all —
a trusted control endpoint (the Host) terminates the model connection and the
isolated execution endpoint (the container) only runs tools, never talks to OpenAI
directly. Two paths toward that were considered and explicitly **not** implemented
here:

- **A credential-injecting TLS-terminating egress proxy.** The current proxy is a
  blind `CONNECT` tunnel; injecting an `Authorization` header would require MITM'ing
  Codex's TLS connection to `api.openai.com`/`chatgpt.com` — a custom CA the
  container's HTTP client would need to trust, reconstructing HTTP/1.1 or HTTP/2
  framing without breaking streaming responses, and doing this against an
  undocumented internal API. This is exactly the "大型通用代理平台" this task said not to
  build, and a bug in it is riskier than the problem it solves (it could corrupt the
  account's OAuth state or break in ways harder to detect than a bounded file leak).
- **`codex exec-server forward --use-agent-identity-auth` with `CODEX_ACCESS_TOKEN`.**
  `codex-cli 0.163.0 --help` documents an `[EXPERIMENTAL]` remote-environment
  mechanism (Noise-transport WebSocket registration, "Agent Identity" auth distinct
  from the primary ChatGPT OAuth material) that looks architecturally like exactly
  the control/execution split this task asks to evaluate. It is undocumented beyond
  its `--help` text, appears to target Codex Cloud/AWS-style remote-environment
  registration, and could not be set up or verified against this local account in
  this session. This is the concrete next thing to investigate, not a claim that it
  works.

Per this task's own instruction — "如果 CLI 不支援必要的權限邊界，請清楚回報限制" — this
limitation is reported rather than papered over with an untested or hand-rolled TLS
broker.

### 18.5 Verification evidence

| Check | Command | Result |
|---|---|---|
| New unit tests (no Docker) | `.venv/bin/python -m pytest -q tests/test_acqe_private_phase2.py -k "scoped_auth or agent_argv"` | 2 passed |
| Targeted ACQE suite | `.venv/bin/python -m pytest -q tests/test_toy_agents_run.py tests/test_acqe_private_phase2.py` | 53 passed, 4 deselected |
| Real Docker credential-isolation canary (no model call) | `ACQE_PRIVATE_FIXTURE=.../private_fixture.json .venv/bin/python -m pytest -q -m real_agents tests/test_acqe_private_phase2.py -k canaries_and_host_verdicts` | 1 passed, 8 deselected, 61.8s |
| Ruff | `.venv/bin/ruff check .` | All checks passed |
| Whitespace | `git diff --check` | clean |
| Full suite | `.venv/bin/python -m pytest -q` (isolated `CCC_MCP_HOME`) | see result recorded where this run completes |

The real-Docker canary run exercised the actual `ccc-acqe-codex:0.163.0` image and
this Host's real, logged-in Codex credential: the container received the scoped
file, `codex login status` still reported `Logged in` against the access-token-only
copy, and the new credential-scope probe observed the sentinel, not the real
refresh token, from inside the container — the same real auth file was never passed
as a mount source. No model/API call was made by this verification.

### 18.6 P1 (model verification) and Codex→Claude handoff observations

P1 is an already-settled product decision (§15.6/§16/§17.2): `requested_unconfirmed`
stays the documented status, every Arm in a comparison still requests the same
explicit `model`/`effort`, and no claim of independently-verified served-model
identity is made anywhere in this codebase. Nothing here changes that; it is listed
in the Phase 2 verification checklist for completeness, not because it needed new
work.

On the handoff itself: the ccc-mcp tunnel was unreachable, so Shared Context
supplied nothing for this continuation (consistent with §15.1's prior observation).
Git status/diff plus this document were sufficient to resume exactly where Codex
left off without re-deriving its analysis; the open P0/P1 items and the exact
mount lines responsible for P0 were locatable directly from `acqe_docker.py` and
§17.3's own wording. No ACQE component needed to be regenerated.

### 18.7 Phase 2 Go/No-Go (unchanged recommendation)

**Still No-Go** for the full exploratory B0/B1/B2 comparison, on the same P0 ground
§17.3 named, now narrowed: the credential exposed to an Agent container is bounded
(single scoped access token, non-renewable, deleted immediately after each
container call, time-boxed to that token's own expiry) rather than the full
standing account credential, but a leak of that bounded token is still a real
account-identifying credential exposure, not nothing. Treat this as risk
*reduction*, not resolution. Authorizing the prepared single-case command from
§17.3 is a decision for the account owner, who should weigh the residual exposure
described in §18.4 before spending a real model call on it. P1's
`requested_unconfirmed` limitation stands unchanged and must still be carried into
any report of results.

## 19. Credential-isolation feasibility re-check: Option A vs. Option B (2026-11-11)

Final Phase 2 feasibility pass on the §18.4 limitation, requested after the
§18 mitigation and flaky-test recheck were accepted. This is analysis only — no
code was changed, no Codex/model call was made, and the §18 bounded mitigation
(scoped per-call access token, no refresh token, deleted after each call) stays
as the only implemented credential control. Every claim below comes from this
Host's actual `codex-cli 0.163.0` (`codex doctor`, `codex features list`,
`codex <subcommand> --help`, `~/.codex/config.toml`), not from outside
documentation, and no value from `auth.json` was read for this section beyond
what §18.2 already recorded.

### 19.1 Option A — Trusted Controller + Isolated Tool Executor

The CLI does expose pieces that look like this split:

| Candidate | `codex features list` status | Notes |
|---|---|---|
| `remote_control` (the `codex remote-control` / `exec-server forward --use-agent-identity-auth` path examined in §15.5/§16) | **`removed`, enabled=false** | The subcommand still answers `--help`, but the feature itself is marked removed in this build. |
| `use_agent_identity` | **`under development`, enabled=false** | `codex doctor` independently confirms: `stored agent identity: false`. No Agent Identity credential exists to register with in the first place. |
| `code_mode` (lets the model run code instead of discrete tool calls) | **`under development`, enabled=false** | |
| `code_mode_host` (`codex app-server --code-mode-host <URL>`: "Connect to a remote code-mode host instead of starting a local host") | `stable`, enabled=true | The one candidate piece that is actually shipped. But using it for our split would mean replacing the harness's single-shot `codex exec --json` subprocess model with a persistent `codex app-server` daemon talking out to a remote code-execution host — a different session architecture, unverified for this purpose, and exactly the "直接新增大量程式碼" this round was told not to do. |

**Finding:** every mechanism that would let the model-talking, credentialed
process live somewhere other than the tool-executing process is either
`removed` or `under development` in this CLI's own feature registry, or (for
`code_mode_host`) would require a substantial, unverified architecture change
to use for this purpose. **Option A is not implementable today without relying
on a feature the vendor itself has not shipped or has withdrawn.**

### 19.2 Option B — Credential Broker (Agent holds neither access nor refresh token)

For a broker to work without a credential ever touching the container, Codex
CLI's HTTP client would need to send its request to an endpoint *we* control
(so no TLS MITM of OpenAI's own certificate is needed) while still being told,
somehow, to attach a real bearer token on our side. Two ways to get there, both
checked directly:

- **Point a custom `model_providers.*.base_url` at our own broker.** `codex
  exec --help` documents `--ignore-user-config`: *"Do not load
  `$CODEX_HOME/config.toml`; auth still uses `CODEX_HOME`."* That line, from
  the CLI's own help text, says authentication is bound to `CODEX_HOME`
  independently of whatever `model_providers`/`base_url` config is or isn't
  loaded — i.e. a custom provider is not how `chatgpt`-mode auth gets
  attached to a request. Nothing in `~/.codex/config.toml` defines a
  `[model_providers.*]` override already proving otherwise, and testing this
  live (pointing a provider at an unreachable port) was **not attempted**:
  there is no safe way to be certain a misconfigured override wouldn't instead
  silently fall through to the real backend and spend a real call, and this
  round was told not to spend Codex quota. The CLI's own help text is treated
  as sufficient evidence without taking that risk.
- **A credential-helper/IPC hook** (the `ssh-agent` pattern: the real secret
  never leaves a trusted process; the client asks it to act, not to hand over
  the key). `codex login --help` only exposes one-time *setup* flows
  (`--with-api-key`, `--with-access-token`, `--device-auth`) that populate
  `auth.json`; none of them describe a per-request delegation hook. No such
  mechanism is documented.

**Finding:** there is no CLI-documented way to give Codex a request destination
or a credential source that keeps a real bearer value out of the file the
container's own `codex` process reads to authenticate. The only way to get
there would be a TLS-terminating proxy that impersonates `api.openai.com` to
the container — the "大型通用代理平台" this task and §18.4 both ruled out, and a
meaningfully larger, less-tested piece of infrastructure than the blind
`CONNECT` tunnel already in place. **Option B is not implementable as a small,
maintainable addition today.**

### 19.3 Supporting signal from the vendor's own feature registry

Two more entries in `codex features list` are directly on point:

- `credential_masking` — **`under development`, enabled=false**. OpenAI's own
  CLI has a feature by this exact name, not yet shipped. This is the vendor
  already working on some version of the problem this task is asking us to
  solve ourselves; building a parallel mechanism now would be duplicating
  unreleased vendor work with far less visibility into Codex's actual wire
  protocol than the vendor has.
- `secret_auth_storage` — **`stable`, enabled=false** (shipped, but off by
  default on this install). Name suggests it may change *how* `auth.json` is
  stored/read (e.g. an OS keychain rather than a plain file), which could be
  relevant to Option B in a future pass. **Not investigated further this
  round** — enabling and probing it is new scope, not something to fold into
  this feasibility check, and it is listed here only as a concrete, low-risk
  next question rather than a claim that it helps.

### 19.4 Complexity / maintenance / security boundary / testability

| | Option A (control/executor split) | Option B (credential broker) |
|---|---|---|
| Implementation complexity | High — needs a persistent `app-server` + remote code-mode host architecture this harness doesn't use today | High — needs a protocol-compatible reverse proxy for an undocumented backend wire format (streaming included), not a byte-blind tunnel |
| Maintenance cost | Unknown/high — built on `under development`/`removed` vendor features that can change or vanish without notice | High — breaks silently on any upstream Codex/backend protocol change; no published contract to test against |
| Security boundary if it worked | Strong (credential genuinely never reaches the container) | Strong (credential genuinely never reaches the container) |
| Testability today | None — `use_agent_identity` has no credential to register (`stored agent identity: false`); `remote_control` is a removed feature | None without either live backend traffic (spends quota, forbidden this round) or reverse-engineering an undocumented protocol |

### 19.5 Go / No-Go and recommendation

**No-Go for both Option A and Option B as new implementation work right now.**
Neither is a small, maintainable, verifiable change; both would mean either
betting on vendor features explicitly marked not-ready, or hand-building the
kind of broad proxy/daemon infrastructure this task and §18.4 both said to
avoid. Building either now would not satisfy "可維護的最小方案" — both are the
opposite of minimal once you look past the one-line `--help` description.

**Recommendation — keep §18's bounded mitigation as the standing control, and
treat full elimination as externally blocked, not internally unsolved:**

1. Re-check `codex features list` (specifically `credential_masking`,
   `use_agent_identity`, `remote_control`) on each Codex CLI upgrade. The
   moment any of these moves to `stable`, that is the trigger to re-open this
   section — not a fixed calendar date.
2. Low-risk, no-new-infrastructure next experiment if this needs re-examining
   sooner: inspect what `secret_auth_storage` actually changes (source only;
   no live `-c` override attempts that could silently reach the real backend)
   and report back, before deciding whether it is worth a feasibility pass of
   its own.
3. Do **not** expand Phase 2 further to chase full elimination. The
   exploratory B0/B1/B2 comparison remains gated on the account owner
   explicitly accepting the residual, bounded exposure described in §18.4 —
   that is a risk-acceptance decision, not an engineering one, and it is
   theirs to make, not this harness's to re-litigate every round.

## 20. Formal decision on §19, and the ACQE Review-only Controlled Experiment plan (2026-11-11)

### 20.1 Formal decision (accepted, recorded verbatim)

The full private-Fixture, Agent-writes-code Benchmark (§17's Docker path, and
any equivalent) is marked **`BLOCKED_BY_EXECUTION_ARCHITECTURE`**: blocked by
§19's finding that Codex CLI has no shipped mechanism to keep a model
credential out of a container that executes Agent-directed tool calls, not by
anything this project chose not to build. Consequences:

1. The §18 scoped-auth mitigation, the Docker sandbox (`acqe_docker.py`,
   `docker/egress_proxy.py`), and the credential-broker idea are **frozen as
   they are** — no further extension of any of them.
2. Every existing Benchmark artifact, Fixture, test, and piece of evidence
   (§11–§19, `tests/test_toy_agents_run.py`, `tests/test_acqe_private_phase2.py`,
   `smoke-test-evidence/`, the Host-only private Fixture) is kept exactly as
   is.
3. No full Agent Benchmark run happens while credential isolation is
   unresolved at the architecture level.
4. Nothing here touches `core/`'s real multi-role workflow engine
   (`flow_engine.py`, `flows.py`, `tasks.py`) — this section is Benchmark-only,
   as every prior ACQE section has been.

This section proposes a smaller, separate experiment that sidesteps the
blocked architecture rather than waiting on it.

### 20.2 Why a review-only design sidesteps §19 instead of re-opening it

§19's problem is specific: an Agent that can **write files and run arbitrary
shell** needs an isolation boundary, because it could embed or exfiltrate a
credential into something that boundary doesn't catch. A review-only task that
*never gets write access and never gets a fix round* does not have that
problem in the first place — not because credential isolation got solved, but
because there is no isolated, adversarial *write*-capable execution step left
to isolate. The model only ever sees a short public issue description, a short
buggy function, and the Host's own visible-test output, and only ever answers
in prose. Running that single read-only call directly through the account
owner's own already-logged-in Codex CLI is the same trust level as any other
everyday use of that CLI — it is not a new credential exposure, and §18/§19 do
not apply to it.

### 20.3 What already exists and needs *zero* changes

Reading `benchmarks/toy-agents/run.py`'s existing B1/B2 machinery end to end
confirms nearly all of it is reusable as-is:

| Piece | Role | Change needed |
|---|---|---|
| `REVIEW_TASKS` / `TASKS` / `REVIEW_ONLY_TASKS` / `ISSUES` (toybench.py-backed) | Case data: `buggy`, `visible`, `hidden`, `own_good`, `defect_label`, `confirmation_witness`/`confirmation_claim_terms`, `fixed` | none |
| `make_repo()` | Builds the public, agent-visible repo (issue + buggy `mod.py` + visible test), one commit | none |
| `general_review_prompt()` (B1) / `contextual_review_prompt()` + `select_review_lens()`/`REVIEW_LENSES` (B2) | The only thing that differs between the two arms | none |
| `run_review_arm()` | Clones the frozen repo, runs the host's own visible pytest, builds the diff-based prompt, makes **one read-only** `isolation.run_codex(...)` call, parses findings, host-verifies them, optionally fixes (gated by `max_fix_attempts`) | none — call with `max_fix_attempts=0` |
| `extract_agent_text()` / `parse_findings()` (`core/flows.py`) | Turns the raw `--json` event stream (or an equivalent stdout string) into `(severity, claim, evidence)` triples | none |
| `host_verify_findings()` | Confirms a Finding only against its own fixture-owned host probe; never a blanket upgrade | none |
| `host_test_in_shadow()` / `pytest()` / `case_from()` / `run_envelope()` / `usage_from_output()` | Host-side scoring, token accounting, and the public case-record shape | none |

`run_review_arm` does not care *how* `isolation.run_codex(repo, prompt,
sandbox)` was implemented — it only needs something with that method
returning a `codex --json`-shaped (or equivalent) `CompletedProcess`. That is
the one seam this plan uses.

### 20.4 What is new, and how small it is

**(a) Skip the model-generated "frozen implementation" call entirely.**
`PUBLIC_CASES`/`REVIEW_TASKS[name]["buggy"]` is already a fixed, pre-written
buggy implementation — nothing about B1-vs-B2 review quality requires that the
code under review was itself produced by a Codex call; `run_frozen_implementation`
exists only so B0/B1/B2 start from one *agent-authored* attempt, which this
smaller experiment does not need. A ~10-line helper builds the two-commit repo
`run_review_arm` expects (`git diff HEAD~1 HEAD` showing the buggy code as "the
diff") directly from `buggy`, with **no model call**:

```
def build_frozen_review_fixture(base, name, label):
    repo = Path(base) / f"{name}-{label}"
    (repo / "tests").mkdir(parents=True)
    (repo / "conftest.py").write_text("import sys\nsys.path.insert(0, '.')\n")
    (repo / "tests" / "test_visible.py").write_text(REVIEW_TASKS[name]["visible"])
    git(repo, "init", "-q"); git(repo, "add", "-A"); git(repo, "commit", "-q", "-m", "skeleton")
    (repo / "mod.py").write_text(REVIEW_TASKS[name]["buggy"])
    git(repo, "add", "-A"); git(repo, "commit", "-q", "-m", "fixture implementation")
    return repo
```

**(b) A trusted-host runner — no Docker, no macOS sandbox-exec.** Because the
only remaining call is one read-only review, the runner can be a ~15-line
class whose `run_codex(repo, prompt, sandbox)` shells straight out to:

```
codex exec --no-daemon --ignore-user-config --ephemeral --skip-git-repo-check \
  --json --model <model> -c model_reasoning_effort=<effort> \
  --sandbox read-only --cd <repo> -
```

directly on the Host, using `~/.codex/auth.json` exactly as the account owner
always has it — no copy, no scoping, no container, because this is not an
adversarial boundary. Codex's own built-in sandbox (not bypassed — no
`--dangerously-bypass-approvals-and-sandbox` this time) still restricts writes
to `--cd`'s directory. This reuses an existing, vendor-shipped, stable
mechanism instead of adding any infrastructure.

**(c) A manual-dialogue runner of about the same size**, for when the account
owner would rather not run even a trusted local call: it writes the exact
prompt `run_review_arm` built to a file, waits for the human to paste the
model's full reply back in, and wraps that text into the same
`item.completed`/`agent_message` JSONL shape `extract_agent_text` already
parses — so `run_review_arm` needs no awareness of which runner it got. It
also prompts for, and records, whatever model name/version/effort the human
selected in that external chat. `usage_from_output` naturally reports
`available: False` for this path (there is no `turn.completed` token-usage
event to parse), which is exactly the honest signal to keep: no invented
token/usage numbers for a call the harness didn't make itself.

**(d) A ~10-line orchestrator** calling `run_review_arm(..., "b1",
general_review_prompt, ...)` and `run_review_arm(..., "b2",
contextual_review_prompt, ...)` against the same frozen fixture from (a), both
with `max_fix_attempts=0` — which (per `run_review_arm`'s own code,
`if blocking and max_fix_attempts > 0:`) guarantees **no `workspace-write`
call is ever made by either arm**. Every model call in this design is
read-only, by construction, not by sandboxing.

Total new code: roughly 50–60 lines across (a)–(d); zero changes to any
existing, already-tested function.

### 20.5 Test plan

1. Unit-test the new frozen-fixture helper: two commits, `git diff HEAD~1 HEAD`
   equals `buggy`'s text, no model call made (same style as the existing
   `test_clone_preserves_symlinks_instead_of_copying_host_targets`-type checks).
2. Unit-test both runners with a fake/stubbed `codex` call (same pattern as
   the existing `FakeIsolation` in `test_private_cases_mocked_b0_b1_b2_use_one_frozen_input`):
   assert the trusted-host runner's argv never includes
   `--dangerously-bypass-approvals-and-sandbox` or `workspace-write`; assert
   the manual runner's synthetic stdout round-trips through
   `extract_agent_text`/`parse_findings` correctly.
3. Run the orchestrator against one real case with a known defect
   (e.g. `average` or `dedupe` from the existing public `TASKS` — not the
   Phase 2 private-Fixture cases, so this pilot needs no `--private-fixture`,
   no Host-private directory, and no `_ACTIVE_ISOLATION` wiring at all) and
   confirm: exactly 2 real Codex calls total (one B1, one B2), both
   `read-only`, `fix_attempted: false` on both, and `host_verify_findings`
   produces the same `confirmed`/`supported`/`hypothesis` grammar already
   tested in §17/§18.
4. Record, per arm: verified Findings (with their `verification_status`),
   false positives (a `confirmed`/`supported` Finding whose claim does not
   match the fixture's actual `defect_label`), false negatives (the fixture's
   real defect present in neither arm's Findings), elapsed seconds, and
   available tokens (`None` when the manual path was used).
5. Repeat on a `defect_label: "none"` control case (already supported by
   `REVIEW_ONLY_TASKS`'s `architecture_risk`, per §15.4) to see whether either
   arm invents an unsupported "defect" where none exists.

### 20.6 Explicit scope limit (carried into every reported result)

Every result from this design is reported as **"B1 vs. B2 review-only
Finding quality on one fixed, human-authored buggy function"**, never as
evidence about ccc-mcp's real multi-role workflow (`core/flow_engine.py`'s
SA→SD→RD→REVIEW gates, round-bounded bounce-back, PM escalation). It says
nothing about fix success, role composition, or end-to-end task resolution —
only about one review prompt's Finding quality against one known defect per
case.

### 20.7 Answers to the five questions asked

1. **能否在不讓 Agent 取得模型認證的前提下進行審查實驗？** Yes — by construction,
   not by a new isolation mechanism. Dropping the implementation-generation
   call and fixing `max_fix_attempts=0` means every remaining call is a single
   read-only review that the account owner's own trusted CLI (or the owner
   themselves, manually) can make directly. There is no adversarial,
   write-capable execution step left for a credential to leak through.
2. **現有 B1/B2 哪些程式可直接重用？** Essentially all of it — see the table in
   §20.3: `REVIEW_TASKS`, `make_repo`, both prompt functions and the review
   lens table, `run_review_arm`, `extract_agent_text`/`parse_findings`,
   `host_verify_findings`, `host_test_in_shadow`/`pytest`, `case_from`/
   `run_envelope`/`usage_from_output`. None of it needs to change.
3. **需要多少修改？** About 50–60 new lines in four small pieces (§20.4a–d);
   zero lines changed in any existing, already-tested function; no new image,
   proxy, network boundary, or daemon.
4. **能否先用一個案例取得有效的初步結果？** Yes — one case with a real defect
   costs exactly 2 real Codex calls (one B1, one B2), cheaper than the
   existing B0/B1/B2's minimum of 3, and needs no Docker/sandbox setup at all
   for that first run.
5. **有哪些限制會影響研究結論？**
   - The fixture's pre-written `buggy` code is a human-authored bug, not
     another model's generation — this measures review-prompt quality against
     a known defect, not an agent's idiosyncratic mistakes, narrowing the
     original B0/B1/B2 intent (§14) rather than replacing it.
   - A single case (or small N) cannot generalize; `evaluation_engine`'s own
     "more samples needed" caution applies with the same force here.
   - If the manual-dialogue runner is used: `requested_model`/`effort` become
     self-reported human input, not harness-enforced or independently
     verified, and no token/usage accounting exists for that call — this must
     be disclosed with every such result, the same way P1's
     `requested_unconfirmed` already is disclosed for Codex's own served-model
     identity.
   - The trusted-host CLI path keeps Codex's own built-in sandbox active, but
     that sandbox's actual read-access policy on this Host has never been
     canary-tested the way `ACQEDockerIsolation`/`ACQEIsolation` were — a
     reasonable, low residual risk for one read-only call by the account
     owner, but not a proven one.
   - Per §20.6, none of this may be reported as a capability claim about the
     full multi-role ccc-mcp workflow.

No code was written this round (§20.4's snippets are plan illustrations, not
committed files), no Codex/model call was made, and no file outside this
document changed.

## 21. First real Review-only pilot result and a scoring-fairness audit (2026-11-11)

The §20 plan was executed for real: `offset_bucket`'s `build_prompts.py`/
`score_review.py` were written and saved under
`benchmarks/toy-agents/review-only-pilot/offset_bucket/` (a durable, non-Git-tracked
repo folder, not Claude's scratchpad), B1/B2 were pasted into two separate manual
ChatGPT conversations with no local tools, and the real replies were saved as
`b1_response.txt`/`b2_response.txt`. First real, non-mock host-verified result:
B1 2 findings (0 confirmed, 2 supported), B2 3 findings (1 confirmed, 2 supported).

Both arms independently found the exact same off-by-one boundary defect
(`bucket_for_offset(10, 10)` returns `0` instead of `1`, because the loop uses
`while offset > boundary` instead of `>=`) and the same unbounded-linear-scan
performance concern; B2 additionally raised a `float('inf')` non-termination
claim that the Fixture's own confirmation vocabulary does not recognize as the
intended defect, and whose standing against the public interface (`"nonnegative
offset"` — silent on finite-vs-infinite) is itself debatable, not a clear
confirmed-vs-missed call either way.

A read-only audit (no file changed, no model call, booleans printed only, the
real confirmation witness and claim_terms never printed) traced the B1-vs-B2
score gap to `host_verify_findings()`'s candidate check requiring the Fixture's
witness string inside the finding's *evidence* half only (`witness in evidence`,
where `evidence` is whatever `parse_findings()` put after the literal word
"evidence:"). B1 phrased its concrete example (`bucket_for_offset(10, 10)` etc.)
*before* "evidence:" (in the claim half); B2's otherwise structurally identical
finding put the same example *after* "evidence:". Confirmed with real code
execution: B1's boundary finding had `claim_term_hit=True` but
`witness_in_evidence=False`; B2's had both `True`. Severity tag (`major` vs
`assumption`) played no role — the candidate check never reads severity.

**Conclusion carried into §22: this was a scorer artifact, not a review-quality
signal.** Comparing by confirmed-count alone would have wrongly suggested B2 is
categorically better; comparing by defect type shows both arms found the same
two substantive issues with comparable precision. No claim about B1-vs-B2
review strategy is supportable from this one case regardless, N=1 either way.

## 22. Minimal fix for the §21 scoring-fairness bug, and a clean re-score (2026-11-11)

### 22.1 The fix

One file changed: `benchmarks/toy-agents/run.py`, `host_verify_findings()`'s
candidate check. Before:

```python
if witness and evidence and witness in evidence
```

After:

```python
if witness and evidence and witness in f"{claim} {evidence}"
```

Nothing else in the function changed: the `claim_terms` relevance check (matched
against `claim` only), the `len(candidates) == 1` ambiguity-stays-Supported rule,
the host-owned `own_good` probe-vs-control confirmation mechanism, and the
`confirmed`/`supported`/`hypothesis` categorization are all untouched. The fix
is generic — it reads `witness`/`claim_terms` from whichever case's `spec` is
passed in, the same as before; no case name (`offset_bucket` or otherwise) is
named anywhere in the new line.

### 22.2 Before / after on the real, unmodified B1/B2 replies

| | B1 (general) | B2 (contextual) |
|---|---|---|
| Before fix | boundary: **supported**; performance: supported (0 confirmed) | boundary: **confirmed**; performance: supported; float-inf: supported (1 confirmed) |
| After fix | boundary: **confirmed**; performance: supported (1 confirmed) | boundary: **confirmed**; performance: supported; float-inf: supported (1 confirmed) |

Both arms now confirm the one defect both of them actually demonstrated with a
reproducible witness call — the asymmetry is gone, with `b1_response.txt`/
`b2_response.txt` themselves untouched (only the scorer changed; the real
replies were re-scored, not re-written).

### 22.3 Four targeted regression tests (`tests/test_toy_agents_run.py`)

1. `test_host_verify_findings_confirms_regardless_of_witness_position_in_claim_or_evidence`
   — the same defect/witness/claim-term match confirms whether the concrete
   example sits before or after the literal word "evidence:".
2. `test_host_verify_findings_does_not_confirm_an_unrelated_finding_for_merely_containing_the_witness`
   — a finding whose claim does not match any `claim_terms` stays Supported even
   though its text happens to contain the witness substring (the widened search
   did not loosen the relevance filter).
3. `test_host_verify_findings_stays_conservative_on_correct_code_with_witness_in_claim`
   — the same claim-half phrasing against a genuinely correct implementation
   stays Supported, never Confirmed (the probe-vs-control check still gates it).
4. `test_review_only_pilot_scorer_never_writes_the_raw_witness_into_its_report`
   — `review-only-pilot/offset_bucket/score_review.py`'s `redact()` still strips
   the witness before anything is printed or saved; `host_verify_findings()`
   itself still legitimately carries it for the Host's own bookkeeping.

All four use the public `clamp` toy task (its `confirmation_witness`,
`"clamp(15, 0, 10)"`, is already openly used throughout this test file — see
e.g. the pre-existing test at `tests/test_toy_agents_run.py:518` — not the
private Phase 2 Fixture), so they run in the default suite, no Docker, no
private Fixture, no model call.

### 22.4 Verification

| Check | Result |
|---|---|
| New regression tests alone | 4 passed |
| `tests/test_toy_agents_run.py` + `tests/test_acqe_private_phase2.py` | 57 passed, 4 deselected |
| Real re-score of the unmodified `b1_response.txt`/`b2_response.txt` | B1 confirmed: 0→1, B2 confirmed: 1→1 (unchanged) |
| Ruff | all checks passed |
| `git diff --check` | clean |
| Full suite (run alone, not alongside Docker) | 896 passed, 1 skipped (environment: no Python 3.9 installed, pre-existing and unrelated to this fix), 5 deselected, exit 0, 736.39s — **zero failures**, including the `test_tasks.py::test_agent_timeout_and_error` flaky timing test that only failed in §20's heavily-loaded run and was already independently confirmed as load-induced, not a regression |

### 22.5 Residual scoring concerns (not fixed this round, flagged only)

- B2's `float('inf')` claim is labeled `[major]` despite resting on an
  under-specified part of the public interface contract (§21) and not matching
  the Fixture's own confirmation vocabulary; this is the reviewer's own severity
  judgment, not a scorer bug, and is out of this round's scope.
- `host_verify_findings()`'s ambiguity rule (`len(candidates) == 1`) is otherwise
  untouched and still means two *simultaneously* matching findings in one arm
  both stay Supported rather than picking one — correct, conservative behavior,
  not reopened here.
- This was audited and fixed for exactly one Fixture case (`offset_bucket`,
  whose `confirmation_witness`/`confirmation_claim_terms` happen to be short,
  literal substrings with no internal "evidence:"-like delimiter). The fix is
  generic to the function, not special-cased, so it applies to every case using
  `host_verify_findings()` the same way — but no other case's real model replies
  have been scored against it to confirm that in practice.

### 22.6 Pilot closure

The first Review-only pilot (`offset_bucket`, B1 vs. B2) can be considered
**closed for its own stated purpose — exercising the Review-only design and its
scorer end to end** — which it did, and in doing so caught a real, fixable
scorer bug before that bug could quietly bias a larger run. It is **not** closed
as a finding about review-lens effectiveness: per §21's conclusion, N=1 and a
scorer-artifact-free tie on the one real defect both arms found is not evidence
either way about B1 vs. B2 review quality. No new case was added and none is
proposed here; that decision is left to whoever asks for the next round.
