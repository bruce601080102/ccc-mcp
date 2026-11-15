import json

import pytest
from archgraph.plan import PlanError, load_plan


def _plan(**overrides):
    doc = {
        "task": "TASK-X",
        "features": [{"id": "F-01", "title_plain": "x", "members": ["a.py::f"]}],
        "symbols": [{"key": "a.py::f", "action": "ADD", "reason": "r"}],
        "files": {"add": ["a.py"]},
    }
    doc.update(overrides)
    return doc


def test_a_valid_plan_gets_a_stable_identity():
    first = load_plan(_plan())
    second = load_plan(_plan())
    assert first.gv_id == second.gv_id and len(first.gv_id) == 16


@pytest.mark.parametrize(
    "overrides",
    [
        {"task": 7},
        {"symbols": [{"key": "no-separator", "action": "ADD", "reason": "r"}]},
        {"symbols": [{"key": "a.py::f", "action": "RENAME", "reason": "r"}]},
        {"files": {"unknown": ["a.py"]}},
        {"features": [{"id": "F-01", "title_plain": "a", "members": ["bare"]}]},
        {"features": [{"id": "F-01", "title_plain": "a"}, {"id": "F-01", "title_plain": "b"}]},
    ],
)
def test_malformed_plans_are_rejected_before_any_comparison(overrides):
    with pytest.raises(PlanError):
        load_plan(_plan(**overrides))


def test_plan_can_be_loaded_from_a_file(tmp_path):
    path = tmp_path / "plan.json"
    path.write_text(json.dumps(_plan()), encoding="utf-8")
    assert load_plan(path).task == "TASK-X"
