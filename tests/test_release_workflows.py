"""Keep checks automatic and publishing strictly opt-in."""

from pathlib import Path

import yaml

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"


def _workflow(name):
    # BaseLoader keeps the Actions `on` key as a string (YAML 1.1 otherwise treats it as bool).
    return yaml.load((WORKFLOWS / name).read_text(), Loader=yaml.BaseLoader)


def test_all_workflows_are_manual_or_reusable_only():
    checks = _workflow("checks.yaml")
    release = _workflow("release.yaml")

    for path in WORKFLOWS.iterdir():
        if path.suffix in {".yaml", ".yml"}:
            assert set(_workflow(path.name)["on"]) <= {"workflow_dispatch", "workflow_call"}

    # Release is the only workflow a person starts; checks.yaml is reusable only.
    assert {path.name for path in WORKFLOWS.iterdir()} == {"checks.yaml", "release.yaml"}

    assert set(checks["on"]) == {"workflow_call"}
    # Release always runs the whole suite; there is no reduced mode to select.
    assert set(checks["on"]["workflow_call"]["inputs"]) == {"release_version"}
    assert checks["on"]["workflow_call"]["inputs"]["release_version"]["default"] == ""
    assert checks["permissions"] == {"contents": "read"}
    assert set(checks["jobs"]) == {"lint", "test", "build", "smoke-install"}
    for name in ("test", "build"):
        assert checks["jobs"][name]["needs"] == "lint"
    assert checks["jobs"]["smoke-install"]["needs"] == "build"
    for job in checks["jobs"].values():
        assert "if" not in job
    version_step = next(
        step
        for step in checks["jobs"]["test"]["steps"]
        if step.get("name") == "Use the manually chosen version for this checkout"
    )
    assert version_step["shell"] == "bash"

    assert set(release["on"]) == {"workflow_dispatch"}
    assert release["on"]["workflow_dispatch"]["inputs"]["version"]["required"] == "true"
    assert release["jobs"]["verify"]["uses"] == "./.github/workflows/checks.yaml"
    assert release["jobs"]["verify"]["with"] == {"release_version": "${{ inputs.version }}"}
    assert release["jobs"]["verify"]["with"]["release_version"] == "${{ inputs.version }}"
    assert {"github-release", "publish"} <= set(release["jobs"])
