"""ChatGPT cannot be handed an MCP config like Claude/Codex, so ccc-mcp calls the
operator-installed local MCP servers on its behalf (list/call_installed_mcp_tool).
Only server+tool pairs the local CLI enabled may run; nothing the caller sends widens that.
"""

import json
import os
import sys
import textwrap

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from ccc_mcp.adapters import mcp_servers
from ccc_mcp.infra.errors import BridgeError, PermissionDenied
from ccc_mcp.interfaces.server import create_server

FAKE = textwrap.dedent(
    """
    from mcp.server import MCPServer

    server = MCPServer("fakemem")

    @server.tool()
    def echo(text: str) -> str:
        "Echo the text back."
        return "echo:" + text

    @server.tool()
    def pid() -> str:
        "Process id of this server."
        import os
        return str(os.getpid())

    @server.tool()
    def delete_everything() -> str:
        "Dangerous; must stay disabled unless the operator enables it."
        return "DELETED"

    server.run(transport="stdio")
    """
)


@pytest.fixture(autouse=True)
def close_pool():
    yield
    mcp_servers.POOL.close_all()


@pytest.fixture
def installed(bridge, tmp_path):
    script = tmp_path / "fake_server.py"
    script.write_text(FAKE, encoding="utf-8")
    bridge.install_mcp_server(
        "demo", "fakemem", command=sys.executable, args=[str(script)], tools=["echo", "pid"]
    )
    return script


def test_lists_only_the_enabled_tools_with_their_schemas(bridge, installed):
    tools = bridge.installed_mcp_tools("demo")["fakemem"]
    assert [t["name"] for t in tools] == ["echo", "pid"]
    assert tools[0]["input_schema"]["properties"]["text"]["type"] == "string"
    assert "Echo" in tools[0]["description"]


def test_calls_an_enabled_tool_and_passes_arguments(bridge, installed):
    out = bridge.call_installed_mcp_tool("demo", "fakemem", "echo", {"text": "hi"})
    assert out["is_error"] is False and out["text"] == "echo:hi"
    assert out["server"] == "fakemem" and out["tool"] == "echo"


def test_a_tool_the_operator_did_not_enable_is_refused_even_though_it_exists(bridge, installed):
    with pytest.raises(PermissionDenied, match="not enabled"):
        bridge.call_installed_mcp_tool("demo", "fakemem", "delete_everything", {})


def test_unknown_server_and_bad_arguments_are_refused(bridge, installed):
    with pytest.raises(BridgeError, match="not installed"):
        bridge.call_installed_mcp_tool("demo", "other", "echo", {})
    with pytest.raises(BridgeError, match="must be an object"):
        bridge.call_installed_mcp_tool("demo", "fakemem", "echo", ["x"])


def test_a_server_that_was_never_installed_cannot_be_reached_through_arguments(bridge):
    """No install, no way in: arguments never carry a command."""
    with pytest.raises(BridgeError, match="not installed"):
        bridge.call_installed_mcp_tool("demo", "/bin/sh", "echo", {"command": "id"})


def test_a_vanished_command_is_reported_not_silently_skipped(bridge, installed, monkeypatch):
    import pathlib

    original = pathlib.Path.is_file
    monkeypatch.setattr(
        pathlib.Path, "is_file", lambda self: False if self == pathlib.Path(sys.executable) else original(self)
    )
    with pytest.raises(BridgeError, match="command is gone"):
        bridge.call_installed_mcp_tool("demo", "fakemem", "echo", {"text": "x"})


async def test_tool_exposure_follows_the_write_gate(bridge):
    writable = {t.name for t in await create_server(bridge.home).list_tools()}
    assert {
        "list_installed_mcp_tools",
        "call_installed_mcp_tool",
        "submit_clarification_request",
        "submit_clarification_response",
    } <= writable
    readonly = {t.name for t in await create_server(bridge.home, http=True).list_tools()}
    assert "list_installed_mcp_tools" in readonly
    assert not {
        "call_installed_mcp_tool",
        "submit_clarification_request",
        "submit_clarification_response",
    } & readonly


async def test_over_the_real_mcp_protocol(bridge, installed):
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "ccc_mcp", "--home", str(bridge.home), "serve"],
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    async with stdio_client(parameters) as (reader, writer):
        async with ClientSession(reader, writer, read_timeout_seconds=60) as session:
            await session.initialize()
            listed = await session.call_tool("list_installed_mcp_tools", {"project_id": "demo"})
            assert not listed.is_error
            assert listed.structured_content["fakemem"][0]["name"] == "echo"
            called = await session.call_tool(
                "call_installed_mcp_tool",
                {"project_id": "demo", "mcp_server": "fakemem", "tool": "echo",
                 "arguments": {"text": "via-protocol"}},
            )
            assert not called.is_error
            assert called.structured_content["text"] == "echo:via-protocol"
            refused = await session.call_tool(
                "call_installed_mcp_tool",
                {"project_id": "demo", "mcp_server": "fakemem", "tool": "delete_everything"},
            )
            assert refused.is_error


def server_pid(bridge, project="demo"):
    return bridge.call_installed_mcp_tool(project, "fakemem", "pid", {})["text"]


def test_the_server_stays_running_between_calls(bridge, installed):
    first = server_pid(bridge)
    assert server_pid(bridge) == first
    assert bridge.call_installed_mcp_tool("demo", "fakemem", "echo", {"text": "x"})["text"] == "echo:x"
    # listing reuses the same process too
    bridge.installed_mcp_tools("demo")
    assert server_pid(bridge) == first


def test_each_scope_gets_its_own_server_process(bridge, installed):
    command, args = sys.executable, [str(installed)]
    one = mcp_servers.call_tool("project-a", command, args, "pid", {})["text"]
    two = mcp_servers.call_tool("project-b", command, args, "pid", {})["text"]
    assert one != two


def test_a_killed_server_is_restarted_transparently(bridge, installed):
    import signal

    first = int(server_pid(bridge))
    os.kill(first, signal.SIGKILL)
    for _ in range(40):  # wait until the connection notices
        try:
            os.kill(first, 0)
        except OSError:
            break
        import time

        time.sleep(0.05)
    second = server_pid(bridge)
    assert second != str(first)


def test_close_all_stops_the_server_processes(bridge, installed):
    pid = int(server_pid(bridge))
    mcp_servers.POOL.close_all()
    import time

    for _ in range(60):
        try:
            os.kill(pid, 0)
        except OSError:
            return
        time.sleep(0.1)
    pytest.fail("the pooled server process was not stopped")


def test_every_bridge_call_leaves_evidence_without_the_result(bridge, installed):
    bridge.call_installed_mcp_tool("demo", "fakemem", "echo", {"text": "secret-looking-result"})
    with pytest.raises(PermissionDenied):
        bridge.call_installed_mcp_tool("demo", "fakemem", "delete_everything", {})
    log = bridge.mcp_call_log()
    assert [(e["tool"], e["is_error"]) for e in log] == [("echo", False)]  # refused ones never ran
    assert log[0]["project"] == "demo" and log[0]["server"] == "fakemem"
    assert "echo:" not in json.dumps(log)  # arguments are summarized, results never stored
    assert log[0]["seconds"] >= 0


def test_the_cli_shows_the_call_log(bridge, installed):
    from typer.testing import CliRunner

    from ccc_mcp.interfaces.cli import app

    bridge.call_installed_mcp_tool("demo", "fakemem", "echo", {"text": "x"})
    result = CliRunner().invoke(app, ["--home", str(bridge.home), "mcp", "calls"])
    assert result.exit_code == 0 and json.loads(result.output)[0]["tool"] == "echo"
