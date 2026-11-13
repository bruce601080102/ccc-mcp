"""Local-only Web console: a third thin interface over the same `CCCMCP` service
that `interfaces/cli.py` and `interfaces/server.py` already use (see service.py's
docstring). No business logic lives here — every handler just calls a bridge method
and serializes its result; the usual safety layers (safe_path, project-root
re-validation, immutable audit tables) apply automatically because the bridge
methods are the same ones the CLI calls.

Trust model: this server binds to 127.0.0.1 only (see interfaces/cli.py's `ui`
command) and every mutating request must carry a per-launch, in-memory-only
session token (`X-CCC-UI-Token`) handed to the browser once, in the URL fragment
(never sent over the network, never logged, never written to disk) when the
console is opened. A request without a valid token cannot read or change
anything. This is the same local-session-token pattern long-established local
dev consoles (e.g. Jupyter) use; see docs/en/7-reliability-and-security.md.
"""

import asyncio
import collections
import hmac
import re
import secrets
from pathlib import Path

from markdown_it import MarkdownIt
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from .. import __version__
from ..adapters import mcp_servers as mcp_registry
from ..adapters.agent_setup import LOGIN_ARGS, ensure_installed, uninstall_provider
from ..adapters.agents.base import native_executable
from ..adapters.environment import git_check, python_version
from ..adapters.tunnel_client import (
    connection_profiles,
    forget_connection_profile,
    forget_tunnel_key,
    get_connection_profile,
    load_tunnel_key,
    prepare_tunnel_connection,
    provider_login_status,
    remembered_tunnels,
    save_connection_profile,
    save_tunnel_key,
    tunnel_client_path,
)
from ..adapters.ui_groups import change_project_groups, project_groups
from ..evaluation_engine.config import validated_role_weights
from ..infra.claude_usage import (
    disable_claude_usage,
    enable_claude_usage,
    probe_claude_usage,
    read_claude_usage,
)
from ..infra.codex_account import read_account_usage
from ..infra.errors import BridgeError
from ..service import CCCMCP

STATIC_DIR = Path(__file__).parent / "webui_static"
DOCS_DIR = Path(__file__).resolve().parent.parent / "docs"
SOURCE_DOCS_DIR = Path(__file__).resolve().parents[3] / "docs"
DOC_SLUG = re.compile(r"(?:README|[0-9]+-[a-z0-9-]+)\Z")


async def documentation(request: Request):
    """Serve the matching bundled guide for the UI's selected language."""
    language = request.path_params["language"]
    slug = request.path_params["slug"]
    if language not in {"en", "ch"} or not DOC_SLUG.fullmatch(slug):
        return JSONResponse({"error": "Document not found"}, status_code=404)
    root = DOCS_DIR if DOCS_DIR.is_dir() else SOURCE_DOCS_DIR
    path = root / language / f"{slug}.md"
    if not path.is_file():
        return JSONResponse({"error": "Document not found"}, status_code=404)
    markdown = await asyncio.to_thread(path.read_text, encoding="utf-8")
    html = MarkdownIt("default", {"html": False}).render(markdown)
    return JSONResponse({"html": html})


async def run(fn, *args, **kwargs):
    """Run bridge logic off the event loop, mirroring interfaces/server.py's `call`."""
    return await asyncio.to_thread(fn, *args, **kwargs)


def endpoint(fn):
    async def handler(request: Request):
        try:
            return await fn(request)
        except BridgeError as exc:
            return JSONResponse({"error": str(exc)}, status_code=400)
        except (KeyError, ValueError, TypeError) as exc:
            return JSONResponse({"error": f"Invalid request: {exc}"}, status_code=400)

    return handler


async def _body(request: Request) -> dict:
    if not await request.body():
        return {}
    data = await request.json()
    if not isinstance(data, dict):
        raise BridgeError("Request body must be a JSON object")
    return data


def bridge_of(request: Request) -> CCCMCP:
    return request.app.state.bridge


