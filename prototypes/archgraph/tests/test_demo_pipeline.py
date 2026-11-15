"""End-to-end: notes-cli TASK-0001..0003 through the prototype, in a temporary demo repository."""

import hashlib
import json
from pathlib import Path

import pytest
from archgraph.pipeline import run_pipeline
from helpers import git
from make_notes_cli import ROOT, build
from run_demo import PLANS, TEST_ARGV, run_demo

HERE = Path(__file__).resolve().parents[1] / "fixtures" / "notes_cli"
SECRET_NEEDLES = ("demo-env-value", "sk-demo-0000", "API_TOKEN=", "API_KEY =")
SOURCE_NEEDLES = ("json.loads(", "def load_json", "self.path.write_text", "class JsonImporter:")


@pytest.fixture(scope="module")
def demo(tmp_path_factory):
    out = tmp_path_factory.mktemp("archgraph-demo")
    results = run_demo(out)
    return out, results


def _delta(out, task):
    return json.loads((out / task / "delta.json").read_text(encoding="utf-8"))


def test_task_one_builds_the_baseline_and_flags_the_unplanned_utility(demo):
    out, results = demo
    t1 = results["TASK-0001"]
    assert t1["commit_state"] == "COMMITTED"
    assert t1["host_test_state"] == "PASSED"
    assert t1["structure_state"] == "FLAGGED_OUT_OF_SCOPE"
    assert t1["out_of_plan_files"] == ["notes/util.py"]
    delta = _delta(out, "TASK-0001")
    assert delta["unplanned_symbols"] == ["notes/util.py::now_iso"]
    assert {row["marker"] for row in delta["symbols"]} == {"ADD"}


def test_task_two_is_matched_committed_and_changes_only_what_the_plan_names(demo):
    out, results = demo
    t2 = results["TASK-0002"]
    assert (t2["commit_state"], t2["structure_state"], t2["host_test_state"]) == (
        "COMMITTED",
        "MATCHED",
        "PASSED",
    )
    delta = _delta(out, "TASK-0002")
    markers = {row["key"]: row["marker"] for row in delta["symbols"]}
    assert markers["notes/models.py::Note"] == "MODIFY"
    assert markers["notes/tags.py::normalize_tags"] == "ADD"
    assert (
        markers["tests/test_search.py::test_search_matches_title_and_body_case_insensitively"]
        == "AFFECTED"
    )
    non_new = [e for e in delta["identity_events"] if e["type"] != "NEW"]
    assert non_new == []  # genuinely new symbols are NEW; nothing moved, removed or ambiguous


def test_task_three_is_uncommitted_blocked_by_scope_and_keeps_secrets_out(demo):
    out, results = demo
    t3 = results["TASK-0003"]
    assert t3["commit_state"] == "UNCOMMITTED"
    assert t3["structure_state"] == "BLOCKED_BY_SCOPE"
    assert t3["host_test_state"] == "PASSED"
    assert t3["out_of_plan_files"] == ["notes/models.py", "notes/schema.sql"]
    excluded = {e["path"]: e["reason"] for e in t3["excluded"]}
    assert excluded == {
        ".env": "SENSITIVE_FILENAME",
        "notes/local_settings.py": "CONTENT_SECRET_SUSPECT:ASSIGNED_SECRET",
    }


def test_task_three_identity_facts_are_exact(demo):
    out, _ = demo
    delta = _delta(out, "TASK-0003")
    rows = {row["key"]: row for row in delta["symbols"]}
    moved = rows["notes/migrate.py::JsonImporter.read"]
    assert moved["marker"] == "MOVED"
    assert moved["from_key"] == "notes/storage.py::NotesStore.load_json"
    assert moved["identity_status"] == "CONFIRMED_BY_USER"
    # The empty class must not be merged with the old one just because both are empty.
    assert rows["notes/migrate.py::JsonImporter"]["marker"] == "ADD"
    assert rows["notes/storage.py::NotesStore"]["marker"] == "REMOVE"
    assert rows["notes/storage.py::NotesStore.save_json"]["marker"] == "REMOVE"
    assert rows["notes/models.py::Note"]["structure_state"] == "UNPLANNED"


def test_the_novice_view_marks_renames_new_features_and_separates_the_fact_kinds(demo):
    out, _ = demo
    novice = json.loads((out / "TASK-0003" / "novice.json").read_text(encoding="utf-8"))
    features = {f["id"]: f for f in novice["features"]}
    assert features["F-06"]["marker"] == "ADD"  # declared new in the plan
    assert "原名" in features["F-04"]["sentence"]
    # Git state, scope and identity pending are separate fields; none is a mix of the others.
    assert "有未規劃的程式變更" in features["F-05"]["scope"]
    assert features["F-05"]["git"] == "尚未 commit"
    assert all(
        f["git"] == "尚未 commit" for f in novice["features"] if f["marker"] != "UNCHANGED"
    )
    for f in novice["features"]:
        assert "尚未 commit" not in f["scope"]
        assert isinstance(f["identity_pending"], int)
    kinds = [n["kind"] for n in novice["notices"]]
    assert kinds == ["OUT_OF_PLAN", "UNCOMMITTED", "PENDING", "EXCLUDED"]


