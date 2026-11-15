"""T07 checks for pilot-v1.1: scorer metrics, completion definition, aggregation, B source, v1 archive.

Offline, no human participants, no model calls. The sheets built in these tests are TOOL FIXTURES
generated from the answer keys (or deliberately broken) in pytest temporary folders. They check
that the scorer and the aggregation run. They are not tester data and are never written into pilot/.
"""

import csv
import hashlib
import importlib
import json
import sys
from pathlib import Path

USERSTUDY = Path(__file__).resolve().parents[1] / "userstudy"
sys.path.insert(0, str(USERSTUDY))

build_pilot = importlib.import_module("build_pilot")
completion = importlib.import_module("completion")
aggregate = importlib.import_module("aggregate")
score = importlib.import_module("score")

PILOT = USERSTUDY / "pilot"
ARCHIVE = PILOT / "archive" / "pilot-v1"


def _keys(case):
    return json.loads((USERSTUDY / "materials" / case / "keys.json").read_text(encoding="utf-8"))


def _key_sheet(keys):
    grid = {
        f: {"a": c["a_uncommitted_change"], "b": c["b_host_test_passed"], "d": c["d_pending_mapping"]}
        for f, c in keys["S_grid_T3"].items()
    }
    return {
        "Q01": keys["G1_current_features"], "Q02": keys["G2_search_implementation_file"],
        "Q03": keys["T1_added"], "Q04": ["無"], "Q05": keys["T2_added"], "Q06": keys["T2_modified"],
        "Q07": keys["T3_added"], "Q08": keys["T3_modified"], "Q09": grid,
        "Q10": {"answer": "不能", "blocking": ["超出計畫的檔案", "待確認的新舊對應"], "actions": ["需先 commit"]},
        "Q11": keys["O1_out_of_plan_files"], "Q12": keys["I1_dependents_of_search"],
    }


# ---------------------------------------------------------------- scorer: Q10 metrics
def test_q10_error_subscores_count_blank_and_invalid_but_not_abstention():
    keys = _keys("case1_notes")
    sheet = _key_sheet(keys)
    sheet["Q10"] = {"answer": None, "blocking": None, "actions": None}  # all blank
    report = score.score(keys, sheet)
    assert report["q10_error_subscores"] == 3
    assert report["q10_subscore_total"] == 3

    sheet["Q10"] = {"answer": "不確定", "blocking": ["不確定"], "actions": ["不確定"]}  # all abstain
    report = score.score(keys, sheet)
    assert report["q10_error_subscores"] == 0
    assert report["q10_all_correct"] is False


def test_q10_invalid_answer_text_is_an_error_subscore():
    keys = _keys("case1_notes")
    sheet = _key_sheet(keys)
    sheet["Q10"]["answer"] = "可能吧"
    report = score.score(keys, sheet)
    assert report["q10_states"]["answer"] == "invalid"
    assert report["q10_error_subscores"] == 1


def test_q10_all_correct_needs_all_three_and_the_perfect_key_has_zero_errors():
    keys = _keys("case1_notes")
    report = score.score(keys, _key_sheet(keys))
    assert report["q10_all_correct"] is True
    assert report["q10_error_subscores"] == 0
    sheet = _key_sheet(keys)
    sheet["Q10"]["actions"] = ["不需要任何動作"]  # answer and blocking right, action wrong
    report = score.score(keys, sheet)
    assert report["q10_error_subscores"] == 1
    assert report["q10_all_correct"] is False


def test_dangerous_merge_is_answering_能_while_a_blocker_is_present():
    keys = _keys("case1_notes")
    assert keys["E1_ready_to_merge"]["blocking_reasons"]  # the case has unresolved blockers
    sheet = _key_sheet(keys)
    sheet["Q10"]["answer"] = "能"
    report = score.score(keys, sheet)
    assert report["q10_blocker_present"] is True
    assert report["q10_dangerous_merge"] is True


def test_blank_or_abstaining_answers_are_not_dangerous_merges():
    keys = _keys("case1_notes")
    for answer in (None, "不確定"):
        sheet = _key_sheet(keys)
        sheet["Q10"]["answer"] = answer
        assert score.score(keys, sheet)["q10_dangerous_merge"] is False


