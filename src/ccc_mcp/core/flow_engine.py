"""Runs a task through a workflow's steps (see core/flows.py for the rules).

The Classic workflow keeps running on the original SA -> SD/RD -> REVIEW state machine in
tasks.py. Any other workflow is driven from here: after each step's report is committed to
the shared Context, ``_flow_advance`` decides the next step from durable rows only (the
source execution's ``flow_step`` option, recorded gate verdicts and the pinned flow
snapshot), so a crash or a retried resume reaches the same decision.
"""

import json
import uuid

from ..adapters.storage import now
from ..infra.errors import BridgeError
from .roles import ROLE_STATS, STAT_AXES, role_stats, team_stats
from .flows import (
    BUILDER_ROLES,
    BUILTIN_FLOWS,
    GATE_ROLES,
    VERIFYING_GATES,
    bounce_index,
    choose_workflow,
    feedback_text,
    flow_definition,
    judge_gate,
    step_instruction,
)

_DECISIONS = {"proceed", "retry", "stop"}


def scenario_for(fake_scenario, role, round_number):
    """Resolve a scripted fake scenario for one dispatch (tests only; real runs ignore it).

    "success" applies to every role. "AR:revision,success;default:success" scripts each
    role's rounds in turn: the first AR round gets "revision", later rounds "success"."""
    if ":" not in fake_scenario:
        return fake_scenario
    default, scripted = "success", {}
    for part in fake_scenario.split(";"):
        key, _, sequence = part.partition(":")
        if not sequence:
            default = key.strip()
            continue
        scripted[key.strip()] = [item.strip() for item in sequence.split(",") if item.strip()]
    sequence = scripted.get(role)
    if not sequence:
        return scripted.get("default", [default])[0]
    return sequence[min(round_number, len(sequence) - 1)]


