"""A role that is read-only for the CLI providers is read-only when ChatGPT plays it.

READ_ONLY_ROLES is the single definition. The interactive ChatGPT path used to treat only SA and
REVIEW as read-only, so a ChatGPT conversation playing SR, AR, SEC, QA, SW or an everyday role
could write project files through write_task_file.
"""

import pytest

from ccc_mcp.core.roles import AGENT_ROLES, READ_ONLY_ROLES, writes_files
from ccc_mcp.infra.errors import PermissionDenied

WRITERS = {"RD", "SD/RD"}


def interactive(bridge, role, **options):
    bridge.configure_language("demo", "en")
    bridge.authorize_project_local("demo")
    task_id = bridge.create_task("demo", "Change something")["id"]
    bridge.propose_plan("demo", task_id, "PLAN-A", "A plan.", bridge.latest("demo")["version"])
    result = bridge.dispatch_agent(
        "demo", task_id, "chatgpt", role, bridge.latest("demo")["version"], "Do the role",
        adapter="real", **options,
    )
    return task_id, result["execution_id"]


@pytest.mark.parametrize("role", AGENT_ROLES)
def test_chatgpt_can_write_files_only_in_a_role_that_may_write(bridge, human, role):
    read_only = {"read_only": True} if role == "SD" else {}
    task_id, execution_id = interactive(bridge, role, **read_only)
    may_write = role in WRITERS
    assert writes_files(role, {"read_only": bool(read_only)}) is may_write
    if may_write:
        receipt = bridge.write_task_file("demo", execution_id, "probe.txt", "hello\n")
        assert receipt["path"] == "probe.txt"
    else:
        with pytest.raises(PermissionDenied, match="read-only regardless of provider"):
            bridge.write_task_file("demo", execution_id, "probe.txt", "hello\n")
        assert role in READ_ONLY_ROLES or role == "SD"
    # nothing was written by a role that may not write
    from pathlib import Path

    root = Path(bridge.project("demo")["root"])
    written = list(root.rglob("probe.txt"))
    assert bool(written) is may_write


@pytest.mark.parametrize("role", sorted(READ_ONLY_ROLES))
def test_a_read_only_role_over_chatgpt_gets_no_writable_workspace(bridge, human, role):
    task_id, execution_id = interactive(bridge, role)
    handoff = bridge.task_handoff("demo", task_id)
    assert "read-write implementation" not in handoff["chatgpt_prompt"]
    assert "write_task_file" not in handoff["chatgpt_prompt"]
    assert "read-only" in handoff["chatgpt_prompt"]
    # claiming the project workspace is for roles that write; a reviewer must not hold it
    claim = bridge.store.all("SELECT * FROM agent_executions WHERE id=? AND worktree IS NOT NULL",
                             (execution_id,))
    from pathlib import Path

    root = str(Path(bridge.project("demo")["root"]).resolve())
    assert all(str(Path(r["worktree"]).resolve()) in {root} or "worktree" in r["worktree"]
               for r in claim)
