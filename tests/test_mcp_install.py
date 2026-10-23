"""`skill install mcp <name>`: discover/verify a local stdio MCP server and hand it to Claude.

A tiny real stdio MCP server (this repo's own SDK) stands in for codebase-memory-mcp, so
the handshake, tool listing and adapter wiring are exercised for real without that tool.
"""

import json
import sys
import textwrap

import pytest
from typer.testing import CliRunner

from ccc_mcp.adapters import mcp_servers
from ccc_mcp.adapters.agents.claude import ClaudeCodeAdapter
from ccc_mcp.infra.errors import BridgeError
from ccc_mcp.interfaces.cli import app

FAKE_SERVER = textwrap.dedent(
    """
    from mcp.server import MCPServer
    from mcp_types import ToolAnnotations

    server = MCPServer("fakemem")

    @server.tool(annotations=ToolAnnotations(read_only_hint=True))
    def list_projects() -> list[str]:
        "List projects."
        return ["demo"]

    @server.tool()
    def delete_project(name: str) -> str:
        "Delete a project."
        return "deleted"

    server.run(transport="stdio")
    """
)


@pytest.fixture
def fake_server(tmp_path):
    script = tmp_path / "fake_mcp_server.py"
    script.write_text(FAKE_SERVER, encoding="utf-8")
    return str(script)


def test_probe_completes_a_real_handshake_and_reports_read_only_hints(fake_server):
    tools = {t["name"]: t for t in mcp_servers.probe_tools(sys.executable, [fake_server])}
    assert set(tools) == {"list_projects", "delete_project"}
    assert tools["list_projects"]["read_only"] is True
    assert tools["delete_project"]["read_only"] is False


def test_probe_rejects_a_command_that_is_not_an_mcp_server():
    with pytest.raises(BridgeError, match="not a working stdio MCP server"):
        mcp_servers.probe_tools(sys.executable, ["-c", "print('hello')"], timeout=10)


def test_resolve_reports_a_missing_command_with_the_conda_hint(monkeypatch, tmp_path):
    monkeypatch.setattr(mcp_servers, "discover", lambda root=None: [])
    monkeypatch.setattr(mcp_servers.shutil, "which", lambda name: None)
    with pytest.raises(BridgeError, match="conda activate"):
        mcp_servers.resolve_server("nothing-here")
    with pytest.raises(BridgeError, match="does not exist"):
        mcp_servers.resolve_server("x", command=str(tmp_path / "missing"))


def test_resolve_finds_the_mcp_suffixed_command_on_path_and_records_an_absolute_path(
    monkeypatch, tmp_path
):
    binary = tmp_path / "codebase-memory-mcp"
    binary.write_text("#!/bin/sh\n")
    monkeypatch.setattr(mcp_servers, "discover", lambda root=None: [])
    monkeypatch.setattr(
        mcp_servers.shutil, "which", lambda n: str(binary) if n == "codebase-memory-mcp" else None
    )
    found = mcp_servers.resolve_server("codebase-memory")
    assert found["command"] == str(binary)
    assert found["source"] == "PATH"


def test_discover_reads_client_configs_without_copying_env(monkeypatch, tmp_path):
    claude = tmp_path / ".claude.json"
    claude.write_text(
        json.dumps({"mcpServers": {"mem": {"command": "mem-bin", "args": ["--x"], "env": {"K": "secret"}},
                                    "web": {"type": "http", "url": "https://example.invalid/mcp"}}})
    )
    monkeypatch.setattr(mcp_servers.Path, "home", classmethod(lambda cls: tmp_path))
    found = {s["name"]: s for s in mcp_servers.discover()}
    assert found["mem"]["transport"] == "stdio" and found["mem"]["has_env"] is True
    assert "secret" not in json.dumps(found)
    assert found["web"]["transport"] == "http" and found["web"]["command"] is None


def test_install_records_absolute_path_and_roles_and_remove(bridge, fake_server):
    result = bridge.install_mcp_server("demo", "fakemem", command=sys.executable, args=[fake_server])
    assert result["command"] == sys.executable
    assert {t["name"] for t in result["tools"]} == {"list_projects", "delete_project"}
    assert result["roles"]["SD/RD"] == "live"
    assert "not supported" in result["roles"]["SA"]  # default SA is Codex

    stored = bridge.get_project_settings("demo")["mcp_servers"]["fakemem"]
    assert stored["allowed_tools"] == ["list_projects", "delete_project"]
    listed = bridge.list_mcp_servers("demo")
    assert listed["installed"]["fakemem"]["command_exists"] is True

    bridge.remove_mcp_server("demo", "fakemem")
    assert bridge.get_project_settings("demo")["mcp_servers"] == {}
    with pytest.raises(BridgeError, match="not installed"):
        bridge.remove_mcp_server("demo", "fakemem")


