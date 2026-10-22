import getpass
import importlib.metadata
import json
import os
import re
import shlex
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Annotated

import typer

from ..adapters.agent_setup import setup_provider
from ..adapters.agents.base import native_executable
from ..adapters.skills import install_skill, list_skills, remove_skill
from ..adapters.tunnel_client import ensure_tunnel_client, tunnel_client_path
from ..core.models import AgentReport, ContextPatch, ContextSnapshot, strict_json_schema
from ..infra.config import cli_language
from ..infra.errors import BridgeError
from ..infra.security import atomic_write, redact
from ..service import CCCMCP


def t(zh: str, en: str, ch: str | None = None) -> str:
    """Pick the CLI's own displayed text (help, usage errors) by CCC_MCP_LANG.

    Independent from a project's `language` setting (zh-TW/ch/en), which controls
    what Codex/Claude/ChatGPT receive, not what this terminal shows. Re-reads the env
    var on every call (cheap) instead of caching at import time, so e.g. `CCC_MCP_LANG=en
    ccc-mcp ...` works per-invocation without needing a fresh process/import. `ch`
    (simplified Chinese) is optional per string — strings that haven't been given one
    yet fall back to the zh-TW (Traditional) text, which a simplified-Chinese reader
    can still follow, unlike the original problem (no English at all for en readers).
    """
    lang = cli_language()
    if lang == "en":
        return en
    if lang == "ch":
        return ch or zh
    return zh


app = typer.Typer(
    no_args_is_help=True,
    help=t(
        "ccc-mcp Context Hub 與 Agent 協作工具",
        "ccc-mcp context hub and agent collaboration tool",
        "ccc-mcp Context Hub 与 Agent 协作工具",
    ),
)
project_app = typer.Typer(no_args_is_help=True)
context_app = typer.Typer(no_args_is_help=True)
task_app = typer.Typer(no_args_is_help=True)
workspace_app = typer.Typer(
    no_args_is_help=True,
    help=t(
        "直接編輯原專案：審閱並核准指定檔案差異",
        "Direct edits to the original project: review and approve a specific file's diff",
        "直接编辑原项目：审阅并核准指定文件差异",
    ),
)
config_app = typer.Typer(
    no_args_is_help=True,
    help=t(
        "本機設定角色、模型、位置與讀取範圍",
        "Local settings: roles, models, locations and read scope",
        "本机设置角色、模型、位置与读取范围",
    ),
)
schema_app = typer.Typer(
    no_args_is_help=True,
    help=t(
        "發布給外部消費者（例如未來的 ccc-mcp-ui）的協議 JSON Schema",
        "Protocol JSON Schema published for external consumers (e.g. a future ccc-mcp-ui)",
        "发布给外部消费者（例如未来的 ccc-mcp-ui）的协议 JSON Schema",
    ),
)
skill_app = typer.Typer(
    no_args_is_help=True,
    help=t(
        "本機 Skill：Codex／Claude／ChatGPT 派工時都看得到，全局安裝",
        "Local skills: visible to Codex/Claude/ChatGPT on every dispatch; installed globally",
        "本机 Skill：Codex／Claude／ChatGPT 派工时都看得到，全局安装",
    ),
)
app.add_typer(project_app, name="project")
app.add_typer(context_app, name="context")
app.add_typer(task_app, name="task")
app.add_typer(workspace_app, name="workspace", hidden=True)
app.add_typer(config_app, name="config")
app.add_typer(schema_app, name="schema")
app.add_typer(skill_app, name="skill")


@app.callback(
    help=t(
        "--home 指定資料庫位置；--project 指定已註冊專案。",
        "--home sets the database location; --project selects a registered project.",
        "--home 指定数据库位置；--project 指定已注册项目。",
    )
)
def options(ctx: typer.Context, home: Path | None = None, project: str | None = None):
    bridge = CCCMCP(home)
    ctx.obj = {"bridge": bridge, "project": project}


def bridge(ctx):
    return ctx.obj["bridge"]


def project_id(ctx):
    if ctx.obj["project"]:
        return ctx.obj["project"]
    cwd = Path.cwd().resolve()
    candidates = [p for p in bridge(ctx).list_projects() if cwd.is_relative_to(Path(p["root"]))]
    if not candidates:
        raise BridgeError(
            t(
                "目前目錄未註冊，請先 init 或使用 --project",
                "This directory isn't registered; run init first or use --project",
                "当前目录未注册，请先 init 或使用 --project",
            )
        )
    return max(candidates, key=lambda p: len(p["root"]))["id"]


def output(value):
    typer.echo(json.dumps(value, ensure_ascii=False, indent=2))


@app.command(
    help=t(
        "註冊專案並產生 .ccc-mcp 共用記憶。",
        "Register a project and generate its .ccc-mcp shared memory.",
        "注册项目并生成 .ccc-mcp 共享记忆。",
    )
)
def init(
    ctx: typer.Context,
    path: Annotated[Path, typer.Argument()] = Path("."),
    id: str | None = None,
    purpose: str | None = None,
    language: str = "zh-TW",
):
    output(bridge(ctx).register_project(path, id, purpose, language))


@project_app.command(
    "add",
    help=t(
        "把一個已存在、尚未註冊的資料夾登記為專案（不會建立新目錄）。"
        "例如：ccc-mcp project add ./my-folder --id my-project",
        "Register an existing, not-yet-registered folder as a project (never creates a "
        "new directory). Example: ccc-mcp project add ./my-folder --id my-project",
    ),
)
def project_add(
    ctx: typer.Context,
    path: Annotated[Path, typer.Argument()] = Path("."),
    id: str | None = None,
    purpose: str | None = None,
    language: str = "zh-TW",
):
    output(bridge(ctx).register_project(path, id, purpose, language))


@project_app.command(
    "list",
    help=t(
        "列出所有已註冊專案的 ID 與實際路徑。",
        "List every registered project's ID and actual path.",
    ),
)
def project_list(ctx: typer.Context):
    output(bridge(ctx).list_projects())


@project_app.command(
    "authorize",
    help=t(
        "持續授權指定專案；後端自動承接 Agent 工作串與計畫。",
        "Grant continuous authorization to a project; the backend then auto-drives the Agent workstream and plans.",
    ),
)
def project_authorize(ctx: typer.Context, id: str):
    output(bridge(ctx).authorize_project_local(id))


@project_app.command(
    "revoke",
    help=t("撤銷專案直接寫入授權，恢復唯讀。", "Revoke a project's direct-write authorization, back to read-only."),
)
def project_revoke(ctx: typer.Context, id: str):
    output(bridge(ctx).revoke_project_local(id))


