import json
from pathlib import Path

from ..adapters.agents import ADAPTERS, CodexAdapter
from ..adapters.agents.base import native_executable, operator_policy
from ..adapters.git import git
from ..infra.config import cli_language, metadata_name
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

PROFILE_NAMES = ("chatgpt-only", "best-model")

# Meeting Mode's bounded SD/RD<->SA clarification loop: a project-wide hard ceiling on
# how many rounds one task's single pre-implementation gate can ever use, independent
# of what an operator configures as max_rounds.
MEETING_MODE_HARD_CAP = 5
# Who decides when SA escalates a clarification round with pm_decision_required:
# "ask_user" (default) means ChatGPT must put the question and SA's recommendation in
# front of the real end user and wait for their actual reply before resolving it;
# "auto_pm" lets ChatGPT decide immediately using its own judgement as PM. This is
# advisory (see server.py's instructions) — ccc-mcp has no way to verify from the
# backend which one actually happened.
MEETING_MODE_ESCALATIONS = {"ask_user", "auto_pm"}
DEFAULT_MEETING_MODE = {
    "enabled": False,
    "min_rounds": 0,
    "max_rounds": MEETING_MODE_HARD_CAP,
    "escalation": "ask_user",
}


def _detect_available_native_providers():
    """Which native CLIs (codex/claude) are actually installed on this machine right
    now — a lightweight executable lookup (no subprocess spawn, no login check), so
    `config profile best-model` reflects what can actually run here, never a fixed
    assumption that both are installed. Someone with only Claude Code, only Codex, or
    neither, all get a sensible result instead of a combination that half-fails at
    dispatch time with a confusing "CLI not installed" error.
    """
    available = set()
    for provider in ADAPTERS:
        try:
            native_executable(provider)
            available.add(provider)
        except BridgeError:
            pass
    return available


def best_model_roles(available=None):
    """The role/provider combination `config profile best-model` currently means:
    Codex for SA/REVIEW and Claude for SD/RD when both are installed (the original
    opinionated split); every role on whichever single native CLI is installed when
    only one is; `None` when neither is, since there is then no native "best model"
    to apply at all.
    """
    if available is None:
        available = _detect_available_native_providers()
    codex, claude = "codex" in available, "claude" in available
    if codex and claude:
        return {role: "codex" if role in {"SA", "REVIEW"} else "claude" for role in ROLES}
    if codex:
        return dict.fromkeys(ROLES, "codex")
    if claude:
        return dict.fromkeys(ROLES, "claude")
    return None


def profile_roles(profile, available=None):
    """Resolve a profile name to the role->provider mapping it currently means.
    `chatgpt-only` is fixed; `best-model` is dynamic (see best_model_roles) and may
    resolve to `None` when no native CLI is installed at all."""
    if profile == "chatgpt-only":
        return dict.fromkeys(ROLES, "chatgpt")
    if profile == "best-model":
        return best_model_roles(available)
    raise BridgeError(f"Unsupported profile; choose one of {PROFILE_NAMES}")


