# 14. Agent Studio: your own roles

Agent Studio lets you make roles of your own, give them instructions, and put them into a workflow next to the built-in ones. You use it in the local web console (`ccc-mcp ui`), under **Role Studio** in a project's sidebar. Nothing about it is reachable from ChatGPT, MCP tools, the CLI or a settings file: a role's instructions are written only by a person at the console.

## 1. What it is, and what it is not

A custom role is a **name and a body of instructions layered over one built-in role**, called its *base role* (SA, SD, RD, SD/RD, CR/REVIEW, SR, AR, SEC, QA, SW, or one of the everyday roles). The base role decides everything that is enforced by code: whether the role may write files, how its report is judged, which sandbox it runs under, whether it acts as a reviewer. **A custom role can never gain a permission by what its instructions say.**

Phase 1, which is what this version ships, has **no AI scoring of prompts**. The prompt check in section 4 is a free local text check. Scoring a role's prompt with a language model is planned work for a later phase; it is not available, and nothing in the console claims it is.

## 2. Create a role

1. Open **Role Studio** and choose **New role**.
2. Give it a **name** (up to 24 characters, no `< > & " ' \`` or backslash, starting with a letter or digit), an optional short description, and an avatar.
3. Choose **Based on**: the base role. It cannot be changed afterwards; use **Make a copy** to start from another one.
4. Write the instructions (section 3), optionally adjust its **Abilities** (display only: they draw the team chart and never change a score) and, under **Advanced**, pick a provider, model, effort and quota fallback for this role.
5. **Create role**. It starts at version 1 and is on. A project can have at most 30 custom roles.

Roles are never deleted: **Switch off** keeps every version and snapshot, and tasks that already started keep running with the role they began with.

## 3. Write the instructions: Guided sections or Full text

- **Guided sections** are six short boxes: goal, duties, limits, input and output format, collaboration rules, and what to do if something fails (4,000 characters each).
- **Full text** is one free box (16,000 characters). It is sent exactly as written; nothing is trimmed or rewritten.

