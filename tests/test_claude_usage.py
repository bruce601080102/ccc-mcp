"""Claude's documented status-line JSON is the source of plan limits."""

import json
import os
import subprocess
import sys
import time

import pytest

from ccc_mcp.infra.claude_usage import (
    disable_claude_usage,
    enable_claude_usage,
    probe_claude_usage,
    read_claude_usage,
)
from ccc_mcp.infra.errors import BridgeError


def test_statusline_capture_preserves_existing_command_and_only_saves_limits(tmp_path):
    home = tmp_path / "ccc"
    claude = tmp_path / ".claude"
    claude.mkdir()
    previous = {
        "type": "command",
        "command": "echo existing status",
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


FAKE_CLAUDE = """#!{python}
import json, os, sys, time
mode = os.environ["FAKE_MODE"]
open(os.environ["FAKE_PID_FILE"], "w").write(str(os.getpid()))
if mode == "login":
    print("Please run /login to continue"); sys.stdout.flush(); time.sleep(60)
if os.environ.get("FAKE_TRUST") == "1":
    print("Do you trust this folder?  1. Yes, I trust this folder"); sys.stdout.flush()
    input()
print("ready >"); sys.stdout.flush()
input()
if mode == "ok":
    now = time.time()
    with open(os.environ["FAKE_SNAPSHOT"], "w") as handle:
        json.dump({{"captured_at": now, "windows": {{
            "five_hour": {{"used_percent": 18.0, "resets_at": now + 18000}},
            "seven_day": {{"used_percent": 32.0, "resets_at": now + 604800}},
        }}}}, handle)
time.sleep(60)
"""


def install_fake_claude(tmp_path, monkeypatch, mode, trust=True):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    script = bin_dir / "claude"
    script.write_text(FAKE_CLAUDE.format(python=sys.executable), encoding="utf-8")
    script.chmod(0o755)
    home = tmp_path / "ccc"
    claude = tmp_path / ".claude"
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("FAKE_MODE", mode)
    monkeypatch.setenv("FAKE_TRUST", "1" if trust else "0")
    monkeypatch.setenv("FAKE_SNAPSHOT", str(home / "claude-usage-snapshot.json"))
    monkeypatch.setenv("FAKE_PID_FILE", str(tmp_path / "fake.pid"))
    return home, claude


posix_only = pytest.mark.skipif(os.name == "nt", reason="the PTY probe is POSIX-only")


@posix_only
def test_probe_reads_limits_without_a_user_terminal_and_cleans_up(tmp_path, monkeypatch):
    home, claude = install_fake_claude(tmp_path, monkeypatch, "ok")
    enable_claude_usage(home, claude)
    result = probe_claude_usage(home, claude, timeout=20)
    assert result["probe"] == {"ok": True, "reason": "updated"}
    assert [row["used_percent"] for row in result["windows"]] == [18.0, 32.0]
    pid = int((tmp_path / "fake.pid").read_text())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)
    assert (home / "usage-probe").is_dir()


@posix_only
def test_probe_skips_a_snapshot_that_is_already_fresh(tmp_path, monkeypatch):
    home, claude = install_fake_claude(tmp_path, monkeypatch, "ok")
    monkeypatch.setenv("PATH", str(tmp_path / "nowhere"))
    enable_claude_usage(home, claude)
    now = time.time()
    (home / "claude-usage-snapshot.json").write_text(
        json.dumps({"captured_at": now, "windows": {"five_hour": {"used_percent": 5, "resets_at": now + 60}}}),
        encoding="utf-8",
    )
    result = probe_claude_usage(home, claude, timeout=1)
    assert result["probe"] == {"ok": True, "reason": "recent"}


@posix_only
def test_probe_names_each_failure_instead_of_blaming_login(tmp_path, monkeypatch):
    home, claude = install_fake_claude(tmp_path, monkeypatch, "silent")
    assert probe_claude_usage(home, claude)["probe"]["reason"] == "sync_disabled"
    enable_claude_usage(home, claude)
    assert probe_claude_usage(home, claude, timeout=6)["probe"] == {
        "ok": False,
        "reason": "no_rate_limit_data",
    }
    monkeypatch.setenv("FAKE_MODE", "login")
    assert probe_claude_usage(home, claude, timeout=10)["probe"]["reason"] == "not_signed_in"

    def missing(_name):
        raise BridgeError("Native claude executable unavailable")

    monkeypatch.setattr("ccc_mcp.adapters.agents.base.native_executable", missing)
    assert probe_claude_usage(home, claude)["probe"]["reason"] == "claude_unavailable"
