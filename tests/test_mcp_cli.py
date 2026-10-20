import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from typer.testing import CliRunner

from ccc_mcp.infra.errors import BridgeError
from ccc_mcp.interfaces.cli import app
from ccc_mcp.interfaces.server import JWTVerifier, create_server


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


def test_cli_language_defaults_to_zh_tw_and_rejects_unsupported_values(monkeypatch):
    from ccc_mcp.infra.config import cli_language

    monkeypatch.delenv("CCC_MCP_LANG", raising=False)
    assert cli_language() == "zh-TW"
    monkeypatch.setenv("CCC_MCP_LANG", "en")
    assert cli_language() == "en"
    monkeypatch.setenv("CCC_MCP_LANG", "ch")
    assert cli_language() == "ch"
    monkeypatch.setenv("CCC_MCP_LANG", "fr")
    assert cli_language() == "zh-TW"  # unsupported value falls back to the default


@pytest.mark.parametrize(
    "lang,needle",
    [
        ("en", b"Register a project"),
        ("ch", "注册项目".encode()),
        (None, "註冊專案".encode()),  # default, no CCC_MCP_LANG set
    ],
)
def test_cli_help_text_switches_language_with_env_var(lang, needle):
    """Regression: CCC_MCP_LANG must switch the CLI's own --help text (en, ch, or the
    zh-TW default). This is a subprocess test, not CliRunner, because help= strings are
    built once at module import time — a later env var change inside the same process
    can't affect an already-imported Typer app."""
    env = {k: v for k, v in os.environ.items() if k != "CCC_MCP_LANG"}
    if lang is not None:
        env["CCC_MCP_LANG"] = lang
    result = subprocess.run(
        [sys.executable, "-m", "ccc_mcp", "--help"],
        capture_output=True,
        env=env,
        shell=False,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0
    assert needle in result.stdout


def test_task_show_infers_project_but_refuses_ambiguous_task(bridge, monkeypatch):
    from conftest import dispatch

    from ccc_mcp.worker import run_execution

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
        "start_project_request",
        "create_task",
        "advance_project",
        "dispatch_agent",
        "get_task_status",
        "get_task_result",
        "review_changes",
        "get_approval_status",
        "recover_workers",
    }
    assert required <= names
    assert not {"approve", "register_project", "propose_plan", "request_approval"} & names
    read_only = create_server(bridge.home, http=True)
    readonly_names = {t.name for t in await read_only.list_tools()}
    assert "dispatch_agent" not in readonly_names
    assert {"read_workspace", "list_workspace", "get_project_settings"} <= readonly_names
    assert not {"write_task_file", "submit_agent_report", "create_managed_project"} & readonly_names
    assert "bootstrap_project" in readonly_names


