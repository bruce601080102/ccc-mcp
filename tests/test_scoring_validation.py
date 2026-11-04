"""Scoring-accuracy harness: compares the evidence-weighted score with ground truth
(SWE-bench style resolved / unresolved labels). Pure data in, numbers out."""
import json

import pytest
from typer.testing import CliRunner

from ccc_mcp.evaluation_engine import validation
from ccc_mcp.infra.errors import BridgeError
from ccc_mcp.interfaces.cli import app

SRC_DIFF = "diff --git a/pkg/mod.py b/pkg/mod.py\n--- a/pkg/mod.py\n+++ b/pkg/mod.py\n@@ -1 +1 @@\n-x = 1\n+x = 2\n"
NEW_TEST_DIFF = (
    SRC_DIFF
    + "diff --git a/tests/test_mod.py b/tests/test_mod.py\nnew file mode 100644\n"
    "--- /dev/null\n+++ b/tests/test_mod.py\n@@ -0,0 +1,2 @@\n+def test_x():\n+    assert True\n"
)


def case(instance_id, resolved, stdout, passed, diff=SRC_DIFF, **extra):
    return {
        "instance_id": instance_id,
        "resolved": resolved,
        "diff": diff,
        "test_result": {"executed_by_host": True, "passed": passed, "stdout": stdout},
        "report": {"summary": "done", "next_action": "", "findings": []},
        **extra,
    }


def test_auroc_ranks_and_handles_ties_and_empty_classes():
    assert validation.auroc([90, 80], [10, 20]) == 1.0
    assert validation.auroc([10], [90]) == 0.0
    assert validation.auroc([50], [50]) == 0.5
    assert validation.auroc([], [50]) is None


def test_scores_follow_host_test_results_and_separate_the_classes():
    cases = [
        case("good-1", True, "10 passed in 1s", True),
        case("good-2", True, "4 passed in 1s", True),
        case("bad-1", False, "3 passed, 7 failed in 1s", False),
        case("bad-2", False, "1 passed, 9 failed in 1s", False),
    ]
    summary = validation.validate(cases)
    assert summary["auroc"] == 1.0
    assert summary["mean_score_resolved"] > summary["mean_score_unresolved"]
    assert summary["false_green"] == []
    assert "too few" in summary["verdict"]


def test_green_local_tests_that_still_fail_the_hidden_tests_are_reported_as_false_green():
    # The astropy__astropy-13398 shape: the agent's own new test passes, the real fix is wrong.
    # Self-written green tests are capped at 70 on the test dimension, which lowers the score
    # but not far enough to withhold PASSED; the harness must keep reporting that honestly.
    full = case(
        "astropy__astropy-13398", False, "1 passed in 0.1s", True, diff=NEW_TEST_DIFF,
        acceptance=["change x"], static_findings=[],
        report={"summary": "change x done", "next_action": "", "findings": []},
    )
    summary = validation.validate([full])
    assert summary["false_green"] == ["astropy__astropy-13398"]
    assert summary["false_green_rate"] == 1.0
    assert summary["rows"][0]["only_self_authored_tests_passed"] is True
    assert summary["rows"][0]["quality_score"] < 100


def test_self_written_green_tests_score_lower_than_the_same_run_with_existing_tests():
    own = case("own", True, "1 passed in 0.1s", True, diff=NEW_TEST_DIFF)
    existing = case("existing", True, "1 passed in 0.1s", True, diff=SRC_DIFF)
    scores = {r["instance_id"]: r["quality_score"] for r in validation.validate([own, existing])["rows"]}
    assert scores["own"] == 70.0 and scores["existing"] == 100.0


def test_thin_evidence_is_not_counted_as_a_passed_verdict():
    thin = case("thin", False, "1 passed in 0.1s", True, diff=NEW_TEST_DIFF)
    summary = validation.validate([thin])
    assert summary["rows"][0]["task_status"] == "INSUFFICIENT_EVIDENCE"
    assert summary["false_green"] == [] and summary["status_passed"] == 0


