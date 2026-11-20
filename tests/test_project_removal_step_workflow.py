"""Removing a project that ran a step workflow (Standard, Full, ...).

Before this fix, removal of such a project failed on a foreign key. That was true of the released
code too (reproduced on the commit before any Stage 0/A/B change); phase runs make it common, so the
tables a step workflow leaves behind are now deleted with the project (the whole database is archived
first, as before). Other projects' rows are untouched and the history protections come back.
"""

import sqlite3

import pytest

from ccc_mcp.adapters.storage import STEP_WORKFLOW_NO_DELETE_TRIGGERS, STEP_WORKFLOW_TABLES, now
from ccc_mcp.worker import run_execution


def seed(service, project, tmp_path):
    service.authorize_project_local(project)
    service.configure_workflow(project, "standard")
    task = service.create_task(project, f"work in {project}")
    dispatched = service.dispatch_agent(
        project, task["id"], "auto", "SA", service.latest(project)["version"], "analyse",
        adapter="fake")
    run_execution(service, dispatched["execution_id"])
    stamp = now()
    with service.store.transaction() as conn:
        conn.execute(
            "INSERT INTO evaluations(id,project_id,task_id,role,agent,model,task_type,"
            "source_execution_id,version,trigger,quality_score,evidence_coverage,task_status,"
            "scoring_details,evidence,disagreements,schema_version,created) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"EV-{project}", project, task["id"], "SA", "codex", None, "t", dispatched["execution_id"],
             1, "manual", 0.5, 1.0, "OK", "{}", "{}", "{}", "1", stamp))
        conn.execute(
            "INSERT INTO reputation_events(id,project_id,task_id,role,agent,model,task_type,"
            "source_execution_id,outcome,created) VALUES(?,?,?,?,?,?,?,?,?,?)",
            (f"RE-{project}", project, task["id"], "SA", "codex", None, "t",
             dispatched["execution_id"], "ok", stamp))
        conn.execute(
            "INSERT INTO task_deletions(id,project_id,task_id,identity,created) VALUES(?,?,?,?,?)",
            (f"TD-{project}", project, "TASK-GHOST", "tester", stamp))
    return task


def count(service, table, project):
    return service.store.one(f"SELECT COUNT(*) AS n FROM {table} WHERE project_id=?", (project,))["n"]


def test_a_step_workflow_project_is_removed_and_a_neighbour_is_untouched(bridge, human, tmp_path):
    other = tmp_path / "neighbour"
    other.mkdir()
    bridge.register_project(other, "keep", "kept")
    seed(bridge, "demo", tmp_path)
    seed(bridge, "keep", tmp_path)
    for table in STEP_WORKFLOW_TABLES:
        assert count(bridge, table, "demo") >= 0
    assert count(bridge, "task_flows", "demo") == 1 and count(bridge, "evaluations", "demo") == 1

    bridge.remove_project_web("demo", "REMOVE demo")

    for table in (*STEP_WORKFLOW_TABLES, "tasks", "agent_executions"):
        assert count(bridge, table, "demo") == 0, table
    for table in ("task_flows", "evaluations", "reputation_events", "task_deletions", "tasks",
                  "agent_executions"):
        assert count(bridge, table, "keep") >= 1, f"{table} of the other project must survive"
    with sqlite3.connect(bridge.store.path) as connection:
        triggers = {row[0] for row in connection.execute(
            "SELECT name FROM sqlite_master WHERE type='trigger'")}
        assert set(STEP_WORKFLOW_NO_DELETE_TRIGGERS) <= triggers          # protection is back
        with pytest.raises(sqlite3.DatabaseError, match="immutable"):
            connection.execute("DELETE FROM evaluations")                  # (keep's row is still there)


def test_removal_of_a_classic_project_is_unchanged(bridge, human):
    bridge.create_task("demo", "classic work")
    bridge.remove_project_web("demo", "REMOVE demo")
    assert count(bridge, "tasks", "demo") == 0
