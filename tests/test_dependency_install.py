import os
import subprocess
import sys
from pathlib import Path

from conftest import approve
from conftest import dispatch as dispatch_role

from ccc_mcp.adapters.test_runner import (
    _resolve_against_venv,
    _venv_environment,
    automatic_test_command,
    detect_dependency_setup,
    ensure_project_venv,
    install_dependencies,
    venv_python_path,
)
from ccc_mcp.worker import run_execution


def pending_patch(bridge, execution_id):
    result = next(
        item
        for item in bridge.latest("demo")["snapshot"]["agent_results"]
        if item["execution_id"] == execution_id
    )
    return {
        "base_context_version": bridge.latest("demo")["version"],
        "changes": {"summary": f"PM reviewed {result['role']} evidence."},
        "source_execution_ids": [execution_id],
        "source_task_ids": [result["task_id"]],
        "source_document_refs": [result["source_document_ref"]],
        "update_reason": "PM verified the completed Agent result",
        "semantic_review_ack": True,
    }


def dispatch_and_run(bridge, task_id, role):
    result = bridge.dispatch_agent(
        "demo",
        task_id,
        "codex" if role in {"SA", "REVIEW"} else "claude",
        role,
        bridge.latest("demo")["version"],
        "Perform role",
        adapter="fake",
    )
    run_execution(bridge, result["execution_id"])
    return result["execution_id"]


def advance_to_implementation(bridge):
    bridge.authorize_project_local("demo")
    task_id = bridge.create_task("demo", "Implement a change")["id"]
    sa = dispatch_and_run(bridge, task_id, "SA")
    return task_id, sa


def test_detect_dependency_setup_prefers_requirements_txt_over_pyproject(tmp_path):
    (tmp_path / "requirements.txt").write_text("flask\n")
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    name, command = detect_dependency_setup(tmp_path)
    assert name == "requirements.txt"
    assert command == [sys.executable, "-m", "pip", "install", "-q", "-r", "requirements.txt"]


def test_detect_dependency_setup_uses_the_given_python_executable(tmp_path):
    (tmp_path / "requirements.txt").write_text("flask\n")
    name, command = detect_dependency_setup(tmp_path, python_executable="/venv/python")
    assert name == "requirements.txt"
    assert command == ["/venv/python", "-m", "pip", "install", "-q", "-r", "requirements.txt"]


