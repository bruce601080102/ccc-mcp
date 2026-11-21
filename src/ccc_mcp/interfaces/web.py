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
import secrets
from pathlib import Path

from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

from .. import __version__
from ..adapters.agent_setup import LOGIN_ARGS, ensure_installed, uninstall_provider
from ..adapters.agents.base import native_executable
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
from ..core.flows import BUILTIN_FLOWS, goal_blocker
from ..infra.errors import BridgeError, VersionConflict
from ..service import CCCMCP

STATIC_DIR = Path(__file__).parent / "webui_static"


async def run(fn, *args, **kwargs):
    """Run bridge logic off the event loop, mirroring interfaces/server.py's `call`."""
    return await asyncio.to_thread(fn, *args, **kwargs)


def endpoint(fn=None, *, conflict_status=400):
    if fn is None:
        return lambda handler: endpoint(handler, conflict_status=conflict_status)

    async def handler(request: Request):
        try:
            return await fn(request)
        except VersionConflict as exc:
            return JSONResponse({"error": str(exc)}, status_code=conflict_status)
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


def _only(body: dict, *allowed: str) -> dict:
    """Refuse fields the route does not know, so a typo or an injected field is an error and
    not silently ignored."""
    unknown = sorted(set(body) - set(allowed))
    if unknown:
        raise BridgeError(f"Unknown fields: {unknown}")
    return body


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
    project_id = request.path_params["project_id"]

    def overview():
        with bridge.lock(project_id):
            return bridge.provider_overview(project_id), bridge.workflow_catalog(project_id)

    result, catalog = await run(overview)
    result["workflow_choices"] = [
        {"id": f["id"], "name": f["name"], "legacy": bool(f.get("legacy")),
         "builtin": f["id"] in BUILTIN_FLOWS, "summary": f.get("summary", ""),
         "goal_ready": goal_blocker(f) is None,
         "steps": [{"role": s["role"], "role_ref": s.get("role_ref")} for s in f["steps"]]}
        for f in catalog["workflows"]
    ]
    result["custom_roles"] = catalog["custom_roles"]
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


# --- Role studio ------------------------------------------------------------------
#
# Custom roles are created and edited here and nowhere else: the console's session token is the
# local authorization. There is deliberately no MCP tool, CLI command or settings.json route for
# any of this, so a ChatGPT conversation or a file in the repository cannot write a role's
# instructions or accept a change to a role's fixed contract.


@endpoint
async def task_contract(request: Request):
    bridge = bridge_of(request)
    return JSONResponse(await run(
        bridge.task_contract_status,
        request.path_params["project_id"],
        request.path_params["task_id"],
    ))


@endpoint
async def accept_task_contract(request: Request):
    bridge = bridge_of(request)
    body = await _body(request)
    _only(body, "step_id", "snapshot_id", "contract_hash", "confirmation")
    return JSONResponse(await run(
        bridge.accept_contract_drift_web,
        request.path_params["project_id"],
        request.path_params["task_id"],
        body.get("step_id"),
        body.get("snapshot_id"),
        body.get("contract_hash"),
        body.get("confirmation"),
    ))


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


# --- Imported custom roles: the one place a person trusts one ---------------------------------
# Roles are no longer created in the console. A role that arrives in an imported backup stays off
# until a person at this console types the phrase for that exact version; nothing else can switch it on.


@endpoint(conflict_status=409)
async def save_team_settings(request: Request):
    body = _only(await _body(request), "revision", "changes")
    return JSONResponse(await run(
        bridge_of(request).save_team_settings, request.path_params["project_id"],
        body.get("revision"), body.get("changes"),
    ))


@endpoint
async def set_role(request: Request):
    bridge = bridge_of(request)
    body = _only(await _body(request), "role", "provider", "model", "effort", "quota_fallback")
    return JSONResponse(await run(
        bridge.configure_role, request.path_params["project_id"], body.get("role"),
        body.get("provider"), body.get("model") or None, body.get("effort") or None,
        body.get("quota_fallback") or None,
    ))


