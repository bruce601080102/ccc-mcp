import platform
import tempfile
from pathlib import Path

from ...infra.errors import BridgeError
from ...infra.telemetry import record_output
from .base import REPORT_MODELS, BaseAgentAdapter, parse_report, probe, run_process, schema_file


class CodexAdapter(BaseAgentAdapter):
    # Appended to the shared operator policy for every Codex dispatch. A class constant
    # (not built inline in run()) so `config prompt`/preview_role_prompt can show exactly
    # this text without actually dispatching Codex.
    EXTRA_INSTRUCTION = (
        "\nUse permitted shell and file tools for the assigned role; SA/REVIEW is read-only. "
        "If an external skill file or graph tool is unavailable or denied, record that "
        "limitation and continue independent permitted repository reads. Do not retry "
        "denied paths, change permissions, request escalation, or claim graph validation "
        "without evidence. A failed external read does not prove repository reads are "
        "unavailable. Cite actual source paths and line numbers in your findings."
    )

    def run(self, package, instruction, cwd, timeout, cancelled):
        if package["role"] not in {"SA", "REVIEW", "SD", "RD", "SD/RD"}:
            raise BridgeError("Unsupported Codex role")
        report_schema = package.get("report_schema", "agent_report")
        model = REPORT_MODELS[report_schema]
        # Requesting anything other than the normal AgentReport schema is itself the
        # read-only signal (Meeting Mode's clarification-ask, role SD/RD, and
        # clarification-answer, role SA which is already read-only anyway).
        readonly = package["role"] in {"SA", "REVIEW"} or report_schema != "agent_report"
        options = package.get("agent_settings", {})
        executable = probe(
            "codex",
            ["exec"],
            [
                "--sandbox",
                "--json",
                "--output-schema",
                "--output-last-message",
                "--ignore-user-config",
                "--skip-git-repo-check",
            ],
        )
        with tempfile.TemporaryDirectory(prefix="ccc-mcp-codex-") as folder:
            output = Path(folder) / "report.json"
            schema = schema_file(folder, model)
            argv = [
                executable,
                "-a",
                "never",
                "exec",
                "--ignore-user-config",
                *(["-c", 'windows.sandbox="elevated"'] if platform.system() == "Windows" else []),
                "--skip-git-repo-check",
                "--sandbox",
                "read-only" if readonly else "workspace-write",
                "--json",
                "--output-schema",
                str(schema),
                "--output-last-message",
                str(output),
                "--cd",
                str(cwd),
                "-",
            ]
            if options.get("model"):
                argv[4:4] = ["--model", options["model"]]
            if options.get("effort"):
                argv[4:4] = ["-c", "model_reasoning_effort=" + options["effort"]]
            prompt = self.prompt(package, instruction) + self.EXTRA_INSTRUCTION
            stdout, stderr = run_process(argv, cwd, prompt, timeout, cancelled)
            record_output("codex", stdout, stderr)
            if not output.is_file():
                raise BridgeError("Codex produced no final structured report")
            return parse_report(output.read_text(encoding="utf-8"), model)
