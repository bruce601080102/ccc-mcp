"""Offline checks for the internal pilot pack (two non-author testers, three blocks each).

No human participants and no model calls. These tests check the structure of the packets, that
testers receive no answer key or scoring material, that the frozen files are unchanged, and the
scorer contract (blank, 無, 是/否, invalid entries, the Q10 all-correct rule). The scorer tests use
the answer key itself as a sheet to check the scorer, not as pilot data.
"""

import importlib
import json
import sys
from pathlib import Path

import pytest

USERSTUDY = Path(__file__).resolve().parents[1] / "userstudy"
sys.path.insert(0, str(USERSTUDY))

build_pilot = importlib.import_module("build_pilot")
freeze = importlib.import_module("freeze")
score = importlib.import_module("score")

PILOT = USERSTUDY / "pilot" / build_pilot.VERSION
TESTERS = build_pilot.TESTERS
LEAK_TOKENS = (
    "E1_ready_to_merge", "S_grid_T3", "G1_current_features", "G2_search_implementation_file",
    "O1_out_of_plan_files", "I1_dependents_of_search", "blocking_reasons", "not_a_reason",
    "pending_actions", "干擾", "不存在", "COMMIT_REQUIRED", "PENDING_IDENTITY", "HOST_TEST_NOT_PASSED",
    "assignment", "rater_only", "RATER", "keys_case", "answer_key",
)


def _packet_text(tester):
    root = PILOT / f"tester_{tester}"
    return "".join(p.read_text(encoding="utf-8", errors="ignore") for p in root.rglob("*") if p.is_file())


def _keys(case):
    return json.loads((USERSTUDY / "materials" / case / "keys.json").read_text(encoding="utf-8"))


def _key_sheet(keys):
    """The answer key written as a response sheet. Used only to check that the scorer awards full marks."""
    grid = {
        feature: {"a": cells["a_uncommitted_change"], "b": cells["b_host_test_passed"], "d": cells["d_pending_mapping"]}
        for feature, cells in keys["S_grid_T3"].items()
    }
    return {
        "Q01": keys["G1_current_features"], "Q02": keys["G2_search_implementation_file"],
        "Q03": keys["T1_added"], "Q04": ["無"] if not keys["T1_modified"] else keys["T1_modified"],
        "Q05": keys["T2_added"], "Q06": keys["T2_modified"], "Q07": keys["T3_added"],
        "Q08": keys["T3_modified"], "Q09": grid,
        "Q10": {"answer": "不能", "blocking": ["超出計畫的檔案", "待確認的新舊對應"], "actions": ["需先 commit"]},
        "Q11": keys["O1_out_of_plan_files"], "Q12": keys["I1_dependents_of_search"],
    }


# ---------------------------------------------------------------- assignment and counterbalance
def test_each_tester_does_every_block_once_with_a_different_condition_per_block():
    for tester in TESTERS:
        conditions = [build_pilot.ASSIGNMENT[tester][b] for b in build_pilot.BLOCKS]
        assert sorted(conditions) == ["A", "B", "C"], (tester, conditions)


def test_assignment_matches_the_coordinator_table():
    assert build_pilot.ASSIGNMENT == {
        "P1": {"block1_notes": "A", "block2_bookmarks": "B", "block3_todos": "C"},
        "P2": {"block1_notes": "C", "block2_bookmarks": "B", "block3_todos": "A"},
    }


def test_no_tester_sees_the_same_case_twice():
    for tester in TESTERS:
        cases = [build_pilot.BLOCK_CASE[b] for b in build_pilot.BLOCKS]
        assert len(set(cases)) == len(cases)


def test_the_two_testers_get_different_conditions_on_the_cases_that_can_be_counterbalanced():
    # notes and todos are counterbalanced (A and C swap between testers); bookmarks is B for both,
    # which is the only shared block. This is the design the coordinator asked for, not full balance.
    for block in ("block1_notes", "block3_todos"):
        assert build_pilot.ASSIGNMENT["P1"][block] != build_pilot.ASSIGNMENT["P2"][block]
    assert build_pilot.ASSIGNMENT["P1"]["block2_bookmarks"] == build_pilot.ASSIGNMENT["P2"]["block2_bookmarks"]


