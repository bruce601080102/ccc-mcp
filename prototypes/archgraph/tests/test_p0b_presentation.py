"""P0B presentation and study-material checks (offline, no model calls, no human participants).

Covers: the approval gate definition, pending identity traceability, project-level warnings for
unattributed out-of-plan files, history carried across Tasks, the rendered page, the scoring
rules, and the study checks (parity, keys against git, fairness, pilot packet hygiene).
"""

import importlib
import json
import sys
from pathlib import Path

import pytest
from archgraph.gate import approval_gate
from archgraph.pipeline import run_pipeline
from run_demo import PLANS, run_demo

USERSTUDY = Path(__file__).resolve().parents[1] / "userstudy"
sys.path.insert(0, str(USERSTUDY))


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    out = tmp_path_factory.mktemp("p0b-demo")
    run_demo(out)
    return out


def _load(out, task, name="architecture.json"):
    return json.loads((out / task / name).read_text(encoding="utf-8"))


# ---------------------------------------------------------------- gate
def _delta(**overrides):
    base = {
        "commit_state": "COMMITTED",
        "host_test_state": "PASSED",
        "out_of_plan_files": [],
        "pending_confirmations": [],
        "missing_planned": [],
    }
    base.update(overrides)
    return base


def test_uncommitted_alone_is_pending_action_not_a_blocker():
    gate = approval_gate(_delta(commit_state="UNCOMMITTED"))
    assert gate["status"] == "NEEDS_COMMIT"
    assert gate["blockers"] == []
    assert [a["code"] for a in gate["actions"]] == ["COMMIT_REQUIRED"]


def test_committed_clean_task_is_ready():
    gate = approval_gate(_delta())
    assert gate["status"] == "READY" and gate["blockers"] == [] and gate["actions"] == []


@pytest.mark.parametrize(
    "overrides, code",
    [
        ({"host_test_state": "FAILED"}, "HOST_TEST_NOT_PASSED"),
        ({"out_of_plan_files": ["a.py"]}, "OUT_OF_PLAN"),
        ({"pending_confirmations": [{"from_key": "a::x", "to_key": "b::y"}]}, "PENDING_IDENTITY"),
        ({"missing_planned": [{"key": "a::x"}]}, "PLANNED_NOT_BUILT"),
    ],
)
def test_each_fact_that_a_commit_would_not_fix_blocks(overrides, code):
    gate = approval_gate(_delta(**overrides))
    assert gate["status"] == "BLOCKED"
    assert code in {b["code"] for b in gate["blockers"]}


def test_blocked_and_uncommitted_are_reported_separately():
    gate = approval_gate(_delta(commit_state="UNCOMMITTED", out_of_plan_files=["a.py"]))
    assert gate["status"] == "BLOCKED"
    assert "COMMIT_REQUIRED" not in {b["code"] for b in gate["blockers"]}
    assert [a["code"] for a in gate["actions"]] == ["COMMIT_REQUIRED"]


def test_demo_gate_verdicts_match_the_facts(demo):
    assert _load(demo, "TASK-0002")["alerts"]["gate"]["status"] == "READY"
    blockers_1 = {b["code"] for b in _load(demo, "TASK-0001")["alerts"]["gate"]["blockers"]}
    assert blockers_1 == {"OUT_OF_PLAN"}  # notes/util.py, committed and tested
    gate3 = _load(demo, "TASK-0003")["alerts"]["gate"]
    assert gate3["status"] == "BLOCKED"
    assert {b["code"] for b in gate3["blockers"]} == {"OUT_OF_PLAN", "PENDING_IDENTITY"}
    assert [a["code"] for a in gate3["actions"]] == ["COMMIT_REQUIRED"]


# ---------------------------------------------------------------- pending traceability
def test_pending_pair_is_listed_with_both_keys_and_reachable_from_both_nodes(demo):
    arch = _load(demo, "TASK-0003")
    pairs = arch["alerts"]["pending_pairs"]
    assert len(pairs) == 1
    assert pairs[0]["from_key"] == "notes/storage.py::NotesStore.__init__"
    assert pairs[0]["to_key"] == "notes/migrate.py::JsonImporter.__init__"
    for path in ("notes/migrate.py", "notes/storage.py"):
        linked = arch["files"][path]["pending_pairs"]
        assert [(p["from_key"], p["to_key"]) for p in linked] == [(pairs[0]["from_key"], pairs[0]["to_key"])]
    assert pairs[0]["reasons"] == ["上層類別的對應無法唯一確定"]


def test_pending_pair_is_not_counted_as_an_actual_match(demo):
    summary = _load(demo, "TASK-0003")["edges_summary"]
    assert not any("NotesStore" in edge or "JsonImporter" in edge for edge in summary["matched"])