@project_app.command(
    "create",
    help=t(
        "建立新專案與 Git 基準；不搬移或覆寫既有目錄。"
        "可用 --profile 一步套用角色組合，省去後續再執行 config profile。"
        "例如：ccc-mcp project create hello-demo --profile chatgpt-only",
        "Create a new project with a Git baseline; never moves or overwrites an "
        "existing directory. Pass --profile to apply a role combination in the same "
        "step, instead of a separate config profile call afterwards. "
        "Example: ccc-mcp project create hello-demo --profile chatgpt-only",
    ),
)
def project_create(
    ctx: typer.Context,
    id: str,
    path: Path | None = None,
    language: str = "zh-TW",
    profile: Annotated[
        str | None,
        typer.Option(
            help=t(
                "chatgpt-only 或 best-model；省略則維持預設角色設定",
                "chatgpt-only or best-model; omit to keep the default role settings",
            )
        ),
    ] = None,
):
    output(bridge(ctx).create_project(id, path, language, profile))


@config_app.command(
    "show",
    help=t(
        "顯示目前專案設定的生效值（角色/語言/test_command 等）；唯讀，不會派工。",
        "Show the current project's effective settings (roles/language/test_command, "
        "etc.); read-only, never dispatches anything.",
    ),
)
def config_show(ctx: typer.Context):
    output(bridge(ctx).public_project_settings(project_id(ctx)))


@config_app.command(
    "providers",
    help=t(
        "新手友善總覽：可編輯設定（roles/language/test_command）與唯讀授權狀態分開顯示，"
        "並列出目前所有可用的 provider 及支援的 effort。",
        "Beginner-friendly overview: editable settings (roles/language/test_command) shown "
        "separately from read-only authorization status, plus every available provider and "
        "the efforts it supports.",
    ),
)
def config_providers(ctx: typer.Context):
    output(bridge(ctx).provider_overview(project_id(ctx)))


@config_app.command(
    "profile",
    help=t(
        "套用一組角色 provider 組合（SA、SD、RD、SD/RD、REVIEW 全部一起設定）：chatgpt-only "
        "全部設為 ChatGPT（清除任何 Codex／Claude 專屬 model/effort/quota-fallback）；"
        "best-model 依本機實際安裝的 Codex／Claude Code CLI 動態決定（兩個都裝＝原本的 "
        "codex/claude 分工；只裝一個＝全部角色用那一個；一個都沒裝則拒絕）。"
        "例如：ccc-mcp config profile chatgpt-only",
        "Apply a bundled provider combination to every role (SA, SD, RD, SD/RD, REVIEW): "
        "chatgpt-only sets all of them to ChatGPT (clearing any Codex/Claude-specific "
        "model/effort/quota-fallback); best-model is resolved dynamically from whichever "
        "native CLIs are actually installed on this machine (both installed keeps the "
        "original Codex/Claude split; only one installed assigns every role to it; "
        "neither installed is rejected). Example: ccc-mcp config profile chatgpt-only",
    ),
)
def config_profile(
    ctx: typer.Context,
    profile: Annotated[
        str, typer.Argument(help=t("chatgpt-only 或 best-model", "chatgpt-only or best-model"))
    ],
):
    output(bridge(ctx).configure_profile(project_id(ctx), profile))


@config_app.command(
    "prompt",
    help=t(
        "純讀取：依目前設定印出該角色實際會收到的操作政策／提示文字，不會真的派工。",
        "Read-only: print exactly what a role would currently be sent, without dispatching anything.",
    ),
)
def config_prompt(ctx: typer.Context, role: str):
    output(bridge(ctx).role_prompt_preview(project_id(ctx), role))


@config_app.command(
    "context-budget",
    help=t(
        "設定派工時 build_handoff 的 token 預算；超額時會壓縮歷史清單並列出省略了什麼。"
        "未設定時維持現狀，不限制任何交接內容。",
        "Set the token budget build_handoff uses on real dispatches; over budget, history "
        "lists are compressed and what was omitted is listed. Unset (default): no limit at all.",
    ),
)
def config_context_budget(
    ctx: typer.Context,
    budget: Annotated[
        int | None,
        typer.Argument(
            help=t(
                "單位約為字元數/3；留空並加 --clear 可清除設定",
                "Roughly character-count/3; leave empty with --clear to remove the setting",
            )
        ),
    ] = None,
    clear: bool = False,
):
    if clear:
        output(bridge(ctx).configure_context_token_budget(project_id(ctx), None))
        return
    if budget is None:
        raise BridgeError(t("請提供預算數字，或加 --clear 清除現有設定", "Provide a budget number, or pass --clear to remove the current setting"))
    output(bridge(ctx).configure_context_token_budget(project_id(ctx), budget))


@config_app.command(
    "role",
    help=t(
        "指定一個角色使用哪個 provider，並可選擇 model/effort/額度備援；每次呼叫會整組取代"
        "該角色設定，要保留的選項請一起填。"
        "例如：ccc-mcp config role REVIEW codex --effort high --quota-fallback chatgpt",
        "Assign a role to a provider, with optional model/effort/quota fallback; each call "
        "replaces the whole role config, so include every option you want to keep. "
        "Example: ccc-mcp config role REVIEW codex --effort high --quota-fallback chatgpt",
    ),
)
def config_role(
    ctx: typer.Context,
    role: Annotated[
        str, typer.Argument(help=t("SA、SD、RD、SD/RD 或 REVIEW", "SA, SD, RD, SD/RD, or REVIEW"))
    ],
    provider: Annotated[
        str, typer.Argument(help=t("codex、claude 或 chatgpt", "codex, claude, or chatgpt"))
    ],
    model: Annotated[
        str | None,
        typer.Option(
            help=t(
                "該 provider 的模型 ID；留空用其 CLI 自身的預設值",
                "A model ID for the provider; omit to use that CLI's own default",
            )
        ),
    ] = None,
    effort: Annotated[
        str | None,
        typer.Option(
            help=t(
                "該 provider 支援的 effort 等級；用 config providers 查詢清單",
                "An effort level this provider supports; see config providers for the list",
            )
        ),
    ] = None,
    quota_fallback: Annotated[
        str | None,
        typer.Option(
            help=t(
                "主要 provider 額度不足時備援的 provider；只在額度不足時生效",
                "A fallback provider used only when the primary provider is out of quota",
            )
        ),
    ] = None,
):
    output(
        bridge(ctx).configure_role(project_id(ctx), role, provider, model, effort, quota_fallback)
    )


@config_app.command(
    "read-root",
    help=t(
        "新增一個唯讀掛載點：Agent 可讀但不能寫入 path 底下的內容，用自訂 name 當識別碼。"
        "例如：ccc-mcp config read-root shared-lib /Users/you/Code/shared-lib",
        "Add a read-only mount: Agents can read, but never write, everything under path, "
        "identified by name. Example: ccc-mcp config read-root shared-lib /Users/you/Code/shared-lib",
    ),
)
def config_read_root(
    ctx: typer.Context,
    name: Annotated[
        str,
        typer.Argument(
            help=t("識別碼；不可用 project 或 worktree", "An identifier; not project or worktree")
        ),
    ],
    path: Annotated[
        Path, typer.Argument(help=t("要開放讀取的本機目錄", "The local directory to expose read-only"))
    ],
):
    output(bridge(ctx).configure_read_root(project_id(ctx), name, path))


