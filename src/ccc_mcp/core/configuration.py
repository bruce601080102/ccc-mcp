import json
from pathlib import Path

from ..adapters.agents import ADAPTERS
from ..adapters.git import git
from ..infra.config import metadata_name
from ..infra.errors import BridgeError
from ..infra.security import atomic_write, canonical, reject_secrets, safe_path
from .context import identifier
from .models import PROTOCOL_VERSION

# "chatgpt" is interactive, not CLI-dispatched, so it is not in the ADAPTERS registry
# but is still a configurable provider.
PROVIDERS = set(ADAPTERS) | {"chatgpt"}
LANGUAGES = {"zh-TW", "ch", "en"}
ROLES = ("SA", "SD", "RD", "SD/RD", "REVIEW")
EFFORTS = {
    "codex": {"minimal", "low", "medium", "high", "xhigh", "max", "ultra"},
    "claude": {"low", "medium", "high", "xhigh", "max", "ultracode"},
    "chatgpt": set(),
}


def directory(path, create=False):
    candidate = Path(path).expanduser().absolute()
    if candidate.resolve() != candidate:
        raise BridgeError("Directory must not contain symlink/junction aliases")
    if create:
        candidate.mkdir(parents=True, exist_ok=True)
    if not candidate.is_dir():
        raise BridgeError("Directory does not exist")
    return candidate


