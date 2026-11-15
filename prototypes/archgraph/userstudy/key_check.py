"""Internal consistency check of the answer keys. Not a substitute for a human pilot.

Verifies that the keys agree with each other and with the fact sheet, so that a key cannot
contradict the evidence it was derived from.
"""

import json
import sys
from pathlib import Path

MATERIALS = Path(__file__).resolve().parent / "materials"
CASES = ["case1_notes", "case2_bookmarks", "case3_todos"]
DISTRACTOR_PREFIX = "匯出 CSV"


def check(case):
    keys = json.loads((MATERIALS / case / "keys.json").read_text(encoding="utf-8"))
    facts = json.loads((MATERIALS / case / "facts.json").read_text(encoding="utf-8"))
    t3 = facts["tasks"]["TASK-0003"]
    problems = []
    grid = {k: v for k, v in keys["S_grid_T3"].items() if not k.startswith(DISTRACTOR_PREFIX)}
    if set(keys["G1_current_features"]) != set(grid):
        problems.append("G1 features differ from the grid rows")
    any_uncommitted = any(v["a_uncommitted_change"] for v in grid.values())
    if any_uncommitted != bool(keys["E1_ready_to_merge"]["pending_actions"]):
        problems.append("E1 pending commit action disagrees with grid column a")
    if bool(t3["out_of_plan_files"]) != ("超出計畫" in keys["E1_ready_to_merge"]["blocking_reasons"]):
        problems.append("E1 'out-of-plan' blocker disagrees with fact sheet")
    if bool(t3["pending_confirmations"]) != ("待確認" in keys["E1_ready_to_merge"]["blocking_reasons"]):
        problems.append("E1 'pending' blocker disagrees with fact sheet")
    if t3["host_test"]["state"] == "PASSED" and any(not v["b_host_test_passed"] for v in grid.values()):
        problems.append("grid column b disagrees with host test state")
    distractor = [v for k, v in keys["S_grid_T3"].items() if k.startswith(DISTRACTOR_PREFIX)]
    if not distractor or any(distractor[0].values()):
        problems.append("distractor row must be all false")
    if not keys["I1_dependents_of_search"]:
        problems.append("I1 has no evidenced dependents")
    if keys["G2_search_implementation_file"] in keys["I1_dependents_of_search"]:
        problems.append("the implementation file cannot depend on itself")
    all_changed = {c["path"] for task in facts["tasks"].values() for c in task["changed_files"]}
    for path in keys["O1_out_of_plan_files"]:
        if path not in all_changed:
            problems.append(f"out-of-plan file not in any changed-file list: {path}")
    return problems


if __name__ == "__main__":
    failed = False
    for case in CASES:
        problems = check(case)
        print(case, "OK" if not problems else problems)
        failed = failed or bool(problems)
    sys.exit(1 if failed else 0)
