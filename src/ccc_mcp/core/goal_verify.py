"""The evidence half of a goal run (Stage C): what is written down when a phase passes, the context
handed to the next phase, and the final verification of the whole goal.

Everything here is a method of `GoalRunHub` (core/goal_orchestrator.py), split out to keep each file
readable. The rules that hold whatever a model says:

* a phase is recorded as passed only from the phase run's own durable rows (the Task finished through
  its Review Gate and the host observed the configured test command pass); this file adds the git state
  and a record of who did the work, it never decides a pass;
* the final verification runs the project's configured test command on the whole project, on the host,
  exactly as a phase does. The plan's `final_acceptance` criteria whose command is that command are
  verified by it; every other criterion needs a person. A model's "done" is never evidence;
* the checks around the test run (the tree changed since the last phase passed, files nobody reported,
  protected files, test files that disappeared, unresolved review findings, a protected-path
  acknowledgement during a phase) can only make a pass need a person, never make a failure a pass;
* when verification fails, the allowed response is one integration-fix run inside the approved scope.
  Anything beyond that waits for a person; a revised plan is a new version that must be approved.
"""

import hashlib
import json
import re
import uuid
from pathlib import Path

from ..adapters.git import git, revision
from ..adapters.storage import decode, now
from ..infra.errors import BridgeError
from ..infra.security import canonical, redact, reject_secrets
from .context import identifier
from .goal_repair import clip, coverage, failure_signature, protected_changes, same_command
from .host_test_run import HostTestBusy, outcome_of

MAX_INTEGRATION_RUNS = 1          # per plan version; each is also paid for out of the goal's budget
HANDOFF_BLOCK, HANDOFF_TOTAL = 1100, 6000
_NOISE = re.compile(r"(^|/)(__pycache__|\.pytest_cache|\.mypy_cache|\.ruff_cache|\.ccc-mcp|\.agentbridge)(/|$)|\.pyc$|\.DS_Store$")
_TESTISH = re.compile(r"(^|/)(tests?/|test_[^/]*\.py$|[^/]*_test\.py$|[^/]*\.(test|spec)\.[a-z]+$)")


def _json(value, default=None):
    try:
        return json.loads(value) if value else default
    except ValueError:
        return default


def _sha(text):
    return hashlib.sha256(text.encode("utf-8", "replace")).hexdigest()


# ---- the working tree, read-only --------------------------------------------------------------------------


def changed_paths(root):
    """Paths that differ from HEAD (tracked changes and new files that git does not ignore), without
    the noise a test run leaves behind. Empty when the folder is not a git repository."""
    if revision(root) == "UNBORN":
        return []
    tracked = git(root, "diff", "HEAD", "--name-only", "-z", "--", ".", ":(exclude).ccc-mcp",
                  ":(exclude).agentbridge", check=False).stdout
    fresh = git(root, "ls-files", "--others", "--exclude-standard", "-z", check=False).stdout
    paths = {p for p in (tracked + b"\0" + fresh).decode("utf-8", "replace").split("\0") if p}
    return sorted(p for p in paths if not _NOISE.search(p))


def tree_digest(root):
    """A fingerprint of the project's working tree: HEAD plus every difference from it, with the
    contents of new files. None when there is no git history to compare with. Two equal digests
    mean the files are the same; it is how the supervisor notices a change nobody reported."""
    head = revision(root)
    if head == "UNBORN":
        return {"head": head, "digest": None}
    digest = hashlib.sha256(head.encode())
    diff = git(root, "diff", "HEAD", "--binary", "--no-ext-diff", "--no-textconv", "--", ".",
               ":(exclude).ccc-mcp", ":(exclude).agentbridge", ":(exclude)**/__pycache__",
               ":(exclude)**/.pytest_cache", check=False).stdout
    digest.update(b"\0diff\0" + diff)
    for path in changed_paths(root):
        target = Path(root) / path
        digest.update(b"\0" + path.encode())
        if target.is_file():
            digest.update(hashlib.sha256(target.read_bytes()).digest())
    return {"head": head, "digest": digest.hexdigest()}


