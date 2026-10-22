import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import UTC, datetime

from ..infra.security import canonical


def now() -> str:
    return datetime.now(UTC).isoformat()


SCHEMA = """
CREATE TABLE IF NOT EXISTS project_settings (
 project_id TEXT PRIMARY KEY REFERENCES projects(id), content TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS operator_settings (
 key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS project_access_events (
 seq INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL,
 project_id TEXT NOT NULL REFERENCES projects(id), root TEXT NOT NULL,
 access TEXT NOT NULL, identity TEXT NOT NULL, created TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS project_access_no_update BEFORE UPDATE ON project_access_events
 BEGIN SELECT RAISE(ABORT, 'Project access history is immutable'); END;
CREATE TRIGGER IF NOT EXISTS project_access_no_delete BEFORE DELETE ON project_access_events
 BEGIN SELECT RAISE(ABORT, 'Project access history is immutable'); END;
CREATE TABLE IF NOT EXISTS project_write_receipts (
 edit_id TEXT PRIMARY KEY REFERENCES workspace_edits(id),
 authorization_id TEXT NOT NULL REFERENCES project_access_events(id));
CREATE TRIGGER IF NOT EXISTS project_receipt_no_update BEFORE UPDATE ON project_write_receipts
 BEGIN SELECT RAISE(ABORT, 'Project write receipts are immutable'); END;
CREATE TRIGGER IF NOT EXISTS project_receipt_no_delete BEFORE DELETE ON project_write_receipts
 BEGIN SELECT RAISE(ABORT, 'Project write receipts are immutable'); END;
CREATE TABLE IF NOT EXISTS workspace_edits (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
 path TEXT NOT NULL, before_content TEXT, before_sha256 TEXT,
 content TEXT NOT NULL, sha256 TEXT NOT NULL, diff TEXT NOT NULL,
 comment_check TEXT NOT NULL, status TEXT NOT NULL,
 identity TEXT, created TEXT NOT NULL, approved_at TEXT, applied_at TEXT);
CREATE TRIGGER IF NOT EXISTS workspace_edit_content_immutable
 BEFORE UPDATE OF project_id,path,before_content,before_sha256,content,sha256,diff,comment_check
 ON workspace_edits
 BEGIN SELECT RAISE(ABORT, 'Workspace edit proposals are immutable'); END;
CREATE TRIGGER IF NOT EXISTS workspace_edits_no_delete BEFORE DELETE ON workspace_edits
 BEGIN SELECT RAISE(ABORT, 'Workspace edit history is immutable'); END;
CREATE TABLE IF NOT EXISTS projects (
 id TEXT PRIMARY KEY, root TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
 created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS workspace_batches (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id), edits TEXT NOT NULL,
 status TEXT NOT NULL, identity TEXT, created TEXT NOT NULL, approved_at TEXT, applied_at TEXT);
CREATE TRIGGER IF NOT EXISTS workspace_batch_members_immutable
 BEFORE UPDATE OF project_id,edits ON workspace_batches
 BEGIN SELECT RAISE(ABORT, 'Workspace batch members are immutable'); END;
CREATE TRIGGER IF NOT EXISTS workspace_batches_no_delete BEFORE DELETE ON workspace_batches
 BEGIN SELECT RAISE(ABORT, 'Workspace batch history is immutable'); END;
CREATE TABLE IF NOT EXISTS context_versions (
 project_id TEXT NOT NULL REFERENCES projects(id), version INTEGER NOT NULL,
 snapshot TEXT NOT NULL, metadata TEXT NOT NULL, created TEXT NOT NULL,
 PRIMARY KEY(project_id, version));
CREATE TABLE IF NOT EXISTS context_patches (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
 payload TEXT NOT NULL, committed_version INTEGER);
CREATE TABLE IF NOT EXISTS tasks (
 id TEXT NOT NULL, project_id TEXT NOT NULL REFERENCES projects(id),
 requirement TEXT NOT NULL, acceptance TEXT NOT NULL, state TEXT NOT NULL,
 created TEXT NOT NULL, PRIMARY KEY(project_id,id));
CREATE TABLE IF NOT EXISTS task_events (
 seq INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL, task_id TEXT NOT NULL,
 old_state TEXT, new_state TEXT NOT NULL, reason TEXT NOT NULL, created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS plans (
 project_id TEXT NOT NULL, task_id TEXT NOT NULL, id TEXT NOT NULL,
 version INTEGER NOT NULL, context_version INTEGER NOT NULL, content TEXT NOT NULL,
 hash TEXT NOT NULL, created TEXT NOT NULL, PRIMARY KEY(project_id,task_id,id,version));
CREATE TABLE IF NOT EXISTS approvals (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL, task_id TEXT NOT NULL,
 plan_id TEXT NOT NULL, plan_version INTEGER NOT NULL, plan_hash TEXT NOT NULL,
 context_version INTEGER NOT NULL, status TEXT NOT NULL,
 identity TEXT, created TEXT NOT NULL, approved_at TEXT);
CREATE TABLE IF NOT EXISTS decisions (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL, task_id TEXT NOT NULL,
 content TEXT NOT NULL, reason TEXT NOT NULL, approver TEXT NOT NULL,
 status TEXT NOT NULL, context_version INTEGER NOT NULL, created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS agent_executions (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL, task_id TEXT NOT NULL,
 agent TEXT NOT NULL, role TEXT NOT NULL, context_version INTEGER NOT NULL,
 instruction TEXT NOT NULL, adapter TEXT NOT NULL, options TEXT NOT NULL,
 status TEXT NOT NULL, package TEXT NOT NULL, result TEXT, error TEXT,
 worktree TEXT, cancel_requested INTEGER NOT NULL DEFAULT 0,
 created TEXT NOT NULL, finished TEXT);
CREATE UNIQUE INDEX IF NOT EXISTS one_live_execution
 ON agent_executions(project_id,task_id) WHERE status IN ('QUEUED','RUNNING');
CREATE TABLE IF NOT EXISTS artifacts (
 project_id TEXT NOT NULL, path TEXT NOT NULL, content TEXT NOT NULL,
 sha256 TEXT NOT NULL, created TEXT NOT NULL, PRIMARY KEY(project_id,path));
CREATE TRIGGER IF NOT EXISTS decisions_no_update BEFORE UPDATE ON decisions
 BEGIN SELECT RAISE(ABORT, 'Decisions are append-only'); END;
CREATE TRIGGER IF NOT EXISTS decisions_no_delete BEFORE DELETE ON decisions
 BEGIN SELECT RAISE(ABORT, 'Decisions are append-only'); END;
CREATE TRIGGER IF NOT EXISTS context_no_update BEFORE UPDATE ON context_versions
 BEGIN SELECT RAISE(ABORT, 'Context history is immutable'); END;
CREATE TRIGGER IF NOT EXISTS context_no_delete BEFORE DELETE ON context_versions
 BEGIN SELECT RAISE(ABORT, 'Context history is immutable'); END;
CREATE TABLE IF NOT EXISTS pending_dispatch (
 project_id TEXT NOT NULL REFERENCES projects(id), task_id TEXT NOT NULL,
 source_execution_id TEXT NOT NULL, role TEXT NOT NULL,
 context_version INTEGER NOT NULL, plan_content TEXT,
 adapter TEXT NOT NULL, fake_scenario TEXT NOT NULL,
 committed_json TEXT NOT NULL, created TEXT NOT NULL,
 PRIMARY KEY(project_id, task_id));
-- A project ID is retired the moment it has one row here; it then disappears from
-- `project list` and every other command refuses it (see ContextHub.project). Append-only
-- and irreversible by design, like every other audit table in this schema: deregistering
-- never deletes the project's Context/task/decision/evidence history, only hides and
-- blocks further use of the ID.
CREATE TABLE IF NOT EXISTS project_deletions (
 id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
 identity TEXT NOT NULL, created TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS idx_project_deletions_project ON project_deletions(project_id);
CREATE TRIGGER IF NOT EXISTS project_deletions_no_update BEFORE UPDATE ON project_deletions
 BEGIN SELECT RAISE(ABORT, 'Project deletion history is immutable'); END;
CREATE TRIGGER IF NOT EXISTS project_deletions_no_delete BEFORE DELETE ON project_deletions
 BEGIN SELECT RAISE(ABORT, 'Project deletion history is immutable'); END;
-- Read-path indexes only: every query below already filters by project_id (often also
-- task_id) and these columns have no other supporting index, so lookups on these tables
-- were full scans. Purely additive (IF NOT EXISTS), so they cannot change any query's
-- result, only its cost, and apply lazily to existing bridge.sqlite3 files on next start.
CREATE INDEX IF NOT EXISTS idx_agent_executions_project_task_created
 ON agent_executions(project_id, task_id, created);
CREATE INDEX IF NOT EXISTS idx_agent_executions_project_created
 ON agent_executions(project_id, created);
CREATE INDEX IF NOT EXISTS idx_decisions_project_created
 ON decisions(project_id, created);
CREATE INDEX IF NOT EXISTS idx_task_events_project_task_seq
 ON task_events(project_id, task_id, seq);
CREATE INDEX IF NOT EXISTS idx_approvals_project_task_created
 ON approvals(project_id, task_id, created);
CREATE INDEX IF NOT EXISTS idx_workspace_edits_project_path_created
 ON workspace_edits(project_id, path, created);
CREATE INDEX IF NOT EXISTS idx_workspace_batches_project
 ON workspace_batches(project_id);
CREATE INDEX IF NOT EXISTS idx_project_access_events_project_seq
 ON project_access_events(project_id, seq);
"""


