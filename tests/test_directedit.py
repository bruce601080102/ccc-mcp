import json
import sqlite3
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ccc_mcp.infra.errors import BridgeError, PermissionDenied, VersionConflict
from ccc_mcp.interfaces.cli import app
from ccc_mcp.interfaces.server import create_server
from ccc_mcp.service import CCCMCP


def target(bridge):
    return Path(bridge.project("demo")["root"]) / "readme.md"


def proposal(bridge, content="original\ntest\n"):
    file = target(bridge)
    file.write_bytes(b"original\n")
    bridge.configure_write_file("demo", "readme.md")
    sha = bridge.read_workspace("demo", "readme.md")["sha256"]
    return bridge.propose_workspace_edit("demo", "readme.md", content, sha)


def test_direct_edit_requires_allowlist_and_exact_local_approval(bridge, human):
    file = target(bridge)
    file.write_bytes(b"original\r\n")
    sha = bridge.read_workspace("demo", "readme.md")["sha256"]
    content = "original\r\ntest\r\n"
    with pytest.raises(PermissionDenied):
        bridge.propose_workspace_edit("demo", "readme.md", content, sha)
    bridge.configure_write_file("demo", "readme.md")
    edit = bridge.propose_workspace_edit("demo", "readme.md", content, sha)
    assert edit["status"] == "PENDING"
    assert edit["comment_check"]["requires_human_review"] == ["readme.md"]
    assert "--project demo workspace approve " + edit["id"] in edit["approval_command"]
    assert file.read_bytes() == b"original\r\n"
    with pytest.raises(PermissionDenied):
        bridge.write_workspace("demo", edit["id"])
    bridge.approve_workspace_edit_local("demo", edit["id"])
    assert file.read_bytes() == b"original\r\n"
    receipt = bridge.write_workspace("demo", edit["id"])
    assert receipt["status"] == "APPLIED"
    assert receipt["identity"] == "test-principal"
    assert receipt["approved_at"] and receipt["applied_at"]
    assert file.read_bytes() == content.encode()
    assert bridge.read_workspace("demo", "readme.md")["sha256"] == receipt["sha256"]
    archive = CCCMCP(bridge.home).get_workspace_edit("demo", edit["id"], True)
    assert archive["before_content"] == "original\r\n"
    assert archive["content"] == content
    assert bridge.latest("demo")["version"] == 2
    assert bridge.latest("demo")["snapshot"]["workspace_changes"] == [
        {"edit_id": edit["id"], "path": "readme.md", "sha256": receipt["sha256"]}
    ]
    assert bridge.list_tasks("demo") == []
    assert not (bridge.home / "worktrees").exists()


def test_native_terminal_ceremony_cannot_be_skipped(bridge):
    edit = proposal(bridge)
    with pytest.raises(PermissionDenied, match="interactive local operator terminal"):
        bridge.approve_workspace_edit_local("demo", edit["id"])
    assert bridge.get_workspace_edit("demo", edit["id"])["status"] == "PENDING"


@pytest.mark.parametrize("stage", ["propose", "approve", "apply"])
def test_stale_file_rejected_at_every_write_boundary(bridge, human, stage):
    edit = proposal(bridge)
    if stage == "apply":
        bridge.approve_workspace_edit_local("demo", edit["id"])
    file = target(bridge)
    file.write_text("external change\n", encoding="utf-8")
    with pytest.raises(VersionConflict):
        if stage == "propose":
            bridge.propose_workspace_edit("demo", "readme.md", "new\n", edit["before_sha256"])
        elif stage == "approve":
            bridge.approve_workspace_edit_local("demo", edit["id"])
        else:
            bridge.write_workspace("demo", edit["id"])
    assert file.read_text(encoding="utf-8") == "external change\n"


def test_changed_during_local_review_is_rejected(bridge, monkeypatch):
    edit = proposal(bridge)

    def confirm(*args):
        target(bridge).write_text("changed during review", encoding="utf-8")
        return "test-principal"

    monkeypatch.setattr("ccc_mcp.core.approval.confirm_local", confirm)
    with pytest.raises(VersionConflict):
        bridge.approve_workspace_edit_local("demo", edit["id"])
    assert bridge.get_workspace_edit("demo", edit["id"])["status"] == "PENDING"


