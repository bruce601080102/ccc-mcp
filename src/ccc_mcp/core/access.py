"""One local project authorization for the Agent-led project workstream."""

import uuid
from pathlib import Path

from ..adapters.storage import now
from ..infra.errors import BridgeError, PermissionDenied, VersionConflict
from ..infra.security import digest


def _authorize_description(project_id, project):
    return (
        f"授權 ccc-mcp 的 Agent 工作串讀寫整個專案：{project_id}\n{project['root']}\n\n"
        "授權後由設定的 SA／實作／審查 Agent 持續處理需求，不需逐檔核准；"
        "工作串與 PLAN 由 ccc-mcp 內部管理，不需逐次 approve 或 accept。\n"
        "修改會核對 SHA256 並保存原始內容及紀錄；憑證、管理資料與外部路徑仍不開放。\n"
        "此授權同時代表同意：若有設定 test_command（ccc-mcp config test-command），"
        "每輪實作完成、REVIEW 派工前會自動在本機執行該命令並存證；未設定則不會自動執行任何命令。\n"
        "授權持續有效，直到執行 project revoke。"
    )


def _deregister_description(project_id, project):
    return (
        f"永久註銷專案：{project_id}\n{project['root']}\n\n"
        "此操作無法復原：之後 `ccc-mcp project list` 不再顯示它，任何指令對這個 "
        "project ID 都會拒絕執行。歷史 Context、task、決策與驗收證據仍完整保留在 "
        "ccc-mcp 的資料庫中以供追溯，但不會再被存取；這個 ID 之後也不能重新註冊。\n"
        "此操作不會刪除或搬移磁碟上的檔案，請自行用 Finder／檔案管理器處理。"
    )


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
            "write_tool": "write_task_file" if active else None,
            "requires_task": active,
            "requires_per_file_approval": False if active else None,
            "direct_write_allowed": False,
        }

    def authorize_project_local(self, project_id):
        from .approval import confirm_local

        return self._authorize_project(project_id, confirm_local)

    def authorize_project_web(self, project_id, typed_confirmation):
        """Same state transition and audit event as `authorize_project_local`, but the
        human confirmation comes from a request already verified (by interfaces/web.py)
        to carry this process's own local Web UI session token, instead of a TTY.
        The operator must still type back the project id in the browser dialog, the
        same confirmation strength the terminal flow requires.
        """
        from .approval import confirm_web

        def confirm(description, challenge):
            return confirm_web(challenge, typed_confirmation)

        return self._authorize_project(project_id, confirm)

    def authorize_preview(self, project_id):
        """Read-only: the exact description and typed-challenge text an authorize call
        will ask the operator to confirm, so a UI can show it before the operator
        commits to typing the challenge back (CLI prints the same text itself)."""
        project = self.project(project_id)
        return {"description": _authorize_description(project_id, project), "challenge": project_id}

    def _authorize_project(self, project_id, confirm):
        access = self.get_project_access(project_id)
        if access["mode"] == "read-write":
            with self.lock(project_id):
                self._save_settings(project_id, self.get_project_settings(project_id))
            self.reconcile_authorized_workstream(project_id)
            self.compact_consumed_context(project_id)
            return self.get_project_settings(project_id)["project_access"]
        project = self.project(project_id)
        identity = confirm(_authorize_description(project_id, project), project_id)
        with self.lock(project_id):
            if self.project(project_id)["root"] != project["root"]:
                raise VersionConflict("Project root changed during authorization")
            self.store.execute(
                "INSERT INTO project_access_events(id,project_id,root,access,identity,created) "
                "VALUES(?,?,?,'read-write',?,?)",
                ("AUTH-" + uuid.uuid4().hex[:16], project_id, project["root"], identity, now()),
            )
            self._save_settings(project_id, self.get_project_settings(project_id))
        self.reconcile_authorized_workstream(project_id)
        self.compact_consumed_context(project_id)
        return self.get_project_settings(project_id)["project_access"]

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
            # Revocation must remain possible even if an edited settings file
            # is malformed; the immutable access event is authoritative.
            settings = self.get_project_settings(project_id, import_file=False)
            settings["writable_files"] = []
            settings["writable_roots"] = []
            self._save_settings(project_id, settings)
        return self.get_project_access(project_id)

    def deregister_project_local(self, project_id):
        """Permanently retire a project ID: it disappears from `project list` and every
        other command refuses it afterward (see ContextHub.project). Irreversible by
        design — there is no undo — and local-terminal-only, like authorize/revoke.

        This never touches the project's files on disk (the operator removes those
        separately, e.g. via Finder) and never deletes a single row of its historical
        Context/task/decision/evidence data; it only appends one `project_deletions`
        record, consistent with every other audit table in this schema.
        """
        from .approval import confirm_local

        return self._deregister_project(project_id, confirm_local)

    def deregister_project_web(self, project_id, typed_confirmation):
        """Same irreversible state transition as `deregister_project_local`, confirmed
        through the local Web UI session instead of a TTY — see `authorize_project_web`."""
        from .approval import confirm_web

        def confirm(description, challenge):
            return confirm_web(challenge, typed_confirmation)

        return self._deregister_project(project_id, confirm)

    def deregister_preview(self, project_id):
        """Read-only: the description and typed-challenge text `project delete` /
        `deregister_project_web` will ask the operator to confirm."""
        project = self.project(project_id)
        return {
            "description": _deregister_description(project_id, project),
            "challenge": f"DELETE {project_id}",
        }

    def _deregister_project(self, project_id, confirm):
        project = self.project(project_id)
        if self.get_project_access(project_id)["mode"] == "read-write":
            raise PermissionDenied(
                f"Revoke project authorization first: ccc-mcp project revoke {project_id}"
            )
        if self.store.one(
            "SELECT 1 FROM agent_executions WHERE project_id=? AND status IN ('QUEUED','RUNNING')",
            (project_id,),
        ):
            raise BridgeError("Wait for or cancel the active execution before deregistering")
        identity = confirm(_deregister_description(project_id, project), f"DELETE {project_id}")
        with self.lock(project_id):
            if self.get_project_access(project_id)["mode"] == "read-write":
                raise PermissionDenied(
                    f"Revoke project authorization first: ccc-mcp project revoke {project_id}"
                )
            if self.store.one(
                "SELECT 1 FROM agent_executions WHERE project_id=? "
                "AND status IN ('QUEUED','RUNNING')",
                (project_id,),
            ):
                raise BridgeError("Wait for or cancel the active execution before deregistering")
            self.store.execute(
                "INSERT INTO project_deletions(id,project_id,identity,created) VALUES(?,?,?,?)",
                ("DEL-" + uuid.uuid4().hex[:16], project_id, identity, now()),
            )
        return {"project_id": project_id, "deregistered": True, "identity": identity}

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