@config_app.command(
    "worktree-dir",
    help=t(
        "設定實作角色（SD/RD）worktree 的存放位置；已有任務 worktree 時不可更改。"
        "例如：ccc-mcp config worktree-dir /Users/you/ccc-mcp-worktrees",
        "Set where implementation-role (SD/RD) worktrees are created; cannot be changed "
        "once a task already has one. Example: ccc-mcp config worktree-dir /Users/you/ccc-mcp-worktrees",
    ),
)
def config_worktree_dir(ctx: typer.Context, path: Path):
    output(bridge(ctx).configure_worktree_dir(project_id(ctx), path))


@config_app.command(
    "test-command",
    help=t(
        "設定 `project authorize` 後、每輪 REVIEW 前自動執行的固定測試命令（本機實際執行，非 Agent）。",
        "Set the fixed test command auto-run on the host (never the Agent) before each "
        "round's REVIEW, once `project authorize` has been granted.",
    ),
)
def config_test_command(
    ctx: typer.Context,
    command: Annotated[
        str | None,
        typer.Argument(
            help=t(
                '例如："pytest -q"；留空並加 --clear 可清除設定',
                'e.g. "pytest -q"; leave empty with --clear to remove the setting',
            )
        ),
    ] = None,
    clear: bool = False,
):
    if clear:
        output(bridge(ctx).configure_test_command(project_id(ctx), None))
        return
    if not command:
        raise BridgeError(t("請提供測試命令字串，或加 --clear 清除現有設定", "Provide a test command string, or pass --clear to remove the current setting"))
    output(bridge(ctx).configure_test_command(project_id(ctx), shlex.split(command)))


@config_app.command(
    "write-file",
    hidden=True,
    help=t(
        "開放一個專案相對檔案的直接編輯提案；每份差異仍須本機核准。",
        "Allow proposing a direct edit to one project-relative file; every diff still needs local approval.",
    ),
)
def config_write_file(ctx: typer.Context, path: str):
    output(bridge(ctx).configure_write_file(project_id(ctx), path))


@config_app.command(
    "revoke-file",
    hidden=True,
    help=t(
        "撤銷一個檔案的直接編輯權；尚未套用的核准也會被擋下。",
        "Revoke direct-edit rights for one file; any not-yet-applied approval is blocked too.",
    ),
)
def config_revoke_file(ctx: typer.Context, path: str):
    output(bridge(ctx).revoke_write_file(project_id(ctx), path))


@config_app.command(
    "write-root",
    hidden=True,
    help=t(
        "一次開放專案相對目錄及子目錄；. 代表整個專案，憑證與管理資料仍禁止。",
        "Allow a project-relative directory and its subdirectories at once; `.` means the "
        "whole project. Credentials and operator metadata remain off-limits.",
    ),
)
def config_write_root(ctx: typer.Context, path: Annotated[str, typer.Argument()] = "."):
    output(bridge(ctx).configure_write_root(project_id(ctx), path))


@config_app.command(
    "revoke-write-root",
    hidden=True,
    help=t(
        "撤銷目錄寫入範圍；獨立的 write-file 規則仍保留。",
        "Revoke a directory write grant; independent write-file rules are kept.",
    ),
)
def config_revoke_write_root(ctx: typer.Context, path: Annotated[str, typer.Argument()] = "."):
    output(bridge(ctx).revoke_write_root(project_id(ctx), path))


@config_app.command(
    "language",
    help=t(
        "設定共用 Context 與 Agent 報告語言：zh-TW（預設）、ch（簡體）或 en。",
        "Set the shared Context and Agent report language: zh-TW (default), ch (simplified Chinese) or en.",
    ),
)
def config_language(ctx: typer.Context, language: str):
    output(bridge(ctx).configure_language(project_id(ctx), language))


@workspace_app.command(
    "show",
    help=t(
        "查看直接編輯的差異與核准紀錄；--include-content 同時顯示前後全文。",
        "View a direct edit's diff and approval record; --include-content also shows the full before/after text.",
    ),
)
def workspace_show(ctx: typer.Context, edit_id: str, include_content: bool = False):
    output(bridge(ctx).get_workspace_edit(project_id(ctx), edit_id, include_content))


@workspace_app.command(
    "approve",
    help=t(
        "舊版本機逐檔審閱工具；一般專案修改請使用 Agent 工作串。",
        "Legacy per-file local review tool; use the Agent workstream for ordinary project changes.",
    ),
)
def workspace_approve(ctx: typer.Context, edit_id: str):
    output(bridge(ctx).approve_workspace_edit_local(project_id(ctx), edit_id))


@config_app.command(
    "revoke-root",
    help=t(
        "移除一個用 config read-root 新增的唯讀掛載點。例如：ccc-mcp config revoke-root shared-lib",
        "Remove a read-only mount added with config read-root. Example: ccc-mcp config revoke-root shared-lib",
    ),
)
def config_revoke_root(ctx: typer.Context, name: str):
    output(bridge(ctx).revoke_read_root(project_id(ctx), name))


@config_app.command(
    "projects-dir",
    help=t(
        "設定全域預設：之後 project create（未帶 --path 時）新建專案的所在目錄；不影響既有專案。"
        "例如：ccc-mcp config projects-dir /Users/you/Code/ccc-mcp-projects",
        "Set the global default directory where project create (without --path) makes "
        "new projects; existing projects are never moved. Example: "
        "ccc-mcp config projects-dir /Users/you/Code/ccc-mcp-projects",
    ),
)
def config_projects_dir(ctx: typer.Context, path: Path):
    output(bridge(ctx).set_projects_directory(path))


@context_app.command(
    "show",
    help=t(
        "讀取角色專屬的 Context 套件（含版本號與來源佐證）；預設角色 PM。"
        "例如：ccc-mcp context show --role REVIEW --task TASK-0001",
        "Read a role-specific Context package (with a pinned version and provenance); "
        "defaults to role PM. Example: ccc-mcp context show --role REVIEW --task TASK-0001",
    ),
)
def context_show(
    ctx: typer.Context, role: str = "PM", task: str | None = None, scope: str | None = None
):
    output(bridge(ctx).get_context(project_id(ctx), role, task, scope))


@context_app.command(
    "collect",
    help=t(
        "彙整尚待 PM 整理進共用 Context 的事件、報告與決策。",
        "Collect events, reports and decisions still awaiting PM compaction into the shared Context.",
    ),
)
def context_collect(ctx: typer.Context):
    output(bridge(ctx).collect_context_updates(project_id(ctx)))


@context_app.command(
    "history",
    help=t(
        "分頁讀取不可變的歷史 Context 版本與差異。例如：ccc-mcp context history --offset 0 --limit 20",
        "Read immutable historical Context versions and diffs, paginated. Example: "
        "ccc-mcp context history --offset 0 --limit 20",
    ),
)
def context_history(ctx: typer.Context, offset: int = 0, limit: int = 20):
    output(bridge(ctx).get_context_history(project_id(ctx), offset, limit))


