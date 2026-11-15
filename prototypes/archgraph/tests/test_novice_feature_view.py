"""Novice main view (feature architecture) checks. Offline; presentation only.

These check what the reader sees first: feature nodes with natural-language markers, the evolution
line, the Task-level approval verdict beside the earlier unresolved items, and that the identity
links point at the feature that owns each side of a pending pair. The data behind the page is not
changed here: the engine, identity, TaskDelta and the approval gate are the same as before.
"""

import json
import re

import pytest
from run_demo import run_demo

RAW_CODES = ("UNCOMMITTED", "COMMITTED", "BLOCKED_BY_SCOPE", "FLAGGED_OUT_OF_SCOPE", "PASSED", "MATCHED", "BLOCKED", "READY")


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    out = tmp_path_factory.mktemp("novice-feature-view")
    run_demo(out)
    return out


def _page(demo, task):
    return (demo / task / "architecture.html").read_text(encoding="utf-8")


def _payload(demo, task):
    page = _page(demo, task)
    match = re.search(r'<script type="application/json" id="archgraph-data">(.*?)</script>', page, re.S)
    return json.loads(match.group(1))


def _novice_section(demo, task):
    page = _page(demo, task)
    start = page.index('<section id="tab-novice"')
    end = page.index('<section id="tab-planned"')
    return page[start:end]


def test_task_one_shows_the_initial_feature_architecture_as_additions(demo):
    payload = _payload(demo, "TASK-0001")
    features = payload["features"]
    named = {k: v for k, v in features.items() if k != "UNATTRIBUTED"}
    assert len(named) == 4
    assert all(v["marker"] == "ADD" for v in named.values())
    assert "UNATTRIBUTED" in features  # notes/util.py is out of plan and unattributed: shown, not hidden


def test_task_two_marks_added_and_modified_features_with_words(demo):
    section = _novice_section(demo, "TASK-0002")
    assert "＋ 新增" in section and "✎ 修改" in section
    markers = {v["marker"] for k, v in _payload(demo, "TASK-0002")["features"].items() if k != "UNATTRIBUTED"}
    assert {"ADD", "MODIFY"} <= markers


def test_task_three_states_the_json_to_sqlite_evolution_in_words(demo):
    section = _novice_section(demo, "TASK-0003")
    assert "演進：" in section and "本機儲存（JSON 檔） → 本機儲存（SQLite）" in section


def test_novice_section_uses_natural_labels_not_raw_states(demo):
    for task in ("TASK-0001", "TASK-0002", "TASK-0003"):
        section = _novice_section(demo, task)
        for code in RAW_CODES:
            assert code not in section, (task, code)


@pytest.fixture(scope="module")
def chain(tmp_path_factory):
    """The three Tasks built the way the history is built: each later Task receives the earlier
    architectures as prior_architectures, so the prior-Task lines are real history."""

    from archgraph.pipeline import run_pipeline
    from make_notes_cli import build
    from run_demo import PLANS, TEST_ARGV

    root = tmp_path_factory.mktemp("novice-chain")
    refs = build(root / "notes-cli")
    db = root / "db.sqlite"
    c1, c2 = f"commit:{refs['C1']}", f"commit:{refs['C2']}"
    run_pipeline(refs["repo"], ["empty", c1], db, root / "t1", plan_source=PLANS / "plan1.json",
                 test_argv=TEST_ARGV)
    arch1 = json.loads((root / "t1" / "architecture.json").read_text(encoding="utf-8"))
    run_pipeline(refs["repo"], ["empty", c1, c2], db, root / "t2", plan_source=PLANS / "plan2.json",
                 prior_architectures=[arch1], test_argv=TEST_ARGV)
    arch2 = json.loads((root / "t2" / "architecture.json").read_text(encoding="utf-8"))
    run_pipeline(refs["repo"], ["empty", c1, c2, "worktree"], db, root / "t3",
                 plan_source=PLANS / "plan3.json", prior_architectures=[arch1, arch2],
                 confirmations=json.loads((PLANS.parent / "confirmations3.json").read_text(encoding="utf-8")),
                 test_argv=TEST_ARGV)
    return root


