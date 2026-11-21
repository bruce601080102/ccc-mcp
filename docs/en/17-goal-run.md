# 17. Running a whole goal: from the approved plan to a verified result

Once a goal's plan is approved you can run **the whole goal** with one start. The goal run starts the phases for you, one at a time, in the order their dependencies allow. Each phase is run the way [chapter 16](16-phase-runner.md) describes (an ordinary Task in the existing workflow engine, the role and provider snapshots saved with the plan, the Review Gate, the host test, the Delivery PM's repair loop). When a phase passes, the goal run writes down what happened, hands the next phase what it needs, and starts it. When every phase has passed, the **whole project is tested again** before the goal is called done.

You no longer copy prompts, paste results back, or create the next Task by hand.

Use it from the goal's page (**Run the whole goal**) or with `ccc-mcp goal run GOAL-0001 [--wait]`. Chinese: [docs/ch/17-goal-run.md](../ch/17-goal-run.md).

## Who keeps it moving (read this before calling it "autonomous")

It is **event driven, not a resident daemon**. Every change wakes a short `ccc_mcp.goal_run_worker` process: an execution ended, a phase changed state, a person acted. The worker takes the goal run's file lock, reads the saved state, makes the next decision, acts, looks again if it was woken meanwhile (or if its own step made a further step possible), and exits.

* **Closing the browser changes nothing.** The chain does not run through the browser. Opening the page again shows the same saved state.
* **One supervisor per goal run.** A second worker finds the lock held and leaves; the wake it carried is noticed by the first before it lets go.
* **If every process dies** (a reboot, `kill -9`), nothing wakes the run by itself. It continues when the web console starts or its watchdog (every 20 seconds) sees the run quiet for 60 seconds, or when you run `ccc-mcp goal run-recover`. Until then the goal run waits in SQLite and says it is *stalled*. The watchdog only wakes; it never answers a question that is a person's.
* **A crash between "phase 1 passed" and "phase 2 created" loses nothing and repeats nothing.** Writing the passed phase down, creating the next phase's run and its Task are each safe to repeat: the next look does what is missing, and a phase can have only one run and one Task.
* **An orphan that cannot be proven gone stops the goal** instead of being redispatched. This reuses the Stage 0/B rules: a host test or a provider CLI left behind by a killed process is stopped only when it is provably the recorded one; otherwise the run waits for a person.

## What has to be true first

Everything from chapter 16 for *every* phase of the plan, checked **before anything starts** so the run does not stop half way for a reason that was knowable: the plan version is approved by a person (for exactly that version and hash), the project is authorized for writes, an explicit test command is configured, every phase's workflow is a step workflow with a developer step, and no role needs a ChatGPT browser tab. Only one goal runs in a project at a time (they share the files), and a phase cannot be started by hand while a goal run owns the goal.

## What happens

1. **Pick the next phase from the dependency graph**, not from the phase number. One phase at a time, so two phases never edit the working tree together.
2. **Start it through the Stage B phase runner** with a fixed Task ID (`GOAL-0001-P2`). Starting a phase twice returns the first run; nothing is created twice.
3. **Hand it the context of the phases before it.** When a phase passes, the goal run records (append-only, hashed): the Task, every role's execution with its provider and prompt hash, the attempts and how they failed, the Delivery PM's diagnosis, the host test (command, result, git revision), the files, and a fingerprint of the working tree. The next phase's Task requirement carries a bounded summary of that: what was delivered, which files, how it was verified, and what went wrong on the way. It is reference material; it cannot change the phase's scope or criteria.
4. **A phase that fails, runs out of limits or needs a person stops the whole goal.** Phases that depend on it never start. The reason is in plain words on the page.
5. **When every phase has passed, verify the whole goal** (next section).

## The final verification

Every phase showing PASSED is not enough. The configured test command runs again on the **whole project**, on the host, through the Stage 0 host-test session (mutually exclusive with any other test, an interrupted test is recognised). Around it:

| Check | A failure means |
|---|---|
| The configured command passes on the whole project, no tests skipped for being "not collected", not interrupted, not blocked | the goal is **not** passed |
| Final acceptance criteria whose command is the configured one are verified by that run; every other criterion needs **you** | the goal waits for your typed acceptance (`ACCEPT GOAL-0001`) |
| The files are exactly what the last phase left (fingerprint) | tests passing is not enough: you decide |
| Every changed file was reported by some phase | you decide |
| No protected files (git, ccc-mcp, CI, test configuration) differ from the last commit | you decide |
| Test files that a phase reported still exist | you decide |
| No unresolved review finding in any phase's Task | you decide |
| No phase was continued after a person acknowledged protected-path changes | you decide |

A model saying "done" is never evidence. The checks can only make a pass need you; they can never turn a failing run into a pass.

