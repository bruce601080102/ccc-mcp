import json

from ..errors import BridgeError
from ..models import AgentReport
from .base import BaseAgentAdapter, parse_report, probe, run_process


class ClaudeCodeAdapter(BaseAgentAdapter):
    def run(self, package, instruction, cwd, timeout, cancelled):
        if package["role"] not in {"SD", "RD", "SD/RD"}:
            raise BridgeError("Claude adapter requires an implementation role")
        executable = probe(
            "claude",
            [],
            [
                "--print",
                "--output-format",
                "--json-schema",
                "--tools",
                "--permission-mode",
                "--bare",
            ],
        )
        # No shell, MCP, network or task tools. Tests are a separately authorized host operation.
        argv = [
            executable,
            "--bare",
            "--print",
            "--no-session-persistence",
            "--output-format",
            "json",
            "--json-schema",
            json.dumps(AgentReport.model_json_schema()),
            "--permission-mode",
            "acceptEdits",
            "--tools",
            "Read,Edit,Write,Glob,Grep",
        ]
        stdout, _ = run_process(argv, cwd, self.prompt(package, instruction), timeout, cancelled)
        try:
            envelope = json.loads(stdout)
            if envelope.get("is_error"):
                raise BridgeError("Claude CLI reported an execution error")
            structured = envelope.get("structured_output")
            return parse_report(
                json.dumps(structured) if structured is not None else envelope.get("result", "")
            )
        except (ValueError, AttributeError):
            raise BridgeError("Claude produced invalid JSON output") from None