@endpoint
async def set_workflow(request: Request):
    bridge = bridge_of(request)
    body = _only(await _body(request), "workflow")
    return JSONResponse(await run(
        bridge.configure_workflow, request.path_params["project_id"], body.get("workflow")))


@endpoint
async def set_review_gate(request: Request):
    bridge = bridge_of(request)
    body = _only(await _body(request), "ar_strictness", "max_rounds", "escalation")
    return JSONResponse(await run(
        bridge.configure_review_gate, request.path_params["project_id"], body.get("ar_strictness"),
        body.get("max_rounds"), body.get("escalation")))


# Your own roles and workflows. One compact surface: a role is a name, a base role (which alone decides
# what it may do) and its instructions; a workflow is a name and an ordered list of roles.


@endpoint
async def get_role(request: Request):
    bridge = bridge_of(request)
    project_id, role_ref = request.path_params["project_id"], request.path_params["role_ref"]
    role = await run(bridge.get_custom_role, project_id, role_ref)
    return JSONResponse({"role": role, "usage": await run(bridge.custom_role_usage, project_id, role_ref)})


@endpoint
async def create_role(request: Request):
    bridge = bridge_of(request)
    body = _only(await _body(request), "name", "base_role", "instructions", "runtime")
    instructions = body.get("instructions") or ""
    return JSONResponse(await run(
        bridge.create_custom_role, request.path_params["project_id"], body.get("name"),
        body.get("base_role"), prompt={"mode": "freeform", "freeform": instructions},
        runtime=body.get("runtime"), actor="local-console",
    ))


@endpoint(conflict_status=409)
async def update_role(request: Request):
    bridge = bridge_of(request)
    body = _only(await _body(request), "expected_version", "name", "instructions", "runtime")
    changes = {}
    if "name" in body:
        changes["name"] = body["name"]
    if "instructions" in body:
        changes["prompt"] = {"mode": "freeform", "freeform": body["instructions"]}
    if "runtime" in body:
        changes["runtime"] = body["runtime"]
    return JSONResponse(await run(
        bridge.update_custom_role, request.path_params["project_id"], request.path_params["role_ref"],
        changes, expected_version=body.get("expected_version"), actor="local-console",
    ))


@endpoint
async def set_role_status(request: Request):
    bridge = bridge_of(request)
    body = _only(await _body(request), "status")
    return JSONResponse(await run(
        bridge.set_custom_role_status, request.path_params["project_id"],
        request.path_params["role_ref"], body.get("status"), actor="local-console",
    ))


@endpoint(conflict_status=409)
async def save_workflow(request: Request):
    bridge = bridge_of(request)
    body = _only(await _body(request), "steps", "name", "id", "revision", "select")
    return JSONResponse(await run(
        bridge.save_workflow, request.path_params["project_id"], body.get("steps"), body.get("name"),
        body.get("id"), revision=body.get("revision"), select=body.get("select") is True,
    ))


@endpoint(conflict_status=409)
async def delete_workflow(request: Request):
    bridge = bridge_of(request)
    body = _only(await _body(request), "revision")
    return JSONResponse(await run(
        bridge.delete_workflow, request.path_params["project_id"], request.path_params["workflow_id"],
        revision=body.get("revision")))


@endpoint
async def pending_roles(request: Request):
    bridge = bridge_of(request)
    project_id = request.path_params["project_id"]
    ids = await run(bridge.pending_imported_roles, project_id)
    roles = []
    for role_ref in ids:
        review = await run(bridge.imported_role_review, project_id, role_ref)
        definition = review["role"]["definition"]
        prompt = definition["prompt"]
        roles.append({
            "id": role_ref, "name": definition["name"], "version": review["confirmation"]["version"],
            "permissions": review["permissions"], "source": review["source"],
            "instruction": prompt["freeform"] if prompt["mode"] == "freeform" else prompt["sections"],
            "version_sha256": review["confirmation"]["version_sha256"],
            "phrase": review["confirmation"]["phrase"],
        })
    return JSONResponse({"pending": roles})


