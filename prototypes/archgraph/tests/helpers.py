"""Test-only helpers. Git writes happen only inside temporary repositories created by the tests."""

import os
import subprocess
from pathlib import Path


def git(repo, *args):
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_OPTIONAL_LOCKS": "0"}
    result = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, env=env, check=True
    )
    return result.stdout.decode("utf-8", "replace")


def write(repo, relative, text):
    target = Path(repo) / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")


def new_repo(root):
    repo = Path(root) / "repo"
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    git(repo, "config", "user.name", "test")
    git(repo, "config", "user.email", "test@example.invalid")
    return repo
