"""Build the notes-cli demo and run the three tasks through the prototype.

Usage:  python run_demo.py <output-directory>

The demo repository is created at <output-directory>/notes-cli. Each task is reported against the
previous state, with the plan for that task, and with host tests run in a temporary copy.
"""

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parents[1]))  # prototypes/archgraph, so `archgraph` imports

from archgraph.pipeline import run_pipeline  # noqa: E402
from make_notes_cli import build  # noqa: E402

PLANS = HERE / "plans"
TEST_ARGV = [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"]


def run_demo(out_root, demo_dir=None):
    out = Path(out_root).resolve()
    out.mkdir(parents=True, exist_ok=True)
    demo = Path(demo_dir).resolve() if demo_dir else out / "notes-cli"
    refs = build(demo)
    db = out / "archgraph.sqlite"
    c1, c2 = f"commit:{refs['C1']}", f"commit:{refs['C2']}"
    confirmations = json.loads((HERE / "confirmations3.json").read_text(encoding="utf-8"))
    results = {
        "TASK-0001": run_pipeline(
            demo,
            ["empty", c1],
            db,
            out / "TASK-0001",
            plan_source=PLANS / "plan1.json",
            test_argv=TEST_ARGV,
        ),
        "TASK-0002": run_pipeline(
            demo,
            [c1, c2],
            db,
            out / "TASK-0002",
            plan_source=PLANS / "plan2.json",
            test_argv=TEST_ARGV,
        ),
        "TASK-0003": run_pipeline(
            demo,
            [c1, c2, "worktree"],
            db,
            out / "TASK-0003",
            plan_source=PLANS / "plan3.json",
            confirmations=confirmations,
            test_argv=TEST_ARGV,
        ),
    }
    (out / "demo_summary.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    return results


if __name__ == "__main__":
    print(json.dumps(run_demo(sys.argv[1]), ensure_ascii=False, indent=2, default=str))
