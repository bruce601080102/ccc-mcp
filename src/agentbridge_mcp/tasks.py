import os
import subprocess
import sys
import uuid
from contextlib import nullcontext

import portalocker

from .context import identifier
from .errors import BridgeError, PermissionDenied, VersionConflict
from .security import canonical, digest, reject_secrets
from .storage import artifact, decode, event, now

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
    "AWAITING_PM_DECISION": {"ANALYZING", "AWAITING_USER_APPROVAL", "REVIEWING"},
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
            task_id = identifier(task_id or f"TASK-{len(existing) + 1:04d}")
            with self.store.transaction() as conn:
                if conn.execute(
                    "SELECT 1 FROM tasks WHERE project_id=? AND id=?", (project_id, task_id)
                ).fetchone():
                    raise BridgeError("Task already exists")
                conn.execute(
                    "INSERT INTO tasks VALUES(?,?,?,?,?,?)",
                    (
                        task_id,
                        project_id,
                        requirement,
                        canonical(acceptance or []),
                        "CREATED",
                        now(),
                    ),
                )
                event(conn, project_id, task_id, None, "CREATED", "PM created requirement")
                artifact(
                    conn,
                    project_id,
                    f".agentbridge/tasks/{task_id}/REQUIREMENT.md",
                    f"# Requirement\n\n{requirement}\n\n## Acceptance\n{canonical(acceptance or [])}\n",
                )
            self.sync_documents(project_id)
        return self.task(project_id, task_id)

    def propose_plan(self, project_id, task_id, plan_id, content, context_version):
        identifier(plan_id)
        reject_secrets(content)
        if not content.strip():
            raise BridgeError("Plan content is required")
        with self.lock(project_id):
            self.task(project_id, task_id)
            if context_version != self.latest(project_id)["version"]:
                raise VersionConflict("Plan context is stale")
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
                plan_hash = digest(content.encode())
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
            self.sync_documents(project_id)
            return {
                "plan_id": plan_id,
                "version": version,
                "hash": plan_hash,
                "context_version": context_version,
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
                "next_action": f"本機終端執行 agentbridge approve {task_id} --plan {plan_id}",
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
        plan = self.plan(project_id, task_id, plan_id)
        identity = confirm_local(
            f"核准 {task_id} / {plan_id} v{plan['version']}\n"
            f"SHA256: {plan['hash']}\n\n{plan['content']}",
            task_id,
        )
        with self.lock(project_id):
            current = self.plan(project_id, task_id, plan_id)
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
                    "# Approved Plan\n\n" + canonical(plan) + "\n",
                )
            self.sync_documents(project_id)
        return {"status": "APPROVED", "identity": identity, "plan_hash": plan["hash"]}

    def approved_plan(self, project_id, task_id):
        approvals = self.get_approval_status(project_id, task_id)
        for approval in approvals:
            if approval["status"] != "APPROVED":
                continue
            plan = self.plan(project_id, task_id, approval["plan_id"])
            if (
                plan["version"] == approval["plan_version"]
                and plan["hash"] == approval["plan_hash"]
                and approval["context_version"] == self.latest(project_id)["version"]
            ):
                # A newer plan of a different ID also invalidates an older authorization.
                newest = self.store.one(
                    "SELECT * FROM plans WHERE project_id=? AND task_id=? ORDER BY created DESC LIMIT 1",
                    (project_id, task_id),
                )
                if newest["id"] == plan["id"] and newest["version"] == plan["version"]:
                    return plan
        raise PermissionDenied(
            "Implementation requires local human approval of the current plan and context"
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
            and os.environ.get("AGENTBRIDGE_ENABLE_REAL_AGENTS") != "1"
        ):
            raise PermissionDenied(
                "Real agents disabled; operator must set AGENTBRIDGE_ENABLE_REAL_AGENTS=1"
            )
        reject_secrets(instruction)
        with self.lock(project_id):
            self.task(project_id, task_id)
            if self.latest(project_id)["version"] != context_version:
                raise VersionConflict("Dispatch Context Version is stale")
            if role not in {"SA", "REVIEW"}:
                self.approved_plan(project_id, task_id)
            package = self.build_handoff(project_id, role, task_id)
            package["target_agent"] = agent
            package["agent_settings"] = settings
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
                "agentbridge_mcp.worker",
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
            return task

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
                    self.save_artifact(
                        conn,
                        project_id,
                        task_id,
                        "CONTEXT-UPDATES.md",
                        execution_id,
                        "# Proposed context updates — PM review required\n\n"
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
                        + "\n",
                    )
                    name = {"SA": "SA-REPORT.md", "REVIEW": "REVIEW.md"}.get(
                        row["role"], "IMPLEMENTATION.md"
                    )
                    self.save_artifact(
                        conn,
                        project_id,
                        task_id,
                        name,
                        execution_id,
                        f"# {name}\n\n{canonical(report)}\n",
                    )
                    if row["role"] not in {"SA", "REVIEW"}:
                        self.save_artifact(
                            conn,
                            project_id,
                            task_id,
                            "SD-DESIGN.md",
                            execution_id,
                            "# Design\n\n" + canonical(report.get("findings", [])) + "\n",
                        )
                        self.save_artifact(
                            conn,
                            project_id,
                            task_id,
                            "TEST-RESULT.md",
                            execution_id,
                            "# Test evidence\n\n"
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
