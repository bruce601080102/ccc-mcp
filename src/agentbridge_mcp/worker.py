import argparse
import json
from pathlib import Path

import portalocker

from .agents import ClaudeCodeAdapter, CodexAdapter, FakeAgentAdapter
from .errors import AgentCancelled, AgentTimeout, BridgeError
from .git import git, tracked_snapshot, working_snapshot, worktree
from .security import comment_check, redact, reject_secrets, safe_path
from .service import AgentBridge


def run_execution(bridge, execution_id):
    lockfile = bridge.home / "locks" / f"{execution_id}.lock"
    lockfile.parent.mkdir(exist_ok=True)
    try:
        lease = portalocker.Lock(lockfile, timeout=0)
        lease.acquire()
    except portalocker.exceptions.LockException:
        return
    try:
        with bridge.store.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM agent_executions WHERE id=?", (execution_id,)
            ).fetchone()
            if not row or row["status"] != "QUEUED":
                return
            row = dict(row)
            conn.execute("UPDATE agent_executions SET status='RUNNING' WHERE id=?", (execution_id,))
        try:
            project_id, task_id = row["project_id"], row["task_id"]
            package = json.loads(row["package"])
            options = json.loads(row["options"])
            root = Path(bridge.project(project_id)["root"])
            if bridge.latest(project_id)["version"] != row["context_version"]:
                raise BridgeError(
                    "Context changed before worker start; dispatch again after review"
                )

            def cancelled():
                return bool(
                    bridge.store.one(
                        "SELECT cancel_requested FROM agent_executions WHERE id=?", (execution_id,)
                    )["cancel_requested"]
                )

            if cancelled():
                raise AgentCancelled("Execution cancelled before start")
            implementation = row["role"] not in {"SA", "REVIEW"}
            cwd = root
            before = {}
            if implementation:
                bridge.approved_plan(project_id, task_id)
                cwd = worktree(bridge.home, root, project_id, task_id)
                before = tracked_snapshot(cwd)
                bridge.store.execute(
                    "UPDATE agent_executions SET worktree=? WHERE id=?", (str(cwd), execution_id)
                )
                package["repository_reference"] = str(cwd)
            elif row["role"] == "REVIEW":
                prior = bridge.store.one(
                    "SELECT worktree FROM agent_executions WHERE project_id=? AND task_id=? "
                    "AND worktree IS NOT NULL ORDER BY created DESC LIMIT 1",
                    (project_id, task_id),
                )
                if prior:
                    expected = safe_path(bridge.home, f"worktrees/{project_id}/{task_id}")
                    if Path(prior["worktree"]) != expected:
                        raise BridgeError("Invalid stored worktree path")
                    cwd = expected
            if row["adapter"] == "fake":
                adapter = FakeAgentAdapter(options["scenario"], options["delay"])
            else:
                adapter = CodexAdapter() if row["agent"] == "codex" else ClaudeCodeAdapter()
            report = adapter.run(package, row["instruction"], cwd, options["timeout"], cancelled)
            reject_secrets(report)
            extras = {
                "log": {
                    "adapter": row["adapter"],
                    "role": row["role"],
                    "structured_output_valid": True,
                },
                "test_evidence": {
                    "executed_by_host": False,
                    "reason": "Fake simulation"
                    if row["adapter"] == "fake"
                    else "Tests require separately authorized `agentbridge task test`",
                },
            }
            state = None
            if implementation:
                after = working_snapshot(cwd)
                check = comment_check(before, after)
                extras["comment_check"] = check
                # Git diff is observed evidence; add untracked files explicitly so new code is visible.
                diff = git(cwd, "diff", "HEAD", "--no-ext-diff", "--no-textconv").stdout.decode(
                    "utf-8", "replace"
                )
                extras["untracked_files"] = [p for p in after if p not in before]
                extras["changed_files"] = [p for p in before if before[p] != after.get(p)]
                reject_secrets(diff)
                with bridge.store.transaction() as conn:
                    path = bridge.save_artifact(
                        conn, project_id, task_id, "CHANGES.diff", execution_id, diff
                    )
                extras["diff_reference"] = path
                extras["new_file_contents"] = {
                    p: redact(after[p].decode("utf-8", "replace"))
                    for p in extras["untracked_files"]
                    if len(after[p]) < 200_000
                }
                if not check["ok"]:
                    state = "BLOCKED"
                if bridge.latest(project_id)["version"] != row["context_version"]:
                    state = "BLOCKED"
                    extras["context_changed_during_execution"] = True
            bridge.finish_execution(execution_id, report=report, state=state, extras=extras)
        except AgentCancelled as exc:
            bridge.finish_execution(execution_id, error=str(exc), state="CANCELLED")
        except AgentTimeout as exc:
            bridge.finish_execution(
                execution_id, error=str(exc), state="FAILED", extras={"failure_kind": "TIMEOUT"}
            )
        except Exception as exc:
            bridge.finish_execution(
                execution_id, error=redact(f"{type(exc).__name__}: {exc}")[:2000], state="FAILED"
            )
    finally:
        lease.release()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", required=True)
    parser.add_argument("--execution", required=True)
    args = parser.parse_args()
    from .context import identifier

    run_execution(AgentBridge(args.home), identifier(args.execution))


if __name__ == "__main__":
    main()
