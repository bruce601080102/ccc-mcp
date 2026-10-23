"""`task_preflight` (CLI: `ccc-mcp task preflight`) is a read-only, pre-dispatch
check reported as a leveled `checks[]` list (`ready`/`warning`/`blocker`, each with a
`remediation` for anything short of `ready`) plus a `summary` tally and an overall
`ready` bool (true iff there is no blocker). It never installs anything, never runs
the test command, never touches Git, and never commits/dispatches anything by being
asked. Covers: project authorization; each automatically-dispatched role's
configured provider actually being available; ChatGPT roles needing a Tunnel;
workflow state that would make a fresh dispatch fail right now (a live execution, an
unresolved pending_dispatch, unrefined PM reports, a pending local approval);
installed Skills being readable and the PM Context package fitting any configured
token budget; and the original three environment checks (test command, dependency
manifest/venv, Git/worktree cleanliness).
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from typer.testing import CliRunner

from ccc_mcp.adapters.test_runner import venv_python_path
from ccc_mcp.adapters.tunnel_client import tunnel_client_path
from ccc_mcp.infra.errors import BridgeError
from ccc_mcp.infra.security import safe_path
from ccc_mcp.interfaces.cli import app
from ccc_mcp.worker import run_execution


def root(bridge):
    return Path(bridge.project("demo")["root"])


def native_clis(monkeypatch, *installed):
    """Deterministically control which native CLIs the "roles" checks (and, via
    detect_profile's own independent best_model_roles() detection, the "profile"
    field) see as installed, instead of depending on whatever happens to be on the
    machine actually running the tests."""

    def fake(executable):
        if executable in installed:
            return f"/fake/bin/{executable}"
        raise BridgeError(f"Native {executable} executable unavailable; install the official CLI")

    monkeypatch.setattr("ccc_mcp.core.tasks.native_executable", fake)
    monkeypatch.setattr("ccc_mcp.core.configuration.native_executable", fake)


def check(result, check_id):
    return next(c for c in result["checks"] if c["id"] == check_id)


def test_default_pytest_command_resolves_and_is_ready(bridge, human, monkeypatch):
    native_clis(monkeypatch, "codex", "claude")
    bridge.authorize_project_local("demo")
    task_id = bridge.create_task("demo", "Implement a change")["id"]

    result = bridge.task_preflight("demo", task_id)

    assert result["ready"] is True
    assert result["next_role"] == "SA"
    assert result["profile"] == "best-model"
    assert result["summary"] == {"ready": len(result["checks"]), "warning": 0, "blocker": 0}
    assert all(c["status"] == "ready" for c in result["checks"])
    assert result["test_command"]["configured"] is False
    assert result["test_command"]["resolvable"] is True
    assert result["test_command"]["command"][-2:] == ["pytest", "-q"]
    assert result["test_command"]["cwd"] == str(root(bridge))
    assert result["test_command"]["cwd_exists"] is True
    assert result["dependencies"] == {
        "workspace_exists": True,
        "checked_path": str(root(bridge)),
        "manifest": None,
        "venv_ready": None,
    }
    assert result["git"] == {"initial_commit": True, "clean": True, "reason": None}


def test_unresolvable_test_command_is_not_ready(bridge, human, monkeypatch):
    native_clis(monkeypatch, "codex", "claude")
    bridge.authorize_project_local("demo")
    bridge.configure_test_command("demo", ["definitely-not-a-real-command-xyz"])
    task_id = bridge.create_task("demo", "Implement a change")["id"]

    result = bridge.task_preflight("demo", task_id)

    assert result["ready"] is False
    assert result["test_command"]["resolvable"] is False
    assert "not found" in result["test_command"]["reason"]
    assert check(result, "test_command")["status"] == "blocker"
    assert check(result, "test_command")["remediation"]


def test_dependency_manifest_is_detected_without_installing_anything(bridge, human, monkeypatch):
    native_clis(monkeypatch, "codex", "claude")
    bridge.authorize_project_local("demo")
    (root(bridge) / "requirements.txt").write_text("requests\n")
    task_id = bridge.create_task("demo", "Implement a change")["id"]

    result = bridge.task_preflight("demo", task_id)

    assert result["dependencies"]["manifest"] == "requirements.txt"
    assert result["dependencies"]["venv_ready"] is False
    assert check(result, "dependencies")["status"] == "warning"
    # Never actually installs: no venv was created as a side effect.
    venv_dir = safe_path(bridge.home / "venvs", "demo")
    assert not venv_python_path(venv_dir).is_file()
    # sync_documents always writes a placeholder TEST-RESULT.md on task creation;
    # preflight must not have replaced it with real (fabricated) test evidence.
    placeholder = (
        root(bridge) / ".ccc-mcp" / "tasks" / task_id / "TEST-RESULT.md"
    ).read_text(encoding="utf-8")
    assert "尚未產生" in placeholder


def test_venv_ready_once_the_dedicated_venv_already_exists(bridge, human, monkeypatch):
    native_clis(monkeypatch, "codex", "claude")
    bridge.authorize_project_local("demo")
    (root(bridge) / "requirements.txt").write_text("requests\n")
    task_id = bridge.create_task("demo", "Implement a change")["id"]
    venv_dir = safe_path(bridge.home / "venvs", "demo")
    subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True)

    result = bridge.task_preflight("demo", task_id)

    assert result["dependencies"]["venv_ready"] is True
    assert check(result, "dependencies")["status"] == "ready"


def test_uncommitted_changes_block_readiness_in_project_mode(bridge, human, monkeypatch):
    native_clis(monkeypatch, "codex", "claude")
    bridge.authorize_project_local("demo")
    task_id = bridge.create_task("demo", "Implement a change")["id"]
    (root(bridge) / "app.py").write_text("# original comment\nvalue = 2\n")

    result = bridge.task_preflight("demo", task_id)

    assert result["git"]["clean"] is False
    assert "commit" in result["git"]["reason"]
    assert result["ready"] is False
    assert check(result, "git")["status"] == "blocker"
    # Read-only: the dirty file is still there, untouched, no git operation ran on it.
    assert "value = 2" in (root(bridge) / "app.py").read_text()


def test_missing_initial_commit_is_reported_and_blocks_readiness(bridge, tmp_path, human, monkeypatch):
    native_clis(monkeypatch, "codex", "claude")
    folder = tmp_path / "plain"
    folder.mkdir()
    bridge.register_project(folder, "plain")
    bridge.authorize_project_local("plain")
    task_id = bridge.create_task("plain", "Implement a change")["id"]

    result = bridge.task_preflight("plain", task_id)

    assert result["git"]["initial_commit"] is False
    assert result["git"]["clean"] is None  # not checked without a commit to diff against
    assert result["ready"] is False
    assert check(result, "git")["status"] == "blocker"


def test_worktree_mode_checks_project_root_before_a_worktree_exists(bridge, human, monkeypatch):
    native_clis(monkeypatch, "codex", "claude")
    task_id = bridge.create_task("demo", "Implement a change")["id"]

    result = bridge.task_preflight("demo", task_id)

    assert result["dependencies"]["workspace_exists"] is False
    assert result["dependencies"]["checked_path"] == str(root(bridge))
    # Worktree mode has nothing to diff against; the clean check only applies to
    # project mode (an authorized project modifies the original repo in place).
    assert result["git"]["clean"] is None


def test_defaults_to_the_project_workstream_task(bridge, human, monkeypatch):
    native_clis(monkeypatch, "codex", "claude")
    bridge.authorize_project_local("demo")
    task_id = bridge.create_task("demo", "Implement a change")["id"]
    assert bridge.task_preflight("demo")["task_id"] == task_id


def test_cli_task_preflight_round_trip(bridge, human, monkeypatch):
    native_clis(monkeypatch, "codex", "claude")
    bridge.authorize_project_local("demo")
    task_id = bridge.create_task("demo", "Implement a change")["id"]

    result = CliRunner().invoke(
        app, ["--home", str(bridge.home), "--project", "demo", "task", "preflight", task_id]
    )

    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["task_id"] == task_id
    assert "ready" in payload
    assert "checks" in payload and "summary" in payload


def test_unauthorized_project_reports_authorization_blocker(bridge, human, monkeypatch):
    native_clis(monkeypatch, "codex", "claude")
    task_id = bridge.create_task("demo", "Implement a change")["id"]

    result = bridge.task_preflight("demo", task_id)

    assert check(result, "authorization")["status"] == "blocker"
    assert "authorize" in check(result, "authorization")["remediation"]
    assert result["ready"] is False


def test_role_provider_blocker_only_for_this_tasks_next_role(bridge, human, monkeypatch):
    """Default roles are SA/REVIEW=codex, SD/RD=claude; with only claude installed,
    a fresh task's next role is SA, so only SA is a blocker — REVIEW isn't reached
    by this task yet, so its missing CLI is only a warning (SD/RD stays ready)."""
    native_clis(monkeypatch, "claude")
    bridge.authorize_project_local("demo")
    task_id = bridge.create_task("demo", "Implement a change")["id"]

    result = bridge.task_preflight("demo", task_id)

    assert result["next_role"] == "SA"
    assert check(result, "role_SA")["status"] == "blocker"
    assert check(result, "role_REVIEW")["status"] == "warning"
    assert check(result, "role_SD/RD")["status"] == "ready"
    assert result["ready"] is False
    assert "codex" in check(result, "role_SA")["remediation"] or "chatgpt" in check(result, "role_SA")["remediation"]


def test_role_provider_blocker_follows_the_actual_next_role(bridge, human, monkeypatch):
    """Once SA has run, the next role becomes SD/RD — a missing SA/REVIEW CLI is no
    longer this task's immediate blocker, but a missing SD/RD CLI now is."""
    native_clis(monkeypatch, "codex")  # claude (SD/RD) missing
    bridge.authorize_project_local("demo")
    task_id = bridge.create_task("demo", "Implement a change")["id"]
    sa = bridge.dispatch_agent(
        "demo", task_id, "codex", "SA", bridge.latest("demo")["version"], "Analyze", adapter="fake"
    )
    run_execution(bridge, sa["execution_id"])

    result = bridge.task_preflight("demo", task_id)

    assert result["next_role"] == "SD/RD"
    assert check(result, "role_SD/RD")["status"] == "blocker"
    # SA and REVIEW both default to codex, which is installed here; REVIEW simply
    # isn't this task's blocker right now, but its provider happens to be fine too.
    assert check(result, "role_SA")["status"] == "ready"
    assert check(result, "role_REVIEW")["status"] == "ready"


