"""Small, locally approved edits to exact allowlisted project-root files."""

import difflib
import uuid
from pathlib import Path

from .errors import BridgeError, PermissionDenied, VersionConflict
from .security import atomic_write, canonical, comment_check, digest, reject_secrets
from .storage import decode, now
from .workspace import allowed_path

MAX_EDIT_BYTES = 200_000


class DirectEditHub:
    def _edit_target(self, project_id, path, require_allowed=True):
        root = Path(self.project(project_id)["root"])
        target = allowed_path(root, path)
        relative = target.relative_to(root).as_posix()
        if target.exists() and not target.is_file():
            raise BridgeError("Direct edits require a file path, not a directory")
        settings = self.get_project_settings(project_id)
        allowed = (
            settings["project_access"]["mode"] == "read-write"
            or Path(relative) in {Path(p) for p in settings["writable_files"]}
            or any(Path(relative).is_relative_to(Path(p)) for p in settings["writable_roots"])
        )
        if require_allowed and not allowed:
            raise PermissionDenied(
                "File is not writable; local operator must config write-root or write-file first"
            )
        return target, relative

    def configure_write_root(self, project_id, path):
        with self.lock(project_id):
            root = Path(self.project(project_id)["root"])
            target = root if path in {".", ""} else allowed_path(root, path)
            if not target.is_dir():
                raise BridgeError("Write root must be an existing project-relative directory")
            relative = target.relative_to(root).as_posix()
            settings = self.get_project_settings(project_id)
            if Path(relative) not in {Path(p) for p in settings["writable_roots"]}:
                settings["writable_roots"].append(relative)
            return self._save_settings(project_id, settings)

    def revoke_write_root(self, project_id, path):
        with self.lock(project_id):
            settings = self.get_project_settings(project_id)
            settings["writable_roots"] = [
                p for p in settings["writable_roots"] if Path(p) != Path(path.replace("\\", "/"))
            ]
            return self._save_settings(project_id, settings)

    def configure_write_file(self, project_id, path):
        with self.lock(project_id):
            target, relative = self._edit_target(project_id, path, require_allowed=False)
            self._edit_content(target)
            settings = self.get_project_settings(project_id)
            if Path(relative) not in {Path(p) for p in settings["writable_files"]}:
                settings["writable_files"].append(relative)
            return self._save_settings(project_id, settings)

    def revoke_write_file(self, project_id, path):
        with self.lock(project_id):
            settings = self.get_project_settings(project_id)
            settings["writable_files"] = [
                p for p in settings["writable_files"] if Path(p) != Path(path.replace("\\", "/"))
            ]
            return self._save_settings(project_id, settings)

    @staticmethod
    def _edit_content(target):
        if not target.exists():
            return None, None
        if target.stat().st_size > MAX_EDIT_BYTES:
            raise BridgeError("Direct edit exceeds 200 KB")
        data = target.read_bytes()
        if len(data) > MAX_EDIT_BYTES:
            raise BridgeError("Direct edit exceeds 200 KB")
        try:
            text = data.decode("utf-8")
        except UnicodeError:
            raise BridgeError("Direct edits support UTF-8 text only") from None
        if "\0" in text:
            raise BridgeError("Binary content is not supported")
        reject_secrets(text)
        return text, digest(data)

    def propose_workspace_edit(self, project_id, path, content, expected_sha256=None, _fresh=False):
        if len(content.encode("utf-8")) > MAX_EDIT_BYTES or "\0" in content:
            raise BridgeError("Direct edit must be UTF-8 text under 200 KB")
        reject_secrets(content)
        with self.lock(project_id):
            target, relative = self._edit_target(project_id, path)
            before, before_sha = self._edit_content(target)
            if before_sha != expected_sha256:
                raise VersionConflict(
                    "File changed or exists; read its current SHA256 before proposing"
                )
            if before == content:
                raise BridgeError("No file changes proposed")
            check = comment_check(
                {relative: before.encode("utf-8")} if before is not None else {},
                {relative: content.encode("utf-8")},
            )
            if check["violations"]:
                raise PermissionDenied("Direct edit would remove original Python comments")
            sha = digest(content.encode("utf-8"))
            existing = self.store.one(
                "SELECT id FROM workspace_edits WHERE project_id=? AND path=? "
                "AND before_sha256 IS ? AND sha256=? AND status IN ('PENDING','APPROVED') "
                "ORDER BY created DESC LIMIT 1",
                (project_id, relative, before_sha, sha),
            )
            if existing and not _fresh:
                return self.get_workspace_edit(project_id, existing["id"])
            edit_id = "EDIT-" + uuid.uuid4().hex[:16]
            diff_lines = difflib.unified_diff(
                (before or "").splitlines(keepends=True),
                content.splitlines(keepends=True),
                fromfile="before/" + relative,
                tofile="after/" + relative,
            )
            diff = "".join(
                line if line.endswith("\n") else line + "\n\\ No newline at end of file\n"
                for line in diff_lines
            )
            self.store.execute(
                "INSERT INTO workspace_edits "
                "(id,project_id,path,before_content,before_sha256,content,sha256,diff,"
                "comment_check,status,created) VALUES(?,?,?,?,?,?,?,?,?,'PENDING',?)",
                (
                    edit_id,
                    project_id,
                    relative,
                    before,
                    before_sha,
                    content,
                    sha,
                    diff,
                    canonical(check),
                    now(),
                ),
            )
            return self.get_workspace_edit(project_id, edit_id)

    def _workspace_edit(self, project_id, edit_id):
        self.project(project_id)
        row = self.store.one(
            "SELECT * FROM workspace_edits WHERE project_id=? AND id=?", (project_id, edit_id)
        )
        if not row:
            raise BridgeError("Workspace edit not found in this project")
        return decode(row, "comment_check")

    def get_workspace_edit(self, project_id, edit_id, include_content=False):
        if edit_id.startswith("BATCH-"):
            return self.get_workspace_batch(project_id, edit_id, include_content)
        row = self._workspace_edit(project_id, edit_id)
        if not include_content:
            row.pop("before_content")
            row.pop("content")
        row["approval_command"] = f"ccc-mcp --project {project_id} workspace approve {edit_id}"
        row["target"] = str(Path(self.project(project_id)["root"]) / row["path"])
        receipt = self.store.one(
            "SELECT authorization_id FROM project_write_receipts WHERE edit_id=?", (edit_id,)
        )
        if receipt:
            row["authorization_kind"] = "project"
            row["authorization_id"] = receipt["authorization_id"]
            row.pop("approval_command", None)
        return row

    def approve_workspace_edit_local(self, project_id, edit_id):
        from .approval import confirm_local

        if edit_id.startswith("BATCH-"):
            return self.approve_workspace_batch_local(project_id, edit_id)
        if self._edit_batch(project_id, edit_id):
            raise PermissionDenied("This edit belongs to a batch; approve the BATCH instead")
        row = self._workspace_edit(project_id, edit_id)
        if row["status"] != "PENDING":
            raise BridgeError("Only pending workspace edits can be approved")
        description = (
            f"直接修改專案原檔：{project_id}/{row['path']}\n"
            f"原檔 SHA256：{row['before_sha256']}\n"
            f"修改後 SHA256：{row['sha256']}\n\n{row['diff']}\n"
            f"註解檢查：{canonical(row['comment_check'])}\n"
            "請核對完整差異並確認原始註解保留。此核准只授權這一份修改。"
        )
        identity = confirm_local(description, edit_id)
        with self.lock(project_id):
            current = self._workspace_edit(project_id, edit_id)
            if current["status"] != "PENDING":
                raise BridgeError("Workspace edit state changed during review")
            target, _ = self._edit_target(project_id, row["path"])
            if self._edit_content(target)[1] != row["before_sha256"]:
                raise VersionConflict("File changed during review; propose a new edit")
            self.store.execute(
                "UPDATE workspace_edits SET status='APPROVED',identity=?,approved_at=? WHERE id=?",
                (identity, now(), edit_id),
            )
        return self.get_workspace_edit(project_id, edit_id)

    def write_workspace(
        self, project_id, edit_id=None, *, path=None, content=None, expected_sha256=None
    ):
        """Write an authorized project file; retain legacy approved-edit compatibility."""
        if self.get_project_settings(project_id)["workflow_mode"] == "agent-required":
            raise PermissionDenied(
                "Direct project writes are disabled by agent-required workflow mode. "
                "Create a Task and dispatch the configured Codex/Claude roles; implementation files "
                "must be written through an approved execution with write_task_file."
            )
        if path is not None:
            if edit_id is not None or content is None:
                raise BridgeError(
                    "Use path/content for a direct write, or edit_id for a legacy edit"
                )
            return self.write_authorized_project_file(project_id, path, content, expected_sha256)
        if not edit_id:
            raise BridgeError("Provide the project-relative path and full content")
        if content is not None or expected_sha256 is not None:
            raise BridgeError("Full content and expected_sha256 require a project-relative path")
        if edit_id.startswith("BATCH-"):
            return self.write_workspace_batch(project_id, edit_id)
        with self.lock(project_id):
            if self._edit_batch(project_id, edit_id):
                raise PermissionDenied("This edit belongs to a batch; apply the BATCH instead")
            row = self._workspace_edit(project_id, edit_id)
            return self._apply_workspace_edit(project_id, row)

    def _apply_workspace_edit(self, project_id, row):
        edit_id = row["id"]
        receipt = self.store.one(
            "SELECT authorization_id FROM project_write_receipts WHERE edit_id=?", (edit_id,)
        )
        if receipt:
            access = self._require_project_write(project_id)
            if access["authorization_id"] != receipt["authorization_id"]:
                raise PermissionDenied("The project grant for this write was revoked or replaced")
        target, _ = self._edit_target(project_id, row["path"])
        if row["status"] not in {"APPROVED", "APPLYING", "APPLIED"} or not row["identity"]:
            raise PermissionDenied("Direct edits require local approval of this exact proposal")
        _, actual_sha = self._edit_content(target)
        if row["status"] in {"APPLYING", "APPLIED"} and actual_sha == row["sha256"]:
            # Recover a crash after replacement but before the receipt was committed.
            self.store.execute(
                "UPDATE workspace_edits SET status='APPLIED',applied_at=COALESCE(applied_at,?) WHERE id=?",
                (now(), edit_id),
            )
            return self.get_workspace_edit(project_id, edit_id)
        if row["status"] == "APPLIED" or actual_sha != row["before_sha256"]:
            raise VersionConflict("Project file changed; refusing to overwrite it")
        self.store.execute("UPDATE workspace_edits SET status='APPLYING' WHERE id=?", (edit_id,))
        atomic_write(target, row["content"])
        self.store.execute(
            "UPDATE workspace_edits SET status='APPLIED',applied_at=? WHERE id=?", (now(), edit_id)
        )
        return self.get_workspace_edit(project_id, edit_id)

    def _edit_batch(self, project_id, edit_id):
        return self.store.one(
            "SELECT id FROM workspace_batches WHERE project_id=? AND EXISTS "
            "(SELECT 1 FROM json_each(workspace_batches.edits) WHERE value=?)",
            (project_id, edit_id),
        )

    def _workspace_batch(self, project_id, batch_id):
        self.project(project_id)
        row = self.store.one(
            "SELECT * FROM workspace_batches WHERE project_id=? AND id=?", (project_id, batch_id)
        )
        if not row:
            raise BridgeError("Workspace batch not found in this project")
        return decode(row, "edits")

    def create_workspace_batch(self, project_id, edit_ids):
        if not 1 <= len(edit_ids) <= 500 or len(set(edit_ids)) != len(edit_ids):
            raise BridgeError("A batch requires 1-500 distinct EDIT IDs")
        with self.lock(project_id):
            existing = self.store.one(
                "SELECT id FROM workspace_batches WHERE project_id=? AND edits=?",
                (project_id, canonical(edit_ids)),
            )
            if existing:
                return self.get_workspace_batch(project_id, existing["id"])
            rows = [self._workspace_edit(project_id, eid) for eid in edit_ids]
            if len({Path(row["path"]) for row in rows}) != len(rows):
                raise BridgeError("A batch cannot contain multiple proposals for the same file")
            for row in rows:
                if row["status"] != "PENDING" or self._edit_batch(project_id, row["id"]):
                    raise BridgeError("Batch members must be pending edits outside other batches")
                target, _ = self._edit_target(project_id, row["path"])
                if self._edit_content(target)[1] != row["before_sha256"]:
                    raise VersionConflict("A batch source changed; propose a new edit")
            batch_id = "BATCH-" + uuid.uuid4().hex[:16]
            self.store.execute(
                "INSERT INTO workspace_batches(id,project_id,edits,status,created) "
                "VALUES(?,?,?,'PENDING',?)",
                (batch_id, project_id, canonical(edit_ids), now()),
            )
            return self.get_workspace_batch(project_id, batch_id)

    def get_workspace_batch(self, project_id, batch_id, include_content=False):
        row = self._workspace_batch(project_id, batch_id)
        row["files"] = [
            self.get_workspace_edit(project_id, eid, include_content) for eid in row["edits"]
        ]
        row["approval_command"] = f"ccc-mcp --project {project_id} workspace approve {batch_id}"
        return row

    def approve_workspace_batch_local(self, project_id, batch_id):
        from .approval import confirm_local

        batch = self.get_workspace_batch(project_id, batch_id)
        if batch["status"] != "PENDING":
            raise BridgeError("Only pending batches can be approved")
        description = f"整批核准原專案修改：{project_id}，共 {len(batch['files'])} 個檔案\n"
        description += "\n".join(
            f"{row['path']} SHA256:{row['before_sha256']} → {row['sha256']}\n"
            f"{row['diff']}\n註解檢查：{canonical(row['comment_check'])}"
            for row in batch["files"]
        )
        identity = confirm_local(description + "\n請確認整批差異及原始註解保留。", batch_id)
        with self.lock(project_id):
            if self._workspace_batch(project_id, batch_id)["status"] != "PENDING":
                raise BridgeError("Batch state changed during review")
            for row in batch["files"]:
                if self._workspace_edit(project_id, row["id"])["status"] != "PENDING":
                    raise BridgeError("Batch member state changed")
                target, _ = self._edit_target(project_id, row["path"])
                if self._edit_content(target)[1] != row["before_sha256"]:
                    raise VersionConflict("A batch source changed during review")
            stamp = now()
            with self.store.transaction() as conn:
                for eid in batch["edits"]:
                    conn.execute(
                        "UPDATE workspace_edits SET status='APPROVED',identity=?,approved_at=? WHERE id=?",
                        (identity, stamp, eid),
                    )
                conn.execute(
                    "UPDATE workspace_batches SET status='APPROVED',identity=?,approved_at=? WHERE id=?",
                    (identity, stamp, batch_id),
                )
        return self.get_workspace_batch(project_id, batch_id)

    def write_workspace_batch(self, project_id, batch_id):
        with self.lock(project_id):
            batch = self._workspace_batch(project_id, batch_id)
            if batch["status"] not in {"APPROVED", "APPLYING", "APPLIED"} or not batch["identity"]:
                raise PermissionDenied("Batch requires local approval")
            rows = [self._workspace_edit(project_id, eid) for eid in batch["edits"]]
            for row in rows:
                target, _ = self._edit_target(project_id, row["path"])
                sha = self._edit_content(target)[1]
                applied = row["status"] in {"APPLYING", "APPLIED"} and sha == row["sha256"]
                if not applied and (row["status"] == "APPLIED" or sha != row["before_sha256"]):
                    raise VersionConflict("A batch source changed; no further files were written")
            self.store.execute(
                "UPDATE workspace_batches SET status='APPLYING' WHERE id=?", (batch_id,)
            )
            for row in rows:
                self._apply_workspace_edit(project_id, row)
            self.store.execute(
                "UPDATE workspace_batches SET status='APPLIED',applied_at=COALESCE(applied_at,?) WHERE id=?",
                (now(), batch_id),
            )
            return self.get_workspace_batch(project_id, batch_id)
