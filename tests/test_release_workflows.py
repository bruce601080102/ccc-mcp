"""Keep checks automatic and publishing strictly opt-in."""

from pathlib import Path

import yaml

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"


def _workflow(name):
    # BaseLoader keeps the Actions `on` key as a string (YAML 1.1 otherwise treats it as bool).
    return yaml.load((WORKFLOWS / name).read_text(), Loader=yaml.BaseLoader)


def test_all_workflows_are_manual_or_reusable_only():
    ci = _workflow("ci.yaml")
    checks = _workflow("checks.yaml")
    release = _workflow("release.yaml")

    for path in WORKFLOWS.iterdir():
        if path.suffix in {".yaml", ".yml"}:
            assert set(_workflow(path.name)["on"]) <= {"workflow_dispatch", "workflow_call"}

    assert set(ci["on"]) == {"workflow_dispatch"}
    assert ci["permissions"] == {"contents": "read"}
    assert ci["jobs"] == {"checks": {"uses": "./.github/workflows/checks.yaml"}}

    assert set(checks["on"]) == {"workflow_call"}
    assert checks["on"]["workflow_call"]["inputs"]["full_checks"]["default"] == "false"
    assert checks["on"]["workflow_call"]["inputs"]["release_version"]["default"] == ""
    assert checks["permissions"] == {"contents": "read"}
    assert set(checks["jobs"]) == {"lint", "quick-test", "test", "build", "smoke-install"}
    for name in ("quick-test", "test", "build"):
        assert checks["jobs"][name]["needs"] == "lint"
    assert checks["jobs"]["quick-test"]["if"] == "inputs.full_checks == false"
    for name in ("test", "build", "smoke-install"):
        assert checks["jobs"][name]["if"] == "inputs.full_checks"
    version_step = next(
        step
        for step in checks["jobs"]["test"]["steps"]
        if step.get("name") == "Use the manually chosen version for this checkout"
    )
    assert version_step["shell"] == "bash"

    assert set(release["on"]) == {"workflow_dispatch"}
    assert release["on"]["workflow_dispatch"]["inputs"]["version"]["required"] == "true"
    assert release["jobs"]["verify"]["uses"] == "./.github/workflows/checks.yaml"
    assert release["jobs"]["verify"]["with"]["full_checks"] == "true"
    assert release["jobs"]["verify"]["with"]["release_version"] == "${{ inputs.version }}"
    assert {"github-release", "publish"} <= set(release["jobs"])
