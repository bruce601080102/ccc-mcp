import time

from ...core.models import AgentReport, ClarificationRequest, ClarificationResponse
from ...infra.errors import AgentCancelled, AgentTimeout, BridgeError
from .base import BaseAgentAdapter

# fake_scenario values usable when package["report_schema"] == "clarification_request"
# (Meeting Mode's read-only SD/RD ask) or "clarification_response" (SA's answer).
# Kept separate from the original {"success","revision","error"} enum so a plain
# AgentReport dispatch can never accidentally be scripted as a clarification exchange.
CLARIFICATION_REQUEST_SCENARIOS = {"ask_question", "no_blocking_question"}
CLARIFICATION_RESPONSE_SCENARIOS = {"confirmed", "recommendation", "pm_decision_required"}


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
        revision = self.scenario == "revision"
        return AgentReport(
            summary=f"Simulated {package['role']} for {package['task_id']}",
            findings=["Fake adapter validated the handoff; no actual code analysis performed."],
            risks=["Simulation only; real code and tests require verification."],
            next_action="修正 Review 問題後重新檢查。" if revision else "PM 閱讀結果並決定下一步。",
            needs_revision=revision,
            simulated=True,
        ).model_dump()
