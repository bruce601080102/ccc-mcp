"""Score returned pilot material and write per-block metrics. Offline. Descriptive only.

Usage:  python aggregate.py <returned_root> <out_csv> [--include-incomplete]

  <returned_root>/<tester>/response_sheets/<block>.json
  <returned_root>/<tester>/TIMING_AND_ERROR_LOG.csv

Condition and case come from pilot/pilot-v1.1/rater_only/assignment.json, never from the tester's sheet.
Testers that fail completion.py are skipped unless --include-incomplete is given.

Metrics per tester x block (protocol section 5 and T07 definitions):
  score                   total points / total possible (整體正確率)
  q09_accuracy            Q09 correct cells / 21
  q10_all_correct         all three Q10 sub-scores correct (整題正確)
  q10_error_rate          Q10 error subscores / 3 (blank, invalid and wrong count; 不確定 does not)
  dangerous_merge         1 if Q10 answered 能 while an unresolved blocker exists in the key
  error_rate, abstention_rate, invalid entries (from score.py)
  help_count, seconds     from the sheet (seconds = end - start; None if the times are not valid)
  log_rows, log_false     timing log rows for this case, and rows coded FALSE

Summaries by condition are counts and simple means/medians. No significance test is run. The pilot
does not evaluate protocol section 8.
"""

import csv
import importlib
import json
import statistics
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
score = importlib.import_module("score")
build_pilot = importlib.import_module("build_pilot")

ASSIGNMENT = HERE / "pilot" / build_pilot.VERSION / "rater_only" / "assignment.json"
FIELDS = [
    "tester", "block", "case", "condition", "completed", "score", "q09_accuracy", "q10_all_correct",
    "q10_error_rate", "dangerous_merge", "blocker_present", "error_rate", "abstention_rate",
    "invalid_entries", "help_count", "seconds", "log_rows", "log_false",
]


def _seconds(start, end):
    try:
        h1, m1, s1 = (int(x) for x in start.split(":"))
        h2, m2, s2 = (int(x) for x in end.split(":"))
    except (AttributeError, ValueError):
        return None
    delta = (h2 * 3600 + m2 * 60 + s2) - (h1 * 3600 + m1 * 60 + s1)
    return delta if delta >= 0 else None


def _log_rows(folder, case):
    path = folder / "TIMING_AND_ERROR_LOG.csv"
    if not path.exists():
        return 0, 0
    with path.open(encoding="utf-8", newline="") as handle:
        rows = [r for r in csv.DictReader(handle) if r.get("case") == case]
    return len(rows), sum(1 for r in rows if r.get("claim_code") == "FALSE")


def score_returned(root, include_incomplete=False):
    import completion

    assignment = json.loads(ASSIGNMENT.read_text(encoding="utf-8"))
    rows = []
    for tester, blocks in assignment.items():
        folder = Path(root) / tester
        if not folder.exists():
            continue
        if not include_incomplete and completion.check(folder, tester):
            continue
        completed = not completion.check(folder, tester)
        for item in blocks:
            block, case, condition = item["block"], item["case"], item["condition"]
            sheet_path = folder / "response_sheets" / f"{block}.json"
            if not sheet_path.exists():
                continue
            sheet = json.loads(sheet_path.read_text(encoding="utf-8"))
            keys = json.loads((HERE / "materials" / case / "keys.json").read_text(encoding="utf-8"))
            report = score.score(keys, sheet)
            log_rows, log_false = _log_rows(folder, case)
            rows.append({
                "tester": tester, "block": block, "case": case, "condition": condition,
                "completed": completed, "score": report["score"], "q09_accuracy": report["q09_accuracy"],
                "q10_all_correct": report["q10_all_correct"],
                "q10_error_rate": round(report["q10_error_subscores"] / report["q10_subscore_total"], 4),
                "dangerous_merge": int(report["q10_dangerous_merge"]),
                "blocker_present": int(report["q10_blocker_present"]),
                "error_rate": report["error_rate"], "abstention_rate": report["abstention_rate"],
                "invalid_entries": len(report["invalid"]), "help_count": sheet.get("questions_asked_for_help"),
                "seconds": _seconds(sheet.get("start_time"), sheet.get("end_time")),
                "log_rows": log_rows, "log_false": log_false,
            })
    return rows


def summarise(rows):
    summary = {}
    for condition in sorted({r["condition"] for r in rows}):
        group = [r for r in rows if r["condition"] == condition]
        secs = [r["seconds"] for r in group if r["seconds"] is not None]
        items = len(group) * 3
        blockers = sum(r["blocker_present"] for r in group)
        summary[condition] = {
            "n_blocks": len(group),
            "mean_score": round(statistics.mean(r["score"] for r in group), 4),
            "q10_error_rate": round(sum(r["q10_error_rate"] for r in group) / len(group), 4) if items else None,
            "dangerous_merge_rate": round(sum(r["dangerous_merge"] for r in group) / blockers, 4) if blockers else None,
            "median_seconds": statistics.median(secs) if secs else None,
            "mean_help": round(statistics.mean(r["help_count"] or 0 for r in group), 2),
        }
    return summary


def main(argv):
    if len(argv) < 3:
        print("usage: python aggregate.py <returned_root> <out_csv> [--include-incomplete]")
        return 2
    rows = score_returned(argv[1], include_incomplete="--include-incomplete" in argv)
    out = Path(argv[2])
    out.parent.mkdir(parents=True, exist_ok=True)
    with out.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)
    print(json.dumps({"rows": len(rows), "by_condition": summarise(rows)}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
