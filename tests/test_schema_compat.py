"""A database written by ccc-mcp 0.4.0 opens with the current code and loses nothing."""

import json
import re
import shutil
import sqlite3
import subprocess
from pathlib import Path

import pytest

from ccc_mcp.adapters.storage import Store
from ccc_mcp.service import CCCMCP

LEGACY_SQL = Path(__file__).parent / "fixtures" / "legacy_schema_0_4_0.sql"
NEW_TABLES = {
    "custom_roles", "custom_role_versions", "custom_role_events", "role_snapshots",
    "role_snapshot_trust_events", "task_roster_pins", "execution_prompts", "approval_rosters",
}
NEW_COLUMNS = {
    "agent_executions": {"role_ref", "role_snapshot", "step_id"},
    "pending_dispatch": {"role_ref", "role_snapshot", "step_id"},
    "flow_gate_results": {"role_ref", "step_id"},
    "flow_escalations": {"role_ref", "step_id"},
    "evaluations": {"role_ref", "role_snapshot", "step_id"},
    "reputation_events": {"role_ref", "role_snapshot", "step_id"},
    "meeting_clarifications": {
        "asker_role_ref", "asker_step_id", "responder_role_ref", "responder_step_id",
    },
}


def columns(path, table):
    with sqlite3.connect(path) as connection:
        return [row[1] for row in connection.execute(f"PRAGMA table_info({table})")]


def tables(path):
    with sqlite3.connect(path) as connection:
        return {
            row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }


def legacy_home(tmp_path):
    home = tmp_path / "legacy-home"
    home.mkdir()
    with sqlite3.connect(home / "bridge.sqlite3") as connection:
        connection.executescript(LEGACY_SQL.read_text(encoding="utf-8"))
    return home


def seed_legacy_rows(home):
    stamp = "2026-01-01T00:00:00+00:00"
    with sqlite3.connect(home / "bridge.sqlite3") as connection:
        connection.execute("INSERT INTO projects(id,root,name,created) VALUES('old','/x','Old',?)",
                           (stamp,))
        connection.execute(
            "INSERT INTO agent_executions(id,project_id,task_id,agent,role,context_version,"
            "instruction,adapter,options,status,package,created) VALUES("
            "'EXEC-old','old','TASK-0001','codex','REVIEW',1,'check','real','{}','SUCCEEDED',"
            "'{}',?)", (stamp,))
        connection.execute(
            "INSERT INTO approvals(id,project_id,task_id,plan_id,plan_version,plan_hash,"
            "context_version,status,created) VALUES('APP-old','old','TASK-0001','PLAN-A',1,'h',"
            "1,'APPROVED',?)", (stamp,))
        connection.execute(
            "INSERT INTO flow_gate_results(id,project_id,task_id,step_index,role,"
            "source_execution_id,verdict,strictness,detail,created) VALUES("
            "'GATE-old','old','TASK-0001',2,'REVIEW','EXEC-old','PASS',NULL,'{}',?)", (stamp,))
        connection.execute(
            "INSERT INTO evaluations(id,project_id,task_id,role,agent,model,task_type,"
            "source_execution_id,version,trigger,quality_score,evidence_coverage,task_status,"
            "scoring_details,evidence,disagreements,schema_version,created) VALUES("
            "'EV-old','old','TASK-0001','REVIEW','codex',NULL,'general','EXEC-old',1,'manual',"
            "80.0,0.9,'PASSED','{}','[]','[]','1',?)", (stamp,))


def test_the_frozen_legacy_schema_really_is_the_old_one():
    text = LEGACY_SQL.read_text(encoding="utf-8")
    assert "agent_executions" in text
    for name in ("role_ref", "role_snapshot", "step_id", "custom_roles", "roster_hash"):
        assert name not in text


def test_opening_an_old_database_adds_columns_and_tables_and_nothing_else(tmp_path):
    home = legacy_home(tmp_path)
    path = home / "bridge.sqlite3"
    before = {name: columns(path, name) for name in tables(path)}
    Store(home)

    after_tables = tables(path)
    assert NEW_TABLES <= after_tables
    assert set(before) <= after_tables
    for table, old in before.items():
        new = columns(path, table)
        assert new[:len(old)] == old, f"{table}: existing columns must keep their order"
        assert set(new) - set(old) == NEW_COLUMNS.get(table, set()), table


