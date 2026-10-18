import os
import sqlite3
import sys
import threading
import time

import pytest
from conftest import approve, dispatch

from ccc_mcp.agents.base import parse_report, run_process
from ccc_mcp.approval import confirm_local
from ccc_mcp.errors import AgentCancelled, AgentTimeout, BridgeError, PermissionDenied
from ccc_mcp.security import atomic_write, comment_check, redact, safe_path
from ccc_mcp.worker import run_execution


@pytest.mark.parametrize(
    "path",
    [
        "../secret",
        "a/../../secret",
        "C:\\secret",
        "C:secret",
        "/etc/passwd",
        "a:stream",
        "..\\secret",
    ],
)
def test_path_traversal(bridge, path):
    with pytest.raises(BridgeError):
        bridge.source_ref("demo", path)


def test_unregistered_project(bridge):
    with pytest.raises(BridgeError, match="not registered"):
        bridge.bootstrap_project("unknown")


def test_symlink_escape(tmp_path):
    inside = tmp_path / "inside"
    inside.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    link = inside / "link"
    if os.name == "nt":
        # Junctions do not require Windows developer-mode symlink privileges.
        # Paths here come exclusively from pytest's temporary directory.
        import subprocess

        script = (
            "New-Item -ItemType Junction -Path '"
            + str(link).replace("'", "''")
            + "' -Target '"
            + str(outside).replace("'", "''")
            + "'"
        )
        subprocess.run(
            ["powershell", "-NoProfile", "-Command", script], capture_output=True, check=False
        )
        if not link.exists():
            # Python's symlink may still be supported on configured hosts.
            try:
                link.symlink_to(outside, target_is_directory=True)
            except OSError:
                pytest.skip("Host cannot create symlink or junction")
    else:
        link.symlink_to(outside, target_is_directory=True)
    with pytest.raises(BridgeError):
        safe_path(inside, "link/private.txt")


def test_atomic_write_does_not_destroy_old_file(tmp_path, monkeypatch):
    target = tmp_path / "data.md"
    atomic_write(target, "old")

    def interrupted(*args):
        raise OSError("interrupted")

    monkeypatch.setattr("ccc_mcp.security.os.replace", interrupted)
    with pytest.raises(OSError):
        atomic_write(target, "new")
    assert target.read_text() == "old"
    assert not list(tmp_path.glob(".bridge-*"))


def test_comment_preservation():
    old = {"x.py": b"# keep me\nx = 1  # and me\n"}
    assert comment_check(old, {"x.py": b"# keep me\nx = 2  # and me\n# additional\n"})["ok"]
    assert comment_check(old, {"x.py": b"# rewritten\nx = 2  # and me\n"})["violations"] == ["x.py"]
    assert comment_check(old, {})["violations"] == ["x.py"]
    assert comment_check({"x.js": b"// keep\n"}, {"x.js": b"// change\n"})["requires_human_review"]


def test_comment_check_handles_git_text_line_endings():
    assert comment_check({"README.md": b"# keep\n"}, {"README.md": b"# keep\r\n"})["ok"]
    assert comment_check({"x.js": b"// keep\r\n"}, {"x.js": b"// keep\n"})["ok"]
    assert comment_check({"x.js": b"// keep\n"}, {"x.js": b"// changed\r\n"})[
        "requires_human_review"
    ]
    assert comment_check({"x.bin": b"\0a\n"}, {"x.bin": b"\0a\r\n"})["requires_human_review"]
    assert comment_check({"x.bin": b"\xff\n"}, {"x.bin": b"\xff\r\n"})["requires_human_review"]


def test_immutable_decisions_database(bridge, human):
    bridge.create_task("demo", "requirement")
    approve(bridge)
    with pytest.raises(sqlite3.IntegrityError, match="append-only"):
        bridge.store.execute("UPDATE decisions SET status='Rejected'")
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        bridge.store.execute("DELETE FROM context_versions")


def test_noninteractive_approval_rejected():
    with pytest.raises(PermissionDenied, match="interactive"):
        confirm_local("Approve", "TASK-0001")


def test_secret_filters_and_context_rejection(bridge):
    for text in [
        "password=hello",
        "token: abcdef",
        "sk-abcdefghijklmnopqrstuvwxyz",
        "Bearer abc.def.123",
        "-----BEGIN PRIVATE KEY-----\nvery private\n-----END PRIVATE KEY-----",
    ]:
        assert "REDACTED" in redact(text)
    with pytest.raises(BridgeError, match="secret"):
        bridge.create_task("demo", "password=super-secret")


def test_subprocess_timeout_and_cancel(tmp_path):
    command = [sys.executable, "-c", "import time; time.sleep(10)"]
    with pytest.raises(AgentTimeout):
        run_process(command, tmp_path, "", 0.1, lambda: False)
    with pytest.raises(AgentCancelled):
        run_process(command, tmp_path, "", 10, lambda: True)


def test_invalid_structured_output():
    with pytest.raises(BridgeError, match="invalid structured"):
        parse_report("not JSON")


def test_worker_lease_not_marked_interrupted(bridge):
    bridge.create_task("demo", "Analyze")
    queued = dispatch(bridge, fake_delay=0.5)
    thread = threading.Thread(target=run_execution, args=(bridge, queued["execution_id"]))
    thread.start()
    deadline = time.monotonic() + 3
    while time.monotonic() < deadline:
        if bridge.get_task_result("demo", "TASK-0001")[-1]["status"] == "RUNNING":
            break
        time.sleep(0.01)
    assert bridge.recover_workers("demo")["interrupted_executions"] == []
    bridge.cancel_task("demo", "TASK-0001")
    thread.join(timeout=5)
    assert not thread.is_alive()
    assert bridge.get_task_result("demo", "TASK-0001")[-1]["status"] == "CANCELLED"
