"""Compatibility of project bundles across releases and databases.

`fixtures/bundle_v1_previous_release.json` was written by the previous committed release (the
code at the last commit before Agent Studio), not by this one.
"""

from pathlib import Path

import pytest
from test_role_bundle import envelope, reseal
from test_schema_compat import legacy_home, seed_legacy_rows

from ccc_mcp.core.project_bundle import ROLE_TABLES, SUPPORTED_VERSIONS
from ccc_mcp.infra.errors import BridgeError
from ccc_mcp.service import CCCMCP

PREVIOUS = Path(__file__).parent / "fixtures" / "bundle_v1_previous_release.json"


def test_a_bundle_written_by_the_previous_release_still_imports(tmp_path):
    text = PREVIOUS.read_text(encoding="utf-8")
    assert envelope(text)["payload"]["version"] == 1
    folder = tmp_path / "target"
    folder.mkdir()
    service = CCCMCP(tmp_path / "home")
    result = service.import_project_bundle(text, folder)
    assert result["tasks"] == 1 and result["custom_roles"] == 0
    assert service.get_project_settings("legacy-demo")["workflow"] == "life"
    assert service.list_tasks("legacy-demo")[0]["requirement"] == "Plan a day trip"
    assert service.list_custom_roles("legacy-demo") == []
    # the imported project is an ordinary project of this release afterwards
    role = service.create_custom_role(
        "legacy-demo", "Strict", "AR", prompt={"mode": "freeform", "freeform": "Be strict."})
    assert role["status"] == "active"


def test_exporting_an_imported_old_bundle_without_roles_stays_version_1(tmp_path):
    folder = tmp_path / "target"
    folder.mkdir()
    service = CCCMCP(tmp_path / "home")
    service.import_project_bundle(PREVIOUS.read_text(encoding="utf-8"), folder)
    again = envelope(service.export_project_bundle("legacy-demo"))["payload"]
    original = envelope(PREVIOUS.read_text(encoding="utf-8"))["payload"]
    assert again["version"] == 1 and not set(again["tables"]) & set(ROLE_TABLES)
    assert set(again["tables"]) == set(original["tables"])
    for table in ("tasks", "task_events", "context_versions", "project_settings"):
        assert len(again["tables"][table]) == len(original["tables"][table]), table


def test_a_database_upgraded_from_0_4_0_exports_a_bundle_the_old_format_covers(tmp_path):
    home = legacy_home(tmp_path)
    seed_legacy_rows(home)
    service = CCCMCP(home)                     # opening it upgrades it in place
    repo = tmp_path / "repo"
    repo.mkdir()
    service.register_project(repo, "demo")
    service.create_task("demo", "Plan a day trip")
    text = service.export_project_bundle("demo")
    payload = envelope(text)["payload"]
    assert payload["version"] == 1 and not set(payload["tables"]) & set(ROLE_TABLES)
    folder = tmp_path / "target"
    folder.mkdir()
    other = CCCMCP(tmp_path / "other-home")
    assert other.import_project_bundle(text, folder)["tasks"] == 1
    # the rows that were already in the old database are untouched by all of this
    assert service.store.one("SELECT 1 FROM agent_executions WHERE id='EXEC-old'")
    assert service.store.one("SELECT 1 FROM evaluations WHERE id='EV-old'")


def test_the_version_a_bundle_declares_tells_an_older_release_to_refuse_it(tmp_path):
    """The previous release reads `version == 1` only (checked against that release's code
    while preparing P5: it imported a version 1 bundle written by this release and answered
    "Unsupported project bundle version" to a version 2 one). A bundle carries version 2
    exactly when it carries role records, so an older release never imports half a project."""
    assert SUPPORTED_VERSIONS == (1, 2)
    repo = tmp_path / "repo"
    repo.mkdir()
    service = CCCMCP(tmp_path / "home")
    service.register_project(repo, "p1")
    service.create_task("p1", "Plan")
    assert envelope(service.export_project_bundle("p1"))["payload"]["version"] == 1
    service.create_custom_role(
        "p1", "Mine", "AR", prompt={"mode": "freeform", "freeform": "Be strict."})
    forged = envelope(service.export_project_bundle("p1"))
    assert forged["payload"]["version"] == 2
    # relabelling it as version 1 does not slip role records into an old-format bundle
    forged["payload"]["version"] = 1
    folder = tmp_path / "t"
    folder.mkdir()
    with pytest.raises(BridgeError, match="missing project records"):
        CCCMCP(tmp_path / "other").import_project_bundle(reseal(forged), folder)