def test_existing_rows_survive_with_empty_new_columns(tmp_path):
    home = legacy_home(tmp_path)
    seed_legacy_rows(home)
    Store(home)
    with sqlite3.connect(home / "bridge.sqlite3") as connection:
        connection.row_factory = sqlite3.Row
        execution = connection.execute("SELECT * FROM agent_executions").fetchone()
        assert execution["id"] == "EXEC-old" and execution["role"] == "REVIEW"
        assert (execution["role_ref"], execution["role_snapshot"], execution["step_id"]) == (
            None, None, None)
        approval = connection.execute("SELECT * FROM approvals").fetchone()
        assert approval["status"] == "APPROVED"
        gate = connection.execute("SELECT * FROM flow_gate_results").fetchone()
        assert gate["verdict"] == "PASS" and gate["role_ref"] is None
        assert connection.execute("SELECT role_ref FROM evaluations").fetchone()[0] is None
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_reopening_is_idempotent(tmp_path):
    home = legacy_home(tmp_path)
    seed_legacy_rows(home)
    Store(home)
    snapshot = {name: columns(home / "bridge.sqlite3", name)
                for name in tables(home / "bridge.sqlite3")}
    Store(home)
    Store(home)
    assert snapshot == {name: columns(home / "bridge.sqlite3", name)
                        for name in tables(home / "bridge.sqlite3")}


def test_legacy_history_tables_keep_their_immutability_triggers(tmp_path):
    home = legacy_home(tmp_path)
    seed_legacy_rows(home)
    Store(home)
    with sqlite3.connect(home / "bridge.sqlite3") as connection:
        with pytest.raises(sqlite3.DatabaseError, match="immutable"):
            connection.execute("UPDATE evaluations SET quality_score=1")
        with pytest.raises(sqlite3.DatabaseError, match="immutable"):
            connection.execute("DELETE FROM flow_gate_results")


def test_the_application_runs_on_an_old_database(tmp_path):
    """Open the service on a legacy database and do ordinary work: the new structures must
    not get in the way of anything that existed."""
    home = legacy_home(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("x = 1\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "-c", "user.name=T", "-c", "user.email=t@example.invalid",
         "commit", "-qm", "init"], check=True)
    service = CCCMCP(home)
    service.register_project(repo, "demo", "legacy database project")
    task = service.create_task("demo", "Add a thing", ["it works"])
    assert task["id"] == "TASK-0001"
    assert [item["id"] for item in service.list_tasks("demo")] == ["TASK-0001"]
    assert service.list_custom_roles("demo") == []
    shutil.rmtree(repo, ignore_errors=True)


def test_the_new_schema_statements_are_all_safe_to_run_twice(tmp_path):
    """Every new object is created with IF NOT EXISTS, so a half-migrated database recovers."""
    source = (Path(__file__).parents[1] / "src/ccc_mcp/adapters/storage.py").read_text("utf-8")
    new_part = source[source.index("-- Custom roles"):]
    statements = re.findall(r"CREATE (?:TABLE|VIEW|TRIGGER|INDEX)[^;]*;", new_part, flags=re.S)
    assert statements
    assert all("IF NOT EXISTS" in statement for statement in statements)
    assert json.dumps(NEW_TABLES, default=sorted)


def test_a_bundle_without_custom_roles_carries_none_of_the_new_columns(tmp_path):
    """Rows that never used the new columns are exported exactly as before, so the bundle still
    imports on a ccc-mcp that predates them."""
    from ccc_mcp.adapters.storage import ADDED_COLUMNS, now

    source = tmp_path / "source"
    source.mkdir()
    service = CCCMCP(tmp_path / "home")
    service.register_project(source, "demo")
    service.create_task("demo", "Plan a day trip")
    with service.store.transaction() as conn:
        conn.execute(
            "INSERT INTO agent_executions(id,project_id,task_id,agent,role,context_version,"
            "instruction,adapter,options,status,package,created) VALUES("
            "'EXEC-1','demo','TASK-0001','codex','REVIEW',1,'x','fake','{}','SUCCEEDED','{}',?)",
            (now(),),
        )
    payload = json.loads(service.export_project_bundle("demo"))["payload"]
    assert payload["tables"]["agent_executions"], "the test needs an execution row"
    for table, added in ADDED_COLUMNS.items():
        for row in payload["tables"].get(table, []):
            assert not set(row) & set(added), table
    target = tmp_path / "target"
    target.mkdir()
    other = CCCMCP(tmp_path / "other-home")
    assert other.import_project_bundle(service.export_project_bundle("demo"), target)["tasks"] == 1