def test_explicit_none_for_q10_blocking_is_blank_and_an_error():
    keys = _keys("case1_notes")
    sheet = _key_sheet(keys)
    sheet["Q10"]["blocking"] = ["無"]
    report = score.score(keys, sheet)
    assert report["q10_states"]["blocking"] == "blank"
    assert report["q10_error_subscores"] == 1


# ---------------------------------------------------------------- completion (T07 definition)
TESTER = "P1"
BLOCKS = build_pilot.BLOCKS


def _returned(tmp_path, *, tester=TESTER, drop=None, times=("09:00:00", "09:12:30"), helps=0, blank=False):
    """A returned folder. Built from blank sheets (or the key with blanks) in a temp dir: tool fixture."""
    root = tmp_path / tester
    (root / "response_sheets").mkdir(parents=True)
    for block in BLOCKS:
        if block == drop:
            continue
        keys = _keys(build_pilot.BLOCK_CASE[block])
        sheet = build_pilot.response_template(tester, block, keys)
        sheet["start_time"], sheet["end_time"] = times
        sheet["questions_asked_for_help"] = helps
        sheet["notes"] = ""
        if not blank:
            sheet = {**_key_sheet(keys), **{k: sheet[k] for k in ("tester", "block", "start_time", "end_time",
                                                                  "questions_asked_for_help", "notes")}}
        (root / "response_sheets" / f"{block}.json").write_text(json.dumps(sheet, ensure_ascii=False), encoding="utf-8")
    with (root / "TIMING_AND_ERROR_LOG.csv").open("w", encoding="utf-8", newline="") as handle:
        csv.writer(handle).writerow(completion.LOG_HEADER)
    return root


def test_complete_when_all_three_sheets_and_the_log_exist_even_with_blanks(tmp_path):
    root = _returned(tmp_path, blank=True)
    assert completion.check(root, TESTER) == []


def test_incomplete_when_a_block_sheet_is_missing(tmp_path):
    root = _returned(tmp_path, drop="block2_bookmarks")
    problems = completion.check(root, TESTER)
    assert any("missing response sheet: block2_bookmarks" in p for p in problems)


def test_incomplete_when_times_or_help_count_are_not_recorded(tmp_path):
    root = _returned(tmp_path, times=("9:00", "09:12:30"), helps=None)
    problems = completion.check(root, TESTER)
    assert any("start_time" in p for p in problems)
    assert any("questions_asked_for_help" in p for p in problems)


def test_incomplete_when_the_timing_log_header_was_changed(tmp_path):
    root = _returned(tmp_path)
    (root / "TIMING_AND_ERROR_LOG.csv").write_text("tester,case\n", encoding="utf-8")
    assert any("header" in p for p in completion.check(root, TESTER))


def test_wrong_tester_code_in_a_sheet_is_reported(tmp_path):
    root = _returned(tmp_path)
    assert any("expected 'P2'" in p for p in completion.check(root, "P2"))


# ---------------------------------------------------------------- aggregation on tool fixtures
def test_aggregate_scores_both_testers_with_conditions_from_the_assignment(tmp_path):
    collect = tmp_path / "collect"
    _returned(collect, tester="P1")
    _returned(collect, tester="P2")
    rows = aggregate.score_returned(collect)
    assert len(rows) == 6
    conditions = {(r["tester"], r["block"]): r["condition"] for r in rows}
    assert conditions[("P1", "block1_notes")] == "A"
    assert conditions[("P2", "block1_notes")] == "C"
    assert conditions[("P2", "block3_todos")] == "A"
    assert all(r["completed"] for r in rows)
    summary = aggregate.summarise(rows)
    assert set(summary) == {"A", "B", "C"}


def test_aggregate_skips_incomplete_testers_unless_asked(tmp_path):
    collect = tmp_path / "collect"
    _returned(collect, tester="P1", drop="block3_todos")
    assert aggregate.score_returned(collect) == []
    assert len(aggregate.score_returned(collect, include_incomplete=True)) == 2


# ---------------------------------------------------------------- B source and v1 archive
def test_b_source_says_claude_wrote_a_fixed_text_baseline_from_the_full_fact_table():
    for case in ("case1_notes", "case2_bookmarks", "case3_todos"):
        head = (USERSTUDY / "materials" / case / "B_text.md").read_text(encoding="utf-8").split("\n", 3)[0:3]
        text = "\n".join(head)
        assert "固定文字基線" in text
        assert "Claude 根據完整事實表" in text
        assert "不是獨立 AI 摘要工具的輸出" in text


