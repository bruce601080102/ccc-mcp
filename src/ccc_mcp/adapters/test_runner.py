import os
import subprocess
import sys
import uuid
from pathlib import Path

from ..infra.errors import AgentProcessError, BridgeError
from ..infra.security import canonical, redact, safe_path
from .agents.base import run_process
from .git import revision

DEFAULT_TEST_COMMAND = [sys.executable, "-m", "pytest", "-q"]


def _implementation_workspace(bridge, project_id, task_id):
    row = bridge.store.one(
        "SELECT * FROM agent_executions WHERE project_id=? AND task_id=? "
        "AND worktree IS NOT NULL ORDER BY created DESC LIMIT 1",
        (project_id, task_id),
    )
    if not row:
        raise BridgeError("No implementation worktree")
    cwd = bridge.execution_workspace(row)
    if Path(row["worktree"]) != cwd:
        raise BridgeError("Invalid worktree")
    return cwd


_PYTHON_MANIFESTS = {"requirements.txt", "pyproject.toml"}


def venv_python_path(venv_dir):
    return venv_dir / ("Scripts" if os.name == "nt" else "bin") / (
        "python.exe" if os.name == "nt" else "python"
    )


def ensure_project_venv(home, project_id, timeout=300):
    """Lazily create (and on later calls, reuse) a ccc-mcp-managed virtual environment
    for this project's host test execution — separate from any venv the operator
    already has for their own local dev workflow, so `project authorize` never has to
    guess its name or location. Returns the venv's python executable path.

    This is also a safety improvement over installing straight into ccc-mcp's own
    interpreter: Agent-authored dependencies land in an isolated, per-project
    environment instead of polluting (or being polluted by) the service process.
    """
    venv_dir = safe_path(Path(home) / "venvs", project_id)
    python = venv_python_path(venv_dir)
    if python.is_file():
        return python
    venv_dir.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        [sys.executable, "-m", "venv", str(venv_dir)],
        capture_output=True,
        stdin=subprocess.DEVNULL,
        timeout=timeout,
        shell=False,
        check=False,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    if result.returncode or not python.is_file():
        raise BridgeError(
            "Could not create project virtual environment: "
            + redact(result.stderr.decode("utf-8", "replace"))[:500]
        )
    return python


def detect_dependency_setup(cwd, python_executable=None):
    """Return (manifest_name, argv) for the first recognized dependency manifest found
    in `cwd`, or None if none match. A pure function (no subprocess) so the detection
    rule itself is trivially unit-testable and independently overridable in tests.
    requirements.txt is checked before pyproject.toml since a project with both almost
    always treats the former as authoritative for what actually gets installed;
    package-lock.json before package.json so a locked install (`npm ci`) is preferred
    over a resolving one (`npm install`) when both exist."""
    python_executable = python_executable or sys.executable
    if (Path(cwd) / "requirements.txt").is_file():
        return "requirements.txt", [python_executable, "-m", "pip", "install", "-q", "-r", "requirements.txt"]
    if (Path(cwd) / "pyproject.toml").is_file():
        return "pyproject.toml", [python_executable, "-m", "pip", "install", "-q", "-e", "."]
    if (Path(cwd) / "package-lock.json").is_file():
        return "package-lock.json", ["npm", "ci"]
    if (Path(cwd) / "package.json").is_file():
        return "package.json", ["npm", "install"]
    return None