# ---- the context package ----------------------------------------------------------------------------------


def compose_handoff(records):
    """The context the next phase is given: for each phase that passed, what it delivered, what it
    changed, how it was verified and what went wrong on the way. Built only from the recorded
    evidence (so it is the same after a restart), bounded in size, and plain text."""
    blocks = []
    for item in records:
        ev = item["evidence"]
        lines = [f"- {ev['phase_id']} \"{ev['title']}\" (task {ev['task_id']}): passed on attempt "
                 f"{len(ev['attempts'])} of {ev['budget']['max_attempts_per_phase']}"]
        if ev.get("outputs"):
            lines.append("  Delivered: " + "; ".join(ev["outputs"])[:300])
        files = ev["files"]["changed"] + ev["files"]["untracked"]
        if files:
            lines.append("  Files: " + ", ".join(files[:12]) + (" ..." if len(files) > 12 else ""))
        test = ev["test"]
        lines.append(f"  Verified: `{' '.join(test.get('command') or [])}` passed on the host"
                     f" (git {str(test.get('git_revision'))[:10]})")
        for attempt in ev["attempts"]:
            if attempt["status"] == "FAILED":
                lines.append(f"  Attempt {attempt['number']} failed ({attempt['outcome']})"
                             + (f": {attempt['failing'][0][:160]}" if attempt.get("failing") else ""))
        for repair in ev["repairs"]:
            if repair.get("diagnosis"):
                lines.append(f"  Decision after attempt {repair['after_attempt']}: "
                             f"{repair['diagnosis'][:200]}")
        block = "\n".join(lines)
        blocks.append(block[:HANDOFF_BLOCK])
    return redact("\n".join(blocks))[:HANDOFF_TOTAL]