@context_app.command(
    "export",
    help=t(
        "把目前共用 Context 輸出成 Markdown；不給 --out 就印到終端機。"
        "例如：ccc-mcp context export --out CONTEXT.md",
        "Export the current shared Context as Markdown; without --out it prints to the "
        "terminal. Example: ccc-mcp context export --out CONTEXT.md",
    ),
)
def context_export(ctx: typer.Context, out: Path | None = None):
    text = bridge(ctx).export_context(project_id(ctx))
    if out:
        atomic_write(out, text)
        typer.echo(str(out.resolve()))
    else:
        typer.echo(text)


@context_app.command(
    "validate",
    help=t(
        "檢查來源雜湊、投影漂移與證據完整性；驗證失敗會以非零結束碼退出。",
        "Check source hashes, projection drift and evidence integrity; exits non-zero on failure.",
    ),
)
def context_validate(ctx: typer.Context):
    result = bridge(ctx).validate_context(project_id(ctx))
    output(result)
    if not result["valid"]:
        raise typer.Exit(1)


@context_app.command(
    "recover",
    help=t(
        "從 SQLite 重建 Markdown（保留來源漂移警示）。",
        "Rebuild the Markdown files from SQLite (source-drift warnings are preserved).",
    ),
)
def context_recover(ctx: typer.Context):
    output(bridge(ctx).recover_context(project_id(ctx)))


@context_app.command(
    "propose",
    help=t(
        "從本機 JSON 檔案提交一份 ContextPatch 提案，回傳可審閱的差異（尚未真的套用）。"
        "例如：ccc-mcp context propose patch.json",
        "Submit a ContextPatch proposal from a local JSON file and return a reviewable "
        "diff (not yet applied). Example: ccc-mcp context propose patch.json",
    ),
)
def context_propose(ctx: typer.Context, patch: Path):
    parsed = ContextPatch.model_validate_json(patch.read_text(encoding="utf-8-sig"))
    output(bridge(ctx).propose_context_patch(project_id(ctx), parsed.model_dump()))


@context_app.command(
    "commit",
    help=t(
        "重新驗證版本後，原子性套用一個已提案的 Patch（用 context propose 回傳的 patch_id）。"
        "例如：ccc-mcp context commit PATCH-xxxx",
        "Revalidate the version and atomically apply a proposed Patch (the patch_id from "
        "context propose). Example: ccc-mcp context commit PATCH-xxxx",
    ),
)
def context_commit(ctx: typer.Context, patch_id: str):
    output(bridge(ctx).commit_context_patch(project_id(ctx), patch_id))


@context_app.command(
    "import-patch",
    help=t(
        "沒有 MCP 寫入權限時，以 JSON 匯入並提交 Patch。",
        "Import and commit a Patch from JSON when MCP write access isn't available.",
    ),
)
def context_import(ctx: typer.Context, patch: Path):
    parsed = ContextPatch.model_validate_json(patch.read_text(encoding="utf-8-sig"))
    proposed = bridge(ctx).propose_context_patch(project_id(ctx), parsed.model_dump())
    output(bridge(ctx).commit_context_patch(project_id(ctx), proposed["patch_id"]))


@context_app.command(
    "source",
    help=t(
        "查詢一個專案相對路徑目前的 SHA256，作為佐證引用。例如：ccc-mcp context source src/app.py",
        "Get the current SHA256 of one project-relative path, for use as a provenance "
        "reference. Example: ccc-mcp context source src/app.py",
    ),
)
def context_source(ctx: typer.Context, path: str):
    output(bridge(ctx).source_ref(project_id(ctx), path))


@task_app.command(
    "create",
    help=t(
        "建立/重用工作串的需求與驗收條件（低階操作；一般請在 ChatGPT 直接提需求，它會自動呼叫）。"
        '例如：ccc-mcp task create "新增 hello.py" --acceptance "執行輸出 Hello, World!"',
        "Create/reuse the workstream's requirement and acceptance criteria (a low-level "
        "operation; normally just ask ChatGPT, which calls this automatically). Example: "
        'ccc-mcp task create "Add hello.py" --acceptance "Running it prints Hello, World!"',
    ),
)
def task_create(
    ctx: typer.Context,
    requirement: str,
    acceptance: Annotated[list[str] | None, typer.Option()] = None,
):
    output(bridge(ctx).create_task(project_id(ctx), requirement, acceptance))


@task_app.command(
    "list",
    help=t(
        "列出此專案所有任務的 ID 與狀態。",
        "List every task's ID and state for this project.",
    ),
)
def task_list(ctx: typer.Context):
    output(bridge(ctx).list_tasks(project_id(ctx)))


@task_app.command(
    "status",
    help=t(
        "查看任務目前狀態（進度、是否卡住、等待誰）；省略 task_id 時用目前的工作串任務。"
        "例如：ccc-mcp task status TASK-0001",
        "View a task's current state (progress, whether it's stuck, who it's waiting on); "
        "omitting task_id uses the current workstream task. Example: ccc-mcp task status TASK-0001",
    ),
)
def task_status(ctx: typer.Context, task_id: Annotated[str | None, typer.Argument()] = None):
    service, project = bridge(ctx), project_id(ctx)
    output(service.get_task_status(project, task_id or service.project_workstream_task(project)))


@task_app.command(
    "result",
    help=t(
        "查詢完整執行資料；一般查看成果可用 task show。",
        "Query full execution data; for everyday results use task show instead.",
    ),
)
def task_result(ctx: typer.Context, task_id: Annotated[str | None, typer.Argument()] = None):
    service, project = bridge(ctx), project_id(ctx)
    output(service.get_task_result(project, task_id or service.project_workstream_task(project)))


