import hashlib
import io
import json
import os
import re
import tempfile
import tokenize
from collections import Counter
from pathlib import Path, PureWindowsPath

from .errors import BridgeError


def safe_path(root: Path, relative: str) -> Path:
    """Reject traversal, symlinks and Windows junction/reparse points, even inside root."""
    win = PureWindowsPath(relative)
    path = Path(relative)
    if (
        not relative
        or path.is_absolute()
        or win.is_absolute()
        or win.drive
        or ".." in path.parts
        or ".." in win.parts
        or ":" in relative
    ):
        raise BridgeError("Unsafe relative path")
    root = root.resolve()
    current = root
    for part in path.parts:
        current = current / part
        if current.is_symlink() or (hasattr(current, "is_junction") and current.is_junction()):
            raise BridgeError("Symlink/junction path is forbidden")
    resolved = current.resolve()
    if not resolved.is_relative_to(root):
        raise BridgeError("Path escapes registered root")
    return current


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".bridge-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp, path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def canonical(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def redact(text: str) -> str:
    text = re.sub(
        r"-----BEGIN [^-]*PRIVATE KEY-----.*?-----END [^-]*PRIVATE KEY-----",
        "[REDACTED PRIVATE KEY]",
        text,
        flags=re.DOTALL,
    )
    text = re.sub(r"\b(?:sk-[\w-]{12,}|gh[pousr]_[\w]{20,})\b", "[REDACTED]", text)
    text = re.sub(r"(?i)(bearer\s+)[\w.\-]+", r"\1[REDACTED]", text)
    return re.sub(
        r"""(?i)(["']?(?:api[_-]?key|token|password|secret)["']?\s*[:=]\s*["']?)[^\s,"'<>}]+""",
        r"\1[REDACTED]",
        text,
    )


def reject_secrets(value) -> None:
    serialized = canonical(value)
    if redact(serialized) != serialized:
        raise BridgeError("Possible secret detected; remove credentials before saving")


def comment_check(before: dict[str, bytes], after: dict[str, bytes]) -> dict:
    """Exact Python comment tokens; other changed languages require human review."""
    violations, uncertain = [], []
    for path, old in before.items():
        new = after.get(path, b"")
        if old == new:
            continue
        if Path(path).suffix != ".py":
            uncertain.append(path)
            continue
        try:

            def comments(data):
                return Counter(
                    t.string
                    for t in tokenize.tokenize(io.BytesIO(data).readline)
                    if t.type == tokenize.COMMENT
                )

            if comments(old) - comments(new):
                violations.append(path)
        except (tokenize.TokenError, SyntaxError, UnicodeError):
            uncertain.append(path)
    return {
        "ok": not violations and not uncertain,
        "violations": violations,
        "requires_human_review": uncertain,
    }
