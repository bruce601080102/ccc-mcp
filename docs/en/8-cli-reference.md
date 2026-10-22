# 8. CLI reference

Use `ccc-mcp --help` for the installed version's complete command list. Set `CCC_MCP_LANG=en` before invoking ccc-mcp for English CLI help; this is separate from the project report language.

## Common commands

```bash
ccc-mcp setup all
ccc-mcp connect tunnel_...
ccc-mcp project create <id> [--path <path>] [--profile chatgpt-only|best-model]
ccc-mcp project add <path> --id <id>
ccc-mcp project authorize <id>
ccc-mcp --project <id> task handoff
ccc-mcp --project <id> task preflight
ccc-mcp --project <id> task status
ccc-mcp --project <id> task usage
ccc-mcp --project <id> task resume
ccc-mcp --project <id> context show
ccc-mcp --project <id> verify
```

Use `config show`, `config providers`, and `config prompt <ROLE>` before changing roles. Role values include `SA`, `SD`, `RD`, `SD/RD`, and `REVIEW`; automatic workflow advancement uses `SD/RD`.

`config profile chatgpt-only` sets all five roles (`SA`, `SD`, `RD`, `SD/RD`, `REVIEW`) to `chatgpt` in one call, clearing any leftover Codex/Claude `model`/`effort`/`quota-fallback` — including the standalone `SD`/`RD` slots that automatic advancement never dispatches, so `config show` never still looks like a Codex/Claude mix. `config profile best-model` switches back, resolved dynamically from whichever native CLIs are actually installed here: both installed keeps the Codex/Claude split, only one installed assigns every role to it, neither installed is rejected. `config show`/`config providers` report the resulting `profile` (`chatgpt-only`, `best-model`, or `custom`) as a read-only, derived field.

`task handoff` reads whose turn it is right now and what to do about it — purely derived from `task status`/context/pending ChatGPT handoffs, never a second tracked state. When an interactive ChatGPT execution is what's blocking progress, it also includes `chatgpt_prompt` (paste-ready text for that role) and `expected_report_fields`.

`task preflight` is a read-only pre-dispatch check — it installs nothing, never runs the test command, never touches Git, never dispatches or commits anything. The output is a leveled `checks[]` list (each `ready`/`warning`/`blocker`, with a `remediation` for anything short of `ready`) plus a `summary` tally; `ready` means there is no `blocker`:

```jsonc
{
  "ready": false,
  "summary": {"ready": 7, "warning": 1, "blocker": 1},
  "checks": [
    {"id": "authorization", "status": "blocker", "message": "...", "remediation": "ccc-mcp project authorize demo-app"}
  ]
}
```

The top level also carries `next_role` (which role `advance_project` would actually dispatch next for this task — one of `SA`/`SD/RD`/`REVIEW`, or `null` once the task is done) and `profile` (the current five-role combination read as `chatgpt-only`/`best-model`/`custom`, the same derivation `config show`'s `profile` field uses).

Checks covered today:

- `authorization`: whether the project is continuously authorized (a read-only project can still use the manual-approval flow, but this is still a `blocker` here since the automatic Agent workflow genuinely needs it).
- `role_SA`/`role_SD`/`role_RD`/`role_SD/RD`/`role_REVIEW`: whether all five roles' configured providers are actually usable — `chatgpt` is always usable (its connection state is covered separately by `tunnel`); a native CLI (`codex`/`claude`) is checked for real. **Only `next_role`** being unavailable is a `blocker`; the other four roles aren't needed by this task yet, so a missing CLI there is only a `warning` (the message says whether it's this task's next role).
- `tunnel`: only present when some role is configured for `chatgpt`, and can only confirm tunnel-client was downloaded, never that it is connected right now. Missing entirely while `next_role` needs it is a `blocker` (ChatGPT cannot reach ccc-mcp at all); missing while only some other role needs it is a `warning`; even once installed, since live connection can't be confirmed, it still tops out at `warning`, never `ready`.
- `live_execution`/`pending_dispatch`/`pm_refinement`/`pending_approval`: whether an Agent execution is already running, a prior round's dispatch was interrupted and never confirmed, PM-unrefined reports exist, or a plan is awaiting local approval — each a reason a fresh dispatch would be rejected (or close to it) right now; `pm_refinement` is a `blocker` under continuous authorization, the rest are `blocker` or `warning` depending on the situation.
- `skills`: whether installed Skill files are readable.
- `context_budget`: only present when `context_token_budget` is configured; calls `build_handoff` itself to check whether the PM package is over budget — the same calculation a real dispatch uses, never a separate estimate.
- `test_command`, `dependencies`, `git`: the original three environment checks (also kept at the top level under `test_command`/`dependencies`/`git`, unchanged, for backward compatibility). `test_command` now carries two deliberately separate path fields:
  - `cwd`/`cwd_exists`: where the test command **actually (or will) run**. In `workspace_mode: worktree` this is always this task's own worktree path (`task_worktree(project_id, task_id)`) — **even before that worktree exists** — never silently substituted with the project root; `cwd_exists` says whether that real workspace exists right now. In `workspace_mode: project`, `cwd` is the project root.
  - `inspection_path`: only differs from `cwd` while the worktree doesn't exist yet — at that point the real workspace has nothing in it, so dependency-manifest detection **previews** the project root instead (the worktree will be created from its HEAD). Once the worktree exists, `inspection_path` equals `cwd`. `dependencies.checked_path` uses this same path.

  In short: **`cwd` answers "where will the test actually run"; `inspection_path` answers "what am I previewing dependencies from right now"** — they only diverge before a worktree exists.

Every check is evaluated fresh; nothing here is a separately tracked state, and a real dispatch moments later can still differ if something changes in between — this is a snapshot, not a lock.

For Skill management, see [step 6](6-skills.md). For public MCP contracts rather than CLI usage, see [step 9](9-protocol.md).
