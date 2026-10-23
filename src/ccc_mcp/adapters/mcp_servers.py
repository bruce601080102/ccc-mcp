"""Find, verify and describe local stdio MCP servers for `skill install mcp <name>`.

Like VS Code's "MCP: List Servers": servers are discovered from the configuration files of
the MCP clients installed on this machine, or located as a command on PATH. Before anything
is installed the command must exist and must complete a real MCP handshake, so a missing
conda/venv activation is reported at install time instead of failing inside a dispatch.
Environment variables from client configs are never copied (they commonly hold secrets).
"""

import asyncio
import atexit
import json
import os
import re
import shutil
import sys
import threading
import tomllib
from concurrent.futures import TimeoutError as FutureTimeoutError
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


MAX_RESULT_CHARS = 60_000
IDLE_SECONDS = 300


class _Session:
    """One long-lived MCP client session, owned by a single task.

    anyio requires a connection to be entered and exited in the same task, so the session
    lives inside `run()` and callers hand it work through a queue instead of sharing it."""

    def __init__(self, command, args):
        self.command, self.args = command, list(args)
        self.queue = None
        self.ready = None
        self.closed = False
        self.task = None

    async def run(self):
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        try:
            with open(os.devnull, "w", encoding="utf-8") as quiet:
                async with stdio_client(
                    StdioServerParameters(command=self.command, args=self.args), quiet
                ) as (reader, writer):
                    async with ClientSession(reader, writer) as session:
                        await session.initialize()
                        self.ready.set_result(True)
                        while True:
                            try:
                                item = await asyncio.wait_for(self.queue.get(), IDLE_SECONDS)
                            except TimeoutError:
                                return
                            if item is None:
                                return
                            kind, payload, future = item
                            try:
                                if kind == "call":
                                    outcome = await session.call_tool(*payload)
                                else:
                                    outcome = (await session.list_tools()).tools
                                future.set_result(outcome)
                            except Exception as exc:  # reported to the waiting caller
                                future.set_exception(exc)
        except BaseException as exc:
            if not self.ready.done():
                self.ready.set_exception(exc if isinstance(exc, Exception) else RuntimeError("cancelled"))
            if not isinstance(exc, Exception):
                raise
        finally:
            self.closed = True


class McpPool:
    """Keeps started MCP servers alive between calls (a slow starter such as
    codebase-memory-mcp takes ~6 s), one process per (scope, command, args), so projects
    never share a server's state. Idle servers are closed after IDLE_SECONDS."""

    def __init__(self):
        self._loop = None
        self._guard = threading.Lock()
        self._sessions = {}

    def _event_loop(self):
        with self._guard:
            if self._loop is None:
                loop = asyncio.new_event_loop()
                threading.Thread(target=loop.run_forever, name="ccc-mcp-pool", daemon=True).start()
                self._loop = loop
            return self._loop

    async def _session(self, key, command, args, timeout):
        session = self._sessions.get(key)
        if session is None or session.closed:
            session = _Session(command, args)
            session.queue = asyncio.Queue()
            session.ready = asyncio.get_running_loop().create_future()
            session.task = asyncio.get_running_loop().create_task(session.run())
            self._sessions[key] = session
        await asyncio.wait_for(asyncio.shield(session.ready), timeout)
        return session

    async def _submit(self, key, command, args, kind, payload, timeout):
        for attempt in (1, 2):
            session = await self._session(key, command, args, timeout)
            future = asyncio.get_running_loop().create_future()
            await session.queue.put((kind, payload, future))
            try:
                return await asyncio.wait_for(asyncio.shield(future), timeout)
            except TimeoutError:
                session.task.cancel()  # a stuck server must not block the next caller
                raise
            except Exception:
                # A connection that died while idle fails the first attempt only.
                session.task.cancel()
                self._sessions.pop(key, None)
                if attempt == 2:
                    raise

    def submit(self, scope, command, args, kind, payload, timeout):
        key = (scope, command, tuple(args))
        loop = self._event_loop()
        return asyncio.run_coroutine_threadsafe(
            self._submit(key, command, list(args), kind, payload, timeout), loop
        ).result(timeout + 30)

    def close_all(self):
        with self._guard:
            loop = self._loop
        if loop is None:
            return

        async def stop():
            for session in list(self._sessions.values()):
                session.task.cancel()
            self._sessions.clear()
            await asyncio.sleep(0.2)

        asyncio.run_coroutine_threadsafe(stop(), loop).result(15)


POOL = McpPool()
atexit.register(POOL.close_all)


def call_tool(scope, command, args, tool, arguments, timeout=180):
    """Run one tool and return {is_error, text, truncated}. The server stays running
    between calls for the same scope (project), so only the first call pays its startup."""
    try:
        result = POOL.submit(scope, command, args, "call", (tool, arguments), timeout)
    except (TimeoutError, FutureTimeoutError):
        raise BridgeError(f"{tool} did not finish within {timeout}s") from None
    except Exception as exc:  # third-party server: report, never crash the caller
        raise BridgeError(f"{tool} failed: {exc}") from None
    text = "\n".join(
        getattr(block, "text", None) or f"[{getattr(block, 'type', 'content')} omitted]"
        for block in result.content
    )
    return {
        "is_error": bool(result.is_error),
        "text": text[:MAX_RESULT_CHARS],
        "truncated": len(text) > MAX_RESULT_CHARS,
    }


def describe_tools(scope, command, args=(), timeout=90):
    """[{name, description, input_schema}] — what a model needs to call each tool."""
    try:
        tools = POOL.submit(scope, command, args, "list", None, timeout)
    except Exception as exc:
        raise BridgeError(f"could not list tools of {command}: {exc}") from None
    return [
        {
            "name": tool.name,
            "description": (tool.description or "").strip(),
            "input_schema": tool.input_schema,
        }
        for tool in tools
    ]
