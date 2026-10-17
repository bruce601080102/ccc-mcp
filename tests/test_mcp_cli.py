import asyncio
import json
import os
import sys

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from typer.testing import CliRunner

from agentbridge_mcp.cli import app
from agentbridge_mcp.errors import BridgeError
from agentbridge_mcp.server import JWTVerifier, create_server


def test_cli_workflow(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    runner = CliRunner()
    args = ["--home", str(tmp_path / "home")]
    init = runner.invoke(app, args + ["init", str(repo), "--id", "demo"])
    assert init.exit_code == 0, init.output
    commands = args + ["--project", "demo"]
    for command in [
        ["context", "show"],
        ["context", "history"],
        ["context", "validate"],
        ["context", "export"],
        ["task", "create", "新增 API", "--acceptance", "回傳 200"],
        ["task", "status", "TASK-0001"],
        ["task", "show", "TASK-0001"],
        ["task", "show"],
        ["task", "list"],
        ["doctor"],
    ]:
        result = runner.invoke(app, commands + command)
        assert result.exit_code == 0, (command, result.output, result.exception)
    payload = {
        "base_context_version": 1,
        "changes": {"summary": "new summary"},
        "update_reason": "CLI fallback",
        "semantic_review_ack": True,
    }
    patch = tmp_path / "patch.json"
    patch.write_text(json.dumps(payload), encoding="utf-8")
    imported = runner.invoke(app, commands + ["context", "import-patch", str(patch)])
    assert imported.exit_code == 0, imported.output
    assert json.loads(imported.output)["context_version"] == 2


def test_task_show_infers_project_but_refuses_ambiguous_task(bridge, monkeypatch):
    from conftest import dispatch

    from agentbridge_mcp.worker import run_execution

    bridge.create_task("demo", "Analyze repository")
    execution = dispatch(bridge)
    run_execution(bridge, execution["execution_id"])
    monkeypatch.chdir(bridge.project("demo")["root"])
    runner = CliRunner()
    args = ["--home", str(bridge.home), "task", "show"]
    before = bridge.get_task_result("demo", "TASK-0001")
    shown = runner.invoke(app, args)
    assert shown.exit_code == 0, shown.output
    for expected in ("demo", "TASK-0001", execution["execution_id"], "codex", "SA", "fake"):
        assert expected in shown.output
    assert bridge.get_task_result("demo", "TASK-0001") == before

    bridge.create_task("demo", "Another task")
    ambiguous = runner.invoke(app, args)
    assert ambiguous.exit_code != 0
    assert "task list" in ambiguous.output
    explicit = runner.invoke(app, args + ["TASK-0001"])
    assert explicit.exit_code == 0, explicit.output
    assert execution["execution_id"] in explicit.output


async def test_mcp_tool_schemas_and_no_fake_human_approval(bridge):
    server = create_server(bridge.home)
    tools = await server.list_tools()
    names = {tool.name for tool in tools}
    required = {
        "bootstrap_project",
        "get_context",
        "propose_context_patch",
        "commit_context_patch",
        "get_context_history",
        "build_handoff",
        "list_projects",
        "dispatch_agent",
        "get_task_status",
        "get_task_result",
        "propose_plan",
        "review_changes",
        "request_approval",
        "get_approval_status",
    }
    assert required <= names
    assert "approve" not in names and "register_project" not in names
    read_only = create_server(bridge.home, http=True)
    readonly_names = {t.name for t in await read_only.list_tools()}
    assert "dispatch_agent" not in readonly_names
    assert {"read_workspace", "list_workspace", "get_project_settings"} <= readonly_names
    assert not {"write_task_file", "submit_agent_report", "create_managed_project"} & readonly_names
    assert "bootstrap_project" in readonly_names


async def test_stdio_protocol_initialize_list_and_call(bridge):
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "agentbridge_mcp", "--home", str(bridge.home), "serve"],
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    async with stdio_client(parameters) as (reader, writer):
        async with ClientSession(reader, writer, read_timeout_seconds=10) as session:
            initialized = await session.initialize()
            assert initialized.server_info.name == "AgentBridge"
            assert len((await session.list_tools()).tools) >= 14
            result = await session.call_tool("bootstrap_project", {"project_id": "demo"})
            assert not result.is_error
            assert result.structured_content["context_version"] == 1
            forbidden = await session.call_tool(
                "get_source_reference", {"project_id": "demo", "path": "../secret"}
            )
            assert forbidden.is_error


