"""The console's side of importing roles: reading what arrived and confirming it.

Only a request that carries the console's session token can read or confirm an imported role,
and confirming needs the phrase for that exact role version. There is no MCP tool for it.
"""

import asyncio

import pytest
from starlette.testclient import TestClient
from test_role_bundle import build_source

from ccc_mcp.interfaces.server import create_server
from ccc_mcp.interfaces.web import create_app

TOKEN = "test-session-token"


@pytest.fixture
def imported(tmp_path, bridge, human):
    first, second, task_id = build_source(bridge, human)
    app = create_app(tmp_path / "home-web", token=TOKEN)
    client = TestClient(app, headers={"X-CCC-UI-Token": TOKEN})
    folder = tmp_path / "web-target"
    folder.mkdir()
    response = client.post(
        f"/api/projects/import?path={folder}", content=bridge.export_project_bundle("demo"))
    assert response.status_code == 200, response.text
    return client, response.json(), first, second


def roles(path=""):
    return f"/api/projects/demo/roles{path}"


def test_the_import_says_how_many_roles_wait_for_a_person(imported):
    client, result, first, second = imported
    assert result["custom_roles"] == result["custom_roles_pending"] == 2
    listing = client.get(roles()).json()
    assert sorted(listing["pending_imports"]) == sorted([first["id"], second["id"]])
    assert {r["status"] for r in listing["roles"]} == {"imported_pending"}


def test_reading_and_confirming_need_the_session_token(imported):
    client, _, first, _ = imported
    anonymous = TestClient(client.app)
    assert anonymous.post(roles(f"/{first['id']}/confirm-snapshot"), json={}).status_code == 401
    assert anonymous.get(roles(f"/{first['id']}/import-review")).status_code == 401
    assert anonymous.post(roles(f"/{first['id']}/confirm-import"), json={}).status_code == 401
    wrong = TestClient(client.app, headers={"X-CCC-UI-Token": "guess"})
    assert wrong.post(roles(f"/{first['id']}/confirm-import"), json={}).status_code == 401


def test_the_review_page_has_what_a_person_needs_and_no_secrets(imported):
    client, _, first, _ = imported
    review = client.get(roles(f"/{first['id']}/import-review")).json()
    assert review["permissions"]["read_only"] is True
    assert review["source"]["source_project_id"] == "demo"
    assert review["confirmation"]["phrase"].startswith(f"ACTIVATE {first['id']} v1 ")
    assert "api_key" not in str(review).lower()


def test_confirming_over_http_needs_the_exact_phrase(imported):
    client, _, first, second = imported
    form = client.get(roles(f"/{first['id']}/import-review")).json()["confirmation"]
    url = roles(f"/{first['id']}/confirm-import")
    body = {"version": form["version"], "version_sha256": form["version_sha256"],
            "snapshot_ids": [], "confirmation": form["phrase"]}

    assert client.post(url, json={**body, "confirmation": "yes"}).status_code == 400
    assert client.post(url, json={**body, "approved": True}).status_code == 400   # unknown field
    assert client.post(url, json={**body, "version": 2}).status_code == 400
    still = client.get(roles(f"/{first['id']}")).json()["role"]
    assert still["status"] == "imported_pending"

    done = client.post(url, json=body)
    assert done.status_code == 200 and done.json()["status"] == "active"
    listing = client.get(roles()).json()
    assert listing["pending_imports"] == [second["id"]]
    assert client.post(url, json=body).status_code == 400                         # not twice
    events = client.get(roles(f"/{first['id']}")).json()["events"]
    activated = [e for e in events if e["action"] == "activated"]
    assert activated and activated[0]["actor"] == "local-console"
    # the review page is only for a role that is waiting
    assert client.get(roles(f"/{first['id']}/import-review")).status_code == 400


def test_an_imported_role_cannot_be_edited_or_switched_on_by_the_ordinary_routes(imported):
    client, _, first, _ = imported
    update = client.post(roles(f"/{first['id']}/update"),
                         json={"expected_version": 1, "changes": {"description": "x"}})
    assert update.status_code == 400
    switch = client.post(roles(f"/{first['id']}/status"), json={"status": "active"})
    assert switch.status_code == 400
    assert client.get(roles(f"/{first['id']}")).json()["role"]["status"] == "imported_pending"


def test_no_mcp_tool_can_import_confirm_or_trust(tmp_path):
    for http in (False, True):
        server = create_server(tmp_path / "home", http=http)
        names = {tool.name for tool in asyncio.run(server.list_tools())}
        offending = {n for n in names if any(
            word in n for word in ("import", "confirm", "trust", "activate", "snapshot"))}
        assert not offending, offending