def test_labels_file_overrides_missing_ground_truth(tmp_path):
    cases = [case("a", None, "2 passed in 1s", True), case("b", None, "1 failed in 1s", False)]
    for item in cases:
        item.pop("resolved")
    labels = tmp_path / "report.json"
    labels.write_text(json.dumps({"resolved_ids": ["a"], "unresolved_ids": ["b"]}))
    summary = validation.validate(cases, validation.load_labels(labels))
    assert summary["resolved"] == 1 and summary["auroc"] == 1.0


def test_a_case_without_a_label_is_an_error_not_a_silent_skip():
    item = case("a", None, "2 passed in 1s", True)
    item.pop("resolved")
    with pytest.raises(BridgeError, match="no ground-truth label"):
        validation.validate([item])


def test_cases_without_test_evidence_are_counted_as_unscored_not_zero():
    item = {"instance_id": "no-evidence", "resolved": True, "diff": None, "test_result": None}
    summary = validation.validate([item])
    assert summary["unscored"] == 1 and summary["auroc"] is None


def test_load_cases_rejects_bad_lines(tmp_path):
    path = tmp_path / "cases.jsonl"
    path.write_text('{"instance_id": "a"}\nnot json\n')
    with pytest.raises(BridgeError, match=":2: not valid JSON"):
        validation.load_cases(path)


def test_cli_prints_a_summary_without_needing_a_project(tmp_path):
    cases = tmp_path / "cases.jsonl"
    cases.write_text(
        "\n".join(
            json.dumps(item)
            for item in [case("a", True, "2 passed in 1s", True), case("b", False, "1 failed in 1s", False)]
        )
    )
    result = CliRunner().invoke(app, ["--home", str(tmp_path / "home"), "validate-scoring", str(cases)])
    assert result.exit_code == 0, result.output
    data = json.loads(result.output)
    assert data["auroc"] == 1.0 and "rows" not in data


def test_export_cases_round_trips_a_real_task_into_the_validator(tmp_path, bridge, human):
    from test_evaluation_engine import (
        advance_to_implementation,
        pending_patch,
        redo_round,
        send_back_for_revision,
        start_task,
    )

    from ccc_mcp.worker import run_execution

    task_id = start_task(
        bridge, requirement="Fix astropy__astropy-13453 so tables keep their format",
        acceptance=["Implement the change"], full_suite=True,
    )
    implementation_id = advance_to_implementation(bridge, task_id)
    worktree, review_id = send_back_for_revision(bridge, task_id, implementation_id)
    round2_id = redo_round(
        bridge, task_id, review_id, worktree, {"test_clean.py": "def test_clean():\n    assert True\n"}
    )
    run_execution(
        bridge,
        bridge.advance_project("demo", pending_patch(bridge, round2_id), adapter="fake")[
            "next_execution"
        ]["execution_id"],
    )

    cases = bridge.export_scoring_cases("demo")
    assert [c["instance_id"] for c in cases] == ["astropy__astropy-13453"]
    assert cases[0]["task_id"] == task_id and "resolved" not in cases[0]
    assert cases[0]["test_result"]["executed_by_host"] is True
    # The fake adapter reports no CLI usage, so the export must say the token total is
    # unknown instead of presenting 0 as a real cost.
    assert cases[0]["usage_complete"] is False and cases[0]["tokens_total"] is None

    out = tmp_path / "cases.jsonl"
    out.write_text("\n".join(json.dumps(c) for c in cases))
    summary = validation.validate(validation.load_cases(out), {"astropy__astropy-13453": True})
    assert summary["cases"] == 1 and summary["rows"][0]["quality_score"] is not None


def test_an_implementation_round_that_changed_nothing_is_never_passed():
    # Tests that were already green before any edit prove nothing about the work done.
    untouched = case("noop", False, "2 passed in 0.1s", True, diff="", acceptance=["x"], static_findings=[])
    row = validation.validate([untouched])["rows"][0]
    assert row["task_status"] == "NEEDS_VERIFICATION"
    assert row["quality_score"] is None or row["quality_score"] < 100