def test_allowlist_is_exact_persistent_and_revocable(bridge, human):
    edit = proposal(bridge)
    bridge.configure_role("demo", "SA", "chatgpt")
    assert CCCMCP(bridge.home).get_project_settings("demo")["writable_files"] == ["readme.md"]
    with pytest.raises(PermissionDenied):
        bridge.propose_workspace_edit("demo", "other/readme.md", "new")
    bridge.approve_workspace_edit_local("demo", edit["id"])
    bridge.revoke_write_file("demo", "readme.md")
    with pytest.raises(PermissionDenied):
        bridge.write_workspace("demo", edit["id"])
    assert target(bridge).read_bytes() == b"original\n"


@pytest.mark.parametrize(
    "path",
    [
        "../outside.md",
        "C:/outside.md",
        ".env",
        ".git/config",
        ".ccc-mcp/CONTEXT.md",
        ".ssh/id_rsa",
    ],
)
def test_unsafe_or_sensitive_files_cannot_be_allowlisted(bridge, path):
    with pytest.raises(BridgeError):
        bridge.configure_write_file("demo", path)
    assert bridge.get_project_settings("demo")["writable_files"] == []


def test_read_root_does_not_grant_write_access(bridge, tmp_path):
    external = tmp_path / "external"
    external.mkdir()
    (external / "note.md").write_text("reference", encoding="utf-8")
    bridge.configure_read_root("demo", "reference", external)
    with pytest.raises(BridgeError):
        bridge.configure_write_file("demo", str(external / "note.md"))
    with pytest.raises(PermissionDenied):
        bridge.propose_workspace_edit("demo", "note.md", "new")
    assert (external / "note.md").read_text() == "reference"


def test_file_rule_can_be_revoked_even_when_target_is_replaced(bridge):
    proposal(bridge)
    file = target(bridge)
    file.unlink()
    file.mkdir()
    bridge.revoke_write_file("demo", "readme.md")
    assert bridge.get_project_settings("demo")["writable_files"] == []


def test_symlink_cannot_redirect_an_approved_write(bridge, human, tmp_path):
    edit = proposal(bridge)
    bridge.approve_workspace_edit_local("demo", edit["id"])
    outside = tmp_path / "outside.md"
    outside.write_bytes(b"original\n")
    file = target(bridge)
    file.unlink()
    try:
        file.symlink_to(outside)
    except OSError:
        pytest.skip("Host does not allow symlinks")
    with pytest.raises(BridgeError):
        bridge.write_workspace("demo", edit["id"])
    assert outside.read_bytes() == b"original\n"


def test_new_file_is_not_created_until_approved(bridge, human):
    bridge.configure_write_file("demo", "docs/new.md")
    edit = bridge.propose_workspace_edit("demo", "docs/new.md", "new file\n")
    file = target(bridge).parent / "docs/new.md"
    assert not file.exists()
    bridge.approve_workspace_edit_local("demo", edit["id"])
    bridge.write_workspace("demo", edit["id"])
    assert file.read_text() == "new file\n"


def test_original_python_comments_preserved(bridge):
    file = target(bridge).parent / "app.py"
    bridge.configure_write_file("demo", "app.py")
    sha = bridge.read_workspace("demo", "app.py")["sha256"]
    with pytest.raises(PermissionDenied, match="comments"):
        bridge.propose_workspace_edit("demo", "app.py", "def value():\n    return 2\n", sha)
    edit = bridge.propose_workspace_edit(
        "demo", "app.py", "# original comment\ndef value():\n    return 2\n", sha
    )
    assert edit["comment_check"]["ok"]
    assert "return 1" in file.read_text()


def test_proposal_and_application_are_idempotent_without_overwriting_later_edits(bridge, human):
    edit = proposal(bridge)
    duplicate = bridge.propose_workspace_edit(
        "demo", "readme.md", "original\ntest\n", edit["before_sha256"]
    )
    assert duplicate["id"] == edit["id"]
    bridge.approve_workspace_edit_local("demo", edit["id"])
    receipt = bridge.write_workspace("demo", edit["id"])
    assert bridge.write_workspace("demo", edit["id"]) == receipt
    target(bridge).write_text("later edit", encoding="utf-8")
    with pytest.raises(VersionConflict):
        bridge.write_workspace("demo", edit["id"])


