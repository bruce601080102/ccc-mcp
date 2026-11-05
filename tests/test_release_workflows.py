"""Keep checks automatic and publishing strictly opt-in."""

from pathlib import Path

import yaml

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"


def _workflow(name):
    # BaseLoader keeps the Actions `on` key as a string (YAML 1.1 otherwise treats it as bool).
    return yaml.load((WORKFLOWS / name).read_text(), Loader=yaml.BaseLoader)


def test_push_and_pull_request_cannot_publish():
    ci = _workflow("ci.yaml")
    checks = _workflow("checks.yaml")
    release = _workflow("release.yaml")

    assert set(ci["on"]) == {"push", "pull_request"}
    assert ci["permissions"] == {"contents": "read"}
    assert ci["jobs"] == {"checks": {"uses": "./.github/workflows/checks.yaml"}}

    assert set(checks["on"]) == {"workflow_call"}
    assert checks["permissions"] == {"contents": "read"}
    assert set(checks["jobs"]) == {"lint", "test", "build", "smoke-install"}

    assert set(release["on"]) == {"workflow_dispatch"}
    assert release["jobs"]["verify"]["uses"] == "./.github/workflows/checks.yaml"
    assert {"github-release", "publish"} <= set(release["jobs"])