def test_b_body_is_unchanged_from_v1_apart_from_the_source_note():
    for case in ("case1_notes", "case2_bookmarks", "case3_todos"):
        old = (ARCHIVE / "materials" / case / "B_text.md").read_text(encoding="utf-8").splitlines()
        new = (USERSTUDY / "materials" / case / "B_text.md").read_text(encoding="utf-8").splitlines()
        assert old[3:] == new[3:], case  # only the title and the source note (first three lines) changed


def test_v1_archive_matches_the_frozen_v1_manifest():
    manifest = json.loads((PILOT / "FREEZE_MANIFEST.json").read_text(encoding="utf-8"))["files"]
    archived = {
        "score.py": ARCHIVE / "scripts" / "score.py",
        "build_pilot.py": ARCHIVE / "scripts" / "build_pilot.py",
        "freeze.py": ARCHIVE / "scripts" / "freeze.py",
    }
    for case in ("case1_notes", "case2_bookmarks", "case3_todos"):
        archived[f"materials/{case}/B_text.md"] = ARCHIVE / "materials" / case / "B_text.md"
    for rel, path in archived.items():
        assert hashlib.sha256(path.read_bytes()).hexdigest() == manifest[rel], rel


def test_pilot_v1_packets_are_untouched_by_v11():
    manifest = json.loads((PILOT / "FREEZE_MANIFEST.json").read_text(encoding="utf-8"))["files"]
    for rel in manifest:
        if rel.startswith("pilot/tester_P") or rel.startswith("pilot/rater_only"):
            assert hashlib.sha256((USERSTUDY / rel).read_bytes()).hexdigest() == manifest[rel], rel


def test_version_is_v12_and_its_packets_are_separate_from_v1_and_v11():
    assert build_pilot.VERSION == "pilot-v1.2"
    for version in ("pilot-v1.1", "pilot-v1.2"):
        assert (PILOT / version / "tester_P1").is_dir()
        assert (PILOT / version / "tester_P2").is_dir()


def test_v12_tester_packets_are_byte_identical_to_v11():
    for folder in ("tester_P1", "tester_P2"):
        old = sorted((PILOT / "pilot-v1.1" / folder).rglob("*"))
        new = sorted((PILOT / "pilot-v1.2" / folder).rglob("*"))
        assert [p.relative_to(PILOT / "pilot-v1.1") for p in old] == [p.relative_to(PILOT / "pilot-v1.2") for p in new]
        for a, b in zip(old, new):
            if a.is_file():
                assert a.read_bytes() == b.read_bytes(), a.name


def test_v11_scripts_are_archived_and_match_the_v11_manifest():
    manifest = json.loads((PILOT / "pilot-v1.1" / "FREEZE_MANIFEST.json").read_text(encoding="utf-8"))["files"]
    for name in ("score.py", "build_pilot.py", "freeze.py", "completion.py", "aggregate.py"):
        archived = PILOT / "archive" / "pilot-v1.1" / "scripts" / name
        assert hashlib.sha256(archived.read_bytes()).hexdigest() == manifest[name], name


# ---------------------------------------------------------------- v1.2 rules: abstention and three dangerous rates
def _answer_sheet(keys, answer):
    sheet = _key_sheet(keys)
    sheet["Q10"]["answer"] = answer
    return sheet


def test_q10_abstain_scores_zero_but_is_neither_an_error_nor_a_false_claim():
    keys = _keys("case1_notes")
    sheet = _key_sheet(keys)
    sheet["Q10"] = {"answer": "不確定", "blocking": ["不確定"], "actions": ["不確定"]}
    report = score.score(keys, sheet)
    assert report["points"]["Q10_answer"] == 0.0
    assert report["points"]["Q10_blocking"] == 0.0
    assert report["points"]["Q10_actions"] == 0.0
    assert report["false_claims"] == 0
    assert report["q10_error_subscores"] == 0
    assert report["q10_abstain_subscores"] == 3