@endpoint
async def confirm_role_import(request: Request):
    """Switch on an imported role. Only this route can: the person must hold the console's session
    token and type the phrase for this exact role version."""
    bridge = bridge_of(request)
    body = _only(await _body(request), "version", "version_sha256", "confirmation")
    return JSONResponse(await run(
        bridge.confirm_imported_role_web, request.path_params["project_id"],
        request.path_params["role_ref"], body.get("version"), body.get("version_sha256"), [],
        body.get("confirmation"),
    ))


# --- Context / Skills / MCP ------------------------------------------------------


@endpoint
async def session_info(request: Request):
    bridge = bridge_of(request)
    return JSONResponse({"home": str(bridge.home), "version": __version__})


# --- Onboarding (first-run status) ----------------------------------------------


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


# Goals (Stage A): plan and approve only. Approval is the one act here that needs the operator's
# typed phrase on top of the console's session token, exactly like authorizing a project.
@endpoint
async def work_get(request: Request):
    """The Tasks page's data: work groups and single tasks. With `?rev=` equal to the current
    revision nothing is sent back but the revision, so the page can poll cheaply and patch only
    when something moved."""
    bridge = bridge_of(request)
    overview = await run(bridge.work_overview, request.path_params["project_id"])
    if request.query_params.get("rev") == overview["revision"]:
        return JSONResponse({"revision": overview["revision"], "unchanged": True})
    return JSONResponse(overview)


@endpoint
async def work_start(request: Request):
    """Start a work group from the console: the same call ChatGPT's start_goal makes."""
    bridge = bridge_of(request)
    body = _only(await _body(request), "requirement", "title", "request_key", "confirm_new")
    return JSONResponse(await run(
        bridge.start_goal_work, request.path_params["project_id"], body.get("requirement", ""),
        body.get("title"), body.get("request_key"), bool(body.get("confirm_new")), None, True,
    ))


@endpoint
async def goal_rename(request: Request):
    bridge = bridge_of(request)
    body = _only(await _body(request), "title")
    return JSONResponse(await run(
        bridge.rename_goal, request.path_params["project_id"], request.path_params["goal_id"],
        body.get("title", ""),
    ))


@endpoint
async def goal_get(request: Request):
    bridge = bridge_of(request)
    return JSONResponse(await run(
        bridge.get_goal, request.path_params["project_id"], request.path_params["goal_id"]
    ))


@endpoint
async def goal_plan(request: Request):
    bridge = bridge_of(request)
    body = _only(await _body(request), "change_request", "base_version", "provider")
    if body.get("provider") not in (None, "codex", "claude"):
        raise BridgeError("provider must be codex or claude")
    return JSONResponse(await run(
        bridge.request_goal_plan, request.path_params["project_id"],
        request.path_params["goal_id"], body.get("change_request"), body.get("base_version"),
        body.get("provider"),
    ))


@endpoint
async def goal_plan_cancel(request: Request):
    bridge = bridge_of(request)
    return JSONResponse(await run(
        bridge.cancel_planning_run, request.path_params["project_id"],
        request.path_params["goal_id"],
    ))


@endpoint
async def goal_import(request: Request):
    bridge = bridge_of(request)
    body = _only(await _body(request), "plan", "base_version", "note")
    return JSONResponse(await run(
        bridge.import_goal_plan, request.path_params["project_id"],
        request.path_params["goal_id"], body.get("plan"), body.get("base_version"),
        body.get("note", ""),
    ))


@endpoint
async def goal_approve(request: Request):
    bridge = bridge_of(request)
    body = _only(await _body(request), "version", "sha256", "confirm")
    version = body.get("version")
    if isinstance(version, bool) or not isinstance(version, int):
        raise BridgeError("version must be a whole number")
    return JSONResponse(await run(
        bridge.approve_goal_web, request.path_params["project_id"],
        request.path_params["goal_id"], version, body.get("sha256", ""), body.get("confirm", ""),
    ))


@endpoint
async def goal_cancel(request: Request):
    bridge = bridge_of(request)
    body = _only(await _body(request), "reason")
    return JSONResponse(await run(
        bridge.cancel_goal, request.path_params["project_id"], request.path_params["goal_id"],
        body.get("reason", ""),
    ))