def test_rater_assignment_file_agrees_with_the_build_script():
    table = json.loads((PILOT / "rater_only" / "assignment.json").read_text(encoding="utf-8"))
    for tester in TESTERS:
        for row in table[tester]:
            assert row["condition"] == build_pilot.ASSIGNMENT[tester][row["block"]]
            assert row["case"] == build_pilot.BLOCK_CASE[row["block"]]


# ---------------------------------------------------------------- packet contents
@pytest.mark.parametrize("tester", TESTERS)
def test_packet_material_is_byte_identical_to_the_source_for_its_condition(tester):
    for block in build_pilot.BLOCKS:
        condition = build_pilot.ASSIGNMENT[tester][block]
        case = build_pilot.BLOCK_CASE[block]
        source = USERSTUDY / "materials" / case
        target = PILOT / f"tester_{tester}" / "blocks" / block
        assert (target / "questions.md").read_bytes() == (source / "questions.md").read_bytes()
        if condition == "B":
            assert (target / "material" / "summary.md").read_bytes() == (source / "B_text.md").read_bytes()
            assert len(list((target / "material").iterdir())) == 1
        else:
            pages = sorted((source / condition).glob("*.html"))
            assert len(pages) == 3
            for page in pages:
                assert (target / "material" / page.name).read_bytes() == page.read_bytes()


@pytest.mark.parametrize("tester", TESTERS)
def test_tester_packet_contains_no_answer_key_or_scoring_material(tester):
    text = _packet_text(tester)
    for token in LEAK_TOKENS:
        assert token not in text, token


@pytest.mark.parametrize("tester", TESTERS)
def test_tester_packet_has_no_case_or_block_it_was_not_assigned(tester):
    root = PILOT / f"tester_{tester}"
    assert sorted(p.name for p in (root / "blocks").iterdir()) == sorted(build_pilot.BLOCKS)
    assert not (root / "rater_only").exists()
    assert not any(p.name.startswith("keys_") for p in root.rglob("*"))
    assert not any(p.name == "facts.json" for p in root.rglob("*"))


@pytest.mark.parametrize("tester", TESTERS)
def test_response_sheets_are_blank_and_carry_no_condition(tester):
    for block in build_pilot.BLOCKS:
        sheet = json.loads((PILOT / f"tester_{tester}" / "response_sheets" / f"{block}.json").read_text(encoding="utf-8"))
        assert sheet["tester"] == tester and sheet["block"] == block
        assert "condition" not in sheet
        assert all(sheet[f"Q{n:02d}"] is None for n in (1, 2, 3, 4, 5, 6, 7, 8, 11, 12))
        assert sheet["Q10"] == {"answer": None, "blocking": None, "actions": None}
        assert all(v is None for row in sheet["Q09"].values() for v in row.values())


def test_blank_response_sheet_scores_zero_with_no_claims():
    keys = _keys("case1_notes")
    sheet = build_pilot.response_template("P1", "block1_notes", keys)
    report = score.score(keys, sheet)
    assert report["score"] == 0.0
    assert report["claims"] == 0 and report["false_claims"] == 0


# ---------------------------------------------------------------- freeze
def test_freeze_manifest_matches_current_files():
    assert freeze.check() == []


def test_freeze_scope_covers_packets_rater_files_materials_and_scripts():
    recorded = json.loads((PILOT / "FREEZE_MANIFEST.json").read_text(encoding="utf-8"))["files"]
    assert any(k.startswith("pilot/pilot-v1.2/tester_P1/") for k in recorded)
    assert any(k.startswith("pilot/pilot-v1.2/tester_P2/") for k in recorded)
    assert any(k.startswith("pilot/pilot-v1.2/rater_only/") for k in recorded)
    assert any(k.startswith("materials/case3_todos/") for k in recorded)
    for script in ("score.py", "build_pilot.py", "freeze.py", "completion.py", "aggregate.py"):
        assert script in recorded


