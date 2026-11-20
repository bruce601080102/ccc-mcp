import time

from ...core.goal_plan import DeliveryPMReport
from ...core.goal_repair import DeliveryPMRepair, protected_reason
from ...core.models import AgentReport, ClarificationRequest, ClarificationResponse
from ...infra.errors import AgentCancelled, AgentTimeout, BridgeError
from .base import BaseAgentAdapter

# fake_scenario values usable when package["report_schema"] == "clarification_request"
# (Meeting Mode's read-only SD/RD ask) or "clarification_response" (SA's answer).
# Kept separate from the original {"success","revision","error"} enum so a plain
# AgentReport dispatch can never accidentally be scripted as a clarification exchange.
CLARIFICATION_REQUEST_SCENARIOS = {"ask_question", "no_blocking_question"}
CLARIFICATION_RESPONSE_SCENARIOS = {"confirmed", "recommendation", "pm_decision_required"}


GATE_FINDINGS_ROLES = {"REVIEW", "SR", "AR", "SEC", "QA", "VC", "RV"}
DOCUMENT_ROLES = {"SW", "PA", "RS", "AN", "PL", "CS", "WR", "ED"}
SPEC_DOCUMENT = "# Specification\n\n## Requirements\n- REQ-001 The system MUST do the thing.\n"


class FakeAgentAdapter(BaseAgentAdapter):
    """Deterministic simulation; no credentials, subprocess model or code mutation."""

    def __init__(self, scenario="success", delay=0.01, clarification_text=None):
        self.scenario, self.delay = scenario, delay
        self.clarification_text = clarification_text

    def run(self, package, instruction, cwd, timeout, cancelled):
        started = time.monotonic()
        while time.monotonic() - started < self.delay:
            if cancelled():
                raise AgentCancelled("Fake execution cancelled")
            if time.monotonic() - started > timeout:
                raise AgentTimeout("Fake execution timed out")
            time.sleep(0.01)
        if cancelled():
            raise AgentCancelled("Fake execution cancelled")
        if self.scenario == "error":
            raise BridgeError("Simulated agent failure")
        report_schema = package.get("report_schema", "agent_report")
        if report_schema == "goal_plan":
            return scripted_goal_plan(self.scenario, package)
        if report_schema == "goal_repair":
            return scripted_goal_repair(self.scenario, package)
        if report_schema == "clarification_request":
            if self.scenario not in CLARIFICATION_REQUEST_SCENARIOS:
                raise BridgeError(
                    f"fake_scenario {self.scenario!r} is not valid for report_schema "
                    "'clarification_request'"
                )
            asking = self.scenario == "ask_question"
            return ClarificationRequest(
                blocking_question=(self.clarification_text or "需要澄清哪個檔案是入口點？")
                if asking
                else None,
                no_blocking_question=not asking,
                rationale="Simulated SD/RD clarification check; no actual code analysis performed.",
            ).model_dump()
        if report_schema == "clarification_response":
            if self.scenario not in CLARIFICATION_RESPONSE_SCENARIOS:
                raise BridgeError(
                    f"fake_scenario {self.scenario!r} is not valid for report_schema "
                    "'clarification_response'"
                )
            return ClarificationResponse(
                response_type=self.scenario,
                content=self.clarification_text or f"Simulated SA {self.scenario} answer.",
            ).model_dump()
        revision = self.scenario in {"revision", "reject", "unsupported_claim"}
        findings = ["Fake adapter validated the handoff; no actual code analysis performed."]
        if package["role"] in GATE_FINDINGS_ROLES:
            # Written the way a real reviewer is told to, so the gate rules can be exercised.
            findings = {
                "revision": ["[major] Scripted blocking problem | evidence: fake adapter scenario 'revision'"],
                "reject": ["[blocker] Scripted fundamental flaw | evidence: fake adapter scenario 'reject'"],
                "unsupported_claim": ["[major] Scripted claim with no support"],
            }.get(self.scenario, findings)
        return AgentReport(
            summary=f"Simulated {package['role']} for {package['task_id']}",
            findings=findings,
            risks=["Simulation only; real code and tests require verification."],
            next_action="修正 Review 問題後重新檢查。" if revision else "PM 閱讀結果並決定下一步。",
            needs_revision=revision,
            simulated=True,
            document=SPEC_DOCUMENT if package["role"] in DOCUMENT_ROLES else "",
        ).model_dump()


def _criterion(identifier, statement, kind="host_test"):
    verify = (
        {"kind": "host_test", "command": ["python", "-m", "pytest", "-q"], "expect_exit_code": 0,
         "description": None}
        if kind == "host_test"
        else {"kind": "manual", "command": None, "expect_exit_code": None,
              "description": "A person looks at the result and confirms it"}
    )
    return {"id": identifier, "statement": statement, "verify": verify}