def test_install_can_restrict_tools_and_rejects_unknown_ones(bridge, fake_server):
    bridge.install_mcp_server(
        "demo", "fakemem", command=sys.executable, args=[fake_server], tools=["list_projects"]
    )
    assert bridge.get_project_settings("demo")["mcp_servers"]["fakemem"]["allowed_tools"] == [
        "list_projects"
    ]
    with pytest.raises(BridgeError, match="does not expose: nope"):
        bridge.install_mcp_server(
            "demo", "fakemem", command=sys.executable, args=[fake_server], tools=["nope"]
        )


def test_settings_json_in_the_project_is_not_an_import_source_for_mcp(bridge, fake_server):
    """A planted file must never add a command that dispatch would later execute."""
    from pathlib import Path

    bridge.install_mcp_server("demo", "fakemem", command=sys.executable, args=[fake_server])
    settings_file = next(Path(bridge.project("demo")["root"]).glob(".*/settings.json"))
    document = json.loads(settings_file.read_text(encoding="utf-8"))
    document["mcp_servers"] = {
        "evil": {"command": "/bin/sh", "args": ["-c", "id"], "allowed_tools": ["x"]}
    }
    settings_file.write_text(json.dumps(document), encoding="utf-8")
    assert set(bridge.get_project_settings("demo")["mcp_servers"]) == {"fakemem"}


def test_dispatch_package_carries_installed_servers(bridge, human, fake_server):
    bridge.install_mcp_server("demo", "fakemem", command=sys.executable, args=[fake_server])
    bridge.authorize_project_local("demo")
    task_id = bridge.create_task("demo", "Do a thing")["id"]
    dispatched = bridge.dispatch_agent(
        "demo", task_id, "auto", "SA", bridge.latest("demo")["version"], "Analyze", adapter="fake"
    )
    row = bridge.store.one("SELECT package FROM agent_executions WHERE id=?", (dispatched["execution_id"],))
    assert "fakemem" in json.loads(row["package"])["mcp_servers"]


def run_adapter(monkeypatch, package):
    captured = {}

    def fake_run_process(argv, cwd, prompt, timeout, cancelled, *extra):
        captured.update(argv=argv, extra=extra)
        envelope = {"structured_output": {"summary": "s", "next_action": "n"}}
        return json.dumps(envelope), ""

    monkeypatch.setattr("ccc_mcp.adapters.agents.claude.run_process", fake_run_process)
    monkeypatch.setattr("ccc_mcp.adapters.agents.claude.probe", lambda *a, **k: "claude")
    ClaudeCodeAdapter().run(
        {"role": "SD/RD", "report_schema": "agent_report", "agent_settings": {}, **package},
        "do it", ".", 30, lambda: False,
    )
    return captured


def test_claude_without_mcp_keeps_safe_mode_and_an_empty_server_list(monkeypatch):
    argv = run_adapter(monkeypatch, {})["argv"]
    assert "--safe-mode" in argv
    assert argv[argv.index("--mcp-config") + 1] == '{"mcpServers":{}}'
    assert "--allowedTools" not in argv


def test_claude_with_mcp_exposes_only_the_installed_tools(monkeypatch, tmp_path):
    binary = tmp_path / "srv"
    binary.write_text("x")
    captured = run_adapter(
        monkeypatch,
        {"mcp_servers": {"mem": {"command": str(binary), "args": ["--x"], "allowed_tools": ["a", "b"]}}},
    )
    argv = captured["argv"]
    config = json.loads(argv[argv.index("--mcp-config") + 1])
    assert config == {"mcpServers": {"mem": {"command": str(binary), "args": ["--x"]}}}
    assert "--strict-mcp-config" in argv and "--safe-mode" not in argv
    assert "--disable-slash-commands" in argv
    assert argv[argv.index("--allowedTools") + 1] == "mcp__mem__a,mcp__mem__b"
    env = captured["extra"][0]
    assert env["CLAUDE_CODE_DISABLE_AUTO_MEMORY"] == "1" and env["CLAUDE_CODE_DISABLE_CLAUDE_MDS"] == "1"


def test_claude_fails_loudly_when_an_installed_command_disappeared(monkeypatch, tmp_path):
    with pytest.raises(BridgeError, match="command is gone"):
        run_adapter(
            monkeypatch,
            {"mcp_servers": {"mem": {"command": str(tmp_path / "gone"), "args": [], "allowed_tools": ["a"]}}},
        )


def test_cli_install_list_and_remove(bridge, fake_server):
    runner = CliRunner()
    base = ["--home", str(bridge.home), "--project", "demo"]
    installed = runner.invoke(
        app,
        base + ["skill", "install", "mcp", "fakemem", "--command", sys.executable, "--arg", fake_server],
    )
    assert installed.exit_code == 0, installed.output
    assert json.loads(installed.output[installed.output.index("{"):])["name"] == "fakemem"
    listed = runner.invoke(app, base + ["mcp", "list"])
    assert "fakemem" in json.loads(listed.output)["installed"]
    assert runner.invoke(app, base + ["mcp", "remove", "fakemem"]).exit_code == 0
    assert runner.invoke(app, base + ["skill", "install", "mcp"]).exit_code != 0
