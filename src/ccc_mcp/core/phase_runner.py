"""Phase runs (Stage B): one approved phase of one goal, run to a verified end through the existing
Task engine, with the Delivery PM repairing failed attempts inside hard limits.

What this is: a supervisor. It creates one Task for the phase (a fixed ID, never reused), lets the
existing Task/Workflow engine run it (Agent Studio roles and providers, the project's authorization,
Context, Review Gate and Host Test all unchanged), and acts only at the points where that engine
would otherwise wait for a person: the Task's SA report needs a reviewed plan, or a step failed. It
does not run agents itself and it is not a second engine.

What it will not do, however it is asked or whatever a model answers:

* run a phase that is not in an approved plan version (the run stores the version and hash it was
  started from) or whose dependencies have not passed, or start two phases at once;
* run any command from the plan. The only thing the host runs is the project's own, operator
  configured test command; a plan's `host_test` criterion counts as verified only when it is that
  command, and every other criterion needs a person to accept it;
* change the requirement, the acceptance criteria, the test command, a role, a provider or a
  permission. The repair spec has no field for any of them and the developer is told so;
* mark a phase PASSED unless the Task finished through its own Review Gate *and* the host observed
  the tests pass, none of the changes touched protected paths, and every criterion is accounted for;
* go on when it cannot tell what is safe. Budget exhausted, no progress, an unreachable provider,
  an unsafe leftover host test: it stops, says why, and waits for a person.

Counting units (all derived from durable rows, so a crash cannot make the counters drift):

* **Attempt**: one developer (RD or SD/RD) dispatch of the phase's Task. The first is attempt 1;
  every repair the Delivery PM asked for is the next. A quota fallback re-dispatch of the same role
  is not a new attempt. Default limit 3, the first included.
* **Agent dispatch**: one provider run attributable to the goal: every execution of its phase
  Tasks (whatever role, fallbacks included) plus every Delivery PM analysis. Default limit 20 per
  goal.
* **Wall clock**: the time a phase run was RUNNING (waiting for a person does not count), summed
  over the goal. Default 60 minutes.
* **Retry vs new Task**: a retry is a new attempt inside the same Task, started with the existing
  `restart_from_step`. A phase never gets a second Task.

Stage B stops after one phase. It does not start the next one, runs no resident process (each wake
is a short process that takes a lock, acts and exits) and never commits, pushes or releases.
"""

import hashlib
import json
import os
import sqlite3
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime

import portalocker

from ..adapters.storage import now
from ..infra.errors import BridgeError, PermissionDenied
from ..infra.security import canonical, reject_secrets
from .context import identifier
from .flows import BUILDER_ROLES
from .goal_plan import BUDGET_CAPS, DEFAULT_BUDGET
from .goal_repair import coverage
from .phase_repair import PhaseRepairMixin

ACTIVE = ("STARTING", "RUNNING")
LIVE = ("STARTING", "RUNNING", "PAUSED", "NEEDS_HUMAN", "AWAITING_ACCEPTANCE")
TERMINAL = ("PASSED", "EXHAUSTED", "CANCELLED")
PM_TIMEOUT_DEFAULT = 300
STALL_SECONDS = 60
NOTE_MAX = 2000

# reason code -> (what a person reads, can a person resume it)
REASONS = {
    "MANUAL_CRITERIA": ("The tests passed. Some acceptance criteria can only be judged by a person.", False),
    "PAUSED_BY_USER": ("Paused by a person.", True),
    "ATTEMPTS_EXHAUSTED": ("Every allowed attempt was used and the phase still does not pass.", False),
    "DISPATCH_BUDGET": ("The goal's agent-dispatch budget is used up.", False),
    "TIME_BUDGET": ("The goal's time budget is used up.", False),
    "NO_PROGRESS": ("The last repair changed nothing that matters: the same failure came back.", True),
    "PM_NEEDS_HUMAN": ("The Delivery PM could not establish a safe repair and asked for a person.", True),
    "PM_INVALID_OUTPUT": ("The Delivery PM's answer was not a valid repair spec, so nothing was acted on.", True),
    "PM_TIMEOUT": ("The Delivery PM did not answer in time.", True),
    "PM_UNAVAILABLE": ("The Delivery PM's provider is unavailable.", True),
    "PM_QUOTA": ("The Delivery PM's provider reports its quota is exhausted.", True),
    "QUOTA_EXHAUSTED": ("A provider reports its quota is exhausted.", True),
    "PROVIDER_UNAVAILABLE": ("A provider could not be started.", True),
    "PROVIDER_TIMEOUT": ("A provider run timed out.", True),
    "INTERRUPTED": ("A worker was interrupted. Its changes need a look before anything runs again.", True),
    "EXECUTION_ERROR": ("An agent run failed.", True),
    "HOST_TEST_UNSAFE": ("A host test may still be running; it was not run again.", True),
    "HOST_TEST_INTERRUPTED": ("The host test was interrupted, so nothing is verified.", True),
    "ENVIRONMENT_BLOCKED": ("The test environment is blocked; a code change cannot fix it.", True),
    "PROTECTED_PATH": ("A change touched protected files (git, ccc-mcp, CI or test configuration).", True),
    "TASK_NEEDS_DECISION": ("The task is waiting for a decision that is not the runner's to make.", True),
    "PLAN_REVIEW_NEEDED": ("The analyst asked for a decision instead of giving a plan.", True),
    "TASK_STOPPED": ("The task was stopped outside the runner.", False),
    "EVIDENCE_MISSING": ("The task finished but the host test evidence does not support passing.", True),
}


class BudgetExhausted(PermissionDenied):
    """A dispatch the goal's declared budget does not allow."""

    def __init__(self, code, message):
        self.code = code
        super().__init__(message)


def _clean_note(value):
    if value is None or not str(value).strip():
        return ""
    text = str(value).replace("\r\n", "\n").strip()
    if len(text) > NOTE_MAX:
        raise BridgeError(f"Note must be at most {NOTE_MAX} characters")
    reject_secrets(text)
    return text


def phase_task_id(goal_id, phase_id):
    """The Task a phase runs as. Fixed by the goal and phase, so it cannot collide with TASK-NNNN
    and the same phase can never map to two Tasks."""
    return identifier(f"{goal_id}-{phase_id}")


