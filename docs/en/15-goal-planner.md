# 15. Goals: plan a whole requirement, then approve the plan

A **Goal** is a whole requirement you state once. A Delivery PM turns it into a plan of phases; you review the plan, ask for changes, and approve it. Planning and approving **start no work**: no Task, no Agent, no host test, no change to your files. Running one approved phase at a time is described in [16. Running a phase](16-phase-runner.md).

Use it in the local console (`ccc-mcp ui`) under **Goals** in a project's sidebar, or with `ccc-mcp goal ...`. Traditional Chinese: [docs/ch/15-goal-planner.md](../ch/15-goal-planner.md).

## What a plan contains

The Delivery PM decides how many phases the goal needs. There is no fixed template; the only limit is a runaway guard of 12. Each phase has a scope, what it delivers, the phases it depends on (no cycles), a suggested workflow and risk, and acceptance criteria. Only `host_test` and `host_command` criteria are decided by a command run by the host; `reviewer_check` is advice and `manual` always waits for a person. A command is an argument list, never a shell string, and plans may not propose commit, push, publishing, network tools or other changes to the repository.

Everything a model returns is **data**. It must pass strict validation (unique phase ids, every dependency exists, no cycle, bounded sizes, no secrets) before it is stored; otherwise the run is shown as failed and nothing is saved.

## The Delivery PM

The Delivery PM is one read-only run of the **Codex or Claude Code CLI**, started only when you ask. One request is one model run: there is no retry loop, only one run may be live per goal, and a goal may use at most 10 planning runs. ChatGPT in a browser cannot be started from here; if that is the project's analyst you are told so, and you can import a plan written elsewhere (`ccc-mcp goal import`), which is labelled manual. Real agents follow the same rule as elsewhere: the project must be authorized, or `CCC_MCP_ENABLE_REAL_AGENTS=1` must be set.

## States

| State | Meaning |
|---|---|
| Not planned yet | The goal exists; no plan |
| Planning | A planning run is live |
| Waiting for your approval | A plan exists and the current version is not approved |
| Approved | You approved the current version |
| Cancelled | Final |

A state is worked out from the stored rows and is never stored, so it cannot disagree with them. Asking for changes creates a **new version**; an earlier approval never covers it.

## Approval

Approving is your act, at this machine: an interactive terminal (`ccc-mcp goal approve`) or the console with the typed phrase `APPROVE <goal> v<version>`. It binds to one version and its hash, is recorded with who and when and which project authorization was active, and repeating it changes nothing. A project's `--write` authorization never approves a plan. **Approving a plan does not approve** commit, push, release, deploy, wider access, a larger budget, an untrusted role or a changed contract.

## Budget

A plan declares limits (3 tries per phase, 20 agent runs, 60 minutes). Planning enforces nothing; a phase run enforces them in code (see chapter 16). The plan's own `enforced` flag stays `false` and cannot be set by a plan.

## Storage and limits

Goals live only in SQLite (`goals`, `goal_plan_versions`, `goal_plan_phases`, `goal_approvals`, `goal_events`, `goal_plan_runs`), append-only. Nothing is written to the shared Context. Removing a project removes its goals; clearing task history does not (and is refused while the project has phase runs). A project bundle that contains goals is refused for now rather than silently dropping them.

Limits inherited from the host-test work (they apply to phase runs): there is no resident executor; a test process left by a killed run can need manual care; Windows is not verified; network file systems and two machines sharing one home are not covered; an older ccc-mcp sharing the same home is not covered by the host-test lease.
