# 16. A phase and its repair loop

Each phase of an approved goal is run as one ordinary Task in the existing workflow engine (the project's providers, the Review Gate, the host test). You do not start phases one by one: starting the goal ([chapter 17](17-goal-run.md)) runs them in dependency order. When the host test or a review fails, the **Delivery PM** reads the evidence and writes a structured repair; the developer role is sent back with it; the host tests run again. You no longer copy answers between ChatGPT, Codex and Claude.

There is no resident process: a short process wakes when something finished, acts, and exits.

Watch it from the goal's page under **Tasks**, or with `ccc-mcp goal phase-show`. Chinese: [docs/ch/16-phase-runner.md](../ch/16-phase-runner.md).

## What has to be true first

* The plan version is **approved** (a person did it, for exactly that version and hash). The run stores the version and hash and acts on nothing else; editing, re-planning or cancelling the goal is refused while a phase is open.
* Every phase it depends on has **passed**, and no other phase is open.
* The project is **authorized for writes** (`ccc-mcp project authorize`). Approving a goal does not authorize anything on the project, and the Task's own plan approval is the existing rule: project authorization authorizes it, a goal approval does not.
* The project has an **explicitly configured test command**. The host runs that command and nothing else. A command written in a plan is never run; a `host_test` criterion counts as verified only when it is that very command, and every other criterion needs a person to accept it.
* The saved workflow has a developer step (a plan naming a team without one is refused when it is stored, so this is rechecked here rather than first discovered here), and every role uses a local CLI. A phase using the Classic roster runs its steps through the same resumable engine as other phases.

## What happens

1. A Task with a fixed ID (`GOAL-0001-P1`, never `TASK-NNNN`, never reused) is created for the phase from the approved scope, outputs and criteria.
2. The existing engine runs it: analyst, designer, developer. The analyst's report becomes *notes* on the approved phase; the phase text and criteria are not rewritten.
3. The host runs the test command. If it passes, the reviewer(s) run. If it fails, the reviewer is not asked to say so again: the Delivery PM is.
4. The Delivery PM (a read-only Codex or Claude run, through the same adapters as planning) receives the requirement, the phase and its criteria, the plan version and hash, the full test output, the git diff, the reviewer's objections, earlier attempts and repairs, and the budget left. It answers with a **repair spec**: what failed, the causes (each `confirmed`, with a quote that must be in the evidence, or a `hypothesis`), where to change, what must not change, one completion condition per approved criterion, and whether the analysis must be redone.
5. The developer role is sent back (`restart_from_step`) with that spec plus fixed text ccc-mcp adds: the criteria are unchanged, the host runs the same tests, do not edit tests, test configuration, CI files, `.git` or `.ccc-mcp`, no commit/push/release.
6. The phase is **PASSED** only when the Task finished through its Review Gate, the host saw the configured test command pass, nothing protected was touched, and every criterion is accounted for. A phase with criteria no command can judge ends **waiting for your acceptance** (`ccc-mcp goal phase-accept`, or the typed phrase in the console), not passed.

## Limits, and what is counted

| Unit | Meaning | Default |
|---|---|---|
| Attempt | One developer (RD / SD/RD) dispatch of the phase's Task. The first is attempt 1; each repair is the next. A quota fallback of the same role is not a new attempt. | 3 per phase |
| Agent dispatch | Every provider run for the goal: each Task execution (any role, fallbacks included) and each Delivery PM analysis. | 20 per goal |
| Time | Time the run was working. Waiting for a person does not count. | 60 minutes per goal |
| Retry vs new Task | A retry is a new attempt inside the same Task. A phase never gets a second Task. | |

They are counted from the database rows, so a crash cannot make a counter wrong, and they are **enforced in `dispatch_agent`**, so the engine's own chain, a repair and a person asking directly are all held to them. Resuming never adds to them.

**No progress.** If an attempt fails with the same failure fingerprint (failing tests and errors, ignoring times, addresses and paths) as the one before and changed nothing that matters (no files, or an identical diff), the run stops with `NO_PROGRESS` instead of spending the next attempt.

## Everything that stops it

It stops, says why, and waits. It never retries on its own after any of these, and no state ends in a hidden loop.

| State | Why |
|---|---|
| `EXHAUSTED` | attempts, agent dispatches or time used up (final; its Task is stopped) |
| `NEEDS_HUMAN` | no progress; the Delivery PM asked for a person, timed out, hit its quota, was unreachable or answered something invalid; an agent run failed, timed out, hit a quota, could not start or was interrupted; the host test may still be running or was interrupted; the test environment is blocked; a protected file was changed; the task is waiting for a decision |
| `AWAITING_ACCEPTANCE` | tests and review passed; criteria only you can judge remain |
| `PAUSED` / `CANCELLED` | you did it |

Each has a reason code, a plain sentence, and an audit event. Resume (`ccc-mcp goal phase-resume RUN [--note ...]`) carries on from where it stopped; the note is passed to the next Delivery PM analysis.

## What a model cannot do

The repair spec has no field for the requirement, the acceptance criteria, the test command, a role, a provider or a permission, and extra fields are refused. A spec is **refused whole** (nothing is sent to the developer) if: it names a different test command than the project's; drops, adds or renames an approved criterion; points at a protected path (`.git`, `.ccc-mcp`, `.github`, `pytest.ini`, `tox.ini`, `setup.cfg`, `pyproject.toml`, `conftest.py`, CI files); has no cause; contains a secret. A cause called `confirmed` whose quotes are not in the evidence is recorded as a `hypothesis`, and the developer is told so. The developer's own changes to protected files stop the run (`PROTECTED_PATH`) even if the tests then pass; continuing needs `--acknowledge-protected-paths`.

## Restart and recovery

State is in SQLite (`goal_phase_runs`, `goal_phase_attempts`, `goal_phase_repairs`, plus `goal_events`; append-only where it is history). After a restart you can see the last prompt hash, provider, Task and repair round. Starting the same phase twice, or from two places at once, returns the same run and Task. One process steps a run at a time (a file lock); a wake that arrives while it is busy is not lost. A start that died half way is finished, not repeated; a Delivery PM call that died is recorded as interrupted and asked again once (and counted); a recorded repair that was not yet sent is sent once without asking again.

`ccc-mcp goal phase-recover` (or **Pick this up again** when a run shows no sign of life) picks up runs whose process died. It uses the host-test recovery rules first: workers that stopped are closed as interrupted and are not replayed; a host test that cannot be proven gone is **not run again** and the run waits for you, and only `--confirm-host-tests-stopped` on the CLI can override that. The console and MCP have no such override.

## Honest limits

* Evidence levels: the tests in this repository drive the real adapters, workers and a real host test with a **scripted stand-in for the model**. No real model, subscription or quota has been exercised. Do not read the repair loop's success on a model as proven.
* One phase at a time. Nothing starts the next phase, and there is no resident executor.
* Windows is unverified; network file systems and two machines sharing a home are not covered; an older ccc-mcp sharing the home is not covered by the host-test lease.
* A leftover Delivery PM CLI process after a killed worker is read-only and ends by its own timeout; it is not stopped for you.
* The protected-path list is a guard against the obvious ways of making a test pass, not a sandbox. Review the diff.
* Wall-clock time keeps running if the machine sleeps while a run is working.
* A phase cannot run unattended with a role assigned to ChatGPT in the browser.
* The ID of a finished phase cannot be run again in the same goal (a rerun needs a new goal).