@pytest.mark.parametrize("case", sorted(build_pilot.BLOCK_CASE.values()))
def test_question_sheet_wording_matches_the_scorer_contract(case):
    """The option strings and grid rows a tester sees must be the ones score.py reads."""
    text = (USERSTUDY / "materials" / case / "questions.md").read_text(encoding="utf-8")
    for option in list(score.BLOCKING_OPTIONS) + list(score.ACTION_OPTIONS):
        assert option in text, option
    for feature in _keys(case)["S_grid_T3"]:
        assert feature in text, feature
    assert "是" in text and "否" in text and "不確定" in text


# ---------------------------------------------------------------- scorer contract
def test_the_answer_key_as_a_sheet_scores_full_marks_for_every_case():
    for case in build_pilot.BLOCK_CASE.values():
        keys = _keys(case)
        report = score.score(keys, _key_sheet(keys))
        assert report["score"] == 1.0, (case, report["points"])
        assert report["false_claims"] == 0
        assert report["q10_all_correct"] is True
        assert report["q09_accuracy"] == 1.0


def test_blank_set_question_is_not_full_marks_even_when_the_key_is_empty():
    keys = _keys("case1_notes")
    sheet = _key_sheet(keys)
    sheet["Q04"] = None  # blank: not answered
    report = score.score(keys, sheet)
    assert report["points"]["Q04"] == 0.0
    assert report["abstentions"] >= 1


def test_explicit_none_is_an_answer_and_is_false_when_the_key_has_items():
    keys = _keys("case1_notes")
    sheet = _key_sheet(keys)
    sheet["Q03"] = ["無"]
    report = score.score(keys, sheet)
    assert report["points"]["Q03"] == 0.0
    assert report["false_claims"] >= 1


def test_chinese_and_json_yes_no_values_are_both_read():
    keys = _keys("case1_notes")
    sheet = _key_sheet(keys)
    for feature, cells in keys["S_grid_T3"].items():
        sheet["Q09"][feature] = {
            "a": "是" if cells["a_uncommitted_change"] else "否",
            "b": "true" if cells["b_host_test_passed"] else "false",
            "d": cells["d_pending_mapping"],
        }
    report = score.score(keys, sheet)
    assert report["points"]["Q09"] == 21.0
    assert report["invalid"] == []


def test_unrecognised_grid_text_is_listed_as_invalid_and_earns_nothing():
    keys = _keys("case1_notes")
    sheet = _key_sheet(keys)
    feature = next(iter(keys["S_grid_T3"]))
    sheet["Q09"][feature]["a"] = "yes"
    report = score.score(keys, sheet)
    assert any("yes" in item for item in report["invalid"])
    assert report["points"]["Q09"] == 20.0
    assert report["false_claims"] == 0


def test_distractor_row_is_all_no_in_every_case():
    for case in build_pilot.BLOCK_CASE.values():
        rows = [v for k, v in _keys(case)["S_grid_T3"].items() if k.startswith("匯出 CSV")]
        assert len(rows) == 1
        assert all(value is False for value in rows[0].values())


def test_q10_all_correct_requires_answer_blocking_and_actions_together():
    keys = _keys("case1_notes")
    sheet = _key_sheet(keys)
    sheet["Q10"]["actions"] = ["不需要任何動作"]
    report = score.score(keys, sheet)
    assert report["q10_all_correct"] is False
    assert report["points"]["Q10_answer"] == 1.0
    assert report["points"]["Q10_blocking"] == 1.0


def test_unknown_q10_option_is_listed_and_counts_as_a_wrong_item():
    keys = _keys("case1_notes")
    sheet = _key_sheet(keys)
    sheet["Q10"]["blocking"] = ["超出計畫的檔案", "待確認的新舊對應", "主機測試通過"]
    report = score.score(keys, sheet)
    assert any("主機測試通過" in item for item in report["invalid"])
    assert report["points"]["Q10_blocking"] == 0.0
    assert report["false_claims"] >= 1
