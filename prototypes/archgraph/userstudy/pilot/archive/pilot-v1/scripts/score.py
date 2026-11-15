"""Score one tester's response sheet against a case's answer key (rater use).

Usage:  python score.py <keys.json> <response.json>

The response format is written by build_pilot.py (response_sheets/*.json). Scoring rules (protocol section 5):

  set questions (Q01, Q03-Q08, Q11, Q12)  F1 of the listed items against the key
  Q02                                     1 if the path matches exactly
  Q09                                     one point per feature x axis cell; 是 / 否 (or JSON true / false)
                                          不確定 or blank abstains; any other text is listed as invalid
  Q10                                     three sub-scores, so one error cannot hide the others:
                                            answer    NO is the only correct answer
                                            blocking  the set of blocking reasons, exactly
                                            actions   the pending actions, exactly
  FALSE claim                             every wrong listed item, wrong cell, wrong option or
                                          wrong answer. Missing answers are not FALSE claims.
  error rate                              FALSE claims / all claims made
  abstention rate                         "不確定" plus unanswered items / all items

A blank field (null) is not answered: 0 points and an abstention. An explicit 無 is an answer.
Q10 all-correct (the protocol's Q10 accuracy) is true only when answer, blocking and actions are all correct.
Unknown option strings in Q10 are listed under "invalid" and count as FALSE claims (as wrong items).

A choice of "尚未 commit" as a blocking reason is a FALSE claim: by the approval-gate definition
(archgraph/gate.py) an uncommitted change is a pending action, not a blocker.
"""

import json
import sys

DISTRACTOR_PREFIX = "匯出 CSV"  # the distractor row in Q09; it exists in no Task
ABSTAIN_MARK = "abstain"
INVALID_MARK = "invalid"
BLOCKING_OPTIONS = {
    "超出計畫的檔案": "OUT_OF_PLAN",
    "待確認的新舊對應": "PENDING_IDENTITY",
    "主機測試未通過": "HOST_TEST_NOT_PASSED",
    "尚未 commit": "UNCOMMITTED",  # not a blocker by definition: always a FALSE claim
}
ACTION_OPTIONS = {
    "需先 commit": "COMMIT_REQUIRED",
    "需要重跑測試": "RERUN_TESTS",  # the host test already passed: always a FALSE claim
    "不需要任何動作": "NO_ACTION",  # commit is required: always a FALSE claim
}
KEY_TO_BLOCKING = {"超出計畫": "OUT_OF_PLAN", "待確認": "PENDING_IDENTITY"}
KEY_TO_ACTION = {"需先 commit": "COMMIT_REQUIRED"}
ABSTAIN = "不確定"


def _items(value):
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    return [v.strip() for v in value if isinstance(v, str) and v.strip() and v.strip() != "無"]


def _cell_value(value):
    """True / False for 是 / 否 (or JSON true / false); abstain for blank or 不確定; invalid otherwise."""
    if value is None:
        return ABSTAIN_MARK
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        text = value.strip().lower()
        if text in ("是", "true"):
            return True
        if text in ("否", "false"):
            return False
        if text in ("", ABSTAIN):
            return ABSTAIN_MARK
    return INVALID_MARK


def _set_score(answer, key):
    answer, key = set(answer), set(key)
    if not answer and not key:
        return 1.0, 0, 0
    correct = len(answer & key)
    precision = correct / len(answer) if answer else 0.0
    recall = correct / len(key) if key else 0.0
    f1 = 0.0 if precision + recall == 0 else 2 * precision * recall / (precision + recall)
    return f1, len(answer - key), len(answer)