@pytest.mark.parametrize("replace_before_crash", [False, True])
def test_retry_recovers_write_interruption(bridge, human, monkeypatch, replace_before_crash):
    from ccc_mcp.infra.security import atomic_write

    edit = proposal(bridge)
    bridge.approve_workspace_edit_local("demo", edit["id"])

    def interrupted(path, content):
        if replace_before_crash:
            atomic_write(path, content)
        raise OSError("simulated interruption")

    monkeypatch.setattr("ccc_mcp.core.directedit.atomic_write", interrupted)
    with pytest.raises(OSError):
        bridge.write_workspace("demo", edit["id"])
    assert bridge.get_workspace_edit("demo", edit["id"])["status"] == "APPLYING"
    monkeypatch.setattr("ccc_mcp.core.directedit.atomic_write", atomic_write)
    receipt = CCCMCP(bridge.home).write_workspace("demo", edit["id"])
    assert receipt["status"] == "APPLIED"
    assert target(bridge).read_bytes() == b"original\ntest\n"


def test_proposed_content_and_history_cannot_be_replaced(bridge):
    edit = proposal(bridge)
    with pytest.raises(sqlite3.IntegrityError):
        bridge.store.execute(
            "UPDATE workspace_edits SET content='tampered' WHERE id=?", (edit["id"],)
        )
    with pytest.raises(sqlite3.IntegrityError):
        bridge.store.execute("DELETE FROM workspace_edits WHERE id=?", (edit["id"],))
    assert bridge.get_workspace_edit("demo", edit["id"])["status"] == "PENDING"
    other = target(bridge).parent.parent / "other-project"
    other.mkdir()
    bridge.register_project(other, "other")
    with pytest.raises(BridgeError, match="not found"):
        bridge.get_workspace_edit("other", edit["id"])


@pytest.mark.parametrize(
    "content",
    ["x" * 200_001, "binary\0data", "sk-" + "a" * 20],
    ids=["oversize", "binary", "credential"],
)
def test_bad_content_rejected(bridge, content):
    edit = proposal(bridge)
    with pytest.raises(BridgeError):
        bridge.propose_workspace_edit("demo", "readme.md", content, edit["before_sha256"])
    assert target(bridge).read_bytes() == b"original\n"


def test_diff_shows_missing_newline_and_cli_commands(bridge, human):
    runner = CliRunner()
    args = ["--home", str(bridge.home), "--project", "demo"]
    target(bridge).write_bytes(b"no newline")
    granted = runner.invoke(app, args + ["config", "write-file", "readme.md"])
    assert granted.exit_code == 0, granted.output
    assert "readme.md" in json.loads(granted.output)["writable_files"]
    sha = bridge.read_workspace("demo", "readme.md")["sha256"]
    edit = bridge.propose_workspace_edit("demo", "readme.md", "no newline\ntest\n", sha)
    assert "\\ No newline at end of file" in edit["diff"]
    approved = runner.invoke(app, args + ["workspace", "approve", edit["id"]])
    assert approved.exit_code == 0, approved.output
    assert json.loads(approved.output)["status"] == "APPROVED"
    shown = runner.invoke(app, args + ["workspace", "show", edit["id"], "--include-content"])
    assert shown.exit_code == 0, shown.output
    assert json.loads(shown.output)["before_content"] == "no newline"
    revoked = runner.invoke(app, args + ["config", "revoke-file", "readme.md"])
    assert revoked.exit_code == 0, revoked.output


async def test_direct_edit_tools_respect_http_readonly(bridge):
    mutable = {t.name for t in await create_server(bridge.home).list_tools()}
    readonly = {t.name for t in await create_server(bridge.home, http=True).list_tools()}
    assert {"get_workspace_edit", "start_project_request", "write_task_file"} <= mutable
    assert "get_workspace_edit" in readonly
    assert not {"propose_workspace_edit", "write_workspace"} & mutable
    assert "start_project_request" not in readonly
    assert not {"approve_workspace_edit", "configure_write_file"} & mutable


async def test_stdio_exposes_read_but_not_direct_edit_tools(bridge, human):
    import os
    import sys

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    target(bridge).write_text("original\n", encoding="utf-8")
    bridge.configure_write_file("demo", "readme.md")
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "ccc_mcp", "--home", str(bridge.home), "serve"],
        env={**os.environ, "PYTHONIOENCODING": "utf-8"},
    )
    async with stdio_client(params) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            read = await session.call_tool(
                "read_workspace", {"project_id": "demo", "path": "readme.md"}
            )
            assert not read.is_error
            names = {tool.name for tool in (await session.list_tools()).tools}
            assert "read_workspace" in names
            assert "write_workspace" not in names
            assert "propose_workspace_edit" not in names
            assert "start_project_request" in names
            assert target(bridge).read_text() == "original\n"
