"""Evaluation Engine Hub: the thin seam between TaskHub/ConfigurationHub's durable
data and the evaluation_engine package's pure scoring logic (see that package's
__init__.py docstring on the dependency direction). Mixed into CCCMCP in service.py
exactly like every other *Hub class — never a subclass of TaskHub, never imported by
it, so this feature stays removable without touching the core workflow (brief #1/#2).
"""

import logging

from ..evaluation_engine import evaluation_service
from ..infra.errors import BridgeError

logger = logging.getLogger(__name__)


class EvaluationHub:
    # Every public method here takes plain positional-or-keyword parameters (no
    # keyword-only `*,`) so server.py's `call(fn, *args)` helper — which only ever
    # forwards positional args to asyncio.to_thread — can call them directly.

    # No self.lock(project_id) here: get_task_result/_latest_test_result and friends
    # already take it internally (see context.py's lock()), which is a plain, non-
    # reentrant portalocker file lock — wrapping these calls in a second acquisition
    # from the same process deadlocks instead of nesting. evaluate_execution's own
    # evaluations/reputation_events write is independently atomic via
    # bridge.store.transaction(), which is a different (SQLite-level) mechanism.

    def evaluate_task(self, project_id, task_id=None, role=None, task_type="general", force=False):
        resolved = task_id or self.project_workstream_task(project_id)
        if not self.get_project_settings(project_id)["evaluation_engine"]["enabled"]:
            raise BridgeError(
                "evaluation_engine is disabled for this project; "
                "enable it with `ccc-mcp config evaluation-engine on` first"
            )
        return evaluation_service.evaluate_task(
            self, project_id, resolved, role=role, task_type=task_type,
            trigger="manual", force=force,
        )

    def reevaluate_execution(self, project_id, task_id, execution_id, task_type="general"):
        """Explicit re-evaluation that always adds a new version (brief #4.5), unlike
        evaluate_task's default skip-if-already-evaluated behavior."""
        if not self.get_project_settings(project_id)["evaluation_engine"]["enabled"]:
            raise BridgeError(
                "evaluation_engine is disabled for this project; "
                "enable it with `ccc-mcp config evaluation-engine on` first"
            )
        return evaluation_service.evaluate_execution(
            self, project_id, task_id, execution_id, task_type=task_type, trigger="manual"
        )

    def get_task_evaluations(self, project_id, task_id=None, role=None):
        resolved = task_id or self.project_workstream_task(project_id)
        return evaluation_service.get_evaluations(self, project_id, resolved, role=role)

    def export_scoring_cases(self, project_id, task_ids=None):
        """Read-only: the inputs `ccc-mcp validate-scoring` needs, one per task."""
        return evaluation_service.export_cases(self, project_id, task_ids)

    def get_agent_reliability(self, agent=None, role=None, task_type=None):
        """Global across every project on this home (see evaluation_service.get_reliability)."""
        return evaluation_service.get_reliability(self, agent=agent, role=role, task_type=task_type)

    def maybe_auto_evaluate(self, execution_id, after_tests=False):
        """Call once, right after finish_execution returns for ANY execution (worker.py,
        alongside advance_meeting_clarification/advance_score_round) — a no-op unless
        evaluation_engine.auto_evaluate is on for this project. Never lets an evaluation
        bug affect the already-committed execution/task state (brief #10): every
        failure is swallowed and logged, not raised, exactly like record_fallback_outcome's
        own best-effort-after-the-fact pattern in worker.py.
        """
        row = self.store.one(
            "SELECT project_id,task_id,status,role FROM agent_executions WHERE id=?", (execution_id,)
        )
        if not row or row["status"] != "SUCCEEDED":
            return
        try:
            settings = self.get_project_settings(row["project_id"])["evaluation_engine"]
        except BridgeError:
            return
        if not settings["enabled"] or not settings["auto_evaluate"]:
            return
        if row["role"] in {"SD", "RD", "SD/RD"} and not after_tests:
            # Implementation tests are run at the following PM handoff. Do not
            # attach a previous round's test result to this execution.
            return
        try:
            if self.store.one(
                "SELECT 1 FROM evaluations WHERE source_execution_id=?", (execution_id,)
            ):
                return
            evaluation_service.evaluate_execution(
                self, row["project_id"], row["task_id"], execution_id,
                task_type="general", trigger="auto",
            )
        except Exception:
            logger.exception("Auto-evaluation failed for execution %s; task data is unaffected", execution_id)
