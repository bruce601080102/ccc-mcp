"""Uncommitted work must survive into the TaskDelta: staged, unstaged, untracked, deleted, excluded."""

import json

from archgraph.delta import reconcile
from archgraph.graph import build_actual
from archgraph.identity import assign_identities
from archgraph.pipeline import run_pipeline
from archgraph.plan import load_plan
from archgraph.snapshot import commit_snapshot, worktree_snapshot
from archgraph.store import Store
from helpers import git, new_repo, write

EMPTY_PLAN = load_plan({"task": "TASK-T", "title": "t", "features": [], "symbols": [], "files": {}})


def _graphs(repo):
    snap, contents = commit_snapshot(repo, "HEAD")
    base = build_actual(snap, contents, "base")
    snap, contents = worktree_snapshot(repo)
    current = build_actual(snap, contents, "worktree")
    return base, current


def test_staged_change_survives_when_the_worktree_was_reverted(tmp_path):
    repo = new_repo(tmp_path)
    write(repo, "a.py", "A = 1\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    write(repo, "a.py", "A = 2\n")
    git(repo, "add", "a.py")  # staged change
    write(repo, "a.py", "A = 1\n")  # worktree back to HEAD
    base, current = _graphs(repo)
    delta = reconcile(EMPTY_PLAN, base, current, None)
    row = next(r for r in delta["files"] if r["path"] == "a.py")
    assert row["change"]["index"] == "modified"
    assert row["commit_state"] == "UNCOMMITTED"
    assert delta["commit_state"] == "UNCOMMITTED"


def test_every_uncommitted_state_appears_in_the_delta_with_its_own_state(tmp_path):
    repo = new_repo(tmp_path)
    write(repo, "keep.py", "K = 1\n")
    write(repo, "edit.py", "E = 1\n")
    write(repo, "gone.py", "G = 1\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    write(repo, "edit.py", "E = 2\n")  # unstaged modification
    git(repo, "rm", "-q", "gone.py")  # staged deletion
    write(repo, "new.py", "N = 1\n")  # untracked
    base, current = _graphs(repo)
    delta = reconcile(EMPTY_PLAN, base, current, None)
    rows = {r["path"]: r for r in delta["files"]}
    assert set(rows) == {"edit.py", "gone.py", "new.py"}
    assert rows["edit.py"]["change"]["worktree"] == "modified"
    assert rows["gone.py"]["change"]["index"] == "deleted"
    assert rows["new.py"]["change"]["worktree"] == "untracked"
    assert all(r["commit_state"] == "UNCOMMITTED" for r in rows.values())


def test_excluded_uncommitted_file_is_listed_not_dropped(tmp_path):
    repo = new_repo(tmp_path)
    write(repo, "a.py", "A = 1\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    write(repo, ".env", "API_TOKEN=local-value-0001\n")
    base, current = _graphs(repo)
    delta = reconcile(EMPTY_PLAN, base, current, None)
    excluded = {e["path"]: e for e in delta["excluded"]}
    assert excluded[".env"]["reason"] == "SENSITIVE_FILENAME"
    assert excluded[".env"]["change"]["worktree"] == "untracked"


def test_uncommitted_delta_is_persisted_and_reloaded_unchanged(tmp_path):
    repo = new_repo(tmp_path)
    write(repo, "a.py", "A = 1\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    write(repo, "a.py", "A = 2\n")
    write(repo, "b.py", "B = 1\n")
    run_pipeline(repo, ["commit:HEAD", "worktree"], tmp_path / "db.sqlite", tmp_path / "out")
    base, current = _graphs(repo)
    plan = EMPTY_PLAN
    delta = reconcile(plan, base, current, None)
    store = Store(tmp_path / "db.sqlite")
    store.save_delta(plan.task, current.gv_id, delta)
    reloaded = store.load_delta(plan.task)
    store.close()
    assert reloaded == json.loads(json.dumps(delta, default=str))
    assert {r["path"] for r in reloaded["files"]} == {"a.py", "b.py"}


def test_a_pending_new_symbol_is_reported_once_as_uncertain_not_also_as_add(tmp_path):
    # JsonImporter.__init__ has the same body as NotesStore.__init__ but no parent identity yet.
    repo = new_repo(tmp_path)
    write(
        repo,
        "old.py",
        "class NotesStore:\n    def __init__(self, path):\n        self.path = path\n",
    )
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    git(repo, "rm", "-q", "old.py")
    write(
        repo,
        "new.py",
        "class JsonImporter:\n    def __init__(self, path):\n        self.path = path\n",
    )
    git(repo, "add", "-A")
    base, current = _graphs(repo)
    assign_identities([], base, {}, 0)  # the base GraphVersion gets its nids first
    assign_identities(base.nodes, current, {}, len(base.nodes))
    delta = reconcile(EMPTY_PLAN, base, current, None)
    rows = [r for r in delta["symbols"] if r["key"] == "new.py::JsonImporter.__init__"]
    assert len(rows) == 1
    assert rows[0]["marker"] == "UNCERTAIN"
    assert "PARENT_IDENTITY_UNRESOLVED" in next(
        p["reasons"]
        for p in delta["pending_confirmations"]
        if p["to_key"] == "new.py::JsonImporter.__init__"
    )