@task_app.command(
    "show",
    help=t(
        "合併顯示任務狀態、結果摘要、Worktree 與用量；只有一個任務時可省略編號。",
        "Combined view of task state, result summary, worktree and usage; the task ID can "
        "be omitted when there's only one task.",
    ),
)
def task_show(ctx: typer.Context, task_id: Annotated[str | None, typer.Argument()] = None):
    service = bridge(ctx)
    project = project_id(ctx)
    if task_id is None:
        tasks = service.list_tasks(project)
        if service.get_project_access(project)["mode"] == "read-write" and tasks:
            task_id = tasks[-1]["id"]
        elif len(tasks) != 1:
            raise typer.BadParameter(
                t(
                    "請用 task list 查看編號，再執行 task show TASK-xxxx；只有一個任務時才能省略編號。",
                    "Run task list to find the ID, then task show TASK-xxxx; the ID can only be omitted when there's exactly one task.",
                )
            )
        task_id = tasks[0]["id"]
    task = service.task(project, task_id)
    typer.echo(t(f"專案：{project}　任務：{task_id}　狀態：{task['state']}", f"Project: {project}  Task: {task_id}  State: {task['state']}"))
    rows = service.get_task_result(project, task_id)
    if not rows:
        typer.echo(t("尚無 Agent 執行紀錄。", "No Agent execution recorded yet."))
    for row in rows:
        typer.echo(f"\n{row['id']}　{row['agent']} / {row['role']}　{row['status']}")
        typer.echo(t(f"執行模式：{row['adapter']}", f"Adapter: {row['adapter']}"))
        result = row.get("result") or {}
        report = result.get("report") or {}
        if report:
            typer.echo(t(f"摘要：{report['summary']}", f"Summary: {report['summary']}"))
            typer.echo(
                t(
                    f"需要修訂：{report.get('needs_revision', '未知')}",
                    f"Needs revision: {report.get('needs_revision', 'unknown')}",
                )
            )
        if row.get("error"):
            typer.echo(t(f"錯誤：{row['error']}", f"Error: {row['error']}"))
        if row.get("worktree"):
            typer.echo(t(f"程式位置：{row['worktree']}", f"Code location: {row['worktree']}"))
        invocation = result.get("invocation") or {}
        typer.echo(
            t(
                f"CLI 呼叫：{invocation.get('cli_invoked', '未知')}",
                f"CLI invoked: {invocation.get('cli_invoked', 'unknown')}",
            )
        )
        usage = invocation.get("usage") or {}
        models = usage.get("reported_models") or []
        if models:
            typer.echo(t("CLI 回報模型：", "CLI-reported models: ") + ", ".join(models))
        if usage.get("available"):
            typer.echo(t("Token：", "Tokens: ") + json.dumps(usage.get("tokens", {}), ensure_ascii=False))
        else:
            typer.echo(t("Token：未提供，無法確認", "Tokens: not provided, cannot confirm"))
    verification = service.get_task_status(project, task_id)["brief"]["verification"]
    typer.echo(
        t(
            f"\n驗收狀態：{verification['message']}",
            f"\nAcceptance status: {verification['message']}",
        )
    )
    typer.echo(
        t(
            "（Agent 自稱完成不等於驗收成功；上面這行分開標示 Agent 自稱與主機實際驗證結果。）",
            "(An Agent's own claim of completion is not the same as a passing "
            "acceptance; the line above separates what the Agent claimed from what "
            "the host actually verified.)",
        )
    )


@task_app.command(
    "usage",
    help=t(
        "只顯示各次執行的 CLI、模型設定與可取得的用量；舊紀錄標示未知。",
        "Show only each execution's CLI, model settings and available usage; older records show as unknown.",
    ),
)
def task_usage(ctx: typer.Context, task_id: Annotated[str | None, typer.Argument()] = None):
    service, project = bridge(ctx), project_id(ctx)
    output(service.get_task_usage(project, task_id or service.project_workstream_task(project)))


@task_app.command(
    "handoff",
    help=t(
        "唯讀：現在輪到誰、下一步做什麼；若正等待這個 ChatGPT 對話接手，額外附上可直接貼"
        "進去的提示詞與預期回報格式。全部資訊都來自既有的 task status／context，不另建新狀態。",
        "Read-only: whose turn it is right now and what to do next; when a ChatGPT "
        "conversation itself is the thing being waited on, also includes a paste-ready "
        "prompt and the expected report fields. Everything comes from existing task "
        "status/context, never a separate tracked state.",
    ),
)
def task_handoff(ctx: typer.Context, task_id: Annotated[str | None, typer.Argument()] = None):
    service, project = bridge(ctx), project_id(ctx)
    output(service.task_handoff(project, task_id))


@task_app.command(
    "preflight",
    help=t(
        "唯讀：派工前整體檢查，回傳分級的 checks[]（ready/warning/blocker，附修復方式）"
        "與 summary 統計——涵蓋授權、角色 provider 是否可用、ChatGPT 的 Tunnel、"
        "執行中/中斷派工/PM 整理/本機核准等工作流狀態、Skills 與 Context 預算，"
        "以及原本的 test_command／依賴／Git 三項環境檢查。不會安裝、不會真的跑測試、"
        "不會動 Git、不會派工或提交任何東西。",
        "Read-only, overall pre-dispatch check: a leveled checks[] list "
        "(ready/warning/blocker, each with a remediation) plus a summary tally — "
        "covering authorization, whether each role's provider is actually usable, "
        "the ChatGPT Tunnel, workflow state (a live/interrupted dispatch, unrefined "
        "PM reports, a pending local approval), Skills and the Context budget, and "
        "the original test_command/dependency/Git environment checks. Installs "
        "nothing, never runs the test command, never touches Git, never dispatches "
        "or commits anything.",
    ),
)
def task_preflight(ctx: typer.Context, task_id: Annotated[str | None, typer.Argument()] = None):
    service, project = bridge(ctx), project_id(ctx)
    output(service.task_preflight(project, task_id))


@task_app.command(
    "dispatch",
    help=t(
        "低階操作：手動派工單一角色，不經過 advance_project 的自動流程。"
        '例如：ccc-mcp task dispatch TASK-0001 codex SA "分析需求" --version 3',
        "Low-level operation: manually dispatch a single role, bypassing advance_project's "
        'automatic flow. Example: ccc-mcp task dispatch TASK-0001 codex SA "Analyze the '
        'requirement" --version 3',
    ),
)
def task_dispatch(
    ctx: typer.Context,
    task_id: str,
    agent: str,
    role: str,
    instruction: str,
    version: Annotated[int, typer.Option()],
    adapter: str = "real",
    timeout: float = 600,
    model: str | None = None,
    effort: str | None = None,
):
    output(
        bridge(ctx).dispatch_agent(
            project_id(ctx),
            task_id,
            agent,
            role,
            version,
            instruction,
            adapter,
            timeout,
            model=model,
            effort=effort,
        )
    )


@task_app.command(
    "plan",
    help=t(
        "提交一份計畫檔案給 PM 評估／核准；非持續授權專案仍需人工在終端機核准。"
        "例如：ccc-mcp task plan TASK-0001 PLAN-A plan.md --version 3",
        "Submit a plan file for PM evaluation/approval; without continuous authorization "
        "it still needs local terminal approval. Example: ccc-mcp task plan TASK-0001 "
        "PLAN-A plan.md --version 3",
    ),
)
def task_plan(
    ctx: typer.Context, task_id: str, plan: str, file: Path, version: Annotated[int, typer.Option()]
):
    output(
        bridge(ctx).propose_plan(
            project_id(ctx), task_id, plan, file.read_text(encoding="utf-8-sig"), version
        )
    )


@task_app.command(
    "request-approval",
    hidden=True,
    help=t(
        "舊版流程：要求使用者核准一份計畫；持續授權專案的計畫已自動核准，不需要這個指令。",
        "Legacy flow: request user approval for a plan; continuously authorized projects "
        "already auto-approve plans and don't need this.",
    ),
)
def request_approval(ctx: typer.Context, task_id: str, plan: str):
    output(bridge(ctx).request_approval(project_id(ctx), task_id, plan))


