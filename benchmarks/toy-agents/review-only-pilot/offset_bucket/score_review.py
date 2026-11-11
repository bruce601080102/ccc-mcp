#!/usr/bin/env python3
"""Offline Host scoring for the `offset_bucket` Review-only B1/B2 pilot.

Reuses benchmarks/toy-agents/run.py's existing, already-tested functions —
load_private_fixture, parse_findings (core/flows.py), host_verify_findings —
unchanged. No new Benchmark architecture: this script only drives functions
that already exist and are already covered by tests/test_acqe_private_phase2.py
and tests/test_toy_agents_run.py.

The private Fixture is read on the Host only, to run the one host-owned
confirmation probe each case already has. This script never writes the
Fixture's own secret strings (hidden test source, own_good probe source,
`fixed` reference, defect_label, or the confirmation_witness/claim_terms
text) into its printed report or into any file -- only the model's own
claim/evidence text and a verification_status/host-confirmed boolean are
ever written out.

Usage:
    .venv/bin/python score_review.py b1_response.txt b2_response.txt \
        [--private-fixture PATH] [--mock]

--mock skips loading the real private Fixture and instead uses an
in-memory placeholder confirmation witness, so the whole scoring path
(parse -> host_verify_findings -> report) can be exercised and tested
without ever touching real private answers. Used by this round's Mock
verification; never pass --mock for an actual scoring run.
"""
import argparse
import importlib.util
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


def redact(verified):
    """host_verify_findings() may attach host_confirmation={"probe", "witness"}
    to a confirmed item. `witness` is a private Fixture string -- replace it
    with a plain boolean before this ever gets printed or saved."""
    out = []
    for item in verified:
        item = dict(item)
        if "host_confirmation" in item:
            item["host_confirmation"] = {
                "probe": item["host_confirmation"]["probe"],
                "witness_matched": True,
            }
        out.append(item)
    return out


def score(run, label, response_path, repo):
    text = Path(response_path).read_text(encoding="utf-8")
    findings = [f for f in run.parse_findings(text.splitlines()) if f[0] is not None]
    verified = redact(run.host_verify_findings(findings, CASE, repo))
    lines = [f"=== {label} ({response_path}) ==="]
    if not verified:
        lines.append("  No [severity] claim evidence: ... lines were found in this response.")
    for item in verified:
        lines.append(f"  [{item['severity']}] {item['verification_status']:<10} {item['claim']}")
        lines.append(f"      evidence: {item['evidence']}")
        if item.get("host_confirmation"):
            lines.append(f"      host_confirmation: {item['host_confirmation']}")
    report = "\n".join(lines)
    print(report)
    return verified, report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("b1_response")
    parser.add_argument("b2_response")
    parser.add_argument(
        "--private-fixture",
        default=str(Path.home() / ".local/share/ccc-mcp/acqe-phase2/private_fixture.json"),
    )
    parser.add_argument(
        "--mock", action="store_true",
        help="Use a placeholder confirmation witness instead of the real private "
             "Fixture -- for exercising this script itself, never for real scoring.",
    )
    args = parser.parse_args()

    run = load_run_module()
    if args.mock:
        # Self-authored mock values for exercising this script's control flow only.
        # Not read from, and not claimed to match, the real private Fixture.
        run.ISSUES[CASE] = run.PUBLIC_CASES[CASE]["issue"]
        run.REVIEW_TASKS[CASE] = {
            **run.PUBLIC_CASES[CASE],
            "hidden": "# mock placeholder, not a real hidden test",
            "own_good": (
                "from mod import bucket_for_offset\n\n\n"
                "def test_mock_boundary_offset_starts_next_bucket():\n"
                "    assert bucket_for_offset(10, 10) == 1\n"
            ),
            "fixed": (
                "def bucket_for_offset(offset, width):\n"
                "    if offset < 0 or width <= 0:\n"
                "        raise ValueError('offset and width must be valid')\n"
                "    return offset // width\n"
            ),
            "confirmation_witness": "bucket_for_offset(10, 10)",
            "confirmation_claim_terms": ("boundary", "exclusive", "inclusive", "next"),
            "defect_label": "boundary",
        }
        print("[MOCK MODE: self-authored placeholder Fixture values, NOT the real private Fixture]\n")
    else:
        run.load_private_fixture(args.private_fixture)

    with tempfile.TemporaryDirectory(prefix="acqe-review-only-score-") as tmp:
        repo = build_frozen_repo(run, Path(tmp))
        b1, b1_report = score(run, "B1 (general_review_prompt)", args.b1_response, repo)
        print()
        b2, b2_report = score(run, "B2 (contextual_review_prompt)", args.b2_response, repo)

    summary = (
        f"\n=== Summary ===\n"
        f"B1 findings: {len(b1)} | confirmed: {sum(i['verification_status'] == 'confirmed' for i in b1)}\n"
        f"B2 findings: {len(b2)} | confirmed: {sum(i['verification_status'] == 'confirmed' for i in b2)}\n"
        "\nReminder: this scores one model response pair against one known case. "
        "It is a review-only Finding-quality pilot, not evidence about the full "
        "multi-role ccc-mcp workflow (ACQE-GAP-ANALYSIS.md §20.6)."
    )
    print(summary)

    out_path = HERE / ("mock_verification_output.txt" if args.mock else "score_report.txt")
    out_path.write_text(b1_report + "\n\n" + b2_report + "\n" + summary + "\n", encoding="utf-8")
    print("\nReport written to:", out_path)


if __name__ == "__main__":
    main()