def install_dependencies(cwd, home=None, project_id=None, timeout=600):
    """Best-effort, auto-detected dependency install before running the configured test
    command — the Agent writes/edits requirements.txt, pyproject.toml or package.json as
    part of implementing a feature; nothing previously installed those for the host test
    run, which surfaced as a bare ImportError instead of a real test result. Covered by
    the same `project authorize` consent as test_command itself (see docs/reliability.md);
    this is still host code execution driven by Agent-authored manifest files, which is a
    real trust boundary, not a free security win.

    When `home`/`project_id` are given and a Python manifest is present, dependencies are
    installed into (and the later test_command run against, via `venv_scripts_dir`) a
    dedicated per-project venv rather than ccc-mcp's own interpreter — see
    ensure_project_venv. Node manifests have no equivalent step: `npm install`/`npm ci`
    already install into the project's own `node_modules`.
    """
    python_executable = None
    venv_scripts_dir = None
    is_python_project = (Path(cwd) / "requirements.txt").is_file() or (
        Path(cwd) / "pyproject.toml"
    ).is_file()
    if home is not None and project_id is not None and is_python_project:
        try:
            venv_python = ensure_project_venv(home, project_id)
        except BridgeError as exc:
            manifest = "requirements.txt" if (Path(cwd) / "requirements.txt").is_file() else "pyproject.toml"
            return {
                "detected": True,
                "manifest": manifest,
                "command": None,
                "installed": False,
                "venv_scripts_dir": None,
                "reason": str(exc),
            }
        python_executable = str(venv_python)
        venv_scripts_dir = str(venv_python.parent)
    detected = detect_dependency_setup(cwd, python_executable)
    if not detected:
        return {
            "detected": False,
            "manifest": None,
            "command": None,
            "installed": None,
            "venv_scripts_dir": None,
            "reason": "No recognized dependency manifest "
            "(requirements.txt/pyproject.toml/package.json) found",
        }
    manifest, command = detected
    base = {
        "detected": True,
        "manifest": manifest,
        "command": command,
        "venv_scripts_dir": venv_scripts_dir if manifest in _PYTHON_MANIFESTS else None,
    }
    try:
        stdout, stderr = run_process(command, cwd, "", timeout, lambda: False)
        return base | {"installed": True, "stdout": redact(stdout), "stderr": stderr}
    except AgentProcessError as exc:
        return base | {
            "installed": False,
            "returncode": exc.returncode,
            "stdout": redact(exc.stdout),
            "stderr": exc.stderr,
            "reason": f"Dependency install exited {exc.returncode}",
        }
    except (BridgeError, OSError) as exc:
        return base | {
            "installed": False,
            "reason": f"Could not run dependency install command: {redact(str(exc))}",
        }


def record_test_evidence(bridge, project_id, task_id, result):
    """Persist `result` as the task's current TEST-RESULT.md, including a "not executed"
    determination — so get_task_status always reflects the latest reason, not a stale
    evidence file left over from an earlier, unrelated execution."""
    with bridge.store.transaction() as conn:
        bridge.save_artifact(
            conn,
            project_id,
            task_id,
            "TEST-RESULT.md",
            "TEST-" + uuid.uuid4().hex[:16],
            "# Observed test result\n\n" + canonical(result) + "\n",
        )
    bridge.sync_documents(project_id)
    return result


# pytest's own exit code for "ran fine, found zero tests" — distinct from 1 (tests
# failed), 2 (interrupted) and 4 (usage error). Only meaningful when the command being
# run actually is pytest; checked via _looks_like_pytest before relying on it.
_PYTEST_NO_TESTS_COLLECTED = 5


def _looks_like_pytest(command):
    return any(Path(part).stem.lower() == "pytest" for part in command)


def _venv_environment(venv_scripts_dir):
    """Env mapping that puts a venv's Scripts/bin dir first on PATH — the same
    mechanism a normal venv "activate" uses. This alone is enough on POSIX, where the
    exec family searches PATH from the environment actually given to the child. It is
    NOT enough on its own on Windows (see _resolve_against_venv): kept anyway so any
    *nested* process the command itself spawns (pytest launching a subprocess, etc.)
    still inherits a correctly venv-prefixed PATH."""
    env = dict(os.environ)
    env["PATH"] = venv_scripts_dir + os.pathsep + env.get("PATH", "")
    env["VIRTUAL_ENV"] = str(Path(venv_scripts_dir).parent)
    env.pop("PYTHONHOME", None)
    return env


def _resolve_against_venv(command, venv_scripts_dir):
    """Rewrite command[0] to the venv's own executable when it names a common
    interpreter/console-script found there.

    On Windows, CreateProcess resolves an unqualified executable name (e.g. "python")
    using the *calling* process's PATH, not the environment block handed to the child —
    so prepending the venv to env["PATH"] alone does not reliably redirect a bare
    "python"/"pytest" to the venv (confirmed by test_dependency_install.py; this isn't
    a hypothetical). Rewriting argv[0] to an absolute path sidesteps that search
    entirely, on every platform.
    """
    if not command:
        return command
    stem = Path(command[0]).stem.lower()
    candidate = Path(venv_scripts_dir) / (stem + (".exe" if os.name == "nt" else ""))
    if candidate.is_file():
        return [str(candidate), *command[1:]]
    return command