class FlowHub:
    def workflow_catalog(self, project_id):
        """Every workflow this project can run (built-in and its own), the selected one, and
        who picks a new task's workflow."""
        settings = self.get_project_settings(project_id)
        custom = settings["workflows"]
        return {
            "selected": settings["workflow"],
            "selection": settings["workflow_selection"],
            "workflows": [
                {**flow, "stats": team_stats([step["role"] for step in flow["steps"]])}
                for flow in (
                    flow_definition(flow_id, custom) for flow_id in [*BUILTIN_FLOWS, *custom]
                )
            ],
            "role_stats": {role: role_stats(role) for role in ROLE_STATS},
            "stat_axes": list(STAT_AXES),
        }

    def _resolve_task_workflow(self, project_id, requirement, requested=None):
        """(workflow id, how it was chosen, why) for a task that is starting a work round.

        "fixed" always uses the selected workflow. "pm" lets the caller (ChatGPT) name one,
        falling back to the selected workflow. "auto" picks from the task's text."""
        settings = self.get_project_settings(project_id)
        selection, selected = settings["workflow_selection"], settings["workflow"]
        custom = settings["workflows"]
        if selection == "pm" and requested:
            flow_definition(requested, custom)  # rejects an unknown id
            return requested, "pm", "Chosen by the PM"
        if selection == "auto":
            catalog = {
                flow_id: flow_definition(flow_id, custom) for flow_id in [*BUILTIN_FLOWS, *custom]
            }
            flow_id, reason = choose_workflow(requirement, catalog, selected)
            return flow_id, "auto", reason
        return selected, "fixed", "The project's selected workflow"

    # -- which workflow a task runs -------------------------------------------------

    def _task_flow(self, project_id, task_id, conn=None):
        """The pinned flow of a task that runs on the step engine, else None."""
        query = "SELECT flow_id,snapshot FROM task_flows WHERE project_id=? AND task_id=?"
        row = (
            conn.execute(query, (project_id, task_id)).fetchone()
            if conn is not None
            else self.store.one(query, (project_id, task_id))
        )
        if not row:
            return None
        flow = json.loads(row["snapshot"])
        return flow if not flow.get("legacy") else None

    def _pin_task_flow(self, conn, project_id, task_id, choice):
        """Pin the chosen workflow (id, selected_by, reason) to a task that is starting a work
        round. Classic keeps no row, so existing projects behave exactly as before."""
        workflow, selected_by, reason = choice
        flow = flow_definition(workflow, self.get_project_settings(project_id)["workflows"])
        flow = {**flow, "selected_by": selected_by, "reason": reason}
        if flow["legacy"]:
            conn.execute(
                "DELETE FROM task_flows WHERE project_id=? AND task_id=?", (project_id, task_id)
            )
            return
        conn.execute(
            "INSERT INTO task_flows(project_id,task_id,flow_id,snapshot,created) "
            "VALUES(?,?,?,?,?) ON CONFLICT(project_id,task_id) DO UPDATE SET "
            "flow_id=excluded.flow_id,snapshot=excluded.snapshot,created=excluded.created",
            (project_id, task_id, flow["id"], json.dumps(flow), now()),
        )

    def _flow_roles(self, project_id, task_id):
        """Roles a task will need, for preflight; None for a Classic task."""
        flow = self._task_flow(project_id, task_id)
        if not flow:
            return None
        return list(dict.fromkeys(step["role"] for step in flow["steps"]))

    def _flow_next_role(self, project_id, task_id):
        """Best-effort guess of the role that runs next in a step-engine task."""
        flow = self._task_flow(project_id, task_id)
        if not flow:
            return None
        latest = self.store.one(
            "SELECT role,status,options FROM agent_executions WHERE project_id=? AND task_id=? "
            "ORDER BY created DESC LIMIT 1",
            (project_id, task_id),
        )
        steps = flow["steps"]
        if not latest:
            return steps[0]["role"]
        if latest["status"] in {"QUEUED", "RUNNING"}:
            return latest["role"]
        index = json.loads(latest["options"]).get("flow_step")
        if index is None or index + 1 >= len(steps):
            return None
        return steps[index + 1]["role"]

    # -- starting a task ------------------------------------------------------------

    def _flow_start(self, project_id, task, adapter="real", fake_scenario="success"):
        flow = self._task_flow(project_id, task["id"])
        if flow["steps"][0]["role"] != "SA":
            # No analyst: the PM's requirement and acceptance criteria are the plan.
            self.propose_plan(
                project_id, task["id"], "PLAN-A", self._requirement_plan(task),
                self.latest(project_id)["version"],
            )
        return self._flow_dispatch(
            project_id, task["id"], flow, 0, adapter, fake_scenario, requirement=task["requirement"]
        )

    @staticmethod
    def _requirement_plan(task):
        acceptance = task.get("acceptance") or []
        lines = [f"Requirement:\n{task['requirement']}"]
        if acceptance:
            lines.append("Acceptance criteria:\n" + "\n".join(f"- {item}" for item in acceptance))
        return "\n\n".join(lines)

    def _flow_dispatch(
        self, project_id, task_id, flow, index, adapter, fake_scenario,
        requirement=None, feedback=None,
    ):
        role = flow["steps"][index]["role"]
        gate = self.get_project_settings(project_id)["review_gate"]
        if requirement is None:
            requirement = self.task(project_id, task_id)["requirement"]
        instruction = step_instruction(
            role, requirement, strictness=gate["ar_strictness"],
            max_rounds=gate["max_rounds"], feedback=feedback,
        )
        prior = self.store.one(
            "SELECT COUNT(*) AS n FROM agent_executions WHERE project_id=? AND task_id=? AND role=?",
            (project_id, task_id, role),
        )["n"]
        return self._dispatch_or_adopt(
            project_id, task_id, role, instruction, adapter,
            scenario_for(fake_scenario, role, prior),
            flow_step=index, read_only=(role == "SD"), fake_script=fake_scenario,
        )

    # -- after a step -----------------------------------------------------------------

    def _flow_advance(self, project_id, pointer):
        task_id = pointer["task_id"]
        flow = self._task_flow(project_id, task_id)
        steps = flow["steps"]
        adapter, fake_scenario = pointer["adapter"], pointer["fake_scenario"]
        source = self.store.one(
            "SELECT * FROM agent_executions WHERE id=?", (pointer["source_execution_id"],)
        )
        index = json.loads(source["options"]).get("flow_step")
        if index is None:
            # Dispatched by hand outside the flow: continue from the last step with that role.
            index = next(
                (i for i in range(len(steps) - 1, -1, -1) if steps[i]["role"] == source["role"]),
                None,
            )
            if index is None:
                raise BridgeError(f"{source['role']} is not a step of this task's workflow")
        role = steps[index]["role"]
        result = {}
        notes = []
        if role == "SA":
            result["plan"] = self.propose_plan(
                project_id, task_id, "PLAN-A", pointer["plan_content"], pointer["context_version"]
            )
        elif role in BUILDER_ROLES:
            evidence = self.run_configured_test_command(project_id, task_id)
            result["test_evidence"] = evidence
            self.maybe_auto_evaluate(pointer["source_execution_id"], after_tests=True)
            notes.append(self._test_note(evidence))
        elif role in GATE_ROLES:
            gate = self._flow_record_gate(project_id, task_id, index, role, source)
            result["gate"] = gate
            if gate["verdict"] != "PASS":
                outcome = self._flow_gate_failed(
                    project_id, task_id, flow, index, gate, adapter, fake_scenario
                )
                result.update(outcome)
                self._clear_pending_dispatch(project_id, task_id)
                return result
        result.update(
            self._flow_continue(
                project_id, task_id, flow, index + 1, adapter, fake_scenario, notes=notes
            )
        )
        self._clear_pending_dispatch(project_id, task_id)
        return result

    @staticmethod
    def _test_note(evidence):
        if evidence["executed_by_host"]:
            verdict = "passed" if evidence.get("passed") else "FAILED"
            return f"Host-executed test command {verdict}: {' '.join(evidence['command'])}."
        return f"No host test evidence was collected ({evidence['reason']}); treat completion as unverified."

    def _flow_continue(
        self, project_id, task_id, flow, index, adapter, fake_scenario, notes=(), pm_note=None
    ):
        """Dispatch step `index`, or finish the task when the workflow has no more steps."""
        if index >= len(flow["steps"]):
            return self._flow_complete(project_id, task_id, flow, adapter, fake_scenario)
        feedback = "\n".join([*notes, *([f"PM decision: {pm_note}"] if pm_note else [])]) or None
        return {
            "next_execution": self._flow_dispatch(
                project_id, task_id, flow, index, adapter, fake_scenario, feedback=feedback
            )
        }

    def _flow_complete(self, project_id, task_id, flow, adapter, fake_scenario):
        if self._host_test_failed(project_id, task_id) or self._host_tests_unverified(
            project_id, task_id
        ):
            # Reviewers may have passed it, but host evidence takes precedence.
            index = len(flow["steps"]) - 1
            return self._flow_escalate(
                project_id, task_id, flow, index,
                "Host test evidence does not support completing this task.",
                {"blocking": []}, adapter, fake_scenario,
            )
        with self.lock(project_id):
            with self.store.transaction() as conn:
                state = conn.execute(
                    "SELECT state FROM tasks WHERE project_id=? AND id=?", (project_id, task_id)
                ).fetchone()["state"]
                if state == "NEEDS_REVISION":
                    self._transition(
                        conn, project_id, task_id, "AWAITING_PM_REVIEW",
                        "The last gate's blocking claim was unsupported and discarded",
                    )
                self._transition(
                    conn, project_id, task_id, "COMPLETED",
                    f"Every step of the {flow['name']} workflow passed",
                )
            self.sync_documents(project_id)
        return {"completed_workstreams": [task_id], "workflow_completed": True}

    # -- gates --------------------------------------------------------------------------

    def _flow_record_gate(self, project_id, task_id, index, role, source):
        existing = self.store.one(
            "SELECT * FROM flow_gate_results WHERE source_execution_id=?", (source["id"],)
        )
        if existing:
            return {**json.loads(existing["detail"]), "verdict": existing["verdict"]}
        saved = json.loads(source["result"]) if source["result"] else {}
        report = saved.get("report") or {}
        settings = self.get_project_settings(project_id)["review_gate"]
        strictness = settings["ar_strictness"] if role == "AR" else None
        gate = judge_gate(role, report, strictness or "normal")
        if role in VERIFYING_GATES and gate["verdict"] == "PASS":
            reason = None
            if self._host_test_failed(project_id, task_id):
                reason = (
                    "Host-executed tests failed even though the reviewer passed the work; host "
                    "test evidence takes precedence. Fix the implementation so the test command passes."
                )
            elif self._host_tests_unverified(project_id, task_id):
                reason = (
                    "The host-executed tests collected nothing or were blocked, so nothing about "
                    "this change was verified; add tests that exercise it."
                )
            if reason:
                gate = {**gate, "verdict": "NEEDS_REVISION", "reason": reason}
        gate = {**gate, "role": role, "step_index": index, "strictness": strictness}
        with self.store.transaction() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO flow_gate_results(id,project_id,task_id,step_index,role,"
                "source_execution_id,verdict,strictness,detail,created) VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    "GATE-" + uuid.uuid4().hex[:16], project_id, task_id, index, role,
                    source["id"], gate["verdict"], strictness, json.dumps(gate), now(),
                ),
            )
        return gate

    def _flow_failed_rounds(self, project_id, task_id, index):
        """How many times this gate has sent work back in the task's current work round."""
        return self.store.one(
            "SELECT COUNT(*) AS n FROM flow_gate_results WHERE project_id=? AND task_id=? "
            "AND step_index=? AND verdict!='PASS' AND created>="
            "(SELECT created FROM tasks WHERE project_id=? AND id=?)",
            (project_id, task_id, index, project_id, task_id),
        )["n"]

    def _flow_gate_failed(self, project_id, task_id, flow, index, gate, adapter, fake_scenario):
        limit = self.get_project_settings(project_id)["review_gate"]["max_rounds"]
        target = bounce_index(flow["steps"], index)
        failed = self._flow_failed_rounds(project_id, task_id, index)
        if gate["verdict"] == "REJECT":
            reason = f"{gate['role']} rejected the approach: it reported a fundamental flaw."
        elif failed >= limit:
            reason = f"{gate['role']} still found blocking problems after {failed} round(s), the limit."
        elif target is None:
            reason = f"{gate['role']} found blocking problems and no earlier step can fix them."
        else:
            return {
                "bounced_to": target,
                "next_execution": self._flow_dispatch(
                    project_id, task_id, flow, target, adapter, fake_scenario,
                    feedback=feedback_text(gate),
                ),
            }
        return self._flow_escalate(
            project_id, task_id, flow, index, reason, gate, adapter, fake_scenario
        )

    def _flow_escalate(self, project_id, task_id, flow, index, reason, detail, adapter, fake_scenario):
        escalation = self.get_project_settings(project_id)["review_gate"]["escalation"]
        row_id = "ESC-" + uuid.uuid4().hex[:16]
        with self.lock(project_id):
            with self.store.transaction() as conn:
                if conn.execute(
                    "SELECT 1 FROM flow_escalations WHERE project_id=? AND task_id=? AND status='OPEN'",
                    (project_id, task_id),
                ).fetchone():
                    return {"escalated": True, "reason": reason}
                conn.execute(
                    "INSERT INTO flow_escalations(id,project_id,task_id,step_index,role,reason,"
                    "detail,adapter,fake_scenario,escalation,status,created) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        row_id, project_id, task_id, index, flow["steps"][index]["role"], reason,
                        json.dumps(detail), adapter, fake_scenario, escalation, "OPEN", now(),
                    ),
                )
                self._transition(
                    conn, project_id, task_id, "AWAITING_PM_DECISION", f"Gate escalated: {reason}"
                )
            self.sync_documents(project_id)
        return {"escalated": True, "escalation_id": row_id, "reason": reason}

    def pending_flow_decision(self, project_id, task_id):
        row = self.store.one(
            "SELECT * FROM flow_escalations WHERE project_id=? AND task_id=? AND status='OPEN' "
            "ORDER BY created DESC LIMIT 1",
            (project_id, task_id),
        )
        if not row:
            return None
        detail = json.loads(row["detail"])
        return {
            "escalation_id": row["id"],
            "role": row["role"],
            "step_index": row["step_index"],
            "reason": row["reason"],
            "findings": detail.get("blocking") or [],
            "escalation": row["escalation"],
            "options": sorted(_DECISIONS),
        }

    def resolve_flow_gate(self, project_id, task_id, decision, note=""):
        """The PM's call on a gate that ran out of rounds or rejected the approach:
        "proceed" accepts the work and moves on, "retry" sends it back once more, "stop"
        cancels the task."""
        if decision not in _DECISIONS:
            raise BridgeError(f"decision must be one of {sorted(_DECISIONS)}")
        if not isinstance(note, str) or len(note) > 2000:
            raise BridgeError("note must be text of at most 2000 characters")
        row = self.store.one(
            "SELECT * FROM flow_escalations WHERE project_id=? AND task_id=? AND status='OPEN' "
            "ORDER BY created DESC LIMIT 1",
            (project_id, task_id),
        )
        if not row:
            raise BridgeError("No gate decision is pending for this task")
        flow = self._task_flow(project_id, task_id)
        if flow is None:
            raise BridgeError("This task does not run a step workflow")
        index = row["step_index"]
        if decision == "retry" and bounce_index(flow["steps"], index) is None:
            raise BridgeError("No earlier step exists to redo; choose proceed or stop")
        with self.store.transaction() as conn:
            conn.execute(
                "UPDATE flow_escalations SET status='RESOLVED',decision=?,decision_note=?,decided=? "
                "WHERE id=?",
                (decision, note or None, now(), row["id"]),
            )
        if decision == "stop":
            self.cancel_task(project_id, task_id, note or "Stopped at a workflow gate decision")
            return {"decision": decision, "task_id": task_id, "state": "CANCELLED"}
        adapter, fake_scenario = row["adapter"], row["fake_scenario"]
        if decision == "proceed":
            result = self._flow_continue(
                project_id, task_id, flow, index + 1, adapter, fake_scenario, pm_note=note
            )
        else:
            detail = json.loads(row["detail"])
            target = bounce_index(flow["steps"], index)
            feedback = feedback_text({**detail, "reason": row["reason"]})
            if note:
                feedback += f"\nPM decision: {note}"
            result = {
                "bounced_to": target,
                "next_execution": self._flow_dispatch(
                    project_id, task_id, flow, target, adapter, fake_scenario, feedback=feedback
                ),
            }
        return {"decision": decision, "task_id": task_id, **result}

    def flow_gate_history(self, project_id, task_id):
        """Every gate verdict of a task, oldest first (read-only)."""
        rows = self.store.all(
            "SELECT step_index,role,verdict,strictness,detail,source_execution_id,created "
            "FROM flow_gate_results WHERE project_id=? AND task_id=? ORDER BY created",
            (project_id, task_id),
        )
        return [{**r, "detail": json.loads(r["detail"])} for r in rows]
