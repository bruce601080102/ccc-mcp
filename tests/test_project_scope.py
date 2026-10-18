from pathlib import Path

import pytest
from conftest import approve

from ccc_mcp.errors import BridgeError, PermissionDenied, VersionConflict
from ccc_mcp.models import AgentReport
from ccc_mcp.service import CCCMCP


def root(bridge):
    return Path(bridge.project("demo")["root"])


def batch(bridge):
    bridge.configure_write_root("demo", ".")
    ids = []
    for name in ("first.md", "second.md"):
        (root(bridge) / name).write_text("original\n", encoding="utf-8")
        sha = bridge.read_workspace("demo", name)["sha256"]
        ids.append(bridge.propose_workspace_edit("demo", name, "original\ntest\n", sha)["id"])
    return bridge.create_workspace_batch("demo", ids)


def task(bridge):
    bridge.configure_role("demo", "SD/RD", "chatgpt")
    bridge.configure_workspace_mode("demo", "project")
    bridge.configure_write_root("demo", ".")
    bridge.create_task("demo", "Change multiple project files")
    approve(bridge)
    return bridge.dispatch_agent("demo", "TASK-0001", "auto", "SD/RD", 1, "Implement")


def test_directory_grant_is_recursive_bounded_and_persistent(bridge):
    (root(bridge) / "src").mkdir()
    bridge.configure_write_root("demo", "src")
    bridge.propose_workspace_edit("demo", "src/sub/new.py", "value = 1\n")
    with pytest.raises(PermissionDenied):
        bridge.propose_workspace_edit("demo", "src-other/new.py", "value = 1\n")
    with pytest.raises(PermissionDenied):
        bridge.propose_workspace_edit("demo", "other.py", "value = 1\n")
    assert CCCMCP(bridge.home).get_project_settings("demo")["writable_roots"] == ["src"]
    bridge.revoke_write_root("demo", "src")
    with pytest.raises(PermissionDenied):
        bridge.propose_workspace_edit("demo", "src/another.py", "value = 2\n")


@pytest.mark.parametrize("path", [".env", ".git/config", ".ccc-mcp/context.md", "../outside.py"])
def test_project_grant_still_blocks_sensitive_and_external_paths(bridge, path):
    bridge.configure_write_root("demo", ".")
    with pytest.raises(BridgeError):
        bridge.propose_workspace_edit("demo", path, "forbidden")


def test_one_local_confirmation_approves_all_batch_files(bridge, monkeypatch):
    proposed = batch(bridge)
    calls = []
    monkeypatch.setattr(
        "ccc_mcp.approval.confirm_local",
        lambda description, challenge: calls.append((description, challenge)) or "test-principal",
    )
    with pytest.raises(PermissionDenied):
        bridge.write_workspace("demo", proposed["id"])
    for eid in proposed["edits"]:
        with pytest.raises(PermissionDenied):
            bridge.approve_workspace_edit_local("demo", eid)
    bridge.approve_workspace_edit_local("demo", proposed["id"])
    assert len(calls) == 1
    assert "first.md" in calls[0][0] and "second.md" in calls[0][0]
    with pytest.raises(PermissionDenied):
        bridge.write_workspace("demo", proposed["edits"][0])
    receipt = bridge.write_workspace("demo", proposed["id"])
    assert receipt["status"] == "APPLIED"
    assert bridge.write_workspace("demo", proposed["id"]) == receipt
    for name in ("first.md", "second.md"):
        assert (root(bridge) / name).read_text() == "original\ntest\n"


@pytest.mark.parametrize("failure", ["stale", "revoked"])
def test_entire_batch_preflight_runs_before_first_write(bridge, human, failure):
    proposed = batch(bridge)
    bridge.approve_workspace_edit_local("demo", proposed["id"])
    if failure == "stale":
        (root(bridge) / "second.md").write_text("external change", encoding="utf-8")
    else:
        bridge.revoke_write_root("demo", ".")
    with pytest.raises((VersionConflict, PermissionDenied)):
        bridge.write_workspace("demo", proposed["id"])
    assert (root(bridge) / "first.md").read_text() == "original\n"
    assert bridge.get_workspace_edit("demo", proposed["id"])["status"] == "APPROVED"


def test_interrupted_batch_is_resumable_without_reappending(bridge, human, monkeypatch):
    from ccc_mcp.security import atomic_write

    proposed = batch(bridge)
    bridge.approve_workspace_edit_local("demo", proposed["id"])

    def interrupted(path, content):
        if path.name == "second.md":
            raise OSError("interrupted before second write")
        atomic_write(path, content)

    monkeypatch.setattr("ccc_mcp.directedit.atomic_write", interrupted)
    with pytest.raises(OSError):
        bridge.write_workspace("demo", proposed["id"])
    assert bridge.get_workspace_edit("demo", proposed["id"])["status"] == "APPLYING"
    monkeypatch.setattr("ccc_mcp.directedit.atomic_write", atomic_write)
    assert bridge.write_workspace("demo", proposed["id"])["status"] == "APPLIED"
    assert (root(bridge) / "first.md").read_text() == "original\ntest\n"


def test_batch_cannot_have_duplicate_file_or_already_grouped_edits(bridge):
    proposed = batch(bridge)
    assert bridge.create_workspace_batch("demo", proposed["edits"])["id"] == proposed["id"]
    with pytest.raises(BridgeError):
        bridge.create_workspace_batch("demo", [proposed["edits"][0]])
    sha = bridge.read_workspace("demo", "first.md")["sha256"]
    another = bridge.propose_workspace_edit("demo", "first.md", "alternative\n", sha)
    with pytest.raises(BridgeError):
        bridge.create_workspace_batch("demo", [another["id"], proposed["edits"][0]])


