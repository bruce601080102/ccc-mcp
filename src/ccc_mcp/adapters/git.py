import os
import subprocess
from pathlib import Path

from ..infra.errors import BridgeError
from ..infra.security import safe_path


def git(root, *args, check=True):
    # Ignore repository-provided Git hooks for commands initiated by the service.
    result = subprocess.run(
        ["git", "-c", "core.hooksPath=", "-C", str(root), *args],
        capture_output=True,
        stdin=subprocess.DEVNULL,
        timeout=30,
        shell=False,
        check=False,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    if check and result.returncode:
        raise BridgeError(
            "Git operation failed: " + result.stderr.decode("utf-8", "replace")[:1000]
        )
    return result


def revision(root):
    result = git(root, "rev-parse", "HEAD", check=False)
    return result.stdout.decode().strip() if result.returncode == 0 else "UNBORN"


def worktree(home, root, project_id, task_id, worktree_root=None):
    target = safe_path(
        Path(worktree_root) if worktree_root else home / "worktrees", f"{project_id}/{task_id}"
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    if revision(root) == "UNBORN":
        raise BridgeError("Implementation requires an initial Git commit")
    if target.exists():
        entries = git(root, "worktree", "list", "--porcelain").stdout.decode("utf-8", "replace")
        if f"worktree {target.as_posix()}" not in entries.replace("\\", "/"):
            raise BridgeError("Existing directory is not the managed task worktree")
        return target
    git(root, "worktree", "add", "--detach", str(target), "HEAD")
    return target


def tracked_snapshot(root):
    # Always compare to the original commit, including after a blocked revision.
    paths = (
        git(root, "ls-tree", "-r", "--name-only", "-z", "HEAD").stdout.decode("utf-8").split("\0")
    )
    result = {}
    for path in filter(None, paths):
        if path.startswith((".ccc-mcp/", ".agentbridge/")):
            continue
        safe_path(Path(root), path)
        result[path] = git(root, "show", f"HEAD:{path}").stdout
    return result


def working_snapshot(root):
    paths = (
        git(root, "ls-files", "--cached", "--others", "--exclude-standard", "-z")
        .stdout.decode("utf-8")
        .split("\0")
    )
    result = {}
    for path in filter(None, paths):
        if path.startswith((".ccc-mcp/", ".agentbridge/")):
            continue
        target = safe_path(Path(root), path)
        if target.is_file():
            result[path] = target.read_bytes()
    return result
