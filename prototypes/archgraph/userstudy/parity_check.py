"""Information-parity check for the three conditions.

For every fact that a question's answer key depends on, verify that the material of each
condition contains the evidence needed to reach it:

  A  ccc-mcp views (the copied architecture.html of each Task)
  B  hand-written text summary (B_text.md)
  C  static diagram of each Task, each page carrying that Task's fact table

This is a presence check, not an answerability test. A fact that is present but hard to
find is still a valid comparison; a fact that is ABSENT from a condition would make that
condition lose points for lack of information, which the protocol forbids.

Exit code 1 if any required fact is missing from any condition.
"""

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
MATERIALS = HERE / "materials"
CASES = ["case1_notes", "case2_bookmarks", "case3_todos"]
TASKS = ["TASK-0001", "TASK-0002", "TASK-0003"]


def text_of(paths):
    return "\n".join(p.read_text(encoding="utf-8") for p in paths)


def conditions(case_dir):
    a = text_of(sorted((case_dir / "A").glob("*.html")))
    b = (case_dir / "B_text.md").read_text(encoding="utf-8")
    c = text_of(sorted((case_dir / "C").glob("*.html")))
    return {"A": a, "B": b, "C": c}


def required_facts(case_dir):
    keys = json.loads((case_dir / "keys.json").read_text(encoding="utf-8"))
    facts = json.loads((case_dir / "facts.json").read_text(encoding="utf-8"))
    items = {}
    for title in keys["G1_current_features"]:
        items[f"G1 feature: {title}"] = title
    items[f"G2 implementation file: {keys['G2_search_implementation_file']}"] = keys["G2_search_implementation_file"]
    for name in ("T1_added", "T2_added", "T3_added", "T2_modified", "T3_modified"):
        for title in keys[name]:
            items[f"{name}: {title}"] = title
    for file_path in keys["O1_out_of_plan_files"]:
        items[f"O1 out-of-plan: {file_path}"] = file_path
    for file_path in keys["I1_dependents_of_search"]:
        items[f"I1 dependent: {file_path}"] = file_path
    # Blocking reasons and the pending commit action are checked by the evidence each
    # condition carries: A shows the gate block, B the Chinese wording, C the fact sheet.
    if "超出計畫" in keys["E1_ready_to_merge"]["blocking_reasons"]:
        items["E1 blocker: out-of-plan"] = {"A": "超出計畫", "B": "超出已核准計畫", "C": "不在已核准計畫內的變更檔案"}
    if "待確認" in keys["E1_ready_to_merge"]["blocking_reasons"]:
        items["E1 blocker: pending"] = {"A": "待確認", "B": "待確認", "C": "待確認的新舊對應"}
    if keys["E1_ready_to_merge"]["pending_actions"]:
        items["E1 action: commit"] = {"A": "需先 commit", "B": "尚未 commit", "C": "UNCOMMITTED"}
    for title in keys["S_grid_T3"]:
        if title.startswith("匯出 CSV"):
            continue  # the distractor is deliberately absent from every condition
        items[f"grid title: {title}"] = title
    for pending in facts["tasks"]["TASK-0003"]["pending_confirmations"]:
        items[f"pending endpoint: {pending['from']}"] = pending["from"].split("::")[0]
    return {label: (needle if isinstance(needle, dict) else {c: needle for c in ("A", "B", "C")})
            for label, needle in items.items()}


def main():
    failures = []
    for case in CASES:
        case_dir = MATERIALS / case
        conds = conditions(case_dir)
        items = required_facts(case_dir)
        print(f"== {case}: {len(items)} required facts")
        for label, needles in items.items():
            missing = [name for name, body in conds.items() if needles[name] not in body]
            if missing:
                failures.append((case, label, missing))
        for case_name, label, missing in [f for f in failures if f[0] == case]:
            print(f"   MISSING in {','.join(missing)}: {label}")
    if failures:
        print(f"\nPARITY FAILED: {len(failures)} missing item(s)")
        sys.exit(1)
    print("\nPARITY OK: every required fact is present in A, B and C for all cases")


if __name__ == "__main__":
    main()
