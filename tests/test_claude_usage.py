"""Claude's documented status-line JSON is the source of plan limits."""

import json
import shlex
import subprocess
import sys
import time

from ccc_mcp.infra.claude_usage import (
    disable_claude_usage,
    enable_claude_usage,
    read_claude_usage,
)


def test_statusline_capture_preserves_existing_command_and_only_saves_limits(tmp_path):
    home = tmp_path / "ccc"
    claude = tmp_path / ".claude"
    claude.mkdir()
    previous = {
        "type": "command",
        "command": f"{shlex.quote(sys.executable)} -c 'import sys; print(\"existing status\")'",
        "padding": 2,
        "refreshInterval": 30,
    }
    settings = {"model": "sonnet", "statusLine": previous}
    (claude / "settings.json").write_text(json.dumps(settings), encoding="utf-8")

    assert enable_claude_usage(home, claude)["enabled"]
    installed = json.loads((claude / "settings.json").read_text())
    assert installed["model"] == "sonnet"
    assert installed["statusLine"]["refreshInterval"] == 30
    assert installed["statusLine"]["padding"] == 2
    assert installed["statusLine"]["command"] != previous["command"]
    assert enable_claude_usage(home, claude)["enabled"]

    payload = json.dumps(
        {
            "rate_limits": {
                "five_hour": {"used_percentage": 23.5, "resets_at": time.time() + 18000},
                "seven_day": {"used_percentage": 41.2, "resets_at": time.time() + 604800},
            },
            "session_id": "private-session",
            "transcript_path": "/private/transcript",
            "prompt": "private prompt",
        }
    )
    result = subprocess.run(
        [
            sys.executable,
            str(home / "claude-usage-statusline.py"),
            str(home / "claude-usage-snapshot.json"),
            str(home / "claude-usage-install.json"),
        ],
        input=payload,
        text=True,
        capture_output=True,
        check=True,
    )
    assert result.stdout.strip() == "existing status"
    snapshot_text = (home / "claude-usage-snapshot.json").read_text()
    assert "private" not in snapshot_text
    observed = read_claude_usage(home, claude)
    assert [row["used_percent"] for row in observed["windows"]] == [23.5, 41.2]
    assert observed["stale"] is False

    assert disable_claude_usage(home, claude)["enabled"] is False
    assert json.loads((claude / "settings.json").read_text()) == settings
    assert not (home / "claude-usage-snapshot.json").exists()


def test_missing_and_expired_rate_limits_are_not_presented_as_current(tmp_path):
    home = tmp_path / "ccc"
    claude = tmp_path / ".claude"
    assert enable_claude_usage(home, claude)["windows"] == []
    helper = home / "claude-usage-statusline.py"
    snapshot = home / "claude-usage-snapshot.json"
    metadata = home / "claude-usage-install.json"
    subprocess.run(
        [sys.executable, str(helper), str(snapshot), str(metadata)],
        input=json.dumps(
            {
                "rate_limits": {
                    "five_hour": {
                        "used_percentage": 33,
                        "resets_at": time.time() - 1,
                    }
                }
            }
        ),
        text=True,
        capture_output=True,
        check=True,
    )
    assert not snapshot.exists()
    snapshot.write_text(
        json.dumps(
            {
                "captured_at": time.time() - 1000,
                "windows": {
                    "five_hour": {"used_percent": 33, "resets_at": time.time() + 60},
                    "seven_day": {"used_percent": 40, "resets_at": time.time() - 1},
                },
            }
        ),
        encoding="utf-8",
    )
    data = read_claude_usage(home, claude)
    assert data["stale"] is True
    assert [row["name"] for row in data["windows"]] == ["5-hour"]
    disable_claude_usage(home, claude)
    assert "statusLine" not in json.loads((claude / "settings.json").read_text())