### When the final verification fails

* **An ordinary implementation problem** is handled inside its phase by chapter 16's repair loop, before the phase can pass.
* **A cross-phase problem** (every phase passed, the whole fails) gets **one integration fix** (`INT-1`): a traceable run of the same machinery, with the verification's failing output as its requirement. It cannot rewrite a finished phase's record, the criteria or the plan. The final verification runs again after it. Its attempts and dispatches come out of the same goal budget.
* **A change of requirement or architecture** cannot be decided by a model. If the Delivery PM says the failure cannot be fixed inside the approved scope, or the integration fix did not help, the goal waits for you. **Ask the Delivery PM for a revised plan** drafts a *new, unapproved version*; nothing continues until you read it and approve that exact version. The run continues under it only if every phase that already passed is unchanged and every phase that ended without passing has a new id (a phase id is spent once it has been run).
* **Cannot be established safely** (an unsafe leftover host test, a blocked environment, no improvement) stops the goal and says why.

## Which evidence decides which criterion

A goal can pass **without anyone confirming it**, but only when every criterion is decided by the host. A criterion counts as machine-verified only when its `verify` is a `host_test` whose command is **exactly the project's configured test command** and which expects exit code 0 (or none). Everything else (a different command or selector, another expected exit code, `host_command`, `reviewer_check`, `manual`) is **yours to decide**, however green the suite is: a passing suite is evidence that the suite passes, not that a different requirement holds. The approval screen lists, per criterion, whether the host or you will decide it, before you approve.

When the run ends, the goal run shows the matrix: for each final criterion the host run that decided it (command, exit code, git revision) or the person who accepted it, and for each phase how its criteria were decided. A model's own "done" is never one of those.

## One budget for the whole goal

| Unit | Counted | Default |
|---|---|---|
| Attempts | per phase: developer dispatches of that phase's Task, the first included | 3 |
| Agent dispatches | over the **whole goal**: every provider run of every phase's Task (any role, fallbacks and interrupted runs included), every Delivery PM analysis, and every revised plan asked for during the run | 20 |
| Time | over the whole goal: time phases were working and the final verification ran. Waiting for a person and a pause do not count. A computer that sleeps while a run is active does (wall-clock) | 60 minutes |
| Tokens / money | **unknown**: the providers' command-line tools do not report them, and the page says so | |

The budget is frozen when the run starts; a revised plan cannot raise it, and starting a new phase never gives a new one. It is derived from the database rows, so a crash cannot make a counter wrong, and it is enforced where the work starts: in `dispatch_agent` for every execution, and in the transaction that records a Delivery PM analysis or a revised-plan request (`BEGIN IMMEDIATE`, so two runners cannot both take the last unit).

## States

`RUNNING` → `VERIFYING` → `PASSED`. Along the way: `WAITING_USER` (a phase or the final result needs you; see the reason on the page), `PAUSED` (you did), `EXHAUSTED` (a limit), `FAILED` (a phase was cancelled, or no phase can start), `CANCELLED` (you did). Every change is an event with a reason; finished goal runs, phase records and verifications cannot be changed or deleted (the database refuses). A goal that has a run shows the run's state; before it, `READY`.

Pause stops new work from starting (a step already running finishes, and what it hands on is not lost). Resume carries on from where it stopped and adds no budget; a second resume is refused. Stopping the whole goal ends the open phase and its Task; nothing is dispatched afterwards, because a phase cannot be started once the run is not running. Plans cannot be changed while the goal works; pause it first.

## What it will not do

Commit, push, reset, rebase, release, deploy or publish. Widen a provider's permissions. Use an untrusted custom-role snapshot. Accept an incompatible contract change. Change the requirement or the acceptance criteria. Run a command a model or a plan supplied. Go over a limit. Approve a plan.

## Known limits

* **No real model has been exercised.** Everything above was shown with a scripted CLI standing in for Codex and Claude (the real adapters, the real worker, a real git project and a real host test, but no provider).
* Windows, network file systems and several machines sharing one home are unverified; an older ccc-mcp on the same home is not covered by the host-test lease.
* Without a console or `goal run-recover`, a run whose every process died waits.
* A goal has at most one run. A run that ended (passed, exhausted, failed or stopped) cannot be started again; a run that is waiting for you can be resumed. A finished phase cannot be run again in the same goal, and the integration fix is limited to one per plan version.
* Time is wall-clock: if the supervisor is killed while the final verification runs, that time keeps counting until the run is picked up again.
* Roles assigned to ChatGPT in the browser require an interactive task. Automated phase and goal runs need local CLI providers.
* The protected-path list and the final checks are guards, not a sandbox: read the diff.
* `clear_project_history` and project-bundle export are refused for projects with goal runs.