def test_no_secret_text_appears_in_any_output_or_the_database(demo):
    out, _ = demo
    for path in out.rglob("*"):
        if path.is_file() and path.name != "notes-cli" and "notes-cli" not in path.parts:
            data = path.read_bytes()
            for needle in SECRET_NEEDLES:
                assert needle.encode() not in data, (path, needle)


def test_no_source_text_is_stored_in_graphs_or_views(demo):
    out, _ = demo
    for name in (
        "TASK-0003/delta.json",
        "TASK-0003/novice.json",
        "TASK-0003/engineer.json",
        "TASK-0003/novice.html",
        "TASK-0003/actual_graph.json",
        "archgraph.sqlite",
    ):
        path = out / name
        if path.exists():
            text = path.read_bytes().decode("utf-8", "replace")
            for needle in SOURCE_NEEDLES:
                assert needle not in text, (name, needle)


def _tree_fingerprint(repo):
    digest = hashlib.sha256()
    for path in sorted(repo.rglob("*")):
        if path.is_file() and ".git" not in path.relative_to(repo).parts:
            digest.update(str(path.relative_to(repo)).encode())
            digest.update(path.read_bytes())
    return digest.hexdigest()


def test_running_the_prototype_leaves_the_repository_byte_identical(tmp_path):
    refs = build(tmp_path / "notes-cli")
    repo = Path(refs["repo"])
    index_before = hashlib.sha256((repo / ".git" / "index").read_bytes()).hexdigest()
    head_before = git(repo, "rev-parse", "HEAD")
    status_before = git(repo, "status", "--porcelain=v1", "--untracked-files=all")
    tree_before = _tree_fingerprint(repo)

    run_pipeline(
        repo,
        [f"commit:{refs['C1']}", f"commit:{refs['C2']}", "worktree"],
        tmp_path / "db.sqlite",
        tmp_path / "out",
        plan_source=PLANS / "plan3.json",
        confirmations=json.loads((HERE / "confirmations3.json").read_text(encoding="utf-8")),
        test_argv=TEST_ARGV,
    )

    assert hashlib.sha256((repo / ".git" / "index").read_bytes()).hexdigest() == index_before
    assert git(repo, "rev-parse", "HEAD") == head_before
    assert git(repo, "status", "--porcelain=v1", "--untracked-files=all") == status_before
    assert _tree_fingerprint(repo) == tree_before


def test_without_confirmation_both_unresolved_pairs_stay_pending_and_are_not_merged(tmp_path):
    refs = build(tmp_path / "notes-cli")
    summary = run_pipeline(
        refs["repo"],
        [f"commit:{refs['C1']}", f"commit:{refs['C2']}", "worktree"],
        tmp_path / "db.sqlite",
        tmp_path / "out",
        plan_source=PLANS / "plan3.json",
        confirmations=[],
        test_argv=None,
    )
    pending = {(p["from_key"], p["to_key"]): p for p in summary["pending_confirmations"]}
    read_pair = pending[
        ("notes/storage.py::NotesStore.load_json", "notes/migrate.py::JsonImporter.read")
    ]
    assert read_pair["score"] == 0.583
    assert "SCORE_BELOW_AUTO" in read_pair["reasons"]
    assert "PARENT_IDENTITY_UNRESOLVED" in read_pair["reasons"]
    # Identical __init__ bodies in two different classes: the strongest similarity, still no merge.
    init_pair = pending[
        ("notes/storage.py::NotesStore.__init__", "notes/migrate.py::JsonImporter.__init__")
    ]
    assert init_pair["score"] == 1.0
    assert init_pair["reasons"] == ["PARENT_IDENTITY_UNRESOLVED"]
    delta = json.loads((tmp_path / "out" / "delta.json").read_text(encoding="utf-8"))
    rows = {row["key"]: row for row in delta["symbols"]}
    assert rows["notes/migrate.py::JsonImporter.read"]["marker"] == "UNCERTAIN"
    assert rows["notes/migrate.py::JsonImporter.__init__"]["marker"] == "UNCERTAIN"
    assert rows["notes/storage.py::NotesStore.load_json"]["marker"] == "REMOVE"
    assert delta["host_test_state"] == "NOT_RUN"


def test_the_demo_is_never_built_inside_the_ccc_mcp_checkout():
    with pytest.raises(SystemExit):
        build(ROOT / "scratch-demo-should-not-exist")
    assert not (ROOT / "scratch-demo-should-not-exist").exists()
