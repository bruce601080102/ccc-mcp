"""Goal runs (Stage C): the supervisor that carries one approved goal from its first phase to a verified end.

A goal run does not run agents and is not a second engine. It owns a few decisions the Stage B phase
runner cannot make, because they are about the *goal*: which phase is next (the plan's dependency graph
decides, never the phase number), what a finished phase hands on to the next one, and whether the whole
goal is done. Every phase is started through `PhaseHub.start_phase_run`, so the Task/Workflow engine,
Agent Studio roles, the project's authorization, Review Gate, Host Test and the per-phase repair loop
are exactly the ones Stage B uses.

Who keeps it moving (this is the honest answer to "is it autonomous?"):

* Not a resident daemon. Each time something changes (an execution ends, a phase run changes state, a
  person acts) a short `ccc_mcp.goal_run_worker` process is started. It takes the goal run's file lock,
  reads the durable state, makes the next decision, acts, looks again if it was woken meanwhile, and
  exits. Closing the browser changes nothing: the chain does not run through the browser.
* If every process dies (a reboot, `kill -9`), nothing wakes the run by itself. It resumes when the web
  console starts or its watchdog sees it stalled, or when `ccc-mcp goal run-recover` is run. Until then
  the goal run is simply waiting in SQLite, and it says so (`stalled`).
* One supervisor per goal run: the file lock. A second worker finds it held and leaves; the wake it
  carried is noticed by the first before it lets go.

What it will not do, whatever a model says or a caller asks:

* start a phase that is not in the plan version a person approved, whose dependencies have not passed,
  or while another phase of the project is open; run two phases at once;
* run any command from the plan or from a model. The host runs only the operator's configured test
  command, in phases and in the final verification;
* take a new budget when a new phase begins: the budget is frozen when the run starts and counted over
  the whole goal (see `PhaseHub.goal_usage`);
* commit, push, release or deploy; widen a provider's permissions; change the requirement or the
  acceptance criteria; or change the approved plan. A model can only *suggest* a different plan: it
  becomes a new version, and a run continues under it only after a person approves that exact version;
* call the goal passed because every phase says PASSED. The whole project is tested again, and the
  checks listed in core/goal_verify.py must also hold.
"""

import json
import os
import sqlite3
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path

import portalocker

from ..adapters.storage import now
from ..infra.errors import BridgeError, PermissionDenied
from ..infra.security import canonical, redact, reject_secrets
from .context import identifier
from .goal_plan import BUDGET_CAPS, DEFAULT_BUDGET
from .goal_verify import GoalVerifyMixin, changed_paths, tree_digest
from .phase_runner import REASONS as PHASE_REASONS
from .phase_runner import BudgetExhausted, phase_requirement

ACTIVE = ("RUNNING", "VERIFYING")
LIVE = ("RUNNING", "VERIFYING", "WAITING_USER", "PAUSED")
TERMINAL = ("PASSED", "EXHAUSTED", "FAILED", "CANCELLED")
PHASE_ACTIVE = ("STARTING", "RUNNING")
NOTE_MAX = 2000

# reason code -> (what a person reads, can `resume` move it on)
GOAL_REASONS = {
    "PAUSED_BY_USER": ("Paused by a person.", True),
    "PHASE_NEEDS_PERSON": ("A phase stopped and needs a person. Deal with it, then resume.", True),
    "PHASE_AWAITING_ACCEPTANCE": (
        "A phase passed its tests, and some of its criteria can only be judged by a person.", False),
    "PLAN_CHANGED": (
        "The goal's plan is not the version this run was started with, so the approval it runs under "
        "no longer covers it. A person has to approve the new version.", True),
    "PLAN_REVISION_NEEDED": (
        "The Delivery PM says this cannot be finished inside the approved plan. A revised plan needs "
        "a person's approval before anything continues.", True),
    "FINAL_ACCEPTANCE_NEEDED": (
        "Every check the computer can do passed. Some final criteria can only be judged by a person.",
        False),
    "VERIFICATION_NEEDS_PERSON": (
        "The tests pass, but something around them needs a person's decision before the goal can be "
        "accepted.", False),
    "FINAL_VERIFICATION_FAILED": (
        "The final verification still fails after the integration fix the plan allows. Ask the "
        "Delivery PM for a revised plan, or stop the goal.", False),
    "ENVIRONMENT_BLOCKED": ("The test environment is blocked; a code change cannot fix it.", True),
    "HOST_TEST_UNSAFE": ("A host test may still be running; it was not run again.", True),
    "HOST_TEST_INTERRUPTED": ("The final host test was interrupted, so nothing is verified.", True),
    "SUPERVISOR_ERROR": ("The supervisor met something unexpected and stopped to be safe.", True),
    "PHASE_CANCELLED": ("A phase run was cancelled, so the goal cannot be completed as planned.", False),
    "GOAL_CANCELLED": ("The goal was cancelled.", False),
    "DEPENDENCY_DEADLOCK": ("No phase can start: the remaining ones depend on a phase that did not pass.", False),
    "PASSED": ("Every phase passed and the whole project passed the final verification.", False),
    "PASSED_WITH_ACCEPTANCE": ("A person accepted the final result.", False),
    "CANCELLED_BY_USER": ("Cancelled by a person.", False),
}
# A goal run stopped for these is waiting on the phase run named in its detail, and moves on by itself
# when that phase run does (a person may act on the phase directly, not only through the goal).
PHASE_DERIVED = {"PHASE_NEEDS_PERSON", "PHASE_AWAITING_ACCEPTANCE", "PAUSED_BY_USER"}


def reason_text(code):
    if code in GOAL_REASONS:
        return GOAL_REASONS[code][0]
    if code in PHASE_REASONS:
        return PHASE_REASONS[code][0]
    return code or ""


def plan_budget(plan):
    """The budget a plan declares, capped. Frozen into the goal run when it starts."""
    budget = {key: plan.get("budget", {}).get(key, DEFAULT_BUDGET[key]) for key in DEFAULT_BUDGET}
    return {key: min(int(budget[key]), BUDGET_CAPS[key]) for key in DEFAULT_BUDGET}


def _json(value, default=None):
    try:
        return json.loads(value) if value else default
    except ValueError:
        return default


def _note(value):
    if value is None or not str(value).strip():
        return ""
    text = str(value).replace("\r\n", "\n").strip()
    if len(text) > NOTE_MAX:
        raise BridgeError(f"Note must be at most {NOTE_MAX} characters")
    reject_secrets(text)
    return text