# Phase runs (Stage B). Starting, pausing, resuming and cancelling go through the session token like
# any change; accepting criteria no command can judge also needs the typed phrase. Nothing here can
# raise a budget, choose a command, or override the host-test safety refusal (that is a CLI-only
# operator flag), and no route starts the next phase.
@endpoint
async def phase_run_get(request: Request):
    bridge = bridge_of(request)
    return JSONResponse(await run(
        bridge.get_phase_run, request.path_params["project_id"], request.path_params["run_id"]
    ))


@endpoint
async def phase_run_pause(request: Request):
    bridge = bridge_of(request)
    body = _only(await _body(request), "reason")
    return JSONResponse(await run(
        bridge.pause_phase_run, request.path_params["project_id"], request.path_params["run_id"],
        body.get("reason", ""),
    ))


@endpoint
async def phase_run_resume(request: Request):
    bridge = bridge_of(request)
    body = _only(await _body(request), "note", "acknowledge")
    return JSONResponse(await run(
        bridge.resume_phase_run, request.path_params["project_id"], request.path_params["run_id"],
        body.get("note", ""), body.get("acknowledge"),
    ))


@endpoint
async def phase_run_cancel(request: Request):
    bridge = bridge_of(request)
    body = _only(await _body(request), "reason")
    return JSONResponse(await run(
        bridge.cancel_phase_run, request.path_params["project_id"], request.path_params["run_id"],
        body.get("reason", ""),
    ))


@endpoint
async def phase_run_accept(request: Request):
    bridge = bridge_of(request)
    body = _only(await _body(request), "confirm")
    return JSONResponse(await run(
        bridge.accept_phase_web, request.path_params["project_id"], request.path_params["run_id"],
        body.get("confirm", ""),
    ))


@endpoint
async def phase_run_accept_preview(request: Request):
    bridge = bridge_of(request)
    return JSONResponse(await run(
        bridge.accept_phase_description, request.path_params["project_id"],
        request.path_params["run_id"],
    ))


@endpoint
async def phase_runs_recover(request: Request):
    bridge = bridge_of(request)
    return JSONResponse(await run(bridge.recover_phase_runs, request.path_params["project_id"]))


# Goal runs (Stage C): the whole approved goal, phase by phase. Same rules as the phase routes: every
# change goes through the session token, accepting what no command can judge needs the typed phrase, and
# nothing here can raise a budget, choose a command, approve a plan or override the host-test safety
# refusal (a CLI-only operator flag). A revised plan is drafted here but approved only by a person at
# the plan's own approval step.
@endpoint
async def goal_run_start(request: Request):
    bridge = bridge_of(request)
    body = _only(await _body(request), "pm_provider")
    if body.get("pm_provider") not in (None, "codex", "claude"):
        raise BridgeError("pm_provider must be codex or claude")
    return JSONResponse(await run(
        bridge.start_goal_run, request.path_params["project_id"], request.path_params["goal_id"],
        adapter="real", pm_provider=body.get("pm_provider"),
    ))


@endpoint
async def goal_run_get(request: Request):
    bridge = bridge_of(request)
    return JSONResponse(await run(
        bridge.get_goal_run, request.path_params["project_id"], request.path_params["run_id"]
    ))


@endpoint
async def goal_run_pause(request: Request):
    bridge = bridge_of(request)
    body = _only(await _body(request), "reason")
    return JSONResponse(await run(
        bridge.pause_goal_run, request.path_params["project_id"], request.path_params["run_id"],
        body.get("reason", ""),
    ))


@endpoint
async def goal_run_resume(request: Request):
    bridge = bridge_of(request)
    body = _only(await _body(request), "note", "acknowledge")
    return JSONResponse(await run(
        bridge.resume_goal_run, request.path_params["project_id"], request.path_params["run_id"],
        body.get("note", ""), body.get("acknowledge"),
    ))