def scripted_goal_plan(scenario, package):
    """A scripted Delivery PM answer, for tests and demos only. `phases:N` makes N phases
    (any N, so tests can show the count is not a template); the named failure scenarios return
    answers the validator must refuse. A change request is echoed into the first phase so a
    revision differs from the version it was made from."""
    count = 2
    if scenario.startswith("phases:"):
        count = int(scenario.split(":", 1)[1])
    elif scenario == "empty":
        count = 0
    content = package.get("content", {})
    request = content.get("change_request")
    goal = content.get("goal", {})
    workflow = next(
        (flow["id"] for flow in content.get("workflows", []) if flow["id"] == "standard"),
        (content.get("workflows") or [{"id": "standard"}])[0]["id"],
    )
    phases = []
    for index in range(1, count + 1):
        depends = [f"P{index - 1}"] if index > 1 else []
        if index >= 4:
            depends.append(f"P{index - 2}")
        scope = f"Stage {index} of: {goal.get('title', 'the goal')}"
        if index == 1 and request:
            scope += f". Revised for: {request}"
        phases.append({
            "id": f"P{index}", "title": f"Phase {index}", "scope": scope,
            "outputs": [f"Deliverable {index}"], "depends_on": depends,
            "acceptance": [_criterion(f"C{index}-1", f"Phase {index} tests pass")],
            "workflow": workflow, "provider_hint": None, "risk": "low",
        })
    final = [_criterion("F-1", "The whole goal works end to end")]
    if scenario == "cyclic" and len(phases) >= 2:
        phases[0]["depends_on"] = ["P2"]
        phases[1]["depends_on"] = ["P1"]
    elif scenario == "duplicate_ids" and len(phases) > 1:
        phases[1]["id"] = phases[0]["id"]
    elif scenario == "missing_dependency" and phases:
        phases[-1]["depends_on"] = ["P99"]
    elif scenario == "no_acceptance" and phases:
        phases[0]["acceptance"] = []
    elif scenario == "shell_command" and phases:
        phases[0]["acceptance"][0]["verify"]["command"] = ["sh", "-c", "pytest && git push"]
    elif scenario == "git_push" and phases:
        phases[0]["acceptance"][0]["verify"]["command"] = ["git", "push", "origin", "main"]
    elif scenario == "unknown_workflow" and phases:
        phases[0]["workflow"] = "no-such-workflow"
    elif scenario == "schema_error":
        raise BridgeError("Agent returned invalid structured report")
    plan = {
        "summary": f"Plan for {goal.get('title', 'the goal')}", "in_scope": ["The stated goal"],
        "out_of_scope": ["Anything not in the goal"], "phases": phases,
        "final_acceptance": final, "decisions_needed": [], "assumptions": [], "risks": [],
    }
    message = f"I planned {count} phase(s)." + (f" Revised for: {request}." if request else "")
    return DeliveryPMReport.model_validate({"message": message, "plan": plan}).model_dump()


def scripted_goal_repair(scenario, package):
    """A scripted Delivery PM analysis of a failed attempt, for tests and demos only. It reads the
    evidence it is given (so its quotes are real) and answers as `scenario` says; the named failure
    scenarios return answers the validator must refuse or downgrade."""
    content = package.get("content", {})
    criteria = content.get("acceptance_criteria", [])
    command = content.get("host_test_command") or ["python", "-m", "pytest", "-q"]
    failure = content.get("failure", {})
    stdout = (failure.get("host_test") or {}).get("stdout", "") or ""
    review = failure.get("review") or {}
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    quote = next((line for line in lines if "FAILED" in line or "assert" in line), None)
    if quote is None and lines:
        quote = lines[-1]
    source = "test_output"
    if quote is None and review.get("blocking"):
        quote, source = review["blocking"][0]["claim"], "review"
    quote = (quote or "no output")[:200]
    changed = [p for p in failure.get("changed_files", []) if not protected_reason(p)]
    spec = {
        "decision": "repair",
        "diagnosis": f"The attempt did not pass: {failure.get('kind', 'a check')} failed.",
        "root_causes": [{
            "statement": "The implementation does not satisfy the failing check shown in the evidence.",
            "confidence": "confirmed", "evidence": [{"source": source, "quote": quote}],
        }],
        "change_scope": changed or ["app.py"],
        "must_not_change": ["The approved acceptance criteria", "The tests and the test command"],
        "rerun_tests": list(command),
        "completion_conditions": [
            {"criterion_id": item["id"], "how_verified": "The host test command passes"}
            for item in criteria
        ],
        "needs_reanalysis": False, "reanalysis_reason": None,
        "instructions": "Fix the implementation so the failing check in the evidence passes.",
        "needs_human_reason": None,
    }
    extra = {}
    if scenario == "needs_human":
        spec["decision"] = "needs_human"
        spec["needs_human_reason"] = "The evidence does not show why this fails; a person must look."
    elif scenario == "plan_change":
        spec["decision"] = "needs_human"
        spec["needs_human_reason"] = "The failure cannot be fixed inside the approved scope."
        spec["plan_change_needed"] = True
        spec["plan_change_summary"] = (
            "Add a phase that moves slug handling into its own module and migrates both callers.")
    elif scenario == "wrong_tests":
        spec["rerun_tests"] = ["python", "-c", "print('ok')"]
    elif scenario == "drop_criterion" and spec["completion_conditions"]:
        spec["completion_conditions"] = spec["completion_conditions"][1:]
    elif scenario == "invent_criterion":
        spec["completion_conditions"].append(
            {"criterion_id": "NEW-1", "how_verified": "A criterion the plan never had"})
    elif scenario == "unverified_cause":
        spec["root_causes"][0]["evidence"] = [
            {"source": "test_output", "quote": "a line that is nowhere in the evidence at all"}]
    elif scenario == "protected_scope":
        spec["change_scope"] = [".github/workflows/ci.yml"]
    elif scenario == "reanalysis":
        spec["needs_reanalysis"] = True
        spec["reanalysis_reason"] = "The design assumed the wrong module boundary."
    elif scenario == "edit_criteria":
        extra["acceptance_changes"] = ["Drop the failing criterion"]
    elif scenario == "no_cause":
        spec["root_causes"] = []
    message = "I read the failure evidence and wrote a repair." + (
        "" if spec["decision"] == "repair" else " I could not establish a cause.")
    answer = DeliveryPMRepair.model_validate({"message": message, "spec": spec}).model_dump()
    answer["spec"].update(extra)
    return answer
