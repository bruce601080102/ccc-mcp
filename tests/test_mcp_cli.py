import json
import os
import sys
from datetime import timedelta

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
    assert "bootstrap_project" in readonly_names


async def test_stdio_protocol_initialize_list_and_call(bridge):
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "agentbridge_mcp", "--home", str(bridge.home), "serve"],
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    async with stdio_client(parameters) as (reader, writer):
        async with ClientSession(
            reader, writer, read_timeout_seconds=timedelta(seconds=10)
        ) as session:
            initialized = await session.initialize()
            assert initialized.serverInfo.name == "AgentBridge"
            assert len((await session.list_tools()).tools) >= 14
            result = await session.call_tool("bootstrap_project", {"project_id": "demo"})
            assert not result.isError
            assert result.structuredContent["context_version"] == 1
            forbidden = await session.call_tool(
                "get_source_reference", {"project_id": "demo", "path": "../secret"}
            )
            assert forbidden.isError


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