def test_missing_tunnel_is_a_blocker_when_next_role_needs_chatgpt(bridge, human, monkeypatch):
    native_clis(monkeypatch)  # neither native CLI installed; irrelevant once chatgpt-only
    bridge.configure_profile("demo", "chatgpt-only")
    bridge.authorize_project_local("demo")
    task_id = bridge.create_task("demo", "Implement a change")["id"]
    assert not tunnel_client_path(bridge.home).is_file()

    result = bridge.task_preflight("demo", task_id)

    assert result["next_role"] == "SA"
    assert check(result, "role_SA")["status"] == "ready"
    assert check(result, "tunnel")["status"] == "blocker"
    assert result["ready"] is False


def test_tunnel_installed_is_still_only_a_warning_not_fully_ready(bridge, human, monkeypatch):
    """Being downloaded is not the same as being connected right now — this must
    never report "ready" and let a ChatGPT-only project look fully set up when the
    Tunnel might not actually be live."""
    bridge.configure_profile("demo", "chatgpt-only")
    bridge.authorize_project_local("demo")
    task_id = bridge.create_task("demo", "Implement a change")["id"]
    tunnel_client_path(bridge.home).parent.mkdir(parents=True, exist_ok=True)
    tunnel_client_path(bridge.home).touch()

    result = bridge.task_preflight("demo", task_id)

    assert check(result, "tunnel")["status"] == "warning"
    assert result["ready"] is True  # a warning alone never blocks readiness


