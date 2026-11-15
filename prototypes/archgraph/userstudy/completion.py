"""Completion check for one tester's returned pilot folder (T07 definition). Offline; no scoring.

Usage:  python completion.py <returned_folder> <tester_code>

A tester has COMPLETED the pilot when the returned folder holds:
  response_sheets/<block>.json for all three blocks, each with
    - tester equal to the tester code, and block equal to the file's block
    - start_time and end_time in HH:MM:SS
    - questions_asked_for_help as an integer >= 0
    - notes present as a string (may be empty)
  TIMING_AND_ERROR_LOG.csv present, with its header row intact (rows may be zero)

Allowed and NOT incomplete: blank answers (they score 0 and count as unanswered), 不確定, and an
empty error log. FEEDBACK is optional: a tester writes one only when reporting a problem.
Exit code 0 when complete, 1 otherwise.
"""

import csv
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
BLOCKS = ["block1_notes", "block2_bookmarks", "block3_todos"]
TIME = re.compile(r"^\d{2}:\d{2}:\d{2}$")
LOG_HEADER = ["tester", "case", "condition", "block_position", "question_id", "start_time", "end_time",
              "seconds", "unclear_wording", "claim_text", "claim_code", "note"]
REQUIRED_FIELDS = [f"Q{n:02d}" for n in range(1, 13)] + [
    "tester", "block", "start_time", "end_time", "questions_asked_for_help", "notes",
]


def _seconds(start, end):
    """Seconds from start to end, or None when either is not HH:MM:SS or end is earlier."""
    try:
        h1, m1, s1 = (int(x) for x in start.split(":"))
        h2, m2, s2 = (int(x) for x in end.split(":"))
    except (AttributeError, ValueError):
        return None
    delta = (h2 * 3600 + m2 * 60 + s2) - (h1 * 3600 + m1 * 60 + s1)
    return delta if delta >= 0 else None


def problems_for_times(sheet):
    """True when a time is missing or malformed (the format message is reported separately)."""
    return not all(isinstance(sheet.get(f), str) and TIME.match(sheet[f]) for f in ("start_time", "end_time"))


def check(folder, tester):
    folder = Path(folder)
    problems = []
    for block in BLOCKS:
        path = folder / "response_sheets" / f"{block}.json"
        if not path.exists():
            problems.append(f"missing response sheet: {block}")
            continue
        try:
            sheet = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as error:
            problems.append(f"{block}: not valid JSON ({error})")
            continue
        missing = [field for field in REQUIRED_FIELDS if field not in sheet]
        if missing:
            problems.append(f"{block}: sheet is missing fields {missing}")
        if sheet.get("tester") != tester:
            problems.append(f"{block}: tester is {sheet.get('tester')!r}, expected {tester!r}")
        if sheet.get("block") != block:
            problems.append(f"{block}: block is {sheet.get('block')!r}")
        for field in ("start_time", "end_time"):
            if not isinstance(sheet.get(field), str) or not TIME.match(sheet[field]):
                problems.append(f"{block}: {field} must be HH:MM:SS")
        if not problems_for_times(sheet):
            if _seconds(sheet["start_time"], sheet["end_time"]) is None:
                problems.append(f"{block}: end_time is earlier than start_time")
        helps = sheet.get("questions_asked_for_help")
        if not isinstance(helps, int) or isinstance(helps, bool) or helps < 0:
            problems.append(f"{block}: questions_asked_for_help must be an integer >= 0")
        if not isinstance(sheet.get("notes"), str):
            problems.append(f"{block}: notes must be a string (may be empty)")
    log = folder / "TIMING_AND_ERROR_LOG.csv"
    if not log.exists():
        problems.append("missing TIMING_AND_ERROR_LOG.csv")
    else:
        with log.open(encoding="utf-8", newline="") as handle:
            header = next(csv.reader(handle), [])
        if header != LOG_HEADER:
            problems.append("TIMING_AND_ERROR_LOG.csv header row was changed")
    return problems


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print("usage: python completion.py <returned_folder> <tester_code>")
        sys.exit(2)
    issues = check(sys.argv[1], sys.argv[2])
    for line in issues:
        print("INCOMPLETE:", line)
    print("COMPLETE" if not issues else f"{len(issues)} PROBLEM(S)")
    sys.exit(1 if issues else 0)
