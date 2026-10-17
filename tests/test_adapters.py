import json
import subprocess
import time
from pathlib import Path

import pytest
from conftest import approve, dispatch

from agentbridge_mcp.agents import ClaudeCodeAdapter, CodexAdapter
from agentbridge_mcp.agents.base import schema_file
from agentbridge_mcp.errors import AgentProcessError, BridgeError
from agentbridge_mcp.git import git
from agentbridge_mcp.models import AgentReport
from agentbridge_mcp.worker import run_execution


def report():
    return AgentReport(summary="stubbed CLI", next_action="Review").model_dump_json()


@pytest.mark.parametrize("role", ["SA", "REVIEW"])
@pytest.mark.parametrize("system", ["Windows", "Linux"])
def test_codex_adapter_uses_readonly_structured_stdin_outside_git(
    monkeypatch, tmp_path, role, system
):
    def probe(executable, args, required):
        assert "--skip-git-repo-check" in required
        return "codex.exe"

    monkeypatch.setattr("agentbridge_mcp.agents.codex.probe", probe)
    monkeypatch.setattr("agentbridge_mcp.agents.codex.platform.system", lambda: system)

    def run(argv, cwd, prompt, timeout, cancelled):
        assert argv[argv.index("--sandbox") + 1] == "read-only"
        assert argv[argv.index("-a") + 1] == "never"
        assert "--ignore-user-config" in argv
        if system == "Windows":
            assert argv[argv.index("-c") + 1] == 'windows.sandbox="elevated"'
        else:
            assert "-c" not in argv
        assert "--skip-git-repo-check" in argv
        assert cwd == tmp_path
        assert argv[argv.index("--cd") + 1] == str(tmp_path)
        assert not (cwd / ".git").exists()
        assert argv[-1] == "-"
        assert "context_version" in prompt
        assert "continue independent permitted repository reads" in prompt
        assert "Do not retry denied paths" in prompt
        Path(argv[argv.index("--output-last-message") + 1]).write_text(report(), encoding="utf-8")
        return "", ""

    monkeypatch.setattr("agentbridge_mcp.agents.codex.run_process", run)
    assert (
        CodexAdapter().run(
            {"role": role, "context_version": 1}, "Analyze", tmp_path, 10, lambda: False
        )["summary"]
        == "stubbed CLI"
    )
    with pytest.raises(BridgeError):
        CodexAdapter().run({"role": "UNKNOWN"}, "", Path.cwd(), 10, lambda: False)


def test_claude_adapter_disables_shell_and_repository_configuration(monkeypatch):
    monkeypatch.setattr("agentbridge_mcp.agents.claude.probe", lambda *args: "claude.exe")

    def run(argv, cwd, prompt, timeout, cancelled):
        assert "--safe-mode" in argv
        assert "--bare" not in argv
        assert argv[argv.index("--setting-sources") + 1] == ""
        assert "--strict-mcp-config" in argv
        assert json.loads(argv[argv.index("--mcp-config") + 1]) == {"mcpServers": {}}
        assert "--no-session-persistence" in argv
        assert "Bash" not in argv[argv.index("--tools") + 1]
        assert "--dangerously-skip-permissions" not in argv
        return json.dumps({"structured_output": json.loads(report())}), ""

    monkeypatch.setattr("agentbridge_mcp.agents.claude.run_process", run)
    result = ClaudeCodeAdapter().run({"role": "RD"}, "Implement", Path.cwd(), 10, lambda: False)
    assert result["summary"] == "stubbed CLI"


@pytest.mark.parametrize(
    "stdout, stderr, expected",
    [
        (json.dumps({"is_error": True, "result": "Not logged in"}), "", "Not logged in"),
        (json.dumps({"errors": ["Authentication unavailable"]}), "", "Authentication unavailable"),
        ("Not logged in", "", "Not logged in"),
        ("", "Invalid flag", "Invalid flag"),
        ("", "", "CLI returned no diagnostic text"),
    ],
)
def test_claude_preserves_stdout_failure_diagnostics(monkeypatch, stdout, stderr, expected):
    monkeypatch.setattr("agentbridge_mcp.agents.claude.probe", lambda *args: "claude.exe")

    def fail(*args):
        raise AgentProcessError(1, stdout, stderr)

    monkeypatch.setattr("agentbridge_mcp.agents.claude.run_process", fail)
    with pytest.raises(BridgeError, match=expected):
        ClaudeCodeAdapter().run({"role": "RD"}, "Implement", Path.cwd(), 10, lambda: False)