def test_detect_dependency_setup_falls_back_to_pyproject(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    name, command = detect_dependency_setup(tmp_path)
    assert name == "pyproject.toml"
    assert command == [sys.executable, "-m", "pip", "install", "-q", "-e", "."]


def test_detect_dependency_setup_installs_declared_test_extra(tmp_path):
    (tmp_path / "pyproject.toml").write_text("[project]\nname='x'\n")
    (tmp_path / "setup.cfg").write_text(
        "[options.extras_require]\ntest =\n    pytest>=8\n"
    )
    assert detect_dependency_setup(tmp_path)[1][-1] == ".[test]"

    (tmp_path / "setup.cfg").unlink()
    (tmp_path / "pyproject.toml").write_text(
        "[project]\nname='x'\n[project.optional-dependencies]\ntest=['pytest>=8']\n"
    )
    assert detect_dependency_setup(tmp_path)[1][-1] == ".[test]"


def test_automatic_test_command_uses_only_changed_test_files(tmp_path):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    test = tmp_path / "tests" / "test_feature.py"
    test.parent.mkdir()
    test.write_text("def test_feature():\n    assert True\n")
    (tmp_path / "module.py").write_text("value = 1\n")
    subprocess.run(["git", "-C", str(tmp_path), "add", "."], check=True)
    subprocess.run(
        ["git", "-C", str(tmp_path), "-c", "user.name=Test",
         "-c", "user.email=test@example.com", "commit", "-qm", "baseline"],
        check=True,
    )

    command, selection = automatic_test_command(tmp_path)
    assert command[-3:] == ["-m", "pytest", "-q"]
    assert selection["mode"] == "full_suite"

    test.write_text("def test_feature():\n    assert 1 == 1\n")
    (tmp_path / "module.py").write_text("value = 2\n")
    command, selection = automatic_test_command(tmp_path)
    assert command[-1] == "tests/test_feature.py"
    assert selection == {
        "mode": "changed_test_files", "paths": ["tests/test_feature.py"], "full_suite": False,
    }

    (tmp_path / "setup.cfg").write_text("[tool:pytest]\ntestpaths = tests\n")
    command, selection = automatic_test_command(tmp_path)
    assert command[-3:] == ["-m", "pytest", "-q"]
    assert selection["mode"] == "project_pytest_config"


def test_detect_dependency_setup_prefers_lockfile_npm_ci(tmp_path):
    (tmp_path / "package.json").write_text("{}")
    (tmp_path / "package-lock.json").write_text("{}")
    name, command = detect_dependency_setup(tmp_path)
    assert (name, command) == ("package-lock.json", ["npm", "ci"])


def test_detect_dependency_setup_npm_install_without_lockfile(tmp_path):
    (tmp_path / "package.json").write_text("{}")
    name, command = detect_dependency_setup(tmp_path)
    assert (name, command) == ("package.json", ["npm", "install"])


def test_detect_dependency_setup_returns_none_when_nothing_recognized(tmp_path):
    (tmp_path / "app.py").write_text("print(1)\n")
    assert detect_dependency_setup(tmp_path) is None


def test_install_dependencies_reports_not_detected_as_a_distinct_state(tmp_path):
    result = install_dependencies(tmp_path)
    assert result == {
        "detected": False,
        "manifest": None,
        "command": None,
        "installed": None,
        "venv_scripts_dir": None,
        "reason": "No recognized dependency manifest "
        "(requirements.txt/pyproject.toml/package.json) found",
    }


def test_install_dependencies_runs_the_detected_command_and_reports_success(tmp_path, monkeypatch):
    (tmp_path / "requirements.txt").write_text("flask\n")
    monkeypatch.setattr(
        "ccc_mcp.adapters.test_runner.detect_dependency_setup",
        lambda cwd, python_executable=None: (
            "requirements.txt", [sys.executable, "-c", "print('installed')"],
        ),
    )
    result = install_dependencies(tmp_path)
    assert result["detected"] is True
    assert result["manifest"] == "requirements.txt"
    assert result["installed"] is True
    assert "installed" in result["stdout"]


def test_install_dependencies_reports_a_failed_install(tmp_path, monkeypatch):
    (tmp_path / "requirements.txt").write_text("flask\n")
    monkeypatch.setattr(
        "ccc_mcp.adapters.test_runner.detect_dependency_setup",
        lambda cwd, python_executable=None: (
            "requirements.txt", [sys.executable, "-c", "import sys; sys.exit(1)"],
        ),
    )
    result = install_dependencies(tmp_path)
    assert result["detected"] is True
    assert result["installed"] is False
    assert "exited 1" in result["reason"]


def test_install_dependencies_handles_a_missing_executable_without_crashing(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "ccc_mcp.adapters.test_runner.detect_dependency_setup",
        lambda cwd, python_executable=None: (
            "package.json", ["definitely-not-a-real-executable-xyz"],
        ),
    )
    result = install_dependencies(tmp_path)
    assert result["installed"] is False
    assert result["reason"]


def test_ensure_project_venv_creates_once_and_reuses(tmp_path):
    """Real venv creation (no network — ensurepip's bundled wheels only), so this is
    actual end-to-end coverage, not a mock. Slower than the rest of this file."""
    home = tmp_path / "home"
    python1 = ensure_project_venv(home, "demo-project")
    assert python1.is_file()
    assert python1 == venv_python_path(home / "venvs" / "demo-project")
    created_at = python1.stat().st_mtime

    python2 = ensure_project_venv(home, "demo-project")
    assert python2 == python1
    assert python2.stat().st_mtime == created_at  # not recreated


def test_venv_environment_puts_the_venv_first_on_path(tmp_path):
    env = _venv_environment(str(tmp_path / "venv" / "Scripts"))
    assert env["PATH"].split(os.pathsep)[0] == str(tmp_path / "venv" / "Scripts")
    assert env["VIRTUAL_ENV"] == str(tmp_path / "venv")
    assert "PYTHONHOME" not in env


def test_resolve_against_venv_rewrites_a_bare_interpreter_name(tmp_path):
    """Windows CreateProcess resolves an unqualified executable name (e.g. "python")
    using the *calling* process's PATH, not the env handed to the child — confirmed by
    this exact scenario previously resolving to the wrong (system) interpreter even
    with env["PATH"] correctly prefixed with the venv. _resolve_against_venv is the fix:
    rewrite argv[0] to an absolute path so no OS-level search happens at all."""
    venv_dir = tmp_path / "venv"
    subprocess.run(
        [sys.executable, "-m", "venv", "--without-pip", str(venv_dir)],
        check=True, capture_output=True, timeout=120,
    )
    python = venv_python_path(venv_dir)

    resolved = _resolve_against_venv(["python", "-c", "print(1)"], str(python.parent))
    assert resolved == [str(python), "-c", "print(1)"]

    # A name the venv doesn't actually provide passes through unchanged.
    assert _resolve_against_venv(["ruby", "-e", "1"], str(python.parent)) == ["ruby", "-e", "1"]


def test_execute_test_command_actually_runs_against_the_venv_interpreter(bridge, human):
    """End-to-end: a configured test_command of plain "python" (no absolute path) must
    really execute with the per-project venv's interpreter once a Python manifest is
    present — not just compute the right path in isolation."""
    bridge.configure_test_command(
        "demo", ["python", "-c", "import sys; print(sys.executable)"]
    )
    bridge.create_task("demo", "需求")
    task_id = "TASK-0001"
    approve(bridge)
    queued = dispatch_role(bridge, "RD")
    run_execution(bridge, queued["execution_id"])
    worktree = Path(bridge.get_task_result("demo", task_id)[-1]["worktree"])
    (worktree / "requirements.txt").write_text("\n")

    from ccc_mcp.adapters.test_runner import run_tests_local

    result = run_tests_local(bridge, "demo", task_id)

    assert result["passed"] is True
    expected_python = venv_python_path(bridge.home / "venvs" / "demo")
    assert Path(result["stdout"].strip()).resolve() == expected_python.resolve()
    assert result["command"][0] == str(expected_python)


def test_install_dependencies_installs_into_a_dedicated_venv_for_python_manifests(tmp_path):
    """Real end-to-end wiring with an empty requirements.txt (nothing to resolve, so
    `pip install` succeeds without needing network) — proves install_dependencies
    actually creates the venv and runs the install command against it."""
    (tmp_path / "requirements.txt").write_text("\n")
    home = tmp_path / "home"

    result = install_dependencies(tmp_path, home=home, project_id="demo-project")

    assert result["detected"] is True
    assert result["installed"] is True
    expected_python = venv_python_path(home / "venvs" / "demo-project")
    assert expected_python.is_file()
    assert result["command"][0] == str(expected_python)
    assert result["venv_scripts_dir"] == str(expected_python.parent)


def test_failed_dependency_install_blocks_review_from_auto_completing(bridge, human, monkeypatch):
    """Regression: a dependency install failure must be treated as a real host test
    failure (executed_by_host=True, passed=False) — not "not executed" — so the same
    test-evidence-authority guarantee (REVIEW can't override a host-observed failure)
    applies to it."""
    bridge.configure_test_command("demo", [sys.executable, "-c", "print('ok')"])
    monkeypatch.setattr(
        "ccc_mcp.adapters.test_runner.detect_dependency_setup",
        lambda cwd, python_executable=None: (
            "requirements.txt", [sys.executable, "-c", "import sys; sys.exit(1)"],
        ),
    )
    task_id, sa = advance_to_implementation(bridge)
    implementation = bridge.advance_project(
        "demo", pending_patch(bridge, sa), "Implement the approved change", adapter="fake"
    )["next_execution"]
    run_execution(bridge, implementation["execution_id"])

    after_tests = bridge.advance_project(
        "demo", pending_patch(bridge, implementation["execution_id"]), adapter="fake"
    )
    assert after_tests["test_evidence"]["executed_by_host"] is True
    assert after_tests["test_evidence"]["passed"] is False
    assert after_tests["test_evidence"]["dependencies"]["installed"] is False
    review_execution_id = after_tests["next_execution"]["execution_id"]
    run_execution(bridge, review_execution_id)  # fake adapter: needs_revision=False

    result = bridge.advance_project(
        "demo", pending_patch(bridge, review_execution_id), adapter="fake"
    )

    assert bridge.task("demo", task_id)["state"] != "COMPLETED"
    assert "completed_workstreams" not in result
    brief = bridge.get_task_status("demo", task_id)["brief"]
    assert brief["tests"]["passed"] is False
    assert brief["tests"]["dependencies"]["detected"] is True
    assert brief["tests"]["dependencies"]["installed"] is False