class ConnectionManager:
    """Supervises one long-lived `tunnel-client run` subprocess for the console's
    own process lifetime (one tunnel connection at a time, matching `ccc-mcp
    connect`'s own foreground-process model). The CLI's `connect` runs this same
    kind of subprocess as the terminal's own foreground job; here nothing blocks
    the event loop, and recent output is kept in memory so the UI can show it.
    """

    def __init__(self):
        self.process = None
        self.tunnel_id = None
        self.state = "disconnected"
        self.error = None
        self.log = collections.deque(maxlen=400)
        self._reader_task = None
        self._stopping = False
        self._secret = None

    def status(self):
        return {
            "state": self.state,
            "tunnel_id": self.tunnel_id,
            "error": self.error,
        }

    async def start(self, client, profile, profile_dir, env, tunnel_id):
        if self.process is not None and self.process.returncode is None:
            raise BridgeError("Already connected; stop the current connection first")
        self.tunnel_id = tunnel_id
        self.state = "starting"
        self.error = None
        self._stopping = False
        self._secret = env.get("CONTROL_PLANE_API_KEY")
        self.log.clear()
        self.process = await asyncio.create_subprocess_exec(
            str(client),
            "run",
            "--profile",
            profile,
            "--profile-dir",
            str(profile_dir),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        self.state = "connected"
        self._reader_task = asyncio.create_task(self._read_output())

    async def _read_output(self):
        process = self.process
        try:
            async for raw_line in process.stdout:
                line = raw_line.decode("utf-8", "replace").rstrip()
                if self._secret:
                    line = line.replace(self._secret, "[REDACTED]")
                self.log.append(line)
        finally:
            returncode = await process.wait()
            self._secret = None
            if self._stopping:
                self.state = "disconnected"
            elif returncode != 0:
                self.state = "error"
                self.error = f"tunnel-client exited with code {returncode}"
            else:
                self.state = "disconnected"

    async def stop(self):
        if self.process is None or self.process.returncode is not None:
            self.state = "disconnected"
            return
        self._stopping = True
        self.state = "stopping"
        self.process.terminate()
        try:
            await asyncio.wait_for(self.process.wait(), timeout=10)
        except TimeoutError:
            self.process.kill()
            await self.process.wait()
        self.state = "disconnected"


class ProviderLoginManager:
    """Runs one native CLI's interactive login (`codex login` / `claude auth login`)
    as a subprocess instead of a TTY, modeled on ConnectionManager above, so the
    Connect page can drive it from the browser — including on a host that has no
    GUI/browser of its own to show the login flow's own prompts in."""

    def __init__(self, provider):
        self.provider = provider
        self.process = None
        self.state = "idle"  # idle | running | success | failed | cancelled
        self.error = None
        self.log = collections.deque(maxlen=200)
        self._reader_task = None

    def status(self):
        return {"provider": self.provider, "state": self.state, "error": self.error, "log": list(self.log)}

    async def start(self, path):
        if self.process is not None and self.process.returncode is None:
            raise BridgeError("A sign-in is already in progress for this provider")
        self.state = "running"
        self.error = None
        self.log.clear()
        self.process = await asyncio.create_subprocess_exec(
            path,
            *LOGIN_ARGS[self.provider],
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        self._reader_task = asyncio.create_task(self._read_output())

    async def _read_output(self):
        process = self.process
        try:
            async for raw_line in process.stdout:
                self.log.append(raw_line.decode("utf-8", "replace").rstrip())
        finally:
            returncode = await process.wait()
            if self.state == "cancelled":
                pass
            elif returncode == 0:
                self.state = "success"
            else:
                self.state = "failed"
                self.error = f"{self.provider} login exited with code {returncode}"

    async def send_input(self, text):
        if self.process is None or self.process.returncode is not None or self.process.stdin is None:
            raise BridgeError("No sign-in is waiting for input")
        self.process.stdin.write(f"{text}\n".encode())
        await self.process.stdin.drain()

    async def cancel(self):
        if self.process is None or self.process.returncode is not None:
            self.state = "idle"
            return
        self.state = "cancelled"
        self.process.terminate()
        try:
            await asyncio.wait_for(self.process.wait(), timeout=10)
        except TimeoutError:
            self.process.kill()
            await self.process.wait()
        self.state = "idle"


# --- Projects -----------------------------------------------------------------


@endpoint
async def list_projects(request: Request):
    bridge = bridge_of(request)
    projects = await run(bridge.list_projects)
    rows = []
    for project in projects:
        access = await run(bridge.get_project_access, project["id"])
        rows.append({**project, "access": access})
    return JSONResponse(rows)


@endpoint
async def dashboard(request: Request):
    return JSONResponse(await run(
        bridge_of(request).dashboard_snapshot,
        request.query_params.get("usage_window", "1d"),
    ))


@endpoint
async def dashboard_account_usage(request: Request):
    return JSONResponse(await read_account_usage())


@endpoint
async def dashboard_claude_usage(request: Request):
    return JSONResponse(await run(read_claude_usage, bridge_of(request).home))


@endpoint
async def dashboard_claude_usage_enable(request: Request):
    return JSONResponse(await run(enable_claude_usage, bridge_of(request).home))


@endpoint
async def dashboard_claude_usage_probe(request: Request):
    return JSONResponse(await run(probe_claude_usage, bridge_of(request).home))


@endpoint
async def dashboard_claude_usage_disable(request: Request):
    return JSONResponse(await run(disable_claude_usage, bridge_of(request).home))


@endpoint
async def get_project_groups(request: Request):
    bridge = bridge_of(request)
    ids = {p["id"] for p in await run(bridge.list_projects)}
    return JSONResponse(await run(project_groups, bridge.home, ids))


@endpoint
async def update_project_groups(request: Request):
    bridge = bridge_of(request)
    ids = {p["id"] for p in await run(bridge.list_projects)}
    return JSONResponse(await run(change_project_groups, bridge.home, ids, await _body(request)))


@endpoint
async def browse(request: Request):
    """List subdirectories of a path, so the UI can offer a folder picker without a
    native OS dialog (a plain local web page cannot open one). This exposes nothing
    `project add <any path>` could not already reach from the CLI — only directory
    *names*, never file contents — and every path a user actually submits is still
    re-validated by register_project/create_project's own symlink/traversal checks."""
    raw = request.query_params.get("path") or str(Path.home())
    target = Path(raw).expanduser()
    if not target.is_absolute():
        raise BridgeError("Path must be absolute")
    if not target.is_dir():
        raise BridgeError("Not a directory")
    entries = []
    try:
        for child in sorted(target.iterdir(), key=lambda p: p.name.lower()):
            try:
                if child.is_dir() and not child.is_symlink():
                    entries.append(child.name)
            except OSError:
                continue
    except OSError as exc:
        raise BridgeError(f"Cannot list directory: {exc}") from None
    return JSONResponse(
        {
            "path": str(target),
            "parent": str(target.parent) if target.parent != target else None,
            "directories": entries,
        }
    )


@endpoint
async def add_project(request: Request):
    bridge = bridge_of(request)
    body = await _body(request)
    path = body.get("path")
    if not path:
        raise BridgeError("path is required")
    registered = await run(
        bridge.register_project,
        path,
        body.get("id"),
        body.get("purpose"),
        body.get("language", "zh-TW"),
    )
    await run(bridge.apply_new_project_workflow, registered["id"])
    if body.get("enable_write"):
        confirm = body.get("confirm", "")
        registered = {
            **registered,
            "access": await run(bridge.authorize_project_web, registered["id"], confirm),
        }
    return JSONResponse(registered)


@endpoint
async def create_project(request: Request):
    bridge = bridge_of(request)
    body = await _body(request)
    project_id = body.get("id")
    if not project_id:
        raise BridgeError("id is required")
    created = await run(
        bridge.create_project,
        project_id,
        body.get("path"),
        body.get("language", "zh-TW"),
        body.get("profile"),
    )
    await run(bridge.apply_new_project_workflow, created["id"])
    if body.get("enable_write"):
        confirm = body.get("confirm", "")
        created = {
            **created,
            "access": await run(bridge.authorize_project_web, created["id"], confirm),
        }
    return JSONResponse(created)


@endpoint
async def export_project(request: Request):
    project_id = request.path_params["project_id"]
    bundle = await run(bridge_of(request).export_project_bundle, project_id)
    return Response(
        bundle,
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="ccc-mcp-{project_id}.json"',
            "Cache-Control": "no-store",
        },
    )


@endpoint
async def import_project(request: Request):
    from ..core.project_bundle import MAX_BYTES

    path = request.query_params.get("path")
    if not path:
        raise BridgeError("Choose an existing project folder")
    if int(request.headers.get("content-length", "0")) > MAX_BYTES:
        raise BridgeError("Project bundle exceeds 100 MB")
    data = await request.body()
    result = await run(bridge_of(request).import_project_bundle, data, path)
    return JSONResponse(result)


@endpoint
async def authorize_preview(request: Request):
    bridge = bridge_of(request)
    result = await run(bridge.authorize_preview, request.path_params["project_id"])
    return JSONResponse(result)


@endpoint
async def deregister_preview(request: Request):
    bridge = bridge_of(request)
    result = await run(bridge.deregister_preview, request.path_params["project_id"])
    return JSONResponse(result)


@endpoint
async def remove_preview(request: Request):
    bridge = bridge_of(request)
    delete_files = request.query_params.get("delete_files") == "true"
    result = await run(bridge.remove_preview, request.path_params["project_id"], delete_files)
    return JSONResponse(result)


@endpoint
async def clear_history_preview(request: Request):
    bridge = bridge_of(request)
    result = await run(bridge.clear_history_preview, request.path_params["project_id"])
    return JSONResponse(result)


@endpoint
async def authorize_project(request: Request):
    bridge = bridge_of(request)
    body = await _body(request)
    result = await run(
        bridge.authorize_project_web, request.path_params["project_id"], body.get("confirm", "")
    )
    return JSONResponse(result)


@endpoint
async def revoke_project(request: Request):
    bridge = bridge_of(request)
    result = await run(bridge.revoke_project_local, request.path_params["project_id"])
    return JSONResponse(result)


@endpoint
async def delete_project(request: Request):
    bridge = bridge_of(request)
    body = await _body(request)
    result = await run(
        bridge.deregister_project_web, request.path_params["project_id"], body.get("confirm", "")
    )
    return JSONResponse(result)


@endpoint
async def remove_project(request: Request):
    bridge = bridge_of(request)
    body = await _body(request)
    if not isinstance(body.get("delete_files", False), bool):
        raise BridgeError("delete_files must be a boolean")
    if not isinstance(body.get("keep_records", False), bool):
        raise BridgeError("keep_records must be a boolean")
    result = await run(
        bridge.remove_project_web,
        request.path_params["project_id"],
        body.get("confirm", ""),
        body.get("delete_files", False),
        body.get("keep_records", False),
    )
    return JSONResponse(result)


@endpoint
async def clear_history(request: Request):
    bridge = bridge_of(request)
    body = await _body(request)
    result = await run(
        bridge.clear_project_history_web,
        request.path_params["project_id"],
        body.get("confirm", ""),
    )
    return JSONResponse(result)


# --- Settings -------------------------------------------------------------------


@endpoint
async def get_settings(request: Request):
    bridge = bridge_of(request)
    result = await run(bridge.provider_overview, request.path_params["project_id"])
    result["evaluation_weights"] = validated_role_weights(
        result["editable"].get("evaluation_engine", {}).get("weights")
    )
    return JSONResponse(result)


@endpoint
async def set_role_name(request: Request):
    bridge = bridge_of(request)
    body = await _body(request)
    result = await run(
        bridge.configure_role_name,
        request.path_params["project_id"],
        body.get("role"),
        body.get("name"),
    )
    return JSONResponse(result)


@endpoint
async def set_role_avatar(request: Request):
    bridge = bridge_of(request)
    body = await _body(request)
    return JSONResponse(await run(
        bridge.configure_role_avatar,
        request.path_params["project_id"],
        body.get("role"),
        body.get("avatar"),
    ))


@endpoint
async def set_role(request: Request):
    bridge = bridge_of(request)
    body = await _body(request)
    result = await run(
        bridge.configure_role,
        request.path_params["project_id"],
        body.get("role"),
        body.get("provider"),
        body.get("model"),
        body.get("effort"),
        body.get("quota_fallback"),
    )
    return JSONResponse(result)


@endpoint
async def set_profile(request: Request):
    bridge = bridge_of(request)
    body = await _body(request)
    result = await run(
        bridge.configure_profile, request.path_params["project_id"], body.get("profile")
    )
    return JSONResponse(result)


@endpoint
async def set_language(request: Request):
    bridge = bridge_of(request)
    body = await _body(request)
    result = await run(
        bridge.configure_language, request.path_params["project_id"], body.get("language")
    )
    return JSONResponse(result)


@endpoint
async def set_runtime(request: Request):
    body = await _body(request)
    if "runtime" not in body:
        raise BridgeError("runtime is required; use null to clear")
    result = await run(
        bridge_of(request).configure_runtime, request.path_params["project_id"], body["runtime"]
    )
    return JSONResponse(result)


@endpoint
async def check_runtime(request: Request):
    body = await _body(request)
    result = await run(
        bridge_of(request).check_project_runtime,
        request.path_params["project_id"], body.get("runtime"),
    )
    return JSONResponse(result)


@endpoint
async def set_test_command(request: Request):
    bridge = bridge_of(request)
    body = await _body(request)
    result = await run(
        bridge.configure_test_command, request.path_params["project_id"], body.get("command")
    )
    return JSONResponse(result)


@endpoint
async def set_meeting_mode(request: Request):
    bridge = bridge_of(request)
    body = await _body(request)
    result = await run(
        bridge.configure_meeting_mode,
        request.path_params["project_id"],
        body.get("enabled"),
        body.get("min_rounds"),
        body.get("max_rounds"),
        body.get("escalation"),
    )
    return JSONResponse(result)


@endpoint
async def set_workflow(request: Request):
    bridge = bridge_of(request)
    body = await _body(request)
    return JSONResponse(await run(
        bridge.configure_workflow, request.path_params["project_id"], body.get("workflow"),
    ))


@endpoint
async def set_review_gate(request: Request):
    bridge = bridge_of(request)
    body = await _body(request)
    return JSONResponse(await run(
        bridge.configure_review_gate,
        request.path_params["project_id"],
        body.get("ar_strictness"),
        body.get("max_rounds"),
        body.get("escalation"),
    ))


@endpoint
async def set_workflow_selection(request: Request):
    bridge = bridge_of(request)
    body = await _body(request)
    return JSONResponse(await run(
        bridge.configure_workflow_selection, request.path_params["project_id"], body.get("selection"),
    ))


@endpoint
async def save_workflow(request: Request):
    bridge = bridge_of(request)
    body = await _body(request)
    return JSONResponse(await run(
        bridge.save_workflow,
        request.path_params["project_id"],
        body.get("steps"),
        body.get("name"),
        body.get("id"),
    ))


@endpoint
async def delete_workflow(request: Request):
    bridge = bridge_of(request)
    return JSONResponse(await run(
        bridge.delete_workflow, request.path_params["project_id"], request.path_params["workflow_id"],
    ))


@endpoint
async def list_workflows(request: Request):
    bridge = bridge_of(request)
    return JSONResponse(await run(bridge.workflow_catalog, request.path_params["project_id"]))


@endpoint
async def task_gate_decision(request: Request):
    bridge = bridge_of(request)
    body = await _body(request)
    return JSONResponse(await run(
        bridge.resolve_flow_gate,
        request.path_params["project_id"],
        request.path_params["task_id"],
        body.get("decision"),
        body.get("note") or "",
    ))


@endpoint
async def set_evaluation_engine(request: Request):
    bridge = bridge_of(request)
    body = await _body(request)
    result = await run(
        bridge.configure_evaluation_engine,
        request.path_params["project_id"],
        body.get("enabled"),
        body.get("auto_evaluate"),
        body.get("weights"),
        body.get("low_sample_threshold"),
    )
    return JSONResponse(result)


# --- Tasks (read-only) -----------------------------------------------------------


@endpoint
async def list_tasks(request: Request):
    bridge = bridge_of(request)
    result = await run(bridge.list_tasks, request.path_params["project_id"])
    return JSONResponse(result)


@endpoint
async def task_status(request: Request):
    bridge = bridge_of(request)
    result = await run(
        bridge.get_task_status, request.path_params["project_id"], request.path_params["task_id"]
    )
    return JSONResponse(result)


@endpoint
async def task_result(request: Request):
    bridge = bridge_of(request)
    result = await run(
        bridge.get_task_result, request.path_params["project_id"], request.path_params["task_id"]
    )
    return JSONResponse(result)


@endpoint
async def task_usage(request: Request):
    bridge = bridge_of(request)
    result = await run(
        bridge.get_task_usage, request.path_params["project_id"], request.path_params["task_id"]
    )
    return JSONResponse(result)


@endpoint
async def task_evaluations(request: Request):
    bridge = bridge_of(request)
    return JSONResponse(await run(
        bridge.get_task_evaluations,
        request.path_params["project_id"], request.path_params["task_id"],
    ))


@endpoint
async def task_evaluate(request: Request):
    bridge = bridge_of(request)
    body = await _body(request)
    return JSONResponse(await run(
        bridge.evaluate_task,
        request.path_params["project_id"], request.path_params["task_id"],
        body.get("role"), body.get("task_type", "general"), body.get("force", False),
    ))


@endpoint
async def task_reevaluate(request: Request):
    bridge = bridge_of(request)
    body = await _body(request)
    execution_id = body.get("execution_id")
    if not isinstance(execution_id, str) or not execution_id:
        raise BridgeError("execution_id is required")
    return JSONResponse(await run(
        bridge.reevaluate_execution,
        request.path_params["project_id"], request.path_params["task_id"],
        execution_id, body.get("task_type", "general"),
    ))


@endpoint
async def agent_reliability(request: Request):
    bridge = bridge_of(request)
    return JSONResponse(await run(bridge.get_agent_reliability))


@endpoint
async def task_approvals(request: Request):
    bridge = bridge_of(request)
    result = await run(
        bridge.get_approval_status,
        request.path_params["project_id"],
        request.path_params["task_id"],
    )
    return JSONResponse(result)


@endpoint
async def task_meeting(request: Request):
    bridge = bridge_of(request)
    result = await run(
        bridge.meeting_transcript, request.path_params["project_id"], request.path_params["task_id"]
    )
    return JSONResponse(result)


@endpoint
async def task_handoff(request: Request):
    bridge = bridge_of(request)
    result = await run(
        bridge.task_handoff, request.path_params["project_id"], request.path_params["task_id"]
    )
    return JSONResponse(result)


@endpoint
async def task_continuation(request: Request):
    # Read-only: the same package as `ccc-mcp task continue`. Without ?target it is the
    # plain handoff report; with ?target=claude|codex|chatgpt it is a Continuation Prompt.
    bridge = bridge_of(request)
    target = request.query_params.get("target") or None
    text = await run(
        bridge.continuity_handoff,
        request.path_params["project_id"],
        request.path_params["task_id"],
        target=target,
    )
    return JSONResponse({"target": target, "text": text})


@endpoint
async def task_timeline(request: Request):
    bridge = bridge_of(request)
    return JSONResponse(await run(
        bridge.task_timeline, request.path_params["project_id"], request.path_params["task_id"]
    ))


@endpoint
async def task_artifacts(request: Request):
    bridge = bridge_of(request)
    return JSONResponse(await run(
        bridge.task_artifacts, request.path_params["project_id"], request.path_params["task_id"]
    ))


@endpoint
async def task_pause(request: Request):
    bridge = bridge_of(request)
    return JSONResponse(await run(
        bridge.pause_task, request.path_params["project_id"], request.path_params["task_id"]
    ))


@endpoint
async def task_resume(request: Request):
    bridge = bridge_of(request)
    return JSONResponse(await run(
        bridge.resume_task, request.path_params["project_id"], request.path_params["task_id"]
    ))


@endpoint
async def task_stop(request: Request):
    bridge = bridge_of(request)
    body = await _body(request)
    return JSONResponse(await run(
        bridge.cancel_task, request.path_params["project_id"], request.path_params["task_id"],
        body.get("reason", ""),
    ))


@endpoint
async def task_delete(request: Request):
    bridge = bridge_of(request)
    return JSONResponse(await run(
        bridge.delete_task, request.path_params["project_id"], request.path_params["task_id"],
    ))


@endpoint
async def task_message(request: Request):
    bridge = bridge_of(request)
    body = await _body(request)
    return JSONResponse(await run(
        bridge.add_task_message, request.path_params["project_id"], request.path_params["task_id"],
        body.get("content", ""), body.get("pause", True),
    ))


@endpoint
async def task_restart(request: Request):
    bridge = bridge_of(request)
    body = await _body(request)
    return JSONResponse(await run(
        bridge.restart_from_step, request.path_params["project_id"], request.path_params["task_id"],
        body.get("step"), body.get("content", ""),
    ))


# --- Context / Skills / MCP ------------------------------------------------------


@endpoint
async def get_context(request: Request):
    bridge = bridge_of(request)
    result = await run(bridge.get_context, request.path_params["project_id"], "PM")
    return JSONResponse(result)


@endpoint
async def list_skills(request: Request):
    from ..adapters.skills import list_skills as _list_skills

    bridge = bridge_of(request)
    result = await run(_list_skills, bridge.home)
    return JSONResponse(result)


@endpoint
async def discover_skills(request: Request):
    from ..adapters.skills import discover_skills as _discover_skills

    bridge = bridge_of(request)
    result = await run(_discover_skills, bridge.home)
    return JSONResponse(result)


def _discovered_skill(bridge: CCCMCP, body: dict) -> dict:
    """Resolve an exact entry shown by discovery, never an arbitrary browser-supplied path."""
    from ..adapters.skills import discover_skills as _discover_skills

    matches = [
        item
        for item in _discover_skills(bridge.home)
        if item["id"] == body.get("id") and item["path"] == body.get("path")
    ]
    if len(matches) != 1:
        raise BridgeError("The selected skill is no longer available; refresh the list")
    found = matches[0]
    if not found["usable"]:
        raise BridgeError(found["reason"] or "This skill cannot be installed")
    return found


@endpoint
async def install_discovered_skill(request: Request):
    from ..adapters.skills import install_skill as _install_skill

    bridge = bridge_of(request)
    body = await _body(request)
    found = await run(_discovered_skill, bridge, body)
    result = await run(_install_skill, bridge.home, found["path"])
    return JSONResponse(result)


@endpoint
async def remove_global_skill(request: Request):
    from ..adapters.skills import remove_skill as _remove_skill
    from ..core.approval import confirm_web

    bridge = bridge_of(request)
    skill_id = request.path_params["skill_id"]
    body = await _body(request)
    await run(confirm_web, f"REMOVE SKILL {skill_id}", body.get("confirm"))
    result = await run(_remove_skill, bridge.home, skill_id)
    return JSONResponse(result)


@endpoint
async def list_mcp(request: Request):
    bridge = bridge_of(request)
    result = await run(bridge.list_mcp_servers, request.path_params["project_id"])
    return JSONResponse(result)


def _discovered_mcp(bridge: CCCMCP, project_id: str, body: dict) -> dict:
    """Resolve an exact entry shown by discovery, never an arbitrary browser command."""
    root = bridge.project(project_id)["root"]
    matches = [
        item
        for item in mcp_registry.discover(root)
        if item["name"] == body.get("name")
        and item["client"] == body.get("client")
        and item["source_file"] == body.get("source_file")
    ]
    if len(matches) != 1:
        raise BridgeError("The selected MCP server is no longer available; refresh the list")
    found = matches[0]
    if found["transport"] != "stdio":
        raise BridgeError("Only local stdio MCP servers can be installed")
    return found


@endpoint
async def probe_mcp(request: Request):
    bridge = bridge_of(request)
    project_id = request.path_params["project_id"]
    body = await _body(request)
    found = await run(_discovered_mcp, bridge, project_id, body)
    resolved = await run(
        mcp_registry.resolve_server,
        found["name"],
        found["command"],
        found["args"],
        bridge.project(project_id)["root"],
    )
    tools = await run(mcp_registry.probe_tools, resolved["command"], resolved["args"])
    return JSONResponse({"name": found["name"], "command": resolved["command"], "tools": tools})


@endpoint
async def install_mcp(request: Request):
    bridge = bridge_of(request)
    project_id = request.path_params["project_id"]
    body = await _body(request)
    found = await run(_discovered_mcp, bridge, project_id, body)
    tools = body.get("tools")
    if not isinstance(tools, list) or not tools or any(not isinstance(t, str) for t in tools):
        raise BridgeError("Select at least one MCP tool to enable")
    result = await run(
        bridge.install_mcp_server,
        project_id,
        found["name"],
        found["command"],
        found["args"],
        tools,
    )
    return JSONResponse(result)


@endpoint
async def probe_mcp_by_name(request: Request):
    """Preview a server by name alone, the same way `skill install mcp <name>` resolves it:
    an exact/close match from a client's config, or a PATH command named `<name>`,
    `<name>-mcp` or `<name>-server`. Covers tools that run but were never registered in any
    MCP client config (for example a bare pip/npm install), so they aren't missed just
    because they don't appear in the discovered-servers table."""
    bridge = bridge_of(request)
    project_id = request.path_params["project_id"]
    body = await _body(request)
    name = (body.get("name") or "").strip()
    if not name:
        raise BridgeError("Enter an MCP server name")
    resolved = await run(
        mcp_registry.resolve_server,
        name,
        (body.get("command") or "").strip() or None,
        None,
        bridge.project(project_id)["root"],
    )
    tools = await run(mcp_registry.probe_tools, resolved["command"], resolved["args"])
    return JSONResponse({**resolved, "tools": tools})


@endpoint
async def install_mcp_by_name(request: Request):
    bridge = bridge_of(request)
    project_id = request.path_params["project_id"]
    body = await _body(request)
    name = (body.get("name") or "").strip()
    if not name:
        raise BridgeError("Enter an MCP server name")
    tools = body.get("tools")
    if not isinstance(tools, list) or not tools or any(not isinstance(t, str) for t in tools):
        raise BridgeError("Select at least one MCP tool to enable")
    result = await run(
        bridge.install_mcp_server,
        project_id,
        name,
        (body.get("command") or "").strip() or None,
        None,
        tools,
    )
    return JSONResponse(result)


@endpoint
async def remove_mcp(request: Request):
    bridge = bridge_of(request)
    result = await run(
        bridge.remove_mcp_server,
        request.path_params["project_id"],
        request.path_params["name"],
    )
    return JSONResponse(result)


@endpoint
async def installed_mcp_tools(request: Request):
    bridge = bridge_of(request)
    project_id = request.path_params["project_id"]
    name = request.path_params["name"]

    def describe():
        spec = bridge._installed_mcp(project_id, name)
        allowed = set(spec["allowed_tools"])
        return [
            tool
            for tool in mcp_registry.describe_tools(project_id, spec["command"], spec["args"])
            if tool["name"] in allowed
        ]

    return JSONResponse(await run(describe))


@endpoint
async def call_mcp_tool(request: Request):
    bridge = bridge_of(request)
    body = await _body(request)
    result = await run(
        bridge.call_installed_mcp_tool,
        request.path_params["project_id"],
        request.path_params["name"],
        request.path_params["tool"],
        body.get("arguments", {}),
    )
    return JSONResponse(result)


@endpoint
async def mcp_calls(request: Request):
    bridge = bridge_of(request)
    result = await run(bridge.mcp_call_log, 50)
    return JSONResponse(result)


@endpoint
async def session_info(request: Request):
    bridge = bridge_of(request)
    return JSONResponse({"home": str(bridge.home), "version": __version__})


# --- Onboarding (first-run status) ----------------------------------------------


def _next_onboarding_action(environment, tunnel, project):
    """Priority-ordered "what to do next" for the Get started page — a pure
    read of facts already exposed by `doctor`/`provider_overview`/`connect_status`,
    not a second, persisted source of setup state."""
    if not environment["python"]["available"] or not environment["git"]["available"]:
        return {"key": "install_environment"}
    if tunnel["state"] != "connected":
        return {"key": "connect_tunnel"}
    if project is None:
        return {"key": "add_project"}
    if not project["authorized"]:
        return {"key": "authorize_project"}
    if project["profile"] != "chatgpt-only":
        return {"key": "set_chatgpt_only_profile"}
    return {"key": "all_set"}


@endpoint
async def onboarding_status(request: Request):
    """Aggregates existing status sources (environment check, Tunnel connection,
    per-project profile/authorization/test-command) into one "what's left before
    your first ChatGPT task" view, instead of adding a second place that tracks
    setup progress."""
    bridge = bridge_of(request)
    manager: ConnectionManager = request.app.state.connection_manager
    tunnel_installed = await run(lambda: tunnel_client_path(bridge.home).is_file())
    environment = {
        "python": {"available": True, "version": python_version()},
        "git": await run(git_check),
    }
    tunnel = {**manager.status(), "tunnel_client_installed": tunnel_installed}

    project = None
    project_id = request.query_params.get("project_id")
    if project_id:
        overview = await run(bridge.provider_overview, project_id)
        project = {
            "profile": overview["profile"],
            "authorized": overview["read_only_status"]["project_access"]["mode"] == "read-write",
            "access_mode": overview["read_only_status"]["project_access"]["mode"],
            "test_command_configured": bool(overview["editable"]["test_command"]),
        }

    return JSONResponse(
        {
            "environment": environment,
            "tunnel": tunnel,
            "project": project,
            "next_action": _next_onboarding_action(environment, tunnel, project),
        }
    )


# --- Connect (ChatGPT Tunnel) ----------------------------------------------------


@endpoint
async def connect_status(request: Request):
    """Everything cheap and local — no subprocess spawn — so the Connect page can
    render immediately. `provider_login_status` (which shells out to `codex`/`claude`
    and can take a perceptible moment) is its own endpoint, fetched separately and
    patched in once ready, instead of blocking the whole page behind it."""
    bridge = bridge_of(request)
    manager: ConnectionManager = request.app.state.connection_manager
    remembered, tunnel_installed = await asyncio.gather(
        run(remembered_tunnels, bridge.home),
        run(lambda: tunnel_client_path(bridge.home).is_file()),
    )
    return JSONResponse(
        {
            **manager.status(),
            "remembered_tunnels": remembered,
            "saved_connections": await run(connection_profiles, bridge.home),
            "tunnel_client_installed": tunnel_installed,
        }
    )


@endpoint
async def connect_providers(request: Request):
    return JSONResponse({"providers": await run(provider_login_status)})


def _provider_login_manager(request: Request) -> ProviderLoginManager:
    provider = request.path_params["provider"]
    managers = request.app.state.provider_login_managers
    if provider not in managers:
        raise BridgeError("Unsupported provider: use codex or claude")
    return managers[provider]


@endpoint
async def provider_install(request: Request):
    provider = _provider_login_manager(request).provider
    path = await run(ensure_installed, provider)
    return JSONResponse({"provider": provider, "path": path})


@endpoint
async def provider_uninstall(request: Request):
    provider = _provider_login_manager(request).provider
    return JSONResponse(await run(uninstall_provider, provider))


@endpoint
async def provider_login_start(request: Request):
    manager = _provider_login_manager(request)
    try:
        path = native_executable(manager.provider)
    except BridgeError:
        raise BridgeError(f"{manager.provider} is not installed yet — install it first") from None
    await manager.start(path)
    return JSONResponse(manager.status())


@endpoint
async def provider_login_status_view(request: Request):
    return JSONResponse(_provider_login_manager(request).status())


@endpoint
async def provider_login_input(request: Request):
    manager = _provider_login_manager(request)
    body = await _body(request)
    text = body.get("text", "")
    await manager.send_input(text)
    return JSONResponse({"ok": True})


@endpoint
async def provider_login_cancel(request: Request):
    manager = _provider_login_manager(request)
    await manager.cancel()
    return JSONResponse(manager.status())


@endpoint
async def connect_logs(request: Request):
    manager: ConnectionManager = request.app.state.connection_manager
    return JSONResponse({"lines": list(manager.log)})


@endpoint
async def connect_start(request: Request):
    bridge = bridge_of(request)
    manager: ConnectionManager = request.app.state.connection_manager
    if manager.state in {"starting", "connected", "stopping"}:
        raise BridgeError("Already connected; stop the current connection first")
    body = await _body(request)
    profile_id = body.get("profile_id")
    saved = get_connection_profile(bridge.home, profile_id) if profile_id else None
    if profile_id and not saved:
        raise BridgeError("Saved connection not found")
    if saved and body.get("tunnel_id") and body["tunnel_id"] != saved["tunnel_id"]:
        raise BridgeError("Save the changed Tunnel ID before connecting")
    tunnel_id = body.get("tunnel_id") or (saved or {}).get("tunnel_id", "")
    api_key = body.get("api_key") or (
        saved["api_key"] if saved else load_tunnel_key(bridge.home, tunnel_id)
    )
    if not api_key:
        raise BridgeError("No runtime API key given or saved for this tunnel")
    prepared = await run(prepare_tunnel_connection, bridge.home, tunnel_id, api_key)
    if body.get("remember"):
        if profile_id or body.get("name"):
            await run(
                save_connection_profile,
                bridge.home,
                tunnel_id,
                api_key,
                body.get("name") or (saved or {}).get("name"),
                profile_id,
            )
        else:
            await run(save_tunnel_key, bridge.home, tunnel_id, api_key)
    await manager.start(
        prepared["client"], prepared["profile"], prepared["profile_dir"], prepared["env"], tunnel_id
    )
    return JSONResponse(manager.status())


@endpoint
async def connect_stop(request: Request):
    manager: ConnectionManager = request.app.state.connection_manager
    await manager.stop()
    return JSONResponse(manager.status())


@endpoint
async def connect_forget(request: Request):
    bridge = bridge_of(request)
    body = await _body(request)
    tunnel_id = body.get("tunnel_id", "")
    profile_id = body.get("profile_id")
    forgotten = (
        await run(forget_connection_profile, bridge.home, profile_id)
        if profile_id
        else await run(forget_tunnel_key, bridge.home, tunnel_id)
    )
    return JSONResponse({"tunnel_id": tunnel_id, "forgotten": forgotten})


@endpoint
async def connect_save(request: Request):
    bridge = bridge_of(request)
    body = await _body(request)
    saved = (
        get_connection_profile(bridge.home, body.get("profile_id"))
        if body.get("profile_id")
        else None
    )
    if body.get("profile_id") and not saved:
        raise BridgeError("Saved connection not found")
    tunnel_id = body.get("tunnel_id") or (saved or {}).get("tunnel_id", "")
    api_key = body.get("api_key") or (saved or {}).get("api_key")
    if not api_key:
        raise BridgeError("Runtime API key is required")
    profile = await run(
        save_connection_profile,
        bridge.home,
        tunnel_id,
        api_key,
        body.get("name") or (saved or {}).get("name"),
        body.get("profile_id"),
    )
    return JSONResponse(profile)


ROUTES = [
    Route("/api/session", session_info),
    Route("/api/docs/{language}/{slug}", documentation, methods=["GET"]),
    Route("/api/onboarding/status", onboarding_status, methods=["GET"]),
    Route("/api/connect/status", connect_status, methods=["GET"]),
    Route("/api/connect/providers", connect_providers, methods=["GET"]),
    Route("/api/connect/providers/{provider}/install", provider_install, methods=["POST"]),
    Route("/api/connect/providers/{provider}/uninstall", provider_uninstall, methods=["POST"]),
    Route("/api/connect/providers/{provider}/login", provider_login_start, methods=["POST"]),
    Route("/api/connect/providers/{provider}/login", provider_login_status_view, methods=["GET"]),
    Route("/api/connect/providers/{provider}/login/input", provider_login_input, methods=["POST"]),
    Route("/api/connect/providers/{provider}/login/cancel", provider_login_cancel, methods=["POST"]),
    Route("/api/connect/logs", connect_logs, methods=["GET"]),
    Route("/api/connect/start", connect_start, methods=["POST"]),
    Route("/api/connect/save", connect_save, methods=["POST"]),
    Route("/api/connect/stop", connect_stop, methods=["POST"]),
    Route("/api/connect/forget", connect_forget, methods=["POST"]),
    Route("/api/browse", browse),
    Route("/api/projects", list_projects, methods=["GET"]),
    Route("/api/dashboard", dashboard, methods=["GET"]),
    Route("/api/dashboard/account-usage", dashboard_account_usage, methods=["GET"]),
    Route("/api/dashboard/claude-usage", dashboard_claude_usage, methods=["GET"]),
    Route("/api/dashboard/claude-usage/enable", dashboard_claude_usage_enable, methods=["POST"]),
    Route("/api/dashboard/claude-usage/probe", dashboard_claude_usage_probe, methods=["POST"]),
    Route("/api/dashboard/claude-usage/disable", dashboard_claude_usage_disable, methods=["POST"]),
    Route("/api/dashboard/reliability", agent_reliability, methods=["GET"]),
    Route("/api/project-groups", get_project_groups, methods=["GET"]),
    Route("/api/project-groups", update_project_groups, methods=["POST"]),
    Route("/api/projects/add", add_project, methods=["POST"]),
    Route("/api/projects/create", create_project, methods=["POST"]),
    Route("/api/projects/import", import_project, methods=["POST"]),
    Route("/api/projects/{project_id}/export", export_project, methods=["GET"]),
    Route("/api/projects/{project_id}/authorize", authorize_project, methods=["POST"]),
    Route("/api/projects/{project_id}/authorize-preview", authorize_preview, methods=["GET"]),
    Route("/api/projects/{project_id}/revoke", revoke_project, methods=["POST"]),
    Route("/api/projects/{project_id}/delete", delete_project, methods=["POST"]),
    Route("/api/projects/{project_id}/delete-preview", deregister_preview, methods=["GET"]),
    Route("/api/projects/{project_id}/remove", remove_project, methods=["POST"]),
    Route("/api/projects/{project_id}/remove-preview", remove_preview, methods=["GET"]),
    Route("/api/projects/{project_id}/clear-history", clear_history, methods=["POST"]),
    Route(
        "/api/projects/{project_id}/clear-history-preview",
        clear_history_preview,
        methods=["GET"],
    ),
    Route("/api/projects/{project_id}/settings", get_settings, methods=["GET"]),
    Route("/api/projects/{project_id}/config/role", set_role, methods=["POST"]),
    Route("/api/projects/{project_id}/config/role-name", set_role_name, methods=["POST"]),
    Route("/api/projects/{project_id}/config/role-avatar", set_role_avatar, methods=["POST"]),
    Route("/api/projects/{project_id}/config/profile", set_profile, methods=["POST"]),
    Route("/api/projects/{project_id}/config/language", set_language, methods=["POST"]),
    Route("/api/projects/{project_id}/config/test-command", set_test_command, methods=["POST"]),
    Route("/api/projects/{project_id}/config/runtime", set_runtime, methods=["POST"]),
    Route("/api/projects/{project_id}/config/runtime/check", check_runtime, methods=["POST"]),
    Route("/api/projects/{project_id}/config/meeting-mode", set_meeting_mode, methods=["POST"]),
    Route("/api/projects/{project_id}/config/workflow", set_workflow, methods=["POST"]),
    Route("/api/projects/{project_id}/config/review-gate", set_review_gate, methods=["POST"]),
    Route("/api/projects/{project_id}/workflows", list_workflows, methods=["GET"]),
    Route("/api/projects/{project_id}/workflows", save_workflow, methods=["POST"]),
    Route(
        "/api/projects/{project_id}/workflows/{workflow_id}/delete", delete_workflow,
        methods=["POST"],
    ),
    Route(
        "/api/projects/{project_id}/config/workflow-selection", set_workflow_selection,
        methods=["POST"],
    ),
    Route(
        "/api/projects/{project_id}/config/evaluation-engine",
        set_evaluation_engine, methods=["POST"],
    ),
    Route("/api/projects/{project_id}/context", get_context, methods=["GET"]),
    Route("/api/projects/{project_id}/mcp", list_mcp, methods=["GET"]),
    Route("/api/projects/{project_id}/mcp/probe", probe_mcp, methods=["POST"]),
    Route("/api/projects/{project_id}/mcp/install", install_mcp, methods=["POST"]),
    Route("/api/projects/{project_id}/mcp/probe-by-name", probe_mcp_by_name, methods=["POST"]),
    Route("/api/projects/{project_id}/mcp/install-by-name", install_mcp_by_name, methods=["POST"]),
    Route("/api/projects/{project_id}/mcp/{name}/remove", remove_mcp, methods=["POST"]),
    Route("/api/projects/{project_id}/mcp/{name}/tools", installed_mcp_tools, methods=["GET"]),
    Route(
        "/api/projects/{project_id}/mcp/{name}/tools/{tool}/call",
        call_mcp_tool,
        methods=["POST"],
    ),
    Route("/api/projects/{project_id}/tasks", list_tasks, methods=["GET"]),
    Route("/api/projects/{project_id}/tasks/{task_id}", task_status, methods=["GET"]),
    Route("/api/projects/{project_id}/tasks/{task_id}/result", task_result, methods=["GET"]),
    Route("/api/projects/{project_id}/tasks/{task_id}/usage", task_usage, methods=["GET"]),
    Route("/api/projects/{project_id}/tasks/{task_id}/evaluations", task_evaluations, methods=["GET"]),
    Route("/api/projects/{project_id}/tasks/{task_id}/evaluate", task_evaluate, methods=["POST"]),
    Route("/api/projects/{project_id}/tasks/{task_id}/reevaluate", task_reevaluate, methods=["POST"]),
    Route("/api/projects/{project_id}/tasks/{task_id}/approvals", task_approvals, methods=["GET"]),
    Route("/api/projects/{project_id}/tasks/{task_id}/meeting", task_meeting, methods=["GET"]),
    Route("/api/projects/{project_id}/tasks/{task_id}/handoff", task_handoff, methods=["GET"]),
    Route("/api/projects/{project_id}/tasks/{task_id}/continuation", task_continuation, methods=["GET"]),
    Route("/api/projects/{project_id}/tasks/{task_id}/timeline", task_timeline, methods=["GET"]),
    Route("/api/projects/{project_id}/tasks/{task_id}/artifacts", task_artifacts, methods=["GET"]),
    Route("/api/projects/{project_id}/tasks/{task_id}/pause", task_pause, methods=["POST"]),
    Route("/api/projects/{project_id}/tasks/{task_id}/resume", task_resume, methods=["POST"]),
    Route("/api/projects/{project_id}/tasks/{task_id}/stop", task_stop, methods=["POST"]),
    Route("/api/projects/{project_id}/tasks/{task_id}/messages", task_message, methods=["POST"]),
    Route("/api/projects/{project_id}/tasks/{task_id}/restart", task_restart, methods=["POST"]),
    Route("/api/projects/{project_id}/tasks/{task_id}/delete", task_delete, methods=["POST"]),
    Route(
        "/api/projects/{project_id}/tasks/{task_id}/gate-decision", task_gate_decision,
        methods=["POST"],
    ),
    Route("/api/skills", list_skills, methods=["GET"]),
    Route("/api/skills/discover", discover_skills, methods=["GET"]),
    Route("/api/skills/install-discovered", install_discovered_skill, methods=["POST"]),
    Route("/api/skills/{skill_id}/remove", remove_global_skill, methods=["POST"]),
    Route("/api/mcp/calls", mcp_calls, methods=["GET"]),
    Mount("/", app=StaticFiles(directory=STATIC_DIR, html=True), name="static"),
]


async def _token_guard(request: Request, call_next):
    if request.url.path.startswith("/api/"):
        token = request.headers.get("x-ccc-ui-token", "")
        if not hmac.compare_digest(token, request.app.state.ui_token):
            return JSONResponse(
                {"error": "Missing or invalid local UI session token"}, status_code=401
            )
    response = await call_next(request)
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    # This console's own frontend (app.js/app.css/i18n.js) changes between ccc-mcp
    # versions; force revalidation on every load instead of letting the browser's
    # heuristic cache (no explicit Cache-Control, so freshness is guessed from
    # Last-Modified) serve a stale UI after an upgrade. Harmless locally — every
    # request is sub-millisecond loopback, not a real network round trip.
    response.headers["Cache-Control"] = "no-cache"
    return response


def create_app(home=None, token=None):
    app = Starlette(routes=ROUTES)
    app.state.bridge = CCCMCP(home)
    app.state.ui_token = token or secrets.token_urlsafe(32)
    app.state.connection_manager = ConnectionManager()
    app.state.provider_login_managers = {
        "codex": ProviderLoginManager("codex"),
        "claude": ProviderLoginManager("claude"),
    }
    app.add_middleware(BaseHTTPMiddleware, dispatch=_token_guard)
    return app