def test_claude_stdout_failure_diagnostics_redact_secrets(monkeypatch):
    monkeypatch.setattr("agentbridge_mcp.agents.claude.probe", lambda *args: "claude.exe")
    secret = "sk-" + "x" * 32

    def fail(*args):
        raise AgentProcessError(
            1, json.dumps({"is_error": True, "result": "Bad key " + secret}), ""
        )

    monkeypatch.setattr("agentbridge_mcp.agents.claude.run_process", fail)
    with pytest.raises(BridgeError) as caught:
        ClaudeCodeAdapter().run({"role": "RD"}, "Implement", Path.cwd(), 10, lambda: False)
    assert "Bad key" in str(caught.value)
    assert secret not in str(caught.value)


def test_codex_schema_is_strict(tmp_path):
    schema = json.loads(schema_file(tmp_path).read_text())
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])


def test_comment_change_blocks_and_does_not_touch_main(bridge, human, monkeypatch):
    bridge.create_task("demo", "Make a change")
    approve(bridge)

    def corrupt(self, package, instruction, cwd, timeout, cancelled):
        (cwd / "app.py").write_text("# changed comment\ndef value():\n    return 2\n")
        return json.loads(report())

    monkeypatch.setattr("agentbridge_mcp.agents.fake.FakeAgentAdapter.run", corrupt)
    queued = dispatch(bridge, "RD")
    run_execution(bridge, queued["execution_id"])
    assert bridge.task("demo", "TASK-0001")["state"] == "BLOCKED"
    result = bridge.get_task_result("demo", "TASK-0001")[-1]["result"]
    assert result["comment_check"]["violations"] == ["app.py"]
    assert "# original comment" in (Path(bridge.project("demo")["root"]) / "app.py").read_text()
    again = dispatch(bridge, "RD")
    run_execution(bridge, again["execution_id"])
    assert bridge.task("demo", "TASK-0001")["state"] == "BLOCKED"


def test_windows_git_checkout_line_endings_do_not_block_implementation(bridge, human, monkeypatch):
    root = Path(bridge.project("demo")["root"])
    git(root, "config", "core.autocrlf", "true")
    (root / "README.md").write_bytes(b"# Project\n")
    git(root, "add", "README.md")
    git(
        root,
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.invalid",
        "commit",
        "-m",
        "Add text baseline",
    )
    bridge.create_task("demo", "Check checkout line endings")
    approve(bridge)
    monkeypatch.setattr(
        "agentbridge_mcp.agents.fake.FakeAgentAdapter.run",
        lambda *args: json.loads(report()),
    )
    queued = dispatch(bridge, "RD")
    run_execution(bridge, queued["execution_id"])
    execution = bridge.get_task_result("demo", "TASK-0001")[-1]
    assert (Path(execution["worktree"]) / "README.md").read_bytes() == b"# Project\r\n"
    assert execution["result"]["comment_check"]["ok"]
    assert execution["result"]["changed_files"] == []
    assert "README.md" in execution["result"]["line_ending_only_files"]
    assert bridge.task("demo", "TASK-0001")["state"] == "AWAITING_PM_REVIEW"


def test_live_streamable_http_transport(bridge):
    import asyncio
    import socket
    import sys

    import httpx
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "agentbridge_mcp",
            "--home",
            str(bridge.home),
            "serve",
            "--transport",
            "streamable-http",
            "--port",
            str(port),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    url = f"http://127.0.0.1:{port}/mcp"
    try:
        deadline = time.monotonic() + 10
        while True:
            try:
                response = httpx.post(url, timeout=1)
                if response.status_code:
                    break
            except httpx.TransportError:
                if time.monotonic() > deadline:
                    raise
                time.sleep(0.1)

        async def connect():
            async with streamable_http_client(url) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    names = {t.name for t in (await session.list_tools()).tools}
                    assert "dispatch_agent" not in names
                    boot = await session.call_tool("bootstrap_project", {"project_id": "demo"})
                    assert boot.structuredContent["context_version"] == 1

        asyncio.run(connect())
    finally:
        process.terminate()
        process.wait(timeout=10)
