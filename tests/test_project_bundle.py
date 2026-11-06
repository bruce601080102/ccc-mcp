import json
import os

import pytest
from starlette.testclient import TestClient
from typer.testing import CliRunner

from ccc_mcp.core.project_bundle import OPTIONAL_TABLES
from ccc_mcp.infra.errors import BridgeError
from ccc_mcp.infra.security import canonical, digest
from ccc_mcp.interfaces.cli import app
from ccc_mcp.interfaces.web import create_app
from ccc_mcp.service import CCCMCP


def test_project_bundle_restores_settings_and_history_without_authorization(tmp_path):
    first = CCCMCP(tmp_path / "first-home")
    source = tmp_path / "source"
    source.mkdir()
    first.register_project(source, "demo")
    first.configure_profile("demo", "chatgpt-only")
    first.configure_workflow("demo", "life")
    first.create_task("demo", "Plan a day trip")
    first.pause_task("demo", "TASK-0001")
    first.add_task_message("demo", "TASK-0001", "Use public transit")
    bundle = first.export_project_bundle("demo")

    target = tmp_path / "target"
    target.mkdir()
    second = CCCMCP(tmp_path / "second-home")
    existing = tmp_path / "existing"
    existing.mkdir()
    second.register_project(existing, "other")
    second.create_task("other", "Keep this task")
    result = second.import_project_bundle(bundle, target)

    assert result["tasks"] == 1
    assert second.get_project_access("demo")["mode"] == "read-only"
    settings = second.get_project_settings("demo")
    assert settings["workflow"] == "life"
    assert settings["roles"]["SA"]["provider"] == "chatgpt"
    assert second.list_tasks("demo")[0]["requirement"] == "Plan a day trip"
    assert any(
        event["summary"] == "Use public transit"
        for event in second.task_timeline("demo", "TASK-0001")["events"]
    )
    assert second.list_tasks("other")[0]["requirement"] == "Keep this task"
    assert (target / ".ccc-mcp" / "CONTEXT.md").is_file()
    assert not (target / "README.md").exists()
    assert (
        second.store.one("SELECT paused FROM task_controls WHERE project_id='demo'")["paused"] == 1
    )

    with pytest.raises(BridgeError, match="Target already has"):
        second.import_project_bundle(bundle, target)


def test_project_bundle_rejects_tampering_and_never_imports_access_grants(tmp_path):
    first = CCCMCP(tmp_path / "home-a")
    source = tmp_path / "source"
    source.mkdir()
    first.register_project(source, "demo")
    bundle = json.loads(first.export_project_bundle("demo"))
    bundle["payload"]["tables"]["project_settings"][0]["content"] = "{}"
    target = tmp_path / "target"
    target.mkdir()
    second = CCCMCP(tmp_path / "home-b")
    with pytest.raises(BridgeError, match="checksum mismatch"):
        second.import_project_bundle(json.dumps(bundle), target)
    assert second.store.one("SELECT 1 FROM projects WHERE id='demo'") is None


def test_authorized_project_import_is_read_only_and_drops_machine_permissions(tmp_path, human):
    first = CCCMCP(tmp_path / "first-home")
    source = tmp_path / "source"
    source.mkdir()
    first.register_project(source, "demo")
    first.authorize_project_local("demo")
    settings = first.get_project_settings("demo")
    settings["mcp_servers"] = {"local-tool": {"command": "tool"}}
    settings["writable_roots"] = [str(source)]
    first._save_settings("demo", settings)
    bundle = first.export_project_bundle("demo")
    exported_settings = json.loads(
        json.loads(bundle)["payload"]["tables"]["project_settings"][0]["content"]
    )
    assert "mcp_servers" not in exported_settings
    assert "writable_roots" not in exported_settings

    target = tmp_path / "target"
    target.mkdir()
    second = CCCMCP(tmp_path / "second-home")
    second.import_project_bundle(bundle, target)

    assert second.get_project_access("demo")["mode"] == "read-only"
    restored = second.get_project_settings("demo")
    assert restored["mcp_servers"] == {}
    assert restored["writable_roots"] == []
    assert restored["workspace_mode"] == "worktree"


