"""Command line for the prototype. Prints summaries only; never prints file contents."""

import argparse
import json
import sys
from pathlib import Path

from .pipeline import run_pipeline
from .snapshot import commit_snapshot, worktree_snapshot


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="archgraph", description="Architecture graph prototype (P0B)."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    snap = sub.add_parser("snapshot", help="print snapshot metadata (no contents)")
    snap.add_argument("--repo", required=True)
    snap.add_argument("--rev", help="commit or rev; omit for the working tree")

    run = sub.add_parser(
        "run", help="build graphs for the steps and report the last one against a plan"
    )
    run.add_argument("--repo", required=True)
    run.add_argument("--db", required=True)
    run.add_argument("--out", required=True)
    run.add_argument(
        "--step", action="append", required=True, help="empty | commit:<rev> | worktree"
    )
    run.add_argument("--plan", help="path to the plan JSON for the last step")
    run.add_argument("--confirmations", help="JSON list of {old_key,new_key,decision}")
    run.add_argument("--test-argv", help='JSON list, e.g. ["python","-m","pytest","-q"]')

    args = parser.parse_args(argv)
    if args.command == "snapshot":
        snapshot, _contents = (
            commit_snapshot(args.repo, args.rev) if args.rev else worktree_snapshot(args.repo)
        )
        print(json.dumps(snapshot.as_dict(), ensure_ascii=False, indent=2))
        return 0

    confirmations = (
        json.loads(Path(args.confirmations).read_text(encoding="utf-8"))
        if args.confirmations
        else []
    )
    test_argv = json.loads(args.test_argv) if args.test_argv else None
    summary = run_pipeline(
        args.repo,
        args.step,
        args.db,
        args.out,
        plan_source=args.plan,
        confirmations=confirmations,
        test_argv=[sys.executable if part == "python" else part for part in test_argv]
        if test_argv
        else None,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, default=str))
    return 0
