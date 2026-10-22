import subprocess
import sys
import threading
import time

import pytest

from ccc_mcp.interfaces.cli import (
    _package_signature,
    _run_tunnel_with_reload,
    _terminate_tunnel_process,
)


def test_package_signature_returns_a_stable_shape():
    version, mtime = _package_signature()
    assert version is None or isinstance(version, str)
    assert isinstance(mtime, float) and mtime > 0
    assert _package_signature() == (version, mtime)


def test_terminate_tunnel_process_stops_a_running_process():
    process = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    assert process.poll() is None
    _terminate_tunnel_process(process)
    assert process.poll() is not None


@pytest.mark.skipif(sys.platform == "win32", reason="relies on a POSIX shebang script")
def test_run_tunnel_with_reload_restarts_once_then_returns_cleanly(tmp_path, monkeypatch):
    # The fake client is a real interpreter spawn, so its own startup latency is not
    # under test control. A signature that flips on a fixed poll count would race that
    # latency (flaky). Instead, a background thread flips a signature file only once the
    # first process has observably started (its own marker file exists), so the restart
    # is triggered by a real event, never a timing guess.
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    sig_file = tmp_path / "signature"
    sig_file.write_text("A")
    client = tmp_path / "fake-tunnel-client"
    client.write_text(
        f"#!{sys.executable}\n"
        "import pathlib, sys, time\n"
        f"runs_dir = pathlib.Path({str(runs_dir)!r})\n"
        "count = len(list(runs_dir.iterdir()))\n"
        "(runs_dir / str(count)).touch()\n"
        "if count == 0:\n"
        "    time.sleep(30)\n"
        "else:\n"
        "    sys.exit(0)\n"
    )
    client.chmod(0o755)

    monkeypatch.setattr(
        "ccc_mcp.interfaces.cli._package_signature", lambda: sig_file.read_text()
    )

    def flip_signature_once_first_process_has_started():
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if any(runs_dir.iterdir()):
                sig_file.write_text("B")
                return
            time.sleep(0.02)
        raise AssertionError("fake client never started")

    flipper = threading.Thread(target=flip_signature_once_first_process_has_started)
    flipper.start()
    try:
        _run_tunnel_with_reload(client, "profile", tmp_path, {}, poll_seconds=0.02)
    finally:
        flipper.join(timeout=10)

    assert sorted(p.name for p in runs_dir.iterdir()) == ["0", "1"]


@pytest.mark.skipif(sys.platform == "win32", reason="relies on a POSIX shebang script")
def test_run_tunnel_with_reload_propagates_a_genuine_failure(tmp_path, monkeypatch):
    client = tmp_path / "fake-tunnel-client"
    client.write_text(f"#!{sys.executable}\nimport sys\nsys.exit(1)\n")
    client.chmod(0o755)

    monkeypatch.setattr(
        "ccc_mcp.interfaces.cli._package_signature", lambda: ("A", 1.0)
    )

    from ccc_mcp.infra.errors import BridgeError

    with pytest.raises(BridgeError, match="exited with an error"):
        _run_tunnel_with_reload(client, "profile", tmp_path, {}, poll_seconds=0.05)
