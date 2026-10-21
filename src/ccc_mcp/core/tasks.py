import json
import os
import subprocess
import sys
import uuid
from contextlib import nullcontext
from datetime import UTC, datetime

import portalocker

from ..adapters.storage import artifact, decode, event, now
from ..infra.config import metadata_name, setting_env
from ..infra.errors import BridgeError, PermissionDenied, VersionConflict
from ..infra.security import canonical, digest, reject_secrets
from .context import identifier


def attribution(language, agent, role=None):
    """One readable line so a human opening a saved .md knows who produced it."""
    who = agent if role is None else f"{agent}（{role}）" if language != "en" else f"{agent} ({role})"
    return {
        "zh-TW": f"> 提供者：{who}\n\n",
        "ch": f"> 提供者：{who}\n\n",
        "en": f"> Provided by: {who}\n\n",
    }[language]


STATES = {
    "CREATED",
    "ANALYZING",
    "AWAITING_PM_DECISION",
    "AWAITING_USER_APPROVAL",
    "APPROVED",
    "IMPLEMENTING",
    "REVIEWING",
    "AWAITING_PM_REVIEW",
    "COMPLETED",
    "FAILED",
    "CANCELLED",
    "REJECTED",
    "BLOCKED",
    "NEEDS_REVISION",
}
TRANSITIONS = {
    "CREATED": {"ANALYZING", "AWAITING_PM_DECISION"},
    "ANALYZING": {"AWAITING_PM_DECISION"},
    "AWAITING_PM_DECISION": {
        "ANALYZING",
        "AWAITING_USER_APPROVAL",
        "APPROVED",
        "REVIEWING",
    },
    "AWAITING_USER_APPROVAL": {"APPROVED", "REJECTED", "AWAITING_PM_DECISION"},
    "APPROVED": {"IMPLEMENTING", "ANALYZING", "AWAITING_PM_DECISION"},
    "IMPLEMENTING": {"AWAITING_PM_REVIEW", "REVIEWING"},
    "REVIEWING": {"AWAITING_PM_REVIEW", "NEEDS_REVISION"},
    "AWAITING_PM_REVIEW": {
        "REVIEWING",
        "ANALYZING",
        "IMPLEMENTING",
        "NEEDS_REVISION",
        "COMPLETED",
        "AWAITING_PM_DECISION",
    },
    "NEEDS_REVISION": {"IMPLEMENTING", "ANALYZING", "AWAITING_PM_DECISION", "REVIEWING"},
    "FAILED": {"ANALYZING", "IMPLEMENTING", "REVIEWING", "AWAITING_PM_DECISION"},
    "BLOCKED": {"ANALYZING", "IMPLEMENTING", "REVIEWING", "AWAITING_PM_DECISION"},
    "CANCELLED": set(),
    "COMPLETED": set(),
    "REJECTED": set(),
}

def _dependency_summary(test_result):
    """Terse view of the auto dependency-install step for brief.tests (full stdout/
    stderr stays in the raw TEST-RESULT.md artifact, same pattern as test output)."""
    deps = (test_result or {}).get("dependencies")
    if not deps:
        return None
    return {
        "detected": deps.get("detected", False),
        "manifest": deps.get("manifest"),
        "installed": deps.get("installed"),
        "venv": bool(deps.get("venv_scripts_dir")),
        "reason": deps.get("reason"),
    }


# Terminal task states map onto themselves; every other combination is derived
# below from signals that already exist (task state, the latest execution's
# status, whether it's an orphaned worker, and unrefined-report backlog).
_TERMINAL_WAITING_ON = {"COMPLETED", "CANCELLED", "REJECTED"}


def waiting_on(
    state, execution_status, orphaned, needs_pm_refinement, pending_dispatch=False,
    chatgpt_handoff=False,
):
    """Collapse task.state + execution status + context_sync into one readable value.

    A PM or operator otherwise has to cross-reference several separate fields to answer
    "what is this task waiting on right now"; this is purely derived, read-only, and
    never stored, so it cannot drift from the state machine it summarizes.
    """
    if state in _TERMINAL_WAITING_ON:
        return state
    if pending_dispatch:
        # Context was already committed for the prior round, but the next role was
        # never confirmed dispatched (crash, worker spawn failure, stale version).
        # Nothing is running; an operator/PM action (resume_pending_dispatch) is needed.
        return "DISPATCH_RETRY_NEEDED"
    if chatgpt_handoff:
        # The *current* interactive ChatGPT PM conversation must act as the role
        # (write_task_file/submit_agent_report) — distinct from an autonomous CLI
        # worker that just needs waiting for.
        return "CHATGPT_HANDOFF_PENDING"
    if execution_status in {"QUEUED", "RUNNING"}:
        return "BLOCKED" if orphaned else "AGENT_RUNNING"
    if needs_pm_refinement:
        return "PM_REFINEMENT"
    if state == "AWAITING_USER_APPROVAL":
        return "USER_APPROVAL"
    if state in {"AWAITING_PM_DECISION", "AWAITING_PM_REVIEW", "NEEDS_REVISION"}:
        return "PM_DECISION"
    if state in {"FAILED", "BLOCKED"}:
        return state
    return "PM_DECISION"


