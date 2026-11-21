"""Tests for interfaces/web.py: the local-only `ccc-mcp ui` console.

Covers the token-gated trust boundary, the add/create+enable-write combined
flow, that an existing read-only project is never silently upgraded, and that
delete/clear-history still require the exact typed confirmation text — all
through the real Starlette app, calling the same core bridge methods the CLI
and MCP server use.
"""

import asyncio
import os
import sys
from pathlib import Path

import pytest
from starlette.testclient import TestClient

from ccc_mcp import __version__
from ccc_mcp.infra.errors import BridgeError
from ccc_mcp.interfaces.web import create_app

TOKEN = "test-session-token"


@pytest.fixture
def client(tmp_path):
    app = create_app(tmp_path / "home", token=TOKEN)
    return TestClient(app, headers={"X-CCC-UI-Token": TOKEN})


def mkrepo(tmp_path, name):
    repo = tmp_path / name
    repo.mkdir()
    (repo / "README.md").write_text("x", encoding="utf-8")
    return repo


def test_session_reports_running_package_version(client):
    response = client.get("/api/session")
    assert response.status_code == 200
    assert response.json()["version"] == __version__


def test_restart_endpoint_sends_a_step_workflow_back_and_is_token_gated(tmp_path, client, human):
    repo = mkrepo(tmp_path, "redo")
    assert client.post("/api/projects/add", json={"path": str(repo), "id": "redo"}).status_code == 200
    bridge = client.app.state.bridge
    bridge.authorize_project_local("redo")
    bridge.configure_workflow("redo", "life")
    task_id = bridge.start_project_request("redo", "Plan a trip")["workstream"]["id"]
    base = f"/api/projects/redo/tasks/{task_id}"

    sent = client.post(f"{base}/restart", json={"step": 0, "content": "Start again, shorter"})
    assert sent.status_code == 200
    assert sent.json()["role"] == "PA" and sent.json()["greeting"]
    events = client.get(f"{base}/timeline").json()["events"]
    assert next(e for e in events if e["event_type"] == "role_returned")["role"] == "PA"

    assert client.post(f"{base}/restart", json={"step": 99, "content": "x"}).status_code == 400
    assert client.post(f"{base}/restart", json={"step": 0, "content": " "}).status_code == 400
    assert TestClient(client.app).post(f"{base}/restart", json={"step": 0, "content": "x"}).status_code == 401


def test_task_status_remains_available_while_project_is_locked(tmp_path, client):
    repo = mkrepo(tmp_path, "busy")
    added = client.post("/api/projects/add", json={"path": str(repo), "id": "busy"})
    assert added.status_code == 200
    bridge = client.app.state.bridge
    bridge.create_task("busy", "新增 API", ["API 回傳 200"])

    with bridge.lock("busy"):
        response = client.get("/api/projects/busy/tasks/TASK-0001")

    assert response.status_code == 200
    assert response.json()["brief"]["task_id"] == "TASK-0001"


def test_task_conversation_endpoints_are_persistent_and_token_gated(tmp_path, client):
    repo = mkrepo(tmp_path, "conversation")
    assert client.post("/api/projects/add", json={"path": str(repo), "id": "conversation"}).status_code == 200
    bridge = client.app.state.bridge
    task_id = bridge.create_task("conversation", "Fix the parser")["id"]
    base = f"/api/projects/conversation/tasks/{task_id}"
    message = "Use the existing <script>alert(1)</script> function"
    assert client.post(f"{base}/messages", json={"content": message, "pause": True}).status_code == 200
    assert client.get(base).json()["brief"]["control"]["status"] == "PAUSED"
    events = client.get(f"{base}/timeline").json()["events"]
    assert next(e for e in events if e["event_type"] == "user_message")["summary"] == message
    assert client.post(f"{base}/resume", json={}).status_code == 200
    assert client.post(f"{base}/stop", json={"reason": "Wrong goal"}).json()["status"] == "CANCELLED"
    assert client.get(base).json()["brief"]["control"]["stop_reason"] == "Wrong goal"
    assert client.post(f"{base}/messages", json={"content": "late"}).status_code == 400
    no_token = TestClient(client.app)
    for path in (base, f"{base}/timeline", f"{base}/artifacts"):
        assert no_token.get(path).status_code == 401
    for path in ("pause", "resume", "stop", "messages"):
        assert no_token.post(f"{base}/{path}", json={}).status_code == 401
    assert client.get("/api/projects/missing/tasks/TASK-0001/timeline").status_code == 400


def test_a_finished_task_can_be_deleted_from_the_console_but_a_running_one_cannot(tmp_path, client):
    repo = mkrepo(tmp_path, "removal")
    assert client.post("/api/projects/add", json={"path": str(repo), "id": "removal"}).status_code == 200
    bridge = client.app.state.bridge
    task_id = bridge.create_task("removal", "Fix the parser")["id"]
    base = f"/api/projects/removal/tasks/{task_id}"

    assert client.post(f"{base}/delete", json={}).status_code == 400
    assert [t["id"] for t in client.get("/api/projects/removal/tasks").json()] == [task_id]

    assert client.post(f"{base}/stop", json={"reason": "not needed"}).status_code == 200
    assert TestClient(client.app).post(f"{base}/delete", json={}).status_code == 401
    assert client.post(f"{base}/delete", json={}).json() == {"deleted": task_id}

    assert client.get("/api/projects/removal/tasks").json() == []
    assert client.get(base).status_code == 400
    assert bridge.create_task("removal", "Next thing")["id"] == "TASK-0002"


