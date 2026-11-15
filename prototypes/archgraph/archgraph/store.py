"""Prototype persistence: one SQLite file, document-per-row. Stores structure and verdicts only.

Stored: GraphVersions (file states, nodes with nid and hashes, edges, identity events) and
TaskDeltas. Not stored: file contents, excluded content, host test output.
"""

import json
import sqlite3
from datetime import UTC, datetime

_SCHEMA = """
CREATE TABLE IF NOT EXISTS graph_versions(
    gv_id TEXT PRIMARY KEY, label TEXT NOT NULL, doc TEXT NOT NULL, created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS task_deltas(
    task TEXT PRIMARY KEY, gv_id TEXT NOT NULL, doc TEXT NOT NULL, created TEXT NOT NULL);
"""


class Store:
    def __init__(self, path):
        self.conn = sqlite3.connect(str(path))
        self.conn.executescript(_SCHEMA)

    def save_graph(self, graph):
        self.conn.execute(
            "INSERT OR REPLACE INTO graph_versions(gv_id, label, doc, created) VALUES (?,?,?,?)",
            (
                graph.gv_id,
                graph.label,
                json.dumps(graph.as_dict(), ensure_ascii=False),
                datetime.now(UTC).isoformat(),
            ),
        )
        self.conn.commit()

    def load_graph(self, gv_id):
        row = self.conn.execute("SELECT doc FROM graph_versions WHERE gv_id=?", (gv_id,)).fetchone()
        if row is None:
            return None
        from .graph import GraphVersion

        return GraphVersion.from_dict(json.loads(row[0]))

    def save_delta(self, task, gv_id, delta):
        self.conn.execute(
            "INSERT OR REPLACE INTO task_deltas(task, gv_id, doc, created) VALUES (?,?,?,?)",
            (
                task,
                gv_id,
                json.dumps(delta, ensure_ascii=False, default=str),
                datetime.now(UTC).isoformat(),
            ),
        )
        self.conn.commit()

    def load_delta(self, task):
        row = self.conn.execute("SELECT doc FROM task_deltas WHERE task=?", (task,)).fetchone()
        return json.loads(row[0]) if row else None

    def close(self):
        self.conn.close()
