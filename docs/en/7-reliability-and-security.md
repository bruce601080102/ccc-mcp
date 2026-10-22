# 7. Reliability and security boundaries

ccc-mcp treats an agent report as an input, not proof. Task state, source hashes, diffs, native CLI audit data, and host-executed test evidence are stored separately and drive completion.

## Reliability guarantees

- A committed context patch creates a durable pending-dispatch pointer before the next role runs. `task resume` retries that exact decision.
- A failing or zero-test host result prevents REVIEW from completing the task, even if the reviewer says no revision is needed.
- Quota exhaustion is classified separately from ordinary execution errors. Only the former may start one configured fallback; fallback loops are prevented.
- Context packages have a visible budget policy: optional Skill bodies, then compressible history, then a hard refusal to dispatch.

## Authorization and execution

`project authorize` permits writes inside the registered project plus configured test execution. ccc-mcp may create a project venv and install dependencies found in `requirements.txt`, `pyproject.toml`, or `package.json`; package install hooks can execute code. Review dependency diffs.

Codex roles use OS-enforced `read-only` or `workspace-write` sandbox modes. Claude Code roles are restricted to selected tools and a worktree, but do **not** currently have the same OS-enforced boundary. Run ccc-mcp under a least-privileged OS account and do not represent Claude containment as verified sandboxing.

All project, task, and evidence paths are validated to resist traversal. Persisted evidence is checked by `ccc-mcp --project <id> verify`.
