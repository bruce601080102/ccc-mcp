# 15. Tasks and Goals: ask ChatGPT, watch it in one place

You say what you want in ChatGPT. ccc-mcp arranges the AI team, runs the tests on your computer, reviews, repairs, and stops only when it is finished or needs a decision from you. The console shows the **same** rows ChatGPT reads and writes, under **Tasks**.

## What ChatGPT decides, and what ccc-mcp checks

ChatGPT sorts each message; the server enforces the parts that must not depend on a model's judgement.

| Your message | What ChatGPT does | What ccc-mcp guarantees |
|---|---|---|
| A question, or chat | Answers. Nothing is created. | — |
| One small change | `start_project_request` (a single task, no Goal). | The task appears under **Single tasks**. |
| Ongoing or multi-step development, repeated test and review | `start_goal` with a short title it writes. | The Goal is created once: a retry with the same `request_key`, or the same request while that Goal is open, returns the existing Goal. |
| More work on something that exists | `get_work_overview`, then `plan_goal(change_request=...)` on that Goal. | A request that looks like an open Goal is returned as a question (`needs_choice`), never silently started twice. |
| No clear project | Asks you. | With several projects and none named, nothing is written. |

A Goal is real only when a tool result says so; ChatGPT's wording is never the record. The tools read and write the same SQLite rows as the console, under the same permission checks.

## The tools

| Tool | Read or write | Purpose |
|---|---|---|
| `get_work_overview` | read | Everything in progress: groups (a Goal and its steps) and single tasks, with headline, who is working, progress and whether it needs you. |
| `get_goal` | read | One Goal: plan, approval, budget, the task of each step (`evidence_reads`), and the acceptance you still owe. |
| `request_goal_approval` | read | What you must approve, its exact phrase, and where to approve. It approves nothing. |
| `start_goal` | write | Start tracked work. Optional `title`, `request_key`, `confirm_new`, and a `plan` ChatGPT wrote. |
| `plan_goal` | write | Store a plan written in the chat, or ask the planner (Codex or Claude Code) to write or revise one. |
| `control_goal` | write | `start`, `pause`, `resume`, `cancel`, `rename`. Starting needs an approved plan, a read-write project and a test command. |

The write tools are absent from read-only HTTP connections. Long runs are done by ccc-mcp's own execution service, so they continue after the chat ends; ChatGPT can only answer when you write, so ask it for status or open **Tasks**.

## What needs you, and what does not

A person must do the parts only a person can: **approve** a plan (at this machine, with the typed phrase), grant write access to a project, and **accept** criteria that no test can decide. Everything inside an approved plan runs by itself: steps, host tests, repairs within the budget, reviews, the final check. Nothing a plan or a model says widens its permissions, raises the budget or approves itself.

## The Tasks page

**Tasks** lists work groups and single tasks. Each group shows its plain-language headline, who is working, progress and a **Needs you** mark; open it to see the steps, each linked to the task that holds the conversation, host test and diff. The page updates in place: nothing reloads, what you are typing, where you scrolled and which groups are open are kept, and a lost connection keeps the last state on screen and retries.

You can also describe work in the box at the top of the page. That calls the same function as `start_goal`, and also asks the configured planner (Codex or Claude Code) for a plan, so the only thing left for you is to read it and approve it. In the approval dialog, **Start working right after approving** is ticked by default: one typed phrase approves exactly that plan version and starts it. Untick it to approve without starting.

## Imported roles

Custom roles are no longer created in the console. A role that arrives inside an imported project backup stays off. The project page shows **Imported roles waiting for you**; **Review and trust** shows the role, its base role (which alone decides what it may do), and its source, and trusts it only when you type the phrase for that exact version. Nothing reachable from MCP, the CLI, a worker or a project's write authorization can do this.

---

## Goal plans and approval

A **Goal** is a whole requirement you state once. A Delivery PM turns it into a plan of phases; you review the plan, ask for changes, and approve it. Planning and approving **start no work**: no Task, no Agent, no host test, no change to your files. How the approved plan is run is described in [16. Phases](16-phase-runner.md) and [17. Running a whole goal](17-goal-run.md).

Open a Goal from **Tasks** in the local console (`ccc-mcp ui`), or use `ccc-mcp goal ...`. Traditional Chinese: [docs/ch/15-goal-planner.md](../ch/15-goal-planner.md).

## What a plan contains

The Delivery PM decides how many phases the goal needs. There is no fixed template; the only limit is a runaway guard of 12. Each phase has a scope, what it delivers, the phases it depends on (no cycles), a suggested workflow and risk, and acceptance criteria. Only `host_test` and `host_command` criteria are decided by a command run by the host; `reviewer_check` is advice and `manual` always waits for a person. A command is an argument list, never a shell string, and plans may not propose commit, push, publishing, network tools or other changes to the repository.

A phase with no workflow specified, or with `workflow: "default"`, uses the workflow selected in **AI team and workflow**. Saving a plan version captures the step order, provider settings, review gates and trusted custom role versions. Later edits in project settings apply to new plans and tasks. A phase can run only on a workflow with a developer step (`RD` or `SD/RD`; see [chapter 14](14-ai-team-and-roles.md#6-which-teams-can-run-a-goal)), so a plan that names any other team, or says `default` while one is selected, is refused when it is stored, with the reason. The planner is told which workflows are `goal_ready`.

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