# ---------------------------------------------------------------- out-of-plan warnings
def test_unattributed_out_of_plan_file_gets_a_project_level_warning(demo):
    arch = _load(demo, "TASK-0003")
    # schema.sql belongs to no planned feature, so it cannot be attributed.
    assert arch["alerts"]["unattributed_out_of_plan"] == ["notes/schema.sql"]
    # models.py is a member of the tags feature, so it is attributed and not in the banner.
    assert "notes/models.py" not in arch["alerts"]["unattributed_out_of_plan"]
    assert arch["files"]["notes/models.py"]["features"] == ["標籤功能"]


def test_task_one_out_of_plan_file_is_warned_as_unattributed(demo):
    assert _load(demo, "TASK-0001")["alerts"]["unattributed_out_of_plan"] == ["notes/util.py"]


def test_warning_is_rendered_not_hidden(demo):
    page = (demo / "TASK-0003" / "architecture.html").read_text(encoding="utf-8")
    assert "專案層級警告" in page
    assert 'data-focus="notes/schema.sql"' in page


# ---------------------------------------------------------------- history
def test_history_carries_every_earlier_task_for_each_file(tmp_path):
    refs = _demo_refs(tmp_path)
    out1 = tmp_path / "t1"
    out2 = tmp_path / "t2"
    out3 = tmp_path / "t3"
    db = tmp_path / "db.sqlite"
    run_pipeline(refs["repo"], ["empty", f"commit:{refs['C1']}"], db, out1,
                 plan_source=PLANS / "plan1.json")
    arch1 = json.loads((out1 / "architecture.json").read_text(encoding="utf-8"))
    run_pipeline(refs["repo"], ["empty", f"commit:{refs['C1']}", f"commit:{refs['C2']}"], db, out2,
                 plan_source=PLANS / "plan2.json", prior_architectures=[arch1])
    arch2 = json.loads((out2 / "architecture.json").read_text(encoding="utf-8"))
    confirmations = json.loads(
        (Path(__file__).resolve().parents[1] / "fixtures" / "notes_cli" / "confirmations3.json").read_text(
            encoding="utf-8"
        )
    )
    run_pipeline(refs["repo"], ["empty", f"commit:{refs['C1']}", f"commit:{refs['C2']}", "worktree"], db, out3,
                 plan_source=PLANS / "plan3.json", prior_architectures=[arch1, arch2],
                 confirmations=confirmations)
    arch3 = json.loads((out3 / "architecture.json").read_text(encoding="utf-8"))
    assert [t["task"] for t in arch3["prior_tasks"]] == ["TASK-0001", "TASK-0002"]
    history = arch3["files"]["notes/cli.py"]["history"]
    assert [h["task"] for h in history] == ["TASK-0001", "TASK-0002"]
    assert history[0]["state"] == "ADDED"


def _demo_refs(tmp_path):
    from make_notes_cli import build

    return build(tmp_path / "notes-cli")


def test_without_prior_tasks_history_is_empty_not_missing(demo):
    arch = _load(demo, "TASK-0001")
    assert arch["prior_tasks"] == []
    assert arch["files"]["notes/cli.py"]["history"] == []


# ---------------------------------------------------------------- planned versus actual
def test_planned_and_actual_are_labelled_as_different_views(demo):
    page = (demo / "TASK-0003" / "architecture.html").read_text(encoding="utf-8")
    assert "功能架構（實際，新手）" in page and "計畫（Planned）" in page
    assert "這裡是實際程式狀態" in page


# ---------------------------------------------------------------- scoring rules
@pytest.fixture(scope="module")
def case1_keys():
    return json.loads((USERSTUDY / "materials" / "case1_notes" / "keys.json").read_text(encoding="utf-8"))


def _perfect(keys):
    return {
        "Q01": keys["G1_current_features"],
        "Q02": keys["G2_search_implementation_file"],
        "Q03": keys["T1_added"],
        "Q04": [],
        "Q05": keys["T2_added"],
        "Q06": keys["T2_modified"],
        "Q07": keys["T3_added"],
        "Q08": keys["T3_modified"],
        "Q09": {f: {"a": a["a_uncommitted_change"], "b": a["b_host_test_passed"], "d": a["d_pending_mapping"]}
                for f, a in keys["S_grid_T3"].items()},
        "Q10": {"answer": "不能", "blocking": ["超出計畫的檔案", "待確認的新舊對應"], "actions": ["需先 commit"]},
        "Q11": keys["O1_out_of_plan_files"],
        "Q12": keys["I1_dependents_of_search"],
    }