def test_remove_project_keeps_folder_by_default_and_checks_confirmation(tmp_path, client):
    repo = mkrepo(tmp_path, "removable")
    assert (
        client.post("/api/projects/add", json={"path": str(repo), "id": "removable"}).status_code
        == 200
    )
    preview = client.get("/api/projects/removable/remove-preview").json()
    assert preview["challenge"] == "REMOVE removable"
    assert "project folder" in preview["description_en"]
    assert "without an archive" in preview["description_en"]
    assert "backed up under archives" in preview["description_en_keep_records"]
    client.app.state.bridge.create_task("removable", "Old task")
    denied = client.post("/api/projects/removable/remove", json={"confirm": "wrong"})
    assert denied.status_code == 400
    assert client.get("/api/projects").json()
    result = client.post(
        "/api/projects/removable/remove",
        json={"confirm": preview["challenge"]},
    )
    assert result.status_code == 200, result.text
    assert result.json()["files_deleted"] is False
    assert result.json()["records_retained"] is False
    assert result.json()["archive"] is None
    assert repo.exists()
    assert client.get("/api/projects").json() == []
    assert client.post("/api/projects/add", json={"path": str(repo), "id": "removable"}).status_code == 200
    assert client.get("/api/projects/removable/tasks").json() == []


def test_remove_project_can_explicitly_keep_records(tmp_path, client):
    repo = mkrepo(tmp_path, "retained")
    client.post("/api/projects/add", json={"path": str(repo), "id": "retained"})
    client.app.state.bridge.create_task("retained", "Old task")
    response = client.post("/api/projects/retained/remove", json={"confirm": "REMOVE retained", "keep_records": True})
    assert response.status_code == 200, response.text
    assert response.json()["records_retained"] is True
    assert (Path(response.json()["archive"]) / "project-metadata").exists()
    assert client.post("/api/projects/add", json={"path": str(repo), "id": "retained"}).status_code == 200
    assert client.get("/api/projects/retained/tasks").json() == []


def test_remove_project_rejects_non_boolean_keep_records(tmp_path, client):
    repo = mkrepo(tmp_path, "invalid-retention")
    client.post("/api/projects/add", json={"path": str(repo), "id": "invalid-retention"})
    response = client.post(
        "/api/projects/invalid-retention/remove",
        json={"confirm": "REMOVE invalid-retention", "keep_records": "false"},
    )
    assert response.status_code == 400
    assert client.get("/api/projects/invalid-retention/tasks").status_code == 200


def test_remove_project_delete_folder_requires_separate_challenge(tmp_path, client):
    repo = mkrepo(tmp_path, "removable")
    client.post("/api/projects/add", json={"path": str(repo), "id": "removable"})
    preview = client.get("/api/projects/removable/remove-preview?delete_files=true").json()
    assert preview["challenge"] == "REMOVE FILES removable"
    denied = client.post(
        "/api/projects/removable/remove",
        json={"confirm": "REMOVE removable", "delete_files": True},
    )
    assert denied.status_code == 400
    assert repo.exists()
    result = client.post(
        "/api/projects/removable/remove",
        json={"confirm": preview["challenge"], "delete_files": True},
    )
    assert result.status_code == 200, result.text
    assert result.json()["files_deleted"] is True
    assert not repo.exists()


# --- Trust boundary --------------------------------------------------------


def test_api_requires_the_session_token(tmp_path):
    app = create_app(tmp_path / "home", token=TOKEN)
    anonymous = TestClient(app)
    assert anonymous.get("/api/projects").status_code == 401
    assert anonymous.get("/api/projects", headers={"X-CCC-UI-Token": "wrong"}).status_code == 401
    assert anonymous.get("/api/projects", headers={"X-CCC-UI-Token": TOKEN}).status_code == 200


def test_static_shell_is_served_without_a_token(tmp_path):
    app = create_app(tmp_path / "home", token=TOKEN)
    anonymous = TestClient(app)
    resp = anonymous.get("/")
    assert resp.status_code == 200
    assert "ccc-mcp" in resp.text
    assert anonymous.get("/i18n.js").status_code == 200


def test_each_launch_gets_its_own_random_token(tmp_path):
    first = create_app(tmp_path / "home1")
    second = create_app(tmp_path / "home2")
    assert first.state.ui_token != second.state.ui_token
    assert len(first.state.ui_token) > 20


# --- Add / create a project, combined with enable-write --------------------


def test_add_project_is_read_only_by_default(tmp_path, client):
    repo = mkrepo(tmp_path, "proj1")
    resp = client.post("/api/projects/add", json={"path": str(repo), "id": "demo1"})
    assert resp.status_code == 200, resp.text
    assert resp.json()["id"] == "demo1"
    listed = client.get("/api/projects").json()
    assert listed[0]["access"]["mode"] == "read-only"


def test_add_project_with_enable_write_needs_the_exact_typed_project_id(tmp_path, client):
    repo = mkrepo(tmp_path, "proj2")
    resp = client.post(
        "/api/projects/add",
        json={"path": str(repo), "id": "demo2", "enable_write": True, "confirm": "not-the-id"},
    )
    assert resp.status_code == 400
    # Registration itself still happened; only the write grant was refused.
    access = client.get("/api/projects").json()[0]["access"]
    assert access["mode"] == "read-only"