def score(keys, response):
    report = {"points": {}, "possible": {}, "false_claims": 0, "claims": 0, "abstentions": 0, "items": 0,
              "invalid": []}

    def add(name, points, possible, false=0, claims=0, abstain=0, items=1):
        report["points"][name] = round(points, 4)
        report["possible"][name] = possible
        report["false_claims"] += false
        report["claims"] += claims
        report["abstentions"] += abstain
        report["items"] += items

    for name, key_name in (
        ("Q01", "G1_current_features"),
        ("Q03", "T1_added"),
        ("Q04", "T1_modified"),
        ("Q05", "T2_added"),
        ("Q06", "T2_modified"),
        ("Q07", "T3_added"),
        ("Q08", "T3_modified"),
        ("Q11", "O1_out_of_plan_files"),
        ("Q12", "I1_dependents_of_search"),
    ):
        raw = response.get(name)
        if raw is None or raw == ABSTAIN or raw == [ABSTAIN]:  # blank or 不確定: not answered
            add(name, 0.0, 1, abstain=1)
            continue
        answer = _items(raw)
        if not answer:  # an explicit 無: one claim that is false when the key has items
            add(name, 0.0 if keys[key_name] else 1.0, 1, false=1 if keys[key_name] else 0, claims=1)
            continue
        f1, false, claims = _set_score(answer, keys[key_name])
        add(name, f1, 1, false=false, claims=claims)

    q02 = response.get("Q02")
    q02_ok = isinstance(q02, str) and q02.strip() == keys["G2_search_implementation_file"]
    add("Q02", 1.0 if q02_ok else 0.0, 1, false=0 if (q02_ok or not q02) else 1, claims=1 if q02 else 0,
        abstain=1 if (not q02 or q02 == ABSTAIN) else 0)

    grid_key = keys["S_grid_T3"]
    grid_answer = response.get("Q09") or {}
    cells_correct = cells_false = cells_abstain = 0
    cells_total = 0
    # Response sheets use the short axis names a / b / d (see build_pilot.response_template).
    short = {"a_uncommitted_change": "a", "b_host_test_passed": "b", "d_pending_mapping": "d"}
    for feature, axes in grid_key.items():
        for axis, truth in axes.items():
            cells_total += 1
            row = grid_answer.get(feature) or {}
            raw = row.get(short[axis], row.get(axis))
            given = _cell_value(raw)
            if given == ABSTAIN_MARK:
                cells_abstain += 1
                continue
            if given == INVALID_MARK:  # not usable: abstain, but listed so the rater checks it
                cells_abstain += 1
                report["invalid"].append(f"Q09 {feature} / {axis}: {raw!r}")
                continue
            if given == truth:
                cells_correct += 1
            else:
                cells_false += 1
    add("Q09", cells_correct, cells_total, false=cells_false, claims=cells_correct + cells_false,
        abstain=cells_abstain, items=cells_total)
    report["q09_accuracy"] = round(cells_correct / cells_total, 4)

    q10 = response.get("Q10") or {}
    readiness = keys["E1_ready_to_merge"]
    answer_ok = q10.get("answer") == "不能"
    add("Q10_answer", 1.0 if answer_ok else 0.0, 1,
        false=0 if (answer_ok or not q10.get("answer")) else 1, claims=1 if q10.get("answer") else 0,
        abstain=1 if not q10.get("answer") or q10.get("answer") == ABSTAIN else 0)

    chosen_blocking = _items(q10.get("blocking"))
    key_blocking = {KEY_TO_BLOCKING[r] for r in readiness["blocking_reasons"]}
    wrong_blocking = [c for c in chosen_blocking if BLOCKING_OPTIONS.get(c) not in key_blocking]
    right_blocking = [c for c in chosen_blocking if BLOCKING_OPTIONS.get(c) in key_blocking]
    missed_blocking = key_blocking - {BLOCKING_OPTIONS.get(c) for c in chosen_blocking}
    blocking_points = (
        1.0 if not wrong_blocking and not missed_blocking and right_blocking else 0.0
    )
    add("Q10_blocking", blocking_points, 1, false=len(wrong_blocking),
        claims=len(chosen_blocking), abstain=0 if chosen_blocking or not key_blocking else 1)

    chosen_actions = _items(q10.get("actions"))
    key_actions = {KEY_TO_ACTION[a] for a in readiness["pending_actions"] if a in KEY_TO_ACTION}
    wrong_actions = [c for c in chosen_actions if ACTION_OPTIONS.get(c) not in key_actions]
    missed_actions = key_actions - {ACTION_OPTIONS.get(c) for c in chosen_actions}
    action_points = 1.0 if not wrong_actions and not missed_actions and chosen_actions else 0.0
    add("Q10_actions", action_points, 1, false=len(wrong_actions), claims=len(chosen_actions),
        abstain=0 if chosen_actions else 1)

    # Unknown options already count as wrong items above; they are also listed for the rater.
    for option in chosen_blocking:
        if option not in BLOCKING_OPTIONS:
            report["invalid"].append(f"Q10 blocking: unknown option {option!r}")
    for option in chosen_actions:
        if option not in ACTION_OPTIONS:
            report["invalid"].append(f"Q10 actions: unknown option {option!r}")
    report["q10_all_correct"] = bool(answer_ok and blocking_points == 1.0 and action_points == 1.0)

    total_points = sum(report["points"].values())
    total_possible = sum(report["possible"].values())
    report["score"] = round(total_points / total_possible, 4)
    report["error_rate"] = round(report["false_claims"] / report["claims"], 4) if report["claims"] else 0.0
    report["abstention_rate"] = round(report["abstentions"] / report["items"], 4) if report["items"] else 0.0
    return report


def main():
    keys = json.loads(open(sys.argv[1], encoding="utf-8").read())
    response = json.loads(open(sys.argv[2], encoding="utf-8").read())
    print(json.dumps(score(keys, response), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
