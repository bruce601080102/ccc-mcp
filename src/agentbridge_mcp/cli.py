import importlib.metadata
import json
import shutil
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Annotated

import typer

from .errors import BridgeError
from .models import ContextPatch
from .security import atomic_write, redact
from .service import AgentBridge

app = typer.Typer(no_args_is_help=True, help="AgentBridge Context Hub 與 Agent 協作工具")
project_app = typer.Typer(no_args_is_help=True)
context_app = typer.Typer(no_args_is_help=True)
task_app = typer.Typer(no_args_is_help=True)
config_app = typer.Typer(no_args_is_help=True, help="本機設定角色、模型、位置與讀取範圍")
app.add_typer(project_app, name="project")
app.add_typer(context_app, name="context")
app.add_typer(task_app, name="task")
app.add_typer(config_app, name="config")


@app.callback()
def options(ctx: typer.Context, home: Path | None = None, project: str | None = None):
    """--home 指定資料庫位置；--project 指定已註冊專案。"""
    bridge = AgentBridge(home)
    ctx.obj = {"bridge": bridge, "project": project}


def bridge(ctx):
    return ctx.obj["bridge"]


def project_id(ctx):
    if ctx.obj["project"]:
        return ctx.obj["project"]
    cwd = Path.cwd().resolve()
    candidates = [p for p in bridge(ctx).list_projects() if cwd.is_relative_to(Path(p["root"]))]
    if not candidates:
        raise BridgeError("目前目錄未註冊，請先 init 或使用 --project")
    return max(candidates, key=lambda p: len(p["root"]))["id"]


def output(value):
    typer.echo(json.dumps(value, ensure_ascii=False, indent=2))


@app.command()
def init(
    ctx: typer.Context,
    path: Annotated[Path, typer.Argument()] = Path("."),
    id: str | None = None,
    purpose: str = "協作開發與維護此專案",
):
    """註冊專案並產生 .agentbridge 共用記憶。"""
    output(bridge(ctx).register_project(path, id, purpose))


@project_app.command("add")
def project_add(
    ctx: typer.Context,
    path: Annotated[Path, typer.Argument()] = Path("."),
    id: str | None = None,
    purpose: str = "協作開發與維護此專案",
):
    output(bridge(ctx).register_project(path, id, purpose))


@project_app.command("list")
def project_list(ctx: typer.Context):
    output(bridge(ctx).list_projects())


@project_app.command("create")
def project_create(ctx: typer.Context, id: str, path: Path | None = None):
    """建立新專案與 Git 基準；不搬移或覆寫既有目錄。"""
    output(bridge(ctx).create_project(id, path))


@config_app.command("show")
def config_show(ctx: typer.Context):
    output(bridge(ctx).get_project_settings(project_id(ctx)))


@config_app.command("role")
def config_role(
    ctx: typer.Context,
    role: str,
    provider: str,
    model: str | None = None,
    effort: str | None = None,
    quota_fallback: str | None = None,
):
    output(
        bridge(ctx).configure_role(project_id(ctx), role, provider, model, effort, quota_fallback)
    )


@config_app.command("read-root")
def config_read_root(ctx: typer.Context, name: str, path: Path):
    output(bridge(ctx).configure_read_root(project_id(ctx), name, path))


@config_app.command("worktree-dir")
def config_worktree_dir(ctx: typer.Context, path: Path):
    output(bridge(ctx).configure_worktree_dir(project_id(ctx), path))


@config_app.command("revoke-root")
def config_revoke_root(ctx: typer.Context, name: str):
    output(bridge(ctx).revoke_read_root(project_id(ctx), name))


@config_app.command("projects-dir")
def config_projects_dir(ctx: typer.Context, path: Path):
    output(bridge(ctx).set_projects_directory(path))


@context_app.command("show")
def context_show(
    ctx: typer.Context, role: str = "PM", task: str | None = None, scope: str | None = None
):
    output(bridge(ctx).get_context(project_id(ctx), role, task, scope))


@context_app.command("collect")
def context_collect(ctx: typer.Context):
    output(bridge(ctx).collect_context_updates(project_id(ctx)))


@context_app.command("history")
def context_history(ctx: typer.Context, offset: int = 0, limit: int = 20):
    output(bridge(ctx).get_context_history(project_id(ctx), offset, limit))


@context_app.command("export")
def context_export(ctx: typer.Context, out: Path | None = None):
    text = bridge(ctx).export_context(project_id(ctx))
    if out:
        atomic_write(out, text)
        typer.echo(str(out.resolve()))
    else:
        typer.echo(text)


@context_app.command("validate")
def context_validate(ctx: typer.Context):
    result = bridge(ctx).validate_context(project_id(ctx))
    output(result)
    if not result["valid"]:
        raise typer.Exit(1)


@context_app.command("recover")
def context_recover(ctx: typer.Context):
    """從 SQLite 重建 Markdown（保留來源漂移警示）。"""
    output(bridge(ctx).recover_context(project_id(ctx)))


@context_app.command("propose")
def context_propose(ctx: typer.Context, patch: Path):
    parsed = ContextPatch.model_validate_json(patch.read_text(encoding="utf-8-sig"))
    output(bridge(ctx).propose_context_patch(project_id(ctx), parsed.model_dump()))


