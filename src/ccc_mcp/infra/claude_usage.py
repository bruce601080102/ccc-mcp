"""Opt-in local capture of Claude Code's documented status-line rate limits."""

import json
import math
import os
import shlex
import subprocess
import sys
import time
from pathlib import Path

from .errors import BridgeError
from .security import atomic_write

_HELPER_SOURCE = Path(__file__).with_name("claude_usage_statusline.py")
_WINDOWS = (("five_hour", "5-hour"), ("seven_day", "7-day"))


def _paths(home: Path, claude_home: Path | None):
    claude_home = claude_home or Path.home() / ".claude"
    return (
        claude_home / "settings.json",
        home / "claude-usage-statusline.py",
        home / "claude-usage-install.json",
        home / "claude-usage-snapshot.json",
    )


def _settings(path: Path):
    if not path.exists():
        return {}
    if path.stat().st_size > 1_000_000:
        raise BridgeError("Claude Code settings are too large")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (ValueError, UnicodeError) as exc:
        raise BridgeError("Claude Code settings.json is invalid") from exc
    if not isinstance(data, dict):
        raise BridgeError("Claude Code settings.json must be an object")
    return data


def _metadata(path: Path):
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _installed(settings, metadata):
    current = settings.get("statusLine")
    return bool(
        metadata.get("command")
        and isinstance(current, dict)
        and current.get("command") == metadata["command"]
    )


def read_claude_usage(home: Path, claude_home: Path | None = None) -> dict:
    settings_path, _, metadata_path, snapshot_path = _paths(home, claude_home)
    try:
        installed = _installed(_settings(settings_path), _metadata(metadata_path))
    except (BridgeError, OSError, AttributeError):
        installed = False
    result = {"enabled": installed, "source": "Claude Code status line", "windows": []}
    if not installed:
        return result
    try:
        data = json.loads(snapshot_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return result
    now = time.time()
    if not isinstance(data, dict):
        return result
    captured = data.get("captured_at")
    if (
        isinstance(captured, bool)
        or not isinstance(captured, (int, float))
        or not math.isfinite(captured)
        or captured > now + 60
    ):
        return result
    result["captured_at"] = captured
    result["stale"] = now - captured > 900
    windows = data.get("windows", {})
    if not isinstance(windows, dict):
        return result
    for key, label in _WINDOWS:
        row = windows.get(key)
        if not isinstance(row, dict):
            continue
        used, resets = row.get("used_percent"), row.get("resets_at")
        if (
            isinstance(used, bool)
            or not isinstance(used, (int, float))
            or not math.isfinite(used)
            or not 0 <= used <= 100
            or isinstance(resets, bool)
            or not isinstance(resets, (int, float))
            or not math.isfinite(resets)
            or resets <= now
        ):
            continue
        result["windows"].append(
            {
                "name": label,
                "used_percent": used,
                "resets_at": resets,
            }
        )
    return result


def enable_claude_usage(home: Path, claude_home: Path | None = None) -> dict:
    settings_path, helper_path, metadata_path, _ = _paths(home, claude_home)
    settings = _settings(settings_path)
    metadata = _metadata(metadata_path)
    if _installed(settings, metadata):
        return read_claude_usage(home, claude_home)
    previous = settings.get("statusLine")
    if previous is not None and (
        not isinstance(previous, dict)
        or previous.get("type") != "command"
        or not isinstance(previous.get("command"), str)
    ):
        raise BridgeError("Existing Claude status line cannot be chained safely")
    if isinstance(previous, dict) and str(helper_path) in previous["command"]:
        raise BridgeError(
            "Existing ccc-mcp status line metadata is missing; restore Claude settings first"
        )
    argv = [
        str(value)
        for value in (
            sys.executable,
            helper_path,
            home / "claude-usage-snapshot.json",
            metadata_path,
        )
    ]
    command = (
        subprocess.list2cmdline(argv)
        if os.name == "nt"
        else " ".join(shlex.quote(value) for value in argv)
    )
    helper_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(helper_path, _HELPER_SOURCE.read_text(encoding="utf-8"))
    atomic_write(
        metadata_path,
        json.dumps(
            {
                "command": command,
                "previous_status_line": previous,
            },
            ensure_ascii=False,
        )
        + "\n",
    )
    # Preserve all other Claude settings and the previous status-line refresh rate.
    status_line = {"type": "command", "command": command}
    if isinstance(previous, dict):
        for field in ("padding", "hideVimModeIndicator", "refreshInterval"):
            if field in previous:
                status_line[field] = previous[field]
    settings["statusLine"] = status_line
    atomic_write(settings_path, json.dumps(settings, ensure_ascii=False, indent=2) + "\n")
    return read_claude_usage(home, claude_home)


def disable_claude_usage(home: Path, claude_home: Path | None = None) -> dict:
    settings_path, helper_path, metadata_path, snapshot_path = _paths(home, claude_home)
    settings = _settings(settings_path)
    metadata = _metadata(metadata_path)
    if _installed(settings, metadata):
        previous = metadata.get("previous_status_line")
        if previous is None:
            settings.pop("statusLine", None)
        else:
            settings["statusLine"] = previous
        atomic_write(settings_path, json.dumps(settings, ensure_ascii=False, indent=2) + "\n")
    for path in (helper_path, metadata_path, snapshot_path):
        path.unlink(missing_ok=True)
    return read_claude_usage(home, claude_home)
