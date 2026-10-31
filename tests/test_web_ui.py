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

import pytest
from starlette.testclient import TestClient

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


def test_remove_project_keeps_folder_by_default_and_checks_confirmation(tmp_path, client):
    repo = mkrepo(tmp_path, "removable")
    assert client.post("/api/projects/add", json={"path": str(repo), "id": "removable"}).status_code == 200
    preview = client.get("/api/projects/removable/remove-preview").json()
    assert preview["challenge"] == "REMOVE removable"
    assert "project folder" in preview["description_en"]
    denied = client.post("/api/projects/removable/remove", json={"confirm": "wrong"})
    assert denied.status_code == 400
    assert client.get("/api/projects").json()
    result = client.post(
        "/api/projects/removable/remove",
        json={"confirm": preview["challenge"]},
    )
    assert result.status_code == 200, result.text
    assert result.json()["files_deleted"] is False
    assert repo.exists()
    assert client.get("/api/projects").json() == []


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


def test_documentation_uses_selected_language_and_requires_token(tmp_path, client):
    english = client.get("/api/docs/en/README")
    chinese = client.get("/api/docs/ch/README")
    assert english.status_code == 200
    assert chinese.status_code == 200
    assert "ccc-mcp documentation" in english.json()["html"]
    assert "ccc-mcp 文件（繁體中文）" in chinese.json()["html"]
    assert english.json()["html"] != chinese.json()["html"]
    assert client.get("/api/docs/en/not-a-guide").status_code == 404
    anonymous = TestClient(create_app(tmp_path / "other-home", token=TOKEN))
    assert anonymous.get("/api/docs/en/README").status_code == 401


def test_global_skill_install_and_remove_affects_every_project(tmp_path, client, monkeypatch):
    from ccc_mcp.adapters import skills as skill_registry

    provider_root = tmp_path / "provider-skills"
    source = provider_root / "shared-guidance"
    source.mkdir(parents=True)
    (source / "SKILL.md").write_text(
        "---\nname: shared-guidance\ndescription: Shared guidance\n---\nFollow this guidance.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        skill_registry, "_provider_skill_roots", lambda: [("Codex", provider_root)]
    )
    for project_id in ("first", "second"):
        repo = mkrepo(tmp_path, project_id)
        assert client.post(
            "/api/projects/add", json={"path": str(repo), "id": project_id}
        ).status_code == 200

    discovered = client.get("/api/skills/discover").json()
    selected = next(item for item in discovered if item["id"] == "shared-guidance")
    assert selected["already_installed"] is False
    installed = client.post(
        "/api/skills/install-discovered",
        json={"id": selected["id"], "path": selected["path"]},
    )
    assert installed.status_code == 200, installed.text
    assert [skill["id"] for skill in client.get("/api/skills").json()] == ["shared-guidance"]
    for project_id in ("first", "second"):
        context = client.get(f"/api/projects/{project_id}/context").json()
        assert any(
            skill["id"] == "shared-guidance"
            for skill in context["content"]["available_skills"]
        )

    path = "/api/skills/shared-guidance/remove"
    assert client.post(path, json={"confirm": "wrong"}).status_code == 400
    assert client.get("/api/skills").json()
    removed = client.post(path, json={"confirm": "REMOVE SKILL shared-guidance"})
    assert removed.status_code == 200, removed.text
    assert client.get("/api/skills").json() == []
    assert (source / "SKILL.md").is_file()


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


def test_project_groups_create_move_rename_reorder_delete_and_persist(tmp_path, client):
    for name in ("alpha", "beta"):
        repo = mkrepo(tmp_path, name)
        assert (
            client.post("/api/projects/add", json={"path": str(repo), "id": name}).status_code
            == 200
        )
    first = client.post("/api/project-groups", json={"action": "create", "name": "Work"})
    assert first.status_code == 200, first.text
    work = first.json()["groups"][0]["id"]
    second = client.post("/api/project-groups", json={"action": "create", "name": "Personal"})
    personal = second.json()["groups"][1]["id"]
    moved = client.post(
        "/api/project-groups", json={"action": "move", "project_id": "alpha", "group_id": work}
    )
    assert moved.json()["assignments"] == {"alpha": work}
    renamed = client.post(
        "/api/project-groups", json={"action": "rename", "group_id": work, "name": "Client"}
    )
    assert renamed.json()["groups"][0]["name"] == "Client"
    reordered = client.post(
        "/api/project-groups", json={"action": "reorder", "group_ids": [personal, work]}
    )
    assert [g["id"] for g in reordered.json()["groups"]] == [personal, work]
    assert client.get("/api/project-groups").json()["assignments"] == {"alpha": work}
    deleted = client.post("/api/project-groups", json={"action": "delete", "group_id": work})
    assert deleted.json()["assignments"] == {}
    assert [g["id"] for g in deleted.json()["groups"]] == [personal]


