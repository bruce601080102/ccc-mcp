# 12. ChatGPT-only end-to-end evidence walkthrough

This is a minimal end-to-end acceptance walkthrough completed in a **real ChatGPT conversation**. It shows how to rerun the path, which outputs count as evidence, and where to resume when the workflow stops. It is neither a simulation nor a claim that a model saying “done” is enough.

The example creates `hello.py` and `test_hello.py`; the test executes the program through `subprocess`. The recorded run on 2026-10-22 completed ChatGPT `SA → SD/RD → REVIEW`, had the host run pytest with `1 passed`, and ended in `COMPLETED`.

## What counts as success

All of the following must hold:

- The five role slots `SA`, `SD`, `RD`, `SD/RD`, and `REVIEW` use `chatgpt`.
- Each of the three workflow stages handed to `SA`, `SD/RD`, and `REVIEW` leaves a successful interactive execution.
- The host, not the Agent, runs pytest and collects at least one test.
- `task status` reports `executed_by_host: true`, `passed: true`, and `brief.verification.host_verification: "passed"`.
- REVIEW is complete and the task state is `COMPLETED`.

A chat response or `agent_claim: "complete"` alone is not sufficient.

## 1. Create an isolated project and save its configuration evidence

Run in the Python environment where ccc-mcp is installed:

```bash
conda activate stock311

ccc-mcp project create chatgpt-e2e-demo --profile chatgpt-only
ccc-mcp project authorize chatgpt-e2e-demo

cd ~/ccc-mcpProjects/chatgpt-e2e-demo
mkdir -p evidence
ccc-mcp --project chatgpt-e2e-demo config show | tee evidence/01-config-show.json
```

`project authorize` is explicit local consent: ccc-mcp may write only through authorized task executions in this project. `--profile chatgpt-only` assigns every role to ChatGPT. `config show` must report `profile: "chatgpt-only"` and `provider: "chatgpt"` for every role.

Set the host test command explicitly so the walkthrough does not depend on an implicit default:

```bash
ccc-mcp --project chatgpt-e2e-demo config test-command "python -m pytest -q"
```

The host executes this command; an Agent never does. Some older output may show `test_command: null` while still selecting default pytest behavior. This walkthrough configures it explicitly so it remains reproducible across versions.

## 2. Connect ChatGPT and have it create the task

Set up the Tunnel as described in [Connect ChatGPT](2-connect-chatgpt.md), and leave its process running:

```bash
ccc-mcp connect tunnel_YOUR_ID
```

In a ChatGPT conversation with the ccc-mcp connection selected, ask it to **actually call the tools**:

> Use ccc-mcp to create a task in `chatgpt-e2e-demo`: create `hello.py`, which prints `Hello, World!` followed by a newline; create a pytest-collectable `test_hello.py` that runs `hello.py` through subprocess and checks stdout and the return code. Complete SA → SD/RD → REVIEW. Do not merely give me code or advice; host test evidence is the only passing evidence.

This creates the task and dispatches interactive SA first. `task preflight` requires an existing task; if it returns `Project workstream has not started` before the task exists, that is expected rather than an environment failure.

## 3. Use handoff to complete SA

After ChatGPT creates the task, inspect it locally:

```bash
ccc-mcp --project chatgpt-e2e-demo task preflight
ccc-mcp --project chatgpt-e2e-demo task handoff | tee evidence/02-handoff-sa.json
ccc-mcp --project chatgpt-e2e-demo task status
```

At the start, `task handoff` should resemble:

```jsonc
{
  "state": "ANALYZING",
  "waiting_on": "CHATGPT_HANDOFF_PENDING",
  "role": "SA",
  "agent": "chatgpt",
  "chatgpt_prompt": "...",
  "execution_id": "EXEC-..."
}
```

Paste `chatgpt_prompt` into the ChatGPT conversation that has the ccc-mcp connection selected. SA is read-only: it reads context and files, then calls `submit_agent_report` with that execution ID. It must not write `hello.py` or claim that it ran host tests.

## 4. Complete SD/RD, host testing, and REVIEW

After the SA report, the PM refines context, creates or automatically authorizes the current plan, then dispatches SD/RD. Continue checking:

```bash
ccc-mcp --project chatgpt-e2e-demo task handoff
ccc-mcp --project chatgpt-e2e-demo task status
```

When the handoff role is `SD/RD`, paste its new `chatgpt_prompt` into ChatGPT. SD/RD must use `write_task_file` to create `hello.py` and `test_hello.py`, then submit its report. Chat text is not a replacement for file writes or a report.

After the PM refines the SD/RD report, ccc-mcp runs the configured pytest command on the **host** before dispatching REVIEW. The next handoff is REVIEW; give its prompt to ChatGPT so it can inspect files and evidence read-only, then submit its REVIEW report.

The observed run had this event sequence:

```text
CREATED → ANALYZING (ChatGPT / SA)
→ AWAITING_PM_DECISION → APPROVED
→ IMPLEMENTING (ChatGPT / SD/RD)
→ AWAITING_PM_REVIEW → REVIEWING (ChatGPT / REVIEW)
→ AWAITING_PM_REVIEW → COMPLETED
```

Do not assume any stage completed because ChatGPT replied. Use `task status` or `task handoff` after every report.

## 5. Read the final acceptance evidence

```bash
ccc-mcp --project chatgpt-e2e-demo task status | tee evidence/03-final-status.json
ccc-mcp --project chatgpt-e2e-demo task show
```

The important final output in the real run was equivalent to:

```jsonc
{
  "state": "COMPLETED",
  "brief": {
    "tests": {
      "executed_by_host": true,
      "passed": true,
      "no_tests_collected": false
    },
    "verification": {
      "agent_claim": "complete",
      "host_verification": "passed",
      "reverted_due_to_host_failure": false
    }
  }
}
```

`task show` should also list the three interactive executions: ChatGPT / SA, ChatGPT / SD/RD, and ChatGPT / REVIEW. Under continuous authorization, SD/RD's code location is the project root.

Optionally, after local human confirmation, rerun the test to capture the complete stdout, exit code, and command:

```bash
ccc-mcp --project chatgpt-e2e-demo task test TASK-0001
```

This is not a substitute for the automatic evidence recorded before REVIEW. It is useful as a final human check or screenshot.

## 6. What to do on failure

If `brief.tests.no_tests_collected` is `true`, or `host_verification` is `failed`, do **not** present the run as a successful README example. ccc-mcp should return the workflow to SD/RD. Run:

```bash
ccc-mcp --project chatgpt-e2e-demo task handoff
```

Give the new SD/RD prompt to ChatGPT and ask it to add or correct pytest-collectable tests. Once it submits a new report, let the workflow return through REVIEW. Only a new host pass changes `host_verification` to `passed`.

## README screenshot checklist

Every screenshot must come from the **same completed task**. Redact tunnel IDs, secret keys, authorization IDs, personal account details, internal paths, and any requirement you do not want public.

1. `01-profile.png`: `config show`, with all five roles on ChatGPT and `profile: chatgpt-only`.
2. `02-handoff.png`: `task handoff`, showing `CHATGPT_HANDOFF_PENDING`, the role, and the handoff prompt.
3. `03-host-tests-passed.png`: final `task status` or `task show`, showing `COMPLETED`, `executed_by_host: true`, `passed: true`, and `host_verification: passed`.

Keep screenshots 1 and 3 in the README. This document and the raw JSON in `evidence/` are the complete, traceable record. A public ChatGPT share link or a chat screenshot is never the sole acceptance record.

Further reading: [Architecture specification](11-architecture-spec.md).
