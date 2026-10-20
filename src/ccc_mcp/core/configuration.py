import json
from pathlib import Path

from ..adapters.agents import ADAPTERS, CodexAdapter
from ..adapters.agents.base import operator_policy
from ..adapters.git import git
from ..infra.config import metadata_name
from ..infra.errors import BridgeError
from ..infra.security import atomic_write, canonical, reject_secrets, safe_path
from .context import context_policy, identifier
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
    @staticmethod
    def _validated_role(role, provider, model=None, effort=None, quota_fallback=None):
        if role not in ROLES or not isinstance(provider, str) or provider not in PROVIDERS:
            raise BridgeError("Unsupported role or provider")
        if model is not None and (
            not isinstance(model, str)
            or not model
            or len(model) > 160
            or model.startswith("-")
            or any(char.isspace() for char in model)
        ):
            raise BridgeError("Invalid model identifier")
        if effort is not None and (
            not isinstance(effort, str) or effort not in EFFORTS.get(provider, set())
        ):
            raise BridgeError("Effort not supported by this provider")
        if provider == "chatgpt" and model:
            raise BridgeError("Choose the ChatGPT web model in the conversation UI, not in ccc-mcp")
        if quota_fallback is not None and (
            not isinstance(quota_fallback, str) or quota_fallback not in PROVIDERS
        ):
            raise BridgeError("Unsupported quota fallback")
        result = {
            "provider": provider,
            "model": model,
            "effort": effort,
            "quota_fallback": quota_fallback,
        }
        reject_secrets(result)
        return result

    @staticmethod
    def _validated_context_token_budget(budget):
        if budget is not None and (
            not isinstance(budget, int) or isinstance(budget, bool) or budget < 1000
        ):
            raise BridgeError("context_token_budget must be an integer >= 1000, or null to disable")
        return budget

    @staticmethod
    def _validated_test_command(command):
        """A fixed argv (never a shell string) run via run_process's shell=False."""
        if command is None:
            return None
        if (
            not isinstance(command, list)
            or not command
            or len(command) > 32
            or not all(isinstance(part, str) and part and len(part) <= 4096 for part in command)
        ):
            raise BridgeError("test_command must be a non-empty list of argv strings")
        reject_secrets(command)
        return command

    def _editable_file_settings(self, settings, defaults, root):
        """Import operator-editable settings; never trust file-based authorization."""
        path = safe_path(root, metadata_name(root) + "/settings.json")
        if not path.exists():
            return settings
        try:
            if path.stat().st_size > 1_000_000:
                raise BridgeError("settings.json is too large")
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise BridgeError("Invalid settings.json; fix the file before continuing") from exc
        if not isinstance(document, dict):
            raise BridgeError("settings.json must contain a JSON object")
        updated = dict(settings)
        if "roles" in document:
            roles = document["roles"]
            if not isinstance(roles, dict):
                raise BridgeError("settings.json roles must be an object")
            updated_roles = dict(settings.get("roles", {}))
            for role, values in roles.items():
                if role not in ROLES or not isinstance(values, dict):
                    raise BridgeError("settings.json contains an invalid role")
                base = {**defaults[role], **updated_roles.get(role, {})}
                configured = {**base, **values}
                updated_roles[role] = self._validated_role(
                    role,
                    configured.get("provider"),
                    configured.get("model"),
                    configured.get("effort"),
                    configured.get("quota_fallback"),
                )
            updated["roles"] = updated_roles
        if "language" in document:
            language = document["language"]
            if not isinstance(language, str) or language not in LANGUAGES:
                raise BridgeError("Language must be zh-TW, ch or en")
            updated["language"] = language
        if "test_command" in document:
            updated["test_command"] = self._validated_test_command(document["test_command"])
        if "context_token_budget" in document:
            updated["context_token_budget"] = self._validated_context_token_budget(
                document["context_token_budget"]
            )
        return updated

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
        if resolved["effort"] and resolved["effort"] not in EFFORTS.get(provider, set()):
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

    def get_project_settings(self, project_id, *, import_file=True):
        project = self.project(project_id)
        defaults = {
            role: {
                "provider": "codex" if role in {"SA", "REVIEW"} else "claude",
                "model": None,
                "effort": None,
                "quota_fallback": None,
            }
            for role in ROLES
        }
        for _ in range(3):
            row = self.store.one(
                "SELECT content FROM project_settings WHERE project_id=?", (project_id,)
            )
            settings = json.loads(row["content"]) if row else {}
            # An outer write transaction already owns SQLite's writer lock.
            # Its caller uses the committed settings; the next unlocked read
            # imports a newly edited file before another dispatch can begin.
            if self.store.in_transaction or not import_file:
                break
            updated = self._editable_file_settings(settings, defaults, Path(project["root"]))
            if updated == settings:
                break
            with self.store.transaction() as conn:
                changed = conn.execute(
                    "UPDATE project_settings SET content=? WHERE project_id=? AND content=?",
                    (canonical(updated), project_id, row["content"]),
                ).rowcount
            if changed:
                settings = updated
                break
        else:
            raise BridgeError("Project settings changed concurrently; retry")
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
            "test_command": settings.get("test_command"),
            "context_token_budget": settings.get("context_token_budget"),
        }

    def _save_settings(self, project_id, settings):
        if self.get_project_access(project_id)["mode"] == "read-write":
            settings = {
                k: v for k, v in settings.items()
                if k not in {"workflow_mode", "workspace_mode"}
            }
        reject_secrets(settings)
        atomic_write(
            safe_path(
                Path(self.project(project_id)["root"]),
                metadata_name(self.project(project_id)["root"]) + "/settings.json",
            ),
            json.dumps(settings, ensure_ascii=False, indent=2) + "\n",
        )
        self.store.execute(
            "INSERT INTO project_settings VALUES(?,?) ON CONFLICT(project_id) DO UPDATE SET content=excluded.content",
            (project_id, canonical(settings)),
        )
        return settings

    def configure_role(
        self, project_id, role, provider, model=None, effort=None, quota_fallback=None
    ):
        updated = self._validated_role(role, provider, model, effort, quota_fallback)
        with self.lock(project_id):
            settings = self.get_project_settings(project_id)
            settings["roles"][role] = updated
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

    def provider_overview(self, project_id):
        """A beginner-friendly view that separates what you can edit (roles/language/
        test_command) from read-only system state (authorization), and lists every
        registered provider — adding a new one (adapters/agents/<x>.py + one line in
        ADAPTERS) shows up here automatically, nothing else to wire up by hand."""
        settings = self.get_project_settings(project_id)
        return {
            "editable": {
                "roles": settings["roles"],
                "language": settings["language"],
                "test_command": settings["test_command"],
                "context_token_budget": settings["context_token_budget"],
            },
            "read_only_status": {
                "project_access": settings["project_access"],
            },
            "available_providers": {
                provider: sorted(EFFORTS.get(provider, set())) for provider in sorted(PROVIDERS)
            },
            "notes": [
                "新增一個 provider：寫一個 adapters/agents/<name>.py 繼承 BaseAgentAdapter，"
                "在 ADAPTERS 註冊一行即可在這裡看到；只有支援 effort 分級時才需要補 EFFORTS。",
                "自動流程（advance_project）只會派合併角色 \"SD/RD\"，從不單獨派 \"SD\" 或 \"RD\"；"
                "單獨設定 roles.SD / roles.RD 不會影響自動派工，只有手動呼叫 "
                "dispatch_agent(role=\"SD\") 這類直接指定角色時才會用到。",
            ],
        }

    def role_prompt_preview(self, project_id, role):
        """Exactly what a role would currently be sent, without dispatching anything.

        Composed from the same functions the real dispatch path uses (operator_policy,
        CodexAdapter.EXTRA_INSTRUCTION, context_policy), so this can never drift from
        what actually gets sent — there is no second copy of the text to keep in sync.
        """
        if role not in ROLES:
            raise BridgeError("Unsupported role")
        settings = self.get_project_settings(project_id)
        language = settings["language"]
        provider = settings["roles"][role]["provider"]
        readonly = role in {"SA", "REVIEW"}
        if provider == "claude":
            tool_access = (
                "Read/Glob/Grep（唯讀）" if readonly else "Read/Edit/Write/Glob/Grep（可寫）"
            )
        elif provider == "codex":
            tool_access = (
                "codex --sandbox read-only（唯讀）"
                if readonly
                else "codex --sandbox workspace-write（可寫，侷限在任務 worktree）"
            )
        else:
            tool_access = "互動式 ChatGPT：沒有 shell，透過 read_workspace／write_workspace 等 MCP 工具操作"
        return {
            "role": role,
            "provider": provider,
            "language": language,
            "tool_access": tool_access,
            "adapter_operator_policy": operator_policy(language),
            "provider_extra_instruction": CodexAdapter.EXTRA_INSTRUCTION
            if provider == "codex"
            else None,
            "context_handoff_policy": context_policy(language),
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

    def configure_test_command(self, project_id, command):
        """The fixed host-run test command; project authorize also consents to running it
        automatically before REVIEW. `command=None` clears it (tasks fall back to unverified)."""
        validated = self._validated_test_command(command)
        with self.lock(project_id):
            settings = self.get_project_settings(project_id)
            settings["test_command"] = validated
            return self._save_settings(project_id, settings)

    def configure_context_token_budget(self, project_id, budget):
        """A per-project default passed into every real dispatch's build_handoff.
        `budget=None` (the default) disables enforcement entirely, preserving today's
        behavior unless you opt in."""
        validated = self._validated_context_token_budget(budget)
        with self.lock(project_id):
            settings = self.get_project_settings(project_id)
            settings["context_token_budget"] = validated
            return self._save_settings(project_id, settings)

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