def test_project_groups_reject_unknown_project_and_duplicate_name(tmp_path, client):
    assert (
        client.post("/api/project-groups", json={"action": "create", "name": "Work"}).status_code
        == 200
    )
    assert (
        client.post("/api/project-groups", json={"action": "create", "name": "work"}).status_code
        == 400
    )
    assert (
        client.post(
            "/api/project-groups",
            json={"action": "move", "project_id": "missing", "group_id": None},
        ).status_code
        == 400
    )


def test_project_groups_support_bilingual_names_without_changing_assignments(tmp_path, client):
    repo = mkrepo(tmp_path, "alpha")
    assert (
        client.post("/api/projects/add", json={"path": str(repo), "id": "alpha"}).status_code == 200
    )
    created = client.post("/api/project-groups", json={"action": "create", "name": "測試"})
    assert created.status_code == 200
    group_id = created.json()["groups"][0]["id"]
    moved = client.post(
        "/api/project-groups", json={"action": "move", "project_id": "alpha", "group_id": group_id}
    )
    assert moved.status_code == 200

    renamed = client.post(
        "/api/project-groups",
        json={"action": "rename", "group_id": group_id, "name_zh": "測試", "name_en": "Test"},
    )
    assert renamed.status_code == 200, renamed.text
    assert renamed.json()["groups"] == [
        {"id": group_id, "name": "測試", "name_zh": "測試", "name_en": "Test"}
    ]
    assert client.get("/api/project-groups").json()["assignments"] == {"alpha": group_id}

    duplicate = client.post(
        "/api/project-groups", json={"action": "create", "name_zh": "另一組", "name_en": "test"}
    )
    assert duplicate.status_code == 400
    empty = client.post(
        "/api/project-groups", json={"action": "create", "name_zh": " ", "name_en": " "}
    )
    assert empty.status_code == 400


def test_project_groups_allow_one_language_and_legacy_rename(tmp_path, client):
    created = client.post(
        "/api/project-groups", json={"action": "create", "name_zh": "工作", "name_en": ""}
    )
    assert created.status_code == 200, created.text
    group_id = created.json()["groups"][0]["id"]
    assert created.json()["groups"][0] == {"id": group_id, "name": "工作", "name_zh": "工作"}
    renamed = client.post(
        "/api/project-groups", json={"action": "rename", "group_id": group_id, "name": "Work"}
    )
    assert renamed.status_code == 200
    assert renamed.json()["groups"][0] == {"id": group_id, "name": "Work"}


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

    resp = client.post(
        "/api/projects/demo10/config/meeting-mode",
        json={"enabled": True, "max_rounds": 2},
    )
    assert resp.status_code == 200
    settings = client.get("/api/projects/demo10/settings").json()
    assert settings["editable"]["meeting_mode"]["enabled"] is True
    assert settings["editable"]["meeting_mode"]["max_rounds"] == 2


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


