"""Host test evidence, kept apart from commit state and structure verification.

Tests run in a temporary copy, never inside the user's repository:
  - a commit is extracted with `git archive` (no repository writes);
  - a working tree is copied file by file, and only files that passed every exclusion check.
The result stores the exit code, a short summary line, and a SHA-256 of the full output. The full
output itself is not kept, so anything a test prints cannot leak into the report.
"""

import hashlib
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from . import gitio


def run_host_tests(repo, snapshot, argv, work_root=None, timeout=180):
    repo = Path(repo).resolve()
    workdir = Path(tempfile.mkdtemp(prefix="archgraph-run-", dir=work_root))
    try:
        if snapshot.mode == "commit":
            if snapshot.head is None:
                raise RuntimeError("cannot run host tests on an empty snapshot")
            gitio.export_commit(repo, snapshot.head, workdir)
            scope = f"commit:{snapshot.head}"
        else:
            for state in snapshot.files:
                source = repo / state.path
                if state.excluded is None and state.worktree_blob is not None and source.is_file():
                    target = workdir / state.path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, target)
            scope = "worktree-copy"
        env = {**os.environ, "PYTHONDONTWRITEBYTECODE": "1"}
        proc = subprocess.run(
            argv,
            cwd=workdir,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            env=env,
            timeout=timeout,
            check=False,
        )
        output = proc.stdout.decode("utf-8", "replace")
        lines = [line for line in output.splitlines() if line.strip()]
        return {
            "state": "PASSED" if proc.returncode == 0 else "FAILED",
            "exit_code": proc.returncode,
            "summary": lines[-1][:200] if lines else "",
            "stdout_sha256": hashlib.sha256(output.encode()).hexdigest(),
            "scope": scope,
            "command": [Path(argv[0]).name, *argv[1:]],
        }
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