@endpoint
async def goal_run_cancel(request: Request):
    bridge = bridge_of(request)
    body = _only(await _body(request), "reason")
    return JSONResponse(await run(
        bridge.cancel_goal_run, request.path_params["project_id"], request.path_params["run_id"],
        body.get("reason", ""),
    ))


@endpoint
async def goal_run_accept(request: Request):
    bridge = bridge_of(request)
    body = _only(await _body(request), "confirm")
    return JSONResponse(await run(
        bridge.accept_goal_web, request.path_params["project_id"], request.path_params["run_id"],
        body.get("confirm", ""),
    ))


@endpoint
async def goal_run_accept_preview(request: Request):
    bridge = bridge_of(request)
    return JSONResponse(await run(
        bridge.accept_goal_description, request.path_params["project_id"],
        request.path_params["run_id"],
    ))


@endpoint
async def goal_run_revise(request: Request):
    bridge = bridge_of(request)
    body = _only(await _body(request), "change_request", "provider")
    if body.get("provider") not in (None, "codex", "claude"):
        raise BridgeError("provider must be codex or claude")
    return JSONResponse(await run(
        bridge.draft_goal_revision, request.path_params["project_id"], request.path_params["run_id"],
        body.get("change_request"), provider=body.get("provider"),
    ))


@endpoint
async def goal_runs_recover(request: Request):
    bridge = bridge_of(request)
    return JSONResponse(await run(bridge.recover_goal_runs, request.path_params["project_id"]))


