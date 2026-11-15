"""The architecture page: states, planned versus actual, edge reconciliation, escaping, determinism.

These tests check that the page is correct and complete. They do not measure how novices use it.
"""

import json
import re
from pathlib import Path

import pytest
from archgraph.pipeline import run_pipeline
from make_notes_cli import build
from run_demo import PLANS, run_demo

HERE = Path(__file__).resolve().parents[1] / "fixtures" / "notes_cli"


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    out = tmp_path_factory.mktemp("arch-demo")
    run_demo(out)
    return out


def _arch(out, task):
    return json.loads((out / task / "architecture.json").read_text(encoding="utf-8"))


def test_task_three_states_cover_every_required_category(demo):
    arch = _arch(demo, "TASK-0003")
    files = arch["files"]
    assert files["notes/sqlite_store.py"]["state"] == "ADDED"
    assert (
        files["notes/migrate.py"]["state"] == "ADDED"
        and "PENDING" in files["notes/migrate.py"]["overlays"]
    )
    assert files["notes/storage.py"]["state"] == "REMOVED"
    assert files["notes/cli.py"]["state"] == "MODIFIED"
    assert "STAGED_DIFF" in files["notes/cli.py"]["overlays"]  # staged copy differs from worktree
    assert files["notes/search.py"]["state"] == "EXISTING"
    assert (
        "OUT_OF_PLAN" in files["notes/models.py"]["overlays"]
        and "UNPLANNED" in files["notes/models.py"]["overlays"]
    )
    assert "OUT_OF_PLAN" in files["notes/schema.sql"]["overlays"]
    assert "UNCOMMITTED" in files["notes/sqlite_store.py"]["overlays"]


def test_content_excluded_and_name_excluded_files_are_shown_as_excluded_never_as_code(demo):
    files = _arch(demo, "TASK-0003")["files"]
    assert files[".env"]["state"] == "EXCLUDED"
    assert files["notes/local_settings.py"]["state"] == "EXCLUDED"
    assert files["notes/local_settings.py"]["symbols"] == []
    assert (
        files["notes/local_settings.py"]["excluded_reason"]
        == "CONTENT_SECRET_SUSPECT:ASSIGNED_SECRET"
    )


def test_header_keeps_commit_structure_and_host_test_apart(demo):
    header = _arch(demo, "TASK-0003")["header"]
    assert header["commit_state"] == "UNCOMMITTED"
    assert header["structure_state"] == "BLOCKED_BY_SCOPE"
    assert header["host_test"] == "PASSED"
    assert header["out_of_plan"] == ["notes/models.py", "notes/schema.sql"]


def test_planned_tab_compares_the_plan_with_what_exists(demo):
    planned = _arch(demo, "TASK-0003")["files"]
    assert planned["notes/storage.py"]["planned"] == "DELETE"
    assert planned["notes/storage.py"]["planned_check"] == "已依計畫刪除"
    assert planned["notes/migrate.py"]["planned_check"] == "已依計畫建立"
    assert planned["notes/cli.py"]["planned_check"] == "已依計畫修改"


def test_planned_and_actual_dependency_edges_are_reconciled(demo):
    summary = _arch(demo, "TASK-0003")["edges_summary"]
    assert "notes/migrate.py → notes/sqlite_store.py" in summary["matched"]
    assert summary["unplanned"] == [] and summary["missing"] == []


def _pipeline_with_plan(tmp_path, mutate):
    refs = build(tmp_path / "notes-cli")
    doc = json.loads((PLANS / "plan3.json").read_text(encoding="utf-8"))
    mutate(doc)
    plan_path = tmp_path / "plan.json"
    plan_path.write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
    out = tmp_path / "out"
    run_pipeline(
        refs["repo"],
        [f"commit:{refs['C1']}", f"commit:{refs['C2']}", "worktree"],
        tmp_path / "db.sqlite",
        out,
        plan_source=plan_path,
        confirmations=json.loads((HERE / "confirmations3.json").read_text(encoding="utf-8")),
        test_argv=None,
    )
    return json.loads((out / "architecture.json").read_text(encoding="utf-8"))


def test_an_actual_dependency_the_plan_did_not_name_is_unplanned(tmp_path):
    def drop_one(doc):
        doc["planned_dependencies"] = [
            pair
            for pair in doc["planned_dependencies"]
            if pair != ["notes/cli.py", "notes/tags.py"]
        ]

    arch = _pipeline_with_plan(tmp_path, drop_one)
    assert "notes/cli.py → notes/tags.py" in arch["edges_summary"]["unplanned"]


def test_a_planned_dependency_that_was_not_built_is_missing(tmp_path):
    def add_one(doc):
        doc["planned_dependencies"].append(["notes/cli.py", "notes/storage.py"])

    arch = _pipeline_with_plan(tmp_path, add_one)
    assert "notes/cli.py → notes/storage.py" in arch["edges_summary"]["missing"]


def test_the_html_is_self_contained_and_uses_no_external_resources(demo):
    page = (demo / "TASK-0003" / "architecture.html").read_text(encoding="utf-8")
    assert (
        "<script src=" not in page
        and "<link " not in page
        and "http://" not in page.split("</head>")[0]
    )
    assert page.count('class="node"') >= 10
    assert (
        'data-tab="tab-novice"' in page
        and 'data-tab="tab-planned"' in page
        and 'data-tab="tab-engineer"' in page
    )


def test_html_payload_cannot_break_out_of_its_script_tag(demo):
    page = (demo / "TASK-0003" / "architecture.html").read_text(encoding="utf-8")
    payload = re.search(
        r'<script type="application/json" id="archgraph-data">(.*?)</script>', page, re.S
    )
    assert payload is not None
    json.loads(payload.group(1))  # parses as JSON
    assert "</" not in payload.group(1)


def test_titles_are_escaped_not_interpreted(tmp_path):
    doc = json.loads((PLANS / "plan1.json").read_text(encoding="utf-8"))
    doc["module_titles"]["notes/cli.py"] = "<img src=x onerror=alert(1)>"
    refs = build(tmp_path / "notes-cli")
    summary = run_pipeline(
        refs["repo"],
        ["empty", f"commit:{refs['C1']}"],
        tmp_path / "db.sqlite",
        tmp_path / "out",
        plan_source=doc,
        test_argv=None,
    )
    page = (tmp_path / "out" / "architecture.html").read_text(encoding="utf-8")
    assert "<img src=x" not in page and "&lt;img src=x" in page
    assert summary["task"] == "TASK-0001"


def test_the_same_inputs_render_the_same_page(tmp_path):
    first = run_demo(tmp_path / "a")
    second = run_demo(tmp_path / "b")
    a = (tmp_path / "a" / "TASK-0003" / "architecture.html").read_text(encoding="utf-8")
    b = (tmp_path / "b" / "TASK-0003" / "architecture.html").read_text(encoding="utf-8")

    # The demo repository is rebuilt each time, so compare the structural parts only.
    def strip(text):
        return re.sub(r"[0-9a-f]{40}|[0-9a-f]{16}|N-\d{4}", "X", text)

    assert strip(a) == strip(b)
    assert first and second


def test_layout_never_overlaps_nodes_in_a_row(demo):
    tab = _arch(demo, "TASK-0003")["tabs"]["novice"]
    seen = {}
    for node, (x, y) in tab["coords"].items():
        assert (x, y) not in seen.values(), node
        seen[node] = (x, y)
