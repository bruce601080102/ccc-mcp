"""Keep release checks and publishing behind the manual Release workflow."""

from collections import defaultdict
from itertools import product
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
    assert release["jobs"]["github-release"]["needs"] == "verify"
    assert set(release["jobs"]["publish"]["needs"]) == {"verify", "github-release"}


def test_every_supported_ci_platform_runs_all_its_shards():
    matrix = _workflow("checks.yaml")["jobs"]["test"]["strategy"]["matrix"]
    keys = ("os", "python-version", "shard")
    jobs = [dict(zip(keys, values, strict=True)) for values in product(*(matrix[k] for k in keys))]
    # Extra platforms have distinct OS labels, so each include entry adds a job.
    assert all(job["os"] not in matrix["os"] for job in matrix["include"])
    shards = defaultdict(list)
    for job in [*jobs, *matrix["include"]]:
        shards[job["os"], job["python-version"]].append(
            tuple(map(int, job["shard"].split("/")))
        )

    assert set(shards) == {
        *(("ubuntu-latest", version) for version in ("3.11", "3.12", "3.13", "3.14")),
        ("macos-latest", "3.14"),
        ("windows-latest", "3.14"),
    }
    for parts in shards.values():
        assert sorted(parts) == [(index, len(parts)) for index in range(1, len(parts) + 1)]
