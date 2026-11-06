"""Portable, project-scoped settings and history snapshots.

The repository itself is deliberately outside this format. SQLite remains the source
of truth for the saved Context, reports, task conversation and workflow evidence.
"""

import json
import sqlite3
import tempfile
import uuid
from pathlib import Path

from ..adapters.storage import now
from ..infra.config import metadata_name
from ..infra.errors import BridgeError
from ..infra.security import canonical, digest, safe_path
from .context import identifier

FORMAT = "ccc-mcp-project"
VERSION = 1
MAX_BYTES = 100 * 1024 * 1024

# Parent rows precede their dependants. Keep this explicit: a new project-scoped table
# must be considered before a backup can claim it contains the full project history.
TABLES = (
    "project_settings",
    "project_access_events",
    "workspace_edits",
    "project_write_receipts",
    "workspace_batches",
    "context_versions",
    "context_patches",
    "tasks",
    "task_events",
    "task_controls",
    "task_interventions",
    "plans",
    "approvals",
    "decisions",
    "agent_executions",
    "score_rounds",
    "artifacts",
    "pending_dispatch",
    "task_deletions",
    "task_flows",
    "flow_gate_results",
    "flow_escalations",
    "meeting_clarifications",
    "evaluations",
    "reputation_events",
)
SEQUENCE_TABLES = {"project_access_events", "task_events", "task_interventions"}
OPTIONAL_TABLES = {
    # Older installations created this scoring ledger. Current code no longer
    # creates it, but a project export must still carry its historical rows.
    "score_rounds": """CREATE TABLE score_rounds (
        id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
        task_id TEXT NOT NULL, round INTEGER NOT NULL,
        source_execution_id TEXT NOT NULL,
        layer1_score INTEGER NOT NULL, layer1_breakdown TEXT NOT NULL,
        layer2_score INTEGER NOT NULL, layer2_rationale TEXT NOT NULL,
        total_score INTEGER NOT NULL, pass_threshold INTEGER NOT NULL,
        decision TEXT NOT NULL, created TEXT NOT NULL)""",
}


def _quoted(name):
    return '"' + name.replace('"', '""') + '"'


def _columns(conn, table):
    return [row[1] for row in conn.execute(f"PRAGMA table_info({_quoted(table)})")]


