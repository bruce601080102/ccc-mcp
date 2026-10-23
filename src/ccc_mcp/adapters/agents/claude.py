import json
import os
from pathlib import Path

from ...infra.errors import AgentProcessError, BridgeError
from ...infra.security import redact
from ...infra.telemetry import record_output
from ..mcp_servers import claude_tool_patterns
from .base import REPORT_MODELS, BaseAgentAdapter, parse_report, probe, run_process


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
        report_schema = package.get("report_schema", "agent_report")
        model = REPORT_MODELS[report_schema]
        # Requesting anything other than the normal AgentReport schema is itself the
        # read-only signal (Meeting Mode's clarification-ask, role SD/RD, and
        # clarification-answer, role SA which is already read-only anyway).
        readonly = package["role"] in {"SA", "REVIEW"} or report_schema != "agent_report"
        options = package.get("agent_settings", {})
        # Only servers an operator installed with the local CLI (`skill install mcp`).
        mcp = package.get("mcp_servers") or {}
        for name, spec in mcp.items():
            if not Path(spec["command"]).is_file():
                raise BridgeError(
                    f"MCP server '{name}' command is gone: {spec['command']}. Re-run "
                    f"`ccc-mcp skill install mcp {name}` in the environment where it works."
                )
        executable = probe(
            "claude",
            [],
            [
                "--print",
                "--output-format",
                "--json-schema",
                "--tools",
                "--permission-mode",
                "--setting-sources",
                "--strict-mcp-config",
                "--mcp-config",
                "--no-session-persistence",
                *(["--disable-slash-commands", "--allowedTools"] if mcp else ["--safe-mode"]),
            ],
        )
        # No shell, network or task tools, and no MCP unless the operator installed some.
        # --safe-mode also switches off every --mcp-config server, so a project with
        # installed MCP runs without it; the surfaces it would have closed are closed
        # individually (settings/hooks via --setting-sources "", skills via
        # --disable-slash-commands, CLAUDE.md and auto-memory via the env vars below).
        argv = [
            executable,
            *([] if mcp else ["--safe-mode"]),
            "--setting-sources",
            "",
            "--strict-mcp-config",
            "--mcp-config",
            json.dumps(
                {
                    "mcpServers": {
                        name: {"command": spec["command"], "args": spec["args"]}
                        for name, spec in mcp.items()
                    }
                },
                separators=(",", ":"),
            ),
            "--print",
            "--no-session-persistence",
            "--output-format",
            "json",
            "--json-schema",
            json.dumps(model.model_json_schema()),
            "--permission-mode",
            "plan" if readonly else "acceptEdits",
            "--tools",
            "Read,Glob,Grep" if readonly else "Read,Edit,Write,Glob,Grep",
        ]
        if mcp:
            argv += ["--disable-slash-commands", "--allowedTools", ",".join(claude_tool_patterns(mcp))]
        if options.get("model"):
            argv += ["--model", options["model"]]
        if options.get("effort"):
            argv += ["--effort", options["effort"]]
        extra = (
            [{**os.environ, "CLAUDE_CODE_DISABLE_AUTO_MEMORY": "1", "CLAUDE_CODE_DISABLE_CLAUDE_MDS": "1"}]
            if mcp
            else []
        )
        try:
            stdout, stderr = run_process(
                argv, cwd, self.prompt(package, instruction), timeout, cancelled, *extra
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
                json.dumps(structured) if structured is not None else envelope.get("result", ""),
                model,
            )
        except (ValueError, AttributeError):
            raise BridgeError("Claude produced invalid JSON output") from None