class ConfigurationHub:
    def resolve_agent_settings(self, project_id, role, agent="auto", model=None, effort=None):
        if role not in ROLES:
            raise BridgeError("Unsupported role")
        configured = self.get_project_settings(project_id)["roles"][role]
        provider = configured["provider"] if agent == "auto" else agent
        if provider not in PROVIDERS:
            raise BridgeError("Unsupported agent")
        resolved = (
            dict(configured)
            if provider == configured["provider"]
            else {
                "provider": provider,
                "model": None,
                "effort": None,
                "quota_fallback": configured["quota_fallback"],
            }
        )
        if model is not None:
            resolved["model"] = model
        if effort is not None:
            resolved["effort"] = effort
        if resolved["effort"] and resolved["effort"] not in EFFORTS[provider]:
            raise BridgeError("Unsupported effort for this provider")
        if resolved["model"] and (
            provider == "chatgpt"
            or resolved["model"].startswith("-")
            or len(resolved["model"]) > 160
            or any(c.isspace() for c in resolved["model"])
        ):
            raise BridgeError("Invalid model; choose ChatGPT web models in the conversation UI")
        reject_secrets(resolved)
        return resolved

    def get_project_settings(self, project_id):
        self.project(project_id)
        row = self.store.one(
            "SELECT content FROM project_settings WHERE project_id=?", (project_id,)
        )
        settings = json.loads(row["content"]) if row else {}
        defaults = {
            role: {
                "provider": "codex" if role in {"SA", "REVIEW"} else "claude",
                "model": None,
                "effort": None,
                "quota_fallback": None,
            }
            for role in ROLES
        }
        project_access = self.get_project_access(project_id)
        continuously_authorized = project_access["mode"] == "read-write"
        workflow_mode = "agent-required" if continuously_authorized else settings.get(
            "workflow_mode", "direct"
        )
        if continuously_authorized:
            project_access = {
                **project_access,
                "write_tool": "write_task_file",
                "requires_task": True,
                "task_visibility": "internal-workstream",
                "requires_plan_approval": False,
                "requires_final_acceptance": False,
                "requires_context_refinement": True,
                "direct_write_allowed": False,
            }
        return {
            "roles": {**defaults, **settings.get("roles", {})},
            "project_access": project_access,
            "read_roots": settings.get("read_roots", {}),
            "writable_files": settings.get("writable_files", []),
            "writable_roots": settings.get("writable_roots", []),
            "workflow_mode": workflow_mode,
            "language": settings.get("language", "zh-TW"),
            "workspace_mode": "project" if continuously_authorized else settings.get(
                "workspace_mode", "worktree"
            ),
            "worktree_dir": settings.get("worktree_dir", str(self.home / "worktrees")),
        }

    def _save_settings(self, project_id, settings):
        if self.get_project_access(project_id)["mode"] == "read-write":
            settings = {
                k: v for k, v in settings.items()
                if k not in {"workflow_mode", "workspace_mode"}
            }
        reject_secrets(settings)
        self.store.execute(
            "INSERT INTO project_settings VALUES(?,?) ON CONFLICT(project_id) DO UPDATE SET content=excluded.content",
            (project_id, canonical(settings)),
        )
        atomic_write(
            safe_path(
                Path(self.project(project_id)["root"]),
                metadata_name(self.project(project_id)["root"]) + "/settings.json",
            ),
            json.dumps(settings, ensure_ascii=False, indent=2) + "\n",
        )
        return settings

    def configure_role(
        self, project_id, role, provider, model=None, effort=None, quota_fallback=None
    ):
        if role not in ROLES or provider not in PROVIDERS:
            raise BridgeError("Unsupported role or provider")
        if effort and effort not in EFFORTS[provider]:
            raise BridgeError(
                "Effort not supported by this provider; model-specific support is checked by its CLI"
            )
        if provider == "chatgpt" and model:
            raise BridgeError("Choose the ChatGPT web model in the conversation UI, not in ccc-mcp")
        if model and (len(model) > 160 or model.startswith("-") or any(c.isspace() for c in model)):
            raise BridgeError("Invalid model identifier")
        if quota_fallback not in {None, *PROVIDERS}:
            raise BridgeError("Unsupported quota fallback")
        with self.lock(project_id):
            settings = self.get_project_settings(project_id)
            settings["roles"][role] = {
                "provider": provider,
                "model": model,
                "effort": effort,
                "quota_fallback": quota_fallback,
            }
            return self._save_settings(project_id, settings)

    def configure_read_root(self, project_id, name, path):
        identifier(name)
        if name in {"project", "worktree"}:
            raise BridgeError("Reserved root name")
        root = directory(path)
        with self.lock(project_id):
            settings = self.get_project_settings(project_id)
            settings["read_roots"][name] = str(root)
            return self._save_settings(project_id, settings)

    def revoke_read_root(self, project_id, name):
        with self.lock(project_id):
            settings = self.get_project_settings(project_id)
            settings["read_roots"].pop(name, None)
            return self._save_settings(project_id, settings)

    def configure_worktree_dir(self, project_id, path):
        root = directory(path, create=True)
        with self.lock(project_id):
            settings = self.get_project_settings(project_id)
            if str(root) != settings["worktree_dir"] and self.store.one(
                "SELECT id FROM agent_executions WHERE project_id=? AND worktree IS NOT NULL LIMIT 1",
                (project_id,),
            ):
                raise BridgeError(
                    "Existing task worktrees cannot be moved; configure the location before implementation"
                )
            settings["worktree_dir"] = str(root)
            return self._save_settings(project_id, settings)

    def task_worktree(self, project_id, task_id):
        root = Path(self.get_project_settings(project_id)["worktree_dir"])
        root.mkdir(parents=True, exist_ok=True)
        directory(root)
        return safe_path(root, f"{identifier(project_id)}/{identifier(task_id)}")

    def configure_workspace_mode(self, project_id, mode):
        if mode not in {"worktree", "project"}:
            raise BridgeError("Workspace mode must be worktree or project")
        with self.lock(project_id):
            settings = self.get_project_settings(project_id)
            if settings["workspace_mode"] != mode:
                if self.store.one(
                    "SELECT id FROM agent_executions WHERE project_id=? "
                    "AND role NOT IN ('SA','REVIEW') AND status IN ('QUEUED','RUNNING')",
                    (project_id,),
                ):
                    raise BridgeError(
                        "Wait for active implementation before changing workspace mode"
                    )
                settings["workspace_mode"] = mode
                self.store.execute(
                    "UPDATE approvals SET status='INVALIDATED' WHERE project_id=? "
                    "AND status IN ('APPROVED','PENDING')",
                    (project_id,),
                )
                self._save_settings(project_id, settings)
            return settings

    def configure_workflow_mode(self, project_id, mode):
        raise BridgeError("Workflow mode selection was removed; project authorization enables the Agent workflow")

    def public_project_settings(self, project_id):
        settings = self.get_project_settings(project_id)
        return {
            "protocol_version": PROTOCOL_VERSION,
            **{
                key: value for key, value in settings.items()
                if key not in {"workflow_mode", "workspace_mode", "worktree_dir", "writable_files", "writable_roots"}
            },
        }

    def configure_language(self, project_id, language):
        if language not in LANGUAGES:
            raise BridgeError("Language must be zh-TW, ch or en")
        with self.lock(project_id):
            settings = self.get_project_settings(project_id)
            settings["language"] = language
            saved = self._save_settings(project_id, settings)
            self.sync_documents(project_id)
            return saved

    def execution_workspace(self, row):
        options = row.get("options") or {}
        if isinstance(options, str):
            options = json.loads(options)
        if options.get("workspace_mode", "worktree") == "project":
            return Path(self.project(row["project_id"])["root"])
        return self.task_worktree(row["project_id"], row["task_id"])

    def projects_directory(self):
        row = self.store.one("SELECT value FROM operator_settings WHERE key='projects_dir'")
        return Path(row["value"]) if row else self.home.parent / "ccc-mcpProjects"

    def set_projects_directory(self, path):
        root = directory(path, create=True)
        self.store.execute(
            "INSERT INTO operator_settings VALUES('projects_dir',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(root),),
        )
        return {"projects_dir": str(root)}

    def create_project(self, project_id, path=None, language="zh-TW"):
        if language not in LANGUAGES:
            raise BridgeError("Language must be zh-TW, ch or en")
        identifier(project_id)
        if self.store.one("SELECT id FROM projects WHERE id=?", (project_id,)):
            raise BridgeError("Project ID is already registered")
        target = (
            Path(path).expanduser().absolute() if path else self.projects_directory() / project_id
        )
        if target.exists():
            raise BridgeError(
                "Project create requires a new directory; use project add for existing files"
            )
        if target.resolve() != target:
            raise BridgeError("Project directory must not contain symlink/junction aliases")
        try:
            target.mkdir(parents=True, exist_ok=False)
        except FileExistsError:
            raise BridgeError("Project directory already exists") from None
        root = directory(target)
        description = {
            "zh-TW": "由 ccc-mcp 管理的專案。",
            "ch": "由 ccc-mcp 管理的项目。",
            "en": "Project managed by ccc-mcp.",
        }[language]
        atomic_write(root / "README.md", f"# {project_id}\n\n{description}\n")
        atomic_write(root / ".gitignore", ".ccc-mcp/\n.env\n.venv/\n__pycache__/\n.pytest_cache/\n")
        git(root, "init")
        git(root, "add", "--", "README.md", ".gitignore")
        git(
            root,
            "-c",
            "user.name=ccc-mcp",
            "-c",
            "user.email=ccc-mcp@localhost",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-m",
            "Initialize project baseline",
        )
        return self.register_project(root, project_id, language=language)
