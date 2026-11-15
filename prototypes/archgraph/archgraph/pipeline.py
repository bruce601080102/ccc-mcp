"""Run the prototype end to end: snapshots -> ActualGraph -> identity -> plan -> TaskDelta -> views.

Each `step` is a snapshot: `empty`, `commit:<rev>`, or `worktree`. Steps are processed in order,
so identity carries from one step to the next. The last step is the one being reported; the
step before it is the base for the delta.
"""

import json
import tempfile
from pathlib import Path

from .delta import reconcile
from .diagram import build_diagram, render_html
from .evidence import run_host_tests
from .graph import build_actual, empty_graph
from .identity import assign_identities
from .plan import load_plan
from .snapshot import commit_snapshot, worktree_snapshot
from .store import Store
from .views import engineer_view, novice_view


def _snapshot_for(repo, step):
    if step == "worktree":
        return worktree_snapshot(repo)
    if step == "empty":
        return commit_snapshot(repo, "empty")
    if step.startswith("commit:"):
        return commit_snapshot(repo, step[len("commit:") :])
    raise ValueError(f"unknown step {step!r}; use empty, commit:<rev>, or worktree")


def run_pipeline(
    repo,
    steps,
    db_path,
    out_dir,
    plan_source=None,
    confirmations=(),
    test_argv=None,
    prior_architectures=(),
):
    """prior_architectures: architecture.json dicts of earlier Tasks, oldest first. They are
    carried into this Task's page as history; they never change this Task's own verdicts."""
    if not steps:
        raise ValueError("at least one step is required")
    repo = Path(repo).resolve()
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    store = Store(db_path)
    decisions = {(c["old_key"], c["new_key"]): c["decision"] for c in confirmations}

    previous = empty_graph()
    counter = 0
    history = []
    base, current, current_snapshot = None, None, None
    for step in steps:
        snapshot, contents = _snapshot_for(repo, step)
        graph = build_actual(snapshot, contents, label=step)
        events, counter = assign_identities(previous.nodes, graph, decisions, counter)
        store.save_graph(graph)
        history.append(
            {
                "step": step,
                "gv_id": graph.gv_id,
                "files": len(snapshot.files),
                "symbols": len(graph.nodes),
                "events": events,
            }
        )
        base, current, current_snapshot = previous, graph, snapshot
        previous = graph

    (out / "history.json").write_text(
        json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out / "snapshot.json").write_text(
        json.dumps(current_snapshot.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    summary = {
        "history": [{k: h[k] for k in ("step", "gv_id", "files", "symbols")} for h in history]
    }
    if plan_source is None:
        (out / "actual_graph.json").write_text(
            json.dumps(current.as_dict(), ensure_ascii=False, indent=2), encoding="utf-8"
        )
        store.close()
        return summary

    plan = load_plan(plan_source)
    tests = None
    if test_argv:
        with tempfile.TemporaryDirectory(prefix="archgraph-") as scratch:
            tests = run_host_tests(repo, current_snapshot, test_argv, work_root=scratch)
    base = base if base is not None else empty_graph()
    delta = reconcile(plan, base, current, tests)
    store.save_delta(plan.task, current.gv_id, delta)
    store.close()

    novice = novice_view(delta, plan)
    engineer = engineer_view(delta, current)
    (out / "delta.json").write_text(
        json.dumps(delta, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    (out / "novice.json").write_text(
        json.dumps(novice, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    diagram = build_diagram(plan, base, current, delta, prior=prior_architectures)
    (out / "architecture.json").write_text(
        json.dumps(diagram, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    (out / "architecture.html").write_text(render_html(diagram), encoding="utf-8")
    (out / "engineer.json").write_text(
        json.dumps(engineer, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    summary.update(
        task=plan.task,
        commit_state=delta["commit_state"],
        structure_state=delta["structure_state"],
        host_test_state=delta["host_test_state"],
        out_of_plan_files=delta["out_of_plan_files"],
        pending_confirmations=delta["pending_confirmations"],
        excluded=[{"path": e["path"], "reason": e["reason"]} for e in delta["excluded"]],
    )
    return summary