@task_app.command(
    "review",
    help=t(
        "派出一次獨立的 Codex 唯讀審查（REVIEW 角色）。例如：ccc-mcp task review TASK-0001 --version 5",
        "Dispatch one independent, read-only Codex review (REVIEW role). Example: "
        "ccc-mcp task review TASK-0001 --version 5",
    ),
)
def task_review(
    ctx: typer.Context, task_id: str, version: Annotated[int, typer.Option()], adapter: str = "real"
):
    output(bridge(ctx).review_changes(project_id(ctx), task_id, version, adapter=adapter))


@task_app.command(
    "cancel",
    help=t(
        "取消任務並要求終止其 worker 的 Agent 行程樹。例如：ccc-mcp task cancel TASK-0001",
        "Cancel a task and request termination of its worker's agent process tree. "
        "Example: ccc-mcp task cancel TASK-0001",
    ),
)
def task_cancel(ctx: typer.Context, task_id: str):
    output(bridge(ctx).cancel_task(project_id(ctx), task_id))


@task_app.command(
    "recover",
    help=t(
        "清除卡在 QUEUED/RUNNING、但沒有存活 worker 持有鎖的執行（worker 已崩潰或未啟動）。",
        "Clear executions stuck QUEUED/RUNNING with no live worker holding their lock "
        "(the worker crashed or never started).",
    ),
)
def task_recover(ctx: typer.Context):
    output(bridge(ctx).recover_workers(project_id(ctx)))


@task_app.command(
    "resume",
    help=t(
        "接續上一輪因派工失敗而中斷的 advance_project（task status 顯示 "
        "waiting_on=DISPATCH_RETRY_NEEDED 時使用）；不需重新提供 ContextPatch，也不會重複提交 "
        "Context 或重複派工。例如：ccc-mcp task resume TASK-0001",
        "Resume a previous round's advance_project that was interrupted before the next "
        "role was confirmed dispatched (use when task status shows "
        "waiting_on=DISPATCH_RETRY_NEEDED); needs no ContextPatch and never re-submits "
        "Context or double-dispatches. Example: ccc-mcp task resume TASK-0001",
    ),
)
def task_resume(ctx: typer.Context, task_id: Annotated[str | None, typer.Argument()] = None):
    service, project = bridge(ctx), project_id(ctx)
    output(service.resume_pending_dispatch(project, task_id or service.project_workstream_task(project)))


@task_app.command(
    "accept",
    hidden=True,
    help=t(
        "舊版流程：人工驗收已完成任務；持續授權專案會自動完成驗收，不需要這個指令。",
        "Legacy flow: manually accept a finished task; continuously authorized projects "
        "complete acceptance automatically and don't need this.",
    ),
)
def task_accept(ctx: typer.Context, task_id: str):
    output(bridge(ctx).accept_local(project_id(ctx), task_id))


@task_app.command(
    "test",
    help=t(
        "本機人工授權後，在任務 Worktree 執行測試命令（預設 pytest -q，或專案設定的 test-command）。",
        "After local human confirmation, run the test command in the task's worktree "
        "(pytest -q by default, or the project's configured test-command).",
    ),
)
def task_test(ctx: typer.Context, task_id: str):
    from ..adapters.test_runner import run_tests_local

    output(run_tests_local(bridge(ctx), project_id(ctx), task_id))


@app.command(
    hidden=True,
    help=t(
        "核准一個待核准計畫（需終端機確認）；持續授權專案不需要這個指令。",
        "Approve a plan awaiting approval (needs terminal confirmation); not needed for "
        "continuously authorized projects.",
    ),
)
def approve(ctx: typer.Context, task_id: str, plan: Annotated[str, typer.Option()]):
    output(bridge(ctx).approve_local(project_id(ctx), task_id, plan))


@app.command(
    help=t(
        "拒絕一個待核准計畫，並記錄原因（需終端機確認）。"
        '例如：ccc-mcp reject TASK-0001 --plan PLAN-A --reason "範圍太大"',
        "Reject a plan awaiting approval and record the reason (needs terminal "
        'confirmation). Example: ccc-mcp reject TASK-0001 --plan PLAN-A --reason "Scope too large"',
    )
)
def reject(
    ctx: typer.Context,
    task_id: str,
    plan: Annotated[str, typer.Option()],
    reason: Annotated[str, typer.Option()],
):
    output(bridge(ctx).reject_local(project_id(ctx), task_id, plan, reason))


@task_app.command(
    "decision",
    help=t(
        "本機記錄一筆決策（需終端機確認），寫入共用 Context 的稽核紀錄。"
        '例如：ccc-mcp task decision TASK-0001 "採用方案 A" "風險最低"',
        "Record a decision locally (needs terminal confirmation) into the shared "
        'Context\'s audit trail. Example: ccc-mcp task decision TASK-0001 "Go with option A" "Lowest risk"',
    ),
)
def task_decision(
    ctx: typer.Context,
    task_id: str,
    content: str,
    reason: str,
    status: str = "Approved",
    supersedes: str | None = None,
):
    output(
        bridge(ctx).record_decision_local(
            project_id(ctx), task_id, content, reason, status, supersedes
        )
    )


