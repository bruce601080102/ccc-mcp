import argparse
import json
from pathlib import Path

import portalocker

from .adapters.agents import ADAPTERS, FakeAgentAdapter
from .adapters.git import git, tracked_snapshot, working_snapshot, worktree
from .infra.errors import AgentCancelled, AgentTimeout, BridgeError
from .infra.security import canonical, comment_check, redact, reject_secrets, same_text_content
from .infra.telemetry import audit_scope, is_quota_error
from .service import CCCMCP


def record_fallback_outcome(bridge, execution_id, fallback_info):
    """Attach whether/why a quota fallback started onto the already-finished execution's
    own result, instead of that information disappearing after finish_execution commits."""
    row = bridge.store.one(
        "SELECT project_id,result FROM agent_executions WHERE id=?", (execution_id,)
    )
    with bridge.lock(row["project_id"]):
        with bridge.store.transaction() as conn:
            current = conn.execute(
                "SELECT result FROM agent_executions WHERE id=?", (execution_id,)
            ).fetchone()
            result = json.loads(current["result"]) if current["result"] else {}
            result["fallback"] = fallback_info
            conn.execute(
                "UPDATE agent_executions SET result=? WHERE id=?", (canonical(result), execution_id)
            )


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
        audit = {
            "provider": row["agent"],
            "source": "native_cli" if row["adapter"] == "real" else "fake",
            "cli_invoked": False,
            "requested": json.loads(row["options"]).get("agent_settings", {}),
            "usage": {"available": False, "reason": "No CLI usage metadata returned"},
        }
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
                if options.get("workspace_mode", "worktree") == "project":
                    cwd = bridge.prepare_project_workspace(project_id, task_id, execution_id)
                else:
                    cwd = worktree(
                        bridge.home,
                        root,
                        project_id,
                        task_id,
                        bridge.get_project_settings(project_id)["worktree_dir"],
                    )
                before = tracked_snapshot(cwd)
                bridge.store.execute(
                    "UPDATE agent_executions SET worktree=? WHERE id=?", (str(cwd), execution_id)
                )
                package["repository_reference"] = str(cwd)
            elif row["role"] == "REVIEW":
                prior = bridge.store.one(
                    "SELECT project_id,task_id,options,worktree FROM agent_executions WHERE project_id=? AND task_id=? "
                    "AND worktree IS NOT NULL ORDER BY created DESC LIMIT 1",
                    (project_id, task_id),
                )
                if prior:
                    expected = bridge.execution_workspace(prior)
                    if Path(prior["worktree"]) != expected:
                        raise BridgeError("Invalid stored worktree path")
                    cwd = expected
            if row["adapter"] == "fake":
                adapter = FakeAgentAdapter(options["scenario"], options["delay"])
            elif row["agent"] in ADAPTERS:
                adapter = ADAPTERS[row["agent"]]()
            else:
                raise BridgeError(f"No adapter registered for provider: {row['agent']}")
            with audit_scope(audit):
                report = adapter.run(
                    package, row["instruction"], cwd, options["timeout"], cancelled
                )
            reject_secrets(report)
            extras = {
                "invocation": audit,
                "log": {
                    "adapter": row["adapter"],
                    "role": row["role"],
                    "structured_output_valid": True,
                },
                "test_evidence": {
                    "executed_by_host": False,
                    "reason": "Fake simulation"
                    if row["adapter"] == "fake"
                    else "Not yet run: advance_project auto-runs a configured test_command "
                    "under project authorization, or run `ccc-mcp task test` manually",
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
                extras["changed_files"] = [
                    p for p in before if not same_text_content(before[p], after.get(p))
                ]
                extras["line_ending_only_files"] = [
                    p
                    for p in before
                    if before[p] != after.get(p) and same_text_content(before[p], after.get(p))
                ]
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
            bridge.finish_execution(
                execution_id, error=str(exc), state="CANCELLED", extras={"invocation": audit}
            )
        except AgentTimeout as exc:
            bridge.finish_execution(
                execution_id,
                error=str(exc),
                state="FAILED",
                extras={"failure_kind": "TIMEOUT", "invocation": audit},
            )
        except Exception as exc:
            quota = is_quota_error(str(exc) + " " + getattr(exc, "stdout", ""))
            bridge.finish_execution(
                execution_id,
                error=redact(f"{type(exc).__name__}: {exc}")[:2000],
                state="FAILED",
                extras={
                    "invocation": audit,
                    "failure_kind": "QUOTA_EXHAUSTED" if quota else "ERROR",
                },
            )
            # Only attempted now, after finish_execution frees this task's "active
            # agent" slot — dispatch_agent would otherwise refuse the fallback while
            # this (still QUEUED/RUNNING) execution occupies it.
            fallback_agent = json.loads(row["options"]).get("agent_settings", {}).get(
                "quota_fallback"
            )
            already_fallback = bool(json.loads(row["options"]).get("fallback_from"))
            if quota and fallback_agent:
                if fallback_agent == row["agent"]:
                    fallback_info = {
                        "attempted": False,
                        "target": fallback_agent,
                        "reason": "Configured fallback equals the primary provider",
                    }
                elif already_fallback:
                    fallback_info = {
                        "attempted": False,
                        "target": fallback_agent,
                        "reason": "This execution was itself a fallback; not chaining further",
                    }
                else:
                    try:
                        dispatched = bridge.dispatch_agent(
                            row["project_id"],
                            row["task_id"],
                            fallback_agent,
                            row["role"],
                            row["context_version"],
                            row["instruction"],
                            "real",
                            fallback_from=execution_id,
                        )
                        fallback_info = {
                            "attempted": True,
                            "target": fallback_agent,
                            "dispatched": True,
                            "fallback_execution_id": dispatched["execution_id"],
                        }
                    except Exception as fallback_exc:
                        # Keep the failed execution; stale context/approval must not be
                        # bypassed. Catch everything (not just BridgeError): a lock timeout
                        # or any other failure here must not crash this worker subprocess
                        # silently (stdout/stderr are discarded) and strand a QUEUED
                        # fallback execution nobody can promote. Unlike before, the reason
                        # is recorded instead of being dropped.
                        fallback_info = {
                            "attempted": True,
                            "target": fallback_agent,
                            "dispatched": False,
                            "failed_reason": redact(
                                f"{type(fallback_exc).__name__}: {fallback_exc}"
                            )[:500],
                        }
                record_fallback_outcome(bridge, execution_id, fallback_info)
    finally:
        lease.release()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--home", required=True)
    parser.add_argument("--execution", required=True)
    args = parser.parse_args()
    from .core.context import identifier

    run_execution(CCCMCP(args.home), identifier(args.execution))


if __name__ == "__main__":
    main()
