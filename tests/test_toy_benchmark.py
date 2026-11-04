"""Scorer vs ground truth on the hand-built toy benchmark (tests/toybench.py).

Pins what the rule-based score can and cannot tell apart from host-visible evidence, so a
future scoring change that improves or worsens it shows up here."""
import pytest
import toybench

from ccc_mcp.evaluation_engine import validation


@pytest.fixture(scope="module")
def summary(tmp_path_factory):
    return validation.validate(toybench.build_cases(tmp_path_factory.mktemp("toy")))


def rows_of(summary, kind):
    return [r for r in summary["rows"] if r["instance_id"].endswith(f"-{kind}")]


def test_benchmark_shape(summary):
    assert summary["cases"] == 30 and summary["resolved"] == 12


def test_score_ranks_better_than_a_coin_flip(summary):
    assert summary["auroc"] > 0.5


def test_a_patch_that_breaks_visible_tests_is_failed_and_scored_below_every_good_patch(summary):
    broken, good = rows_of(summary, "broken"), rows_of(summary, "good") + rows_of(summary, "good_tested")
    assert {r["task_status"] for r in broken} == {"FAILED"}
    assert max(r["quality_score"] for r in broken) < min(r["quality_score"] for r in good)


def test_known_limit_wrong_patches_that_keep_visible_tests_green_look_passed(summary):
    # Without hidden tests, host evidence cannot separate these from correct patches. If this
    # starts failing because the scorer got smarter, update the docs along with the pin.
    wrong_green = set(summary["false_green"])
    assert wrong_green == {r["instance_id"] for k in ("overfit", "selftest") for r in rows_of(summary, k)}
