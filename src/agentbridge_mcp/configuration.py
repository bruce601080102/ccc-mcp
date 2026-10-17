import json
from pathlib import Path

from .context import identifier
from .errors import BridgeError
from .git import git
from .security import atomic_write, canonical, reject_secrets, safe_path

PROVIDERS = {"codex", "claude", "chatgpt"}
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
        return {
            "roles": {**defaults, **settings.get("roles", {})},
            "read_roots": settings.get("read_roots", {}),
            "worktree_dir": settings.get("worktree_dir", str(self.home / "worktrees")),
        }

    def _save_settings(self, project_id, settings):
        reject_secrets(settings)
        self.store.execute(
            "INSERT INTO project_settings VALUES(?,?) ON CONFLICT(project_id) DO UPDATE SET content=excluded.content",
            (project_id, canonical(settings)),
        )
        atomic_write(
            safe_path(Path(self.project(project_id)["root"]), ".agentbridge/settings.json"),
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
            raise BridgeError(
                "Choose the ChatGPT web model in the conversation UI, not in AgentBridge"
            )
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

    def projects_directory(self):
        row = self.store.one("SELECT value FROM operator_settings WHERE key='projects_dir'")
        return Path(row["value"]) if row else self.home.parent / "AgentBridgeProjects"

    def set_projects_directory(self, path):
        root = directory(path, create=True)
        self.store.execute(
            "INSERT INTO operator_settings VALUES('projects_dir',?) "
            "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            (str(root),),
        )
        return {"projects_dir": str(root)}

    def create_project(self, project_id, path=None):
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
        atomic_write(root / "README.md", f"# {project_id}\n\nProject managed by AgentBridge.\n")
        atomic_write(
            root / ".gitignore", ".agentbridge/\n.env\n.venv/\n__pycache__/\n.pytest_cache/\n"
        )
        git(root, "init")
        git(root, "add", "--", "README.md", ".gitignore")
        git(
            root,
            "-c",
            "user.name=AgentBridge",
            "-c",
            "user.email=agentbridge@localhost",
            "-c",
            "commit.gpgsign=false",
            "commit",
            "-m",
            "Initialize project baseline",
        )
        return self.register_project(root, project_id)