def test_gate_verdict_is_per_task_and_earlier_unresolved_items_are_still_named(chain, demo):
    # TASK-0002 is READY on its own, yet the earlier out-of-plan file is named beside the verdict.
    page = (chain / "t2" / "architecture.html").read_text(encoding="utf-8")
    section = page[page.index('<section id="tab-novice"'):page.index('<section id="tab-planned"')]
    assert "可合併（本 Task）" in section
    assert "另有前序未解事項" in page  # the verdict card names it
    assert "前序未解事項" in page and "notes/util.py" in page  # and the expandable block lists it
    # TASK-0001 is BLOCKED by its own out-of-plan file, and the reason is stated on the card.
    page1 = _page(demo, "TASK-0001")
    assert "未通過（本 Task 有阻擋項）" in page1 and "超出計畫 1 個檔案" in page1


def test_identity_links_point_at_the_feature_that_owns_each_side(demo):
    payload = _payload(demo, "TASK-0003")
    owners = payload["feature_of_file"]
    assert owners["notes/storage.py"] == ["F-04"]  # old JSON node -> 本機儲存（SQLite） feature
    assert owners["notes/migrate.py"] == ["F-06"]  # new importer -> 匯入舊 JSON feature
    assert owners["notes/schema.sql"] == ["UNATTRIBUTED"]
    for pair in payload["pending"]:
        assert pair["from_file"] in owners and pair["to_file"] in owners


def test_planned_and_actual_remain_separate_tabs(demo):
    page = _page(demo, "TASK-0003")
    assert 'data-tab="tab-planned"' in page and "計畫（Planned）" in page
    assert "功能架構（實際，新手）" in page


# ---------------------------------------------------------------- T08-UI-002
def test_the_novice_view_never_mixes_git_state_into_unconfirmed(demo):
    for task in ("TASK-0001", "TASK-0002", "TASK-0003"):
        section = _novice_section(demo, task)
        assert "未確認" not in section, task


def test_arrows_keep_direct_evidence_and_mark_inferred_relations(demo):
    payload = _payload(demo, "TASK-0003")
    owners = payload["feature_of_file"]
    assert payload["edges"]
    for edge in payload["edges"]:
        pairs = [tuple(part.strip() for part in ev.split("→")) for ev in edge["evidence"]]
        if edge["status"] in ("DIRECT", "DIRECT_UNPLANNED"):
            # direct evidence exists: one import whose two files each belong to exactly one feature
            assert any(
                len(owners[a]) == 1 and len(owners[b]) == 1 and "UNATTRIBUTED" not in owners[a] + owners[b]
                for a, b in pairs
            ), edge
        else:
            assert edge["status"] == "INFERRED", edge
            # no direct evidence: every import involves a file that belongs to several features
            assert all(len(owners[a]) > 1 or len(owners[b]) > 1 for a, b in pairs), edge


def test_the_inferred_relation_is_drawn_dashed_and_explained(demo):
    section = _novice_section(demo, "TASK-0003")
    assert "推測性功能關係" in section
    assert "不代表已確認的功能依賴" in section
    if any(e["status"] == "INFERRED" for e in _payload(demo, "TASK-0003")["edges"]):
        assert 'stroke-dasharray="3 4"' in section


def test_narrow_screens_put_the_graph_before_the_attention_blocks_and_details(demo):
    page = _page(demo, "TASK-0003")
    assert "@media (max-width: 1099px)" in page
    assert "#tab-novice .canvas { order: 1; }" in page
    section = _novice_section(demo, "TASK-0003")
    assert section.index('class="canvas"') < section.index('class="novice-attention"')
    assert section.index('class="canvas"') < section.index('class="status-detail"')
