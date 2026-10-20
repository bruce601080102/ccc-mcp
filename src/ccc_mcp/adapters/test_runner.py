import sys
import uuid
from pathlib import Path

from ..core.approval import confirm_local
from ..infra.errors import AgentProcessError, BridgeError
from ..infra.security import canonical, redact
from .agents.base import run_process
from .git import revision

DEFAULT_TEST_COMMAND = [sys.executable, "-m", "pytest", "-q"]


def _implementation_workspace(bridge, project_id, task_id):
    row = bridge.store.one(
        "SELECT * FROM agent_executions WHERE project_id=? AND task_id=? "
        "AND worktree IS NOT NULL ORDER BY created DESC LIMIT 1",
        (project_id, task_id),
    )
    if not row:
        raise BridgeError("No implementation worktree")
    cwd = bridge.execution_workspace(row)
    if Path(row["worktree"]) != cwd:
        raise BridgeError("Invalid worktree")
    return cwd


def record_test_evidence(bridge, project_id, task_id, result):
    """Persist `result` as the task's current TEST-RESULT.md, including a "not executed"
    determination — so get_task_status always reflects the latest reason, not a stale
    evidence file left over from an earlier, unrelated execution."""
    with bridge.store.transaction() as conn:
        bridge.save_artifact(
            conn,
            project_id,
            task_id,
            "TEST-RESULT.md",
            "TEST-" + uuid.uuid4().hex[:16],
            "# Observed test result\n\n" + canonical(result) + "\n",
        )
    bridge.sync_documents(project_id)
    return result


def execute_test_command(bridge, project_id, task_id, command, identity, cwd=None):
    """Run `command` (an argv, never a shell string) in the task's implementation
    workspace and persist it as TEST-RESULT.md evidence. Shared by the human-confirmed
    `ccc-mcp task test` path and the automatic project-authorize-consented path that
    runs before REVIEW (core.tasks.TaskHub.run_configured_test_command)."""
    cwd = cwd or _implementation_workspace(bridge, project_id, task_id)
    try:
        stdout, stderr = run_process(command, cwd, "", 600, lambda: False)
        result = {"passed": True, "stdout": redact(stdout), "stderr": stderr}
    except AgentProcessError as exc:
        result = {
            "passed": False,
            "returncode": exc.returncode,
            "stdout": exc.stdout,
            "stderr": exc.stderr,
        }
    except BridgeError as exc:
        result = {"passed": False, "error": redact(str(exc))}
    result |= {
        "executed_by_host": True,
        "identity": identity,
        "git_revision": revision(cwd),
        "command": command,
    }
    return record_test_evidence(bridge, project_id, task_id, result)


def run_tests_local(bridge, project_id, task_id):
    bridge.approved_plan(project_id, task_id)
    cwd = _implementation_workspace(bridge, project_id, task_id)
    command = bridge.get_project_settings(project_id).get("test_command") or DEFAULT_TEST_COMMAND
    identity = confirm_local(
        f"在 {cwd} 執行 {' '.join(command)}\n測試會執行儲存庫程式碼，請先審閱測試內容。",
        task_id,
    )
    # Serialize with agent activity for this task using the project lock.
    with bridge.lock(project_id):
        live = bridge.store.one(
            "SELECT 1 FROM agent_executions WHERE project_id=? AND task_id=? "
            "AND status IN ('QUEUED','RUNNING')",
            (project_id, task_id),
        )
        if live:
            raise BridgeError("Wait for the active agent before running tests")
        return execute_test_command(bridge, project_id, task_id, command, identity, cwd)
