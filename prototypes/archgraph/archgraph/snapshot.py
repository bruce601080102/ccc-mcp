"""Commit snapshots and working-tree snapshots, with staged / unstaged / untracked / deleted states.

A snapshot records metadata only (path, blob id, change state, exclusion reason). File bytes are
returned separately, in memory, and only for Python files that passed every exclusion check.
Nothing read here is persisted; a secret-suspect file is excluded before it is hashed or kept.
"""

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path

from . import gitio
from .paths import MAX_BYTES, content_exclusion, looks_binary, mode_exclusion, path_exclusion


@dataclass(frozen=True)
class FileState:
    path: str
    head_blob: str | None
    index_blob: str | None
    worktree_blob: str | None
    index_change: str  # none | added | modified | deleted   (HEAD -> index)
    worktree_change: str  # none | modified | deleted | untracked | not_read   (index -> worktree)
    excluded: str | None  # reason, or None when eligible for indexing

    @property
    def read(self):
        return self.worktree_change != "not_read"

    @property
    def compare_blob(self):
        """The blob to compare between snapshots. Falls back to git's own record when unread."""
        if not self.read:
            return self.index_blob or self.head_blob
        return self.worktree_blob

    def as_dict(self):
        return asdict(self)


@dataclass
class Snapshot:
    mode: str  # commit | worktree
    repo: str
    head: str | None
    rev: str | None
    files: list

    def digest(self):
        payload = json.dumps([f.as_dict() for f in self.files], sort_keys=True).encode()
        return hashlib.sha256(f"{self.mode}|{self.head}|{self.rev}|".encode() + payload).hexdigest()

    def by_path(self):
        return {f.path: f for f in self.files}

    def as_dict(self):
        return {
            "mode": self.mode,
            "head": self.head,
            "rev": self.rev,
            "digest": self.digest(),
            "files": [f.as_dict() for f in self.files],
        }


def _index_change(head_blob, index_blob):
    if head_blob is None and index_blob is None:
        return "none"
    if head_blob is None:
        return "added"
    if index_blob is None:
        return "deleted"
    return "modified" if head_blob != index_blob else "none"


def commit_snapshot(repo, rev="HEAD"):
    """Snapshot of a commit. `rev="empty"` is the state before any code exists."""
    repo = Path(repo).resolve()
    if rev == "empty":
        return Snapshot("commit", str(repo), None, "empty", []), {}
    commit = gitio.resolve(repo, rev)
    files, contents = [], {}
    for path, (mode, blob) in sorted(gitio.tree_entries(repo, commit).items()):
        reason = path_exclusion(path) or mode_exclusion(mode)  # decided before any read
        if reason is None and path.endswith(".py"):
            data = gitio.read_blob(repo, blob)
            if looks_binary(data):
                reason = "BINARY"
            else:
                reason = content_exclusion(data)
            if reason is None and len(data) <= MAX_BYTES:
                contents[path] = data
            elif reason is None:
                reason = "TOO_LARGE"
        files.append(FileState(path, blob, blob, blob, "none", "none", reason))
    return Snapshot("commit", str(repo), commit, rev, files), contents


def worktree_snapshot(repo):
    """Working tree including staged, unstaged, untracked and deleted files.

    Reads only the user's files. Does not run add, commit, reset, checkout or any write, and
    does not touch the user's index or object database.
    """
    repo = Path(repo).resolve()
    head = gitio.head_sha(repo)
    head_tree = gitio.tree_entries(repo, head)
    index = gitio.index_entries(repo)
    untracked = set(gitio.untracked_paths(repo))
    files, contents = [], {}
    for path in sorted(set(head_tree) | set(index) | untracked):
        head_mode, head_blob = head_tree.get(path, (None, None))
        entries = index.get(path, [])
        conflict = len(entries) > 1 or any(stage for _, _, stage in entries)
        index_mode, index_blob = (entries[0][0], entries[0][1]) if entries else (None, None)
        index_change = _index_change(head_blob, None if conflict else index_blob)

        reason = path_exclusion(path)  # BEFORE the file is opened
        reason = reason or mode_exclusion(head_mode) or mode_exclusion(index_mode)
        if conflict:
            reason = reason or "MERGE_CONFLICT"
        full = repo / path
        if reason is None and full.is_symlink():
            reason = "SYMLINK_OR_SUBMODULE"
        if reason is not None:
            change = "untracked" if path in untracked else "not_read"
            files.append(FileState(path, head_blob, index_blob, None, index_change, change, reason))
            continue

        worktree_blob = None
        if full.is_file():
            if full.stat().st_size > MAX_BYTES:
                reason = "TOO_LARGE"
            else:
                data = full.read_bytes()
                worktree_blob = gitio.git_blob_sha(data)  # hash only; bytes are not kept
                if looks_binary(data):
                    reason = "BINARY"
                else:
                    reason = content_exclusion(data)
                if reason is None and path.endswith(".py"):
                    contents[path] = data

        if path in untracked:
            change = "untracked"
        elif index_blob is None or conflict:
            change = "none"
        elif worktree_blob is None:
            change = "deleted"
        else:
            change = "modified" if worktree_blob != index_blob else "none"
        files.append(
            FileState(path, head_blob, index_blob, worktree_blob, index_change, change, reason)
        )
    return Snapshot("worktree", str(repo), head, None, files), contents
