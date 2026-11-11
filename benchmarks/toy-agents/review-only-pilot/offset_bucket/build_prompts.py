#!/usr/bin/env python3
"""Build the B1/B2 Review-only prompts for the private `offset_bucket` case.

Reuses benchmarks/toy-agents/run.py's existing, already-tested functions —
general_review_prompt, contextual_review_prompt, select_review_lens — and
acqe_public_cases.py's PUBLIC_CASES. No model call. No new Benchmark code:
this script only drives functions that already exist in the codebase.

The private Fixture is loaded (Host-only, via run.py's own
load_private_fixture()) purely so run.py's ISSUES/REVIEW_TASKS dicts are
populated the same way the real harness populates them — this script never
writes a hidden test, defect_label, confirmation_witness, or `fixed`
reference into anything it outputs. Only `issue` (public), `buggy` (public),
and `visible` (public) ever reach the generated prompt files.

Usage:
    .venv/bin/python build_prompts.py [--private-fixture PATH]

Default --private-fixture is ~/.local/share/ccc-mcp/acqe-phase2/private_fixture.json
(the path already documented in docs/development/ACQE-GAP-ANALYSIS.md §17).
Writes b1_prompt.txt and b2_prompt.txt next to this script.
"""
import argparse
import importlib.util
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO_ROOT = HERE.parents[3]  # .../ccc-mcp
CASE = "offset_bucket"


def load_run_module():
    sys.path.insert(0, str(REPO_ROOT / "tests"))
    sys.path.insert(0, str(REPO_ROOT / "benchmarks" / "toy-agents"))
    spec = importlib.util.spec_from_file_location(
        "acqe_run", REPO_ROOT / "benchmarks" / "toy-agents" / "run.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def build_frozen_repo(run, base):
    """Two commits: a skeleton (visible test only), then the fixture's own
    pre-written `buggy` code — the diff this produces is exactly what
    run_review_arm() would show a real agent-authored attempt. No model call."""
    public = run.PUBLIC_CASES[CASE]
    repo = base / f"{CASE}-fixture"
    (repo / "tests").mkdir(parents=True)
    (repo / "conftest.py").write_text("import sys\nsys.path.insert(0, '.')\n")
    (repo / "tests" / "test_visible.py").write_text(public["visible"])
    run.git(repo, "init", "-q")
    run.git(repo, "add", "-A")
    run.git(repo, "commit", "-q", "-m", "skeleton")
    (repo / "mod.py").write_text(public["buggy"])
    run.git(repo, "add", "-A")
    run.git(repo, "commit", "-q", "-m", "fixture implementation")
    return repo


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--private-fixture",
        default=str(Path.home() / ".local/share/ccc-mcp/acqe-phase2/private_fixture.json"),
    )
    args = parser.parse_args()

    run = load_run_module()
    # Host-only: populates run.ISSUES[CASE] and run.REVIEW_TASKS[CASE] from the
    # public case plus the private Fixture's secret fields. Nothing secret is
    # read by this script afterward -- only ISSUES[CASE] (== the public issue
    # text) and PUBLIC_CASES[CASE]["buggy"/"visible"] feed the prompts below.
    run.load_private_fixture(args.private_fixture)

    with tempfile.TemporaryDirectory(prefix="acqe-review-only-build-") as tmp:
        repo = build_frozen_repo(run, Path(tmp))
        diff = subprocess.run(
            ["git", "-C", str(repo), "diff", "HEAD~1", "HEAD", "--", "."],
            capture_output=True, text=True, check=True,
        ).stdout
        visible = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "tests"], cwd=str(repo),
            capture_output=True, text=True, check=False,
        )
        visible_stdout = visible.stdout + visible.stderr
        visible_passed = visible.returncode == 0

    b1 = run.general_review_prompt(CASE, diff, visible_stdout, visible_passed)
    b2 = run.contextual_review_prompt(CASE, diff, visible_stdout, visible_passed)

    (HERE / "b1_prompt.txt").write_text(b1, encoding="utf-8")
    (HERE / "b2_prompt.txt").write_text(b2, encoding="utf-8")
    (HERE / "diff.txt").write_text(diff, encoding="utf-8")
    (HERE / "visible_stdout.txt").write_text(visible_stdout, encoding="utf-8")

    print("Case:", CASE)
    print("Public issue (for your own reference; NOT what distinguishes B1 vs B2):")
    print(" ", run.ISSUES[CASE])
    print("Visible tests passed on the frozen buggy code:", visible_passed)
    print("Review lens B2 will use:", run.select_review_lens(run.ISSUES[CASE]))
    print()
    print("Wrote:", HERE / "b1_prompt.txt")
    print("Wrote:", HERE / "b2_prompt.txt")


if __name__ == "__main__":
    main()