@app.command(
    help=t(
        "檢查環境；不執行模型、不假造登入狀態。",
        "Check the environment; never invokes a model, never fakes login status.",
        "检查环境；不执行模型、不假造登录状态。",
    )
)
def doctor(ctx: typer.Context):
    checks = {
        "python": sys.version.split()[0],
        "sqlite": sqlite3.sqlite_version,
        "mcp": importlib.metadata.version("mcp"),
        "registered_projects": len(bridge(ctx).list_projects()),
    }
    for executable in ["git", "codex", "claude"]:
        try:
            path = (
                shutil.which(executable)
                if executable == "git"
                else native_executable(executable)
            )
        except BridgeError:
            path = None
        if not path:
            checks[executable] = {
                "available": False,
                "message": t(
                    "未安裝；Fake 與 Context 功能仍可使用",
                    "Not installed; Fake adapter and Context features still work",
                ),
            }
            continue
        try:
            result = subprocess.run(
                [path, "--version"],
                capture_output=True,
                stdin=subprocess.DEVNULL,
                timeout=15,
                shell=False,
                check=False,
            )
            checks[executable] = {
                "available": result.returncode == 0,
                "version": redact(result.stdout.decode("utf-8", "replace").strip()),
                "authentication": "not checked",
            }
        except (OSError, subprocess.TimeoutExpired) as exc:
            checks[executable] = {"available": False, "error": type(exc).__name__}
    tunnel_binary = tunnel_client_path(bridge(ctx).home)
    checks["tunnel_client"] = {
        "available": tunnel_binary.is_file(),
        "message": (
            None
            if tunnel_binary.is_file()
            else t(
                "尚未安裝；執行 ccc-mcp connect <tunnel_id> 會自動下載並驗證官方 tunnel-client"
                "（兩種工作流都需要它連上 ChatGPT）",
                "Not installed yet; `ccc-mcp connect <tunnel_id>` downloads and verifies "
                "the official tunnel-client automatically (both workflows need it to "
                "connect ChatGPT)",
            )
        ),
    }
    checks["workflows"] = {
        "chatgpt_only": t(
            "不需要 Codex 或 Claude Code CLI；仍需要 git、python 與已連線的 ChatGPT MCP "
            "Tunnel（見上面的 tunnel_client）。套用：ccc-mcp --project <id> config profile "
            "chatgpt-only，或建立專案時加 --profile chatgpt-only。ChatGPT 執行一律互動式"
            "（task usage 會標示 interactive／chatgpt_web），不會偽裝成已呼叫 CLI。",
            "Needs neither the Codex nor the Claude Code CLI; still needs git, python, "
            "and a connected ChatGPT MCP Tunnel (see tunnel_client above). Apply with "
            "ccc-mcp --project <id> config profile chatgpt-only, or pass --profile "
            "chatgpt-only on project create. Every ChatGPT execution stays honestly "
            "interactive (task usage marks it interactive/chatgpt_web) — never faked as "
            "a CLI call.",
        ),
        "best_model": t(
            "額外需要已安裝且登入的 Codex 和／或 Claude Code CLI；只裝其中一個時，"
            "config profile best-model 會把全部角色動態指到那一個。",
            "Additionally needs the Codex and/or Claude Code CLI installed and signed "
            "in; with only one of them installed, config profile best-model dynamically "
            "assigns every role to it.",
        ),
    }
    try:
        checks["context_schema"] = bridge(ctx).validate_context(project_id(ctx))
    except BridgeError as exc:
        checks["context_schema"] = {"registered": False, "message": str(exc)}
    output(checks)


PROTOCOL_MODELS = {
    "agent-report": AgentReport,
    "context-snapshot": ContextSnapshot,
    "context-patch": ContextPatch,
}


@schema_app.command(
    "export",
    help=t(
        "把發布給外部消費者的協議 JSON Schema 寫到指定目錄（見 docs/ch/9-protocol.md）。",
        "Write the protocol JSON Schema published for external consumers to a directory (see docs/en/9-protocol.md).",
    ),
)
def schema_export(out_dir: Path):
    out_dir.mkdir(parents=True, exist_ok=True)
    written = []
    for name, model in PROTOCOL_MODELS.items():
        path = out_dir / f"{name}.json"
        path.write_text(json.dumps(strict_json_schema(model), indent=2) + "\n", encoding="utf-8")
        written.append(str(path))
    output({"written": written})


@skill_app.command(
    "install",
    help=t(
        "從本機路徑或 URL（.md 單檔／.zip 封存）安裝一個全局 Skill。",
        "Install a global skill from a local path or URL (a single .md file, or a .zip archive).",
    ),
)
def skill_install(ctx: typer.Context, source: str):
    if source.startswith(("http://", "https://")):
        typer.echo(
            t(
                "提醒：skill 內容會直接放進 Agent 的 prompt，請只安裝你信任的來源。",
                "Reminder: skill content goes straight into the Agent's prompt; only install sources you trust.",
            ),
            err=True,
        )
    output(install_skill(bridge(ctx).home, source))


@skill_app.command("list", help=t("列出所有已安裝的 Skill。", "List every installed skill."))
def skill_list(ctx: typer.Context):
    output(list_skills(bridge(ctx).home))


@skill_app.command("remove", help=t("移除一個已安裝的 Skill。", "Remove an installed skill."))
def skill_remove(ctx: typer.Context, skill_id: str):
    output(remove_skill(bridge(ctx).home, skill_id))


@app.command(
    help=t(
        "安裝並登入本機 Codex／Claude Code CLI（codex、claude 或 all）。",
        "Install and log in to the local Codex/Claude Code CLI (codex, claude, or all).",
        "安装并登录本机 Codex／Claude Code CLI（codex、claude 或 all）。",
    )
)
def setup(provider: Annotated[str, typer.Argument()] = "codex"):
    if provider not in {"codex", "claude", "all"}:
        raise BridgeError("Unsupported provider: use codex, claude, or all")
    providers = ["codex", "claude"] if provider == "all" else [provider]
    ready = [setup_provider(name) for name in providers]
    output(
        {
            "ready": ready,
            "next_action": t(
                "ccc-mcp connect tunnel_你的實際ID",
                "ccc-mcp connect tunnel_YOUR_ACTUAL_ID",
                "ccc-mcp connect tunnel_你的实际ID",
            ),
        }
    )


@app.command(
    help=t(
        "啟動 MCP；HTTP 預設唯讀，啟用寫入需 OAuth。",
        "Start the MCP server; HTTP defaults to read-only, enabling writes needs OAuth.",
        "启动 MCP；HTTP 预设只读，启用写入需 OAuth。",
    )
)
def serve(
    ctx: typer.Context,
    transport: str = "stdio",
    host: str = "127.0.0.1",
    port: int = 8765,
    enable_writes: bool = False,
):
    if transport not in {"stdio", "streamable-http"}:
        raise BridgeError("Unsupported transport")
    from .server import serve as run_server

    run_server(bridge(ctx).home, transport, host, port, enable_writes)


def _profile_mcp_interpreter(profile_path: Path) -> str | None:
    """Pull the python executable out of a generated profile's mcp_command, or None."""
    match = re.search(r'command:\s*"((?:[^"\\]|\\.)*)"', profile_path.read_text(encoding="utf-8"))
    if not match:
        return None
    unescaped = match.group(1).replace('\\"', '"').replace("\\\\", "\\")
    try:
        parts = shlex.split(unescaped)
    except ValueError:
        return None
    return parts[0] if parts else None