def execute_test_command(bridge, project_id, task_id, command, identity, cwd=None):
    """Run `command` (an argv, never a shell string) in the task's implementation
    workspace and persist it as TEST-RESULT.md evidence. Shared by the human-confirmed
    `ccc-mcp task test` path and the automatic project-authorize-consented path that
    runs before REVIEW (core.tasks.TaskHub.run_configured_test_command), which now runs
    DEFAULT_TEST_COMMAND when no test_command was explicitly configured.

    A dependency manifest (requirements.txt/pyproject.toml/package.json) is installed
    first, best-effort, if one is present (see install_dependencies). A failed install is
    a real host-observed test failure — not "not executed" — so it still blocks
    completion via the same test-evidence-authority path as a failing test_command. A
    detected Python manifest also runs `command` itself with the per-project venv's
    Scripts/bin directory on PATH, so the configured command does not need to hardcode
    that venv's absolute interpreter path.
    """
    cwd = cwd or _implementation_workspace(bridge, project_id, task_id)
    dependencies = install_dependencies(cwd, home=bridge.home, project_id=project_id)
    if dependencies["detected"] and dependencies["installed"] is False:
        result = {"passed": False, "reason": dependencies["reason"]}
    else:
        scripts_dir = dependencies.get("venv_scripts_dir")
        env = _venv_environment(scripts_dir) if scripts_dir else None
        command = _resolve_against_venv(command, scripts_dir) if scripts_dir else command
        try:
            stdout, stderr = run_process(command, cwd, "", 600, lambda: False, env=env)
            result = {"passed": True, "stdout": redact(stdout), "stderr": stderr}
        except AgentProcessError as exc:
            if exc.returncode == _PYTEST_NO_TESTS_COLLECTED and _looks_like_pytest(command):
                # A brand-new project with no tests yet is not a test *failure* — pytest
                # itself distinguishes this (exit 5) from an actual failure (exit 1).
                # Defaulting to pytest automatically (no explicit test_command) must not
                # punish a project for not having written tests yet.
                result = {
                    "passed": True,
                    "no_tests_collected": True,
                    "stdout": redact(exc.stdout),
                    "stderr": exc.stderr,
                }
            else:
                result = {
                    "passed": False,
                    "returncode": exc.returncode,
                    "stdout": exc.stdout,
                    "stderr": exc.stderr,
                }
        except BridgeError as exc:
            result = {"passed": False, "error": redact(str(exc))}
    result |= {
        "executed_by_host": True,
        "identity": identity,
        "git_revision": revision(cwd),
        # The actual argv executed (post venv-resolution, if any), not just the
        # operator-configured nominal command — evidence should describe reality.
        "command": command,
        "dependencies": dependencies,
    }
    return record_test_evidence(bridge, project_id, task_id, result)


def run_tests_local(bridge, project_id, task_id):
    # Imported here, not at module level: tests monkeypatch confirm_local by string path
    # (ccc_mcp.core.approval.confirm_local), which only reaches callers that re-resolve
    # the name at call time. A module-level `from` import freezes this module's own
    # `confirm_local` binding at whatever moment this module first gets imported — which,
    # depending on test collection order, can happen before any monkeypatch is active.
    # Every other local-approval call site in this codebase (core/tasks.py) already
    # follows this same local-import pattern for the identical reason.
    from ..core.approval import confirm_local

    bridge.approved_plan(project_id, task_id)
    cwd = _implementation_workspace(bridge, project_id, task_id)
    command = bridge.get_project_settings(project_id).get("test_command") or DEFAULT_TEST_COMMAND
    identity = confirm_local(
        f"在 {cwd} 執行 {' '.join(command)}\n測試會執行儲存庫程式碼，請先審閱測試內容。",
        task_id,
    )
    # Serialize with agent activity for this task using the project lock.
    with bridge.lock(project_id):
        live = bridge.store.one(
            "SELECT 1 FROM agent_executions WHERE project_id=? AND task_id=? "
            "AND status IN ('QUEUED','RUNNING')",
            (project_id, task_id),
        )
        if live:
            raise BridgeError("Wait for the active agent before running tests")
        return execute_test_command(bridge, project_id, task_id, command, identity, cwd)
