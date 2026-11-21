# 14. The AI team, your own workflows and your own roles

The **AI team and workflow** card on a project's page is where you choose who works on new tasks, in what order, and with which AI tool. Everything technical (model, effort, quota fallback, review gates) is folded away until you open it. This chapter explains the card, how to make a workflow or a role of your own, and what stays fixed whatever you write.

## 1. Read the card

- **Workflow.** The drop-down groups the workflows as *Software teams* (Classic, Standard, Full), *Everyday teams* (life, business, content, decision) and *Your workflows*. Choosing one lists **that workflow's own steps**, taken from the saved workflow, not from a fixed template: a content team shows its assistant, researcher, writer, editor and fact checker, never a developer.
- **Steps.** Each row shows the step number, the role's name, the AI tool (ChatGPT, Codex or Claude Code) and an **Advanced** button. Roles run in the order shown. A role that appears twice is one setting, edited on its first step.
- **Save or discard.** Changes are a draft until you press **Save**; the card says when you have unsaved changes, and the buttons that open other editors wait until you save or discard. If the settings changed in another window, your draft stays on screen and the card asks you to discard it to load the saved ones, instead of overwriting the other change.
- **Goals.** A team with no developer step shows a hint: it runs ordinary tasks, but a Goal phase needs a developer step (see section 6).

New tasks use the workflow selected **when they start**. A task keeps the workflow and the role versions it started with; changing the card later never changes a task that is already running.

## 2. What is under Advanced

| Where | What it holds |
| --- | --- |
| A step's **Advanced** | Model (optional), effort, and the AI tool to use if quota runs out. A custom role opens its own editor instead. |
| **Advanced team settings** | Who chooses the workflow for a new task (the selected one, the task coordinator, or by the task's text), two one-click presets (ChatGPT for every built-in role, or the installed CLI tools), and the review gates (adversarial strictness, rounds per gate from 1 to 5, what happens when a gate gives up). |

The CLI sets the same values: `config role`, `config workflow`, `config workflow-selection`, `config review-gate`, `config profile`. See the [CLI reference](8-cli-reference.md).

## 3. Your own workflows

Press **New workflow** (or **Edit** on one of yours):

1. Give it a name (up to 40 characters).
2. Add steps, up to 12, and choose a role for each, built-in or your own. Move a step up or down, or remove it. **Add a step** appends one.
3. The first step must produce something (analyst, designer or developer); reviewers check what came before them.
4. **Save**. The workflow is selected for new tasks once it is saved.

A project can have 20 workflows of its own. Built-in workflows cannot be edited or deleted; start a new one instead. **Delete** removes one of yours: tasks already running it keep it, and new tasks go back to the Classic workflow. The same operations exist on the command line as `config workflow-save` and `config workflow-delete`.

## 4. Your own roles

Press **New role** (or **Edit** under **Manage custom roles**):

- **Name**: up to 24 characters, starting with a letter or digit, without `< > & " ' \`` or a backslash.
- **It works like**: the built-in role it is based on. It cannot be changed afterwards.
- **Instructions**: up to 16,000 characters, sent as written.
- **AI tool**: a tool of its own, or the same as its base role. Model, effort and quota fallback are under **Advanced**.

A project can have 30 custom roles. Roles are never deleted: **Disable** keeps every version, and tasks that already started keep running with the role they began with. A disabled role cannot be added to a workflow, and a new task cannot start on a workflow that still uses it.

### What a role can never do

A custom role is **a name and instructions layered over one built-in role**. The built-in role decides everything that code enforces: whether the role may change files, how its report is judged, which sandbox it runs under, whether it acts as a reviewer. **Nothing you write can give it a permission.**

| Kind of rule | Who writes it | Can your instructions change it? |
| --- | --- | --- |
| Your instructions | you | yes |
| Fixed execution contract (operator policy, report formats, handoff and gate rules) | ccc-mcp, versioned and hashed | no |
| Rules enforced by code (read or write access, sandbox, plan approval, project authorization, host-run tests, gate verdicts, quota fallback) | ccc-mcp code | no |

All of these reach the provider in one message. The markers show the structure, but a model may ignore them: this is not privilege separation between messages, and safety rests on the third row.

## 5. Versions, pinning and imports

- **Versions.** Every save of a role is a new immutable version with a SHA-256 fingerprint. Saving needs the version you started from, so two editors cannot overwrite each other.
- **Pinning.** When a task starts a work round, ccc-mcp freezes each custom role it uses into a snapshot (definition, base role, resolved provider settings and the fixed-contract manifest). Editing or disabling the role afterwards cannot change that task, and a plan approval is bound to the pinned team. Goals do the same: a plan keeps the team it was written with, and only plans written after a change use the new settings.
- **Contract changes.** If ccc-mcp's own fixed contract changes while a task is pinned to an older one, the change is classified. Same-meaning wording is recorded and the task continues. A change in meaning stops the task before the next step until you review it on the task page and type the phrase naming that task, step and contract. Project write authorization never accepts it for you.
- **Export and import.** **Export backup** and `ccc-mcp project export` include custom roles, their versions, snapshots, roster pins and the roles your workflows name. They leave out the full provider text stored for each run, stored runtime API keys and executable MCP server registrations, and an export refuses to proceed if a role looks like it holds a credential. A backup still contains Context, reports and task history, so treat it as a sensitive file.
- **Trust on import.** An imported file is untrusted data. Every role in it arrives **waiting for you**, whatever the file claims, and nothing can run it, edit it or add it to a task until you confirm it. The **Imported roles waiting for you** card on the project's page lists them. **Review and trust** shows where it came from, what its base role may do and its instructions, and asks you to type a phrase that names that exact role and version. ChatGPT, MCP tools, workers, the CLI and the project's write authorization cannot confirm a role.

A bundle with no custom roles is still written as version 1 and read by older releases. A bundle with role records is version 2, which older releases refuse as "Unsupported project bundle version" instead of importing half a project.

## 6. Which teams can run a Goal

A Goal phase is accepted on host-run tests of what a developer changed, and a failed step is repaired by sending it back to that developer. So only a workflow with an `RD` or `SD/RD` step can run one: Classic, Standard, Full, and your own workflows that include a developer step (a custom role counts as the built-in role it works like). The everyday teams write reports and change no files, so they run ordinary tasks only.

The check happens where the plan is stored. A plan that names a team with no developer step, or says `default` while one is selected, is refused with the reason, rather than failing after you approve it. The Delivery PM is told which workflows can run a phase, and with Codex or Claude Code for every role in the team it runs without a ChatGPT conversation. See [15](15-goal-planner.md), [16](16-phase-runner.md) and [17](17-goal-run.md).

## 7. Known limits

- The backend can build a preview of the message a role would receive and run a keyword check on instructions, but this version of the console does not show either. The check gives no score, and "no findings" would not mean safe.
- Messages from some import checks are written in English by the backend.
- The Codex and Claude Code runs in the test suite are scripted CLIs; the tests that use installed, signed-in CLIs are excluded by default.
- Role ids come from the name (for example `u-reviewer-one`); they are readable but not editable.

Back to the [documentation index](README.md). 繁體中文版：[14. AI 團隊、自訂工作流與角色](../ch/14-ai-team-and-roles.md).