def phase_requirement(goal, phase, handoff=None):
    """The Task's requirement text. `handoff` is the context package of the phases that already
    passed (Stage C): reference material, never a change to this phase's scope or criteria."""
    outputs = "\n".join(f"- {item}" for item in phase["outputs"]) or "- (none stated)"
    text = (
        f"Goal {goal['id']}: {goal['title']}\n\nOverall requirement of the goal (context only):\n"
        f"{goal['requirement']}\n\n"
        f"THIS TASK IS PHASE {phase['id']} - {phase['title']} ONLY.\n"
        f"Scope of this phase:\n{phase['scope']}\n\nWhat this phase delivers:\n{outputs}\n\n"
        "Other phases of the goal are out of scope for this task: do not start or pre-empt them."
    )
    if handoff:
        text += (
            "\n\nContext from phases that already passed (verified by the host; reference only, "
            "do not redo or change their work unless this phase's scope says so):\n" + handoff
        )
    return text


class PhaseHub(PhaseRepairMixin):
    # Which adapter phase runs use when the caller does not say. Tests and demos set "fake".
    phase_adapter_default = "real"

    # ---- small readers ----------------------------------------------------------------------

    def _phase_run(self, run_id):
        row = self.store.one("SELECT * FROM goal_phase_runs WHERE id=?", (run_id,))
        if not row:
            raise BridgeError("Phase run not found")
        return row

    def _phase_run_of_task(self, project_id, task_id, conn=None):
        """The live phase run that supervises this Task, or None. Called on every dispatch, so it
        is one indexed read, and a Task that is not a phase's costs nothing else."""
        query = (
            "SELECT * FROM goal_phase_runs WHERE project_id=? AND task_id=? AND status IN "
            "('STARTING','RUNNING','PAUSED','NEEDS_HUMAN','AWAITING_ACCEPTANCE')"
        )
        row = (
            conn.execute(query, (project_id, task_id)).fetchone() if conn is not None
            else self.store.one(query, (project_id, task_id))
        )
        return dict(row) if row else None

    def _run_lock_path(self, run_id):
        path = self.home / "locks" / f"phaserun-{run_id}.lock"
        path.parent.mkdir(exist_ok=True)
        return path

    def _stepper_alive(self, run_id):
        try:
            with portalocker.Lock(self._run_lock_path(run_id), timeout=0):
                return False
        except portalocker.exceptions.LockException:
            return True

    @staticmethod
    def _clock(value):
        return datetime.fromisoformat(value.replace("Z", "+00:00"))

    # ---- budget -----------------------------------------------------------------------------

    def goal_usage(self, project_id, goal_id, conn=None):
        """What the goal has used so far, over all of its phases. Derived from durable rows, so it
        is right after any crash. Counting policy, in one place:

        * `executions`: every provider run of a Task a phase run of this goal supervises (any role,
          fallbacks, interrupted and failed ones included), counted when the run is created;
        * `analyses`: every Delivery PM failure analysis, counted when it is recorded;
        * `plans`: every Delivery PM planning run asked for after the goal run began (a revised plan);
          planning done before the goal run started is outside the run's budget;
        * `seconds`: time a phase run was STARTING/RUNNING plus time the goal run was verifying.
          Waiting for a person, a pause and a stopped run do not count. A machine that sleeps while
          a run is active does count: it is wall-clock time.
        Token and money use are unknown: the provider CLIs do not report them."""
        def one(query, args):
            row = (conn.execute(query, args).fetchone() if conn is not None
                   else self.store.one(query, args))
            return row["n"]

        def many(query, args):
            return conn.execute(query, args).fetchall() if conn is not None else self.store.all(query, args)

        executions = one(
            "SELECT COUNT(*) AS n FROM agent_executions e JOIN goal_phase_runs r "
            "ON r.project_id=e.project_id AND r.task_id=e.task_id WHERE r.project_id=? AND r.goal_id=?",
            (project_id, goal_id))
        analyses = one(
            "SELECT COUNT(*) AS n FROM goal_phase_repairs WHERE project_id=? AND goal_id=?",
            (project_id, goal_id))
        plans = one(
            "SELECT COUNT(*) AS n FROM goal_plan_runs p JOIN goal_runs g ON g.project_id=p.project_id "
            "AND g.goal_id=p.goal_id WHERE p.project_id=? AND p.goal_id=? AND p.created>=g.created",
            (project_id, goal_id))
        seconds = 0.0
        for table in ("goal_phase_runs", "goal_runs"):
            for row in many(
                f"SELECT active_seconds,active_since FROM {table} WHERE project_id=? AND goal_id=?",
                (project_id, goal_id),
            ):
                seconds += row["active_seconds"] or 0
                if row["active_since"]:
                    seconds += max(
                        0.0, (datetime.now(UTC) - self._clock(row["active_since"])).total_seconds())
        return {"executions": executions, "analyses": analyses, "plans": plans, "seconds": seconds}

    def goal_budget(self, project_id, goal_id, limits, conn=None):
        usage = self.goal_usage(project_id, goal_id, conn)
        used = usage["executions"] + usage["analyses"] + usage["plans"]
        minutes = usage["seconds"] / 60
        return {
            "dispatches": {"used": used, "max": limits["max_agent_dispatches"],
                           "left": max(0, limits["max_agent_dispatches"] - used),
                           "executions": usage["executions"], "analyses": usage["analyses"],
                           "plans": usage["plans"]},
            "minutes": {"used": round(minutes, 1), "max": limits["max_wall_clock_minutes"],
                        "left": round(max(0.0, limits["max_wall_clock_minutes"] - minutes), 1)},
            "tokens": {"available": False, "reason": "The provider CLIs do not report token or cost use"},
            "enforced": True,
        }

    def reserve_dispatch(self, conn, project_id, goal_id, limits, what):
        """Check the goal's budget and, in the same transaction, let the caller record the dispatch
        it is about to make. Because the transaction is `BEGIN IMMEDIATE`, two runners cannot both
        take the last unit: the second sees the first one's row and is refused."""
        budget = self.goal_budget(project_id, goal_id, limits, conn)
        if budget["dispatches"]["left"] < 1:
            raise BudgetExhausted(
                "DISPATCH_BUDGET",
                f"Goal budget exhausted: {budget['dispatches']['used']} of "
                f"{budget['dispatches']['max']} agent dispatches used ({what})")
        if budget["minutes"]["left"] <= 0:
            raise BudgetExhausted(
                "TIME_BUDGET", f"Goal budget exhausted: {budget['minutes']['max']} minutes used")
        return budget

    def phase_budget(self, run):
        """What the goal has used of its declared budget, and what is left (attempts are per phase;
        the rest is for the whole goal, however many phases there are)."""
        limits = json.loads(run["budget"])
        project_id = run["project_id"]
        builders = self.store.one(
            "SELECT COUNT(*) AS n FROM agent_executions WHERE project_id=? AND task_id=? "
            "AND role IN ('RD','SD/RD') AND COALESCE(json_extract(options,'$.report_schema'),"
            "'agent_report')='agent_report' AND COALESCE(json_extract(options,'$.read_only'),0)=0 "
            "AND json_extract(options,'$.fallback_from') IS NULL",
            (project_id, run["task_id"]),
        )["n"]
        return {
            "attempts": {"used": builders, "max": limits["max_attempts_per_phase"],
                         "left": max(0, limits["max_attempts_per_phase"] - builders)},
            **self.goal_budget(project_id, run["goal_id"], limits),
        }

    def _phase_dispatch_gate(self, project_id, task_id, role, report_schema, read_only, fallback_from):
        """Called by dispatch_agent for every dispatch. Refuses one the goal's budget does not
        allow, whoever asks (the engine's own chain, a repair, a person), so the limit holds in
        code and not in a prompt."""
        run = self._phase_run_of_task(project_id, task_id)
        if not run:
            return
        budget = self.phase_budget(run)
        builder = (
            role in BUILDER_ROLES and report_schema == "agent_report" and not read_only
            and not fallback_from
        )
        refusal = None
        if budget["dispatches"]["left"] < 1:
            refusal = BudgetExhausted(
                "DISPATCH_BUDGET",
                f"Goal budget exhausted: {budget['dispatches']['used']} of "
                f"{budget['dispatches']['max']} agent dispatches used",
            )
        elif budget["minutes"]["left"] <= 0:
            refusal = BudgetExhausted(
                "TIME_BUDGET", f"Goal budget exhausted: {budget['minutes']['max']} minutes used")
        elif builder and budget["attempts"]["left"] < 1:
            refusal = BudgetExhausted(
                "ATTEMPTS_EXHAUSTED",
                f"Phase budget exhausted: {budget['attempts']['used']} of "
                f"{budget['attempts']['max']} attempts used",
            )
        if refusal:
            # The refusal can happen inside a worker whose caller swallows it. Leave a durable record
            # so the run's next look ends it as EXHAUSTED instead of waiting for a dispatch that will
            # never come.
            with self.store.transaction() as conn:
                self._event(conn, run["project_id"], run["goal_id"], "phase_dispatch_refused",
                            "system", {"run_id": run["id"], "code": refusal.code,
                                       "role": role, "message": str(refusal)})
            raise refusal

    # ---- state changes ----------------------------------------------------------------------

    def _set_status(
        self, run_id, new, *, expect=None, reason=None, detail=None, resume_to=None,
        kind=None, actor="system", extra=None,
    ):
        """Move a run to `new` if it is still in one of the `expect` statuses; the one place a
        status changes. Wall-clock time is accounted for here: it runs while the run is STARTING
        or RUNNING and stops the moment it is not. Returns True when it moved."""
        stamp = now()
        with self.store.transaction() as conn:
            row = conn.execute("SELECT * FROM goal_phase_runs WHERE id=?", (run_id,)).fetchone()
            if not row:
                raise BridgeError("Phase run not found")
            if row["status"] == new or (expect and row["status"] not in expect):
                return False
            if row["status"] in TERMINAL:
                return False
            seconds, since = row["active_seconds"] or 0.0, row["active_since"]
            if row["status"] in ACTIVE and since:
                seconds += max(0.0, (datetime.now(UTC) - self._clock(since)).total_seconds())
                since = None
            if new in ACTIVE:
                since = since or stamp
            else:
                since = None
            conn.execute(
                "UPDATE goal_phase_runs SET status=?,reason=?,detail=?,resume_to=?,active_seconds=?,"
                "active_since=?,updated=?,finished=? WHERE id=?",
                (new, reason, canonical(detail) if detail is not None else None, resume_to, seconds,
                 since, stamp, stamp if new in TERMINAL else None, run_id),
            )
            self._event(
                conn, row["project_id"], row["goal_id"], kind or f"phase_run_{new.lower()}", actor,
                {"run_id": run_id, "phase_id": row["phase_id"], "from": row["status"], "to": new,
                 "reason": reason, "detail": detail, **(extra or {})},
            )
            owner = row["goal_run_id"]
        # A goal run supervises this phase: tell it that something changed (after the commit, so
        # what it reads is what was just written). Never raises.
        if owner and (new not in ACTIVE or row["status"] in ("PAUSED", "NEEDS_HUMAN", "AWAITING_ACCEPTANCE")):
            self._notify_goal_run(owner)
        return True

    def _stop_for_person(self, run_id, reason, detail=None, resume_to="RUNNING", status="NEEDS_HUMAN"):
        """The safe default: whenever the runner cannot tell what is safe, it stops here."""
        return self._set_status(
            run_id, status, expect=ACTIVE, reason=reason,
            detail={"message": REASONS[reason][0], **(detail or {})}, resume_to=resume_to,
            kind="phase_run_needs_human" if status == "NEEDS_HUMAN" else None,
        )

    def _exhaust(self, run_id, reason, detail=None):
        """A budget is spent: the run ends, and its Task is stopped so nothing keeps working."""
        run = self._phase_run(run_id)
        moved = self._set_status(
            run_id, "EXHAUSTED", expect=ACTIVE + ("PAUSED", "NEEDS_HUMAN"), reason=reason,
            detail={"message": REASONS[reason][0], **(detail or {})}, kind="phase_run_exhausted",
        )
        if moved:
            self._close_open_attempt(run, "FAILED", f"stopped: {reason}")
            try:
                self.cancel_task(run["project_id"], run["task_id"], f"Phase budget: {reason}")
            except BridgeError:
                pass
        return moved

    # ---- starting a phase ---------------------------------------------------------------------

    def _approved_phase(self, project_id, goal_id, phase_id, synthetic=None):
        """The phase as approved: (goal row, plan version row, phase dict, plan dict, approval row).
        Raises unless the goal's current plan version is the approved one. `synthetic` is an
        integration fix the goal run composed (never a phase of the plan)."""
        with self.store.connect() as conn:
            conn.execute("BEGIN")
            goal = self._goal(conn, project_id, goal_id)
            if goal["cancelled"]:
                raise BridgeError("The goal was cancelled")
            if not goal["current_version"]:
                raise BridgeError("This goal has no plan")
            if conn.execute(
                "SELECT 1 FROM goal_plan_runs WHERE project_id=? AND goal_id=? AND status IN "
                "('QUEUED','RUNNING')", (project_id, goal["id"]),
            ).fetchone():
                raise BridgeError("The Delivery PM is still planning this goal")
            version = conn.execute(
                "SELECT * FROM goal_plan_versions WHERE project_id=? AND goal_id=? AND version=?",
                (project_id, goal["id"], goal["current_version"]),
            ).fetchone()
            approval = conn.execute(
                "SELECT * FROM goal_approvals WHERE project_id=? AND goal_id=? AND plan_version=?",
                (project_id, goal["id"], goal["current_version"]),
            ).fetchone()
        if not approval or approval["plan_sha256"] != version["sha256"]:
            raise PermissionDenied(
                "The current plan version is not approved. A person must approve exactly this "
                "version before any phase can run"
            )
        plan = json.loads(version["content"])
        if synthetic is not None:
            return dict(goal), dict(version), synthetic, plan, dict(approval)
        phase = next((item for item in plan["phases"] if item["id"] == phase_id), None)
        if phase is None:
            raise BridgeError(f"The approved plan has no phase {phase_id!r}")
        return dict(goal), dict(version), phase, plan, dict(approval)

    def _bound_phase(self, run):
        """The phase exactly as it was approved when the run started: (goal, version row, phase,
        plan). Read from the plan version the run is bound to, and its hash is checked again, so a
        run never acts on anything but what a person approved."""
        with self.store.connect() as conn:
            goal = self._goal(conn, run["project_id"], run["goal_id"])
            version = conn.execute(
                "SELECT * FROM goal_plan_versions WHERE project_id=? AND goal_id=? AND version=?",
                (run["project_id"], run["goal_id"], run["plan_version"]),
            ).fetchone()
        if not version or version["sha256"] != run["plan_sha256"]:
            raise BridgeError("The plan this run was started from is no longer intact")
        plan = json.loads(version["content"])
        if run["synthetic"]:
            return dict(goal), dict(version), json.loads(run["synthetic"]), plan
        phase = next(item for item in plan["phases"] if item["id"] == run["phase_id"])
        return dict(goal), dict(version), phase, plan

    def check_phase_runnable(self, project_id, goal, phase, adapter, requirement=None):
        """Everything that must be true for a phase to run, without creating anything: the project is
        authorized, a test command is configured, the workflow is a step workflow with a developer
        step, and no role needs a person at a browser. Returns (access, test_command, flow id,
        workflow choice, workflow catalog entry). Shared by a phase start and by a goal run's
        pre-flight, so the goal run refuses at the start, not half way through."""
        access = self.get_project_access(project_id)
        if access["mode"] != "read-write":
            raise PermissionDenied(
                "The project is not authorized for writes. Approving a goal does not authorize "
                f"work on the project; run `ccc-mcp project authorize {project_id}` locally first"
            )
        settings = self.get_project_settings(project_id)
        test_command = settings.get("test_command")
        if not test_command:
            raise BridgeError(
                "Configure the project's test command first (`ccc-mcp config test-command`). A phase "
                "is accepted only on a test command the operator chose, never on one picked "
                "automatically or proposed by a model"
            )
        requirement = requirement or phase_requirement(goal, phase)
        choice = self._resolve_task_workflow(project_id, requirement, phase["workflow"])
        flow_id = choice[0]
        catalog = {flow["id"]: flow for flow in self.workflow_catalog(project_id)["workflows"]}
        if not any(step["role"] in BUILDER_ROLES for step in catalog[flow_id]["steps"]):
            raise BridgeError(
                f"The {catalog[flow_id]['name']} workflow has no developer step (RD or SD/RD), so "
                "there is nothing a repair could be sent back to"
            )
        if catalog[flow_id].get("legacy"):
            raise BridgeError(
                "Phase runs need a step workflow (for example Standard). This project runs the "
                "Classic workflow, which cannot hand a failed step back to the Delivery PM"
            )
        if adapter == "real":
            self._require_unattended(project_id, catalog[flow_id])
        return access, test_command, flow_id, choice, catalog[flow_id]

    def start_phase_run(
        self, project_id, goal_id, phase_id, *, adapter=None, fake_scenario="success",
        pm_provider=None, pm_scenario="success", pm_timeout=PM_TIMEOUT_DEFAULT,
        goal_run_id=None, handoff=None, synthetic=None, budget_override=None,
    ):
        """Start one phase of an approved plan, on purpose. Returns the run; starting the same
        phase again returns the same run and creates nothing. A goal run (Stage C) starts phases
        through here too, passing `goal_run_id`, the context package of the phases before it, and
        the goal's frozen budget; a person cannot start a phase by hand while a goal run is live."""
        self.project(project_id)
        goal_id, phase_id = identifier(goal_id), identifier(phase_id)
        adapter = adapter or self.phase_adapter_default
        if adapter not in {"real", "fake"}:
            raise BridgeError("Unsupported adapter")
        if not isinstance(pm_timeout, int) or isinstance(pm_timeout, bool) or not 5 <= pm_timeout <= 900:
            raise BridgeError("pm_timeout must be 5 to 900 seconds")
        existing = self.store.one(
            "SELECT * FROM goal_phase_runs WHERE project_id=? AND goal_id=? AND phase_id=?",
            (project_id, goal_id, phase_id),
        )
        if existing:
            if existing["status"] == "STARTING":
                self._finish_start(existing["id"])
            return {**self.get_phase_run(project_id, existing["id"]), "already_started": True}
        if goal_run_id is None:
            controlled = self.store.one(
                "SELECT id,status FROM goal_runs WHERE project_id=? AND goal_id=? AND status IN "
                "('RUNNING','WAITING_USER','PAUSED','VERIFYING')", (project_id, goal_id))
            if controlled:
                raise BridgeError(
                    f"Goal run {controlled['id']} is {controlled['status'].lower().replace('_', ' ')} "
                    "and starts this goal's phases itself; a phase cannot be started by hand")

        goal, version, phase, plan, approval = self._approved_phase(
            project_id, goal_id, phase_id, synthetic)
        requirement = phase_requirement(goal, phase, handoff)
        access, test_command, flow_id, choice, entry = self.check_phase_runnable(
            project_id, goal, phase, adapter, requirement)
        if synthetic is None:
            for dependency in phase["depends_on"]:
                done = self.store.one(
                    "SELECT status FROM goal_phase_runs WHERE project_id=? AND goal_id=? AND phase_id=?",
                    (project_id, goal_id, dependency),
                )
                if not done or done["status"] != "PASSED":
                    raise BridgeError(f"Phase {phase_id} depends on {dependency}, which has not passed")
        busy = self.store.one(
            "SELECT goal_id,phase_id FROM goal_phase_runs WHERE project_id=? AND status IN "
            "('STARTING','RUNNING','PAUSED','NEEDS_HUMAN','AWAITING_ACCEPTANCE')", (project_id,),
        )
        if busy:
            raise BridgeError(
                f"Phase {busy['phase_id']} of {busy['goal_id']} is still open; finish or cancel it first"
            )
        pm = {"scenario": pm_scenario}
        if adapter == "real":
            pm = {**self._planning_settings(project_id, pm_provider, adapter)}
        else:
            pm = {**self._planning_settings(project_id, pm_provider, adapter), **pm}
        if budget_override is not None:
            budget = dict(budget_override)
        else:
            budget = {**DEFAULT_BUDGET, **{
                key: plan.get("budget", {}).get(key, DEFAULT_BUDGET[key]) for key in DEFAULT_BUDGET}}
            for key, cap in BUDGET_CAPS.items():
                budget[key] = min(int(budget[key]), cap)
        criteria = [
            {"id": item["id"], "statement": item["statement"], "kind": item["verify"]["kind"],
             "command": item["verify"].get("command"),
             "description": item["verify"].get("description"),
             "coverage": coverage(item, test_command)}
            for item in phase["acceptance"]
        ]
        acceptance = [item["statement"] for item in criteria]
        reject_secrets([requirement, acceptance])
        task_id = phase_task_id(goal["id"], phase_id)
        run_id = "PRUN-" + uuid.uuid4().hex[:16]
        actor = self._goal_actor()
        stamp = now()
        try:
            self._record_run(
                project_id, goal, phase, phase_id, version, approval, task_id, flow_id, requirement,
                acceptance, adapter, fake_scenario, pm, pm_timeout, budget, criteria, test_command,
                actor, stamp, run_id, access, choice, goal_run_id, handoff, synthetic)
        except sqlite3.IntegrityError:
            # Another caller recorded this phase a moment ago: there is one run, and it is theirs.
            winner = self.store.one(
                "SELECT id FROM goal_phase_runs WHERE project_id=? AND goal_id=? AND phase_id=?",
                (project_id, goal["id"], phase_id))
            if not winner:
                raise
            return {**self.get_phase_run(project_id, winner["id"]), "already_started": True}
        self._finish_start(run_id, requirement=requirement, acceptance=acceptance)
        return {**self.get_phase_run(project_id, run_id), "already_started": False}

    def _record_run(
        self, project_id, goal, phase, phase_id, version, approval, task_id, flow_id, requirement,
        acceptance, adapter, fake_scenario, pm, pm_timeout, budget, criteria, test_command, actor,
        stamp, run_id, access, choice, goal_run_id=None, handoff=None, synthetic=None,
    ):
        with self.store.transaction() as conn:
            # The goal's plan cannot move under a run, and the approval is checked again here, in
            # the transaction that records the run, so a plan edited a moment ago cannot slip by.
            current = conn.execute(
                "SELECT current_version FROM goals WHERE project_id=? AND id=?",
                (project_id, goal["id"]),
            ).fetchone()
            if current["current_version"] != version["version"]:
                raise BridgeError("The plan changed while the phase was being started; try again")
            if conn.execute(
                "SELECT 1 FROM tasks WHERE project_id=? AND id=?", (project_id, task_id)
            ).fetchone():
                raise BridgeError(f"Task {task_id} already exists and is not this phase's run")
            if goal_run_id:
                owner = conn.execute(
                    "SELECT status FROM goal_runs WHERE id=?", (goal_run_id,)).fetchone()
                if not owner or owner["status"] not in ("RUNNING", "VERIFYING"):
                    raise BridgeError("The goal run is not running, so it starts no phase")
            conn.execute(
                "INSERT INTO goal_phase_runs(id,project_id,goal_id,phase_id,plan_version,plan_sha256,"
                "approval_id,task_id,workflow_id,requirement_sha256,adapter,fake_scenario,pm_settings,"
                "pm_timeout,budget,criteria,test_command,status,creator,created,updated,kind,"
                "synthetic,handoff,goal_run_id) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'STARTING',?,?,?,?,?,?,?)",
                (run_id, project_id, goal["id"], phase_id, version["version"], version["sha256"],
                 approval["id"], task_id, flow_id,
                 hashlib.sha256(canonical([requirement, acceptance]).encode()).hexdigest(),
                 adapter, fake_scenario if adapter == "fake" else None, canonical(pm), pm_timeout,
                 canonical(budget), canonical(criteria), canonical(list(test_command)), actor,
                 stamp, stamp, "INTEGRATION" if synthetic else "PLAN",
                 canonical(synthetic) if synthetic else None, handoff, goal_run_id),
            )
            self._event(
                conn, project_id, goal["id"], "phase_run_started", actor,
                {"run_id": run_id, "phase_id": phase_id, "task_id": task_id,
                 "plan_version": version["version"], "plan_sha256": version["sha256"],
                 "approval_id": approval["id"], "workflow": flow_id, "budget": budget,
                 "project_authorization": access["authorization_id"],
                 "goal_run_id": goal_run_id, "kind": "INTEGRATION" if synthetic else "PLAN",
                 "handoff_sha256": hashlib.sha256(handoff.encode()).hexdigest() if handoff else None,
                 "coverage": {item["id"]: item["coverage"] for item in criteria}},
            )

    def _require_unattended(self, project_id, flow):
        """A role played by ChatGPT in a browser cannot run without a person, so a flow that
        contains one cannot be run by this."""
        for step in flow["steps"]:
            provider = self.resolve_agent_settings(project_id, step["role"], "auto")["provider"]
            if provider == "chatgpt":
                raise BridgeError(
                    f"Role {step['role']} in the {flow['name']} workflow is played by ChatGPT in a "
                    "browser, which cannot run unattended. Choose Codex or Claude Code for it in "
                    "Agent Studio"
                )

    def _finish_start(self, run_id, requirement=None, acceptance=None):
        """The steps after the run is recorded, each safe to repeat: create the Task if it is
        not there, dispatch its first step if nothing has run, open attempt 1, go RUNNING."""
        run = self._phase_run(run_id)
        if run["status"] != "STARTING":
            return
        project_id, task_id = run["project_id"], run["task_id"]
        if not self.store.one("SELECT 1 FROM tasks WHERE project_id=? AND id=?", (project_id, task_id)):
            if requirement is None:
                goal, _, phase, _ = self._bound_phase(run)
                requirement = phase_requirement(goal, phase, run["handoff"])
                acceptance = [item["statement"] for item in json.loads(run["criteria"])]
            try:
                created = self.create_task(
                    project_id, requirement, acceptance, task_id=task_id,
                    workflow=run["workflow_id"],
                )
            except BridgeError as exc:
                if "already exists" not in str(exc):
                    raise
                created = self.task(project_id, task_id)       # another caller created it first
            if hashlib.sha256(canonical([created["requirement"], acceptance]).encode()).hexdigest() != (
                run["requirement_sha256"]
            ):
                self._stop_for_person(run_id, "EVIDENCE_MISSING", {"why": "task requirement differs"})
                return
        task = self.task(project_id, task_id)
        started = self.store.one(
            "SELECT COUNT(*) AS n FROM agent_executions WHERE project_id=? AND task_id=?",
            (project_id, task_id),
        )["n"]
        if task["state"] == "CREATED" and not started:
            self._open_attempt(run, "INITIAL", None)
            self._set_status(run_id, "RUNNING", expect=("STARTING",), kind="phase_run_running")
            try:
                self._flow_start(project_id, task, run["adapter"], run["fake_scenario"] or "success")
            except BudgetExhausted as exc:
                self._exhaust(run_id, exc.code, {"error": str(exc)})
            except (BridgeError, PermissionDenied) as exc:
                self._stop_for_person(run_id, "PROVIDER_UNAVAILABLE", {"error": str(exc)[:500]})
            return
        self._open_attempt(run, "INITIAL", None)
        self._set_status(run_id, "RUNNING", expect=("STARTING",), kind="phase_run_running")

    # ---- attempts ------------------------------------------------------------------------------

    def _open_attempt(self, run, kind, repair_id):
        """Record that attempt `n` begins. Safe to repeat: a number is recorded once."""
        with self.store.transaction() as conn:
            number = conn.execute(
                "SELECT COALESCE(MAX(number),0) FROM goal_phase_attempts WHERE run_id=?", (run["id"],)
            ).fetchone()[0]
            if kind == "INITIAL" and number:
                return number
            if number and conn.execute(
                "SELECT 1 FROM goal_phase_attempts WHERE run_id=? AND number=? AND repair_id IS ? "
                "AND status='RUNNING'", (run["id"], number, repair_id),
            ).fetchone() and repair_id is not None:
                return number
            number += 1
            conn.execute(
                "INSERT INTO goal_phase_attempts(id,run_id,project_id,goal_id,phase_id,number,kind,"
                "repair_id,status,started) VALUES(?,?,?,?,?,?,?,?,'RUNNING',?)",
                ("PATT-" + uuid.uuid4().hex[:16], run["id"], run["project_id"], run["goal_id"],
                 run["phase_id"], number, kind, repair_id, now()),
            )
            self._event(
                conn, run["project_id"], run["goal_id"], "phase_attempt_started", "system",
                {"run_id": run["id"], "phase_id": run["phase_id"], "attempt": number, "kind": kind,
                 "repair_id": repair_id},
            )
        return number

    def _close_open_attempt(self, run, status, outcome, evidence=None):
        with self.store.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM goal_phase_attempts WHERE run_id=? AND status='RUNNING' "
                "ORDER BY number DESC LIMIT 1", (run["id"],),
            ).fetchone()
            if not row:
                return None
            conn.execute(
                "UPDATE goal_phase_attempts SET status=?,outcome=?,evidence=?,finished=? WHERE id=?",
                (status, outcome, canonical(evidence) if evidence is not None else None, now(),
                 row["id"]),
            )
            self._event(
                conn, run["project_id"], run["goal_id"], "phase_attempt_finished", "system",
                {"run_id": run["id"], "phase_id": run["phase_id"], "attempt": row["number"],
                 "status": status, "outcome": outcome,
                 "signature": (evidence or {}).get("signature")},
            )
            return row["number"]

    # ---- control: pause, resume, cancel, accept ---------------------------------------------------

    def pause_phase_run(self, project_id, run_id, reason=""):
        """Stop new work from starting. A step already running finishes; nothing after it begins
        until a person resumes."""
        run = self._phase_run(run_id)
        if run["project_id"] != project_id:
            raise BridgeError("Phase run not found")
        note = _clean_note(reason)
        if run["status"] not in ("STARTING", "RUNNING"):
            raise BridgeError(f"A run that is {run['status']} cannot be paused")
        actor = self._goal_actor()
        if self._set_status(
            run_id, "PAUSED", expect=ACTIVE, reason="PAUSED_BY_USER", resume_to="RUNNING",
            detail={"message": REASONS["PAUSED_BY_USER"][0], "note": note}, actor=actor,
            kind="phase_run_paused",
        ):
            try:
                self.pause_task(project_id, run["task_id"])
            except PermissionDenied:
                pass
        return self.get_phase_run(project_id, run_id)

    def resume_phase_run(
        self, project_id, run_id, note="", acknowledge=None, operator_confirmed=False, spawn=True,
    ):
        """Resume a paused run, or one that stopped for a person. What was stopped for is dealt
        with first (an unsafe host test stays refused unless the operator confirms), nothing that
        already ran is run again, and no budget is added: whatever is left is all there is."""
        run = self._phase_run(run_id)
        if run["project_id"] != project_id:
            raise BridgeError("Phase run not found")
        note = _clean_note(note)
        if run["status"] not in ("PAUSED", "NEEDS_HUMAN"):
            raise BridgeError(f"A run that is {run['status']} cannot be resumed")
        if run["reason"] and run["reason"] in REASONS and not REASONS[run["reason"]][1]:
            raise BridgeError(f"This run cannot be resumed: {REASONS[run['reason']][0]}")
        if run["reason"] == "PROTECTED_PATH" and acknowledge != "protected-paths":
            raise PermissionDenied(
                "Files that are protected were changed. Look at the diff; to continue anyway, "
                "resume with acknowledge='protected-paths'"
            )
        actor = self._goal_actor()
        if self._set_status(
            run_id, "RUNNING", expect=("PAUSED", "NEEDS_HUMAN"), kind="phase_run_resumed", actor=actor,
            detail={"was": run["reason"], "note": note or None, "acknowledged": acknowledge},
            # A step that finished while the run was paused had its result recorded, but the engine's
            # hand-over to the next role is refused for a paused Task. The next look at the run
            # (a short worker process, never this request) does that hand-over, once.
            resume_to="HANDOVER",
        ):
            control = self.task_control(project_id, run["task_id"])
            if control["paused"]:
                self.resume_task(project_id, run["task_id"])
            self._resume_work(run_id, run["reason"], operator_confirmed=operator_confirmed)
        if spawn:
            self.wake_phase_run(run_id)
        return self.get_phase_run(project_id, run_id)

    def cancel_phase_run(self, project_id, run_id, reason=""):
        """End the run and stop its Task. Whatever a running worker has already written stays; no
        further step starts."""
        run = self._phase_run(run_id)
        if run["project_id"] != project_id:
            raise BridgeError("Phase run not found")
        note = _clean_note(reason)
        if run["status"] in TERMINAL:
            return self.get_phase_run(project_id, run_id)
        actor = self._goal_actor()
        if self._set_status(
            run_id, "CANCELLED", reason="CANCELLED_BY_USER", actor=actor,
            detail={"note": note}, kind="phase_run_cancelled",
        ):
            self._close_open_attempt(run, "FAILED", "cancelled")
            try:
                self.cancel_task(project_id, run["task_id"], note or "Phase run cancelled")
            except (BridgeError, PermissionDenied):
                pass
        return self.get_phase_run(project_id, run_id)

    def accept_phase_description(self, project_id, run_id):
        """What a person reads before accepting the criteria no command can decide."""
        run = self._phase_run(run_id)
        pending = [c for c in json.loads(run["criteria"]) if c["coverage"] != "host_test"]
        lines = [f"Accept phase {run['phase_id']} of {run['goal_id']}", "",
                 "The host tests passed and the Review Gate passed. You are accepting these "
                 "criteria yourself:"]
        lines += [f"- {c['id']}: {c['statement']} ({c['kind']})" for c in pending]
        return {
            "challenge": f"ACCEPT {run['goal_id']} {run['phase_id']}",
            "description": "\n".join(lines), "criteria": pending,
        }

    def accept_phase_local(self, project_id, run_id):
        from .approval import confirm_local

        info = self.accept_phase_description(project_id, run_id)
        identity = confirm_local(info["description"], info["challenge"])
        return self._accept(project_id, run_id, identity)

    def accept_phase_web(self, project_id, run_id, typed_confirmation):
        info = self.accept_phase_description(project_id, run_id)
        if typed_confirmation != info["challenge"]:
            raise PermissionDenied("The confirmation text does not match.")
        return self._accept(project_id, run_id, self._goal_actor())

    def _accept(self, project_id, run_id, identity):
        run = self._phase_run(run_id)
        if run["project_id"] != project_id or run["status"] != "AWAITING_ACCEPTANCE":
            raise BridgeError("This run is not waiting for a person's acceptance")
        pending = [c["id"] for c in json.loads(run["criteria"]) if c["coverage"] != "host_test"]
        if self._set_status(
            run_id, "PASSED", expect=("AWAITING_ACCEPTANCE",), reason="PASSED_WITH_ACCEPTANCE",
            actor=identity, kind="phase_run_passed",
            detail={"accepted_by": identity, "accepted_criteria": pending},
        ):
            self._close_open_attempt(self._phase_run(run_id), "PASSED", "accepted by a person")
        return self.get_phase_run(project_id, run_id)

    def wait_for_phase_run(self, project_id, run_id, timeout=3600, poll=1.0):
        """Block until the run is no longer being worked on (for the CLI). Returns the run."""
        deadline = time.monotonic() + timeout
        while True:
            run = self.get_phase_run(project_id, run_id)
            if run["status"] not in ACTIVE or time.monotonic() >= deadline:
                return run
            time.sleep(poll)

    # ---- waking and stepping -----------------------------------------------------------------------

    def spawn_phase_worker(self, run_id):
        subprocess.Popen(
            [sys.executable, "-m", "ccc_mcp.phase_worker", "--home", str(self.home), "--run", run_id],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            shell=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            start_new_session=os.name != "nt",
        )

    def wake_phase_run(self, run_id):
        """Ask for the run to be looked at. The request is recorded first, then a short worker is
        started; if that worker finds another one already stepping, the request is not lost: the
        one stepping looks again before it lets go."""
        with self.store.transaction() as conn:
            row = conn.execute("SELECT status FROM goal_phase_runs WHERE id=?", (run_id,)).fetchone()
            if not row or row["status"] not in ACTIVE:
                return False
            conn.execute("UPDATE goal_phase_runs SET wake_seq=wake_seq+1 WHERE id=?", (run_id,))
        try:
            self.spawn_phase_worker(run_id)
        except Exception:
            return False
        return True

    def notify_phase_runner(self, execution_id):
        """Called by the worker when an execution is over, whatever happened. Does nothing for a
        Task that no phase run supervises. Never raises: the execution is already recorded."""
        try:
            row = self.store.one(
                "SELECT project_id,task_id FROM agent_executions WHERE id=?", (execution_id,))
            if not row:
                return
            run = self.store.one(
                "SELECT id FROM goal_phase_runs WHERE project_id=? AND task_id=? AND status IN "
                "('STARTING','RUNNING')", (row["project_id"], row["task_id"]),
            )
            if run:
                self.wake_phase_run(run["id"])
        except Exception:
            return

    def run_phase_worker(self, run_id, *, recovering=False, max_rounds=50):
        """Step a run until there is nothing more it can do now. Called by `ccc_mcp.phase_worker`.
        One process steps a run at a time (it holds the run's lock); a wake that arrives while it
        is busy is noticed before it lets go and before the next step."""
        result = {"status": None, "rounds": 0}
        for _ in range(max_rounds):
            try:
                lease = portalocker.Lock(self._run_lock_path(run_id), timeout=0)
                lease.acquire()
            except portalocker.exceptions.LockException:
                result["busy"] = True
                return result
            try:
                run = self._phase_run(run_id)
                seen = run["wake_seq"]
                step = self.step_phase_run(run_id, recovering=recovering, locked=True)
                # A finished run's row is frozen by the database, and there is nothing left to hand
                # over for it, so only a live run records what it has seen.
                self.store.execute(
                    "UPDATE goal_phase_runs SET handled_seq=? WHERE id=? AND status NOT IN "
                    "('PASSED','EXHAUSTED','CANCELLED')", (seen, run_id))
                result.update(step)
                result["rounds"] += 1
            finally:
                lease.release()
            after = self._phase_run(run_id)
            if after["status"] not in ACTIVE:
                return result
            # Look again if something woke the run meanwhile, and also when this step itself changed
            # the Task (a hand-over finished, a repair sent): what that made possible (an escalation
            # to analyse, a result to refine) is for the next look, and no other process is coming.
            if after["wake_seq"] == seen and not step.get("acted"):
                return result
            recovering = False
        return result

    def step_phase_run(self, run_id, *, recovering=False, locked=False):
        """One look at the run and, if there is something to do, one decision. Reads only durable
        state, so a repeated or restarted call reaches the same decision. Without `locked` it takes
        the run's lock itself and gives up (returns busy) if another process holds it."""
        if not locked:
            try:
                with portalocker.Lock(self._run_lock_path(run_id), timeout=0):
                    return self.step_phase_run(run_id, recovering=recovering, locked=True)
            except portalocker.exceptions.LockException:
                return {"status": self._phase_run(run_id)["status"], "busy": True}
        run = self._phase_run(run_id)
        if run["status"] not in ACTIVE:
            return {"status": run["status"], "acted": None}
        if run["status"] == "STARTING":
            self._finish_start(run_id)
            return {"status": self._phase_run(run_id)["status"], "acted": "started"}
        return self._decide(run, recovering)

    # ---- the decision -------------------------------------------------------------------------------

    def _decide(self, run, recovering):
        project_id, task_id, run_id = run["project_id"], run["task_id"], run["id"]
        task = self.task(project_id, task_id)
        control = self.task_control(project_id, task_id)
        if control["stop_requested"] or task["state"] in {"CANCELLED", "REJECTED"}:
            if self._set_status(
                run_id, "CANCELLED", expect=ACTIVE, reason="TASK_STOPPED",
                detail={"message": REASONS["TASK_STOPPED"][0]}, kind="phase_run_cancelled"):
                self._close_open_attempt(run, "FAILED", "task stopped")
            return {"status": "CANCELLED", "acted": "task_stopped"}
        if control["paused"]:
            self._set_status(
                run_id, "PAUSED", expect=ACTIVE, reason="PAUSED_BY_USER", resume_to="RUNNING",
                detail={"message": REASONS["PAUSED_BY_USER"][0], "note": "the task was paused"},
                kind="phase_run_paused")
            return {"status": "PAUSED", "acted": "paused"}
        budget = self.phase_budget(run)
        if budget["minutes"]["left"] <= 0 and task["state"] != "COMPLETED":
            self._exhaust(run_id, "TIME_BUDGET", {"budget": budget})
            return {"status": "EXHAUSTED", "acted": "time_budget"}
        if self.store.one(
            "SELECT 1 FROM agent_executions WHERE project_id=? AND task_id=? "
            "AND status IN ('QUEUED','RUNNING')", (project_id, task_id)):
            return {"status": run["status"], "acted": None, "waiting_for": "agent"}
        if control.get("host_test_running") and not recovering:
            return {"status": run["status"], "acted": None, "waiting_for": "host_test"}
        refused = self.store.one(
            "SELECT detail FROM goal_events WHERE project_id=? AND goal_id=? "
            "AND kind='phase_dispatch_refused' AND json_extract(detail,'$.run_id')=? "
            "ORDER BY seq DESC LIMIT 1", (project_id, run["goal_id"], run_id))
        if refused and task["state"] != "COMPLETED":
            code = json.loads(refused["detail"])["code"]
            self._exhaust(run_id, code, {"budget": budget, "refused": json.loads(refused["detail"])})
            return {"status": "EXHAUSTED", "acted": code.lower()}
        handover = run["resume_to"] == "HANDOVER"
        if handover:
            self.store.execute("UPDATE goal_phase_runs SET resume_to=NULL WHERE id=?", (run_id,))
        if recovering or handover or run["adapter"] == "fake":
            progressed = self._advance_stalled(run)
            if progressed:
                return {"status": self._phase_run(run_id)["status"], "acted": "advanced"}
            task = self.task(project_id, task_id)
        if task["state"] == "COMPLETED":
            return self._conclude(run)
        pending_repair = self._repair_awaiting_dispatch(run)
        if pending_repair:
            return self._dispatch_repair(run, pending_repair)
        escalation = self.store.one(
            "SELECT * FROM flow_escalations WHERE project_id=? AND task_id=? AND status='OPEN' "
            "ORDER BY created DESC LIMIT 1", (project_id, task_id))
        if escalation:
            return self._on_failure(run, task, escalation)
        if task["state"] == "AWAITING_PM_DECISION":
            return self._refine_analysis(run, task)
        if task["state"] in {"BLOCKED", "FAILED"}:
            return self._on_execution_problem(run, task)
        return {"status": run["status"], "acted": None, "waiting_for": "engine"}

    def _resume_work(self, run_id, was, operator_confirmed=False):
        """After a person resumed (`was` is why it had stopped): put the Task back in motion from
        where it stopped."""
        run = self._phase_run(run_id)
        project_id, task_id = run["project_id"], run["task_id"]
        task = self.task(project_id, task_id)
        if was in {"HOST_TEST_UNSAFE", "HOST_TEST_INTERRUPTED", "INTERRUPTED"} or (
            self.task_control(project_id, task_id).get("host_test_running")
        ):
            recovered = self.recover_workers(project_id, operator_confirmed)
            if recovered.get("host_tests_not_recovered"):
                self._stop_for_person(
                    run_id, "HOST_TEST_UNSAFE",
                    {"refused": recovered["host_tests_not_recovered"],
                     "why": "It could not be proven that the old test process is gone, so tests "
                            "were not run again. Stop it yourself, or confirm as the operator."})
                return
        if task["state"] in {"BLOCKED", "FAILED"}:
            self._restart_failed_step(run, task)

    def _advance_stalled(self, run):
        """Finish what the engine's own chain left half done: an interrupted dispatch pointer, or a
        report that nobody refined (a paused task, a crashed worker, or a fake run whose workers
        do not auto-advance). Everything used here is the engine's own, idempotent entry point."""
        project_id, task_id = run["project_id"], run["task_id"]
        try:
            if self._pending_dispatch_row(project_id, task_id):
                self.resume_pending_dispatch(project_id, task_id)
                return True
            latest = self.store.one(
                "SELECT id,status,role FROM agent_executions WHERE project_id=? AND task_id=? "
                "ORDER BY created DESC LIMIT 1", (project_id, task_id))
            if latest and latest["status"] == "SUCCEEDED" and latest["role"] != "SA":
                outcome = self.auto_advance_low_risk(latest["id"], allow_fake=run["adapter"] == "fake")
                return bool(outcome.get("advanced"))
        except BudgetExhausted as exc:
            self._exhaust(run["id"], exc.code, {"error": str(exc)})
            return True
        except (BridgeError, PermissionDenied):
            return False
        return False
