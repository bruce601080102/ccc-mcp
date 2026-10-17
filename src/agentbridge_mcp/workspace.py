import json
import uuid
from pathlib import Path

from .configuration import directory
from .errors import BridgeError, PermissionDenied, VersionConflict
from .evidence import implementation_evidence
from .git import worktree
from .models import AgentReport
from .security import atomic_write, canonical, digest, redact, reject_secrets, safe_path
from .storage import decode

DENIED = {
    ".git",
    ".agentbridge",
    ".ssh",
    ".codex",
    ".claude",
    ".aws",
    ".azure",
    ".venv",
    "node_modules",
}


def allowed_path(root, path):
    parts = Path(path.replace("\\", "/")).parts
    if any(
        part.lower() in DENIED
        or part.lower().startswith(".env")
        or part.lower() in {"id_rsa", "id_ed25519", "credentials", "credentials.json"}
        for part in parts
    ):
        raise PermissionDenied(
            "Managed metadata, credentials and dependency directories are not exposed"
        )
    return safe_path(directory(root), path)


class WorkspaceHub:
    def pending_chatgpt_handoffs(self, project_id):
        self.project(project_id)
        rows = self.store.all(
            "SELECT id,task_id,role,instruction,package,worktree,options FROM agent_executions "
            "WHERE project_id=? AND agent='chatgpt' AND adapter='interactive' "
            "AND status='RUNNING' AND cancel_requested=0 ORDER BY created",
            (project_id,),
        )
        return [decode(row, "package", "options") for row in rows]

    def _interactive_execution(self, project_id, execution_id):
        self.project(project_id)
        row = decode(
            self.store.one(
                "SELECT * FROM agent_executions WHERE project_id=? AND id=?",
                (project_id, execution_id),
            ),
            "options",
            "package",
        )
        if not row or row["agent"] != "chatgpt" or row["adapter"] != "interactive":
            raise PermissionDenied("Not a ChatGPT interactive execution")
        if row["status"] != "RUNNING" or row["cancel_requested"]:
            raise PermissionDenied("Execution is no longer active")
        if self.latest(project_id)["version"] != row["context_version"]:
            raise VersionConflict("Context changed; dispatch again before editing or submitting")
        if row["role"] not in {"SA", "REVIEW"}:
            self.approved_plan(project_id, row["task_id"])
        return row

    def prepare_chatgpt_execution(self, execution_id):
        row = self.store.one("SELECT * FROM agent_executions WHERE id=?", (execution_id,))
        project_id = row["project_id"]
        try:
            with self.lock(project_id):
                if row["cancel_requested"]:
                    raise PermissionDenied("Execution cancelled")
                if self.latest(project_id)["version"] != row["context_version"]:
                    raise VersionConflict("Context changed before ChatGPT handoff")
                cwd = Path(self.project(project_id)["root"])
                if row["role"] not in {"SA", "REVIEW"}:
                    self.approved_plan(project_id, row["task_id"])
                    cwd = worktree(
                        self.home,
                        cwd,
                        project_id,
                        row["task_id"],
                        self.get_project_settings(project_id)["worktree_dir"],
                    )
                elif row["role"] == "REVIEW":
                    candidate = self.task_worktree(project_id, row["task_id"])
                    if candidate.is_dir():
                        cwd = candidate
                self.store.execute(
                    "UPDATE agent_executions SET status='RUNNING',worktree=? WHERE id=? AND status='QUEUED'",
                    (
                        str(cwd) if cwd != Path(self.project(project_id)["root"]) else None,
                        execution_id,
                    ),
                )
                package = json.loads(row["package"])
                return {
                    "execution_id": execution_id,
                    "task_id": row["task_id"],
                    "status": "AWAITING_CHATGPT",
                    "context_package": package,
                    "workspace": str(cwd),
                    "instruction": row["instruction"],
                    "next_action": "Current ChatGPT conversation performs the role using read_workspace/list_workspace; "
                    "implementation roles may use write_task_file. Finish with submit_agent_report. "
                    "Do not launch another agent. Usage and model are not independently observable for ChatGPT web.",
                }
        except BridgeError as exc:
            self.finish_execution(execution_id, error=str(exc), state="FAILED")
            raise

    def _read_root(self, project_id, root_id, execution_id=None):
        project = self.project(project_id)
        if root_id == "project":
            return Path(project["root"])
        if root_id == "worktree":
            if not execution_id:
                raise BridgeError("Worktree reads require execution_id")
            row = self.store.one(
                "SELECT task_id,worktree FROM agent_executions WHERE project_id=? AND id=?",
                (project_id, execution_id),
            )
            if not row:
                raise PermissionDenied("Execution does not belong to this project")
            if row["worktree"]:
                expected = self.task_worktree(project_id, row["task_id"])
                if Path(row["worktree"]) != expected:
                    raise PermissionDenied("Unexpected worktree path")
                return expected
            return Path(project["root"])
        root = self.get_project_settings(project_id)["read_roots"].get(root_id)
        if not root:
            raise PermissionDenied("Read root is not configured by the local operator")
        return directory(root)

    def list_workspace(
        self, project_id, path="", root_id="project", execution_id=None, offset=0, limit=100
    ):
        if offset < 0 or not 1 <= limit <= 200:
            raise BridgeError("Invalid pagination")
        root = self._read_root(project_id, root_id, execution_id)
        folder = allowed_path(root, path) if path else directory(root)
        if not folder.is_dir():
            raise BridgeError("Not a directory")
        entries = []
        for item in sorted(folder.iterdir(), key=lambda p: p.name.lower()):
            relative = item.relative_to(root).as_posix()
            try:
                target = allowed_path(root, relative)
            except BridgeError:
                continue
            entries.append({"path": relative, "directory": target.is_dir()})
        return {
            "entries": entries[offset : offset + limit],
            "has_more": offset + limit < len(entries),
        }

    def read_workspace(
        self, project_id, path, root_id="project", execution_id=None, start_line=1, max_lines=200
    ):
        if start_line < 1 or not 1 <= max_lines <= 600:
            raise BridgeError("Invalid line range")
        target = allowed_path(self._read_root(project_id, root_id, execution_id), path)
        if not target.is_file() or target.stat().st_size > 2_000_000:
            raise BridgeError("File missing or exceeds 2 MB read limit")
        data = target.read_bytes()
        try:
            body = data.decode("utf-8")
        except UnicodeError:
            raise BridgeError("Only UTF-8 text files are supported") from None
        lines = body.splitlines()
        text = "\n".join(lines[start_line - 1 : start_line - 1 + max_lines])
        content = redact(text)
        return {
            "path": path,
            "sha256": digest(data),
            "start_line": start_line,
            "total_lines": len(lines),
            "content": content,
            "redacted": content != text,
        }

    def write_task_file(self, project_id, execution_id, path, content, expected_sha256=None):
        if len(content.encode()) > 200_000:
            raise BridgeError("File write exceeds 200 KB")
        reject_secrets(content)
        with self.lock(project_id):
            row = self._interactive_execution(project_id, execution_id)
            if row["role"] in {"SA", "REVIEW"}:
                raise PermissionDenied("SA/Review is read-only regardless of provider")
            root = self.task_worktree(project_id, row["task_id"])
            if str(root) != row["worktree"]:
                raise PermissionDenied("Unexpected worktree")
            target = allowed_path(root, path)
            actual = digest(target.read_bytes()) if target.is_file() else None
            if actual != expected_sha256:
                raise VersionConflict(
                    "File changed or already exists; read its SHA256 before writing"
                )
            atomic_write(target, content)
            receipt = {"path": path, "before_sha256": actual, "sha256": digest(content.encode())}
            with self.store.transaction() as conn:
                self.save_artifact(
                    conn,
                    project_id,
                    row["task_id"],
                    "FILE-WRITE.json",
                    "WRITE-" + uuid.uuid4().hex[:16],
                    canonical(receipt) + "\n",
                )
            self.sync_documents(project_id)
            return receipt

    def submit_agent_report(self, project_id, execution_id, report):
        parsed = AgentReport.model_validate(report).model_dump()
        if parsed["simulated"]:
            raise BridgeError("Interactive ChatGPT results cannot claim simulated execution")
        reject_secrets(parsed)
        with self.lock(project_id):
            row = self._interactive_execution(project_id, execution_id)
            extras = {
                "invocation": {
                    "source": "chatgpt_web",
                    "cli_invoked": False,
                    "usage": {
                        "available": False,
                        "reason": "ChatGPT web usage is not exposed to MCP",
                    },
                },
                "test_evidence": {
                    "executed_by_host": False,
                    "reason": "Local tests require separate authorization",
                },
            }
            state = None
            if row["role"] not in {"SA", "REVIEW"}:
                evidence, state = implementation_evidence(self, row, Path(row["worktree"]))
                extras.update(evidence)
            self.finish_execution(
                execution_id, report=parsed, state=state, extras=extras, locked=True
            )
        return {
            "execution_id": execution_id,
            "status": "RECORDED",
            "next_action": "PM collects context updates and refines ContextPatch before the next handoff",
        }