ROUTES = [
    Route("/api/session", session_info),
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
    Route("/api/projects/{project_id}/config/profile", set_profile, methods=["POST"]),
    Route("/api/projects/{project_id}/config/language", set_language, methods=["POST"]),
    Route("/api/projects/{project_id}/config/test-command", set_test_command, methods=["POST"]),
    Route("/api/projects/{project_id}/config/runtime", set_runtime, methods=["POST"]),
    Route("/api/projects/{project_id}/config/runtime/check", check_runtime, methods=["POST"]),
    Route("/api/projects/{project_id}/tasks", list_tasks, methods=["GET"]),
    Route("/api/projects/{project_id}/tasks/{task_id}", task_status, methods=["GET"]),
    Route("/api/projects/{project_id}/tasks/{task_id}/result", task_result, methods=["GET"]),
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
    Route("/api/projects/{project_id}/config/team", save_team_settings, methods=["POST"]),
    Route("/api/projects/{project_id}/config/role", set_role, methods=["POST"]),
    Route("/api/projects/{project_id}/config/workflow", set_workflow, methods=["POST"]),
    Route("/api/projects/{project_id}/config/review-gate", set_review_gate, methods=["POST"]),
    Route("/api/projects/{project_id}/roles/pending", pending_roles, methods=["GET"]),
    Route("/api/projects/{project_id}/roles", create_role, methods=["POST"]),
    Route("/api/projects/{project_id}/roles/{role_ref}", get_role, methods=["GET"]),
    Route("/api/projects/{project_id}/roles/{role_ref}/update", update_role, methods=["POST"]),
    Route("/api/projects/{project_id}/roles/{role_ref}/status", set_role_status, methods=["POST"]),
    Route("/api/projects/{project_id}/workflows", save_workflow, methods=["POST"]),
    Route("/api/projects/{project_id}/workflows/{workflow_id}/delete", delete_workflow, methods=["POST"]),
    Route(
        "/api/projects/{project_id}/roles/{role_ref}/confirm-import", confirm_role_import,
        methods=["POST"],
    ),
    Route("/api/projects/{project_id}/work", work_get, methods=["GET"]),
    Route("/api/projects/{project_id}/work/goals", work_start, methods=["POST"]),
    Route("/api/projects/{project_id}/goals/{goal_id}/rename", goal_rename, methods=["POST"]),
    Route("/api/projects/{project_id}/goals/{goal_id}", goal_get, methods=["GET"]),
    Route("/api/projects/{project_id}/goals/{goal_id}/plan", goal_plan, methods=["POST"]),
    Route("/api/projects/{project_id}/goals/{goal_id}/plan/cancel", goal_plan_cancel,
          methods=["POST"]),
    Route("/api/projects/{project_id}/goals/{goal_id}/import", goal_import, methods=["POST"]),
    Route("/api/projects/{project_id}/goals/{goal_id}/approve", goal_approve, methods=["POST"]),
    Route("/api/projects/{project_id}/goals/{goal_id}/cancel", goal_cancel, methods=["POST"]),
    Route("/api/projects/{project_id}/goals/{goal_id}/run", goal_run_start, methods=["POST"]),
    Route("/api/projects/{project_id}/goal-runs/recover", goal_runs_recover, methods=["POST"]),
    Route("/api/projects/{project_id}/goal-runs/{run_id}", goal_run_get, methods=["GET"]),
    Route("/api/projects/{project_id}/goal-runs/{run_id}/pause", goal_run_pause, methods=["POST"]),
    Route("/api/projects/{project_id}/goal-runs/{run_id}/resume", goal_run_resume, methods=["POST"]),
    Route("/api/projects/{project_id}/goal-runs/{run_id}/cancel", goal_run_cancel, methods=["POST"]),
    Route("/api/projects/{project_id}/goal-runs/{run_id}/accept", goal_run_accept, methods=["POST"]),
    Route("/api/projects/{project_id}/goal-runs/{run_id}/accept-preview", goal_run_accept_preview,
          methods=["GET"]),
    Route("/api/projects/{project_id}/goal-runs/{run_id}/revise", goal_run_revise, methods=["POST"]),
    Route("/api/projects/{project_id}/phase-runs/recover", phase_runs_recover, methods=["POST"]),
    Route("/api/projects/{project_id}/phase-runs/{run_id}", phase_run_get, methods=["GET"]),
    Route("/api/projects/{project_id}/phase-runs/{run_id}/pause", phase_run_pause, methods=["POST"]),
    Route("/api/projects/{project_id}/phase-runs/{run_id}/resume", phase_run_resume,
          methods=["POST"]),
    Route("/api/projects/{project_id}/phase-runs/{run_id}/cancel", phase_run_cancel,
          methods=["POST"]),
    Route("/api/projects/{project_id}/phase-runs/{run_id}/accept", phase_run_accept,
          methods=["POST"]),
    Route("/api/projects/{project_id}/phase-runs/{run_id}/accept-preview", phase_run_accept_preview,
          methods=["GET"]),
    Route("/api/projects/{project_id}/tasks/{task_id}/contract", task_contract, methods=["GET"]),
    Route(
        "/api/projects/{project_id}/tasks/{task_id}/contract/accept", accept_task_contract,
        methods=["POST"],
    ),
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


GOAL_WATCHDOG_SECONDS = 20


def _goal_watchdog(bridge, stop):
    """While the console runs, wake any live goal run whose workers all died and that has been quiet
    for a while. It only wakes; it never recovers dead workers or answers anything a person must. A
    console that is not running does nothing, which is why `goal run-recover` exists."""
    while not stop.wait(GOAL_WATCHDOG_SECONDS):
        try:
            bridge.wake_stalled_goal_runs()
        except Exception:
            continue


def create_app(home=None, token=None, goal_watchdog=False):
    app = Starlette(routes=ROUTES)
    app.state.bridge = CCCMCP(home)
    if goal_watchdog:
        import threading

        stop = threading.Event()
        threading.Thread(
            target=_goal_watchdog, args=(app.state.bridge, stop), daemon=True,
            name="goal-run-watchdog").start()
        # Once at start: a console that was closed while a goal ran picks it up again, the same way
        # `goal run-recover` would, but only waking, never recovering dead workers by itself.
        try:
            app.state.bridge.wake_stalled_goal_runs()
        except Exception:
            pass
        app.state.goal_watchdog_stop = stop
    app.state.ui_token = token or secrets.token_urlsafe(32)
    app.state.connection_manager = ConnectionManager()
    app.state.provider_login_managers = {
        "codex": ProviderLoginManager("codex"),
        "claude": ProviderLoginManager("claude"),
    }
    app.add_middleware(BaseHTTPMiddleware, dispatch=_token_guard)
    return app