def test_live_execution_is_a_blocker(bridge, human, monkeypatch):
    native_clis(monkeypatch, "codex", "claude")
    bridge.authorize_project_local("demo")
    task_id = bridge.create_task("demo", "Implement a change")["id"]
    bridge.dispatch_agent("demo", task_id, "codex", "SA", bridge.latest("demo")["version"], "Analyze", adapter="fake")

    result = bridge.task_preflight("demo", task_id)

    assert check(result, "live_execution")["status"] == "blocker"
    assert result["ready"] is False


def test_pending_dispatch_is_a_blocker(bridge, human, monkeypatch):
    native_clis(monkeypatch, "codex", "claude")
    bridge.authorize_project_local("demo")
    task_id = bridge.create_task("demo", "Implement a change")["id"]
    sa = bridge.dispatch_agent(
        "demo", task_id, "codex", "SA", bridge.latest("demo")["version"], "Analyze", adapter="fake"
    )
    run_execution(bridge, sa["execution_id"])
    item = next(
        r for r in bridge.latest("demo")["snapshot"]["agent_results"]
        if r["execution_id"] == sa["execution_id"]
    )
    patch = {
        "base_context_version": bridge.latest("demo")["version"],
        "changes": {"summary": "x"},
        "source_execution_ids": [sa["execution_id"]],
        "source_task_ids": [item["task_id"]],
        "source_document_refs": [item["source_document_ref"]],
        "update_reason": "r",
        "semantic_review_ack": True,
    }

    def flaky(execution_id):
        raise RuntimeError("simulated worker spawn failure")

    monkeypatch.setattr(bridge, "spawn_worker", flaky)
    with pytest.raises(BridgeError):
        bridge.advance_project("demo", patch, "Implement the approved change", adapter="fake")

    result = bridge.task_preflight("demo", task_id)

    assert check(result, "pending_dispatch")["status"] == "blocker"
    assert result["ready"] is False


