import sys
import uuid
from pathlib import Path

from ..core.approval import confirm_local
from ..infra.errors import AgentProcessError, BridgeError
from ..infra.security import canonical, redact
from .agents.base import run_process
from .git import revision


def run_tests_local(bridge, project_id, task_id):
    bridge.approved_plan(project_id, task_id)
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
    identity = confirm_local(
        f"在 {cwd} 執行 {sys.executable} -m pytest -q\n測試會執行儲存庫程式碼，請先審閱測試內容。",
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
        try:
            stdout, stderr = run_process(
                [sys.executable, "-m", "pytest", "-q"], cwd, "", 600, lambda: False
            )
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
            "command": [sys.executable, "-m", "pytest", "-q"],
        }
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
