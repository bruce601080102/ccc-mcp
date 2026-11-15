"""Sensitive-path and secret-content exclusion for the architecture-graph prototype.

Order matters: path exclusion is decided from the file NAME alone and runs before any file
content is read. Content scanning runs only on files that passed the path check, and a
suspect file's content is never stored, reported, or put into the graph.
"""

import fnmatch
import re
from pathlib import PurePosixPath

MAX_BYTES = 1_000_000

DENIED_DIRECTORIES = {
    ".git",
    ".ccc-mcp",
    ".agentbridge",
    ".ssh",
    ".aws",
    ".gnupg",
    ".codex",
    ".claude",
    ".venv",
    "venv",
    "node_modules",
    "__pycache__",
}

SENSITIVE_NAME_PATTERNS = (
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "*.kdbx",
    "id_rsa",
    "id_rsa.*",
    "id_ed25519",
    "id_ed25519.*",
    "credentials",
    "credentials.*",
    "secrets.*",
    "*.secret",
)

# Conservative content patterns. They are used only to decide exclusion, never reported.
SECRET_PATTERNS = {
    "PRIVATE_KEY_BLOCK": re.compile(rb"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    "AWS_ACCESS_KEY_ID": re.compile(rb"\bAKIA[0-9A-Z]{16}\b"),
    "ASSIGNED_SECRET": re.compile(
        rb"(?i)\b(api[_-]?key|secret|token|password)\b\s*[:=]\s*['\"][^'\"\s]{8,}['\"]"
    ),
}


def path_exclusion(relpath):
    """Return a reason if the path must not be read or indexed, else None. Name-only check."""
    normalised = relpath.replace("\\", "/")
    path = PurePosixPath(normalised)
    if path.is_absolute() or ".." in path.parts or not normalised:
        return "PATH_OUTSIDE_REPOSITORY"
    for part in path.parts:
        if part.lower() in DENIED_DIRECTORIES:
            return "DENIED_DIRECTORY"
    name = path.name.lower()
    for pattern in SENSITIVE_NAME_PATTERNS:
        if fnmatch.fnmatch(name, pattern):
            return "SENSITIVE_FILENAME"
    return None


def mode_exclusion(git_mode):
    """Symlinks and submodules are never read as ordinary text."""
    if git_mode in ("120000", "160000"):
        return "SYMLINK_OR_SUBMODULE"
    return None


def looks_binary(data):
    return b"\x00" in data[:8000]


def content_exclusion(data):
    """Return `CONTENT_SECRET_SUSPECT:<pattern name>` if the bytes look like a credential."""
    for name, pattern in SECRET_PATTERNS.items():
        if pattern.search(data):
            return f"CONTENT_SECRET_SUSPECT:{name}"
    return None