def test_pm_refinement_is_a_blocker_while_continuously_authorized(bridge, human, monkeypatch):
    native_clis(monkeypatch, "codex", "claude")
    bridge.authorize_project_local("demo")
    task_id = bridge.create_task("demo", "Implement a change")["id"]
    sa = bridge.dispatch_agent(
        "demo", task_id, "codex", "SA", bridge.latest("demo")["version"], "Analyze", adapter="fake"
    )
    run_execution(bridge, sa["execution_id"])

    result = bridge.task_preflight("demo", task_id)

    assert check(result, "pm_refinement")["status"] == "blocker"
    assert result["ready"] is False


def test_pending_local_approval_is_a_warning(bridge, human, monkeypatch):
    """Without continuous authorization, a plan awaiting local approval doesn't fully
    block (another valid path — rejecting it, or approving a different plan — still
    exists), so it is a warning, distinct from the authorization blocker itself."""
    native_clis(monkeypatch, "codex", "claude")
    task_id = bridge.create_task("demo", "Implement a change")["id"]
    bridge.propose_plan("demo", task_id, "PLAN-A", "Implement the change", bridge.latest("demo")["version"])
    bridge.request_approval("demo", task_id, "PLAN-A")

    result = bridge.task_preflight("demo", task_id)

    assert check(result, "pending_approval")["status"] == "warning"
    assert "PLAN-A" in check(result, "pending_approval")["message"]


def test_skills_check_reports_a_readable_count(bridge, human, monkeypatch):
    native_clis(monkeypatch, "codex", "claude")
    bridge.authorize_project_local("demo")
    task_id = bridge.create_task("demo", "Implement a change")["id"]

    result = bridge.task_preflight("demo", task_id)

    assert check(result, "skills")["status"] == "ready"


def test_context_budget_blocker_when_pm_package_is_too_large(bridge, human, monkeypatch):
    native_clis(monkeypatch, "codex", "claude")
    bridge.authorize_project_local("demo")
    bridge.configure_context_token_budget("demo", 1000)
    # The requirement text lives under content["task"], which build_handoff never
    # trims (only bounded-history lists like decisions are compressible) — so a long
    # enough requirement guarantees budget_exceeded stays true even after trimming.
    task_id = bridge.create_task("demo", "Implement a change: " + "x" * 20000)["id"]

    result = bridge.task_preflight("demo", task_id)

    assert check(result, "context_budget")["status"] == "blocker"
    assert result["ready"] is False


def test_context_budget_not_checked_when_unconfigured(bridge, human, monkeypatch):
    native_clis(monkeypatch, "codex", "claude")
    bridge.authorize_project_local("demo")
    task_id = bridge.create_task("demo", "Implement a change")["id"]

    result = bridge.task_preflight("demo", task_id)

    assert not any(c["id"] == "context_budget" for c in result["checks"])


def test_summary_tally_matches_checks(bridge, human, monkeypatch):
    native_clis(monkeypatch, "codex", "claude")
    bridge.authorize_project_local("demo")
    task_id = bridge.create_task("demo", "Implement a change")["id"]

    result = bridge.task_preflight("demo", task_id)

    tally = {"ready": 0, "warning": 0, "blocker": 0}
    for c in result["checks"]:
        tally[c["status"]] += 1
    assert tally == result["summary"]
    assert sum(result["summary"].values()) == len(result["checks"])


def test_profile_reports_chatgpt_only(bridge, human, monkeypatch):
    native_clis(monkeypatch, "codex", "claude")
    bridge.configure_profile("demo", "chatgpt-only")
    bridge.authorize_project_local("demo")
    task_id = bridge.create_task("demo", "Implement a change")["id"]

    result = bridge.task_preflight("demo", task_id)

    assert result["profile"] == "chatgpt-only"


