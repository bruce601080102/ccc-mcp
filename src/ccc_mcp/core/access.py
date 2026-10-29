"""One local project authorization for the Agent-led project workstream."""

import json
import os
import shutil
import sqlite3
import stat
import uuid
from datetime import UTC, datetime
from pathlib import Path

from ..adapters.storage import now
from ..infra.config import metadata_name
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


def _remove_description(project_id, project, delete_files):
    files = (
        "專案根目錄及其中所有檔案也會從磁碟永久刪除；無法復原。"
        if delete_files
        else "原始專案資料夾及程式碼會保留，僅 .ccc-mcp 管理資料會搬入封存。"
    )
    return (
        f"移除 ccc-mcp 專案註冊：{project_id}\n{project['root']}\n\n"
        "此專案會從 project list 消失；其 ccc-mcp 歷史與設定先備份到 archives，"
        "之後可使用同一 ID 重新註冊。" + files
    )


def _remove_description_en(project_id, project, delete_files):
    files = (
        "The entire project folder and all its contents will be permanently deleted."
        if delete_files
        else "The project folder and source files will be kept; only ccc-mcp metadata is archived."
    )
    return (
        f"Remove ccc-mcp registration: {project_id}\n{project['root']}\n\n"
        "The project will disappear from project list. Its ccc-mcp history and settings "
        "are backed up under archives, and the same ID can be registered again. " + files
    )


def _retry_readonly_delete(operation, path, exc_info):
    """Allow Windows read-only Git objects to be removed; never follow symlinks."""
    error = exc_info[1]
    if os.name != "nt" or not isinstance(error, PermissionError):
        raise error
    os.chmod(path, os.stat(path, follow_symlinks=False).st_mode | stat.S_IWRITE)
    operation(path)