async def test_stdio_protocol_initialize_list_and_call(bridge):
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "ccc_mcp", "--home", str(bridge.home), "serve"],
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    async with stdio_client(parameters) as (reader, writer):
        async with ClientSession(reader, writer, read_timeout_seconds=10) as session:
            initialized = await session.initialize()
            assert initialized.server_info.name == "ccc-mcp"
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
    bridge.configure_role("demo", "SA", "chatgpt")
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "ccc_mcp",
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
        assert discovered["_meta"]["io.modelcontextprotocol/serverInfo"]["name"] == "ccc-mcp"
        listing = await exchange("tools/list")
        names = {tool["name"] for tool in listing["tools"]}
        assert {"start_project_request", "create_task"} <= names
        assert "write_workspace" not in names
        read = await exchange(
            "tools/call",
            {"name": "read_workspace", "arguments": {"project_id": "demo", "path": "app.py"}},
        )
        assert "original comment" in read["structuredContent"]["content"]
        started = await exchange(
            "tools/call",
            {
                "name": "create_task",
                "arguments": {
                    "project_id": "demo",
                    "requirement": "Analyze app.py before changing it",
                    "acceptance": [],
                    "task_id": "TASK-legacy-client",
                },
            },
        )
        assert not started["isError"]
        assert started["structuredContent"]["sa_execution"]["status"] == "AWAITING_CHATGPT"
        assert started["structuredContent"]["task_id"] == "TASK-0001"
        assert started["structuredContent"]["requested_task_id_ignored"] == "TASK-legacy-client"
        assert len(bridge.list_tasks("demo")) == 1
        assert bridge.read_workspace("demo", "app.py")["content"] == read["structuredContent"]["content"]
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
    bridge.store.execute(
        "INSERT INTO project_access_events(id,project_id,root,access,identity,created) "
        "VALUES(?,?,?,'read-write',?,?)",
        ("AUTH-test", "demo", bridge.project("demo")["root"], "test-principal", "2026-01-01T00:00:00Z"),
    )
    parameters = StdioServerParameters(
        command=sys.executable,
        args=["-m", "ccc_mcp", "--home", str(bridge.home), "serve"],
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    async with stdio_client(parameters) as (reader, writer):
        async with ClientSession(reader, writer, read_timeout_seconds=15) as session:
            await session.initialize()
            created = await session.call_tool(
                "start_project_request",
                {"project_id": "demo", "requirement": "Analyze source", "acceptance": []},
            )
            assert not created.is_error
            execution_id = created.structured_content["sa_execution"]["execution_id"]
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


def test_server_instructions_tell_pm_about_available_skills(bridge):
    # available_skills rides inside bootstrap_project/get_context payloads, but
    # ChatGPT-as-PM never sees agents/base.py's policy text (that's only sent to
    # real Codex/Claude CLI subprocesses) — without a mention here, PM has no
    # reason to notice or act on the field at all.
    server = create_server(bridge.home)
    assert "available_skills" in server.instructions


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


def test_connect_rejects_invalid_tunnel_id(tmp_path):
    runner = CliRunner()
    result = runner.invoke(app, ["--home", str(tmp_path / "home"), "connect", "bad_id"])
    assert result.exit_code == 1
    assert isinstance(result.exception, BridgeError)
    assert "Usage: ccc-mcp connect" in str(result.exception)


def test_connect_stops_after_failed_admin_auth_check(tmp_path, monkeypatch):
    from ccc_mcp.interfaces import cli as cli_module

    monkeypatch.setenv("CONTROL_PLANE_API_KEY", "secret-key")
    fake_client = tmp_path / "tunnel-client.exe"
    monkeypatch.setattr(cli_module, "ensure_tunnel_client", lambda home: fake_client)

    def unavailable(executable):
        raise BridgeError("native CLI unavailable")

    monkeypatch.setattr(cli_module, "native_executable", unavailable)

    calls = []

    class FakeResult:
        def __init__(self, returncode):
            self.returncode = returncode

    def fake_run(argv, **kwargs):
        calls.append(argv)
        return FakeResult(returncode=1)

    monkeypatch.setattr(cli_module.subprocess, "run", fake_run)

    runner = CliRunner()
    result = runner.invoke(app, ["--home", str(tmp_path / "home"), "connect", "tunnel_" + "a" * 32])

    assert result.exit_code == 1
    assert isinstance(result.exception, BridgeError)
    assert "Authentication/access check failed" in str(result.exception)
    assert len(calls) == 1
    assert calls[0][1:4] == ["admin", "tunnels", "get"]


@pytest.mark.parametrize("failed_stage", ["init", "doctor"])
def test_connect_stops_after_profile_or_doctor_failure(tmp_path, monkeypatch, failed_stage):
    from ccc_mcp.interfaces import cli as cli_module

    monkeypatch.setenv("CONTROL_PLANE_API_KEY", "secret-key")
    monkeypatch.setattr(
        cli_module, "ensure_tunnel_client", lambda home: tmp_path / "tunnel-client.exe"
    )
    monkeypatch.setattr(
        cli_module, "native_executable", lambda name: (_ for _ in ()).throw(BridgeError("missing"))
    )
    calls = []

    def fake_run(argv, **kwargs):
        stage = argv[1]
        calls.append(stage)
        if stage == "init" and failed_stage != "init":
            profile_dir = Path(argv[argv.index("--profile-dir") + 1])
            profile = argv[argv.index("--profile") + 1]
            (profile_dir / f"{profile}.yaml").write_text("test profile", encoding="utf-8")
        return subprocess.CompletedProcess(argv, int(stage == failed_stage))

    monkeypatch.setattr(cli_module.subprocess, "run", fake_run)
    result = CliRunner().invoke(
        app, ["--home", str(tmp_path / "home"), "connect", "tunnel_" + "a" * 32]
    )
    assert result.exit_code == 1
    assert isinstance(result.exception, BridgeError)
    assert calls == (["admin", "init"] if failed_stage == "init" else ["admin", "init", "doctor"])


def test_connect_uses_forward_slash_mcp_command_and_existing_profile(tmp_path, monkeypatch):
    from ccc_mcp.interfaces import cli as cli_module

    monkeypatch.setenv("CONTROL_PLANE_API_KEY", "secret-key")
    monkeypatch.setattr(
        cli_module, "ensure_tunnel_client", lambda home: tmp_path / "tunnel-client.exe"
    )
    monkeypatch.setattr(
        cli_module, "native_executable", lambda name: (_ for _ in ()).throw(BridgeError("missing"))
    )
    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        if argv[1] == "init":
            profile_dir = Path(argv[argv.index("--profile-dir") + 1])
            profile = argv[argv.index("--profile") + 1]
            mcp_command = argv[argv.index("--mcp-command") + 1]
            escaped = mcp_command.replace("\\", "\\\\").replace('"', '\\"')
            (profile_dir / f"{profile}.yaml").write_text(
                f'mcp:\n  commands:\n    - channel: main\n      command: "{escaped}"\n',
                encoding="utf-8",
            )
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(cli_module.subprocess, "run", fake_run)
    args = ["--home", str(tmp_path / "home"), "connect", "tunnel_" + "a" * 32]
    first = CliRunner().invoke(app, args)
    assert first.exit_code == 0, first.output
    assert [call[1] for call in calls] == ["admin", "init", "doctor", "run"]
    command = calls[1][calls[1].index("--mcp-command") + 1]
    assert "\\" not in command
    assert Path(cli_module.sys.executable).absolute().as_posix() in command
    assert (tmp_path / "home").resolve().as_posix() in command

    calls.clear()
    again = CliRunner().invoke(app, args)
    assert again.exit_code == 0, again.output
    # "-c" is the saved profile's interpreter getting re-validated (it's still the
    # same interpreter running this test, so it stays usable and init is skipped).
    assert [call[1] for call in calls] == ["admin", "-c", "doctor", "run"]


def test_connect_mcp_command_keeps_symlinked_interpreter_path(tmp_path, monkeypatch):
    """Regression test for the real bug: `python -m venv` makes bin/python a *symlink*
    to the base interpreter on POSIX (Windows uses a copy instead, which is why this
    was never an issue there). The old code called .resolve() on sys.executable, which
    follows that symlink to the base interpreter -- one with no pyvenv.cfg next to it,
    so it can't see the venv's site-packages and the spawned `-m ccc_mcp` process fails
    with ModuleNotFoundError. The generated --mcp-command must keep the symlink path,
    not the resolved target."""
    from ccc_mcp.interfaces import cli as cli_module

    real_target = tmp_path / "real-python"
    real_target.write_text("#!/bin/sh\n")
    venv_python = tmp_path / "venv-python"
    venv_python.symlink_to(real_target)

    monkeypatch.setenv("CONTROL_PLANE_API_KEY", "secret-key")
    monkeypatch.setattr(cli_module.sys, "executable", str(venv_python))
    monkeypatch.setattr(
        cli_module, "ensure_tunnel_client", lambda home: tmp_path / "tunnel-client.exe"
    )
    monkeypatch.setattr(
        cli_module, "native_executable", lambda name: (_ for _ in ()).throw(BridgeError("missing"))
    )

    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        if argv[1] == "init":
            profile_dir = Path(argv[argv.index("--profile-dir") + 1])
            profile = argv[argv.index("--profile") + 1]
            (profile_dir / f"{profile}.yaml").write_text("test profile", encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(cli_module.subprocess, "run", fake_run)
    result = CliRunner().invoke(
        app, ["--home", str(tmp_path / "home"), "connect", "tunnel_" + "a" * 32]
    )

    assert result.exit_code == 0, result.output
    init_call = next(call for call in calls if call[1] == "init")
    command = init_call[init_call.index("--mcp-command") + 1]
    assert str(venv_python) in command
    assert str(real_target) not in command


def test_connect_recreates_profile_with_unusable_saved_interpreter(tmp_path, monkeypatch):
    """Simulates the real bug: connect was first run from a venv/conda env that no
    longer (or never did) has ccc_mcp installed, so the saved profile's mcp_command
    points to a dead interpreter. connect should detect that and regenerate the
    profile instead of silently reusing it forever."""
    from ccc_mcp.interfaces import cli as cli_module

    monkeypatch.setenv("CONTROL_PLANE_API_KEY", "secret-key")
    monkeypatch.setattr(
        cli_module, "ensure_tunnel_client", lambda home: tmp_path / "tunnel-client.exe"
    )
    monkeypatch.setattr(
        cli_module, "native_executable", lambda name: (_ for _ in ()).throw(BridgeError("missing"))
    )
    home = tmp_path / "home"
    profile_dir = home / "tunnel"
    profile_dir.mkdir(parents=True)
    profile_path = profile_dir / ("ccc-mcp-" + "a" * 32 + ".yaml")
    stale_python = tmp_path / "gone" / "bin" / "python3"
    profile_path.write_text(
        f'mcp:\n  commands:\n    - channel: main\n      command: "\\"{stale_python}\\" -m ccc_mcp serve"\n',
        encoding="utf-8",
    )

    calls = []

    def fake_run(argv, **kwargs):
        calls.append(argv)
        if argv[0] == str(stale_python):
            # Simulates the dead interpreter failing to import ccc_mcp.
            return subprocess.CompletedProcess(argv, 1)
        if argv[1] == "init":
            profile_dir = Path(argv[argv.index("--profile-dir") + 1])
            profile = argv[argv.index("--profile") + 1]
            (profile_dir / f"{profile}.yaml").write_text("regenerated", encoding="utf-8")
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(cli_module.subprocess, "run", fake_run)
    result = CliRunner().invoke(app, ["--home", str(home), "connect", "tunnel_" + "a" * 32])

    assert result.exit_code == 0, result.output
    assert [call[1] for call in calls] == ["admin", "-c", "init", "doctor", "run"]
    assert profile_path.read_text(encoding="utf-8") == "regenerated"
    assert "Recreating it" in result.output


def test_update_invokes_pip_upgrade(tmp_path, monkeypatch):
    from ccc_mcp.interfaces import cli as cli_module

    calls = []

    class FakeResult:
        returncode = 0

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return FakeResult()

    monkeypatch.setattr(cli_module.subprocess, "run", fake_run)

    runner = CliRunner()
    result = runner.invoke(app, ["--home", str(tmp_path / "home"), "update"])

    assert result.exit_code == 0, result.output
    assert calls[0][0] == [cli_module.sys.executable, "-m", "pip", "install", "--upgrade", "ccc-mcp"]


def test_update_dev_installs_editable_from_repo_root(tmp_path, monkeypatch):
    from ccc_mcp.interfaces import cli as cli_module

    calls = []

    class FakeResult:
        returncode = 0

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return FakeResult()

    monkeypatch.setattr(cli_module.subprocess, "run", fake_run)

    runner = CliRunner()
    result = runner.invoke(app, ["--home", str(tmp_path / "home"), "update", "--dev"])

    assert result.exit_code == 0, result.output
    assert calls[0][0] == [cli_module.sys.executable, "-m", "pip", "install", "-e", ".[dev]"]
    assert calls[0][1]["cwd"] is not None


def test_setup_rejects_unsupported_provider():
    result = CliRunner().invoke(app, ["setup", "grok"])
    assert result.exit_code == 1
    assert isinstance(result.exception, BridgeError)
    assert "Unsupported provider" in str(result.exception)


def test_setup_all_runs_both_providers_and_stops_on_first_failure(monkeypatch):
    from ccc_mcp.interfaces import cli as cli_module

    calls = []

    def fake_setup_provider(provider):
        calls.append(provider)
        if provider == "codex":
            return {"provider": "codex", "path": "C:/codex.exe", "logged_in": True}
        raise BridgeError("claude login is still incomplete")

    monkeypatch.setattr(cli_module, "setup_provider", fake_setup_provider)
    result = CliRunner().invoke(app, ["setup", "all"])

    assert calls == ["codex", "claude"]
    assert result.exit_code == 1
    assert isinstance(result.exception, BridgeError)


def test_setup_single_provider_reports_next_action(monkeypatch):
    from ccc_mcp.interfaces import cli as cli_module

    monkeypatch.setattr(
        cli_module,
        "setup_provider",
        lambda provider: {"provider": provider, "path": "C:/codex.exe", "logged_in": True},
    )
    result = CliRunner().invoke(app, ["setup", "codex"])

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["ready"] == [{"provider": "codex", "path": "C:/codex.exe", "logged_in": True}]
    assert "ccc-mcp connect" in payload["next_action"]


def test_skill_install_list_remove_roundtrip(tmp_path):
    source = tmp_path / "my-skill"
    source.mkdir()
    (source / "SKILL.md").write_text(
        "---\nname: my-skill\ndescription: d\n---\nBody.\n", encoding="utf-8"
    )
    runner = CliRunner()
    home_args = ["--home", str(tmp_path / "home")]

    installed = runner.invoke(app, home_args + ["skill", "install", str(source)])
    assert installed.exit_code == 0, installed.output
    assert json.loads(installed.output)["id"] == "my-skill"

    listed = runner.invoke(app, home_args + ["skill", "list"])
    assert listed.exit_code == 0, listed.output
    assert json.loads(listed.output)[0]["id"] == "my-skill"

    removed = runner.invoke(app, home_args + ["skill", "remove", "my-skill"])
    assert removed.exit_code == 0, removed.output
    assert json.loads(removed.output) == {"id": "my-skill", "removed": True}

    listed_after = runner.invoke(app, home_args + ["skill", "list"])
    assert json.loads(listed_after.output) == []


def test_skill_install_from_url_warns_about_trust(tmp_path, monkeypatch):
    from ccc_mcp.interfaces import cli as cli_module

    monkeypatch.setattr(
        cli_module,
        "install_skill",
        lambda home, source: {"id": "x", "name": "x", "description": "", "path": str(home)},
    )
    result = CliRunner().invoke(
        app, ["--home", str(tmp_path / "home"), "skill", "install", "https://example.invalid/s.md"]
    )
    assert result.exit_code == 0, result.output
    assert "信任的來源" in result.output
