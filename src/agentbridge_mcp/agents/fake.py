import time

from ..errors import AgentCancelled, AgentTimeout, BridgeError
from ..models import AgentReport
from .base import BaseAgentAdapter


class FakeAgentAdapter(BaseAgentAdapter):
    """Deterministic simulation; no credentials, subprocess model or code mutation."""

    def __init__(self, scenario="success", delay=0.01):
        self.scenario, self.delay = scenario, delay

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
        revision = self.scenario == "revision"
        return AgentReport(
            summary=f"Simulated {package['role']} for {package['task_id']}",
            findings=["Fake adapter validated the handoff; no actual code analysis performed."],
            risks=["Simulation only; real code and tests require verification."],
            next_action="修正 Review 問題後重新檢查。" if revision else "PM 閱讀結果並決定下一步。",
            needs_revision=revision,
            simulated=True,
        ).model_dump()
