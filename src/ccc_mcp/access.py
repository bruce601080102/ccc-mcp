"""One local project authorization for ordinary ChatGPT file editing."""

import uuid
from pathlib import Path

from .errors import PermissionDenied, VersionConflict
from .security import digest
from .storage import now


class ProjectAccessHub:
    def get_project_access(self, project_id):
        project = self.project(project_id)
        event = self.store.one(
            "SELECT * FROM project_access_events WHERE project_id=? ORDER BY seq DESC LIMIT 1",
            (project_id,),
        )
        active = bool(
            event and event["access"] == "read-write" and event["root"] == project["root"]
        )
        return {
            "mode": "read-write" if active else "read-only",
            "root": project["root"],
            "authorization_id": event["id"] if active else None,
            "identity": event["identity"] if active else None,
            "authorized_at": event["created"] if active else None,
            "write_tool": "write_workspace",
            "requires_task": False,
            "requires_per_file_approval": False if active else None,
        }

    def authorize_project_local(self, project_id):
        from .approval import confirm_local

        access = self.get_project_access(project_id)
        if access["mode"] == "read-write":
            return access
        project = self.project(project_id)
        identity = confirm_local(
            f"授權 ChatGPT 讀寫整個專案：{project_id}\n{project['root']}\n\n"
            "授權後可直接新增、修改此專案內的文字檔，不需逐檔核准或建立 Task／PLAN。\n"
            "修改會核對 SHA256 並保存原始內容及紀錄；憑證、管理資料與外部路徑仍不開放。\n"
            "授權持續有效，直到執行 project revoke。",
            project_id,
        )
        with self.lock(project_id):
            if self.project(project_id)["root"] != project["root"]:
                raise VersionConflict("Project root changed during authorization")
            self.store.execute(
                "INSERT INTO project_access_events(id,project_id,root,access,identity,created) "
                "VALUES(?,?,?,'read-write',?,?)",
                ("AUTH-" + uuid.uuid4().hex[:16], project_id, project["root"], identity, now()),
            )
            self._save_settings(project_id, self.get_project_settings(project_id))
        return self.get_project_access(project_id)

    def revoke_project_local(self, project_id):
        from .approval import local_identity

        identity = local_identity()
        with self.lock(project_id):
            project = self.project(project_id)
            self.store.execute(
                "INSERT INTO project_access_events(id,project_id,root,access,identity,created) "
                "VALUES(?,?,?,'read-only',?,?)",
                ("AUTH-" + uuid.uuid4().hex[:16], project_id, project["root"], identity, now()),
            )
            # Revoke old granular grants too, so they cannot silently retain original-file access.
            settings = self.get_project_settings(project_id)
            settings["writable_files"] = []
            settings["writable_roots"] = []
            self._save_settings(project_id, settings)
        return self.get_project_access(project_id)

    def _require_project_write(self, project_id):
        access = self.get_project_access(project_id)
        if access["mode"] != "read-write":
            raise PermissionDenied(
                f"Project is read-only. Run ccc-mcp project authorize {project_id} locally once."
            )
        return access

    def write_authorized_project_file(self, project_id, path, content, expected_sha256=None):
        access = self._require_project_write(project_id)
        # Reuse durable before/after storage and recovery, but authority is the project grant,
        # not a fictitious per-edit human approval. No task or context change is necessary.
        previous = self.store.one(
            "SELECT e.id FROM workspace_edits e JOIN project_write_receipts r ON e.id=r.edit_id "
            "WHERE e.project_id=? AND e.path=? AND e.before_sha256 IS ? AND e.sha256=? "
            "AND r.authorization_id=? ORDER BY e.created DESC LIMIT 1",
            (
                project_id,
                Path(path.replace("\\", "/")).as_posix(),
                expected_sha256,
                digest(content.encode("utf-8")),
                access["authorization_id"],
            ),
        )
        if previous:
            with self.lock(project_id):
                self._require_project_write(project_id)
                return self._apply_workspace_edit(
                    project_id, self._workspace_edit(project_id, previous["id"])
                )
        edit = self.propose_workspace_edit(project_id, path, content, expected_sha256, _fresh=True)
        with self.lock(project_id):
            current = self._require_project_write(project_id)
            if current["authorization_id"] != access["authorization_id"]:
                raise PermissionDenied(
                    "Project authorization changed before writing; read and retry"
                )
            with self.store.transaction() as conn:
                conn.execute(
                    "INSERT INTO project_write_receipts VALUES(?,?)",
                    (edit["id"], access["authorization_id"]),
                )
                conn.execute(
                    "UPDATE workspace_edits SET status='APPROVED',identity=?,approved_at=? WHERE id=?",
                    (access["identity"], access["authorized_at"], edit["id"]),
                )
            return self._apply_workspace_edit(
                project_id, self._workspace_edit(project_id, edit["id"])
            )