class GoalRunHub(GoalVerifyMixin):
    # Which adapter goal runs use when the caller does not say. Tests and demos set "fake".
    goal_adapter_default = "real"
    goal_stall_seconds = 60

    # ---- small readers ----------------------------------------------------------------------------

    def _goal_run(self, run_id):
        row = self.store.one("SELECT * FROM goal_runs WHERE id=?", (run_id,))
        if not row:
            raise BridgeError("Goal run not found")
        return row

    def _event_now(self, run, kind, detail, actor="system"):
        with self.store.transaction() as conn:
            self._event(conn, run["project_id"], run["goal_id"], kind, actor, detail)

    def _goal_lock_path(self, run_id):
        path = self.home / "locks" / f"goalrun-{run_id}.lock"
        path.parent.mkdir(exist_ok=True)
        return path

    def _supervisor_alive(self, run_id):
        try:
            with portalocker.Lock(self._goal_lock_path(run_id), timeout=0):
                return False
        except portalocker.exceptions.LockException:
            return True

    def _plan_version_row(self, run):
        return self.store.one(
            "SELECT * FROM goal_plan_versions WHERE project_id=? AND goal_id=? AND version=?",
            (run["project_id"], run["goal_id"], run["plan_version"]))

    def _relevant_phase_runs(self, run, plan):
        """The goal's phase runs that belong to the plan this run is bound to, plus its integration
        fixes. A cancelled run of a phase that a revised plan no longer has is history, not state."""
        ids = {phase["id"] for phase in plan["phases"]}
        return [
            row for row in self.store.all(
                "SELECT * FROM goal_phase_runs WHERE project_id=? AND goal_id=? ORDER BY created, id",
                (run["project_id"], run["goal_id"]))
            if (row["kind"] or "PLAN") == "INTEGRATION" or row["phase_id"] in ids
        ]

    # ---- state changes ---------------------------------------------------------------------------

    def _set_goal_status(
        self, run_id, new, *, expect=None, reason=None, detail=None, resume_to=None, kind=None,
        actor="system",
    ):
        """Move a goal run to `new` if it is still in one of the `expect` statuses; the one place a
        goal run's status changes. Verification time is accounted for here. Returns True when it
        moved."""
        stamp = now()
        with self.store.transaction() as conn:
            row = conn.execute("SELECT * FROM goal_runs WHERE id=?", (run_id,)).fetchone()
            if not row:
                raise BridgeError("Goal run not found")
            if row["status"] == new or (expect and row["status"] not in expect):
                return False
            if row["status"] in TERMINAL:
                return False
            seconds, since = row["active_seconds"] or 0.0, row["active_since"]
            if row["status"] == "VERIFYING" and since:
                seconds += max(0.0, (datetime.now(UTC) - self._clock(since)).total_seconds())
                since = None
            since = (since or stamp) if new == "VERIFYING" else None
            conn.execute(
                "UPDATE goal_runs SET status=?,reason=?,detail=?,resume_to=?,active_seconds=?,"
                "active_since=?,updated=?,finished=? WHERE id=?",
                (new, reason, canonical(detail) if detail is not None else None, resume_to, seconds,
                 since, stamp, stamp if new in TERMINAL else None, run_id))
            self._event(
                conn, row["project_id"], row["goal_id"], kind or f"goal_run_{new.lower()}", actor,
                {"goal_run_id": run_id, "from": row["status"], "to": new, "reason": reason,
                 "detail": detail})
        return True

    # ---- starting ----------------------------------------------------------------------------------

    def start_goal_run(
        self, project_id, goal_id, *, adapter=None, fake_scenario="success", pm_provider=None,
        pm_scenario="success", pm_timeout=300, spawn=True,
    ):
        """Start the whole approved goal, on purpose. One call, one run: calling it again returns the
        run that exists and creates nothing. Everything that could stop it half way is checked first."""
        self.project(project_id)
        goal_id = identifier(goal_id)
        adapter = adapter or self.goal_adapter_default
        if adapter not in {"real", "fake"}:
            raise BridgeError("Unsupported adapter")
        if not isinstance(pm_timeout, int) or isinstance(pm_timeout, bool) or not 5 <= pm_timeout <= 900:
            raise BridgeError("pm_timeout must be 5 to 900 seconds")
        existing = self.store.one(
            "SELECT * FROM goal_runs WHERE project_id=? AND goal_id=?", (project_id, goal_id))
        if existing:
            if existing["status"] in ACTIVE and spawn:
                self.wake_goal_run(existing["id"])
            return {**self.get_goal_run(project_id, existing["id"]), "already_started": True}

        goal, version, _, plan, approval = self._approved_phase(project_id, goal_id, "-", synthetic={})
        others = self.store.one(
            "SELECT goal_id FROM goal_runs WHERE project_id=? AND goal_id<>? AND status IN "
            "('RUNNING','WAITING_USER','PAUSED','VERIFYING')", (project_id, goal_id))
        if others:
            raise BridgeError(
                f"Goal {others['goal_id']} is running in this project. One goal at a time works on "
                "the project's files; finish or cancel it first")
        open_phase = self.store.one(
            "SELECT goal_id,phase_id,status FROM goal_phase_runs WHERE project_id=? AND status IN "
            "('STARTING','RUNNING','PAUSED','NEEDS_HUMAN','AWAITING_ACCEPTANCE')", (project_id,))
        if open_phase:
            raise BridgeError(
                f"Phase {open_phase['phase_id']} of {open_phase['goal_id']} is still open; finish or "
                "cancel it first")
        if self.store.one(
            "SELECT 1 FROM goal_phase_runs WHERE project_id=? AND goal_id=? AND plan_sha256<>?",
            (project_id, goal_id, version["sha256"])):
            raise BridgeError(
                "A phase of this goal already ran under an older plan version. Start the goal run "
                "only on the plan that run was approved under")
        access, test_command = None, None
        for phase in plan["phases"]:
            try:
                access, test_command, *_ = self.check_phase_runnable(
                    project_id, goal, phase, adapter, phase_requirement(goal, phase), version=version)
            except (BridgeError, PermissionDenied) as exc:
                raise type(exc)(f"Phase {phase['id']} cannot run: {exc}") from None
        pm = self._planning_settings(project_id, pm_provider, adapter)
        if adapter == "fake":
            pm = {**pm, "scenario": pm_scenario}
        root = Path(self.project(project_id)["root"])
        tree = tree_digest(root)
        base = {"head": tree["head"], "tree_digest": tree["digest"], "dirty": changed_paths(root)}
        run_id = "GORUN-" + uuid.uuid4().hex[:16]
        actor = self._goal_actor()
        stamp = now()
        budget = plan_budget(plan)
        try:
            with self.store.transaction() as conn:
                conn.execute(
                    "INSERT INTO goal_runs(id,project_id,goal_id,plan_version,plan_sha256,approval_id,"
                    "adapter,fake_scenario,pm_settings,pm_timeout,test_command,budget,base,status,"
                    "creator,created,updated) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,'RUNNING',?,?,?)",
                    (run_id, project_id, goal["id"], version["version"], version["sha256"],
                     approval["id"], adapter, fake_scenario if adapter == "fake" else None,
                     canonical(pm), pm_timeout, canonical(list(test_command)), canonical(budget),
                     canonical(base), actor, stamp, stamp))
                self._event(
                    conn, project_id, goal["id"], "goal_run_started", actor,
                    {"goal_run_id": run_id, "plan_version": version["version"],
                     "plan_sha256": version["sha256"], "approval_id": approval["id"],
                     "budget": budget, "test_command": list(test_command),
                     "project_authorization": access["authorization_id"],
                     "phases": [p["id"] for p in plan["phases"]], "base": base})
        except sqlite3.IntegrityError:
            winner = self.store.one(
                "SELECT id FROM goal_runs WHERE project_id=? AND goal_id=?", (project_id, goal_id))
            if not winner:
                raise
            return {**self.get_goal_run(project_id, winner["id"]), "already_started": True}
        if spawn:
            self.wake_goal_run(run_id)
        return {**self.get_goal_run(project_id, run_id), "already_started": False}

    # ---- waking and stepping ------------------------------------------------------------------------

    def spawn_goal_worker(self, run_id):
        subprocess.Popen(
            [sys.executable, "-m", "ccc_mcp.goal_run_worker", "--home", str(self.home), "--run", run_id],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            shell=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            start_new_session=os.name != "nt",
        )

    def wake_goal_run(self, run_id):
        """Ask for the goal run to be looked at. The request is recorded first, then a short worker
        is started; if that worker finds another one stepping, the request is not lost."""
        with self.store.transaction() as conn:
            row = conn.execute("SELECT status FROM goal_runs WHERE id=?", (run_id,)).fetchone()
            if not row or row["status"] not in LIVE:
                return False
            conn.execute("UPDATE goal_runs SET wake_seq=wake_seq+1 WHERE id=?", (run_id,))
        try:
            self.spawn_goal_worker(run_id)
        except Exception:
            return False
        return True

    def _notify_goal_run(self, run_id):
        try:
            self.wake_goal_run(run_id)
        except Exception:
            return False
        return True

    def run_goal_worker(self, run_id, *, recovering=False, max_rounds=50):
        """Step a goal run until there is nothing more it can do now. Called by
        `ccc_mcp.goal_run_worker`. One process steps a run at a time (it holds the run's lock)."""
        result = {"status": None, "rounds": 0}
        for _ in range(max_rounds):
            try:
                lease = portalocker.Lock(self._goal_lock_path(run_id), timeout=0)
                lease.acquire()
            except portalocker.exceptions.LockException:
                result["busy"] = True
                return result
            try:
                run = self._goal_run(run_id)
                seen = run["wake_seq"]
                step = self.step_goal_run(run_id, recovering=recovering, locked=True)
                self.store.execute(
                    "UPDATE goal_runs SET handled_seq=? WHERE id=? AND status NOT IN "
                    "('PASSED','EXHAUSTED','FAILED','CANCELLED')", (seen, run_id))
                result.update(step)
                result["rounds"] += 1
            finally:
                lease.release()
            after = self._goal_run(run_id)
            if after["status"] not in LIVE:
                return result
            # Look again if something woke the run meanwhile, and when this step changed something
            # that makes a further step possible (a phase recorded, the next one started).
            if after["wake_seq"] == seen and not (step.get("acted") and after["status"] in ACTIVE):
                return result
            recovering = False
        return result

    def step_goal_run(self, run_id, *, recovering=False, locked=False):
        """One look at the goal run and, if there is something to do, one decision. Reads only
        durable state, so a repeated or restarted call reaches the same decision."""
        if not locked:
            try:
                with portalocker.Lock(self._goal_lock_path(run_id), timeout=0):
                    return self.step_goal_run(run_id, recovering=recovering, locked=True)
            except portalocker.exceptions.LockException:
                return {"status": self._goal_run(run_id)["status"], "busy": True}
        run = self._goal_run(run_id)
        try:
            if run["status"] in ("WAITING_USER", "PAUSED"):
                return self._reconcile(run)
            if run["status"] not in ACTIVE:
                return {"status": run["status"], "acted": None}
            return self._decide_goal(run, recovering)
        except BudgetExhausted as exc:
            self._set_goal_status(
                run_id, "EXHAUSTED", expect=LIVE, reason=exc.code, detail={"error": str(exc)},
                kind="goal_run_exhausted")
            return {"status": "EXHAUSTED", "acted": exc.code.lower()}
        except (sqlite3.DatabaseError, OSError) as exc:
            # Something the supervisor cannot reason about (a locked or damaged database): do not
            # guess and do not spin; say so and wait for a person.
            self._set_goal_status(
                run_id, "WAITING_USER", expect=ACTIVE, reason="SUPERVISOR_ERROR",
                detail={"message": reason_text("SUPERVISOR_ERROR"), "error": redact(str(exc))[:500]},
                resume_to="RUNNING")
            return {"status": "WAITING_USER", "acted": "supervisor_error"}

    # ---- the decision -------------------------------------------------------------------------------

    def _plan_problem(self, run, goal):
        """Why the plan this run was started with can no longer be relied on, or None."""
        if goal["current_version"] != run["plan_version"]:
            return (f"The goal's plan moved from version {run['plan_version']} to "
                    f"{goal['current_version']}")
        version = self._plan_version_row(run)
        if not version or version["sha256"] != run["plan_sha256"]:
            return "The plan this run was started from is no longer intact"
        approval = self.store.one(
            "SELECT plan_sha256 FROM goal_approvals WHERE project_id=? AND goal_id=? AND plan_version=?",
            (run["project_id"], run["goal_id"], run["plan_version"]))
        if not approval or approval["plan_sha256"] != run["plan_sha256"]:
            return "No approval covers the exact plan this run was started from"
        return None

    def _decide_goal(self, run, recovering):
        run_id, project_id, goal_id = run["id"], run["project_id"], run["goal_id"]
        goal = self.store.one("SELECT * FROM goals WHERE project_id=? AND id=?", (project_id, goal_id))
        if goal["cancelled"]:
            if self._set_goal_status(
                run_id, "CANCELLED", expect=LIVE, reason="GOAL_CANCELLED",
                detail={"message": reason_text("GOAL_CANCELLED")}, kind="goal_run_cancelled"):
                self._cancel_open_phase_runs(run, "The goal was cancelled")
            return {"status": "CANCELLED", "acted": "goal_cancelled"}
        problem = self._plan_problem(run, dict(goal))
        if problem:
            self._set_goal_status(
                run_id, "WAITING_USER", expect=ACTIVE, reason="PLAN_CHANGED",
                detail={"message": reason_text("PLAN_CHANGED"), "why": problem}, resume_to="RUNNING")
            return {"status": "WAITING_USER", "acted": "plan_changed"}
        plan = json.loads(self._plan_version_row(run)["content"])
        runs = self._relevant_phase_runs(run, plan)

        for row in runs:                                           # 1. something is being worked on
            if row["status"] in PHASE_ACTIVE:
                self._nudge_phase(row, recovering)
                return {"status": run["status"], "acted": None, "waiting_for": "phase",
                        "phase_id": row["phase_id"]}
        for row in runs:                                           # 2. a phase waits for a person
            outcome = self._phase_waits(run, row)
            if outcome:
                return outcome
        for row in runs:                                           # 3. a phase ended badly
            if row["status"] == "EXHAUSTED":
                self._set_goal_status(
                    run_id, "EXHAUSTED", expect=ACTIVE, reason=row["reason"] or "ATTEMPTS_EXHAUSTED",
                    detail={"message": reason_text(row["reason"]), "phase_id": row["phase_id"],
                            "phase_run_id": row["id"]}, kind="goal_run_exhausted")
                return {"status": "EXHAUSTED", "acted": "phase_exhausted"}
            if row["status"] == "CANCELLED":
                self._set_goal_status(
                    run_id, "FAILED", expect=ACTIVE, reason="PHASE_CANCELLED",
                    detail={"message": reason_text("PHASE_CANCELLED"), "phase_id": row["phase_id"],
                            "phase_run_id": row["id"]}, kind="goal_run_failed")
                return {"status": "FAILED", "acted": "phase_cancelled"}
        for row in runs:                                           # 4. write down what passed
            if row["status"] == "PASSED":
                self._record_passed(run, row)

        passed = {row["phase_id"] for row in runs if row["status"] == "PASSED"}
        started = {row["phase_id"] for row in runs}
        pending = [p for p in plan["phases"] if p["id"] not in started]
        if not pending:                                            # 6. every phase passed: verify
            return self._verify(run, goal, plan)
        ready = [p for p in pending if all(d in passed for d in p["depends_on"])]
        if not ready:
            self._set_goal_status(
                run_id, "FAILED", expect=ACTIVE, reason="DEPENDENCY_DEADLOCK",
                detail={"message": reason_text("DEPENDENCY_DEADLOCK"),
                        "pending": [p["id"] for p in pending]}, kind="goal_run_failed")
            return {"status": "FAILED", "acted": "dependency_deadlock"}
        return self._start_next_phase(run, plan, ready[0])         # 5. dependencies satisfied

    def _nudge_phase(self, row, recovering):
        """A phase run that is active but that nobody is stepping: wake it. Does nothing while an
        agent works, while its stepper holds the lock, or before it has been idle for a while."""
        if self._stepper_alive(row["id"]):
            return
        if self.store.one(
            "SELECT 1 FROM agent_executions WHERE project_id=? AND task_id=? "
            "AND status IN ('QUEUED','RUNNING')", (row["project_id"], row["task_id"])):
            return
        idle = time.time() - self._clock(row["updated"]).timestamp()
        if recovering or idle > self.phase_stall_seconds:
            self.wake_phase_run(row["id"])

    def _phase_waits(self, run, row):
        status = row["status"]
        if status not in ("PAUSED", "NEEDS_HUMAN", "AWAITING_ACCEPTANCE"):
            return None
        detail = _json(row["detail"], {}) or {}
        base = {"phase_id": row["phase_id"], "phase_run_id": row["id"], "phase_reason": row["reason"],
                "phase_message": reason_text(row["reason"])}
        if status == "PAUSED":
            self._set_goal_status(
                run["id"], "PAUSED", expect=ACTIVE, reason="PAUSED_BY_USER",
                detail={"message": reason_text("PAUSED_BY_USER"), "source": "phase", **base},
                resume_to="RUNNING")
            return {"status": "PAUSED", "acted": "paused"}
        if status == "AWAITING_ACCEPTANCE":
            code = "PHASE_AWAITING_ACCEPTANCE"
        elif row["reason"] == "PM_NEEDS_HUMAN" and detail.get("plan_change"):
            code = "PLAN_REVISION_NEEDED"
            base["plan_change_summary"] = detail.get("plan_change_summary")
        else:
            code = "PHASE_NEEDS_PERSON"
        self._set_goal_status(
            run["id"], "WAITING_USER", expect=ACTIVE, reason=code,
            detail={"message": reason_text(code), **base}, resume_to="RUNNING")
        return {"status": "WAITING_USER", "acted": code.lower()}

    def _start_next_phase(self, run, plan, phase):
        run_id = run["id"]
        budget = self.goal_budget(run["project_id"], run["goal_id"], json.loads(run["budget"]))
        if budget["dispatches"]["left"] < 1:
            raise BudgetExhausted("DISPATCH_BUDGET", "Goal budget exhausted before the next phase")
        if budget["minutes"]["left"] <= 0:
            raise BudgetExhausted("TIME_BUDGET", "Goal time budget exhausted before the next phase")
        handoff = self._handoff_for(run_id, plan, phase)
        pm = json.loads(run["pm_settings"])
        try:
            started = self.start_phase_run(
                run["project_id"], run["goal_id"], phase["id"], adapter=run["adapter"],
                fake_scenario=run["fake_scenario"] or "success", pm_provider=pm.get("provider"),
                pm_scenario=pm.get("scenario", "success"), pm_timeout=run["pm_timeout"],
                goal_run_id=run_id, handoff=handoff, budget_override=json.loads(run["budget"]))
        except BudgetExhausted:
            raise
        except (BridgeError, PermissionDenied) as exc:
            current = self._goal_run(run_id)
            if current["status"] not in ACTIVE:           # paused or cancelled while this ran
                return {"status": current["status"], "acted": None}
            self._set_goal_status(
                run_id, "WAITING_USER", expect=ACTIVE, reason="SUPERVISOR_ERROR",
                detail={"message": reason_text("SUPERVISOR_ERROR"), "error": redact(str(exc))[:500],
                        "while": f"starting phase {phase['id']}"}, resume_to="RUNNING")
            return {"status": "WAITING_USER", "acted": "phase_refused"}
        return {"status": "RUNNING", "acted": "phase_started", "phase_id": phase["id"],
                "phase_run": started["id"], "already_started": started.get("already_started")}

    def _reconcile(self, run):
        """A goal run that waits because of a phase moves on by itself when that phase does, so a
        person can deal with the phase directly (accept it, resume it) or through the goal."""
        reason = run["reason"]
        if reason not in PHASE_DERIVED:
            return {"status": run["status"], "acted": None}
        detail = _json(run["detail"], {}) or {}
        phase_run = detail.get("phase_run_id")
        if reason == "PAUSED_BY_USER" and detail.get("source") != "phase":
            return {"status": run["status"], "acted": None}
        row = self.store.one("SELECT status FROM goal_phase_runs WHERE id=?", (phase_run,)) \
            if phase_run else None
        if row and row["status"] in ("STARTING", "RUNNING", "PASSED"):
            moved = self._set_goal_status(
                run["id"], "RUNNING", expect=("WAITING_USER", "PAUSED"), kind="goal_run_resumed",
                detail={"was": reason, "phase_run_id": phase_run, "by": "the phase continued"})
            if moved:
                return self.step_goal_run(run["id"], locked=True)
        return {"status": run["status"], "acted": None}

    def _cancel_open_phase_runs(self, run, why):
        for row in self.store.all(
            "SELECT id FROM goal_phase_runs WHERE project_id=? AND goal_id=? AND status IN "
            "('STARTING','RUNNING','PAUSED','NEEDS_HUMAN','AWAITING_ACCEPTANCE')",
            (run["project_id"], run["goal_id"]),
        ):
            try:
                self.cancel_phase_run(run["project_id"], row["id"], why)
            except (BridgeError, PermissionDenied):
                pass

    # ---- control: pause, resume, cancel, accept ------------------------------------------------------------

    def pause_goal_run(self, project_id, run_id, reason=""):
        """Stop new work from starting. The step an agent is in finishes; nothing after it begins, and
        no further phase is started, until a person resumes."""
        run = self._owned(project_id, run_id)
        note = _note(reason)
        if run["status"] not in ACTIVE:
            raise BridgeError(f"A goal run that is {run['status']} cannot be paused")
        moved = self._set_goal_status(
            run_id, "PAUSED", expect=ACTIVE, reason="PAUSED_BY_USER", actor=self._goal_actor(),
            detail={"message": reason_text("PAUSED_BY_USER"), "note": note, "source": "goal"},
            resume_to=run["status"], kind="goal_run_paused")
        if moved:
            for row in self.store.all(
                "SELECT id FROM goal_phase_runs WHERE project_id=? AND goal_id=? AND status IN "
                "('STARTING','RUNNING')", (project_id, run["goal_id"])):
                try:
                    self.pause_phase_run(project_id, row["id"], note or "the goal run was paused")
                except (BridgeError, PermissionDenied):
                    pass
        return self.get_goal_run(project_id, run_id)

    def resume_goal_run(
        self, project_id, run_id, note="", acknowledge=None, operator_confirmed=False, spawn=True,
    ):
        """Resume a paused goal run, or one that stopped for a person. What it stopped for is dealt
        with first; nothing that already ran is run again, and no budget is added. A second resume of
        the same stop is refused."""
        run = self._owned(project_id, run_id)
        note = _note(note)
        if run["status"] not in ("PAUSED", "WAITING_USER"):
            raise BridgeError(f"A goal run that is {run['status']} cannot be resumed")
        reason = run["reason"]
        goal = self.store.one(
            "SELECT current_version FROM goals WHERE project_id=? AND id=?",
            (project_id, run["goal_id"]))
        # A newer plan version is the way on for a run whose plan cannot be finished as it stands: it
        # continues under it only if a person approved that exact version (see _adopt_plan).
        revised = goal["current_version"] != run["plan_version"]
        if not revised and reason in GOAL_REASONS and not GOAL_REASONS[reason][1]:
            raise BridgeError(f"This goal run cannot be resumed: {GOAL_REASONS[reason][0]}")
        actor = self._goal_actor()
        detail = _json(run["detail"], {}) or {}
        target = run["resume_to"] if run["resume_to"] in ACTIVE else "RUNNING"
        if revised or reason in ("PLAN_CHANGED", "PLAN_REVISION_NEEDED"):
            self._adopt_plan(run, actor)
        if reason in ("PHASE_NEEDS_PERSON", "PAUSED_BY_USER") and detail.get("phase_run_id"):
            row = self.store.one("SELECT status FROM goal_phase_runs WHERE id=?", (detail["phase_run_id"],))
            if row and row["status"] in ("PAUSED", "NEEDS_HUMAN"):
                self.resume_phase_run(
                    project_id, detail["phase_run_id"], note, acknowledge, operator_confirmed,
                    spawn=spawn)
        elif reason == "PAUSED_BY_USER":
            for row in self.store.all(
                "SELECT id FROM goal_phase_runs WHERE project_id=? AND goal_id=? AND status='PAUSED'",
                (project_id, run["goal_id"])):
                self.resume_phase_run(project_id, row["id"], note, acknowledge, operator_confirmed,
                                      spawn=spawn)
        elif reason in ("ENVIRONMENT_BLOCKED", "HOST_TEST_UNSAFE", "HOST_TEST_INTERRUPTED"):
            recovered = self.recover_workers(project_id, operator_confirmed)
            if recovered.get("host_tests_not_recovered"):
                raise PermissionDenied(
                    "It could not be proven that the old test process is gone, so the tests were not "
                    "run again. Stop it yourself, or confirm as the operator.")
        if not self._set_goal_status(
            run_id, target, expect=("PAUSED", "WAITING_USER"), kind="goal_run_resumed", actor=actor,
            detail={"was": reason, "note": note or None, "acknowledged": acknowledge},
        ):
            raise BridgeError("This goal run was already resumed")
        if spawn:
            self.wake_goal_run(run_id)
        return self.get_goal_run(project_id, run_id)

    def cancel_goal_run(self, project_id, run_id, reason=""):
        """End the goal run, stop its open phase run and its Task. Nothing further is dispatched:
        a phase cannot be started once the run is not running."""
        run = self._owned(project_id, run_id)
        note = _note(reason)
        if run["status"] in TERMINAL:
            return self.get_goal_run(project_id, run_id)
        if self._set_goal_status(
            run_id, "CANCELLED", expect=LIVE, reason="CANCELLED_BY_USER", actor=self._goal_actor(),
            detail={"message": reason_text("CANCELLED_BY_USER"), "note": note},
            kind="goal_run_cancelled"):
            self._cancel_open_phase_runs(run, note or "The goal run was cancelled")
        return self.get_goal_run(project_id, run_id)

    def _owned(self, project_id, run_id):
        run = self._goal_run(run_id)
        if run["project_id"] != project_id:
            raise BridgeError("Goal run not found")
        return run

    def accept_goal_description(self, project_id, run_id):
        """What a person reads before accepting the result themselves."""
        run = self._owned(project_id, run_id)
        last = self._latest_acceptable(run)
        pending = [c for c in last["evidence"]["criteria"] if c["coverage"] != "host_test"]
        lines = [f"Accept goal {run['goal_id']}", "",
                 "The host tests passed on the whole project. You are accepting the following yourself:"]
        lines += [f"- {c['id']}: {c['statement']} ({c['kind']})" for c in pending]
        lines += [f"- {p['code']}: {p['detail']}" for p in last["evidence"]["problems"]]
        return {"challenge": f"ACCEPT {run['goal_id']}", "description": "\n".join(lines),
                "criteria": pending, "problems": last["evidence"]["problems"]}

    def _latest_acceptable(self, run):
        if run["status"] != "WAITING_USER" or run["reason"] not in (
            "FINAL_ACCEPTANCE_NEEDED", "VERIFICATION_NEEDS_PERSON"
        ):
            raise BridgeError("This goal run is not waiting for a person's acceptance")
        rows = self._verification_rows(run["id"])
        last = rows[-1] if rows else None
        if not last or last["status"] not in ("PASSED", "NEEDS_PERSON"):
            raise BridgeError("There is no passing verification to accept")
        if (last["evidence"].get("ledger_count") != len(self._ledger(run["id"]))
                or last["evidence"].get("plan_sha256") != run["plan_sha256"]):
            raise BridgeError("The verification is out of date; resume the run to verify again")
        root = Path(self.project(run["project_id"])["root"])
        if tree_digest(root)["digest"] != last["evidence"]["tree"]["digest"]:
            raise BridgeError(
                "The files changed since the verification. Resume the run to verify them again")
        return last

    def accept_goal_local(self, project_id, run_id):
        from .approval import confirm_local

        info = self.accept_goal_description(project_id, run_id)
        identity = confirm_local(info["description"], info["challenge"])
        return self._accept_goal(project_id, run_id, identity)

    def accept_goal_web(self, project_id, run_id, typed_confirmation):
        info = self.accept_goal_description(project_id, run_id)
        if typed_confirmation != info["challenge"]:
            raise PermissionDenied("The confirmation text does not match.")
        return self._accept_goal(project_id, run_id, self._goal_actor())

    def _accept_goal(self, project_id, run_id, identity):
        run = self._owned(project_id, run_id)
        last = self._latest_acceptable(run)
        self._set_goal_status(
            run_id, "PASSED", expect=("WAITING_USER",), reason="PASSED_WITH_ACCEPTANCE", actor=identity,
            kind="goal_run_passed",
            detail={"accepted_by": identity, "verification": last["id"],
                    "acceptance": self.acceptance_matrix(last, identity),
                    "criteria": [c["id"] for c in last["evidence"]["criteria"]
                                 if c["coverage"] != "host_test"],
                    "problems": [p["code"] for p in last["evidence"]["problems"]]})
        return self.get_goal_run(project_id, run_id)

    # ---- a different plan ------------------------------------------------------------------------------------

    def draft_goal_revision(
        self, project_id, run_id, change_request=None, *, fake_scenario="success", provider=None,
        timeout=300, spawn=True,
    ):
        """Ask the Delivery PM to draft a revised plan for a goal run that stopped for a person. The
        draft is a new, unapproved version: it changes nothing about the run until a person approves
        that exact version and resumes. The phase run that was open is ended (a phase cannot be
        re-run), and the request is counted against the goal's budget."""
        run = self._owned(project_id, run_id)
        if run["status"] != "WAITING_USER":
            raise BridgeError("A revised plan is drafted for a goal run that is waiting for a person")
        detail = _json(run["detail"], {}) or {}
        if change_request is None or not str(change_request).strip():
            parts = [detail.get("plan_change_summary"), detail.get("why"),
                     *(detail.get("failing") or [])[:5]]
            change_request = (
                f"A run of this goal stopped ({reason_text(run['reason'])}). "
                + " ".join(str(p) for p in parts if p)
                + " Propose a revised plan. Keep the definition of every phase that already passed "
                  "unchanged, and give any new or redone work new phase ids.")[:3900]
        for row in self.store.all(
            "SELECT id FROM goal_phase_runs WHERE project_id=? AND goal_id=? AND status IN "
            "('PAUSED','NEEDS_HUMAN','AWAITING_ACCEPTANCE')", (project_id, run["goal_id"])):
            self.cancel_phase_run(project_id, row["id"], "Superseded by a revised plan")
        pm = _json(run["pm_settings"], {})
        planned = self.request_goal_plan(
            project_id, run["goal_id"], change_request, provider=provider or pm.get("provider"),
            adapter=run["adapter"], fake_scenario=fake_scenario, timeout=timeout, spawn=spawn)
        self._event_now(run, "goal_run_revision_requested",
                        {"goal_run_id": run_id, "planning_run": planned["id"]},
                        actor=self._goal_actor())
        return planned

    def _adopt_plan(self, run, actor):
        """Continue under a newer plan version, but only one a person approved, and only if it keeps
        everything that already passed exactly as it was. Anything else stays refused."""
        goal = self.store.one(
            "SELECT * FROM goals WHERE project_id=? AND id=?", (run["project_id"], run["goal_id"]))
        if goal["current_version"] == run["plan_version"]:
            problem = self._plan_problem(run, dict(goal))
            if problem:
                raise PermissionDenied(problem)
            return
        new = self.store.one(
            "SELECT * FROM goal_plan_versions WHERE project_id=? AND goal_id=? AND version=?",
            (run["project_id"], run["goal_id"], goal["current_version"]))
        approval = self.store.one(
            "SELECT * FROM goal_approvals WHERE project_id=? AND goal_id=? AND plan_version=?",
            (run["project_id"], run["goal_id"], goal["current_version"]))
        if not new or not approval or approval["plan_sha256"] != new["sha256"]:
            raise PermissionDenied(
                f"Plan version {goal['current_version']} is not approved. A person must approve "
                "exactly that version before this goal run continues under it")
        if self.store.one(
            "SELECT 1 FROM goal_plan_runs WHERE project_id=? AND goal_id=? AND status IN "
            "('QUEUED','RUNNING')", (run["project_id"], run["goal_id"])):
            raise BridgeError("The Delivery PM is still drafting the revised plan")
        old_plan = json.loads(self._plan_version_row(run)["content"])
        new_plan = json.loads(new["content"])
        old = {p["id"]: p for p in old_plan["phases"]}
        fresh = {p["id"]: p for p in new_plan["phases"]}
        problems = []
        for row in self.store.all(
            "SELECT * FROM goal_phase_runs WHERE project_id=? AND goal_id=? AND "
            "COALESCE(kind,'PLAN')='PLAN'", (run["project_id"], run["goal_id"])):
            pid = row["phase_id"]
            if row["status"] == "PASSED":
                if pid not in fresh or canonical(fresh[pid]) != canonical(old.get(pid)):
                    problems.append(f"phase {pid} already passed and must stay exactly as it was")
            elif pid in fresh and pid in old:
                problems.append(f"phase {pid} ended without passing and cannot run again; give the "
                                "work a new phase id")
        if problems:
            raise PermissionDenied("The revised plan cannot continue this run: " + "; ".join(problems))
        with self.store.transaction() as conn:
            conn.execute(
                "UPDATE goal_runs SET plan_version=?,plan_sha256=?,approval_id=? WHERE id=? AND "
                "status IN ('WAITING_USER','PAUSED')",
                (new["version"], new["sha256"], approval["id"], run["id"]))
            self._event(
                conn, run["project_id"], run["goal_id"], "goal_run_plan_adopted", actor,
                {"goal_run_id": run["id"], "from_version": run["plan_version"],
                 "to_version": new["version"], "plan_sha256": new["sha256"],
                 "approval_id": approval["id"],
                 "new_phases": sorted(set(fresh) - set(old))})

    # ---- recovery, on a person's request or when the console starts ------------------------------------------------

    def recover_goal_runs(self, project_id, operator_confirmed=False, spawn_only=False):
        """Pick up goal runs whose supervisor died. What is stuck is looked at in the engine's own
        safe order: workers that stopped are closed as interrupted, an unsafe leftover host test is
        refused (and stops the run for a person), then the run is stepped again from durable state.
        `spawn_only` (the console's watchdog) merely wakes them."""
        self.project(project_id)
        report = []
        for row in self.store.all(
            "SELECT * FROM goal_runs WHERE project_id=? AND status IN ('RUNNING','VERIFYING')",
            (project_id,),
        ):
            if self._supervisor_alive(row["id"]):
                report.append({"goal_run_id": row["id"], "skipped": "a supervisor is stepping it"})
                continue
            if spawn_only:
                report.append({"goal_run_id": row["id"], "woken": self.wake_goal_run(row["id"])})
                continue
            phases = self.recover_phase_runs(project_id, operator_confirmed)
            step = self.run_goal_worker(row["id"], recovering=True)
            self._event_now(row, "goal_run_recovered", {
                "goal_run_id": row["id"], "phase_runs": phases, "acted": step.get("acted")})
            report.append({"goal_run_id": row["id"], "phase_runs": phases,
                           "status": step.get("status"), "acted": step.get("acted")})
        return report

    def wake_stalled_goal_runs(self):
        """Used by the console's watchdog: wake every live goal run (of any project) that has no
        supervisor and has been quiet for a while. Wakes only; recovery of dead workers stays an
        explicit act. Returns the ids woken."""
        woken = []
        for row in self.store.all(
            "SELECT id,updated FROM goal_runs WHERE status IN ('RUNNING','VERIFYING')"
        ):
            if self._supervisor_alive(row["id"]):
                continue
            idle = time.time() - self._clock(row["updated"]).timestamp()
            if idle > self.goal_stall_seconds and self.wake_goal_run(row["id"]):
                woken.append(row["id"])
        return woken

    # ---- reading ------------------------------------------------------------------------------------------------------

    def _goal_run_row_view(self, row):
        reason = row["reason"]
        return {
            "id": row["id"], "goal_id": row["goal_id"], "status": row["status"], "reason": reason,
            "reason_text": reason_text(reason) if reason else None,
            "resumable": bool(reason in GOAL_REASONS and GOAL_REASONS[reason][1]) if row["status"] in (
                "PAUSED", "WAITING_USER") else False,
            "detail": _json(row["detail"], {}), "plan_version": row["plan_version"],
            "plan_sha256": row["plan_sha256"], "adapter": row["adapter"],
            "created": row["created"], "updated": row["updated"], "finished": row["finished"],
        }

    def get_goal_run_for_goal(self, project_id, goal_id):
        row = self.store.one(
            "SELECT id FROM goal_runs WHERE project_id=? AND goal_id=?", (project_id, goal_id))
        return self.get_goal_run(project_id, row["id"]) if row else None

    def get_goal_run(self, project_id, run_id):
        """Everything about a goal run for a person to read: where each phase stands, who is working,
        what the whole goal has used, what the final verification found, and what to do next."""
        row = self._owned(project_id, run_id)
        summary = self._goal_run_row_view(row)
        version = self._plan_version_row(row)
        plan = json.loads(version["content"])
        limits = _json(row["budget"], {})
        budget = self.goal_budget(project_id, row["goal_id"], limits)
        runs = self._relevant_phase_runs(row, plan)
        by_phase = {r["phase_id"]: r for r in runs}
        ledger = {r["phase_id"]: r for r in self._ledger(run_id)}
        passed = {pid for pid, r in by_phase.items() if r["status"] == "PASSED"}
        phases, current = [], None
        for item in [*plan["phases"], *[
            _json(r["synthetic"], {}) for r in runs if (r["kind"] or "PLAN") == "INTEGRATION"
        ]]:
            r = by_phase.get(item["id"])
            if r is not None:
                state = r["status"]
            else:
                state = "READY" if all(d in passed for d in item["depends_on"]) else "BLOCKED"
            entry = {
                "id": item["id"], "title": item["title"], "depends_on": item["depends_on"],
                "kind": "INTEGRATION" if item["id"].startswith("INT-") else "PLAN",
                "state": state, "outputs": item["outputs"],
                "phase_run_id": r["id"] if r else None, "task_id": r["task_id"] if r else None,
                "reason": r["reason"] if r else None,
                "reason_text": reason_text(r["reason"]) if r and r["reason"] else None,
                "recorded": item["id"] in ledger,
                "waiting_for": [d for d in item["depends_on"] if d not in passed] if r is None else [],
            }
            if r is not None:
                full = self._run_summary(r)
                entry["attempts"] = full["budget"]["attempts"]
                entry["updated"] = r["updated"]
                if r["status"] in PHASE_ACTIVE:
                    active = self.store.one(
                        "SELECT role,agent,status,created FROM agent_executions WHERE project_id=? "
                        "AND task_id=? AND status IN ('QUEUED','RUNNING') ORDER BY created DESC",
                        (project_id, r["task_id"]))
                    entry["working"] = dict(active) if active else None
                    current = {"phase_id": item["id"], "phase_run_id": r["id"],
                               "working": entry["working"]}
            phases.append(entry)
        verifications = [
            {"id": v["id"], "seq": v["seq"], "status": v["status"], "outcome": v["outcome"],
             "started": v["started"], "finished": v["finished"], "evidence": v["evidence"]}
            for v in self._verification_rows(run_id)]
        events = [
            {"seq": e["seq"], "kind": e["kind"], "actor": e["actor"], "created": e["created"],
             "detail": _json(e["detail"], {})}
            for e in self.store.all(
                "SELECT * FROM goal_events WHERE project_id=? AND goal_id=? AND "
                "json_extract(detail,'$.goal_run_id')=? ORDER BY seq LIMIT 400",
                (project_id, row["goal_id"], run_id))]
        alive = self._supervisor_alive(run_id)
        idle = time.time() - self._clock(row["updated"]).timestamp()
        phase_busy = any(
            r["status"] in PHASE_ACTIVE and (
                self._stepper_alive(r["id"]) or self.store.one(
                    "SELECT 1 FROM agent_executions WHERE project_id=? AND task_id=? "
                    "AND status IN ('QUEUED','RUNNING')", (project_id, r["task_id"])))
            for r in runs)
        stalled = bool(row["status"] in ACTIVE and not alive and not phase_busy
                       and idle > self.goal_stall_seconds)
        return {
            **summary,
            "approval_id": row["approval_id"], "creator": row["creator"],
            "test_command": _json(row["test_command"], []), "base": _json(row["base"], {}),
            "phases": phases, "current": current, "ledger": [
                {"phase_id": r["phase_id"], "kind": r["kind"], "task_id": r["task_id"],
                 "evidence_sha256": r["evidence_sha256"], "handoff_sha256": r["handoff_sha256"],
                 "created": r["created"], "evidence": r["evidence"], "handoff": r["handoff"]}
                for r in self._ledger(run_id)],
            "verifications": verifications, "events": events, "budget": budget,
            "acceptance": self._acceptance_view(row, ledger, verifications),
            "limits": limits, "supervisor_alive": alive, "stalled": stalled,
            "idle_since": row["updated"],
            "automation": {
                "resident_daemon": False,
                "how": "Short workers are woken by every change; the console and `goal run-recover` "
                       "wake a run whose workers all died.",
            },
            "time_policy": "Counts the time phases are running and the final verification runs. "
                           "Waiting for a person and a pause do not count; a machine that sleeps "
                           "while a run is active does.",
            "actions": self._actions(row, verifications),
        }

    def _acceptance_view(self, row, ledger, verifications):
        """The criteria of the goal and of each passed phase, and what decided each (see
        GoalVerifyMixin.acceptance_matrix). Phase criteria were decided when the phase passed: by the
        host test, or by the person named in the phase's record."""
        usable = [v for v in verifications if v["status"] in ("PASSED", "NEEDS_PERSON")]
        last = usable[-1] if usable else None
        accepted_by = None
        if row["status"] == "PASSED" and row["reason"] == "PASSED_WITH_ACCEPTANCE":
            accepted_by = (_json(row["detail"], {}) or {}).get("accepted_by")
        phases = []
        for phase_id, record in ledger.items():
            ev = record["evidence"]
            phases.append({
                "phase_id": phase_id, "task_id": record["task_id"],
                "criteria": [{"id": c["id"], "decided_by": (
                    "host_test" if c["coverage"] == "host_test" else "person")} for c in ev["criteria"]],
                "host_test": ev["test"], "accepted_by": ev.get("passed_by")
                if any(c["coverage"] != "host_test" for c in ev["criteria"]) else None})
        return {"goal": self.acceptance_matrix(last, accepted_by) if last else [], "phases": phases}

    def _actions(self, row, verifications):
        status, reason = row["status"], row["reason"]
        actions = []
        if status in ACTIVE:
            actions.append("pause")
        if status in ("PAUSED", "WAITING_USER") and reason in GOAL_REASONS and GOAL_REASONS[reason][1]:
            actions.append("resume")
        if status == "WAITING_USER":
            if reason in ("FINAL_ACCEPTANCE_NEEDED", "VERIFICATION_NEEDS_PERSON"):
                actions.append("accept")
            actions.append("revise")
        if status in LIVE:
            actions.append("cancel")
        if status in ACTIVE:
            actions.append("recover")
        return actions