def test_profile_reports_custom_for_a_mixed_combination(bridge, human, monkeypatch):
    native_clis(monkeypatch, "codex", "claude")
    bridge.authorize_project_local("demo")
    bridge.configure_role("demo", "SA", "chatgpt")
    task_id = bridge.create_task("demo", "Implement a change")["id"]

    result = bridge.task_preflight("demo", task_id)

    assert result["profile"] == "custom"


def test_next_role_is_none_once_the_task_is_completed(bridge, human, monkeypatch):
    native_clis(monkeypatch, "codex", "claude")
    bridge.authorize_project_local("demo")
    bridge.configure_test_command("demo", [sys.executable, "-c", "print('ok')"])
    task_id = bridge.create_task("demo", "Implement a change")["id"]

    def pending_patch(execution_id):
        item = next(
            r for r in bridge.latest("demo")["snapshot"]["agent_results"]
            if r["execution_id"] == execution_id
        )
        return {
            "base_context_version": bridge.latest("demo")["version"],
            "changes": {"summary": "x"},
            "source_execution_ids": [execution_id],
            "source_task_ids": [item["task_id"]],
            "source_document_refs": [item["source_document_ref"]],
            "update_reason": "r",
            "semantic_review_ack": True,
        }

    sa = bridge.dispatch_agent(
        "demo", task_id, "codex", "SA", bridge.latest("demo")["version"], "Analyze", adapter="fake"
    )
    run_execution(bridge, sa["execution_id"])
    impl = bridge.advance_project(
        "demo", pending_patch(sa["execution_id"]), "Implement the approved change", adapter="fake"
    )["next_execution"]
    run_execution(bridge, impl["execution_id"])
    reviewed = bridge.advance_project("demo", pending_patch(impl["execution_id"]), adapter="fake")
    run_execution(bridge, reviewed["next_execution"]["execution_id"])
    bridge.advance_project("demo", pending_patch(reviewed["next_execution"]["execution_id"]), adapter="fake")
    assert bridge.task("demo", task_id)["state"] == "COMPLETED"

    result = bridge.task_preflight("demo", task_id)

    assert result["next_role"] is None
    assert not any(c["id"].startswith("role_") for c in result["checks"])


def test_worktree_mode_test_command_cwd_is_the_future_worktree_not_project_root(
    bridge, human, monkeypatch
):
    """Regression: `cwd` is where the test command actually runs — this task's own
    worktree in worktree mode — even before that worktree exists. It must never be
    silently reported as the project root; `inspection_path` is the separate,
    best-effort path dependency-manifest *detection* previews instead."""
    native_clis(monkeypatch, "codex", "claude")
    task_id = bridge.create_task("demo", "Implement a change")["id"]

    result = bridge.task_preflight("demo", task_id)

    expected_worktree = bridge.task_worktree("demo", task_id)
    assert result["test_command"]["cwd"] == str(expected_worktree)
    assert result["test_command"]["cwd_exists"] is False
    assert result["test_command"]["inspection_path"] == str(root(bridge))
    assert result["test_command"]["cwd"] != result["test_command"]["inspection_path"]
    message = check(result, "test_command")["message"]
    assert "worktree" in message.lower() or "worktree" in message
    assert str(expected_worktree) in message


def test_project_mode_cwd_and_inspection_path_are_both_the_project_root(bridge, human, monkeypatch):
    native_clis(monkeypatch, "codex", "claude")
    bridge.authorize_project_local("demo")
    task_id = bridge.create_task("demo", "Implement a change")["id"]

    result = bridge.task_preflight("demo", task_id)

    assert result["test_command"]["cwd"] == str(root(bridge))
    assert result["test_command"]["inspection_path"] == str(root(bridge))
    assert result["test_command"]["cwd_exists"] is True


def test_worktree_mode_cwd_matches_inspection_path_once_the_worktree_exists(
    bridge, human, monkeypatch
):
    native_clis(monkeypatch, "codex", "claude")
    task_id = bridge.create_task("demo", "Implement a change")["id"]
    bridge.task_worktree("demo", task_id).mkdir(parents=True, exist_ok=True)

    result = bridge.task_preflight("demo", task_id)

    assert result["test_command"]["cwd_exists"] is True
    assert result["test_command"]["cwd"] == result["test_command"]["inspection_path"]