def _check_schema(conn, project_id=None):
    known = set(TABLES) | {"projects", "project_deletions", "operator_settings"}
    for (table,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table'"):
        if table.startswith("sqlite_"):
            continue
        if "project_id" in _columns(conn, table) and table not in known:
            # A historical extension with no rows for this project cannot lose
            # anything. Other projects' extensions must not block an import.
            if (
                project_id is not None
                and conn.execute(
                    f"SELECT 1 FROM {_quoted(table)} WHERE project_id=? LIMIT 1", (project_id,)
                ).fetchone()
            ):
                raise BridgeError(f"Project bundle does not yet support table {table}")
    for table in TABLES:
        if table not in OPTIONAL_TABLES and not _columns(conn, table):
            raise BridgeError(f"Project bundle requires table {table}")


class ProjectBundleHub:
    def export_project_bundle(self, project_id):
        project = self.project(project_id)
        # Import any local settings.json edits before taking the SQLite snapshot.
        self.get_project_settings(project_id)
        with self.lock(project_id):
            with self.store.connect() as conn:
                conn.execute("BEGIN")
                _check_schema(conn, project_id)
                busy = conn.execute(
                    "SELECT 1 FROM agent_executions WHERE project_id=? "
                    "AND status IN ('QUEUED','RUNNING') LIMIT 1",
                    (project_id,),
                ).fetchone()
                pending = conn.execute(
                    "SELECT 1 FROM pending_dispatch WHERE project_id=? LIMIT 1",
                    (project_id,),
                ).fetchone()
                host_test = conn.execute(
                    "SELECT 1 FROM task_controls WHERE project_id=? AND host_test_running=1 LIMIT 1",
                    (project_id,),
                ).fetchone()
                if busy or pending or host_test:
                    raise BridgeError("Wait for the current agent handoff before exporting")
                tables = {}
                for table in TABLES:
                    if table in OPTIONAL_TABLES and not _columns(conn, table):
                        tables[table] = []
                        continue
                    if table == "project_write_receipts":
                        query = (
                            "SELECT r.* FROM project_write_receipts r "
                            "JOIN workspace_edits e ON e.id=r.edit_id "
                            "WHERE e.project_id=? ORDER BY r.rowid"
                        )
                    else:
                        query = f"SELECT * FROM {table} WHERE project_id=? ORDER BY rowid"
                    rows = [dict(row) for row in conn.execute(query, (project_id,))]
                    if table == "project_settings":
                        for row in rows:
                            settings = json.loads(row["content"])
                            for key in (
                                "mcp_servers",
                                "read_roots",
                                "writable_files",
                                "writable_roots",
                                "worktree_dir",
                                "project_access",
                            ):
                                settings.pop(key, None)
                            row["content"] = canonical(settings)
                    tables[table] = rows
        payload = {
            "format": FORMAT,
            "version": VERSION,
            "exported_at": now(),
            "project": {key: project[key] for key in ("id", "name", "created")},
            "tables": tables,
        }
        bundle = canonical({"payload": payload, "sha256": digest(canonical(payload).encode())})
        if len(bundle.encode("utf-8")) > MAX_BYTES:
            raise BridgeError("Project bundle exceeds 100 MB")
        return bundle

    def import_project_bundle(self, data, path):
        if isinstance(data, bytes):
            if len(data) > MAX_BYTES:
                raise BridgeError("Project bundle exceeds 100 MB")
            try:
                data = data.decode("utf-8")
            except UnicodeError as exc:
                raise BridgeError("Project bundle must be UTF-8 JSON") from exc
        if not isinstance(data, str) or len(data.encode("utf-8")) > MAX_BYTES:
            raise BridgeError("Project bundle must be UTF-8 JSON under 100 MB")
        try:
            envelope = json.loads(data)
        except json.JSONDecodeError as exc:
            raise BridgeError("Invalid project bundle JSON") from exc
        if not isinstance(envelope, dict) or set(envelope) != {"payload", "sha256"}:
            raise BridgeError("Invalid project bundle envelope")
        payload = envelope["payload"]
        if (
            not isinstance(payload, dict)
            or digest(canonical(payload).encode()) != envelope["sha256"]
        ):
            raise BridgeError("Project bundle checksum mismatch")
        if payload.get("format") != FORMAT or payload.get("version") != VERSION:
            raise BridgeError("Unsupported project bundle version")
        source = payload.get("project")
        tables = payload.get("tables")
        if (
            not isinstance(source, dict)
            or not isinstance(tables, dict)
            or not set(tables) <= set(TABLES)
        ):
            raise BridgeError("Project bundle is missing project records")
        tables = {table: tables.get(table, []) for table in TABLES}
        if not all(isinstance(rows, list) for rows in tables.values()):
            raise BridgeError("Invalid project bundle tables")
        if tables["pending_dispatch"] or any(
            isinstance(row, dict) and row.get("status") in {"QUEUED", "RUNNING"}
            for row in tables["agent_executions"]
        ):
            raise BridgeError("Project bundle contains an active agent handoff")
        if any(
            isinstance(row, dict) and row.get("host_test_running")
            for row in tables["task_controls"]
        ):
            raise BridgeError("Project bundle contains an active host test")
        if not isinstance(source.get("id"), str):
            raise BridgeError("Project bundle has an invalid ID")
        project_id = identifier(source["id"])
        candidate = Path(path).expanduser().absolute()
        if candidate.is_symlink() or (
            hasattr(candidate, "is_junction") and candidate.is_junction()
        ):
            raise BridgeError("Cannot import into a symlink or junction")
        root = candidate.resolve()
        if not root.is_dir():
            raise BridgeError("Import target must be an existing project directory")
        metadata = safe_path(root, metadata_name(root))
        if metadata.exists():
            raise BridgeError("Target already has .ccc-mcp records; choose a clean project folder")
        try:
            with tempfile.TemporaryDirectory(prefix=".ccc-mcp-import-", dir=root):
                pass
        except OSError as exc:
            raise BridgeError(f"Import target is not writable: {exc}") from exc

        for row in tables["artifacts"]:
            if not isinstance(row, dict) or not isinstance(row.get("path"), str):
                raise BridgeError("Invalid artifact path in project bundle")
            parts = Path(row["path"]).parts
            if not parts or parts[0] not in {".ccc-mcp", ".agentbridge"}:
                raise BridgeError("Project bundle artifact is outside its metadata folder")
            safe_path(root, row["path"])
            if not isinstance(row.get("content"), str) or digest(
                row["content"].encode()
            ) != row.get("sha256"):
                raise BridgeError("Project bundle artifact checksum mismatch")

        with self.lock(project_id):
            try:
                with self.store.transaction() as conn:
                    self._insert_bundle(conn, project_id, root, source, tables)
            except sqlite3.DatabaseError as exc:
                raise BridgeError(f"Invalid project bundle records: {exc}") from exc
            self.sync_documents(project_id)
        return {
            "project_id": project_id,
            "root": str(root),
            "read_only": True,
            "tasks": len(tables["tasks"]),
            "context_versions": len(tables["context_versions"]),
        }

    def _insert_bundle(self, conn, project_id, root, source, tables):
        _check_schema(conn)
        if conn.execute(
            "SELECT 1 FROM projects WHERE id=? OR root=?", (project_id, str(root))
        ).fetchone():
            raise BridgeError("Project ID or folder is already registered")
        if conn.execute(
            "SELECT 1 FROM project_deletions WHERE project_id=?", (project_id,)
        ).fetchone():
            raise BridgeError("This project ID was permanently retired on this device")
        conn.execute(
            "INSERT INTO projects(id,root,name,created) VALUES(?,?,?,?)",
            (
                project_id,
                str(root),
                source.get("name") or root.name,
                source.get("created") or now(),
            ),
        )
        for table in TABLES:
            rows = tables[table]
            if table in OPTIONAL_TABLES and not _columns(conn, table):
                if not rows:
                    continue
                conn.execute(OPTIONAL_TABLES[table])
            columns = _columns(conn, table)
            for row in rows:
                if not isinstance(row, dict) or not set(row) <= set(columns):
                    raise BridgeError(f"Invalid {table} record columns")
                if table != "project_write_receipts" and row.get("project_id") != project_id:
                    raise BridgeError(f"Foreign project record in {table}")
                values = dict(row)
                if table == "project_settings":
                    try:
                        settings = json.loads(values["content"])
                    except (KeyError, TypeError, ValueError) as exc:
                        raise BridgeError("Invalid project settings") from exc
                    if not isinstance(settings, dict):
                        raise BridgeError("Invalid project settings")
                    # Machine-specific grants and executable MCP registrations are
                    # never authority transported by a downloaded file.
                    for key in (
                        "mcp_servers",
                        "read_roots",
                        "writable_files",
                        "writable_roots",
                        "worktree_dir",
                        "project_access",
                    ):
                        settings.pop(key, None)
                    settings["workspace_mode"] = "worktree"
                    settings["workflow_mode"] = "direct"
                    values["content"] = canonical(settings)
                insert_columns = [
                    c
                    for c in columns
                    if c in values and not (c == "seq" and table in SEQUENCE_TABLES)
                ]
                sql = f"INSERT INTO {table} ({','.join(insert_columns)}) VALUES ({','.join('?' for _ in insert_columns)})"
                conn.execute(sql, tuple(values[column] for column in insert_columns))
        if len(tables["project_settings"]) != 1 or not tables["context_versions"]:
            raise BridgeError("Project bundle lacks settings or Context history")
        # Preserve the old access audit, then explicitly revoke it on this
        # device. Even an import to the identical path starts read-only.
        conn.execute(
            "INSERT INTO project_access_events(id,project_id,root,access,identity,created) "
            "VALUES(?,?,?,'read-only','import',?)",
            ("AUTH-" + uuid.uuid4().hex[:16], project_id, str(root), now()),
        )
        for task in tables["tasks"]:
            if task["state"] not in {"COMPLETED", "CANCELLED"}:
                conn.execute(
                    "INSERT INTO task_controls(project_id,task_id,paused) VALUES(?,?,1) "
                    "ON CONFLICT(project_id,task_id) DO UPDATE SET paused=1,host_test_running=0",
                    (project_id, task["id"]),
                )
        violations = conn.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise BridgeError("Project bundle has broken record references")