def test_project_bundle_cannot_write_source_files_via_artifact(tmp_path):
    first = CCCMCP(tmp_path / "first-home")
    source = tmp_path / "source"
    source.mkdir()
    first.register_project(source, "demo")
    envelope = json.loads(first.export_project_bundle("demo"))
    envelope["payload"]["tables"]["artifacts"].append(
        {
            "project_id": "demo",
            "path": "README.md",
            "content": "overwrite",
            "sha256": digest(b"overwrite"),
            "created": "2026-01-01",
        }
    )
    envelope["sha256"] = digest(canonical(envelope["payload"]).encode())
    target = tmp_path / "target"
    target.mkdir()
    (target / "README.md").write_text("keep", encoding="utf-8")
    second = CCCMCP(tmp_path / "second-home")
    with pytest.raises(BridgeError, match="outside its metadata"):
        second.import_project_bundle(canonical(envelope), target)
    assert (target / "README.md").read_text(encoding="utf-8") == "keep"
    assert second.store.one("SELECT 1 FROM projects WHERE id='demo'") is None


def test_legacy_score_rounds_are_exported_and_restored(tmp_path):
    first = CCCMCP(tmp_path / "first-home")
    source = tmp_path / "source"
    source.mkdir()
    first.register_project(source, "demo")
    first.create_task("demo", "Review the plan")
    with first.store.connect() as conn:
        conn.execute(OPTIONAL_TABLES["score_rounds"])
        conn.execute(
            "INSERT INTO score_rounds VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "SCORE-1",
                "demo",
                "TASK-0001",
                1,
                "EXEC-old",
                75,
                "{}",
                80,
                "reviewed",
                78,
                70,
                "PASS",
                "2026-01-01",
            ),
        )
    bundle = first.export_project_bundle("demo")
    assert len(json.loads(bundle)["payload"]["tables"]["score_rounds"]) == 1

    target = tmp_path / "target"
    target.mkdir()
    second = CCCMCP(tmp_path / "second-home")
    second.import_project_bundle(bundle, target)
    assert (
        second.store.one("SELECT total_score FROM score_rounds WHERE project_id=?", ("demo",))[
            "total_score"
        ]
        == 78
    )


def test_empty_unknown_legacy_table_does_not_block_export(tmp_path):
    bridge = CCCMCP(tmp_path / "home")
    source = tmp_path / "source"
    source.mkdir()
    bridge.register_project(source, "demo")
    bridge.store.execute("CREATE TABLE old_extension(project_id TEXT, detail TEXT)")
    assert bridge.export_project_bundle("demo")

    bridge.store.execute("INSERT INTO old_extension VALUES(?,?)", ("demo", "historical row"))
    with pytest.raises(BridgeError, match="old_extension"):
        bridge.export_project_bundle("demo")


@pytest.mark.skipif(os.name == "nt", reason="symlink creation may require Windows privileges")
def test_import_accepts_a_canonicalizable_parent_path(tmp_path):
    first = CCCMCP(tmp_path / "first-home")
    source = tmp_path / "source"
    source.mkdir()
    first.register_project(source, "demo")
    bundle = first.export_project_bundle("demo")

    parent = tmp_path / "real-parent"
    target = parent / "target"
    target.mkdir(parents=True)
    alias = tmp_path / "alias-parent"
    alias.symlink_to(parent, target_is_directory=True)
    second = CCCMCP(tmp_path / "second-home")
    result = second.import_project_bundle(bundle, alias / "target")
    assert result["root"] == str(target)


def test_project_bundle_cli_and_web_roundtrip(tmp_path):
    home = tmp_path / "home"
    source = tmp_path / "source"
    source.mkdir()
    bridge = CCCMCP(home)
    bridge.register_project(source, "demo")
    backup = tmp_path / "backup.json"
    runner = CliRunner()
    exported = runner.invoke(
        app, ["--home", str(home), "project", "export", "demo", "--out", str(backup)]
    )
    assert exported.exit_code == 0, exported.output
    assert backup.is_file()

    web = TestClient(create_app(home, token="test-token"), headers={"X-CCC-UI-Token": "test-token"})
    response = web.get("/api/projects/demo/export")
    assert response.status_code == 200
    assert response.headers["content-disposition"].endswith('ccc-mcp-demo.json"')
    assert TestClient(web.app).get("/api/projects/demo/export").status_code == 401

    target = tmp_path / "target"
    target.mkdir()
    import_home = tmp_path / "import-home"
    imported = runner.invoke(
        app, ["--home", str(import_home), "project", "import", str(backup), "--path", str(target)]
    )
    assert imported.exit_code == 0, imported.output
    assert CCCMCP(import_home).project("demo")["root"] == str(target)

    other = tmp_path / "web-target"
    other.mkdir()
    web2 = TestClient(
        create_app(tmp_path / "web-home", token="test-token"),
        headers={"X-CCC-UI-Token": "test-token"},
    )
    assert (
        web2.post(f"/api/projects/import?path={other}", content=response.content).status_code == 200
    )
    assert (
        TestClient(web2.app)
        .post(f"/api/projects/import?path={other}", content=response.content)
        .status_code
        == 401
    )
