"""Standalone Claude Code status-line bridge.

Claude Code passes session JSON on stdin. Keep only the documented rate-limit
fields in ccc-mcp's local snapshot, then run the user's previous status line.
This file is copied to the ccc-mcp home so it needs only Python's stdlib.
"""

import json
import math
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path


def _window(value, now):
    if not isinstance(value, dict):
        return None
    used = value.get("used_percentage")
    reset = value.get("resets_at")
    if (
        isinstance(used, bool)
        or not isinstance(used, (int, float))
        or not math.isfinite(used)
        or not 0 <= used <= 100
        or isinstance(reset, bool)
        or not isinstance(reset, (int, float))
        or not math.isfinite(reset)
        or reset <= now
    ):
        return None
    return {"used_percent": round(float(used), 2), "resets_at": int(reset)}


def capture(payload, snapshot_path):
    if len(payload) > 2_000_000:
        return {}
    try:
        data = json.loads(payload)
        limits = data.get("rate_limits", {})
        if not isinstance(limits, dict):
            return {}
    except (ValueError, AttributeError):
        return {}
    now = time.time()
    windows = {
        name: window
        for name in ("five_hour", "seven_day")
        if (window := _window(limits.get(name), now)) is not None
    }
    if not windows:
        return {}
    snapshot_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix=".claude-usage-", dir=snapshot_path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump({"captured_at": now, "windows": windows}, handle)
            handle.write("\n")
        os.replace(temp, snapshot_path)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)
    return windows


def main():
    if len(sys.argv) != 3:
        return 2
    payload = sys.stdin.buffer.read(2_000_001)
    snapshot_path = Path(sys.argv[1])
    metadata_path = Path(sys.argv[2])
    try:
        windows = capture(payload, snapshot_path)
    except (OSError, ValueError):
        windows = {}
    try:
        metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
        previous = metadata.get("previous_status_line") or {}
        command = previous.get("command") if previous.get("type") == "command" else None
    except (OSError, ValueError, AttributeError):
        command = None
    if isinstance(command, str) and command:
        try:
            result = subprocess.run(
                command,
                input=payload,
                shell=True,
                capture_output=True,
                timeout=10,
                check=False,
            )
            sys.stdout.buffer.write(result.stdout)
            sys.stderr.buffer.write(result.stderr)
        except (OSError, subprocess.TimeoutExpired):
            pass
    elif windows:
        parts = [
            f"{label}: {100 - windows[key]['used_percent']:g}% left"
            for key, label in (("five_hour", "5h"), ("seven_day", "7d"))
            if key in windows
        ]
        print("Claude · " + " · ".join(parts))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
