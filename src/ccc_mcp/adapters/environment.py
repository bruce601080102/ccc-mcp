"""Environment facts (Python version, git availability) shared by the CLI
``doctor`` command and the web console's onboarding status endpoint, so both
read the same check instead of keeping two copies of the detection logic.

This module returns raw facts only (no bilingual message text): each caller
decides how to phrase a failure for its own audience.
"""

import shutil
import subprocess
import sys


def python_version() -> str:
    return sys.version.split()[0]


def git_check() -> dict:
    """Raw git availability/version facts.

    Returns ``{"available": True, "path", "version"}`` on success,
    ``{"available": False, "path": None}`` when git is not on PATH, or
    ``{"available": False, "path", "error"}`` if invoking it failed.
    """
    path = shutil.which("git")
    if not path:
        return {"available": False, "path": None}
    try:
        result = subprocess.run(
            [path, "--version"],
            capture_output=True,
            stdin=subprocess.DEVNULL,
            timeout=15,
            shell=False,
            check=False,
        )
        return {
            "available": result.returncode == 0,
            "path": path,
            "version": result.stdout.decode("utf-8", "replace").strip(),
        }
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"available": False, "path": path, "error": type(exc).__name__}