def test_mcp_ui_probes_selected_tools_and_installs_only_the_selected_subset(
    tmp_path, client, monkeypatch
):
    from ccc_mcp.adapters import mcp_servers

    repo = mkrepo(tmp_path, "mcp-repo")
    client.post("/api/projects/add", json={"path": str(repo), "id": "mcp-demo"})
    discovered = {
        "name": "example-mcp",
        "client": "VS Code",
        "source_file": "local-mcp.json",
        "transport": "stdio",
        "command": sys.executable,
        "args": [],
        "has_env": False,
    }
    monkeypatch.setattr(mcp_servers, "discover", lambda root=None: [discovered])
    monkeypatch.setattr(
        mcp_servers,
        "probe_tools",
        lambda command, args: [
            {"name": "read", "description": "Read data", "read_only": True},
            {"name": "delete", "description": "Delete data", "read_only": False},
        ],
    )
    selected = {key: discovered[key] for key in ("name", "client", "source_file")}
    preview = client.post("/api/projects/mcp-demo/mcp/probe", json=selected)
    assert preview.status_code == 200, preview.text
    assert [tool["name"] for tool in preview.json()["tools"]] == ["read", "delete"]

    empty = client.post("/api/projects/mcp-demo/mcp/install", json={**selected, "tools": []})
    assert empty.status_code == 400
    unknown = client.post(
        "/api/projects/mcp-demo/mcp/install", json={**selected, "tools": ["unknown"]}
    )
    assert unknown.status_code == 400

    installed = client.post(
        "/api/projects/mcp-demo/mcp/install", json={**selected, "tools": ["read"]}
    )
    assert installed.status_code == 200, installed.text
    listed = client.get("/api/projects/mcp-demo/mcp").json()["installed"]["example-mcp"]
    assert listed["allowed_tools"] == ["read"]
    assert listed["scope"] == "project"

    monkeypatch.setattr(
        mcp_servers,
        "describe_tools",
        lambda scope, command, args: [
            {"name": "read", "description": "Read data", "input_schema": {"type": "object"}},
            {"name": "delete", "description": "Delete data", "input_schema": {"type": "object"}},
        ],
    )
    monkeypatch.setattr(
        mcp_servers,
        "call_tool",
        lambda scope, command, args, tool, arguments: {
            "is_error": False,
            "text": f"{tool}:{arguments['value']}",
            "truncated": False,
        },
    )
    tools = client.get("/api/projects/mcp-demo/mcp/example-mcp/tools")
    assert tools.status_code == 200
    assert [tool["name"] for tool in tools.json()] == ["read"]
    called = client.post(
        "/api/projects/mcp-demo/mcp/example-mcp/tools/read/call",
        json={"arguments": {"value": "hello"}},
    )
    assert called.status_code == 200
    assert called.json()["text"] == "read:hello"
    assert (
        client.post(
            "/api/projects/mcp-demo/mcp/example-mcp/tools/delete/call",
            json={"arguments": {"value": "bad"}},
        ).status_code
        == 400
    )
    assert client.get("/api/mcp/calls").json()[-1]["tool"] == "read"

    removed = client.post("/api/projects/mcp-demo/mcp/example-mcp/remove")
    assert removed.status_code == 200
    assert client.get("/api/projects/mcp-demo/mcp").json()["installed"] == {}


def test_mcp_ui_lists_venv_executable_and_installs_after_tool_selection(tmp_path, client, monkeypatch):
    from ccc_mcp.adapters import mcp_servers

    repo = mkrepo(tmp_path, "mcp-venv-repo")
    executable = "codebase-memory-mcp.exe" if os.name == "nt" else "codebase-memory-mcp"
    binary = repo / ".venv" / ("Scripts" if os.name == "nt" else "bin") / executable
    binary.parent.mkdir(parents=True)
    binary.write_text("#!/bin/sh\n")
    binary.chmod(0o755)
    client.post("/api/projects/add", json={"path": str(repo), "id": "mcp-venv"})
    monkeypatch.setattr(mcp_servers, "client_config_files", lambda root=None: [])

    probes = []
    def probe(command, args):
        probes.append(command)
        return [{"name": "search", "description": "Search", "read_only": True}]

    monkeypatch.setattr(
        mcp_servers, "probe_tools",
        probe,
    )

    listed = client.get("/api/projects/mcp-venv/mcp")
    assert listed.status_code == 200
    candidate = next(
        item for item in listed.json()["discovered"]
        if item["name"] == "codebase-memory-mcp" and item["command"] == str(binary)
    )
    assert listed.json()["installed"] == {}
    assert probes == []
    selected = {key: candidate[key] for key in ("name", "client", "source_file")}
    preview = client.post("/api/projects/mcp-venv/mcp/probe", json=selected)
    assert preview.status_code == 200, preview.text
    assert preview.json()["tools"][0]["name"] == "search"
    assert probes == [str(binary)]
    installed = client.post(
        "/api/projects/mcp-venv/mcp/install", json={**selected, "tools": ["search"]}
    )
    assert installed.status_code == 200, installed.text
    assert client.get("/api/projects/mcp-venv/mcp").json()["installed"]["codebase-memory-mcp"][
        "allowed_tools"
    ] == ["search"]


def test_mcp_ui_rejects_unlisted_or_remote_server_and_requires_token(tmp_path, client, monkeypatch):
    from ccc_mcp.adapters import mcp_servers

    repo = mkrepo(tmp_path, "mcp-remote")
    client.post("/api/projects/add", json={"path": str(repo), "id": "mcp-remote"})
    monkeypatch.setattr(
        mcp_servers,
        "discover",
        lambda root=None: [
            {
                "name": "remote",
                "client": "VS Code",
                "source_file": "local-mcp.json",
                "transport": "http",
                "command": None,
                "args": [],
                "has_env": False,
            }
        ],
    )
    path = "/api/projects/mcp-remote/mcp/probe"
    assert client.post(path, json={"name": "fake"}).status_code == 400
    assert (
        client.post(
            path, json={"name": "remote", "client": "VS Code", "source_file": "local-mcp.json"}
        ).status_code
        == 400
    )
    anonymous = TestClient(create_app(tmp_path / "home", token=TOKEN))
    assert anonymous.post(path, json={"name": "remote"}).status_code == 401


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
    import os

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