class Store:
    def __init__(self, home):
        self.path = home / "bridge.sqlite3"
        self._transaction_state = threading.local()
        with self.connect() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.executescript(SCHEMA)

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=30000")
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    @contextmanager
    def transaction(self):
        with self.connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            self._transaction_state.active = True
            try:
                yield conn
            finally:
                self._transaction_state.active = False

    @property
    def in_transaction(self):
        return getattr(self._transaction_state, "active", False)

    def all(self, query, args=()):
        with self.connect() as conn:
            return [dict(r) for r in conn.execute(query, args).fetchall()]

    def one(self, query, args=()):
        rows = self.all(query, args)
        return rows[0] if rows else None

    def execute(self, query, args=()):
        with self.connect() as conn:
            conn.execute(query, args)


def decode(row, *fields):
    if row:
        row = dict(row)
        for field in fields:
            if row.get(field) is not None:
                row[field] = json.loads(row[field])
    return row


def event(conn, project, task, old, new, reason):
    conn.execute(
        "INSERT INTO task_events(project_id,task_id,old_state,new_state,reason,created) "
        "VALUES(?,?,?,?,?,?)",
        (project, task, old, new, reason, now()),
    )


def artifact(conn, project, path, content):
    from ..infra.security import digest, reject_secrets

    reject_secrets(content)
    conn.execute(
        "INSERT INTO artifacts VALUES(?,?,?,?,?)",
        (project, path, content, digest(content.encode()), now()),
    )


def dump(value):
    return canonical(value)