async def test_chatgpt_discover_then_list_and_write_without_initialize(bridge, human):
    """Replay the modern discovery request that ChatGPT sends when refreshing tools."""
    bridge.authorize_project_local("demo")
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "agentbridge_mcp",
        "--home",
        str(bridge.home),
        "serve",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )

    async def exchange(method, params=None):
        request = {
            "jsonrpc": "2.0",
            "id": method,
            "method": method,
            "params": {
                **(params or {}),
                "_meta": {
                    "io.modelcontextprotocol/protocolVersion": "2026-07-28",
                    "io.modelcontextprotocol/clientInfo": {"name": "chatgpt-test", "version": "1"},
                    "io.modelcontextprotocol/clientCapabilities": {},
                },
            },
        }
        process.stdin.write((json.dumps(request) + "\n").encode())
        await process.stdin.drain()
        line = await asyncio.wait_for(process.stdout.readline(), timeout=15)
        assert line, "MCP server exited without a JSON-RPC response"
        response = json.loads(line)
        assert response["id"] == method
        assert "error" not in response, response
        return response["result"]

    try:
        discovered = await exchange("server/discover")
        assert "2026-07-28" in discovered["supportedVersions"]
        assert "tools" in discovered["capabilities"]
        assert discovered["_meta"]["io.modelcontextprotocol/serverInfo"]["name"] == "AgentBridge"
        listing = await exchange("tools/list")
        write_tool = next(t for t in listing["tools"] if t["name"] == "write_workspace")
        assert write_tool["annotations"]["readOnlyHint"] is False
        assert {"path", "content", "expected_sha256"} <= write_tool["inputSchema"][
            "properties"
        ].keys()
        read = await exchange(
            "tools/call",
            {"name": "read_workspace", "arguments": {"project_id": "demo", "path": "app.py"}},
        )
        original = read["structuredContent"]
        written = await exchange(
            "tools/call",
            {
                "name": "write_workspace",
                "arguments": {
                    "project_id": "demo",
                    "path": "app.py",
                    "content": original["content"] + "\n# discovery regression\n",
                    "expected_sha256": original["sha256"],
                },
            },
        )
        assert not written["isError"]
        assert written["structuredContent"]["status"] == "APPLIED"
        confirmed = bridge.read_workspace("demo", "app.py")
        assert confirmed["content"].endswith("# discovery regression")
        assert confirmed["sha256"] == written["structuredContent"]["sha256"]
        assert bridge.list_tasks("demo") == []
    finally:
        process.stdin.close()
        try:
            await asyncio.wait_for(process.wait(), timeout=5)
        except TimeoutError:
            process.kill()
            await process.wait()
        errors = (await process.stderr.read()).decode(errors="replace")
    assert process.returncode == 0, errors
    assert "Failed to validate request" not in errors


async def test_stdio_chatgpt_role_roundtrip_without_native_cli(bridge):
    bridge.configure_role("demo", "SA", "chatgpt")
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "agentbridge_mcp", "--home", str(bridge.home), "serve"],
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    async with stdio_client(parameters) as (reader, writer):
        async with ClientSession(reader, writer, read_timeout_seconds=15) as session:
            await session.initialize()
            created = await session.call_tool(
                "create_task",
                {"project_id": "demo", "requirement": "Analyze source", "acceptance": []},
            )
            assert not created.is_error
            queued = await session.call_tool(
                "dispatch_agent",
                {
                    "project_id": "demo",
                    "task_id": "TASK-0001",
                    "role": "SA",
                    "context_version": 1,
                    "instruction": "Read app.py",
                },
            )
            assert not queued.is_error
            execution_id = queued.structured_content["execution_id"]
            source = await session.call_tool(
                "read_workspace", {"project_id": "demo", "path": "app.py"}
            )
            assert (
                not source.is_error and "original comment" in source.structured_content["content"]
            )
            submitted = await session.call_tool(
                "submit_agent_report",
                {
                    "project_id": "demo",
                    "execution_id": execution_id,
                    "report": {
                        "summary": "Read app.py",
                        "next_action": "PM refine",
                        "context_updates": ["Observed value function"],
                    },
                },
            )
            assert not submitted.is_error
    assert bridge.task("demo", "TASK-0001")["state"] == "AWAITING_PM_DECISION"


def test_http_auth_required_for_remote_and_writes(bridge):
    with pytest.raises(BridgeError, match="OAuth"):
        create_server(bridge.home, http=True, host="0.0.0.0")
    with pytest.raises(BridgeError, match="OAuth"):
        create_server(bridge.home, http=True, enable_writes=True)
    with pytest.raises(BridgeError, match="HTTPS"):
        JWTVerifier("http://issuer", "https://example.com/mcp", "https://example.com/jwks")


async def test_jwt_verifier_rejects_unverified_tokens(monkeypatch):
    verifier = JWTVerifier(
        "https://issuer.invalid", "https://bridge.invalid/mcp", "https://issuer.invalid/jwks"
    )
    import jwt

    monkeypatch.setattr(
        verifier, "decode", lambda token: (_ for _ in ()).throw(jwt.InvalidSignatureError())
    )
    assert await verifier.verify_token("forged") is None
