"""Assemble the internal pilot packets: two non-author testers, three blocks each.

Assignment (the same table is written to pilot/rater_only/assignment.json):
  P1: block1 notes = A   block2 bookmarks = B   block3 todos = C
  P2: block1 notes = C   block2 bookmarks = B   block3 todos = A

  pilot/tester_P1/, pilot/tester_P2/   what one tester receives. Each packet holds exactly one
                                        material per block, the question sheet, response sheets,
                                        the tester guide, the feedback format and the timing log.
                                        It contains NO answer keys, NO scoring rules, NO condition
                                        letters and nothing from rater_only/.
  pilot/rater_only/                     answer keys, scoring notes, the coordinator guide and the
                                        assignment. Never given to a tester.

Block folders use neutral names. Blinding is weak: page content still shows which view is which
(A is the ccc-mcp view), so the pilot does NOT claim blinding.

Usage: python build_pilot.py   (then: python freeze.py write)
"""

import json
import shutil
from pathlib import Path

HERE = Path(__file__).resolve().parent
MATERIALS = HERE / "materials"
PILOT = HERE / "pilot"
DOCS = HERE / "pilot_docs"

BLOCKS = ["block1_notes", "block2_bookmarks", "block3_todos"]
BLOCK_CASE = {"block1_notes": "case1_notes", "block2_bookmarks": "case2_bookmarks", "block3_todos": "case3_todos"}
TESTERS = ["P1", "P2"]
ASSIGNMENT = {
    "P1": {"block1_notes": "A", "block2_bookmarks": "B", "block3_todos": "C"},
    "P2": {"block1_notes": "C", "block2_bookmarks": "B", "block3_todos": "A"},
}


def load_keys(case):
    return json.loads((MATERIALS / case / "keys.json").read_text(encoding="utf-8"))


def response_template(tester, block, keys):
    """Blank response sheet. Blank = null (not answered). A tester writes 無 for an explicit none."""
    return {
        "tester": tester,
        "block": block,
        "start_time": None,
        "end_time": None,
        "Q01": None,
        "Q02": None,
        "Q03": None,
        "Q04": None,
        "Q05": None,
        "Q06": None,
        "Q07": None,
        "Q08": None,
        "Q09": {title: {"a": None, "b": None, "d": None} for title in keys["S_grid_T3"]},
        "Q10": {"answer": None, "blocking": None, "actions": None},
        "Q11": None,
        "Q12": None,
        "questions_asked_for_help": None,
        "notes": None,
    }


def write_tester_packet(tester, out):
    if out.exists():
        shutil.rmtree(out)
    (out / "blocks").mkdir(parents=True)
    (out / "response_sheets").mkdir()
    for block in BLOCKS:
        condition = ASSIGNMENT[tester][block]
        case = BLOCK_CASE[block]
        src = MATERIALS / case
        dst = out / "blocks" / block
        (dst / "material").mkdir(parents=True)
        if condition == "B":
            shutil.copy(src / "B_text.md", dst / "material" / "summary.md")
        else:
            for html in sorted((src / condition).glob("*.html")):
                shutil.copy(html, dst / "material" / html.name)
        shutil.copy(src / "questions.md", dst / "questions.md")
        sheet = response_template(tester, block, load_keys(case))
        (out / "response_sheets" / f"{block}.json").write_text(
            json.dumps(sheet, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    for name in ("TESTER_GUIDE.md", "FEEDBACK_FORMAT.md", "TIMING_AND_ERROR_LOG.csv"):
        shutil.copy(DOCS / name, out / name)


def write_rater_only(out):
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    for case in sorted(set(BLOCK_CASE.values())):
        shutil.copy(MATERIALS / case / "keys.json", out / f"keys_{case}.json")
    shutil.copy(DOCS / "RATER_NOTES.md", out / "RATER_NOTES.md")
    shutil.copy(DOCS / "COORDINATOR_GUIDE.md", out / "COORDINATOR_GUIDE.md")
    table = {
        tester: [
            {"block": block, "case": BLOCK_CASE[block], "condition": ASSIGNMENT[tester][block]}
            for block in BLOCKS
        ]
        for tester in TESTERS
    }
    (out / "assignment.json").write_text(json.dumps(table, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    PILOT.mkdir(exist_ok=True)
    for tester in TESTERS:
        write_tester_packet(tester, PILOT / f"tester_{tester}")
    write_rater_only(PILOT / "rater_only")
    print("wrote", ", ".join(f"pilot/tester_{t}" for t in TESTERS), "and pilot/rater_only")
