import getpass
import os
import subprocess
import sys

from ..infra.errors import PermissionDenied


def local_identity():
    if os.name == "nt":
        result = subprocess.run(
            ["whoami", "/user", "/fo", "csv", "/nh"],
            capture_output=True,
            timeout=10,
            shell=False,
            check=False,
        )
        if result.returncode:
            raise PermissionDenied("Cannot determine Windows security principal")
        return result.stdout.decode("utf-8", "replace").strip()
    return f"uid:{os.getuid()} user:{getpass.getuser()}"


def confirm_local(description, challenge):
    if not sys.stdin.isatty() or not sys.stdout.isatty():
        raise PermissionDenied("Human approval requires an interactive local operator terminal")
    identity = local_identity()
    print(description)
    print(f"\nOS principal: {identity}")
    typed = input(f"審閱後輸入 {challenge} 以確認（其他輸入取消）：")
    if typed != challenge:
        raise PermissionDenied("Local approval cancelled")
    return identity


def confirm_web(challenge, typed):
    """Confirmation for a request already authenticated as this process's own local
    Web UI session (loopback-bound, per-launch session token). The web layer
    (interfaces/web.py) is responsible for verifying that token before ever calling
    anything that uses this as its approval — this function only re-checks, server
    side, that the operator typed back the exact challenge shown in the browser,
    the same confirmation strength `confirm_local` gets from a terminal.
    """
    if typed != challenge:
        raise PermissionDenied("Local approval cancelled")
    return local_identity()
