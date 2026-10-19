import json

from ...core.models import AgentReport
from ...infra.errors import AgentProcessError, BridgeError
from ...infra.security import redact
from ...infra.telemetry import record_output
from .base import BaseAgentAdapter, parse_report, probe, run_process


def error_detail(stdout):
    try:
        envelope = json.loads(stdout)
    except (ValueError, TypeError):
        return redact(stdout.strip())[:1500] or "CLI returned no diagnostic text"
    if not isinstance(envelope, dict):
        return "CLI returned no diagnostic text"
    errors = envelope.get("errors", [])
    if not isinstance(errors, list):
        errors = [errors]
    messages = [item for item in errors if isinstance(item, str)]
    if isinstance(envelope.get("result"), str):
        messages.append(envelope["result"])
    return redact("; ".join(messages).strip())[:1500] or "CLI returned no diagnostic text"


class ClaudeCodeAdapter(BaseAgentAdapter):
    def run(self, package, instruction, cwd, timeout, cancelled):
        if package["role"] not in {"SA", "REVIEW", "SD", "RD", "SD/RD"}:
            raise BridgeError("Unsupported Claude role")
        readonly = package["role"] in {"SA", "REVIEW"}
        options = package.get("agent_settings", {})
        executable = probe(
            "claude",
            [],
            [
                "--print",
                "--output-format",
                "--json-schema",
                "--tools",
                "--permission-mode",
                "--safe-mode",
                "--setting-sources",
                "--strict-mcp-config",
                "--mcp-config",
                "--no-session-persistence",
            ],
        )
        # No shell, MCP, network or task tools. Tests are a separately authorized host operation.
        argv = [
            executable,
            "--safe-mode",
            "--setting-sources",
            "",
            "--strict-mcp-config",
            "--mcp-config",
            '{"mcpServers":{}}',
            "--print",
            "--no-session-persistence",
            "--output-format",
            "json",
            "--json-schema",
            json.dumps(AgentReport.model_json_schema()),
            "--permission-mode",
            "plan" if readonly else "acceptEdits",
            "--tools",
            "Read,Glob,Grep" if readonly else "Read,Edit,Write,Glob,Grep",
        ]
        if options.get("model"):
            argv += ["--model", options["model"]]
        if options.get("effort"):
            argv += ["--effort", options["effort"]]
        try:
            stdout, stderr = run_process(
                argv, cwd, self.prompt(package, instruction), timeout, cancelled
            )
            record_output("claude", stdout, stderr)
        except AgentProcessError as exc:
            record_output("claude", exc.stdout, exc.stderr)
            detail = redact(exc.stderr.strip())[:1500] or error_detail(exc.stdout)
            raise BridgeError(f"Claude exited {exc.returncode}: {detail}") from None
        try:
            envelope = json.loads(stdout)
            if envelope.get("is_error"):
                raise BridgeError(f"Claude CLI reported an execution error: {error_detail(stdout)}")
            structured = envelope.get("structured_output")
            return parse_report(
                json.dumps(structured) if structured is not None else envelope.get("result", "")
            )
        except (ValueError, AttributeError):
            raise BridgeError("Claude produced invalid JSON output") from None
