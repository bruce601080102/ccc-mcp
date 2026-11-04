"""Opt-in local capture of Claude Code's documented status-line rate limits."""

import json
import math
import os
import re
import shlex
import subprocess
import sys
import threading
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


_PROBE_LOCK = threading.Lock()
_PROBE_MIN_INTERVAL = 60
_ANSI = re.compile(r"\x1b(?:\[[0-9;?>]*[A-Za-z]|\][^\x07]*\x07)")


def _probe_outcome(ok: bool, reason: str) -> dict:
    return {"ok": ok, "reason": reason}


def _drive_claude_session(executable, cwd: Path, snapshot_path: Path, timeout: float) -> dict:
    """Run one tiny interactive Claude Code turn inside a pseudo-terminal.

    The status line (and so the plan-limit snapshot) only refreshes in an interactive
    session, which a headless `--print` call never is. Driving a real TUI in a PTY is the
    only no-credential way to get those numbers without asking the user to open a terminal.
    """
    import fcntl
    import pty
    import select
    import signal
    import struct
    import termios

    before = snapshot_path.stat().st_mtime if snapshot_path.exists() else 0
    cwd.mkdir(parents=True, exist_ok=True)
    pid, fd = pty.fork()
    if pid == 0:
        try:
            os.chdir(cwd)
            os.environ["TERM"] = "xterm-256color"
            os.execv(executable, [executable, "--model", "haiku"])
        finally:
            os._exit(127)
    fcntl.ioctl(fd, termios.TIOCSWINSZ, struct.pack("HHHH", 40, 120, 0, 0))
    start = time.monotonic()
    last_output = start
    screen = ""
    trusted = sent = False
    outcome = _probe_outcome(False, "timeout")
    try:
        while time.monotonic() - start < timeout:
            ready, _, _ = select.select([fd], [], [], 0.4)
            if ready:
                try:
                    data = os.read(fd, 65536)
                except OSError:
                    outcome = _probe_outcome(False, "session_ended")
                    break
                if not data:
                    outcome = _probe_outcome(False, "session_ended")
                    break
                screen = (screen + _ANSI.sub("", data.decode("utf-8", "ignore")))[-6000:]
                last_output = time.monotonic()
            if snapshot_path.exists() and snapshot_path.stat().st_mtime > before:
                outcome = _probe_outcome(True, "updated")
                break
            lowered = screen.lower()
            if not trusted and "trust this folder" in lowered:
                os.write(fd, b"\r")
                trusted, screen, last_output = True, "", time.monotonic()
                continue
            if not sent and any(m in lowered for m in ("/login", "select login method")):
                outcome = _probe_outcome(False, "not_signed_in")
                break
            quiet = time.monotonic() - last_output > 2.5 and time.monotonic() - start > 3
            if not sent and quiet:
                os.write(fd, b"reply with: ok")
                time.sleep(0.4)
                os.write(fd, b"\r")
                sent, last_output = True, time.monotonic()
        else:
            outcome = _probe_outcome(False, "no_rate_limit_data" if sent else "timeout")
    finally:
        for sig in (signal.SIGTERM, signal.SIGKILL):
            try:
                os.killpg(pid, sig)
            except (ProcessLookupError, PermissionError):
                pass
            deadline = time.monotonic() + 2
            reaped = False
            while time.monotonic() < deadline:
                try:
                    done, _ = os.waitpid(pid, os.WNOHANG)
                except ChildProcessError:
                    reaped = True
                    break
                if done:
                    reaped = True
                    break
                time.sleep(0.05)
            if reaped:
                break
        try:
            os.close(fd)
        except OSError:
            pass
    return outcome


def probe_claude_usage(home: Path, claude_home: Path | None = None, timeout: float = 45) -> dict:
    """Refresh the Claude plan-limit snapshot without the user opening a terminal.

    Costs one trivial Haiku message from the signed-in Claude Code account. The probe runs
    in ccc-mcp's own empty `usage-probe` folder, so confirming Claude Code's "trust this
    folder" prompt there trusts nothing of the user's.
    """
    state = read_claude_usage(home, claude_home)
    if not state["enabled"]:
        return {**state, "probe": _probe_outcome(False, "sync_disabled")}
    captured = state.get("captured_at")
    if state["windows"] and captured and time.time() - captured < _PROBE_MIN_INTERVAL:
        return {**state, "probe": _probe_outcome(True, "recent")}
    try:
        import pty  # noqa: F401 - POSIX only
    except ImportError:
        return {**state, "probe": _probe_outcome(False, "unsupported")}
    from ..adapters.agents import base

    try:
        executable = base.native_executable("claude")
    except BridgeError:
        return {**state, "probe": _probe_outcome(False, "claude_unavailable")}
    if not _PROBE_LOCK.acquire(blocking=False):
        return {**state, "probe": _probe_outcome(False, "busy")}
    try:
        _, _, _, snapshot_path = _paths(home, claude_home)
        outcome = _drive_claude_session(executable, home / "usage-probe", snapshot_path, timeout)
    finally:
        _PROBE_LOCK.release()
    return {**read_claude_usage(home, claude_home), "probe": outcome}


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