class GoalVerifyMixin:
    # ---- what a passed phase leaves behind ------------------------------------------------------------

    def _passport(self, goal_run, phase_run):
        """The record of a passed phase run: the Task, who did the work (role, role identity,
        provider), which prompt each run was given, the host test, the files, the git state."""
        project_id, task_id = phase_run["project_id"], phase_run["task_id"]
        _, _, phase, _ = self._bound_phase(phase_run)
        root = Path(self.project(project_id)["root"])
        builders = self._builder_rows(phase_run)
        saved = (decode(builders[-1], "result").get("result") or {}) if builders else {}
        test = self._latest_test_result(project_id, task_id) or {}
        executions = []
        for row in self.store.all(
            "SELECT e.id,e.role,e.role_ref,e.step_id,e.agent,e.status,e.options,e.instruction,"
            "p.prompt_sha256,p.provider FROM agent_executions e LEFT JOIN execution_prompts p "
            "ON p.execution_id=e.id WHERE e.project_id=? AND e.task_id=? ORDER BY e.created, e.id",
            (project_id, task_id),
        ):
            options = _json(row["options"], {})
            executions.append({
                "id": row["id"], "role": row["role"], "role_ref": row["role_ref"],
                "step_id": row["step_id"], "agent": row["agent"],
                "provider": row["provider"] or row["agent"],
                # The full prompt text is stored only for a custom role's run; for the built-in roles
                # the hash of the instruction that was sent is what identifies the prompt.
                "status": row["status"], "flow_step": options.get("flow_step"),
                "fallback_from": options.get("fallback_from"),
                "prompt_sha256": row["prompt_sha256"],
                "instruction_sha256": _sha(row["instruction"] or "")})
        attempts = [
            {"number": a["number"], "kind": a["kind"], "status": a["status"], "outcome": a["outcome"],
             "signature": a["evidence"].get("signature"),
             "failing": (a["evidence"].get("failing_lines") or a["evidence"].get("signature_lines")
                         or [])[:3]}
            for a in self._attempt_rows(phase_run)]
        repairs = [
            {"after_attempt": r["after_attempt"], "decision": r["decision"],
             "diagnosis": (r["spec"] or {}).get("diagnosis") if isinstance(r["spec"], dict) else None,
             "execution": r["restart_execution_id"]}
            for r in self._repair_rows(phase_run) if r["status"] == "SUCCEEDED"]
        tree = tree_digest(root)
        accepted = self.store.one(
            "SELECT actor FROM goal_events WHERE project_id=? AND goal_id=? AND kind='phase_run_passed' "
            "AND json_extract(detail,'$.run_id')=? ORDER BY seq DESC LIMIT 1",
            (project_id, phase_run["goal_id"], phase_run["id"]))
        return {
            "phase_id": phase_run["phase_id"], "title": phase["title"], "outputs": phase["outputs"],
            "kind": phase_run["kind"] or "PLAN", "phase_run_id": phase_run["id"], "task_id": task_id,
            "plan_version": phase_run["plan_version"], "plan_sha256": phase_run["plan_sha256"],
            "workflow_id": phase_run["workflow_id"], "budget": _json(phase_run["budget"], {}),
            "attempts": attempts, "repairs": repairs, "executions": executions,
            "gates": [{"role": g["role"], "verdict": g["verdict"]}
                      for g in self.flow_gate_history(project_id, task_id)],
            "test": {"command": test.get("command"), "passed": test.get("passed"),
                     "returncode": test.get("returncode"),
                     "executed_by_host": test.get("executed_by_host"),
                     "git_revision": test.get("git_revision")},
            "files": {"changed": list(saved.get("changed_files") or [])[:80],
                      "untracked": list(saved.get("untracked_files") or [])[:80]},
            "git": {"head": tree["head"], "tree_digest": tree["digest"]},
            "criteria": [{"id": c["id"], "coverage": c["coverage"]}
                         for c in _json(phase_run["criteria"], [])],
            "passed_by": accepted["actor"] if accepted else None,
        }

    def _record_passed(self, goal_run, phase_run):
        """Write the passed phase down, once (a second call changes nothing). This is the step a
        crash can interrupt: nothing downstream depends on it having happened, because the next
        decision does it again."""
        evidence = self._passport(goal_run, phase_run)
        text = canonical(evidence)
        record = {"evidence": evidence}
        handoff = compose_handoff([record])
        with self.store.transaction() as conn:
            if conn.execute(
                "SELECT 1 FROM goal_run_phases WHERE goal_run_id=? AND phase_id=?",
                (goal_run["id"], phase_run["phase_id"]),
            ).fetchone():
                return False
            conn.execute(
                "INSERT INTO goal_run_phases(id,goal_run_id,project_id,goal_id,phase_id,phase_run_id,"
                "task_id,kind,evidence,evidence_sha256,handoff,handoff_sha256,created) "
                "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                ("GRP-" + uuid.uuid4().hex[:16], goal_run["id"], goal_run["project_id"],
                 goal_run["goal_id"], phase_run["phase_id"], phase_run["id"], phase_run["task_id"],
                 evidence["kind"], text, _sha(text), handoff, _sha(handoff), now()))
            self._event(
                conn, goal_run["project_id"], goal_run["goal_id"], "goal_phase_recorded", "system",
                {"goal_run_id": goal_run["id"], "phase_id": phase_run["phase_id"],
                 "phase_run_id": phase_run["id"], "task_id": phase_run["task_id"],
                 "evidence_sha256": _sha(text), "git": evidence["git"],
                 "attempts": len(evidence["attempts"]),
                 "providers": sorted({e["provider"] or e["agent"] for e in evidence["executions"]})})
        return True

    def _ledger(self, goal_run_id):
        return [
            {**dict(row), "evidence": _json(row["evidence"], {})} for row in self.store.all(
                "SELECT * FROM goal_run_phases WHERE goal_run_id=? ORDER BY rowid", (goal_run_id,))]

    def _handoff_for(self, goal_run_id, plan, phase):
        """The context package for `phase`: every phase that passed before it, in the order they
        passed, so a later phase also sees what an earlier one decided."""
        wanted = [r for r in self._ledger(goal_run_id) if r["kind"] == "PLAN"]
        if phase.get("depends_on") is not None and phase["id"] != "INT-1":
            needed, stack = set(), list(phase["depends_on"])
            by_id = {p["id"]: p for p in plan["phases"]}
            while stack:
                current = stack.pop()
                if current not in needed:
                    needed.add(current)
                    stack.extend(by_id.get(current, {}).get("depends_on", []))
            wanted = [r for r in wanted if r["phase_id"] in needed]
        return compose_handoff(wanted) or None

    # ---- the final verification ---------------------------------------------------------------------------

    def _verification_rows(self, goal_run_id):
        return [
            {**dict(row), "evidence": _json(row["evidence"], {})} for row in self.store.all(
                "SELECT * FROM goal_verifications WHERE goal_run_id=? ORDER BY seq", (goal_run_id,))]

    def _verification_anchor(self, run, plan):
        """The Task the host test is recorded against. Host tests are recorded per Task, and the
        verification must not overwrite the evidence of a phase's own Task, so it has one of its own.
        It is never dispatched: it is cancelled the moment it exists."""
        anchor = identifier(f"{run['goal_id']}-VERIFY")
        if not self.store.one("SELECT 1 FROM tasks WHERE project_id=? AND id=?",
                              (run["project_id"], anchor)):
            ledger = self._ledger(run["id"])
            flow = ledger[-1]["evidence"]["workflow_id"] if ledger else plan["phases"][0]["workflow"]
            try:
                self.create_task(
                    run["project_id"],
                    f"Final verification of goal {run['goal_id']}. This Task only holds the host "
                    "test result of the whole project; it is never run.",
                    ["The configured test command passes on the whole project"],
                    task_id=anchor, workflow=flow, phase_workflow=True,
                    frozen_workflow=self.goal_plan_workflow(
                        dict(self._plan_version_row(run)), flow))
            except BridgeError as exc:
                if "already exists" not in str(exc):
                    raise
            try:
                self.cancel_task(run["project_id"], anchor, "Verification anchor; never dispatched")
            except BridgeError:
                pass
        return anchor

    def _checks(self, run, goal_run_plan, ledger, root):
        """Everything around the test run that a pass must also satisfy (see the module docstring).
        Each problem is {"code", "detail"}; any problem means a person decides."""
        problems = []
        base = _json(run["base"], {})
        now_tree = tree_digest(root)
        before = ledger[-1]["evidence"]["git"]["tree_digest"] if ledger else base.get("tree_digest")
        drift = bool(now_tree["digest"] and before and now_tree["digest"] != before)
        if drift:
            problems.append({
                "code": "TREE_CHANGED",
                "detail": "The files changed after the last phase passed, outside any recorded run."})
        recorded = set()
        for record in ledger:
            recorded.update(record["evidence"]["files"]["changed"])
            recorded.update(record["evidence"]["files"]["untracked"])
        current = changed_paths(root)
        unaccounted = sorted(set(current) - set(base.get("dirty", [])) - recorded)
        if unaccounted:
            problems.append({
                "code": "UNREPORTED_FILES",
                "detail": "Files that differ from the last commit were not reported by any phase: "
                          + ", ".join(unaccounted[:10])})
        protected = protected_changes([p for p in current if p not in set(base.get("dirty", []))])
        if protected:
            problems.append({
                "code": "PROTECTED_PATHS",
                "detail": "Protected files differ from the last commit: "
                          + ", ".join(item["path"] for item in protected[:10])})
        removed = sorted(p for p in recorded if _TESTISH.search(p) and not (Path(root) / p).exists())
        if removed:
            problems.append({
                "code": "TESTS_REMOVED",
                "detail": "Test files a phase reported are gone: " + ", ".join(removed[:10])})
        review = []
        for record in ledger:
            task_id = record["task_id"]
            for row in self.store.all(
                "SELECT role,reason FROM flow_escalations WHERE project_id=? AND task_id=? "
                "AND status='OPEN'", (run["project_id"], task_id)):
                review.append(f"{task_id}: {row['role']} {row['reason']}")
            last = {}
            for gate in self.flow_gate_history(run["project_id"], task_id):
                last[gate["role"]] = gate["verdict"]
            review += [f"{task_id}: {role} verdict {verdict}" for role, verdict in last.items()
                       if verdict != "PASS"]
        if review:
            problems.append({"code": "OPEN_REVIEW_FINDINGS",
                             "detail": "Unresolved review findings: " + "; ".join(review[:6])})
        deviations = []
        for row in self.store.all(
            "SELECT detail FROM goal_events WHERE project_id=? AND goal_id=? "
            "AND kind='phase_run_resumed'", (run["project_id"], run["goal_id"])):
            if (_json(row["detail"], {}).get("detail") or {}).get("acknowledged") == "protected-paths":
                deviations.append("a phase was resumed with protected paths acknowledged")
        if deviations:
            problems.append({"code": "DEVIATION_ACKNOWLEDGED", "detail": deviations[0]})
        criteria = []
        command = _json(run["test_command"], [])
        for item in goal_run_plan["final_acceptance"]:
            criteria.append({"id": item["id"], "statement": item["statement"],
                             "kind": item["verify"]["kind"], "coverage": coverage(item, command)})
        return {
            "tree": {"head": now_tree["head"], "digest": now_tree["digest"], "before": before,
                     "changed_since_last_phase": drift},
            "changed_files": current[:200], "unreported_files": unaccounted[:60],
            "problems": problems, "criteria": criteria,
        }

    def _run_verification(self, run, plan):
        """One verification: the configured test command on the whole project, then the checks. The
        host test goes through the Stage 0 session, so it is mutually exclusive with any other test of
        the same Task, an interrupted one is recognised, and an orphan that cannot be proven gone
        stops the goal instead of running a second test beside it."""
        from ..adapters.test_runner import execute_test_command

        project_id, goal_id = run["project_id"], run["goal_id"]
        if self.store.one(
            "SELECT 1 FROM agent_executions WHERE project_id=? AND status IN ('QUEUED','RUNNING')",
            (project_id,)):
            return {"status": run["status"], "acted": None, "waiting_for": "agent"}
        budget = self.goal_budget(project_id, goal_id, _json(run["budget"], {}))
        if budget["minutes"]["left"] <= 0:
            self._set_goal_status(run["id"], "EXHAUSTED", expect=("RUNNING", "VERIFYING"),
                                  reason="TIME_BUDGET", detail={"budget": budget},
                                  kind="goal_run_exhausted")
            return {"status": "EXHAUSTED", "acted": "time_budget"}
        self._set_goal_status(run["id"], "VERIFYING", expect=("RUNNING",), kind="goal_run_verifying")
        anchor = self._verification_anchor(run, plan)
        ledger = self._ledger(run["id"])
        seq = len(self._verification_rows(run["id"])) + 1
        verification_id = "GVER-" + uuid.uuid4().hex[:16]
        with self.store.transaction() as conn:
            conn.execute(
                "INSERT INTO goal_verifications(id,goal_run_id,project_id,goal_id,seq,anchor_task_id,"
                "status,started) VALUES(?,?,?,?,?,?,'RUNNING',?)",
                (verification_id, run["id"], project_id, goal_id, seq, anchor, now()))
            self._event(conn, project_id, goal_id, "goal_verification_started", "system",
                        {"goal_run_id": run["id"], "verification_id": verification_id, "seq": seq})
        command = _json(run["test_command"], [])
        root = Path(self.project(project_id)["root"])
        access = self.get_project_access(project_id)
        if access["mode"] != "read-write":
            self._finish_verification(
                verification_id, "INTERRUPTED", "not authorized", {"why": "project is not authorized"})
            self._set_goal_status(
                run["id"], "WAITING_USER", expect=("RUNNING", "VERIFYING"), reason="ENVIRONMENT_BLOCKED",
                detail={"why": "The project is no longer authorized for writes."}, resume_to="RUNNING")
            return {"status": "WAITING_USER", "acted": "not_authorized"}
        try:
            with self.host_test_session(project_id, anchor, enforce_runnable=False) as outcome:
                result = execute_test_command(
                    self, project_id, anchor, command, access["identity"], root)
                outcome[0] = outcome_of(result)
        except HostTestBusy as exc:
            self._finish_verification(
                verification_id, "INTERRUPTED", "host test not safe to run",
                {"why": str(exc)[:500]})
            self._set_goal_status(
                run["id"], "WAITING_USER", expect=("RUNNING", "VERIFYING"), reason="HOST_TEST_UNSAFE",
                detail={"why": str(exc)[:500]}, resume_to="RUNNING")
            return {"status": "WAITING_USER", "acted": "host_test_unsafe"}
        test = self._latest_test_result(project_id, anchor) or {}
        checks = self._checks(run, plan, ledger, root)
        stdout, cut = clip(test.get("stdout", ""), 6000)
        stderr, _ = clip(test.get("stderr", ""), 2000)
        signature, lines = None, []
        if test.get("passed") is False:
            signature, lines, _ = failure_signature(test.get("stdout", ""), test.get("stderr", ""))
        host = {
            "command": test.get("command"), "passed": test.get("passed"),
            "returncode": test.get("returncode"), "executed_by_host": test.get("executed_by_host"),
            "no_tests_collected": bool(test.get("no_tests_collected")),
            "interrupted": bool(test.get("interrupted")),
            "runtime_blocked": bool(test.get("runtime_blocked")),
            "reason": test.get("reason") or test.get("error"), "git_revision": test.get("git_revision"),
            "stdout": stdout, "stderr": stderr, "truncated": bool(cut), "signature": signature,
            "failing_lines": lines[:10],
        }
        evidence = {"host_test": host, "ledger_count": len(ledger), "plan_sha256": run["plan_sha256"],
                    "anchor_task_id": anchor, **checks}
        green = bool(
            host["executed_by_host"] and host["passed"] is True and not host["no_tests_collected"]
            and not host["interrupted"] and not host["runtime_blocked"]
            and same_command(host["command"] or [], command))
        if green and not checks["problems"]:
            status, outcome_text = "PASSED", "the configured test command passes on the whole project"
        elif green:
            status, outcome_text = "NEEDS_PERSON", "tests pass, but a person has to decide"
        elif host["runtime_blocked"] or host["interrupted"] or not host["executed_by_host"]:
            status, outcome_text = "INTERRUPTED", "the host test did not run to a result"
        else:
            status, outcome_text = "FAILED", "the configured test command fails on the whole project"
        self._finish_verification(verification_id, status, outcome_text, evidence)
        row = next(r for r in self._verification_rows(run["id"]) if r["id"] == verification_id)
        return self._act_on_verification(self._goal_run(run["id"]), plan, row)

    def _finish_verification(self, verification_id, status, outcome, evidence):
        with self.store.transaction() as conn:
            row = conn.execute(
                "SELECT * FROM goal_verifications WHERE id=?", (verification_id,)).fetchone()
            if not row or row["status"] != "RUNNING":
                return False
            conn.execute(
                "UPDATE goal_verifications SET status=?,outcome=?,evidence=?,finished=? WHERE id=?",
                (status, outcome, canonical(evidence), now(), verification_id))
            self._event(conn, row["project_id"], row["goal_id"], "goal_verification_finished",
                        "system", {"goal_run_id": row["goal_run_id"], "verification_id": verification_id,
                                   "seq": row["seq"], "status": status, "outcome": outcome})
        return True

    def _verify(self, run, goal, plan):
        """The whole-goal step once every plan phase has passed. Reads the newest verification and
        either runs a new one, acts on the current one, or asks for the integration fix."""
        run_id = run["id"]
        rows = self._verification_rows(run_id)
        last = rows[-1] if rows else None
        if last and last["status"] == "RUNNING":
            # The supervisor died during a verification. Its host test is dealt with by the Stage 0
            # session of the next run; this record is closed as interrupted, never as a pass.
            self._finish_verification(last["id"], "INTERRUPTED", "supervisor stopped", {})
            rows = self._verification_rows(run_id)
            last = rows[-1]
        ledger = self._ledger(run_id)
        current = bool(
            last and last["status"] in ("PASSED", "NEEDS_PERSON", "FAILED")
            and last["evidence"].get("ledger_count") == len(ledger)
            and last["evidence"].get("plan_sha256") == run["plan_sha256"])
        if current and last["status"] in ("PASSED", "NEEDS_PERSON"):
            root = Path(self.project(run["project_id"])["root"])
            current = tree_digest(root)["digest"] == last["evidence"]["tree"]["digest"]
        if not current:
            return self._run_verification(run, plan)
        return self._act_on_verification(run, plan, last)

    def _act_on_verification(self, run, plan, last):
        from .goal_orchestrator import reason_text

        run_id = run["id"]
        if last["status"] == "PASSED":
            return self._conclude_manual(run, last)
        if last["status"] == "NEEDS_PERSON":
            code = "VERIFICATION_NEEDS_PERSON"
            self._set_goal_status(
                run_id, "WAITING_USER", expect=("RUNNING", "VERIFYING"), reason=code,
                detail={"message": reason_text(code), "problems": last["evidence"]["problems"],
                        "criteria": [c["id"] for c in last["evidence"]["criteria"]
                                     if c["coverage"] != "host_test"],
                        "verification": last["id"]}, resume_to="RUNNING")
            return {"status": "WAITING_USER", "acted": "needs_person"}
        if last["status"] == "INTERRUPTED":
            host = last["evidence"].get("host_test") or {}
            code = ("ENVIRONMENT_BLOCKED" if host.get("runtime_blocked") or not host.get("executed_by_host")
                    else "HOST_TEST_INTERRUPTED")
            self._set_goal_status(
                run_id, "WAITING_USER", expect=("RUNNING", "VERIFYING"), reason=code,
                detail={"message": reason_text(code), "verification": last["id"],
                        "reason": host.get("reason")}, resume_to="RUNNING")
            return {"status": "WAITING_USER", "acted": code.lower()}
        return self._after_failed_verification(run, plan, last)

    @staticmethod
    def acceptance_matrix(last, accepted_by=None):
        """Which evidence decided each final criterion: the host test run (with its command, exit code
        and git revision) or a named person. A criterion with neither is `pending`; nothing else is
        ever accepted as evidence (a model's self-assessment, or a suite that was merely green)."""
        host = (last or {}).get("evidence", {}).get("host_test") or {}
        rows = []
        for item in (last or {}).get("evidence", {}).get("criteria", []):
            if item["coverage"] == "host_test":
                decided = {"by": "host_test", "verification": last["id"],
                           "command": host.get("command"), "returncode": host.get("returncode"),
                           "git_revision": host.get("git_revision"),
                           "passed": bool(last["status"] in ("PASSED", "NEEDS_PERSON"))}
            elif accepted_by:
                decided = {"by": "person", "identity": accepted_by}
            else:
                decided = {"by": "pending"}
            rows.append({"id": item["id"], "statement": item["statement"], "kind": item["kind"],
                         "decided": decided})
        return rows

    def _conclude_manual(self, run, last):
        """Tests and checks are clean. The final criteria that no command can judge still wait for a
        person; if there are none the goal passes."""
        pending = [c for c in last["evidence"]["criteria"] if c["coverage"] != "host_test"]
        if pending:
            from .goal_orchestrator import reason_text

            self._set_goal_status(
                run["id"], "WAITING_USER", expect=("RUNNING", "VERIFYING"),
                reason="FINAL_ACCEPTANCE_NEEDED",
                detail={"message": reason_text("FINAL_ACCEPTANCE_NEEDED"),
                        "criteria": [c["id"] for c in pending], "verification": last["id"]},
                resume_to="RUNNING")
            return {"status": "WAITING_USER", "acted": "final_acceptance_needed"}
        self._set_goal_status(
            run["id"], "PASSED", expect=("RUNNING", "VERIFYING"), reason="PASSED",
            detail={"verification": last["id"], "criteria": [c["id"] for c in last["evidence"]["criteria"]],
                    "acceptance": self.acceptance_matrix(last),
                    "verified_by": "host test command on the whole project"},
            kind="goal_run_passed")
        return {"status": "PASSED", "acted": "goal_passed"}

    def _after_failed_verification(self, run, plan, last):
        from .goal_orchestrator import reason_text

        used = self.store.one(
            "SELECT COUNT(*) AS n FROM goal_phase_runs WHERE project_id=? AND goal_id=? AND kind="
            "'INTEGRATION' AND plan_version=?", (run["project_id"], run["goal_id"], run["plan_version"]))["n"]
        budget = self.goal_budget(run["project_id"], run["goal_id"], _json(run["budget"], {}))
        if used >= MAX_INTEGRATION_RUNS or budget["dispatches"]["left"] < 1:
            code = "FINAL_VERIFICATION_FAILED"
            self._set_goal_status(
                run["id"], "WAITING_USER", expect=("RUNNING", "VERIFYING"), reason=code,
                detail={"message": reason_text(code), "verification": last["id"],
                        "integration_runs": used, "failing": last["evidence"]["host_test"]["failing_lines"],
                        "budget": budget}, resume_to="RUNNING")
            return {"status": "WAITING_USER", "acted": "verification_failed"}
        return self._start_integration(run, plan, last, used + 1)

    def _start_integration(self, run, plan, last, number):
        """A cross-phase problem: the whole project fails although every phase passed. One integration
        fix runs through the same Stage B machinery (same Task engine, repair loop and budget), with
        the verification's own evidence as its requirement. It cannot change a finished phase's
        record, the criteria or the plan."""
        host = last["evidence"]["host_test"]
        facts = "\n".join(host.get("failing_lines") or [])[:1500] or (host.get("stdout") or "")[-1500:]
        try:
            reject_secrets(facts)
        except BridgeError:
            facts = "(the failing output is withheld because it looks like it contains a secret)"
        ledger = self._ledger(run["id"])
        test_command = _json(run["test_command"], [])
        phase_id = f"INT-{number}"
        synthetic = {
            "id": phase_id, "title": f"Integration fix {number}",
            "scope": (
                "Every phase of this goal passed on its own, but the final verification of the whole "
                "project failed. Fix the code so the project's configured test command passes again, "
                "changing only what the failure requires. Do not change, delete or weaken any test, the "
                "test command or the acceptance criteria, and do not redo finished phases.\n\n"
                f"Failing output of the final verification:\n{facts}"),
            "outputs": ["The whole project passes the configured test command"],
            "depends_on": [p["id"] for p in plan["phases"]],
            "acceptance": [{
                "id": f"INT{number}-C1",
                "statement": "The project's configured test command passes on the whole project",
                "verify": {"kind": "host_test", "command": list(test_command),
                           "expect_exit_code": None, "description": None}}],
            "workflow": ledger[-1]["evidence"]["workflow_id"] if ledger else plan["phases"][0]["workflow"],
            "provider_hint": None, "risk": "medium",
        }
        handoff = compose_handoff([r for r in ledger])
        pm = _json(run["pm_settings"], {})
        self._event_now(run, "goal_integration_requested", {
            "goal_run_id": run["id"], "phase_id": phase_id, "verification": last["id"],
            "failing": host.get("failing_lines")})
        try:
            started = self.start_phase_run(
                run["project_id"], run["goal_id"], phase_id, adapter=run["adapter"],
                fake_scenario=run["fake_scenario"] or "success", pm_provider=pm.get("provider"),
                pm_scenario=pm.get("scenario", "success"), pm_timeout=run["pm_timeout"],
                goal_run_id=run["id"], handoff=handoff or None, synthetic=synthetic,
                budget_override=_json(run["budget"], {}))
        except Exception as exc:
            from .goal_orchestrator import reason_text

            self._set_goal_status(
                run["id"], "WAITING_USER", expect=("RUNNING", "VERIFYING"),
                reason="SUPERVISOR_ERROR",
                detail={"message": reason_text("SUPERVISOR_ERROR"), "error": redact(str(exc))[:500],
                        "while": "starting the integration fix"}, resume_to="RUNNING")
            return {"status": "WAITING_USER", "acted": "integration_refused"}
        self._set_goal_status(run["id"], "RUNNING", expect=("VERIFYING",), kind="goal_run_running",
                              detail={"integration": phase_id})
        return {"status": "RUNNING", "acted": "integration_started", "phase_run": started["id"]}