def test_add_project_with_enable_write_and_matching_confirm_grants_write_in_one_call(
    tmp_path, client
):
    repo = mkrepo(tmp_path, "proj3")
    resp = client.post(
        "/api/projects/add",
        json={"path": str(repo), "id": "demo3", "enable_write": True, "confirm": "demo3"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["access"]["mode"] == "read-write"


def test_create_project_with_enable_write(tmp_path, client):
    resp = client.post(
        "/api/projects/create",
        json={
            "id": "created1",
            "path": str(tmp_path / "newroot"),
            "enable_write": True,
            "confirm": "created1",
        },
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["access"]["mode"] == "read-write"


# --- An existing read-only project is never silently upgraded --------------


def test_existing_readonly_project_stays_readonly_until_explicit_authorize_call(tmp_path, client):
    repo = mkrepo(tmp_path, "proj4")
    client.post("/api/projects/add", json={"path": str(repo), "id": "demo4"})
    # Merely reading the project, its settings or the authorize preview text
    # must never change its access mode.
    client.get("/api/projects")
    preview = client.get("/api/projects/demo4/authorize-preview").json()
    assert preview["challenge"] == "demo4"
    assert "demo4" in preview["description"]
    client.get("/api/projects/demo4/settings")
    access = client.get("/api/projects").json()[0]["access"]
    assert access["mode"] == "read-only"

    resp = client.post("/api/projects/demo4/authorize", json={"confirm": "demo4"})
    assert resp.status_code == 200
    assert resp.json()["mode"] == "read-write"


def test_authorize_wrong_confirmation_text_does_not_grant_write(tmp_path, client):
    repo = mkrepo(tmp_path, "proj5")
    client.post("/api/projects/add", json={"path": str(repo), "id": "demo5"})
    resp = client.post("/api/projects/demo5/authorize", json={"confirm": "demo5x"})
    assert resp.status_code == 400
    assert client.get("/api/projects").json()[0]["access"]["mode"] == "read-only"


def test_revoke_needs_no_confirmation_and_is_idempotent(tmp_path, client):
    repo = mkrepo(tmp_path, "proj6")
    client.post(
        "/api/projects/add",
        json={"path": str(repo), "id": "demo6", "enable_write": True, "confirm": "demo6"},
    )
    assert client.post("/api/projects/demo6/revoke").json()["mode"] == "read-only"
    # Revoking an already read-only project is a harmless no-op.
    assert client.post("/api/projects/demo6/revoke").json()["mode"] == "read-only"


# --- Destructive actions: clear-history and delete --------------------------


def test_clear_history_requires_the_exact_typed_challenge(tmp_path, client):
    repo = mkrepo(tmp_path, "proj7")
    client.post("/api/projects/add", json={"path": str(repo), "id": "demo7"})
    preview = client.get("/api/projects/demo7/clear-history-preview").json()
    assert preview["challenge"] == "CLEAR HISTORY demo7"

    refused = client.post("/api/projects/demo7/clear-history", json={"confirm": "nope"})
    assert refused.status_code == 400

    done = client.post("/api/projects/demo7/clear-history", json={"confirm": "CLEAR HISTORY demo7"})
    assert done.status_code == 200
    assert done.json()["history_cleared"] is True
    assert done.json()["project_files_deleted"] is False


def test_delete_requires_the_exact_typed_challenge_and_never_touches_disk(tmp_path, client):
    repo = mkrepo(tmp_path, "proj8")
    client.post("/api/projects/add", json={"path": str(repo), "id": "demo8"})
    preview = client.get("/api/projects/demo8/delete-preview").json()
    assert preview["challenge"] == "DELETE demo8"

    refused = client.post("/api/projects/demo8/delete", json={"confirm": "DELETE wrong"})
    assert refused.status_code == 400
    assert any(p["id"] == "demo8" for p in client.get("/api/projects").json())

    done = client.post("/api/projects/demo8/delete", json={"confirm": "DELETE demo8"})
    assert done.status_code == 200
    assert not any(p["id"] == "demo8" for p in client.get("/api/projects").json())
    assert repo.exists()
    assert (repo / "README.md").exists()


def test_delete_refuses_while_project_is_still_read_write(tmp_path, client):
    repo = mkrepo(tmp_path, "proj9")
    client.post(
        "/api/projects/add",
        json={"path": str(repo), "id": "demo9", "enable_write": True, "confirm": "demo9"},
    )
    resp = client.post("/api/projects/demo9/delete", json={"confirm": "DELETE demo9"})
    assert resp.status_code == 400
    assert "revoke" in resp.json()["error"].lower()


# --- Path safety for the folder browser -------------------------------------


def test_browse_rejects_a_relative_path(tmp_path, client):
    resp = client.get("/api/browse", params={"path": "relative/path"})
    assert resp.status_code == 400


def test_browse_lists_directories_only_never_file_contents(tmp_path, client):
    d = tmp_path / "root"
    d.mkdir()
    (d / "sub").mkdir()
    (d / "file.txt").write_text("secret contents", encoding="utf-8")
    resp = client.get("/api/browse", params={"path": str(d)})
    assert resp.status_code == 200
    body = resp.json()
    assert body["directories"] == ["sub"]
    assert "secret contents" not in resp.text


# --- Settings -----------------------------------------------------------------


def test_config_endpoints_round_trip(tmp_path, client):
    repo = mkrepo(tmp_path, "proj10")
    client.post("/api/projects/add", json={"path": str(repo), "id": "demo10"})

    resp = client.post("/api/projects/demo10/config/language", json={"language": "en"})
    assert resp.status_code == 200
    assert client.get("/api/projects/demo10/settings").json()["editable"]["language"] == "en"

    resp = client.post(
        "/api/projects/demo10/config/test-command",
        json={"command": ["python", "-m", "pytest", "-q"]},
    )
    assert resp.status_code == 200
    settings = client.get("/api/projects/demo10/settings").json()
    assert settings["editable"]["test_command"] == ["python", "-m", "pytest", "-q"]


# --- Tasks (read-only) --------------------------------------------------------


def test_tasks_list_is_empty_for_a_fresh_project(tmp_path, client):
    repo = mkrepo(tmp_path, "proj11")
    client.post("/api/projects/add", json={"path": str(repo), "id": "demo11"})
    resp = client.get("/api/projects/demo11/tasks")
    assert resp.status_code == 200
    assert resp.json() == []


def test_unknown_project_returns_a_client_error_not_a_crash(tmp_path, client):
    resp = client.get("/api/projects/does-not-exist/tasks")
    assert resp.status_code == 400
    assert "error" in resp.json()


# --- Connect (ChatGPT Tunnel) -------------------------------------------------
#
# The subprocess-lifecycle tests below drive `ConnectionManager` directly with
# `await`, on pytest-asyncio's one function-scoped event loop. Starlette's
# `TestClient` spins up a fresh portal/event loop per call, which tears down any
# background `asyncio.create_task` left over from a previous call — fine for the
# synchronous bridge-method endpoints elsewhere in this file, but wrong for a
# genuinely long-lived background subprocess, so this part tests the manager's
# real async behavior instead of going through repeated HTTP round trips.

TUNNEL = "tunnel_" + "c" * 32


def test_connect_status_reports_disconnected_by_default(tmp_path, client):
    resp = client.get("/api/connect/status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["state"] == "disconnected"
    assert body["tunnel_id"] is None
    assert body["remembered_tunnels"] == []
    # provider login status is its own endpoint (shells out to codex/claude, can take
    # a perceptible moment) so it never blocks the fast, local-only status payload.
    assert "providers" not in body


def test_connect_providers_is_a_separate_fast_path_endpoint(tmp_path, client):
    resp = client.get("/api/connect/providers")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body["providers"]) == {"codex", "claude"}
    for info in body["providers"].values():
        assert set(info) == {"installed", "logged_in", "hint"}


def test_connect_start_without_any_key_is_refused(tmp_path, client):
    resp = client.post("/api/connect/start", json={"tunnel_id": TUNNEL})
    assert resp.status_code == 400
    assert "key" in resp.json()["error"].lower()


def _fake_prepared(tmp_path, body="print('ready', flush=True)\nimport time\ntime.sleep(3)\n"):

    client_script = tmp_path / "fake-tunnel-client"
    client_script.write_text(f"#!{sys.executable}\n{body}")
    client_script.chmod(0o755)
    profile_dir = tmp_path / "tunnel"
    profile_dir.mkdir(exist_ok=True)
    return {
        "client": client_script,
        "profile": "fake-profile",
        "profile_dir": profile_dir,
        "env": dict(os.environ),
    }


@pytest.mark.skipif(sys.platform == "win32", reason="relies on a POSIX shebang script")
async def test_connection_manager_start_and_stop_lifecycle(tmp_path):
    from ccc_mcp.interfaces.web import ConnectionManager

    prepared = _fake_prepared(tmp_path)
    manager = ConnectionManager()
    await manager.start(
        prepared["client"], prepared["profile"], prepared["profile_dir"], prepared["env"], TUNNEL
    )
    assert manager.status() == {"state": "connected", "tunnel_id": TUNNEL, "error": None}

    await manager.stop()
    assert manager.status()["state"] == "disconnected"


@pytest.mark.skipif(sys.platform == "win32", reason="relies on a POSIX shebang script")
async def test_connection_manager_refuses_a_second_connection_while_one_is_live(tmp_path):
    from ccc_mcp.interfaces.web import ConnectionManager

    prepared = _fake_prepared(tmp_path)
    manager = ConnectionManager()
    await manager.start(
        prepared["client"], prepared["profile"], prepared["profile_dir"], prepared["env"], TUNNEL
    )
    with pytest.raises(BridgeError, match="stop"):
        await manager.start(
            prepared["client"],
            prepared["profile"],
            prepared["profile_dir"],
            prepared["env"],
            TUNNEL,
        )
    await manager.stop()


@pytest.mark.skipif(sys.platform == "win32", reason="relies on a POSIX shebang script")
async def test_connection_manager_captures_subprocess_output(tmp_path):
    from ccc_mcp.interfaces.web import ConnectionManager

    prepared = _fake_prepared(tmp_path, body="print('hello from tunnel-client', flush=True)\n")
    manager = ConnectionManager()
    await manager.start(
        prepared["client"], prepared["profile"], prepared["profile_dir"], prepared["env"], TUNNEL
    )
    for _ in range(50):
        if manager.log:
            break
        await asyncio.sleep(0.1)
    assert "hello from tunnel-client" in list(manager.log)
    await manager.stop()


@pytest.mark.skipif(sys.platform == "win32", reason="relies on a POSIX shebang script")
async def test_connection_manager_redacts_runtime_key_from_output(tmp_path):
    from ccc_mcp.interfaces.web import ConnectionManager

    prepared = _fake_prepared(tmp_path, body="print('secret=sk-private', flush=True)\n")
    prepared["env"]["CONTROL_PLANE_API_KEY"] = "sk-private"
    manager = ConnectionManager()
    await manager.start(
        prepared["client"], prepared["profile"], prepared["profile_dir"], prepared["env"], TUNNEL
    )
    for _ in range(50):
        if manager.log:
            break
        await asyncio.sleep(0.1)
    assert "sk-private" not in "\n".join(manager.log)
    assert "[REDACTED]" in "\n".join(manager.log)
    await manager.stop()


@pytest.mark.skipif(sys.platform == "win32", reason="relies on a POSIX shebang script")
async def test_connection_manager_reports_error_state_on_nonzero_exit(tmp_path):
    from ccc_mcp.interfaces.web import ConnectionManager

    prepared = _fake_prepared(tmp_path, body="import sys; sys.exit(3)\n")
    manager = ConnectionManager()
    await manager.start(
        prepared["client"], prepared["profile"], prepared["profile_dir"], prepared["env"], TUNNEL
    )
    for _ in range(50):
        if manager.state != "connected":
            break
        await asyncio.sleep(0.1)
    assert manager.state == "error"
    assert "3" in manager.error


@pytest.mark.skipif(sys.platform == "win32", reason="relies on a POSIX shebang script")
async def test_provider_login_manager_captures_output_and_settles_on_success(tmp_path):
    from ccc_mcp.interfaces.web import ProviderLoginManager

    script = tmp_path / "fake-codex"
    script.write_text(f"#!{sys.executable}\nprint('open https://example.test/auth', flush=True)\n")
    script.chmod(0o755)
    manager = ProviderLoginManager("codex")
    await manager.start(str(script))
    for _ in range(50):
        if manager.state != "running":
            break
        await asyncio.sleep(0.1)
    assert manager.state == "success"
    assert any("https://example.test/auth" in line for line in manager.log)


@pytest.mark.skipif(sys.platform == "win32", reason="relies on a POSIX shebang script")
async def test_provider_login_manager_refuses_a_second_login_while_one_is_running(tmp_path):
    from ccc_mcp.interfaces.web import ProviderLoginManager

    script = tmp_path / "fake-codex"
    script.write_text(f"#!{sys.executable}\nimport time\ntime.sleep(3)\n")
    script.chmod(0o755)
    manager = ProviderLoginManager("codex")
    await manager.start(str(script))
    with pytest.raises(BridgeError, match="already in progress"):
        await manager.start(str(script))
    await manager.cancel()


@pytest.mark.skipif(sys.platform == "win32", reason="relies on a POSIX shebang script")
async def test_provider_login_manager_relays_stdin_to_the_subprocess(tmp_path):
    from ccc_mcp.interfaces.web import ProviderLoginManager

    script = tmp_path / "fake-claude"
    script.write_text(
        f"#!{sys.executable}\n"
        "paste = input()\n"
        "print('got: ' + paste, flush=True)\n"
    )
    script.chmod(0o755)
    manager = ProviderLoginManager("claude")
    await manager.start(str(script))
    await manager.send_input("ABC-123")
    for _ in range(50):
        if manager.state != "running":
            break
        await asyncio.sleep(0.1)
    assert manager.state == "success"
    assert "got: ABC-123" in list(manager.log)


@pytest.mark.skipif(sys.platform == "win32", reason="relies on a POSIX shebang script")
async def test_provider_login_manager_cancel_stops_the_subprocess(tmp_path):
    from ccc_mcp.interfaces.web import ProviderLoginManager

    script = tmp_path / "fake-codex"
    script.write_text(f"#!{sys.executable}\nimport time\ntime.sleep(10)\n")
    script.chmod(0o755)
    manager = ProviderLoginManager("codex")
    await manager.start(str(script))
    await manager.cancel()
    assert manager.state == "idle"
    assert manager.process.returncode is not None


def test_provider_login_endpoints_reject_an_unsupported_provider(tmp_path, client):
    assert client.post("/api/connect/providers/grok/install").status_code == 400
    assert client.post("/api/connect/providers/grok/uninstall").status_code == 400
    assert client.post("/api/connect/providers/grok/login").status_code == 400
    assert client.get("/api/connect/providers/grok/login").status_code == 400


def test_provider_install_endpoint_calls_ensure_installed(tmp_path, client, monkeypatch):
    import ccc_mcp.interfaces.web as web_module

    calls = []
    monkeypatch.setattr(web_module, "ensure_installed", lambda provider: calls.append(provider) or "/usr/local/bin/codex")
    resp = client.post("/api/connect/providers/codex/install")
    assert resp.status_code == 200
    assert resp.json() == {"provider": "codex", "path": "/usr/local/bin/codex"}
    assert calls == ["codex"]


def test_provider_uninstall_endpoint_calls_uninstall_provider(tmp_path, client, monkeypatch):
    import ccc_mcp.interfaces.web as web_module

    monkeypatch.setattr(
        web_module,
        "uninstall_provider",
        lambda provider: {"provider": provider, "uninstalled": True, "still_present": False, "hint": None},
    )
    resp = client.post("/api/connect/providers/claude/uninstall")
    assert resp.status_code == 200
    assert resp.json()["uninstalled"] is True


def test_provider_login_start_requires_the_cli_to_be_installed_first(tmp_path, client, monkeypatch):
    import ccc_mcp.interfaces.web as web_module

    monkeypatch.setattr(
        web_module, "native_executable", lambda provider: (_ for _ in ()).throw(BridgeError("missing"))
    )
    resp = client.post("/api/connect/providers/codex/login")
    assert resp.status_code == 400
    assert "install" in resp.json()["error"].lower()


def test_provider_login_start_delegates_to_the_managers_start(tmp_path, client, monkeypatch):
    # A real TestClient call (without entering it as a `with` context manager) spins
    # up its own event loop per request — nothing keeps a background asyncio task
    # (the login subprocess reader) alive between separate calls the way the real,
    # single-loop `ccc-mcp ui` server does. ProviderLoginManager's own subprocess
    # handling is covered directly in the async tests above; here we only check
    # that the endpoint resolves the installed path and calls manager.start with it.
    import ccc_mcp.interfaces.web as web_module

    monkeypatch.setattr(web_module, "native_executable", lambda provider: "/usr/local/bin/codex")
    manager = client.app.state.provider_login_managers["codex"]
    calls = []

    async def fake_start(path):
        calls.append(path)
        manager.state = "running"

    monkeypatch.setattr(manager, "start", fake_start)
    resp = client.post("/api/connect/providers/codex/login")
    assert resp.status_code == 200
    assert resp.json()["state"] == "running"
    assert calls == ["/usr/local/bin/codex"]


def test_provider_login_status_reports_the_managers_state(tmp_path, client):
    manager = client.app.state.provider_login_managers["claude"]
    manager.state = "failed"
    manager.error = "claude login exited with code 1"
    manager.log.append("open https://example.test/auth")
    resp = client.get("/api/connect/providers/claude/login")
    assert resp.status_code == 200
    assert resp.json() == {
        "provider": "claude",
        "state": "failed",
        "error": "claude login exited with code 1",
        "log": ["open https://example.test/auth"],
    }


def test_provider_login_input_delegates_to_the_managers_send_input(tmp_path, client, monkeypatch):
    manager = client.app.state.provider_login_managers["codex"]
    received = []

    async def fake_send_input(text):
        received.append(text)

    monkeypatch.setattr(manager, "send_input", fake_send_input)
    resp = client.post("/api/connect/providers/codex/login/input", json={"text": "ABC-123"})
    assert resp.status_code == 200
    assert received == ["ABC-123"]


def test_provider_login_cancel_delegates_to_the_managers_cancel(tmp_path, client, monkeypatch):
    manager = client.app.state.provider_login_managers["codex"]
    cancelled = []

    async def fake_cancel():
        cancelled.append(True)
        manager.state = "idle"

    monkeypatch.setattr(manager, "cancel", fake_cancel)
    resp = client.post("/api/connect/providers/codex/login/cancel")
    assert resp.status_code == 200
    assert resp.json()["state"] == "idle"
    assert cancelled == [True]


def test_connect_start_passes_remember_through_to_credential_storage(tmp_path, client, monkeypatch):
    import ccc_mcp.interfaces.web as web_module
    from ccc_mcp.adapters.tunnel_client import load_tunnel_key

    async def fake_start(self, client_path, profile, profile_dir, env, tunnel_id):
        self.state = "connected"
        self.tunnel_id = tunnel_id

    monkeypatch.setattr(
        web_module, "prepare_tunnel_connection", lambda home, tid, key: _fake_prepared(tmp_path)
    )
    monkeypatch.setattr(web_module.ConnectionManager, "start", fake_start)

    resp = client.post(
        "/api/connect/start",
        json={"tunnel_id": TUNNEL, "api_key": "sk-remember-me", "remember": True},
    )
    assert resp.status_code == 200, resp.text
    assert load_tunnel_key(tmp_path / "home", TUNNEL) == "sk-remember-me"


def test_connect_start_reuses_a_remembered_key_without_resending_it(tmp_path, client, monkeypatch):
    import ccc_mcp.interfaces.web as web_module
    from ccc_mcp.adapters.tunnel_client import save_tunnel_key

    save_tunnel_key(tmp_path / "home", TUNNEL, "sk-already-saved")
    assert client.get("/api/connect/status").json()["saved_connections"] == [
        {"id": TUNNEL, "name": TUNNEL, "tunnel_id": TUNNEL}
    ]
    seen_keys = []

    def fake_prepare(home, tid, key):
        seen_keys.append(key)
        return _fake_prepared(tmp_path)

    async def fake_start(self, client_path, profile, profile_dir, env, tunnel_id):
        self.state = "connected"
        self.tunnel_id = tunnel_id

    monkeypatch.setattr(web_module, "prepare_tunnel_connection", fake_prepare)
    monkeypatch.setattr(web_module.ConnectionManager, "start", fake_start)

    resp = client.post("/api/connect/start", json={"tunnel_id": TUNNEL})
    assert resp.status_code == 200, resp.text
    assert seen_keys == ["sk-already-saved"]


def test_connect_forget_removes_a_saved_key(tmp_path, client):
    from ccc_mcp.adapters.tunnel_client import save_tunnel_key

    save_tunnel_key(tmp_path / "home", TUNNEL, "sk-to-forget")
    resp = client.post("/api/connect/forget", json={"tunnel_id": TUNNEL})
    assert resp.status_code == 200
    assert resp.json()["forgotten"] is True
    status = client.get("/api/connect/status").json()
    assert TUNNEL not in status["remembered_tunnels"]


def test_named_connections_allow_multiple_keys_for_same_tunnel_without_exposing_them(
    tmp_path, client
):
    first = client.post(
        "/api/connect/save", json={"name": "Work", "tunnel_id": TUNNEL, "api_key": "sk-work"}
    )
    second = client.post(
        "/api/connect/save",
        json={"name": "Personal", "tunnel_id": TUNNEL, "api_key": "sk-personal"},
    )
    assert first.status_code == second.status_code == 200
    first_id, second_id = first.json()["id"], second.json()["id"]
    assert first_id != second_id
    status = client.get("/api/connect/status").json()
    assert len(status["saved_connections"]) == 2
    assert "sk-work" not in str(status)
    assert "sk-personal" not in str(status)
    updated = client.post(
        "/api/connect/save",
        json={"profile_id": first_id, "name": "Work 2", "api_key": "sk-rotated"},
    )
    assert updated.status_code == 200
    from ccc_mcp.adapters.tunnel_client import get_connection_profile

    assert get_connection_profile(tmp_path / "home", first_id)["api_key"] == "sk-rotated"
    assert get_connection_profile(tmp_path / "home", second_id)["api_key"] == "sk-personal"
    forgotten = client.post("/api/connect/forget", json={"profile_id": first_id})
    assert forgotten.json()["forgotten"] is True
    assert len(client.get("/api/connect/status").json()["saved_connections"]) == 1


def test_named_connection_can_start_with_its_saved_key(tmp_path, client, monkeypatch):
    import ccc_mcp.interfaces.web as web_module

    saved = client.post(
        "/api/connect/save", json={"name": "Work", "tunnel_id": TUNNEL, "api_key": "sk-work"}
    ).json()
    seen = []

    def fake_prepare(home, tunnel_id, api_key):
        seen.append((tunnel_id, api_key))
        return _fake_prepared(tmp_path)

    async def fake_start(self, client_path, profile, profile_dir, env, tunnel_id):
        self.state = "connected"
        self.tunnel_id = tunnel_id

    monkeypatch.setattr(web_module, "prepare_tunnel_connection", fake_prepare)
    monkeypatch.setattr(web_module.ConnectionManager, "start", fake_start)
    response = client.post("/api/connect/start", json={"profile_id": saved["id"]})
    assert response.status_code == 200
    assert seen == [(TUNNEL, "sk-work")]
    again = client.post("/api/connect/start", json={"profile_id": saved["id"]})
    assert again.status_code == 400
    assert seen == [(TUNNEL, "sk-work")]


def test_gate_decision_needs_a_pending_gate(tmp_path, client):
    repo = mkrepo(tmp_path, "gates")
    assert client.post("/api/projects/add", json={"path": str(repo), "id": "gates"}).status_code == 200
    client.app.state.bridge.create_task("gates", "Something")
    response = client.post(
        "/api/projects/gates/tasks/TASK-0001/gate-decision", json={"decision": "proceed"}
    )
    assert response.status_code == 400
    assert "No gate decision is pending" in response.text


def test_continuation_prompt_is_available_from_the_task_page_and_token_gated(tmp_path, client):
    repo = mkrepo(tmp_path, "continuation-ui")
    assert client.post(
        "/api/projects/add", json={"path": str(repo), "id": "continuation-ui"}
    ).status_code == 200
    bridge = client.app.state.bridge
    task_id = bridge.create_task("continuation-ui", "新增 greet 函式")["id"]
    base = f"/api/projects/continuation-ui/tasks/{task_id}/continuation"

    for target, label in [("claude", "Claude Code"), ("codex", "Codex CLI"), ("chatgpt", "ChatGPT")]:
        body = client.get(base, params={"target": target}).json()
        assert body["target"] == target
        assert f"# Continuation Prompt — for {label}" in body["text"]
        assert "requirement: 新增 greet 函式" in body["text"]

    plain = client.get(base).json()
    assert plain["target"] is None
    assert plain["text"].startswith("# Continuity Handoff")
    assert "Continuation Prompt" not in plain["text"]

    rejected = client.get(base, params={"target": "gpt-unknown"})
    assert rejected.status_code == 400
    assert "Unsupported continuation target" in rejected.json()["error"]

    assert TestClient(client.app).get(base).status_code == 401


# --- Tasks page: the same data ChatGPT reads -----------------------------------------------------


def test_work_endpoints_start_list_rename_and_poll_by_revision(tmp_path, client):
    repo = mkrepo(tmp_path, "work")
    client.post("/api/projects/add", json={"path": str(repo), "id": "w1"})

    empty = client.get("/api/projects/w1/work").json()
    assert empty["groups"] == [] and empty["singles"] == []

    started = client.post("/api/projects/w1/work/goals", json={
        "requirement": "Build a small export tool for finance, with checks",
        "request_key": "console-key-0001"}).json()
    assert started["status"] == "created"
    again = client.post("/api/projects/w1/work/goals", json={
        "requirement": "Build a small export tool for finance, with checks",
        "request_key": "console-key-0001"}).json()
    assert again["status"] == "exists" and again["id"] == started["id"]

    listing = client.get("/api/projects/w1/work").json()
    assert [g["id"] for g in listing["groups"]] == [started["id"]]
    revision = listing["revision"]
    # Asking with the revision already held sends back nothing to repaint.
    assert client.get(f"/api/projects/w1/work?rev={revision}").json() == {
        "revision": revision, "unchanged": True}

    renamed = client.post(f"/api/projects/w1/goals/{started['id']}/rename",
                          json={"title": "Finance export"}).json()
    assert renamed["title"] == "Finance export"
    after = client.get(f"/api/projects/w1/work?rev={revision}").json()
    assert after["revision"] != revision and after["groups"][0]["title"] == "Finance export"
    # The name a person gave never replaces what was asked for.
    assert client.get(f"/api/projects/w1/goals/{started['id']}").json()["requirement"].startswith(
        "Build a small export tool")


def test_work_endpoints_are_token_gated_and_refuse_unknown_fields(tmp_path, client):
    repo = mkrepo(tmp_path, "gate")
    client.post("/api/projects/add", json={"path": str(repo), "id": "w2"})
    anonymous = TestClient(client.app)
    assert anonymous.get("/api/projects/w2/work").status_code == 401
    assert anonymous.post("/api/projects/w2/work/goals", json={"requirement": "x y z"}).status_code == 401
    refused = client.post("/api/projects/w2/work/goals", json={"requirement": "do it", "approve": True})
    assert refused.status_code == 400 and "Unknown fields" in refused.json()["error"]


def test_the_console_cannot_start_a_goal_that_nobody_approved(tmp_path, client):
    repo = mkrepo(tmp_path, "unapproved")
    client.post("/api/projects/add", json={"path": str(repo), "id": "w3"})
    goal = client.post("/api/projects/w3/work/goals", json={"requirement": "Ship the thing"}).json()
    response = client.post(f"/api/projects/w3/goals/{goal['id']}/run", json={})
    assert response.status_code == 400
    assert client.get("/api/projects/w3/work").json()["groups"][0]["state"] == "DRAFT"


# --- What was removed stays removed -----------------------------------------------------------


@pytest.mark.parametrize("path", [
    "/api/dashboard", "/api/dashboard/reliability", "/api/dashboard/account-usage",
    "/api/docs/en/README", "/api/onboarding/status", "/api/skills", "/api/skills/discover",
    "/api/mcp/calls", "/api/project-groups", "/api/projects/x/roles", "/api/projects/x/mcp",
    "/api/projects/x/workflows", "/api/projects/x/context", "/api/projects/x/tasks/T/evaluations",
])
def test_removed_console_endpoints_are_gone_not_hidden(client, path):
    assert client.get(path).status_code == 404


async def test_removed_mcp_tools_are_not_offered_and_the_goal_tools_are(tmp_path):
    from ccc_mcp.interfaces.server import create_server

    names = {tool.name for tool in await create_server(tmp_path / "h").list_tools()}
    assert not names & {
        "evaluate_task", "reevaluate_execution", "get_task_evaluations", "get_agent_reliability",
        "list_installed_mcp_tools", "call_installed_mcp_tool"}
    assert {"start_goal", "plan_goal", "control_goal", "get_goal", "get_work_overview",
            "request_goal_approval"} <= names


def test_removed_cli_commands_are_gone(tmp_path):
    from typer.testing import CliRunner

    from ccc_mcp.interfaces.cli import app

    runner = CliRunner()
    shown = runner.invoke(app, ["--home", str(tmp_path / "h"), "--help"]).output
    for gone in ("reputation", "compare-tokens", "validate-scoring"):
        assert gone not in shown
    for argv in (["reputation"], ["compare-tokens"], ["validate-scoring"], ["mcp", "list"],
                 ["skill", "discover"], ["goal", "phase-start"]):
        result = runner.invoke(app, ["--home", str(tmp_path / "h"), *argv])
        assert result.exit_code != 0, argv


# --- Advanced: roles and workflow, folded into the project page -----------------------------------


def test_the_project_page_can_still_set_roles_workflow_and_review_gate(tmp_path, client):
    repo = mkrepo(tmp_path, "adv")
    client.post("/api/projects/add", json={"path": str(repo), "id": "adv1"})
    settings = client.get("/api/projects/adv1/settings").json()
    assert {"standard", "full"} <= {f["id"] for f in settings["workflow_choices"]}

    assert client.post("/api/projects/adv1/config/role", json={
        "role": "REVIEW", "provider": "claude", "effort": "high", "model": "", "quota_fallback": "chatgpt",
    }).status_code == 200
    role = client.get("/api/projects/adv1/settings").json()["editable"]["roles"]["REVIEW"]
    assert (role["provider"], role["effort"], role["quota_fallback"]) == ("claude", "high", "chatgpt")

    assert client.post("/api/projects/adv1/config/workflow", json={"workflow": "full"}).status_code == 200
    assert client.get("/api/projects/adv1/settings").json()["editable"]["workflow"] == "full"
    gate = client.post("/api/projects/adv1/config/review-gate",
                       json={"ar_strictness": "strict", "max_rounds": 3, "escalation": "ask_user"})
    assert gate.status_code == 200
    assert client.get("/api/projects/adv1/settings").json()["editable"]["review_gate"]["max_rounds"] == 3


def test_the_role_and_workflow_routes_refuse_bad_input_and_anonymous_callers(tmp_path, client):
    repo = mkrepo(tmp_path, "adv2")
    client.post("/api/projects/add", json={"path": str(repo), "id": "adv2"})
    for path, body in (("role", {"role": "NOPE", "provider": "codex"}),
                       ("role", {"role": "SA", "provider": "codex", "approve": True}),
                       ("workflow", {"workflow": "does-not-exist"}),
                       ("review-gate", {"max_rounds": 99})):
        assert client.post(f"/api/projects/adv2/config/{path}", json=body).status_code == 400, (path, body)
        assert TestClient(client.app).post(f"/api/projects/adv2/config/{path}", json=body).status_code == 401