def test_three_dangerous_rates_are_computed_from_the_same_rows():
    rows = [
        {"condition": "A", "blocker_present": 1, "dangerous_merge": 1, "explicit_decision": 1, "answer_abstain": 0,
         "score": 0.5, "q10_error_rate": 0.0, "q10_abstain_rate": 0.0, "seconds": 60, "help_count": 0},
        {"condition": "A", "blocker_present": 1, "dangerous_merge": 0, "explicit_decision": 1, "answer_abstain": 0,
         "score": 0.5, "q10_error_rate": 0.0, "q10_abstain_rate": 0.0, "seconds": 60, "help_count": 0},
        {"condition": "A", "blocker_present": 1, "dangerous_merge": 0, "explicit_decision": 0, "answer_abstain": 1,
         "score": 0.5, "q10_error_rate": 0.0, "q10_abstain_rate": 1.0, "seconds": 60, "help_count": 0},
        {"condition": "A", "blocker_present": 1, "dangerous_merge": 0, "explicit_decision": 0, "answer_abstain": 0,
         "score": 0.5, "q10_error_rate": 1.0, "q10_abstain_rate": 0.0, "seconds": 60, "help_count": 0},
    ]
    summary = aggregate.summarise(rows)["A"]
    assert summary["dangerous_all"] == 0.25        # 1 of 4 Q10 items with a blocker
    assert summary["dangerous_explicit"] == 0.5    # 1 of 2 explicit decisions
    assert summary["dangerous_abstain_rate"] == 0.25
    assert summary["q10_abstain_rate"] == 0.25     # mean of the row-level abstain rates
    assert summary["answer_abstain_rate"] == 0.25


def test_completion_rejects_a_sheet_missing_fields_or_with_end_before_start(tmp_path):
    root = _returned(tmp_path)
    sheet_path = root / "response_sheets" / "block1_notes.json"
    sheet = json.loads(sheet_path.read_text(encoding="utf-8"))
    del sheet["Q09"]
    sheet["start_time"], sheet["end_time"] = "10:00:00", "09:00:00"
    sheet_path.write_text(json.dumps(sheet, ensure_ascii=False), encoding="utf-8")
    problems = completion.check(root, TESTER)
    assert any("missing fields" in p and "Q09" in p for p in problems)
    assert any("earlier than start_time" in p for p in problems)


def test_blank_answers_still_complete_and_score_zero_under_v12(tmp_path):
    root = _returned(tmp_path, blank=True)
    assert completion.check(root, TESTER) == []
    keys = _keys("case1_notes")
    sheet = json.loads((root / "response_sheets" / "block1_notes.json").read_text(encoding="utf-8"))
    report = score.score(keys, sheet)
    assert report["score"] == 0.0
    assert report["false_claims"] == 0


# ---------------------------------------------------------------- T08-B: diagram consistency (verify_diagrams)
def test_verify_diagrams_passes_on_a_fresh_case_and_catches_a_corrupted_view(tmp_path):
    """Builds case1 in a temp folder (no model, no ccc-mcp checkout changes), then checks TASK-0003
    twice: once as built, once with a corrupted gate and an extra planned node. The second run must fail."""
    import shutil

    build_cases = importlib.import_module("build_cases")
    verify_diagrams = importlib.import_module("verify_diagrams")
    scratch = tmp_path / "cases"
    build_cases.run_case("case1_notes", scratch)
    materials = USERSTUDY / "materials" / "case1_notes"
    results = []
    verify_diagrams.check_task(scratch, "case1_notes", "TASK-0003", results)
    assert [r["check"] for r in results if not r["ok"]] == []

    mutated = tmp_path / "mutated"
    shutil.copytree(scratch, mutated)
    path = mutated / "case1_notes" / "TASK-0003" / "architecture.json"
    arch = json.loads(path.read_text(encoding="utf-8"))
    arch["header"]["gate"]["status"] = "READY"
    arch["tabs"]["planned"]["nodes"].append("notes/fake.py")
    path.write_text(json.dumps(arch, ensure_ascii=False), encoding="utf-8")
    results = []
    verify_diagrams.check_task(mutated, "case1_notes", "TASK-0003", results)
    failed = {r["check"] for r in results if not r["ok"]}
    assert "A gate status equals recomputed approval gate" in failed
    assert "B planned tab nodes equal the approved plan files" in failed
    assert materials.exists()