def test_perfect_sheet_scores_one_with_no_false_claims(case1_keys):
    import score

    report = score.score(case1_keys, _perfect(case1_keys))
    assert report["score"] == 1.0
    assert report["false_claims"] == 0


def test_answering_yes_to_every_grid_cell_is_penalised(case1_keys):
    import score

    response = _perfect(case1_keys)
    response["Q09"] = {f: {"a": True, "b": True, "d": True} for f in case1_keys["S_grid_T3"]}
    report = score.score(case1_keys, response)
    assert report["points"]["Q09"] < report["possible"]["Q09"]
    assert report["false_claims"] > 0


def test_uncommitted_as_a_blocker_is_a_false_claim(case1_keys):
    import score

    response = _perfect(case1_keys)
    response["Q10"]["blocking"] = ["超出計畫的檔案", "待確認的新舊對應", "尚未 commit"]
    report = score.score(case1_keys, response)
    assert report["points"]["Q10_blocking"] == 0.0
    assert report["false_claims"] >= 1


def test_tests_passing_is_not_a_reason_to_merge(case1_keys):
    import score

    response = _perfect(case1_keys)
    response["Q10"] = {"answer": "能", "blocking": [], "actions": ["不需要任何動作"]}
    report = score.score(case1_keys, response)
    assert report["points"]["Q10_answer"] == 0.0
    assert report["points"]["Q10_actions"] == 0.0


def test_unanswered_items_are_abstentions_not_false_claims(case1_keys):
    import score

    report = score.score(case1_keys, {})
    assert report["false_claims"] == 0
    assert report["abstentions"] > 0
    assert report["score"] < 0.2


def test_explicit_uncertainty_counts_as_abstention(case1_keys):
    import score

    response = _perfect(case1_keys)
    response["Q09"] = {f: {"a": "不確定", "b": "不確定", "d": "不確定"} for f in case1_keys["S_grid_T3"]}
    report = score.score(case1_keys, response)
    assert report["false_claims"] == 0
    assert report["abstentions"] >= len(case1_keys["S_grid_T3"]) * 3


# ---------------------------------------------------------------- study checks
@pytest.fixture(scope="module")
def study_materials(tmp_path_factory, monkeypatch_module):
    """Build the three cases into a temp folder and point the study scripts at it."""
    build_cases = importlib.import_module("build_cases")
    build_materials = importlib.import_module("build_materials")
    scratch = tmp_path_factory.mktemp("p0b-cases")
    for case_id in build_cases.CASES:
        build_cases.run_case(case_id, scratch)
    materials = tmp_path_factory.mktemp("p0b-materials")
    monkeypatch_module.setattr(build_materials, "MATERIALS", materials)
    for case in build_materials.CASES:
        build_materials.export_case(case, scratch, materials)
    B_SOURCE = USERSTUDY / "materials"
    for case in build_materials.CASES:
        (materials / case).joinpath("B_text.md").write_text(
            (B_SOURCE / case / "B_text.md").read_text(encoding="utf-8"), encoding="utf-8"
        )
    return {"scratch": scratch, "materials": materials}


@pytest.fixture(scope="module")
def monkeypatch_module():
    from _pytest.monkeypatch import MonkeyPatch

    mp = MonkeyPatch()
    yield mp
    mp.undo()


def test_parity_holds_for_every_case(study_materials, monkeypatch_module):
    parity = importlib.import_module("parity_check")
    monkeypatch_module.setattr(parity, "MATERIALS", study_materials["materials"])
    for case in parity.CASES:
        conds = parity.conditions(study_materials["materials"] / case)
        for label, needles in parity.required_facts(study_materials["materials"] / case).items():
            for name, body in conds.items():
                assert needles[name] in body, f"{case} {name} missing {label}"


def test_answer_keys_agree_with_git_plan_import_and_host_test(study_materials, monkeypatch_module):
    verify = importlib.import_module("verify_keys")
    monkeypatch_module.setattr(verify, "MATERIALS", study_materials["materials"])
    for case in verify.CASES:
        results = verify.verify_case(case, study_materials["scratch"], run_tests=False)
        failed = [name for name, ok, _ in results if not ok]
        assert failed == [], f"{case}: {failed}"


def test_fairness_holds_for_every_case(study_materials, monkeypatch_module):
    fairness = importlib.import_module("fairness_check")
    monkeypatch_module.setattr(fairness, "MATERIALS", study_materials["materials"])
    assert len({fairness.normalized(c) for c in fairness.CASES}) == 1
    for case in fairness.CASES:
        problems, _ = fairness.findability(case)
        assert problems == [], problems


# Tester-packet, assignment, freeze and scorer-contract checks live in test_p0b_pilot_pack.py.