class TaskHub:
    def task(self, project_id, task_id):
        self.project(project_id)
        row = self.store.one(
            "SELECT * FROM tasks WHERE project_id=? AND id=?", (project_id, identifier(task_id))
        )
        if not row:
            raise BridgeError("Task not found")
        return decode(row, "acceptance")

    def list_tasks(self, project_id):
        self.project(project_id)
        return [
            decode(row, "acceptance")
            for row in self.store.all(
                "SELECT * FROM tasks WHERE project_id=? ORDER BY created", (project_id,)
            )
        ]

    def project_workstream_task(self, project_id):
        tasks = self.list_tasks(project_id)
        if not tasks:
            raise BridgeError("Project workstream has not started")
        return tasks[-1]["id"]

    def reconcile_authorized_workstream(self, project_id):
        """Complete already-refined review rounds without a second user acceptance ceremony."""
        if self.get_project_access(project_id)["mode"] != "read-write":
            return {"completed_workstreams": []}
        pending = {
            item["execution_id"] for item in self.context_sync_status(project_id)["pending_reports"]
        }
        consumed = set()
        for row in self.store.all(
            "SELECT metadata FROM context_versions WHERE project_id=?", (project_id,)
        ):
            consumed.update(json.loads(row["metadata"]).get("source_execution_ids", []))
        completed = []
        with self.lock(project_id):
            with self.store.transaction() as conn:
                tasks = conn.execute(
                    "SELECT id,state FROM tasks WHERE project_id=?", (project_id,)
                ).fetchall()
                for task in tasks:
                    if task["state"] != "AWAITING_PM_REVIEW":
                        continue
                    review = conn.execute(
                        "SELECT id,status,result FROM agent_executions WHERE project_id=? "
                        "AND task_id=? AND role='REVIEW' ORDER BY created DESC LIMIT 1",
                        (project_id, task["id"]),
                    ).fetchone()
                    saved = json.loads(review["result"]) if review and review["result"] else {}
                    if (
                        review
                        and review["status"] == "SUCCEEDED"
                        and review["id"] in consumed
                        and review["id"] not in pending
                        and not (saved.get("report") or {}).get("needs_revision")
                    ):
                        self._transition(
                            conn,
                            project_id,
                            task["id"],
                            "COMPLETED",
                            "Existing successful review was already refined into shared Context",
                        )
                        completed.append(task["id"])
            if completed:
                self.sync_documents(project_id)
        return {"completed_workstreams": completed}

    def _transition(self, conn, project_id, task_id, new_state, reason):
        row = conn.execute(
            "SELECT state FROM tasks WHERE project_id=? AND id=?", (project_id, task_id)
        ).fetchone()
        if not row:
            raise BridgeError("Task not found")
        old = row["state"]
        if old == new_state:
            return
        allowed = TRANSITIONS.get(old, set()) | (
            {"FAILED", "CANCELLED", "BLOCKED"}
            if old not in {"COMPLETED", "CANCELLED", "REJECTED"}
            else set()
        )
        if new_state not in allowed:
            raise BridgeError(f"Illegal task transition: {old} -> {new_state}")
        conn.execute(
            "UPDATE tasks SET state=? WHERE project_id=? AND id=?", (new_state, project_id, task_id)
        )
        event(conn, project_id, task_id, old, new_state, reason)

    def create_task(self, project_id, requirement, acceptance=None, task_id=None):
        reject_secrets([requirement, acceptance])
        if not requirement.strip():
            raise BridgeError("Requirement is required")
        with self.lock(project_id):
            self.project(project_id)
            existing = self.list_tasks(project_id)
            continuous = self.get_project_access(project_id)["mode"] == "read-write"
            reuse = continuous and task_id is None and bool(existing)
            if reuse and self.context_sync_status(project_id)["pending_reports"]:
                raise PermissionDenied(
                    "PM must compact pending agent reports before starting the next project round"
                )
            task_id = identifier(
                task_id or (existing[-1]["id"] if reuse else f"TASK-{len(existing) + 1:04d}")
            )
            language = self.get_project_settings(project_id)["language"]
            acceptance = acceptance or []
            requirement_content = (
                f"# 需求\n\n{attribution(language, 'PM')}{requirement}\n\n## 驗收條件\n{canonical(acceptance)}\n"
                if language == "zh-TW"
                else f"# 需求\n\n{attribution(language, 'PM')}{requirement}\n\n## 验收条件\n{canonical(acceptance)}\n"
                if language == "ch"
                else f"# Requirement\n\n{attribution(language, 'PM')}{requirement}\n\n## Acceptance\n{canonical(acceptance)}\n"
            )
            with self.store.transaction() as conn:
                if reuse:
                    if conn.execute(
                        "SELECT 1 FROM agent_executions WHERE project_id=? AND task_id=? "
                        "AND status IN ('QUEUED','RUNNING')",
                        (project_id, task_id),
                    ).fetchone():
                        raise BridgeError("Project workstream already has an active agent")
                    old_state = existing[-1]["state"]
                    conn.execute(
                        "UPDATE tasks SET requirement=?,acceptance=?,state='CREATED',created=? "
                        "WHERE project_id=? AND id=?",
                        (requirement, canonical(acceptance), now(), project_id, task_id),
                    )
                    conn.execute(
                        "UPDATE approvals SET status='INVALIDATED' WHERE project_id=? "
                        "AND task_id=? AND status IN ('APPROVED','PENDING')",
                        (project_id, task_id),
                    )
                    event(
                        conn,
                        project_id,
                        task_id,
                        old_state,
                        "CREATED",
                        "PM started a new project work round",
                    )
                else:
                    if conn.execute(
                        "SELECT 1 FROM tasks WHERE project_id=? AND id=?", (project_id, task_id)
                    ).fetchone():
                        raise BridgeError("Task already exists")
                    conn.execute(
                        "INSERT INTO tasks VALUES(?,?,?,?,?,?)",
                        (task_id, project_id, requirement, canonical(acceptance), "CREATED", now()),
                    )
                    event(conn, project_id, task_id, None, "CREATED", "PM created requirement")
                immutable = (
                    f"{metadata_name(self.project(project_id)['root'])}/tasks/{task_id}/"
                    f"requirements/REQ-{uuid.uuid4().hex[:16]}.md"
                )
                artifact(
                    conn,
                    project_id,
                    immutable,
                    requirement_content,
                )
                conn.execute(
                    "INSERT INTO artifacts VALUES(?,?,?,?,?) ON CONFLICT(project_id,path) DO UPDATE SET "
                    "content=excluded.content,sha256=excluded.sha256,created=excluded.created",
                    (
                        project_id,
                        f"{metadata_name(self.project(project_id)['root'])}/tasks/{task_id}/REQUIREMENT.md",
                        requirement_content,
                        digest(requirement_content.encode()),
                        now(),
                    ),
                )
            self.sync_documents(project_id)
        result = self.task(project_id, task_id)
        result["workstream"] = continuous
        result["reused"] = reuse
        return result

    def start_project_request(self, project_id, requirement, acceptance=None):
        """One user-facing entry point: create/reuse the workstream and dispatch SA."""
        if self.get_project_access(project_id)["mode"] != "read-write":
            raise PermissionDenied(
                f"Project is read-only. Run ccc-mcp project authorize {project_id} locally once."
            )
        task = self.create_task(project_id, requirement, acceptance)
        dispatch = self.dispatch_agent(
            project_id,
            task["id"],
            "auto",
            "SA",
            self.latest(project_id)["version"],
            requirement,
        )
        return {"workstream": task, "sa_execution": dispatch}

    def _pending_dispatch_row(self, project_id, task_id):
        return self.store.one(
            "SELECT * FROM pending_dispatch WHERE project_id=? AND task_id=?",
            (project_id, task_id),
        )

    def _clear_pending_dispatch(self, project_id, task_id):
        self.store.execute(
            "DELETE FROM pending_dispatch WHERE project_id=? AND task_id=?",
            (project_id, task_id),
        )

    def _dispatch_or_adopt(self, project_id, task_id, role, instruction, adapter, fake_scenario):
        """Dispatch `role`, unless a resume attempt already dispatched it and only
        crashed before the pointer could be cleared — then adopt that live execution
        instead of dispatching (and losing track of) a second one."""
        live = self.store.one(
            "SELECT id,role FROM agent_executions WHERE project_id=? AND task_id=? "
            "AND status IN ('QUEUED','RUNNING') ORDER BY created DESC LIMIT 1",
            (project_id, task_id),
        )
        if live and live["role"] == role:
            return {"task_id": task_id, "execution_id": live["id"], "status": "QUEUED", "adopted": True}
        return self.dispatch_agent(
            project_id,
            task_id,
            "auto",
            role,
            self.latest(project_id)["version"],
            instruction,
            adapter=adapter,
            fake_scenario=fake_scenario,
        )

    def _review_outcome(self, project_id, task_id):
        """Whether a just-committed REVIEW round is actually done, from durable facts only
        (task state after commit_context_patch, and host-recorded test evidence) — never
        from re-reading the Agent's own report a second time. commit_context_patch already
        refuses to mark the task COMPLETED when host test evidence shows a failure even if
        REVIEW itself reported no revision needed (see _host_test_failed); this mirrors
        that same decision so the two can never disagree."""
        if self.task(project_id, task_id)["state"] == "COMPLETED":
            return "completed", None
        if self._host_test_failed(project_id, task_id):
            return (
                "revise",
                "Host-executed tests failed after REVIEW even though REVIEW reported no "
                "revision needed; host test evidence takes precedence. Fix the implementation "
                "so the configured test command passes.",
            )
        if self._host_tests_unverified(project_id, task_id):
            return (
                "revise",
                "The host-executed test command collected zero tests, so nothing about this "
                "change was actually verified; add tests that exercise it before this round "
                "can be accepted.",
            )
        return "revise", "Revise the implementation to address the latest REVIEW findings"

    def _complete_pending_dispatch(self, project_id, pointer):
        """Perform (or retry) exactly the dispatch decision recorded by a prior advance_project
        call. Idempotent: propose_plan no-ops on an identical retry, dispatch_agent's active-
        execution guard rejects a true double-dispatch, and the pointer is cleared only once
        dispatch_agent has actually succeeded — so a failure here always leaves the pointer in
        place for the next resume attempt, never a silently lost or duplicated step."""
        task_id, role = pointer["task_id"], pointer["role"]
        adapter, fake_scenario = pointer["adapter"], pointer["fake_scenario"]
        result = {}
        if role == "SA":
            result["plan"] = self.propose_plan(
                project_id, task_id, "PLAN-A", pointer["plan_content"], pointer["context_version"]
            )
            result["next_execution"] = self._dispatch_or_adopt(
                project_id, task_id, "SD/RD", pointer["plan_content"], adapter, fake_scenario
            )
        elif role in {"SD", "RD", "SD/RD"}:
            test_evidence = self.run_configured_test_command(project_id, task_id)
            result["test_evidence"] = test_evidence
            instruction = "Review the implementation and verify reported evidence. " + (
                f"Host-executed test command {'passed' if test_evidence.get('passed') else 'FAILED'}: "
                f"{' '.join(test_evidence['command'])}."
                if test_evidence["executed_by_host"]
                else f"No host test evidence was collected ({test_evidence['reason']}); "
                "treat completion as unverified."
            )
            result["next_execution"] = self._dispatch_or_adopt(
                project_id, task_id, "REVIEW", instruction, adapter, fake_scenario
            )
        else:
            outcome, instruction = self._review_outcome(project_id, task_id)
            if outcome == "completed":
                result["completed_workstreams"] = json.loads(pointer["committed_json"]).get(
                    "completed_workstreams", []
                )
            else:
                result["next_execution"] = self._dispatch_or_adopt(
                    project_id, task_id, "SD/RD", instruction, adapter, fake_scenario
                )
        # Only reached once every step above has actually succeeded.
        self._clear_pending_dispatch(project_id, task_id)
        return result

    def advance_project(
        self, project_id, patch, plan_content=None, adapter="real", fake_scenario="success"
    ):
        """Commit PM refinement and dispatch the next configured role automatically.

        Resumable by construction: once propose_context_patch/commit_context_patch succeed,
        the decision of what to dispatch next is durably recorded in `pending_dispatch`
        *before* attempting that dispatch. If the dispatch step itself fails or this process
        never returns (crash, worker spawn failure, transient VersionConflict), calling
        advance_project again with the exact same patch — or resume_pending_dispatch with no
        patch at all — resumes from that pointer instead of re-deciding, re-committing the
        Context, or losing the original execution's record.
        """
        if self.get_project_access(project_id)["mode"] != "read-write":
            raise PermissionDenied("Project authorization is required")
        source_ids = patch.get("source_execution_ids", [])
        if len(source_ids) != 1:
            raise BridgeError("Advancement requires exactly one completed Agent execution")
        source_execution_id = source_ids[0]
        execution = self.store.one(
            "SELECT id,task_id,role,status FROM agent_executions WHERE project_id=? AND id=?",
            (project_id, source_execution_id),
        )
        if not execution or execution["status"] != "SUCCEEDED":
            raise BridgeError("Source Agent execution has not succeeded")
        task_id = execution["task_id"]

        pointer = self._pending_dispatch_row(project_id, task_id)
        if pointer:
            if pointer["source_execution_id"] != source_execution_id:
                raise BridgeError(
                    "Task has an unresolved pending dispatch from a previous round "
                    f"(source execution {pointer['source_execution_id']}); call "
                    "resume_pending_dispatch to finish it before advancing a different execution"
                )
            result = {
                "context": json.loads(pointer["committed_json"]),
                "source_execution_id": source_execution_id,
                "resumed": True,
                "note": "Context was already committed in a previous attempt; only the "
                "pending next dispatch was retried. The patch argument was ignored.",
            }
            result.update(self._complete_pending_dispatch(project_id, pointer))
            return result

        pending = {
            item["execution_id"]
            for item in self.context_sync_status(project_id)["pending_reports"]
        }
        if execution["id"] not in pending:
            raise BridgeError("Source Agent execution is not awaiting PM refinement")
        role = execution["role"]
        if role == "SA" and not (plan_content or "").strip():
            raise BridgeError("An SA result requires a reviewed implementation plan")
        if role not in {"SA", "SD", "RD", "SD/RD", "REVIEW"}:
            raise BridgeError("Unsupported project advancement role")
        if role != "SA" and plan_content is not None:
            raise BridgeError("A plan is only accepted after SA")
        proposed = self.propose_context_patch(project_id, patch)
        # The pending_dispatch pointer is written atomically inside commit_context_patch's
        # own transaction (see its docstring) — a crash between "context committed" and
        # "pointer recorded" is exactly the gap that made this unsafe before; now either
        # both land together or neither does.
        committed = self.commit_context_patch(
            project_id,
            proposed["patch_id"],
            next_dispatch={
                "task_id": task_id,
                "source_execution_id": source_execution_id,
                "role": role,
                "plan_content": plan_content,
                "adapter": adapter,
                "fake_scenario": fake_scenario,
            },
        )
        result = {"context": committed, "source_execution_id": execution["id"]}
        pointer = self._pending_dispatch_row(project_id, task_id)
        result.update(self._complete_pending_dispatch(project_id, pointer))
        return result

    def resume_pending_dispatch(self, project_id, task_id=None):
        """Explicitly retry the last interrupted advance_project dispatch without needing
        the original ContextPatch again — everything required was already captured durably
        in `pending_dispatch` at commit time."""
        task_id = task_id or self.project_workstream_task(project_id)
        pointer = self._pending_dispatch_row(project_id, task_id)
        if not pointer:
            return {
                "resumed": False,
                "task_id": task_id,
                "reason": "No pending dispatch is recorded for this task; nothing to resume.",
            }
        result = {
            "context": json.loads(pointer["committed_json"]),
            "source_execution_id": pointer["source_execution_id"],
            "resumed": True,
        }
        result.update(self._complete_pending_dispatch(project_id, pointer))
        return result

    def propose_plan(self, project_id, task_id, plan_id, content, context_version):
        identifier(plan_id)
        reject_secrets(content)
        if not content.strip():
            raise BridgeError("Plan content is required")
        plan_hash = digest(content.encode())
        with self.lock(project_id):
            self.task(project_id, task_id)
            if context_version != self.latest(project_id)["version"]:
                raise VersionConflict("Plan context is stale")
            access = self.get_project_access(project_id)
            latest_plan = self.store.one(
                "SELECT version,context_version,hash FROM plans WHERE project_id=? AND "
                "task_id=? AND id=? ORDER BY version DESC LIMIT 1",
                (project_id, task_id, plan_id),
            )
            if (
                access["mode"] == "read-write"
                and latest_plan
                and latest_plan["context_version"] == context_version
                and latest_plan["hash"] == plan_hash
            ):
                # A retry (e.g. resuming an interrupted advance_project dispatch) must
                # not create a duplicate plan version/approval/decision for the exact
                # same content and context — only the dispatch step needs retrying.
                return {
                    "plan_id": plan_id,
                    "version": latest_plan["version"],
                    "hash": plan_hash,
                    "context_version": context_version,
                    "status": "APPROVED",
                    "idempotent": True,
                }
            with self.store.transaction() as conn:
                if conn.execute(
                    "SELECT 1 FROM agent_executions WHERE project_id=? AND task_id=? "
                    "AND status IN ('QUEUED','RUNNING')",
                    (project_id, task_id),
                ).fetchone():
                    raise BridgeError("Cannot revise plan while an agent is running")
                self._transition(
                    conn, project_id, task_id, "AWAITING_PM_DECISION", "PM evaluating plan"
                )
                version = conn.execute(
                    "SELECT coalesce(max(version),0)+1 FROM plans "
                    "WHERE project_id=? AND task_id=? AND id=?",
                    (project_id, task_id, plan_id),
                ).fetchone()[0]
                conn.execute(
                    "INSERT INTO plans VALUES(?,?,?,?,?,?,?,?)",
                    (
                        project_id,
                        task_id,
                        plan_id,
                        version,
                        context_version,
                        content,
                        plan_hash,
                        now(),
                    ),
                )
                access = self.get_project_access(project_id)
                if access["mode"] == "read-write":
                    approval_id = "APP-" + uuid.uuid4().hex[:16]
                    self._transition(
                        conn,
                        project_id,
                        task_id,
                        "APPROVED",
                        "Project authorization automatically authorized the current plan",
                    )
                    conn.execute(
                        "INSERT INTO approvals VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                        (
                            approval_id,
                            project_id,
                            task_id,
                            plan_id,
                            version,
                            plan_hash,
                            context_version,
                            "APPROVED",
                            access["identity"],
                            now(),
                            now(),
                        ),
                    )
                    conn.execute(
                        "INSERT INTO decisions VALUES(?,?,?,?,?,?,?,?,?)",
                        (
                            "DEC-" + uuid.uuid4().hex[:16],
                            project_id,
                            task_id,
                            f"Authorized {plan_id} v{version} sha256:{plan_hash}",
                            f"Ongoing project authorization {access['authorization_id']}",
                            access["identity"],
                            "Approved",
                            context_version,
                            now(),
                        ),
                    )
                    self.save_artifact(
                        conn,
                        project_id,
                        task_id,
                        "APPROVED-PLAN.md",
                        approval_id,
                        "# Authorized Plan\n\n"
                        + attribution(self.get_project_settings(project_id)["language"], "PM")
                        + canonical(
                            {
                                "id": plan_id,
                                "version": version,
                                "context_version": context_version,
                                "content": content,
                                "hash": plan_hash,
                                "authorization_id": access["authorization_id"],
                            }
                        )
                        + "\n",
                    )
            self.sync_documents(project_id)
            return {
                "plan_id": plan_id,
                "version": version,
                "hash": plan_hash,
                "context_version": context_version,
                "status": (
                    "APPROVED"
                    if self.get_project_access(project_id)["mode"] == "read-write"
                    else "AWAITING_PM_DECISION"
                ),
            }

    def plan(self, project_id, task_id, plan_id):
        self.task(project_id, task_id)
        row = self.store.one(
            "SELECT * FROM plans WHERE project_id=? AND task_id=? AND id=? "
            "ORDER BY version DESC LIMIT 1",
            (project_id, task_id, identifier(plan_id)),
        )
        if not row:
            raise BridgeError("Plan not found")
        return row

    def request_approval(self, project_id, task_id, plan_id):
        with self.lock(project_id):
            plan = self.plan(project_id, task_id, plan_id)
            if self.get_project_access(project_id)["mode"] == "read-write":
                return {
                    "plan": plan,
                    "status": "APPROVED",
                    "next_action": "Project authorization is active; dispatch implementation.",
                }
            if plan["context_version"] != self.latest(project_id)["version"]:
                raise VersionConflict("Plan must be refreshed for the current Context Version")
            request_id = "APP-" + uuid.uuid4().hex[:16]
            with self.store.transaction() as conn:
                self._transition(
                    conn,
                    project_id,
                    task_id,
                    "AWAITING_USER_APPROVAL",
                    "PM requested user approval",
                )
                conn.execute(
                    "INSERT INTO approvals VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        request_id,
                        project_id,
                        task_id,
                        plan_id,
                        plan["version"],
                        plan["hash"],
                        plan["context_version"],
                        "PENDING",
                        None,
                        now(),
                        None,
                    ),
                )
            self.sync_documents(project_id)
            return {
                "approval_id": request_id,
                "plan": plan,
                "status": "PENDING",
                "next_action": (
                    f"本機終端執行 ccc-mcp --project {project_id} "
                    f"approve {task_id} --plan {plan_id}"
                ),
            }

    def get_approval_status(self, project_id, task_id):
        self.task(project_id, task_id)
        return self.store.all(
            "SELECT * FROM approvals WHERE project_id=? AND task_id=? ORDER BY created DESC",
            (project_id, task_id),
        )

    def approve_local(self, project_id, task_id, plan_id):
        from .approval import confirm_local

        # No identity or approved=true argument can bypass the operator terminal ceremony.
        if self.get_project_access(project_id)["mode"] == "read-write":
            plan = self.approved_plan(project_id, task_id)
            return {
                "status": "APPROVED",
                "identity": self.get_project_access(project_id)["identity"],
                "plan_hash": plan["hash"],
                "already_authorized_by_project": True,
            }
        plan = self.plan(project_id, task_id, plan_id)
        workspace_mode = self.get_project_settings(project_id)["workspace_mode"]
        identity = confirm_local(
            f"核准 {task_id} / {plan_id} v{plan['version']}\n"
            f"專案：{project_id}　根目錄：{self.project(project_id)['root']}\n"
            f"修改位置模式：{workspace_mode}（project 為直接修改原專案）\n"
            f"SHA256: {plan['hash']}\n\n{plan['content']}",
            task_id,
        )
        with self.lock(project_id):
            current = self.plan(project_id, task_id, plan_id)
            if self.get_project_settings(project_id)["workspace_mode"] != workspace_mode:
                raise VersionConflict("Workspace mode changed while reviewing")
            if current != plan or self.latest(project_id)["version"] != plan["context_version"]:
                raise VersionConflict("Plan or context changed while reviewing")
            pending = self.store.one(
                "SELECT * FROM approvals WHERE project_id=? AND task_id=? AND plan_id=? "
                "AND plan_version=? AND status='PENDING' ORDER BY created DESC LIMIT 1",
                (project_id, task_id, plan_id, plan["version"]),
            )
            if not pending:
                raise BridgeError("No pending approval request for this plan")
            with self.store.transaction() as conn:
                self._transition(
                    conn, project_id, task_id, "APPROVED", "Local operator approved exact plan hash"
                )
                conn.execute(
                    "UPDATE approvals SET status='APPROVED',identity=?,approved_at=? WHERE id=?",
                    (identity, now(), pending["id"]),
                )
                conn.execute(
                    "INSERT INTO decisions VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        "DEC-" + uuid.uuid4().hex[:16],
                        project_id,
                        task_id,
                        f"Approved {plan_id} v{plan['version']} sha256:{plan['hash']}",
                        "Local operator reviewed requirement and plan",
                        identity,
                        "Approved",
                        plan["context_version"],
                        now(),
                    ),
                )
                self.save_artifact(
                    conn,
                    project_id,
                    task_id,
                    "APPROVED-PLAN.md",
                    pending["id"],
                    "# Approved Plan\n\n"
                    + attribution(self.get_project_settings(project_id)["language"], "Local operator")
                    + canonical(plan)
                    + "\n",
                )
            self.sync_documents(project_id)
        return {"status": "APPROVED", "identity": identity, "plan_hash": plan["hash"]}

    def approved_plan(self, project_id, task_id, require_current_context=True):
        if self.get_project_access(project_id)["mode"] == "read-write":
            plan = self.store.one(
                "SELECT * FROM plans WHERE project_id=? AND task_id=? ORDER BY created DESC LIMIT 1",
                (project_id, task_id),
            )
            if plan:
                return plan
            raise PermissionDenied("Implementation requires a PM plan")
        approvals = self.get_approval_status(project_id, task_id)
        for approval in approvals:
            if approval["status"] != "APPROVED":
                continue
            plan = self.plan(project_id, task_id, approval["plan_id"])
            if (
                plan["version"] == approval["plan_version"]
                and plan["hash"] == approval["plan_hash"]
                and (
                    not require_current_context
                    or self.context_preserves_approval(project_id, approval["context_version"])
                )
            ):
                # A newer plan of a different ID also invalidates an older authorization.
                newest = self.store.one(
                    "SELECT * FROM plans WHERE project_id=? AND task_id=? ORDER BY created DESC LIMIT 1",
                    (project_id, task_id),
                )
                if newest["id"] == plan["id"] and newest["version"] == plan["version"]:
                    return plan
        action = "Implementation" if require_current_context else "Review"
        raise PermissionDenied(
            f"{action} requires local human approval of the applicable plan"
            + (" and current context" if require_current_context else "")
        )

    def context_preserves_approval(self, project_id, approved_version):
        """Automatic evidence append does not invalidate a human-approved implementation plan."""
        current = self.latest(project_id)["version"]
        if current < approved_version:
            return False
        if current == approved_version:
            return True
        rows = self.store.all(
            "SELECT metadata FROM context_versions WHERE project_id=? AND version>? ORDER BY version",
            (project_id, approved_version),
        )
        return bool(rows) and all(
            (metadata := json.loads(row["metadata"])).get("author_role") == "SYSTEM"
            and set(metadata.get("changed_sections", [])) <= {"agent_results"}
            for row in rows
        )

    def record_decision_local(
        self, project_id, task_id, content, reason, status="Approved", supersedes=None
    ):
        from .approval import confirm_local

        self.task(project_id, task_id)
        if status not in {"Approved", "Rejected", "Superseded"}:
            raise BridgeError("Unsupported decision status")
        reject_secrets([content, reason])
        if supersedes:
            previous = self.store.one(
                "SELECT * FROM decisions WHERE id=? AND project_id=?",
                (identifier(supersedes), project_id),
            )
            if not previous:
                raise BridgeError("Superseded decision not found")
            reason = f"Supersedes {supersedes}. {reason}"
        identity = confirm_local(f"記錄決策 ({status})\n{content}\n原因：{reason}", task_id)
        decision_id = "DEC-" + uuid.uuid4().hex[:16]
        with self.lock(project_id):
            with self.store.transaction() as conn:
                conn.execute(
                    "INSERT INTO decisions VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        decision_id,
                        project_id,
                        task_id,
                        content,
                        reason,
                        identity,
                        status,
                        self.latest(project_id)["version"],
                        now(),
                    ),
                )
            self.sync_documents(project_id)
        return {"decision_id": decision_id, "status": status}

    def reject_local(self, project_id, task_id, plan_id, reason):
        from .approval import confirm_local

        plan = self.plan(project_id, task_id, plan_id)
        reject_secrets(reason)
        identity = confirm_local(
            f"拒絕 {plan_id} v{plan['version']}\n{plan['content']}\n原因：{reason}", task_id
        )
        with self.lock(project_id):
            with self.store.transaction() as conn:
                self._transition(conn, project_id, task_id, "REJECTED", reason)
                conn.execute(
                    "UPDATE approvals SET status='REJECTED',identity=?,approved_at=? WHERE project_id=? "
                    "AND task_id=? AND status='PENDING'",
                    (identity, now(), project_id, task_id),
                )
                conn.execute(
                    "INSERT INTO decisions VALUES(?,?,?,?,?,?,?,?,?)",
                    (
                        "DEC-" + uuid.uuid4().hex[:16],
                        project_id,
                        task_id,
                        f"Rejected {plan_id} v{plan['version']} sha256:{plan['hash']}",
                        reason,
                        identity,
                        "Rejected",
                        self.latest(project_id)["version"],
                        now(),
                    ),
                )
            self.sync_documents(project_id)
        return {"status": "REJECTED"}

    def dispatch_agent(
        self,
        project_id,
        task_id,
        agent,
        role,
        context_version,
        instruction,
        adapter="real",
        timeout=600,
        fake_scenario="success",
        fake_delay=0.01,
        model=None,
        effort=None,
        fallback_from=None,
    ):
        settings = self.resolve_agent_settings(project_id, role, agent, model, effort)
        agent = settings["provider"]
        if adapter not in {"real", "fake"} or fake_scenario not in {"success", "revision", "error"}:
            raise BridgeError("Unsupported adapter or fake scenario")
        if not 0.05 <= timeout <= 7200 or not 0 <= fake_delay <= 60:
            raise BridgeError("Timeout/delay outside supported bounds")
        if (
            adapter == "real"
            and agent != "chatgpt"
            and self.get_project_access(project_id)["mode"] != "read-write"
            and setting_env("ENABLE_REAL_AGENTS") != "1"
        ):
            raise PermissionDenied(
                "Real agents disabled; operator must set CCC_MCP_ENABLE_REAL_AGENTS=1"
            )
        reject_secrets(instruction)
        with self.lock(project_id):
            self.task(project_id, task_id)
            if self.latest(project_id)["version"] != context_version:
                raise VersionConflict("Dispatch Context Version is stale")
            project_settings = self.get_project_settings(project_id)
            workspace_mode = project_settings["workspace_mode"]
            if project_settings["workflow_mode"] == "agent-required" and role != "SA":
                pending = self.context_sync_status(project_id)["pending_reports"]
                if pending:
                    raise PermissionDenied(
                        "Agent-required workflow has unrefined reports. PM must cite the original "
                        "AgentReport, propose_context_patch, and commit_context_patch before the next role."
                    )
            if role not in {"SA", "REVIEW"}:
                self.approved_plan(project_id, task_id)
                if workspace_mode == "project":
                    self.prepare_project_workspace(project_id, task_id)
                prior = self.store.one(
                    "SELECT options FROM agent_executions WHERE project_id=? AND task_id=? "
                    "AND worktree IS NOT NULL AND role NOT IN ('SA','REVIEW') ORDER BY created DESC LIMIT 1",
                    (project_id, task_id),
                )
                if (
                    prior
                    and json.loads(prior["options"]).get("workspace_mode", "worktree")
                    != workspace_mode
                ):
                    raise BridgeError("Existing task workspace mode is pinned; create a new task")
                live = self.store.all(
                    "SELECT options FROM agent_executions WHERE project_id=? AND role NOT IN ('SA','REVIEW') "
                    "AND status IN ('QUEUED','RUNNING')",
                    (project_id,),
                )
                if live and (
                    workspace_mode == "project"
                    or any(
                        json.loads(r["options"]).get("workspace_mode") == "project" for r in live
                    )
                ):
                    raise BridgeError("Project-mode implementation is exclusive within a project")
            package = self.build_handoff(
                project_id, role, task_id, project_settings.get("context_token_budget")
            )
            if package["budget_exceeded"]:
                raise BridgeError(
                    "Context handoff exceeds context_token_budget "
                    f"({package['estimated_tokens']} estimated tokens > "
                    f"{package['token_budget']}) even after trimming Skill text and "
                    "compressible history; raise context_token_budget, remove Skills, or "
                    "reduce decision/report history, then dispatch again. Already omitted: "
                    f"{package['budget_omitted']}"
                )
            package["target_agent"] = agent
            package["agent_settings"] = settings
            package["workspace_mode"] = workspace_mode
            execution_id = "EXEC-" + uuid.uuid4().hex[:16]
            state = {"SA": "ANALYZING", "REVIEW": "REVIEWING"}.get(role, "IMPLEMENTING")
            with self.store.transaction() as conn:
                if conn.execute(
                    "SELECT 1 FROM agent_executions WHERE project_id=? AND task_id=? "
                    "AND status IN ('QUEUED','RUNNING')",
                    (project_id, task_id),
                ).fetchone():
                    raise BridgeError("Task already has an active agent")
                self._transition(conn, project_id, task_id, state, f"PM dispatched {agent}/{role}")
                conn.execute(
                    "INSERT INTO agent_executions(id,project_id,task_id,agent,role,context_version,"
                    "instruction,adapter,options,status,package,created) VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        execution_id,
                        project_id,
                        task_id,
                        agent,
                        role,
                        context_version,
                        instruction,
                        "interactive" if agent == "chatgpt" and adapter == "real" else adapter,
                        canonical(
                            {
                                "timeout": timeout,
                                "scenario": fake_scenario,
                                "delay": fake_delay,
                                "agent_settings": settings,
                                "workspace_mode": workspace_mode,
                                "interactive": agent == "chatgpt" and adapter == "real",
                                "fallback_from": fallback_from,
                            }
                        ),
                        "QUEUED",
                        canonical(package),
                        now(),
                    ),
                )
            self.sync_documents(project_id)
        if agent == "chatgpt" and adapter == "real":
            return self.prepare_chatgpt_execution(execution_id)
        try:
            self.spawn_worker(execution_id)
        except Exception:
            self.finish_execution(execution_id, error="Unable to start worker", state="FAILED")
            raise BridgeError("Unable to start worker") from None
        return {"task_id": task_id, "execution_id": execution_id, "status": "QUEUED"}

    def spawn_worker(self, execution_id):
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        subprocess.Popen(
            [
                sys.executable,
                "-m",
                "ccc_mcp.worker",
                "--home",
                str(self.home),
                "--execution",
                execution_id,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            shell=False,
            creationflags=flags,
            start_new_session=os.name != "nt",
        )

    def get_task_status(self, project_id, task_id):
        with self.lock(project_id):
            task = self.task(project_id, task_id)
            task["executions"] = self.store.all(
                "SELECT id,status,role,error,worktree FROM agent_executions "
                "WHERE project_id=? AND task_id=? ORDER BY created",
                (project_id, task_id),
            )
            task["events"] = self.store.all(
                "SELECT * FROM task_events WHERE project_id=? AND task_id=? ORDER BY seq",
                (project_id, task_id),
            )
            latest = self.store.one(
                "SELECT id,agent,role,adapter,status,result,error,created,options "
                "FROM agent_executions WHERE project_id=? AND task_id=? ORDER BY created DESC LIMIT 1",
                (project_id, task_id),
            )
            saved = json.loads(latest["result"]) if latest and latest["result"] else {}
            report = saved.get("report") or {}
            progress = None
            if latest and latest["status"] in {"QUEUED", "RUNNING"}:
                options = json.loads(latest["options"]) if latest["options"] else {}
                timeout = options.get("timeout")
                elapsed = (
                    datetime.now(UTC) - datetime.fromisoformat(latest["created"])
                ).total_seconds()
                # A worker that never even reached run_execution (e.g. the subprocess
                # failed to start or crashed before acquiring its lock) leaves the row
                # QUEUED forever with no error — invisible unless something checks the
                # lock directly. Give new dispatches a few seconds' grace so this isn't
                # a false positive against a worker that just hasn't started yet.
                orphaned = False
                if latest["status"] == "QUEUED" and not options.get("interactive") and elapsed > 5:
                    lockfile = self.home / "locks" / f"{latest['id']}.lock"
                    try:
                        with portalocker.Lock(lockfile, timeout=0):
                            orphaned = True
                    except portalocker.exceptions.LockException:
                        orphaned = False
                if orphaned:
                    note = (
                        "No live worker process holds this execution's lock even though "
                        "it is still QUEUED; the worker most likely crashed before it "
                        "started. Call recover_workers to clear it, then dispatch again."
                    )
                elif timeout is None or elapsed < timeout:
                    note = (
                        "Still within the configured timeout; a real Agent CLI call can "
                        "legitimately take several minutes. Re-check get_task_status later "
                        "rather than assuming it is stuck."
                    )
                else:
                    note = (
                        "Elapsed time exceeds the configured timeout but status has not "
                        "flipped yet; the worker may have crashed. Treat as possibly stuck."
                    )
                progress = {
                    "elapsed_seconds": round(elapsed),
                    "timeout_seconds": timeout,
                    "orphaned": orphaned,
                    "note": note,
                }
            needs_pm_refinement = self.context_sync_status(project_id)["needs_pm_refinement"]
            test_result = self._latest_test_result(project_id, task_id)
            pointer = self._pending_dispatch_row(project_id, task_id)
            chatgpt_handoff = bool(
                latest
                and latest["status"] == "RUNNING"
                and json.loads(latest["options"] or "{}").get("interactive")
            )
            task["brief"] = {
                "task_id": task_id,
                "state": task["state"],
                "waiting_on": waiting_on(
                    task["state"],
                    latest["status"] if latest else None,
                    bool(progress and progress["orphaned"]),
                    needs_pm_refinement,
                    pending_dispatch=bool(pointer),
                    chatgpt_handoff=chatgpt_handoff,
                ),
                "agent": latest["agent"] if latest else None,
                "role": latest["role"] if latest else None,
                "adapter": latest["adapter"] if latest else None,
                "execution_status": latest["status"] if latest else None,
                "progress": progress,
                "summary": report.get("summary"),
                "needs_revision": report.get("needs_revision"),
                "next_action": report.get("next_action"),
                "error": latest["error"] if latest else None,
                "failure_kind": saved.get("failure_kind"),
                "context_version": self.latest(project_id)["version"],
                "context_refinement_pending": needs_pm_refinement,
                "tests": {
                    "executed_by_host": bool(test_result and test_result.get("executed_by_host")),
                    "passed": test_result.get("passed") if test_result else None,
                    "reason": None if (test_result or {}).get("executed_by_host") else (
                        (test_result or {}).get("reason")
                        or "No test evidence recorded for this task yet"
                    ),
                    "dependencies": _dependency_summary(test_result),
                    "no_tests_collected": bool((test_result or {}).get("no_tests_collected")),
                },
                "fallback": saved.get("fallback"),
                "pending_dispatch": (
                    {
                        "source_execution_id": pointer["source_execution_id"],
                        "role_completed": pointer["role"],
                        "context_version": pointer["context_version"],
                        "created": pointer["created"],
                        "next_action": "Context for this round was already committed; call "
                        "resume_pending_dispatch to finish dispatching the next role "
                        "without resubmitting a ContextPatch.",
                    }
                    if pointer
                    else None
                ),
            }
            return task

    def run_configured_test_command(self, project_id, task_id):
        """Auto-run the project's test command before REVIEW is dispatched.

        `project authorize` (the durable read-write grant) is the consent for this — the
        same grant that already lets the configured Agent workflow modify project files
        without per-step approval also covers running a fixed, host-executed test
        command. Without continuous authorization, no command runs and the reason is
        recorded instead of silently skipping. A project that never explicitly configured
        `test_command` still gets `DEFAULT_TEST_COMMAND` (`python -m pytest -q`) run
        automatically — `config test-command` only needs to be set to use something else.
        """
        from ..adapters.test_runner import (
            DEFAULT_TEST_COMMAND,
            execute_test_command,
            record_test_evidence,
        )

        access = self.get_project_access(project_id)
        if access["mode"] != "read-write":
            return record_test_evidence(
                self,
                project_id,
                task_id,
                {
                    "executed_by_host": False,
                    "reason": "Project is not continuously authorized; run project authorize",
                },
            )
        command = self.get_project_settings(project_id).get("test_command") or DEFAULT_TEST_COMMAND
        return execute_test_command(self, project_id, task_id, command, access["identity"])

    def _latest_test_result(self, project_id, task_id):
        root = self.project(project_id)["root"]
        path = f"{metadata_name(root)}/tasks/{task_id}/TEST-RESULT.md"
        row = self.store.one(
            "SELECT content FROM artifacts WHERE project_id=? AND path=?", (project_id, path)
        )
        if not row:
            return None
        try:
            # The JSON payload is always the last "\n\n"-separated block, whether the
            # artifact came from execute_test_command (no attribution line) or the
            # finish_execution default (heading + attribution line + JSON).
            return json.loads(row["content"].rsplit("\n\n", 1)[-1])
        except ValueError:
            return None

    def _host_test_failed(self, project_id, task_id):
        """True only when the host actually ran the configured test command and it
        failed — never for "not executed"/unverified, which is a separate signal
        (brief.tests.executed_by_host). Used to stop a REVIEW Agent's own self-report
        from overriding host-observed evidence (see commit_context_patch/_review_outcome)."""
        result = self._latest_test_result(project_id, task_id)
        return bool(result and result.get("executed_by_host") and result.get("passed") is False)

    def _host_tests_unverified(self, project_id, task_id):
        """True when the host ran the test command but it collected zero tests
        (pytest exit 5) — distinct from a real pass and from "never executed at all".
        Per the original requirement ("測試失敗或未驗證時，不可對外宣稱完整驗收成功"),
        nothing was actually verified here, so — like a host test failure — this must
        not silently let REVIEW auto-complete the task."""
        result = self._latest_test_result(project_id, task_id)
        return bool(result and result.get("executed_by_host") and result.get("no_tests_collected"))

    def get_task_result(self, project_id, task_id):
        with self.lock(project_id):
            self.task(project_id, task_id)
            return [
                decode(r, "result", "package", "options")
                for r in self.store.all(
                    "SELECT * FROM agent_executions WHERE project_id=? AND task_id=? ORDER BY created",
                    (project_id, task_id),
                )
            ]

    def review_changes(
        self,
        project_id,
        task_id,
        context_version,
        instruction="Review all task changes",
        adapter="real",
        fake_scenario="success",
    ):
        return self.dispatch_agent(
            project_id,
            task_id,
            "auto",
            "REVIEW",
            context_version,
            instruction,
            adapter,
            fake_scenario=fake_scenario,
        )

    def get_task_usage(self, project_id, task_id):
        results = self.get_task_result(project_id, task_id)
        return [
            {
                "execution_id": row["id"],
                "agent": row["agent"],
                "role": row["role"],
                "status": row["status"],
                "invocation": (row.get("result") or {}).get(
                    "invocation",
                    {
                        "cli_invoked": None,
                        "usage": {
                            "available": False,
                            "reason": "Historical execution did not record invocation metadata",
                        },
                    },
                ),
            }
            for row in results
        ]

    def cancel_task(self, project_id, task_id):
        with self.lock(project_id):
            self.task(project_id, task_id)
            with self.store.transaction() as conn:
                conn.execute(
                    "UPDATE agent_executions SET cancel_requested=1 WHERE project_id=? AND task_id=? "
                    "AND status IN ('QUEUED','RUNNING')",
                    (project_id, task_id),
                )
                self._transition(
                    conn, project_id, task_id, "CANCELLED", "PM cancelled task; stop requested"
                )
                conn.execute(
                    "DELETE FROM pending_dispatch WHERE project_id=? AND task_id=?",
                    (project_id, task_id),
                )
            self.sync_documents(project_id)
        for row in self.store.all(
            "SELECT id,options FROM agent_executions WHERE project_id=? AND task_id=? "
            "AND agent='chatgpt' AND adapter='interactive' AND status='RUNNING'",
            (project_id, task_id),
        ):
            self.finish_execution(
                row["id"], error="Interactive execution cancelled", state="CANCELLED"
            )
        return {"status": "CANCELLED", "stop_requested": True}

    def recover_workers(self, project_id):
        self.project(project_id)
        recovered = []
        rows = self.store.all(
            "SELECT * FROM agent_executions WHERE project_id=? AND status IN ('QUEUED','RUNNING')",
            (project_id,),
        )
        for row in rows:
            if decode(row, "options")["options"].get("interactive"):
                continue
            lockfile = self.home / "locks" / f"{row['id']}.lock"
            try:
                with portalocker.Lock(lockfile, timeout=0):
                    self.finish_execution(
                        row["id"],
                        error="Interrupted worker: inspect worktree before explicit redispatch",
                        state="BLOCKED",
                    )
                    recovered.append(row["id"])
            except portalocker.exceptions.LockException:
                pass
        return {"interrupted_executions": recovered, "automatic_replay": False}

    def finish_execution(
        self, execution_id, report=None, error=None, state=None, extras=None, locked=False
    ):
        row = self.store.one("SELECT * FROM agent_executions WHERE id=?", (execution_id,))
        project_id, task_id = row["project_id"], row["task_id"]
        with nullcontext() if locked else self.lock(project_id):
            with self.store.transaction() as conn:
                current = conn.execute(
                    "SELECT * FROM agent_executions WHERE id=?", (execution_id,)
                ).fetchone()
                if current["status"] not in {"QUEUED", "RUNNING"}:
                    return
                cancelled = bool(current["cancel_requested"])
                target = (
                    "CANCELLED"
                    if cancelled
                    else state
                    or (
                        "AWAITING_PM_DECISION"
                        if row["role"] == "SA"
                        else "NEEDS_REVISION"
                        if report and report.get("needs_revision")
                        else "AWAITING_PM_REVIEW"
                    )
                )
                if report:
                    language = self.get_project_settings(project_id)["language"]
                    headings = {
                        "zh-TW": ("建議的上下文更新 — 待 PM 審核", "設計", "測試證據"),
                        "ch": ("建议的上下文更新 — 待 PM 审核", "设计", "测试证据"),
                        "en": (
                            "Proposed context updates — PM review required",
                            "Design",
                            "Test evidence",
                        ),
                    }[language]
                    updates_content = (
                        f"# {headings[0]}\n\n"
                        + attribution(language, row["agent"], row["role"])
                        + canonical(
                            {
                                "context_version": row["context_version"],
                                "role": row["role"],
                                "summary": report["summary"],
                                "updates": report.get("context_updates", []),
                                "findings": report.get("findings", []),
                                "risks": report.get("risks", []),
                                "next_action": report["next_action"],
                            }
                        )
                        + "\n"
                    )
                    self.save_artifact(
                        conn,
                        project_id,
                        task_id,
                        "CONTEXT-UPDATES.md",
                        execution_id,
                        updates_content,
                    )
                    name = {"SA": "SA-REPORT.md", "REVIEW": "REVIEW.md"}.get(
                        row["role"], "IMPLEMENTATION.md"
                    )
                    report_content = (
                        f"# {name}\n\n"
                        + attribution(language, row["agent"], row["role"])
                        + f"{canonical(report)}\n"
                    )
                    report_path = self.save_artifact(
                        conn,
                        project_id,
                        task_id,
                        name,
                        execution_id,
                        report_content,
                    )
                    self.record_agent_context_result(
                        conn, row, report, report_path, digest(report_content.encode())
                    )
                    if row["role"] not in {"SA", "REVIEW"}:
                        self.save_artifact(
                            conn,
                            project_id,
                            task_id,
                            "SD-DESIGN.md",
                            execution_id,
                            f"# {headings[1]}\n\n"
                            + attribution(language, row["agent"], row["role"])
                            + canonical(report.get("findings", []))
                            + "\n",
                        )
                        self.save_artifact(
                            conn,
                            project_id,
                            task_id,
                            "TEST-RESULT.md",
                            execution_id,
                            f"# {headings[2]}\n\n"
                            + attribution(language, "ccc-mcp host", row["role"])
                            + canonical((extras or {}).get("test_evidence", []))
                            + "\n",
                        )
                if extras and extras.get("invocation"):
                    self.save_artifact(
                        conn,
                        project_id,
                        task_id,
                        "INVOCATION.json",
                        execution_id,
                        canonical(extras["invocation"]) + "\n",
                    )
                result = {"report": report, **(extras or {})} if report else extras
                conn.execute(
                    "UPDATE agent_executions SET status=?,result=?,error=?,finished=? WHERE id=?",
                    (
                        "CANCELLED" if cancelled else "FAILED" if error else "SUCCEEDED",
                        canonical(result) if result else None,
                        error,
                        now(),
                        execution_id,
                    ),
                )
                self._transition(
                    conn, project_id, task_id, target, error or "Agent result requires PM review"
                )
            self.sync_documents(project_id)

    def accept_local(self, project_id, task_id):
        from .approval import confirm_local

        task = self.task(project_id, task_id)
        if task["state"] != "AWAITING_PM_REVIEW":
            raise BridgeError("Task must be awaiting PM review")
        if self.get_project_settings(project_id)["workflow_mode"] == "agent-required":
            pending = self.context_sync_status(project_id)["pending_reports"]
            if pending:
                raise PermissionDenied(
                    "Agent-required workflow still has unrefined reports. PM must commit a "
                    "provenance-backed ContextPatch before the task can be accepted."
                )
        identity = confirm_local(
            "驗收任務及測試證據：\n" + canonical(self.get_task_result(project_id, task_id)), task_id
        )
        with self.lock(project_id):
            with self.store.transaction() as conn:
                self._transition(
                    conn, project_id, task_id, "COMPLETED", f"Local acceptance by {identity}"
                )
            self.sync_documents(project_id)
        return {"state": "COMPLETED"}