def _profile_is_stale(profile_path: Path) -> bool:
    """True if the profile's mcp_command no longer points to a usable ccc_mcp install.

    This happens when `connect` was first run from a different venv/conda env than the
    one that has ccc_mcp installed: the saved command keeps that old interpreter forever,
    since the profile is only (re)written when it does not already exist.
    """
    interpreter = _profile_mcp_interpreter(profile_path)
    if not interpreter:
        return True
    try:
        result = subprocess.run(
            [interpreter, "-c", "import ccc_mcp"],
            capture_output=True,
            stdin=subprocess.DEVNULL,
            timeout=15,
            shell=False,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return True
    return result.returncode != 0


@app.command(
    help=t(
        "連接 ChatGPT：安裝/驗證 tunnel-client 並啟動長駐連線（取代 scripts/connect-chatgpt.sh）。",
        "Connect to ChatGPT: install/verify tunnel-client and start a persistent connection "
        "(replaces scripts/connect-chatgpt.sh).",
        "连接 ChatGPT：安装/验证 tunnel-client 并启动长驻连接（取代 scripts/connect-chatgpt.sh）。",
    )
)
def connect(ctx: typer.Context, tunnel_id: str):
    if not re.fullmatch(r"tunnel_[a-z0-9]{32}", tunnel_id):
        raise BridgeError("Usage: ccc-mcp connect tunnel_YOUR_ID")
    home = bridge(ctx).home
    client = ensure_tunnel_client(home)

    key = os.environ.get("CONTROL_PLANE_API_KEY")
    if not key:
        typer.echo("Enter the OpenAI runtime API key locally (hidden; do not paste into chat):")
        key = getpass.getpass("")
    if not key:
        raise BridgeError("No key entered")
    if any(c.isspace() for c in key) or key.startswith("tunnel_"):
        raise BridgeError(
            "Invalid key input: enter only the runtime Secret Key, "
            "without spaces/newlines or a tunnel ID"
        )

    for executable, status_args, login_hint in [
        ("codex", ["login", "status"], "codex login"),
        ("claude", ["auth", "status", "--text"], "claude auth login"),
    ]:
        try:
            path = native_executable(executable)
        except BridgeError:
            typer.echo(f"{executable} unavailable. Install it, or configure roles to use another provider.")
            continue
        result = subprocess.run(
            [path, *status_args],
            capture_output=True,
            stdin=subprocess.DEVNULL,
            timeout=15,
            shell=False,
            check=False,
        )
        if result.returncode == 0:
            typer.echo(f"{executable} native CLI found; login available.")
        else:
            typer.echo(f"{executable} found; finish login with: {login_hint}")

    env = {k: v for k, v in os.environ.items() if k not in {"OPENAI_ADMIN_KEY", "OPENAI_API_KEY"}}
    env["CONTROL_PLANE_API_KEY"] = key
    typer.echo("Checking runtime authentication and access to this tunnel...")
    check = subprocess.run(
        [str(client), "admin", "tunnels", "get", tunnel_id], env=env, shell=False, check=False
    )
    if check.returncode != 0:
        raise BridgeError(
            "Authentication/access check failed; connection was not started. Check the runtime "
            "key, its Platform organization, and Tunnels Read + Use permissions."
        )

    full_env = {**os.environ, "CONTROL_PLANE_API_KEY": key}
    profile = f"ccc-mcp-{tunnel_id.removeprefix('tunnel_')}"
    profile_dir = home / "tunnel"
    profile_dir.mkdir(parents=True, exist_ok=True)
    # tunnel-client parses --mcp-command as a command line. Backslashes in
    # Windows paths are consumed as escapes, so use forward slashes here.
    # Deliberately .absolute(), never .resolve(): on POSIX, `python -m venv` makes
    # bin/python a *symlink* to the base interpreter (Windows uses a copy, so this
    # was never an issue there). .resolve() follows that symlink to the base
    # interpreter, which has no pyvenv.cfg next to it and therefore no idea it
    # should see this venv's site-packages — the spawned `-m ccc_mcp` then fails
    # with ModuleNotFoundError. sys.executable from a console-script invocation is
    # already absolute, so .absolute() is just a defensive no-op for the normal case.
    mcp_command = (
        f'"{Path(sys.executable).absolute().as_posix()}" '
        f'-m ccc_mcp --home "{home.resolve().as_posix()}" serve'
    )
    profile_paths = (profile_dir / f"{profile}.yaml", profile_dir / f"{profile}.yml")
    existing_profile = next((path for path in profile_paths if path.is_file()), None)
    if existing_profile is not None and _profile_is_stale(existing_profile):
        typer.echo(
            f"Existing tunnel profile {existing_profile} points to a Python that can no "
            "longer import ccc_mcp (connect was likely first run from a different venv/conda "
            "env). Recreating it with the interpreter running this command now."
        )
        existing_profile.unlink()
    if not any(path.is_file() for path in profile_paths):
        initialized = subprocess.run(
            [
                str(client), "init", "--sample", "sample_mcp_stdio_local",
                "--profile", profile, "--profile-dir", str(profile_dir),
                "--tunnel-id", tunnel_id, "--mcp-command", mcp_command,
                "--control-plane-api-key-ref", "env:CONTROL_PLANE_API_KEY",
                "--health-listen-addr", "127.0.0.1:0",
            ],
            env=full_env,
            shell=False,
            check=False,
        )
        if initialized.returncode != 0:
            raise BridgeError(
                "tunnel-client init failed; the profile was not created. "
                "Fix the MCP command error above before retrying."
            )
        if not any(path.is_file() for path in profile_paths):
            raise BridgeError("tunnel-client init returned success but no profile file was created")

    diagnosed = subprocess.run(
        [str(client), "doctor", "--profile", profile, "--profile-dir", str(profile_dir), "--explain"],
        env=full_env,
        shell=False,
        check=False,
    )
    if diagnosed.returncode != 0:
        raise BridgeError("tunnel-client doctor failed; the connection was not started")
    typer.echo("\nKeep this terminal open while ChatGPT uses ccc-mcp. Press Ctrl+C to stop.\n")
    connected = subprocess.run(
        [str(client), "run", "--profile", profile, "--profile-dir", str(profile_dir)],
        env=full_env,
        shell=False,
        check=False,
    )
    if connected.returncode != 0:
        raise BridgeError("tunnel-client run exited with an error")


@app.command(
    help=t(
        "更新 ccc-mcp；--dev 在原始碼目錄重新可編輯安裝，--tunnel-client 一併更新 tunnel-client。",
        "Update ccc-mcp; --dev reinstalls editable from the source directory, --tunnel-client also updates tunnel-client.",
        "更新 ccc-mcp；--dev 在源码目录重新可编辑安装，--tunnel-client 一并更新 tunnel-client。",
    )
)
def update(
    ctx: typer.Context,
    dev: bool = False,
    tunnel_client: bool = False,
):
    if dev:
        repo_root = None
        for parent in Path(__file__).resolve().parents:
            if (parent / "pyproject.toml").is_file():
                repo_root = parent
                break
        if repo_root is None:
            raise BridgeError("Could not locate pyproject.toml above this file for --dev update")
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "-e", ".[dev]"],
            cwd=repo_root,
            shell=False,
            check=False,
        )
    else:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", "--upgrade", "ccc-mcp"], shell=False, check=False
        )
    if result.returncode != 0:
        raise BridgeError("pip install failed; see output above")
    if tunnel_client:
        path = ensure_tunnel_client(bridge(ctx).home, force=True)
        typer.echo(f"tunnel-client updated: {path}")
    typer.echo(
        t(
            "更新完成；請重新執行 ccc-mcp connect <tunnel_id> 讓新版生效。",
            "Update complete; re-run ccc-mcp connect <tunnel_id> for the new version to take effect.",
            "更新完成；请重新执行 ccc-mcp connect <tunnel_id> 让新版生效。",
        )
    )


def main():
    from pydantic import ValidationError

    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
    try:
        app()
    except (BridgeError, ValidationError, OSError, sqlite3.IntegrityError) as exc:
        typer.echo(redact(str(exc)), err=True)
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