Both bodies are always kept, so switching modes never loses what you typed; only the mode in use is sent. **Preview what will be sent** shows the whole message, with each part labelled by who may change it (you, ccc-mcp's fixed contract, or the provider wrapper). A preview calls no model, creates no run and stores nothing. A real run stores the exact text it sent, with credential-like strings redacted.

## 4. The free static prompt check

**Check my instructions** reads the text with plain rules: no model is called, nothing is sent anywhere and no tokens are spent. It points out missing areas, contradictions and wording that cannot work because ccc-mcp fixes it elsewhere. It gives **no score**. Detection is by keywords, so "no findings" is not a clean bill of health, and flagging an "ignore the rules" sentence does not make it safe: the protection is enforced by code (section 7).

## 5. Versions and snapshots

- Every save is a **new immutable version** with a fingerprint (SHA-256). The **History** tab lists versions and shows what changed between any two. Saving needs the version you started from, so two editors cannot overwrite each other.
- When a task starts a work round, ccc-mcp **pins** the roster: it freezes each custom role into a **snapshot** (the definition, its base role, the provider settings it resolved to, and the fixed-contract manifest) identified by the hash of that content. Editing or switching off the role afterwards cannot change what that task uses. A plan approval is bound to the pinned roster, so an approval given for one team cannot carry another team forward.
- Each step of a task has its own step identity, so the same role used twice stays two steps, and results, evaluations and the chat room show which role and version spoke.

## 6. Add a role to a workflow

In a project's **Settings → Workflow**, build your own workflow from steps and choose a custom role for any step whose base role fits. A workflow needs 1 to 12 steps and must start with a producing role (SA, SD, RD or SD/RD); a project can have 20 workflows of its own. A role that is switched off cannot be added to a workflow, and a new task cannot start on a workflow that still uses it. Gate roles (reviewers) can send work back to the step that made it, with the same round limit as the built-in workflows.

## 7. The fixed contract and the safety limits

Three kinds of rule act on a role, and only one of them is text:

| Kind | Who writes it | Can your instructions change it? |
| --- | --- | --- |
| Your instructions | you, in Role Studio | yes |
| Fixed execution contract (operator policy, report formats, handoff and gate rules) | ccc-mcp, versioned and hashed | no |
| Rules enforced by code (read-only or writable access, sandbox, plan approval, project authorization, host-run tests, gate verdicts, quota fallback) | ccc-mcp code | no |

All of these reach the provider in **one message**. The markers make the structure clear, but a model may disregard them: this is not privilege separation between messages. Safety rests on the third row.

If ccc-mcp's own fixed contract changes while a task is pinned to an older one, the change is classified. Same-meaning wording is recorded and the task continues. A change in meaning stops the task before the next step until you review it on the task page and type a phrase naming that task, step and contract. A project's write authorization never accepts it for you.

## 8. Export, import and trust confirmation

**Export backup** (project overview) and `ccc-mcp project export` now include custom roles: definitions and every version, the frozen snapshots and their hashes, the roster pins and approval bindings, the role history, and the roles named by your workflows. They leave out the complete provider text stored for each run (it contains the project's Context), separately stored runtime API keys and executable MCP server registrations. An export refuses to proceed if a role looks like it holds a credential.

Backups still contain Context, Agent reports, execution instructions, task history, role prompts, and retained project and provider settings. These fields may contain private content or undetected credentials; export does not uniformly redact every field. Some write paths reject or redact credential patterns, but those checks cannot cover every secret. Treat backups as sensitive files and inspect them before sharing.

**Import backup** (All projects) and `ccc-mcp project import` read the file as untrusted data. Before anything is written, every definition, hash, snapshot, roster and reference is checked again, and a bundle that fails any check is refused whole and leaves nothing behind (the import is one database transaction). The checksum in a file only shows it was not damaged; it says nothing about who wrote it.

Whatever the file claims:

- Every imported role arrives as **Waiting for confirmation** and every imported snapshot as untrusted. The trust records of the device the file came from are kept as history for you to read, never counted.
- An unconfirmed role cannot be edited, switched on, added to a new task or run, by any path: the database refuses an execution that names an untrusted snapshot.
- Turning on write access for the project does not confirm anything. ChatGPT, MCP tools, workers and the CLI cannot confirm a role.

To confirm a role, open it in **Role Studio**. The **Review** tab shows where the file came from (its project, export time and fingerprint, as claims), what the role may do (from its base role, not from the file), all of its instructions, its versions, the snapshots in the file and the workflows that use it. **Confirm and switch on…** asks you to type a phrase that contains the role id, its version and the first characters of its fingerprint, so a different role or a changed version cannot be confirmed by accident. Confirming records the console as the actor, switches that one role on, and confirms the snapshots of that version (older snapshots only if you tick them, or one by one later in **History**). Snapshots themselves are never modified.

Importing the same file again, or into a folder that already has ccc-mcp records, is refused. A project's roles are separate from every other project's, even when the role ids look alike. The folder's own source files and uncommitted changes are never touched; the imported project starts read-only and unfinished tasks start paused.

**Compatibility.** A bundle with no role records is still written as version 1 and is read by older releases: a project that never used custom roles exports exactly as before, even after running tasks, because only tasks that use custom roles pin a roster. A bundle with role records is **version 2**: a release that predates Agent Studio refuses it with "Unsupported project bundle version" instead of importing half a project. This release reads both. There is no downgrade path: to use a version 2 project on an older release you would have to re-create it there.

## 9. Known limits

- Messages from the prompt check, the preview and the import checks are written in English by the backend; the console labels are translated.
- A paused imported task that was pinned to an older snapshot stays blocked until you confirm that exact snapshot: tick it in the **Review** tab, or later use **Confirm this snapshot…** in the role's **History** tab.
- Real provider runs (Codex, Claude Code) are covered by simulated CLIs in the test suite; the tests that use installed, signed-in CLIs are excluded by default and were not run for this version.
- Windows process-boundary behaviour for provider runs has not been verified on a real Windows machine for this feature.
- Role ids are generated from the name (for example `u-reviewer-one`); they are readable but not editable.

Back to the [documentation index](README.md). 繁體中文版：[14. Agent Studio](../ch/14-agent-studio.md).