class ProjectAccessHub:
    def _removable_project(self, project_id):
        # Do not call project(): its missing-root recovery creates a new folder, which
        # would violate the default "keep existing files unchanged" removal behavior.
        row = self.store.one("SELECT * FROM projects WHERE id=?", (project_id,))
        if not row or self.store.one(
            "SELECT 1 FROM project_deletions WHERE project_id=?", (project_id,)
        ):
            raise BridgeError("Project is not registered")
        root = Path(row["root"])
        if root.is_symlink() or (hasattr(root, "is_junction") and root.is_junction()):
            raise BridgeError("Refusing to remove a project whose root is a symlink or junction")
        return row

    def remove_project_local(self, project_id, delete_files=False):
        from .approval import confirm_local

        return self._remove_project(project_id, bool(delete_files), confirm_local)

    def remove_project_web(self, project_id, typed_confirmation, delete_files=False):
        from .approval import confirm_web

        def confirm(_description, challenge):
            return confirm_web(challenge, typed_confirmation)

        return self._remove_project(project_id, bool(delete_files), confirm)

    def remove_preview(self, project_id, delete_files=False):
        project = self._removable_project(project_id)
        if delete_files and Path(project["root"]).exists():
            self._check_removable_root(Path(project["root"]))
        return {
            "description": _remove_description(project_id, project, delete_files),
            "description_en": _remove_description_en(project_id, project, delete_files),
            "challenge": f"REMOVE FILES {project_id}" if delete_files else f"REMOVE {project_id}",
            "delete_files": bool(delete_files),
        }

    def _check_removable_root(self, root):
        if root.is_symlink() or (hasattr(root, "is_junction") and root.is_junction()):
            raise BridgeError("Refusing to delete a symlink or junction project root")
        resolved = root.resolve(strict=True)
        if root.parent == root or Path.home().resolve().is_relative_to(resolved):
            raise BridgeError("Refusing to delete the home directory or an ancestor")
        if self.home.resolve().is_relative_to(resolved):
            raise BridgeError("Refusing to delete the ccc-mcp data directory or an ancestor")
        if Path.cwd().resolve().is_relative_to(resolved):
            raise BridgeError("Current working directory is inside this project; run ccc-mcp from outside it before deleting files")
        for other in self.store.all("SELECT id,root FROM projects"):
            other_root = Path(other["root"]).resolve()
            if other_root != resolved and other_root.is_relative_to(resolved):
                raise BridgeError(f"Another registered project is inside this directory: {other['id']}")
        return resolved

    def _remove_project(self, project_id, delete_files, confirm):
        project = self._removable_project(project_id)
        if delete_files and Path(project["root"]).exists():
            self._check_removable_root(Path(project["root"]))
        if self.store.one(
            "SELECT 1 FROM agent_executions WHERE project_id=? AND status IN ('QUEUED','RUNNING')",
            (project_id,),
        ):
            raise BridgeError("Wait for or cancel the active execution before removing the project")
        challenge = f"REMOVE FILES {project_id}" if delete_files else f"REMOVE {project_id}"
        identity = confirm(_remove_description(project_id, project, delete_files), challenge)
        with self.lock(project_id):
            current = self._removable_project(project_id)
            if current["root"] != project["root"]:
                raise VersionConflict("Project root changed during removal")
            if self.store.one(
                "SELECT 1 FROM agent_executions WHERE project_id=? "
                "AND status IN ('QUEUED','RUNNING')", (project_id,)
            ):
                raise BridgeError("Wait for or cancel the active execution before removing the project")
            root = Path(project["root"])
            if delete_files and root.exists():
                self._check_removable_root(root)
            stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            archive = self.home / "archives" / f"{project_id}-removed-{stamp}-{uuid.uuid4().hex[:6]}"
            archive.mkdir(parents=True, mode=0o700)
            with sqlite3.connect(self.store.path) as source, sqlite3.connect(archive / "bridge.sqlite3") as target:
                source.backup(target)
            try:
                (archive / "bridge.sqlite3").chmod(0o600)
            except OSError:
                pass
            metadata = root / metadata_name(root)
            moved = []
            staged_root = None
            rows = self.store.all(
                "SELECT DISTINCT task_id,worktree FROM agent_executions "
                "WHERE project_id=? AND worktree IS NOT NULL", (project_id,)
            )
            settings_row = self.store.one(
                "SELECT content FROM project_settings WHERE project_id=?", (project_id,)
            )
            settings = json.loads(settings_row["content"]) if settings_row else {}
            worktree_dir = Path(settings.get("worktree_dir", str(self.home / "worktrees")))
            worktrees = []
            for row in rows:
                path = Path(row["worktree"])
                if not path.exists() or path == root or (delete_files and path.is_relative_to(root)):
                    continue
                expected = worktree_dir / project_id / row["task_id"]
                if path.resolve() != expected.resolve():
                    raise BridgeError(f"Refusing to move unexpected worktree: {path}")
                worktrees.append((row["task_id"], path))
            try:
                if delete_files and root.exists():
                    staged_root = root.with_name(f".{root.name}.ccc-mcp-removing-{uuid.uuid4().hex[:8]}")
                    root.rename(staged_root)
                    moved.append((staged_root, root))
                # Archive managed worktrees outside a folder scheduled for deletion;
                # never follow an unexpected execution-supplied path.
                for task_id, path in worktrees:
                    target = archive / "worktrees" / task_id
                    target.parent.mkdir(exist_ok=True)
                    if not target.exists():
                        shutil.move(str(path), str(target))
                        moved.append((target, path))
                if staged_root is None and (metadata.exists() or metadata.is_symlink()):
                    target = archive / "project-metadata"
                    shutil.move(str(metadata), str(target))
                    moved.append((target, metadata))
                with self.store.transaction() as conn:
                    trigger_names = (
                        "project_access_no_delete", "project_receipt_no_delete",
                        "workspace_edits_no_delete", "workspace_batches_no_delete",
                        "context_no_delete", "decisions_no_delete",
                        "meeting_clarifications_no_delete",
                    )
                    trigger_sql = [
                        row[0] for row in conn.execute(
                            "SELECT sql FROM sqlite_master WHERE type='trigger' "
                            f"AND name IN ({','.join('?' for _ in trigger_names)})",
                            trigger_names,
                        )
                    ]
                    for trigger in trigger_names:
                        conn.execute(f"DROP TRIGGER {trigger}")
                    conn.execute(
                        "DELETE FROM project_write_receipts WHERE edit_id IN "
                        "(SELECT id FROM workspace_edits WHERE project_id=?)", (project_id,)
                    )
                    for table in (
                        "pending_dispatch", "meeting_clarifications", "approvals", "plans",
                        "task_events", "agent_executions", "artifacts", "decisions",
                        "context_patches", "context_versions", "tasks", "workspace_batches",
                        "workspace_edits", "project_access_events", "project_settings",
                    ):
                        conn.execute(f"DELETE FROM {table} WHERE project_id=?", (project_id,))
                    conn.execute("DELETE FROM projects WHERE id=?", (project_id,))
                    # Restore every immutable-history trigger before committing.
                    for sql in trigger_sql:
                        conn.execute(sql)
            except BaseException:
                for source, destination in reversed(moved):
                    if source.exists() and not destination.exists():
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        shutil.move(str(source), str(destination))
                raise
            # The original directory path is already gone. A cleanup failure leaves the
            # staging directory intact and reports its path instead of claiming deletion.
            cleanup_error = None
            if staged_root is not None:
                try:
                    shutil.rmtree(staged_root, onerror=_retry_readonly_delete)
                except OSError as exc:
                    cleanup_error = str(exc)
            return {
                "project_id": project_id,
                "removed": True,
                "files_deleted": bool(delete_files and cleanup_error is None),
                "directory_was_missing": not root.exists() and staged_root is None,
                "archive": str(archive),
                "identity": identity,
                **({"cleanup_error": cleanup_error, "remaining_path": str(staged_root)} if cleanup_error else {}),
            }

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
