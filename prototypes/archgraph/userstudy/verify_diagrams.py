"""T08-B automated consistency check of the three architecture views against their evidence.

Offline apart from the host-test re-run. Not a human study and not a usability result: this checks
that the diagrams, the node states, the change history and the test results agree with each other
and with git, the approved plan and a fresh host-test run.

For every case and every Task (TASK-0001..0003) it checks:
  view A  architecture.json header and alerts  vs  delta.json (recomputed approval gate included)
  view B  architecture.json planned tab        vs  the approved plan file (PlannedGraph)
  view C  engineer.json                        vs  delta.json (ActualGraph and node states)
  history history.json                         vs  delta base, and prior_tasks vs each earlier Task
  HTML    architecture.html                    shows the gate, out-of-plan files and pending pairs
  tests   delta host-test state                vs  exit code, and vs a fresh re-run (verify_keys)

Usage: python verify_diagrams.py <scratch-dir-with-cases> [--report <out.json>]
Exit code 0 only if every check passes for every case.
"""

import importlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
verify_keys = importlib.import_module("verify_keys")
from archgraph.gate import approval_gate  # noqa: E402

CASES = ["case1_notes", "case2_bookmarks", "case3_todos"]
TASKS = ["TASK-0001", "TASK-0002", "TASK-0003"]
PLAN_FILE = verify_keys.PLAN_FILE


