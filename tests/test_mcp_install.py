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
    assert result["roles"]["SA"] == "live"  # default SA is Codex; it receives the server too

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


def run_codex_adapter(monkeypatch, tmp_path, package):
    from pathlib import Path

    from ccc_mcp.adapters.agents.codex import CodexAdapter

    captured = {}

    def fake_run_process(argv, cwd, prompt, timeout, cancelled):
        captured["argv"] = argv
        report = json.dumps({"summary": "s", "next_action": "n", "findings": [], "risks": [],
                             "tests": [], "needs_revision": False, "simulated": False,
                             "context_updates": []})
        Path(argv[argv.index("--output-last-message") + 1]).write_text(report, encoding="utf-8")
        return "", ""

    monkeypatch.setattr("ccc_mcp.adapters.agents.codex.run_process", fake_run_process)
    monkeypatch.setattr("ccc_mcp.adapters.agents.codex.probe", lambda *a, **k: "codex")
    CodexAdapter().run({"role": "SA", **package}, "go", tmp_path, 30, lambda: False)
    return captured["argv"]


def test_codex_without_mcp_gets_no_server_overrides(monkeypatch, tmp_path):
    argv = run_codex_adapter(monkeypatch, tmp_path, {})
    assert not any(a.startswith("mcp_servers.") for a in argv)


def test_codex_with_mcp_waits_for_it_auto_approves_and_limits_tools(monkeypatch, tmp_path):
    binary = tmp_path / "srv"
    binary.write_text("x")
    argv = run_codex_adapter(
        monkeypatch,
        tmp_path,
        {"mcp_servers": {"mem": {"command": str(binary), "args": ["--x"], "allowed_tools": ["a", "b"]}}},
    )
    overrides = {a.split("=", 1)[0]: a.split("=", 1)[1] for a in argv if a.startswith("mcp_servers.")}
    assert json.loads(overrides["mcp_servers.mem.command"]) == str(binary)
    assert json.loads(overrides["mcp_servers.mem.args"]) == ["--x"]
    assert json.loads(overrides["mcp_servers.mem.enabled_tools"]) == ["a", "b"]
    # needed in practice: without these Codex starts the turn before a slow server is
    # up, and under `-a never` refuses every MCP call that needs approval.
    assert overrides["mcp_servers.mem.required"] == "true"
    assert json.loads(overrides["mcp_servers.mem.default_tools_approval_mode"]) == "approve"
    assert argv[-1] == "-" and "--ignore-user-config" in argv


def test_codex_fails_loudly_when_an_installed_command_disappeared(monkeypatch, tmp_path):
    with pytest.raises(BridgeError, match="command is gone"):
        run_codex_adapter(
            monkeypatch,
            tmp_path,
            {"mcp_servers": {"mem": {"command": str(tmp_path / "gone"), "args": [], "allowed_tools": ["a"]}}},
        )


