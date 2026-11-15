"""Working-tree classification checked against a real `git status` in a temporary repository."""

import hashlib
import os

from archgraph.snapshot import commit_snapshot, worktree_snapshot
from helpers import git, new_repo, write


def _index_bytes_hash(repo):
    return hashlib.sha256((repo / ".git" / "index").read_bytes()).hexdigest()


def _build(tmp_path):
    repo = new_repo(tmp_path)
    write(repo, "a.py", "A = 1\n")
    write(repo, "b.py", "B = 1\n")
    write(repo, "c.py", "C = 1\n")
    write(repo, "d.txt", "d\n")
    write(repo, ".gitignore", "ignored.log\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    # a.py: staged change, then a further unstaged change
    write(repo, "a.py", "A = 2\n")
    git(repo, "add", "a.py")
    write(repo, "a.py", "A = 3\n")
    write(repo, "b.py", "B = 2\n")  # unstaged modification
    os.remove(repo / "c.py")  # unstaged deletion
    git(repo, "rm", "-q", "--cached", "d.txt")  # staged deletion; the file stays on disk
    write(repo, "e.py", "E = 1\n")  # untracked
    write(repo, "f.py", "F = 1\n")
    git(repo, "add", "f.py")  # staged addition
    write(repo, "ignored.log", "noise\n")  # ignored, must not appear
    write(repo, ".env", "API_TOKEN=local-value-000\n")  # untracked and sensitive
    return repo


def test_each_path_gets_the_expected_index_and_worktree_state(tmp_path):
    repo = _build(tmp_path)
    snapshot, contents = worktree_snapshot(repo)
    states = {f.path: f for f in snapshot.files}

    assert states["a.py"].index_change == "modified"
    assert states["a.py"].worktree_change == "modified"
    assert states["b.py"].index_change == "none" and states["b.py"].worktree_change == "modified"
    assert states["c.py"].worktree_change == "deleted"
    assert (
        states["d.txt"].index_change == "deleted" and states["d.txt"].worktree_change == "untracked"
    )
    assert states["e.py"].index_change == "none" and states["e.py"].worktree_change == "untracked"
    assert states["f.py"].index_change == "added" and states["f.py"].worktree_change == "none"
    assert "ignored.log" not in states


def test_git_status_agrees_on_which_paths_are_affected(tmp_path):
    repo = _build(tmp_path)
    snapshot, _ = worktree_snapshot(repo)
    ours = {
        f.path
        for f in snapshot.files
        if f.index_change != "none" or f.worktree_change not in ("none",)
    }
    porcelain = git(repo, "status", "--porcelain=v1", "--untracked-files=all")
    git_paths = {line[3:] for line in porcelain.splitlines() if line.strip()}
    git_paths.discard(
        ".env"
    )  # git reports it; the prototype excludes it before reading, so it has no state
    assert ours - {".env"} == git_paths


def test_sensitive_and_secret_content_never_enter_the_snapshot_or_contents(tmp_path):
    repo = _build(tmp_path)
    write(repo, "settings.py", 'API_KEY = "sk-demo-0000000000000000"\n')
    snapshot, contents = worktree_snapshot(repo)
    states = {f.path: f for f in snapshot.files}
    assert states[".env"].excluded == "SENSITIVE_FILENAME"
    assert states[".env"].worktree_blob is None
    assert states["settings.py"].excluded == "CONTENT_SECRET_SUSPECT:ASSIGNED_SECRET"
    assert ".env" not in contents and "settings.py" not in contents
    assert b"sk-demo" not in repr(snapshot.as_dict()).encode()


def test_commit_snapshot_reports_committed_content_not_the_working_copy(tmp_path):
    repo = _build(tmp_path)
    snapshot, contents = commit_snapshot(repo, "HEAD")
    assert contents["a.py"] == b"A = 1\n"  # the working copy says A = 3
    assert all(f.index_change == "none" and f.worktree_change == "none" for f in snapshot.files)


def test_reading_snapshots_leaves_the_user_index_and_worktree_byte_identical(tmp_path):
    repo = _build(tmp_path)
    index_before = _index_bytes_hash(repo)
    entries_before = git(repo, "ls-files", "-s", "-z")
    status_before = git(repo, "status", "--porcelain=v1")
    worktree_before = {
        p: (repo / p).read_bytes() for p in ("a.py", "b.py", "e.py", "f.py") if (repo / p).exists()
    }

    worktree_snapshot(repo)
    commit_snapshot(repo, "HEAD")

    assert _index_bytes_hash(repo) == index_before
    assert git(repo, "ls-files", "-s", "-z") == entries_before
    assert git(repo, "status", "--porcelain=v1") == status_before
    assert {p: (repo / p).read_bytes() for p in worktree_before} == worktree_before
