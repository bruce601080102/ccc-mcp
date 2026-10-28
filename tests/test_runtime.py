import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from starlette.testclient import TestClient
from typer.testing import CliRunner

from ccc_mcp.adapters.runtime import (
    require_runtime,
    resolve_runtime,
    resolve_runtime_command,
    runtime_environment,
    validate_runtime,
    version_matches,
)
from ccc_mcp.adapters.test_runner import (
    ensure_project_venv,
    execute_test_command,
    project_venv_dir,
    venv_python_path,
)
from ccc_mcp.infra.errors import BridgeError
from ccc_mcp.interfaces.cli import app
from ccc_mcp.interfaces.web import create_app
from ccc_mcp.service import CCCMCP


def python_runtime(version=None):
    return {"language": "python", "version": version, "executable": sys.executable}


@pytest.mark.parametrize("value", [
    [], "python", {}, {"language": []}, {"language": "unknown"},
    {"language": "python", "version": ">=3.9"},
    {"language": "python", "version": False},
    {"language": "python", "executable": False},
    {"language": "python", "executable": "./python"},
    {"language": "python", "executable": "python\n--version"},
    {"language": "python", "shell": True},
])
def test_rejects_invalid_runtime_settings(value):
    with pytest.raises(BridgeError):
        validate_runtime(value)


def test_versions_match_components_and_never_accept_another_minor():
    assert version_matches("3.9.13", "3.9")
    assert version_matches("22.14.0", "22")
    assert not version_matches("3.90.1", "3.9")
    assert not version_matches("3.10.0", "3.9")
    assert not version_matches("3.9.14", "3.9.13")


def test_settings_survive_restart_file_import_and_handoff(bridge):
    runtime = python_runtime("3.9")
    bridge.configure_runtime("demo", runtime)
    restarted = CCCMCP(bridge.home)
    assert restarted.public_project_settings("demo")["runtime"] == runtime
    assert restarted.provider_overview("demo")["editable"]["runtime"] == runtime
    for role in ("PM", "SA", "SD", "RD", "SD/RD", "REVIEW"):
        content = restarted.build_handoff("demo", role)["content"]
        assert content["runtime"] == runtime
        assert "Do not substitute" in content["runtime_policy"]
    path = Path(bridge.project("demo")["root"]) / ".ccc-mcp/settings.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["runtime"] = {"language": "javascript", "version": "22", "executable": None}
    path.write_text(json.dumps(data), encoding="utf-8")
    assert restarted.get_project_settings("demo")["runtime"] == data["runtime"]
    restarted.configure_runtime("demo", None)
    assert CCCMCP(bridge.home).get_project_settings("demo")["runtime"] is None


def test_cannot_change_runtime_during_an_active_execution(bridge):
    bridge.create_task("demo", "Analyze")
    bridge.dispatch_agent("demo", "TASK-0001", "codex", "SA", 1, "Analyze", adapter="fake")
    with pytest.raises(BridgeError, match="active agent"):
        bridge.configure_runtime("demo", python_runtime())


def test_python_probe_observes_real_executable_and_rejects_mismatch():
    result = require_runtime(python_runtime(f"{sys.version_info.major}.{sys.version_info.minor}"))
    assert result["actual_version"] == ".".join(map(str, sys.version_info[:3]))
    assert Path(result["executable"]).samefile(sys.executable)
    mismatch = resolve_runtime(python_runtime("0.1"))
    assert mismatch["available"] and not mismatch["matches"]
    assert "Requested python 0.1" in mismatch["reason"]


def test_selected_venv_is_separate_and_tests_use_it_without_a_manifest(bridge):
    runtime = python_runtime(f"{sys.version_info.major}.{sys.version_info.minor}")
    bridge.configure_runtime("demo", runtime)
    bridge.create_task("demo", "Check runtime")
    cwd = Path(bridge.project("demo")["root"])
    command = ["python", "-c", "import json,sys; print(json.dumps(sys.executable))"]
    bridge.configure_test_command("demo", command)
    result = execute_test_command(bridge, "demo", "TASK-0001", command, "operator", cwd)
    assert result["passed"], result
    expected = venv_python_path(project_venv_dir(bridge.home, "demo", runtime))
    assert Path(json.loads(result["stdout"])).samefile(expected)
    assert result["command"][0] == str(expected)
    assert not (bridge.home / "venvs/demo").exists()
    assert result["runtime"]["matches"]
    assert ensure_project_venv(bridge.home, "demo", runtime=runtime) == expected
    other = {**runtime, "version": ".".join(map(str, sys.version_info[:3]))}
    assert project_venv_dir(bridge.home, "demo", runtime) != project_venv_dir(bridge.home, "demo", other)


def test_runtime_mismatch_stops_tests_and_dependency_install(bridge, monkeypatch):
    bridge.configure_runtime("demo", python_runtime("0.1"))
    bridge.create_task("demo", "Must not run")
    def unexpected(*args, **kwargs):
        pytest.fail("No dependency install or test process may run with a mismatched runtime")
    monkeypatch.setattr("ccc_mcp.adapters.test_runner.install_dependencies", unexpected)
    monkeypatch.setattr("ccc_mcp.adapters.test_runner.run_process", unexpected)
    result = execute_test_command(
        bridge, "demo", "TASK-0001", ["python", "-c", "print(1)"], "operator",
        Path(bridge.project("demo")["root"]),
    )
    assert not result["passed"] and not result["executed_by_host"]
    assert "Requested python 0.1" in result["reason"]
    assert bridge._latest_test_result("demo", "TASK-0001")["reason"] == result["reason"]
    assert bridge._host_tests_unverified("demo", "TASK-0001")
    assert "project runtime" in bridge._review_outcome("demo", "TASK-0001")[1]


