"""Find, verify and describe local stdio MCP servers for `skill install mcp <name>`.

Like VS Code's "MCP: List Servers": servers are discovered from the configuration files of
the MCP clients installed on this machine, or located as a command on PATH. Before anything
is installed the command must exist and must complete a real MCP handshake, so a missing
conda/venv activation is reported at install time instead of failing inside a dispatch.
Environment variables from client configs are never copied (they commonly hold secrets).
"""

import asyncio
import json
import os
import re
import shutil
import sys
import tomllib
from pathlib import Path

from ..infra.errors import BridgeError

NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")


def _app_support(app):
    home = Path.home()
    if sys.platform == "darwin":
        return home / "Library" / "Application Support" / app
    if os.name == "nt":
        return Path(os.environ.get("APPDATA", home / "AppData" / "Roaming")) / app
    return home / ".config" / app


def client_config_files(project_root=None):
    """(client, path, format) for every place a client may keep MCP server definitions."""
    home = Path.home()
    files = [
        ("Claude Code", home / ".claude.json", "claude"),
        ("Claude Desktop", _app_support("Claude") / "claude_desktop_config.json", "mcpServers"),
        ("Cursor", home / ".cursor" / "mcp.json", "mcpServers"),
        ("VS Code", _app_support("Code") / "User" / "mcp.json", "servers"),
        ("Codex", home / ".codex" / "config.toml", "codex"),
    ]
    if project_root:
        root = Path(project_root)
        files += [
            ("project .mcp.json", root / ".mcp.json", "mcpServers"),
            ("project .vscode/mcp.json", root / ".vscode" / "mcp.json", "servers"),
        ]
    return files


def _entries(client, path, kind):
    try:
        if kind == "codex":
            document = tomllib.loads(path.read_text(encoding="utf-8"))
            table = document.get("mcp_servers") or {}
        else:
            document = json.loads(path.read_text(encoding="utf-8"))
            table = dict(document.get("servers" if kind == "servers" else "mcpServers") or {})
            if kind == "claude":
                for project in (document.get("projects") or {}).values():
                    table.update((project or {}).get("mcpServers") or {})
    except (OSError, ValueError, UnicodeError):
        return []
    found = []
    for name, spec in table.items():
        if not isinstance(spec, dict):
            continue
        stdio = bool(spec.get("command")) and not spec.get("url")
        found.append(
            {
                "name": name,
                "client": client,
                "source_file": str(path),
                "transport": "stdio" if stdio else "http",
                "command": spec.get("command") if stdio else None,
                "args": [str(a) for a in spec.get("args") or []] if stdio else [],
                "has_env": bool(spec.get("env")),
            }
        )
    return found


def discover(project_root=None):
    """Every MCP server defined by a client on this machine (stdio and remote)."""
    found = []
    for client, path, kind in client_config_files(project_root):
        if path.is_file():
            found += _entries(client, path, kind)
    return found


def _norm(text):
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _which(command):
    expanded = str(Path(command).expanduser())
    if os.path.isabs(expanded) or os.sep in expanded:
        return expanded if Path(expanded).is_file() and os.access(expanded, os.X_OK) else None
    return shutil.which(expanded)


def resolve_server(name, command=None, args=None, project_root=None):
    """Absolute command + args for `name`: --command, a client-configured server, or PATH."""
    if not NAME.fullmatch(name):
        raise BridgeError("MCP name must be letters, digits, underscore or hyphen")
    warnings = []
    if command:
        path = _which(command)
        if not path:
            raise BridgeError(f"--command {command} does not exist or is not executable")
        return {"name": name, "command": path, "args": list(args or []), "source": "--command",
                "warnings": warnings}

    servers = discover(project_root)
    wanted = _norm(name)
    exact = [s for s in servers if _norm(s["name"]) == wanted]
    close = [s for s in servers if wanted and (wanted in _norm(s["name"]) or _norm(s["name"]) in wanted)]
    for candidate in exact + [s for s in close if s not in exact]:
        if candidate["transport"] != "stdio":
            warnings.append(
                f"{candidate['client']} defines '{candidate['name']}' as a remote (HTTP) server; "
                "only local stdio servers are supported"
            )
            continue
        path = _which(candidate["command"])
        if path:
            if candidate["has_env"]:
                warnings.append(
                    f"{candidate['client']}'s entry sets environment variables; they are NOT copied "
                    "(they may hold secrets). Export them in the shell that runs ccc-mcp if needed."
                )
            return {"name": candidate["name"], "command": path, "args": candidate["args"],
                    "source": f"{candidate['client']} ({candidate['source_file']})",
                    "warnings": warnings}
        warnings.append(
            f"{candidate['client']} defines '{candidate['name']}' with command "
            f"'{candidate['command']}', which cannot be run from this shell"
        )

    for guess in (name, f"{name}-mcp", f"{name}-server"):
        path = shutil.which(guess)
        if path:
            return {"name": name, "command": path, "args": list(args or []),
                    "source": "PATH", "warnings": warnings}

    known = ", ".join(sorted({s["name"] for s in servers})) or "none"
    raise BridgeError(
        f"No runnable MCP server '{name}' found. {' '.join(warnings)} "
        f"Servers defined by clients on this machine: {known}. If it lives in a conda/venv "
        "environment, activate that environment in this shell first (for example "
        "`conda activate <env>`) so the command resolves, or pass --command /full/path. "
        "The full path is recorded at install time, so later runs do not need the activation."
    )


async def _list_tools(command, args, timeout):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    with open(os.devnull, "w", encoding="utf-8") as quiet:
        async with asyncio.timeout(timeout):
            async with stdio_client(StdioServerParameters(command=command, args=args), quiet) as (
                reader, writer,
            ):
                async with ClientSession(reader, writer) as session:
                    await session.initialize()
                    return (await session.list_tools()).tools


def probe_tools(command, args=(), timeout=45):
    """Complete a real MCP handshake and return [{name, description, read_only}]."""
    try:
        tools = asyncio.run(_list_tools(command, list(args), timeout))
    except TimeoutError:
        raise BridgeError(f"{command} did not finish the MCP handshake within {timeout}s") from None
    except Exception as exc:  # the server is third-party; any failure means "not a usable server"
        raise BridgeError(f"{command} is not a working stdio MCP server: {exc}") from None
    return [
        {
            "name": tool.name,
            "description": (tool.description or "").strip().split("\n")[0][:160],
            "read_only": bool(getattr(tool.annotations, "read_only_hint", False)),
        }
        for tool in tools
    ]


def claude_tool_patterns(servers):
    """`--allowedTools` entries for the installed servers: mcp__<server>__<tool>."""
    return [
        f"mcp__{name}__{tool}"
        for name, spec in servers.items()
        for tool in spec.get("allowed_tools", [])
    ]
