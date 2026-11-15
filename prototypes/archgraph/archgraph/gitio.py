"""Read-only Git access for the prototype.

The user's repository must never be changed. This module therefore only runs an explicit
whitelist of inspection subcommands, with optional index locks disabled so that a status-like
read never refreshes .git/index. Any other subcommand is refused before a process starts.
Blob identifiers are computed in Python from file bytes; no object is written to .git/objects.
"""

import hashlib
import io
import os
import subprocess
import tarfile

ALLOWED_VERBS = frozenset({"rev-parse", "ls-files", "ls-tree", "cat-file", "archive"})


class GitError(RuntimeError):
    pass


def _git(repo, verb, *args):
    if verb not in ALLOWED_VERBS:
        raise GitError(f"git {verb} is refused: the prototype is read-only")
    env = {**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"}
    proc = subprocess.run(
        ["git", "--no-optional-locks", "-C", str(repo), verb, *args],
        capture_output=True,
        stdin=subprocess.DEVNULL,
        env=env,
        timeout=120,
        check=False,
    )
    if proc.returncode != 0:
        raise GitError(proc.stderr.decode("utf-8", "replace")[:500])
    return proc.stdout


def git_blob_sha(data):
    """The object id git would give these bytes as a blob, computed without writing anything."""
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def head_sha(repo):
    try:
        out = _git(repo, "rev-parse", "--verify", "-q", "HEAD").decode().strip()
    except GitError:
        return None  # unborn branch
    return out or None


def resolve(repo, rev):
    return _git(repo, "rev-parse", "--verify", "-q", f"{rev}^{{commit}}").decode().strip()


def tree_entries(repo, commit):
    """path -> (mode, blob) for every file in the commit's tree."""
    entries = {}
    if commit is None:
        return entries
    for record in _git(repo, "ls-tree", "-r", "-z", commit).split(b"\0"):
        if not record:
            continue
        meta, _, path = record.partition(b"\t")
        mode, _kind, blob = meta.decode().split()
        entries[path.decode("utf-8", "surrogateescape")] = (mode, blob)
    return entries


def index_entries(repo):
    """path -> [(mode, blob, stage), ...] from the index. More than one stage means a conflict."""
    entries = {}
    for record in _git(repo, "ls-files", "-s", "-z").split(b"\0"):
        if not record:
            continue
        meta, _, path = record.partition(b"\t")
        mode, blob, stage = meta.decode().split()
        entries.setdefault(path.decode("utf-8", "surrogateescape"), []).append(
            (mode, blob, int(stage))
        )
    return entries


def untracked_paths(repo):
    raw = _git(repo, "ls-files", "--others", "--exclude-standard", "-z")
    return [p.decode("utf-8", "surrogateescape") for p in raw.split(b"\0") if p]


def read_blob(repo, blob):
    return _git(repo, "cat-file", "blob", blob)


def export_commit(repo, commit, destination):
    """Extract a commit's tree into `destination` using `git archive` (no repository writes)."""
    data = _git(repo, "archive", "--format=tar", commit)
    with tarfile.open(fileobj=io.BytesIO(data)) as archive:
        archive.extractall(destination, filter="data")