def _load(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def plan_files(plan):
    """Planned files, plus the endpoints of planned dependencies (the planned tab draws both, as the
    approved plan states them)."""
    files = plan["files"]
    names = set(files.get("add", []) + files.get("modify", []) + files.get("delete", []) + files.get("affected", []))
    for src, dst in plan.get("planned_dependencies") or []:
        names.update((src, dst))
    return names


def check_task(scratch, case, task, results):
    task_dir = scratch / case / task
    plan = _load(scratch / case / "plans" / PLAN_FILE[task])
    delta = _load(task_dir / "delta.json")
    arch = _load(task_dir / "architecture.json")
    engineer = _load(task_dir / "engineer.json")
    novice = _load(task_dir / "novice.json")
    history = _load(task_dir / "history.json")
    html = (task_dir / "architecture.html").read_text(encoding="utf-8")
    header, alerts = arch["header"], arch["alerts"]

    def check(name, ok, detail=""):
        results.append({"case": case, "task": task, "check": name, "ok": bool(ok), "detail": str(detail)})

    # view A: header and alerts agree with the delta
    gate = approval_gate(delta)
    check("A gate status equals recomputed approval gate", header["gate"]["status"] == gate["status"],
          f"header={header['gate']['status']} recomputed={gate['status']}")
    check("A gate blocker codes equal recomputed blockers",
          sorted(b["code"] for b in header["gate"]["blockers"]) == sorted(b["code"] for b in gate["blockers"]))
    check("A commit state equals delta", header["commit_state"] == delta["commit_state"])
    check("A structure state equals delta", header["structure_state"] == delta["structure_state"])
    check("A host test equals delta host-test state", header["host_test"] == delta["host_test_state"])
    check("A out-of-plan list equals delta", header["out_of_plan"] == delta["out_of_plan_files"],
          f"header={header['out_of_plan']} delta={delta['out_of_plan_files']}")
    check("A pending count equals delta", header["pending"] == len(delta["pending_confirmations"]))
    check("A pending pairs in alerts equal delta",
          len(alerts["pending_pairs"]) == len(delta["pending_confirmations"]))
    check("A excluded count equals delta", header["excluded"] == len(delta["excluded"]))
    check("A unattributed out-of-plan files are a subset of out-of-plan files",
          set(alerts["unattributed_out_of_plan"]) <= set(delta["out_of_plan_files"]))

    # view B: planned tab equals the approved plan (PlannedGraph)
    planned_nodes = set(arch["tabs"]["planned"]["nodes"])
    check("B planned tab nodes equal the approved plan files", planned_nodes == plan_files(plan),
          f"tab-only={sorted(planned_nodes - plan_files(plan))} plan-only={sorted(plan_files(plan) - planned_nodes)}")

    # view C: engineer view equals the delta (ActualGraph and node states)
    check("C engineer file states equal delta file states", engineer["files"] == delta["files"])
    check("C engineer out-of-plan list equals delta", engineer["out_of_plan_files"] == delta["out_of_plan_files"])
    check("C engineer excluded paths equal delta",
          sorted(e["path"] for e in engineer["excluded"]) == sorted(e["path"] for e in delta["excluded"]))
    check("C engineer identity events equal delta", engineer["identity_events"] == delta["identity_events"])
    check("C engineer task id equals the Task", engineer["task"] == task)

    # novice view (the plain-language view) reports the same task and provisional flag
    check("novice view task equals the Task", novice["task"] == task)
    check("novice provisional flag equals delta current.provisional",
          bool(novice["provisional"]) == bool(delta["current"].get("provisional")))

    # novice feature view: nodes, markers, members, reachability of pending pairs
    tab = arch["tabs"]["novice"]
    feats = tab["features"]
    check("novice feature nodes equal novice.json features",
          {k for k in feats if k != "UNATTRIBUTED"} == {f["id"] for f in novice["features"]})
    check("novice feature markers equal novice.json",
          all(feats[f["id"]]["marker"] == f["marker"] for f in novice["features"]))
    check("novice unattributed node exists exactly when files are unattributed",
          ("UNATTRIBUTED" in feats) == bool(alerts["unattributed_out_of_plan"]))
    check("novice feature files equal the plan's members",
          all(sorted(x["path"] for x in feats[pf["id"]]["files"])
              == sorted({m.split("::", 1)[0] for m in pf.get("members", [])}) for pf in plan["features"]))
    check("novice edges connect existing feature nodes",
          all(e["src"] in tab["nodes"] and e["dst"] in tab["nodes"] for e in tab["edges"]))
    owners = tab["feature_of_file"]
    check("novice main view draws no feature arrows (no symbol-level evidence exists)", tab["edges"] == [])
    relation_ok = True
    relation_real = True
    real_pairs = set(arch["edges_summary"]["matched"]) | set(arch["edges_summary"]["unplanned"]) | set(
        arch["edges_summary"]["neutral"])
    for rel in tab["relations"]:
        pairs = [tuple(x.strip() for x in ev.split("→")) for ev in rel["evidence"]]
        if rel["status"] == "FILE_IMPORT":
            relation_ok &= any(len(owners[a]) == 1 and len(owners[b]) == 1
                               and "UNATTRIBUTED" not in owners[a] + owners[b] for a, b in pairs)
        else:
            relation_ok &= rel["status"] == "FILE_IMPORT_SHARED" and all(
                len(owners[a]) > 1 or len(owners[b]) > 1 for a, b in pairs)
        relation_real &= all(f"{a} → {b}" in real_pairs for a, b in pairs)
    check("feature relations are file-import evidence, classified by file ownership", relation_ok)
    check("every relation's evidence is a real file import in the engineer view", relation_real)
    check("no unconfirmed field mixes Git, scope and identity in the novice view",
          all("unconfirmed" not in f for f in novice["features"]) and all(
              isinstance(f.get("identity_pending"), int) for f in novice["features"]))
    check("every pending pair is reachable from the novice view",
          all(p["from_file"] in tab["feature_of_file"] and p["to_file"] in tab["feature_of_file"]
              for p in alerts["pending_pairs"]))

    # history: the base of the delta is the step before the reported one
    current = delta["current"]
    expected_last = "worktree" if current["mode"] == "worktree" else f"commit:{current['head']}"
    check("history ends with the reported step", history[-1]["step"] == expected_last,
          f"history={history[-1]['step']} expected={expected_last}")
    if delta["base"]["mode"] == "commit":
        check("history base step equals delta base commit",
              history[-2]["step"] == delta["base"]["label"], f"history={history[-2]['step']} delta={delta['base']['label']}")
    check("prior_tasks in view A are the earlier Tasks, in order",
          [p["task"] for p in arch["prior_tasks"]] == TASKS[:TASKS.index(task)])

    # HTML shows the same key facts
    check("HTML shows the gate status", header["gate"]["status"] in html)
    check("HTML names every out-of-plan file", all(p in html for p in delta["out_of_plan_files"]))
    check("HTML names the Task", task in html)

    # tests: state agrees with exit code
    host = delta["host_test"]
    check("host test state agrees with exit code",
          (host.get("state") == "PASSED") == (host.get("exit_code") == 0),
          f"state={host.get('state')} exit={host.get('exit_code')}")


def run(scratch):
    results = []
    for case in CASES:
        for task in TASKS:
            check_task(scratch, case, task, results)
        # Independent layer: git, plan, imports and a fresh host-test re-run (verify_keys).
        for name, ok, detail in verify_keys.verify_case(case, scratch, run_tests=True):
            results.append({"case": case, "task": "ALL", "check": f"verify_keys: {name}", "ok": bool(ok),
                            "detail": detail})
    return results


def main(argv):
    if len(argv) < 2:
        print("usage: python verify_diagrams.py <scratch-dir-with-cases> [--report <out.json>]")
        return 2
    results = run(Path(argv[1]).resolve())
    failed = [r for r in results if not r["ok"]]
    for r in failed:
        print("FAIL", r["case"], r["task"], r["check"], r["detail"])
    print(f"{len(results) - len(failed)}/{len(results)} checks passed")
    if "--report" in argv:
        out = Path(argv[argv.index("--report") + 1])
        out.write_text(json.dumps({"passed": len(results) - len(failed), "total": len(results),
                                   "results": results}, ensure_ascii=False, indent=2), encoding="utf-8")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