def test_no_project_means_global_and_reaches_every_project(bridge, fake_server, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    bridge.register_project(str(other), "other", "second project")
    result = bridge.install_mcp_server(None, "fakemem", command=sys.executable, args=[fake_server])
    assert result["scope"] == "global"
    assert "every role of every project" in result["roles"]
    # no project settings were touched; both projects see it through the merge
    assert bridge.get_project_settings("demo")["mcp_servers"] == {}
    for project in ("demo", "other"):
        assert bridge.effective_mcp_servers(project)["fakemem"]["scope"] == "global"
    assert "fakemem" in json.loads((bridge.home / "mcp-servers.json").read_text())


def test_a_project_install_is_scoped_and_overrides_the_global_one(bridge, fake_server, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    bridge.register_project(str(other), "other", "second project")
    bridge.install_mcp_server(None, "fakemem", command=sys.executable, args=[fake_server])
    bridge.install_mcp_server(
        "demo", "fakemem", command=sys.executable, args=[fake_server], tools=["list_projects"]
    )
    mine = bridge.effective_mcp_servers("demo")["fakemem"]
    assert mine["scope"] == "project" and mine["allowed_tools"] == ["list_projects"]
    assert bridge.effective_mcp_servers("other")["fakemem"]["allowed_tools"] == [
        "list_projects", "delete_project"
    ]


def test_dispatch_package_carries_global_servers_too(bridge, human, fake_server):
    bridge.install_mcp_server(None, "fakemem", command=sys.executable, args=[fake_server])
    bridge.authorize_project_local("demo")
    task_id = bridge.create_task("demo", "Do a thing")["id"]
    dispatched = bridge.dispatch_agent(
        "demo", task_id, "auto", "SA", bridge.latest("demo")["version"], "Analyze", adapter="fake"
    )
    row = bridge.store.one("SELECT package FROM agent_executions WHERE id=?", (dispatched["execution_id"],))
    assert json.loads(row["package"])["mcp_servers"]["fakemem"]["scope"] == "global"


def test_remove_targets_the_scope_it_was_asked_for(bridge, fake_server):
    bridge.install_mcp_server(None, "fakemem", command=sys.executable, args=[fake_server])
    with pytest.raises(BridgeError, match="installed globally"):
        bridge.remove_mcp_server("demo", "fakemem")  # a project cannot remove a global one
    assert bridge.remove_mcp_server(None, "fakemem")["scope"] == "global"
    assert bridge.effective_mcp_servers("demo") == {}
    with pytest.raises(BridgeError, match="not installed globally"):
        bridge.remove_mcp_server(None, "fakemem")


def test_list_shapes_for_project_and_for_global(bridge, fake_server):
    bridge.install_mcp_server(None, "globalmem", command=sys.executable, args=[fake_server])
    bridge.install_mcp_server("demo", "fakemem", command=sys.executable, args=[fake_server])
    scoped = bridge.list_mcp_servers("demo")
    assert {n: v["scope"] for n, v in scoped["installed"].items()} == {
        "globalmem": "global", "fakemem": "project"
    }
    everything = bridge.list_mcp_servers()
    assert set(everything["global"]) == {"globalmem"}
    assert set(everything["projects"]["demo"]) == {"fakemem"}
    assert "projects" in bridge.list_mcp_servers("demo", all_projects=True)


def test_cli_scope_comes_only_from_an_explicit_project(bridge, fake_server, tmp_path, monkeypatch):
    runner = CliRunner()
    home = ["--home", str(bridge.home)]
    arguments = ["skill", "install", "mcp", "fakemem", "--command", sys.executable, "--arg", fake_server]
    # Even inside a registered project's directory, no --project still means global.
    monkeypatch.chdir(bridge.project("demo")["root"])
    installed = runner.invoke(app, home + arguments)
    assert installed.exit_code == 0, installed.output
    assert json.loads(installed.output[installed.output.index("{"):])["scope"] == "global"
    assert bridge.get_project_settings("demo")["mcp_servers"] == {}

    listed = json.loads(runner.invoke(app, home + ["mcp", "list"]).output)
    assert "fakemem" in listed["global"]
    scoped = json.loads(runner.invoke(app, home + ["--project", "demo", "mcp", "list"]).output)
    assert scoped["installed"]["fakemem"]["scope"] == "global"

    assert runner.invoke(app, home + ["--project", "demo", "mcp", "remove", "fakemem"]).exit_code != 0
    assert runner.invoke(app, home + ["mcp", "remove", "fakemem"]).exit_code == 0


def test_bootstrap_tells_chatgpt_about_installed_mcp_and_how_to_reach_it(bridge, fake_server):
    empty = bridge.bootstrap_project("demo")["installed_mcp"]
    assert empty["servers"] == {}
    assert "skill install mcp" in empty["how_to_use"] and "cannot install" in empty["how_to_use"]
    bridge.install_mcp_server("demo", "fakemem", command=sys.executable, args=[fake_server], tools=["list_projects"])
    hint = bridge.bootstrap_project("demo")["installed_mcp"]
    assert hint["servers"]["fakemem"] == {"scope": "project", "enabled_tools": ["list_projects"]}
    assert "call_installed_mcp_tool" in hint["how_to_use"] and "NOT separate tools" in hint["how_to_use"]
    bridge.install_mcp_server(None, "globalmem", command=sys.executable, args=[fake_server])
    assert bridge.bootstrap_project("demo")["installed_mcp"]["servers"]["globalmem"]["scope"] == "global"