@context_app.command("commit")
def context_commit(ctx: typer.Context, patch_id: str):
    output(bridge(ctx).commit_context_patch(project_id(ctx), patch_id))


@context_app.command("import-patch")
def context_import(ctx: typer.Context, patch: Path):
    """沒有 MCP 寫入權限時，以 JSON 匯入並提交 Patch。"""
    parsed = ContextPatch.model_validate_json(patch.read_text(encoding="utf-8-sig"))
    proposed = bridge(ctx).propose_context_patch(project_id(ctx), parsed.model_dump())
    output(bridge(ctx).commit_context_patch(project_id(ctx), proposed["patch_id"]))


@context_app.command("source")
def context_source(ctx: typer.Context, path: str):
    output(bridge(ctx).source_ref(project_id(ctx), path))


@task_app.command("create")
def task_create(
    ctx: typer.Context,
    requirement: str,
    acceptance: Annotated[list[str] | None, typer.Option()] = None,
):
    output(bridge(ctx).create_task(project_id(ctx), requirement, acceptance))


@task_app.command("list")
def task_list(ctx: typer.Context):
    output(bridge(ctx).list_tasks(project_id(ctx)))


@task_app.command("status")
def task_status(ctx: typer.Context, task_id: str):
    output(bridge(ctx).get_task_status(project_id(ctx), task_id))


@task_app.command("result")
def task_result(ctx: typer.Context, task_id: str):
    output(bridge(ctx).get_task_result(project_id(ctx), task_id))


@task_app.command("usage")
def task_usage(ctx: typer.Context, task_id: str):
    """只顯示各次執行的 CLI、模型設定與可取得的用量；舊紀錄標示未知。"""
    output(bridge(ctx).get_task_usage(project_id(ctx), task_id))


@task_app.command("dispatch")
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


@task_app.command("plan")
def task_plan(
    ctx: typer.Context, task_id: str, plan: str, file: Path, version: Annotated[int, typer.Option()]
):
    output(
        bridge(ctx).propose_plan(
            project_id(ctx), task_id, plan, file.read_text(encoding="utf-8-sig"), version
        )
    )


@task_app.command("request-approval")
def request_approval(ctx: typer.Context, task_id: str, plan: str):
    output(bridge(ctx).request_approval(project_id(ctx), task_id, plan))


@task_app.command("review")
def task_review(
    ctx: typer.Context, task_id: str, version: Annotated[int, typer.Option()], adapter: str = "real"
):
    output(bridge(ctx).review_changes(project_id(ctx), task_id, version, adapter=adapter))


@task_app.command("cancel")
def task_cancel(ctx: typer.Context, task_id: str):
    output(bridge(ctx).cancel_task(project_id(ctx), task_id))


@task_app.command("recover")
def task_recover(ctx: typer.Context):
    output(bridge(ctx).recover_workers(project_id(ctx)))


@task_app.command("accept")
def task_accept(ctx: typer.Context, task_id: str):
    output(bridge(ctx).accept_local(project_id(ctx), task_id))


@task_app.command("test")
def task_test(ctx: typer.Context, task_id: str):
    """本機人工授權後，在任務 Worktree 執行固定 pytest 命令。"""
    from .test_runner import run_tests_local

    output(run_tests_local(bridge(ctx), project_id(ctx), task_id))


@app.command()
def approve(ctx: typer.Context, task_id: str, plan: Annotated[str, typer.Option()]):
    output(bridge(ctx).approve_local(project_id(ctx), task_id, plan))


@app.command()
def reject(
    ctx: typer.Context,
    task_id: str,
    plan: Annotated[str, typer.Option()],
    reason: Annotated[str, typer.Option()],
):
    output(bridge(ctx).reject_local(project_id(ctx), task_id, plan, reason))


@task_app.command("decision")
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


@app.command()
def doctor(ctx: typer.Context):
    """檢查環境；不執行模型、不假造登入狀態。"""
    checks = {
        "python": sys.version.split()[0],
        "sqlite": sqlite3.sqlite_version,
        "mcp": importlib.metadata.version("mcp"),
        "registered_projects": len(bridge(ctx).list_projects()),
    }
    for executable in ["git", "codex", "claude"]:
        path = shutil.which(executable)
        if not path:
            checks[executable] = {
                "available": False,
                "message": "未安裝；Fake 與 Context 功能仍可使用",
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
    try:
        checks["context_schema"] = bridge(ctx).validate_context(project_id(ctx))
    except BridgeError as exc:
        checks["context_schema"] = {"registered": False, "message": str(exc)}
    output(checks)


@app.command()
def serve(
    ctx: typer.Context,
    transport: str = "stdio",
    host: str = "127.0.0.1",
    port: int = 8765,
    enable_writes: bool = False,
):
    """啟動 MCP；HTTP 預設唯讀，啟用寫入需 OAuth。"""
    if transport not in {"stdio", "streamable-http"}:
        raise BridgeError("Unsupported transport")
    from .server import serve as run_server

    run_server(bridge(ctx).home, transport, host, port, enable_writes)


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
