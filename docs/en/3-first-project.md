# 3. Create a first project

After [installation](1-install-and-sign-in.md), create a small project that can be verified locally.

```bash
ccc-mcp project create hello-demo
ccc-mcp project authorize hello-demo
```

Projects are created under `~/ccc-mcpProjects/` by default. Use `--path` for one project in another directory, or register an existing directory instead:

```bash
ccc-mcp project add "/path/to/existing-project" --id hello-demo
ccc-mcp project authorize hello-demo
```

Authorization is a local, explicit grant to modify that one project and run its configured test command. It does not grant access outside the project.

With the ccc-mcp ChatGPT connection selected, ask for a small task such as:

> Create `hello.py` in `hello-demo`; it should print `Hello, World!`, with a test that verifies the output.

Verify from the owning machine:

```bash
ccc-mcp --project hello-demo task status
ccc-mcp --project hello-demo task usage
ccc-mcp --project hello-demo context show
```

`cli_invoked: true` is evidence that a native Codex or Claude CLI actually ran. A chat response alone is not completion evidence.

Continue with [Run and resume the workflow](4-workflow.md).

## ChatGPT-only 15-minute smoke test

To try the whole loop with ChatGPT alone, without installing Codex or Claude Code. Before starting, `ccc-mcp doctor` lists whether Git/Codex/Claude are currently installed and exactly what each workflow needs (`workflows.chatgpt_only`/`workflows.best_model`), so "no Codex/Claude Code CLI needed" isn't just taken on faith.

```bash
ccc-mcp project create hello-demo-chatgpt --profile chatgpt-only
ccc-mcp project authorize hello-demo-chatgpt
```

`--profile chatgpt-only` sets `SA`, `SD`, `RD`, `SD/RD`, and `REVIEW` all to ChatGPT in the same step as creation (for an existing project, use `ccc-mcp --project hello-demo-chatgpt config profile chatgpt-only` instead — same effect). Confirm it actually took before going further:

```bash
ccc-mcp --project hello-demo-chatgpt config show
```

The output's `profile` should be `"chatgpt-only"`, with every role under `roles` showing `"provider": "chatgpt"`.

Connect the Tunnel per [Connect ChatGPT](2-connect-chatgpt.md) (`ccc-mcp connect <tunnel_id>`) and confirm ccc-mcp shows up in ChatGPT's connected tools. With that connection selected, ask for:

> Create `hello.py` in `hello-demo-chatgpt`; it should print `Hello, World!`, with a test that verifies the output.

ChatGPT itself performs all three automatic roles (SA, SD/RD, REVIEW) through MCP tools (`read_workspace`/`write_task_file`/`submit_agent_report`) — no background CLI subprocess ever runs. Verify from the owning machine, not just the chat reply:

```bash
ccc-mcp --project hello-demo-chatgpt task handoff
ccc-mcp --project hello-demo-chatgpt task status
ccc-mcp --project hello-demo-chatgpt task usage
ccc-mcp --project hello-demo-chatgpt context show
```

- `task handoff`: the most direct answer to "whose turn is it, and what do I do now." When this ChatGPT conversation itself is what's being waited on (`waiting_on: "CHATGPT_HANDOFF_PENDING"`), it includes a ready-to-paste `chatgpt_prompt` plus `expected_report_fields`; otherwise it reports what's actually being waited on (a background agent still running, the PM needing to refine a report, an interrupted dispatch needing resume, etc.) — all derived from the same task status/context, never a separately tracked state.
- `task status`/`brief.waiting_on`: `CHATGPT_HANDOFF_PENDING` means this ChatGPT conversation itself must act now, not a background worker; `brief.tests` shows whether the host actually ran the configured test command and whether it passed.
- `task usage`: a ChatGPT execution is marked `"interactive": true` with `invocation.source: "chatgpt_web"` — `cli_invoked: false` there means "never invokes a CLI by design," not a failure; ccc-mcp never fakes a CLI call.
- `context show`: the current Context version, next action, and whether this round has been refined yet.

Finally, confirm the host actually ran the test command and it passed (see `brief.tests` from `task status`, or run `ccc-mcp --project hello-demo-chatgpt task test` manually). ChatGPT claiming "tests passed" in chat never substitutes for this record.
