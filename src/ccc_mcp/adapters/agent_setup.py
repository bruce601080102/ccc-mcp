"""Install and log in to native codex/claude CLIs without touching the shell's PATH.

Replaces the install/login portion of scripts/setup-real-agents.sh and
scripts/enable-real-agents.sh: everything here calls the resolved absolute
executable path directly, the same trick `ccc-mcp connect` uses, so it never
needs the caller's interactive shell PATH to be pre-configured.
"""

import os
import shutil
import subprocess
from pathlib import Path

from ..infra.errors import BridgeError
from .agents.base import native_executable

NPM_PACKAGE = {"codex": "@openai/codex@latest", "claude": "@anthropic-ai/claude-code"}
LOGIN_ARGS = {"codex": ["login"], "claude": ["auth", "login"]}
STATUS_ARGS = {"codex": ["login", "status"], "claude": ["auth", "status", "--text"]}


def _resolve_or_none(provider):
    try:
        return native_executable(provider)
    except BridgeError:
        return None


def _logged_in(path, provider):
    result = subprocess.run(
        [path, *STATUS_ARGS[provider]],
        capture_output=True,
        stdin=subprocess.DEVNULL,
        timeout=15,
        shell=False,
        check=False,
    )
    return result.returncode == 0


def _node_major_version():
    result = subprocess.run(
        ["node", "-p", "process.versions.node.split('.')[0]"],
        capture_output=True,
        stdin=subprocess.DEVNULL,
        timeout=15,
        shell=False,
        check=False,
    )
    try:
        return int(result.stdout.decode().strip())
    except ValueError:
        return None


def _ensure_npm():
    if shutil.which("npm"):
        return
    if not shutil.which("winget") and not shutil.which("winget.exe"):
        raise BridgeError("npm is required, and WinGet is unavailable; install Node.js and retry")
    subprocess.run(
        [
            "winget", "install", "--id", "OpenJS.NodeJS.LTS", "--exact", "--source", "winget",
            "--accept-package-agreements", "--accept-source-agreements",
        ],
        shell=False,
        check=True,
    )
    # This process's own PATH, not the caller's shell: winget installing Node.js
    # doesn't make it visible to an already-running Python process otherwise.
    node_dir = Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "nodejs"
    os.environ["PATH"] = f"{node_dir}{os.pathsep}{os.environ['PATH']}"
    if not shutil.which("npm"):
        raise BridgeError("Node.js was installed, but npm is not visible yet; open a new terminal and retry")


def setup_provider(provider: str) -> dict:
    """Resolve, install via npm if missing, and log in to one CLI provider."""
    if provider not in NPM_PACKAGE:
        raise BridgeError(f"Unsupported provider: {provider}")
    path = _resolve_or_none(provider)
    if path is None:
        # _ensure_npm() first: on a machine with no Node.js at all, this is what
        # actually installs it (via winget). Checking the Node version before this
        # ran meant a brand-new machine failed here before auto-install ever had a
        # chance to run — a one-line Node install would have fixed it, but the
        # error told the user to do it manually instead.
        _ensure_npm()
        if provider == "claude":
            node_major = _node_major_version()
            if node_major is None or node_major < 22:
                raise BridgeError(
                    "Claude Code npm installation requires Node.js 22+; the Node.js "
                    "already installed here is older — upgrade it manually and retry"
                )
        subprocess.run(["npm", "install", "-g", NPM_PACKAGE[provider]], shell=False, check=True)
        path = native_executable(provider)
    if not _logged_in(path, provider):
        subprocess.run([path, *LOGIN_ARGS[provider]], shell=False, check=False)
    if not _logged_in(path, provider):
        raise BridgeError(f"{provider} login is still incomplete")
    return {"provider": provider, "path": path, "logged_in": True}
