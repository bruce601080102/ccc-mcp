import platform
import tempfile
from pathlib import Path

from ..errors import BridgeError
from .base import BaseAgentAdapter, parse_report, probe, run_process, schema_file


class CodexAdapter(BaseAgentAdapter):
    def run(self, package, instruction, cwd, timeout, cancelled):
        if package["role"] not in {"SA", "REVIEW"}:
            raise BridgeError("Codex adapter is restricted to read-only SA/review")
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
        with tempfile.TemporaryDirectory(prefix="agentbridge-codex-") as folder:
            output = Path(folder) / "report.json"
            schema = schema_file(folder)
            argv = [
                executable,
                "-a",
                "never",
                "exec",
                "--ignore-user-config",
                *(["-c", 'windows.sandbox="elevated"'] if platform.system() == "Windows" else []),
                "--skip-git-repo-check",
                "--sandbox",
                "read-only",
                "--json",
                "--output-schema",
                str(schema),
                "--output-last-message",
                str(output),
                "--cd",
                str(cwd),
                "-",
            ]
            prompt = self.prompt(package, instruction) + (
                "\nUse permitted read-only shell commands to inspect repository source. "
                "If an external skill file or graph tool is unavailable or denied, record that "
                "limitation and continue independent permitted repository reads. Do not retry "
                "denied paths, change permissions, request escalation, or claim graph validation "
                "without evidence. A failed external read does not prove repository reads are "
                "unavailable. Cite actual source paths and line numbers in your findings."
            )
            _, _ = run_process(argv, cwd, prompt, timeout, cancelled)
            if not output.is_file():
                raise BridgeError("Codex produced no final structured report")
            return parse_report(output.read_text(encoding="utf-8"))