def test_one_task_approval_allows_multiple_original_project_files(bridge, human, monkeypatch):
    calls = []
    monkeypatch.setattr(
        "ccc_mcp.approval.confirm_local",
        lambda *args: calls.append(args) or "test-principal",
    )
    execution = task(bridge)
    assert execution["workspace"] == str(root(bridge))
    eid = execution["execution_id"]
    original = bridge.read_workspace("demo", "app.py", "worktree", eid)
    bridge.write_task_file(
        "demo",
        eid,
        "app.py",
        "# original comment\ndef value():\n    return 2\n",
        original["sha256"],
    )
    bridge.write_task_file("demo", eid, "new.py", "value = 3\n")
    assert "return 2" in (root(bridge) / "app.py").read_text()
    assert (root(bridge) / "new.py").read_text() == "value = 3\n"
    assert len(calls) == 1
    assert "project" in calls[0][0]
    assert not (bridge.home / "worktrees").exists()
    report = AgentReport(summary="Changed 2 files", next_action="Host test").model_dump()
    bridge.submit_agent_report("demo", eid, report)
    result = bridge.get_task_result("demo", "TASK-0001")[-1]
    assert result["result"]["changed_files"] == ["app.py"]
    assert result["result"]["untracked_files"] == ["new.py"]
    assert result["status"] == "SUCCEEDED"


def test_project_mode_requires_plan_and_write_scope(bridge, human):
    bridge.configure_workspace_mode("demo", "project")
    bridge.configure_role("demo", "SD/RD", "chatgpt")
    bridge.create_task("demo", "Implement")
    with pytest.raises(PermissionDenied):
        bridge.dispatch_agent("demo", "TASK-0001", "auto", "SD/RD", 1, "Implement")
    approve(bridge)
    with pytest.raises(PermissionDenied, match="write-root"):
        bridge.dispatch_agent("demo", "TASK-0001", "auto", "SD/RD", 1, "Implement")


def test_project_mode_change_invalidates_earlier_plan_approval(bridge, human):
    bridge.create_task("demo", "Implement")
    approve(bridge)
    bridge.configure_workspace_mode("demo", "project")
    with pytest.raises(PermissionDenied):
        bridge.approved_plan("demo", "TASK-0001")


def test_active_project_task_blocks_mode_change_and_parallel_implementation(bridge, human):
    task(bridge)
    with pytest.raises(BridgeError):
        bridge.configure_workspace_mode("demo", "worktree")
    bridge.create_task("demo", "Another")
    approve(bridge, task="TASK-0002")
    with pytest.raises(BridgeError, match="exclusive"):
        bridge.dispatch_agent("demo", "TASK-0002", "auto", "SD/RD", 1, "Implement")


def test_project_mode_rejects_preexisting_uncommitted_code(bridge, human):
    (root(bridge) / "app.py").write_text("# original comment\nvalue=99\n", encoding="utf-8")
    with pytest.raises(BridgeError, match="commit"):
        task(bridge)
    assert "99" in (root(bridge) / "app.py").read_text()


def test_project_mode_keeps_sha_comment_and_revocation_checks(bridge, human):
    eid = task(bridge)["execution_id"]
    sha = bridge.read_workspace("demo", "app.py")["sha256"]
    with pytest.raises(VersionConflict):
        bridge.write_task_file("demo", eid, "app.py", "new", "stale")
    with pytest.raises(PermissionDenied, match="comments"):
        bridge.write_task_file("demo", eid, "app.py", "value=2\n", sha)
    bridge.revoke_write_root("demo", ".")
    with pytest.raises(PermissionDenied):
        bridge.write_task_file("demo", eid, "another.py", "value=2\n")


def test_project_mode_does_not_silently_enable_native_cli_root_writes(bridge, human):
    bridge.configure_workspace_mode("demo", "project")
    bridge.create_task("demo", "Implement")
    approve(bridge)
    with pytest.raises(BridgeError, match="ChatGPT"):
        bridge.dispatch_agent(
            "demo", "TASK-0001", "claude", "SD/RD", 1, "Implement", adapter="fake"
        )


def test_project_mode_allows_same_task_revision_without_requiring_commit(bridge, human):
    eid = task(bridge)["execution_id"]
    bridge.write_task_file("demo", eid, "new.py", "value = 1\n")
    bridge.submit_agent_report(
        "demo", eid, AgentReport(summary="First implementation", next_action="Refine").model_dump()
    )
    retry = bridge.dispatch_agent("demo", "TASK-0001", "auto", "SD/RD", 1, "Refine")
    sha = bridge.read_workspace("demo", "new.py")["sha256"]
    bridge.write_task_file("demo", retry["execution_id"], "new.py", "value = 2\n", sha)
    assert (root(bridge) / "new.py").read_text() == "value = 2\n"


def test_scope_cli_accepts_directory_as_positional_argument(bridge):
    from typer.testing import CliRunner

    from ccc_mcp.cli import app

    runner = CliRunner()
    prefix = ["--home", str(bridge.home), "--project", "demo", "config"]
    for command in (["write-root", "."], ["workspace-mode", "project"], ["revoke-write-root", "."]):
        result = runner.invoke(app, prefix + command)
        assert result.exit_code == 0, result.output
    assert bridge.get_project_settings("demo")["workspace_mode"] == "project"
    assert bridge.get_project_settings("demo")["writable_roots"] == []