def detect_profile(roles):
    """Best-effort label for config show/providers; always derived from every role's
    current provider against what each profile currently means on this machine, so it
    can never drift from what config role/config profile actually left in place —
    and `best-model` only matches when it's the combination this machine could
    actually run right now, not a fixed assumption some other machine might satisfy."""
    if all(roles[role]["provider"] == "chatgpt" for role in ROLES):
        return "chatgpt-only"
    best_model = best_model_roles()
    if best_model is not None and all(
        roles[role]["provider"] == best_model[role] for role in ROLES
    ):
        return "best-model"
    return "custom"


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
    def _validated_meeting_mode(value):
        """{"enabled": bool, "min_rounds": int, "max_rounds": int, "escalation": str}.
        min_rounds is enforced through SD/RD's prompt (see docs/en/4-workflow.md): until
        that many rounds are done SD/RD is told it MUST ask. If it still declines, the
        workflow proceeds and the gate reports min_rounds_unmet; only the 0 <= min <=
        max <= 5 shape is validated here. 5 is the hard system cap, never configurable past that.
        escalation picks who decides a pm_decision_required round: "ask_user" (default)
        or "auto_pm"; see MEETING_MODE_ESCALATIONS."""
        if not isinstance(value, dict):
            raise BridgeError("meeting_mode must be an object")
        enabled = value.get("enabled", False)
        min_rounds = value.get("min_rounds", 0)
        max_rounds = value.get("max_rounds", MEETING_MODE_HARD_CAP)
        escalation = value.get("escalation", "ask_user")
        if not isinstance(enabled, bool):
            raise BridgeError("meeting_mode.enabled must be a boolean")
        for name, number in (("min_rounds", min_rounds), ("max_rounds", max_rounds)):
            if not isinstance(number, int) or isinstance(number, bool):
                raise BridgeError(f"meeting_mode.{name} must be an integer")
        if not 0 <= min_rounds <= max_rounds <= MEETING_MODE_HARD_CAP:
            raise BridgeError(
                f"meeting_mode requires 0 <= min_rounds <= max_rounds <= {MEETING_MODE_HARD_CAP}"
            )
        if escalation not in MEETING_MODE_ESCALATIONS:
            raise BridgeError(
                f"meeting_mode.escalation must be one of {sorted(MEETING_MODE_ESCALATIONS)}"
            )
        return {
            "enabled": enabled,
            "min_rounds": min_rounds,
            "max_rounds": max_rounds,
            "escalation": escalation,
        }

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
        if "meeting_mode" in document:
            updated["meeting_mode"] = self._validated_meeting_mode(document["meeting_mode"])
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
            # Merge (not replace) so a project saved before `escalation` existed still
            # reads a complete dict instead of KeyError-ing downstream.
            "meeting_mode": {**DEFAULT_MEETING_MODE, **settings.get("meeting_mode", {})},
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

    def configure_profile(self, project_id, profile):
        """Apply a bundled provider combination to every role (SA, SD, RD, SD/RD,
        REVIEW) in one call.

        Each role is re-validated through `_validated_role` with no model/effort/
        quota_fallback, so switching profiles always clears any provider-specific
        leftovers from before — never a partial overwrite that keeps e.g. a Claude
        `effort` sitting unused under a now-ChatGPT role. Standalone SD/RD are
        included even though advance_project never auto-dispatches them (it only ever
        uses the combined SD/RD role) — config show must show a clean, fully-applied
        profile, not a provider mix that only matters for manual dispatch_agent calls.

        `best-model` is resolved dynamically (see best_model_roles) against whatever
        native CLIs are actually installed on this machine, not a fixed assumption
        that both Codex and Claude Code are present.
        """
        mapping = profile_roles(profile)
        if mapping is None:
            raise BridgeError(
                "Neither the Codex nor the Claude Code CLI is installed on this "
                "machine; best-model has no native provider to assign. Install one of "
                "them, or run `config profile chatgpt-only` instead."
            )
        with self.lock(project_id):
            settings = self.get_project_settings(project_id)
            for role, provider in mapping.items():
                settings["roles"][role] = self._validated_role(role, provider)
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
            "profile": detect_profile(settings["roles"]),
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
            "profile": detect_profile(settings["roles"]),
            "editable": {
                "roles": settings["roles"],
                "language": settings["language"],
                "test_command": settings["test_command"],
                "context_token_budget": settings["context_token_budget"],
                "meeting_mode": settings["meeting_mode"],
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
                "profile 是從目前所有角色（SA/SD/RD/SD-RD/REVIEW）的 provider 推算出來的唯讀標籤，"
                "從不單獨保存；用 `config profile chatgpt-only` 或 `config profile best-model` "
                "一次套用整組角色（包含不會被自動派工用到的 SD、RD），混合設定時顯示 \"custom\"。"
                "best-model 會依本機實際安裝的 Codex／Claude Code CLI 動態決定：兩個都裝了維持原本 "
                "SA/REVIEW=codex、SD/RD=claude 的組合；只裝一個就全部角色都用那一個；一個都沒裝則 "
                "config profile best-model 會直接拒絕，請改裝其中一個或使用 chatgpt-only。",
            ],
        }

    def role_prompt_preview(self, project_id, role):
        """Exactly what a role would currently be sent, without dispatching anything.

        Composed from the same functions the real dispatch path uses (operator_policy,
        CodexAdapter.EXTRA_INSTRUCTION, context_policy), so this can never drift from
        what actually gets sent — there is no second copy of the text to keep in sync.

        `adapter_operator_policy`/`context_handoff_policy` are the real payload text, so
        they follow the *project's* `language` setting (what Codex/Claude/ChatGPT actually
        receive) — CCC_MCP_LANG never changes them, by design, otherwise this preview
        would stop matching what a real dispatch sends. `tool_access` is this command's
        own description, not sent to any agent, so it does follow CCC_MCP_LANG.
        """
        if role not in ROLES:
            raise BridgeError("Unsupported role")
        settings = self.get_project_settings(project_id)
        language = settings["language"]
        provider = settings["roles"][role]["provider"]
        readonly = role in {"SA", "REVIEW"}

        def ui(zh, en):
            return en if cli_language() == "en" else zh

        if provider == "claude":
            tool_access = (
                ui("Read/Glob/Grep（唯讀）", "Read/Glob/Grep (read-only)")
                if readonly
                else ui("Read/Edit/Write/Glob/Grep（可寫）", "Read/Edit/Write/Glob/Grep (read-write)")
            )
        elif provider == "codex":
            tool_access = (
                ui("codex --sandbox read-only（唯讀）", "codex --sandbox read-only (read-only)")
                if readonly
                else ui(
                    "codex --sandbox workspace-write（可寫，侷限在任務 worktree）",
                    "codex --sandbox workspace-write (read-write, confined to the task worktree)",
                )
            )
        else:
            tool_access = ui(
                "互動式 ChatGPT：沒有 shell，透過 read_workspace／write_workspace 等 MCP 工具操作",
                "Interactive ChatGPT: no shell, operates via MCP tools like read_workspace/write_workspace",
            )
        return {
            "role": role,
            "provider": provider,
            "language": language,
            "tool_access": tool_access,
            "note": ui(
                f"adapter_operator_policy／context_handoff_policy 是實際送給 Agent 的內容，"
                f"語言跟隨這個專案的 language 設定（目前是 {language}），不受 CCC_MCP_LANG 影響；"
                "要改這兩段文字的語言，請用 `ccc-mcp config language`。",
                f"adapter_operator_policy/context_handoff_policy are the real payload sent "
                f"to the Agent, so they follow this project's `language` setting (currently "
                f"{language}), not CCC_MCP_LANG; change it with `ccc-mcp config language` instead.",
            ),
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

    def configure_meeting_mode(
        self, project_id, enabled, min_rounds=None, max_rounds=None, escalation=None
    ):
        """Default off, preserving today's behavior unless explicitly enabled. Omitted
        min_rounds/max_rounds/escalation keep whatever is already configured (or the
        defaults) — only enabled is required so `config meeting-mode off` never has to
        also repeat the round bounds or escalation policy."""
        with self.lock(project_id):
            settings = self.get_project_settings(project_id)
            current = settings.get("meeting_mode", DEFAULT_MEETING_MODE)
            updated = {
                "enabled": enabled,
                "min_rounds": current["min_rounds"] if min_rounds is None else min_rounds,
                "max_rounds": current["max_rounds"] if max_rounds is None else max_rounds,
                "escalation": (
                    current.get("escalation", "ask_user") if escalation is None else escalation
                ),
            }
            settings["meeting_mode"] = self._validated_meeting_mode(updated)
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

    def create_project(self, project_id, path=None, language="zh-TW", profile=None):
        if language not in LANGUAGES:
            raise BridgeError("Language must be zh-TW, ch or en")
        if profile is not None and profile_roles(profile) is None:
            raise BridgeError(
                "Neither the Codex nor the Claude Code CLI is installed on this "
                "machine; best-model has no native provider to assign. Install one of "
                "them, or pass --profile chatgpt-only instead."
            )
        identifier(project_id)
        if self.store.one(
            "SELECT 1 FROM project_deletions WHERE project_id=?", (project_id,)
        ):
            raise BridgeError(
                f"'{project_id}' was permanently deregistered with `project delete`; "
                "that ID cannot be reused. Choose a different project ID."
            )
        if self.store.one("SELECT id FROM projects WHERE id=?", (project_id,)):
            raise BridgeError(
                "Project ID is already registered; use it directly instead of creating it "
                "again (e.g. `ccc-mcp --project "
                f"{project_id} config show`) — if its root directory was deleted outside "
                "ccc-mcp, the next command against it recreates an empty one automatically. "
                "Use a different ID for a genuinely new project."
            )
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
        registered = self.register_project(root, project_id, language=language)
        if profile is not None:
            self.configure_profile(project_id, profile)
            registered = {**registered, "profile": profile}
        return registered