def test_installed_python39_is_used_instead_of_the_service_python(bridge):
    runtime = {"language": "python", "version": "3.9"}
    found = resolve_runtime(runtime)
    if not found["available"] or not found["matches"]:
        pytest.skip("Python 3.9 is not installed")
    bridge.configure_runtime("demo", runtime)
    bridge.create_task("demo", "Verify Python 3.9 selection")
    command = ["python3", "-c", "import json,sys; print(json.dumps(list(sys.version_info[:3])))"]
    bridge.configure_test_command("demo", command)
    result = execute_test_command(
        bridge, "demo", "TASK-0001", command, "operator", Path(bridge.project("demo")["root"])
    )
    assert result["passed"], result
    assert json.loads(result["stdout"])[:2] == [3, 9]
    assert result["runtime"]["actual_version"].startswith("3.9.")


def test_non_python_requires_its_own_test_command(bridge):
    bridge.configure_runtime("demo", {"language": "java", "version": "21"})
    bridge.create_task("demo", "Java project")
    result = execute_test_command(
        bridge, "demo", "TASK-0001", [sys.executable, "-m", "pytest"], "operator",
        Path(bridge.project("demo")["root"]),
    )
    assert not result["executed_by_host"]
    assert "Configure a test command" in result["reason"]
    preflight = bridge.task_preflight("demo", "TASK-0001")
    assert preflight["test_command"]["command"] is None
    assert preflight["runtime"]["language"] == "java"


def test_web_runtime_save_preview_reload_validation_and_clear(bridge):
    web = create_app(bridge.home, token="runtime-session")
    with TestClient(web, headers={"X-CCC-UI-Token": "runtime-session"}) as client:
        url = "/api/projects/demo/config/runtime"
        runtime = python_runtime(f"{sys.version_info.major}.{sys.version_info.minor}")
        assert client.post(url, json={"runtime": runtime}).status_code == 200
        assert client.get("/api/projects/demo/settings").json()["editable"]["runtime"] == runtime
        result = client.post(url + "/check", json={"runtime": {**runtime, "version": "0.1"}})
        assert result.status_code == 200 and not result.json()["matches"]
        assert client.get("/api/projects/demo/settings").json()["editable"]["runtime"] == runtime
        assert client.post(url + "/check", json={}).json()["matches"]
        assert client.post(url, json={"runtime": {"language": "python", "version": "*"}}).status_code == 400
        assert client.post(url, json={}).status_code == 400
        assert client.post(url, json={"runtime": None}).status_code == 200
        assert client.get("/api/projects/demo/settings").json()["editable"]["runtime"] is None
    with TestClient(web) as anonymous:
        assert anonymous.post(url + "/check", json={}).status_code == 401


def test_cli_runtime_and_web_settings_share_the_same_saved_selection(bridge):
    runner = CliRunner()
    args = ["--home", str(bridge.home), "--project", "demo", "config", "runtime"]
    response = runner.invoke(app, [*args, "--language", "python", "--version", "3.9"])
    assert response.exit_code == 0, response.output
    assert bridge.provider_overview("demo")["editable"]["runtime"]["version"] == "3.9"
    checked = runner.invoke(app, [*args, "--check", "--language", "python", "--executable", sys.executable])
    assert checked.exit_code == 0, checked.output
    assert '"matches": true' in checked.output
    assert bridge.get_project_settings("demo")["runtime"]["version"] == "3.9"
    assert runner.invoke(app, [*args, "--clear"]).exit_code == 0
    assert bridge.get_project_settings("demo")["runtime"] is None


@pytest.mark.skipif(not shutil.which("node"), reason="Node.js not installed")
def test_node_runtime_selects_the_configured_binary_for_tests(bridge):
    node = shutil.which("node")
    version = subprocess.check_output([node, "--version"]).decode().strip().lstrip("v")
    runtime = {"language": "javascript", "version": version.split(".")[0], "executable": node}
    bridge.configure_runtime("demo", runtime)
    bridge.configure_test_command("demo", ["node", "-p", "process.execPath"])
    bridge.create_task("demo", "Check Node runtime")
    result = execute_test_command(
        bridge, "demo", "TASK-0001", ["node", "-p", "process.execPath"], "operator",
        Path(bridge.project("demo")["root"]),
    )
    assert result["passed"], result
    assert Path(result["stdout"].strip()).samefile(node)
    assert result["runtime"]["actual_version"] == version


def test_resolves_bare_sibling_tools_using_runtime_path(tmp_path):
    runtime = {"language": "go", "executable": str(tmp_path / "go")}
    env = runtime_environment(runtime)
    assert env["PATH"].split(os.pathsep)[0] == str(tmp_path)
    assert resolve_runtime_command(["go", "test"], runtime, env) == [str(tmp_path / "go"), "test"]
