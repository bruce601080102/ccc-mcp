import pytest

from ccc_mcp.adapters.skills import install_skill
from ccc_mcp.infra.errors import BridgeError


def install_big_skill(bridge, tmp_path, size=20000):
    skill_dir = tmp_path / "big-skill"
    skill_dir.mkdir()
    (skill_dir / "SKILL.md").write_text(
        "---\nname: big-skill\ndescription: a large test skill\n---\n" + ("x" * size),
        encoding="utf-8",
    )
    return install_skill(bridge.home, str(skill_dir))


def dispatch(bridge, role="SA", **kwargs):
    return bridge.dispatch_agent(
        "demo", "TASK-0001", "auto", role, bridge.latest("demo")["version"], "Perform role",
        **kwargs,
    )


def test_build_handoff_trims_skill_content_before_touching_history(bridge, tmp_path):
    """Regression (P1-4): a full Skill body previously always entered every handoff
    regardless of budget. It must now be the first thing a budget trim shrinks (Skills
    are optional operator guidance, unlike decisions/reports), while id/name/description
    stay so the Agent still knows the Skill exists and what it's for."""
    skill = install_big_skill(bridge, tmp_path)

    full = bridge.build_handoff("demo", "PM")
    full_skill = next(s for s in full["content"]["available_skills"] if s["id"] == skill["id"])
    assert len(full_skill["content"]) > 15000
    assert full["budget_omitted"] == []

    trimmed = bridge.build_handoff("demo", "PM", token_budget=200)
    trimmed_skill = next(
        s for s in trimmed["content"]["available_skills"] if s["id"] == skill["id"]
    )
    assert trimmed_skill["content"] == ""
    assert trimmed_skill["content_omitted"] is True
    assert trimmed_skill["id"] == skill["id"]
    assert trimmed_skill["description"] == "a large test skill"
    assert any("available_skills" in note for note in trimmed["budget_omitted"])


def test_dispatch_agent_hard_blocks_when_still_over_budget_after_trimming(
    bridge, tmp_path
):
    """Regression (P0-4): previously a configured context_token_budget was purely
    advisory — dispatch_agent happily dispatched a handoff that still exceeded it even
    after trimming. An unreachable budget must now refuse to dispatch instead of
    silently sending an over-budget handoff.

    Skills and decision/report history are both trimmable, so a huge Skill alone isn't
    enough to prove the budget is truly unreachable — a long *requirement* is never
    trimmed (it's protected, direct evidence for the dispatch), so it reliably forces
    budget_exceeded to stay true no matter how aggressively everything else is cut.
    """
    install_big_skill(bridge, tmp_path)
    bridge.create_task("demo", "需求 " + ("x" * 20000))
    bridge.configure_context_token_budget("demo", 1000)

    with pytest.raises(BridgeError, match="context_token_budget"):
        dispatch(bridge, "SA", adapter="fake")
    assert bridge.get_task_result("demo", "TASK-0001") == []


def test_dispatch_agent_succeeds_once_trimming_fits_the_budget(bridge, tmp_path):
    """A budget that's tight but achievable after trimming must still let dispatch
    proceed — the hard block is only for a budget that can't be met at all."""
    install_big_skill(bridge, tmp_path)
    bridge.create_task("demo", "需求")
    # Measure the fully-trimmed floor for this exact package, then dispatch with
    # just enough headroom above it — avoids hard-coding a token count that could
    # drift with unrelated content changes.
    floor = bridge.build_handoff("demo", "SA", "TASK-0001", token_budget=1)
    bridge.configure_context_token_budget("demo", max(floor["estimated_tokens"] + 50, 1000))

    queued = dispatch(bridge, "SA", adapter="fake")
    package = bridge.get_task_result("demo", "TASK-0001")[-1]["package"]
    assert package["budget_omitted"]
    assert queued["status"] == "QUEUED"


def test_dispatch_agent_without_a_budget_still_sends_full_skill_content(bridge, tmp_path):
    skill = install_big_skill(bridge, tmp_path)
    bridge.create_task("demo", "需求")
    dispatch(bridge, "SA", adapter="fake")
    package = bridge.get_task_result("demo", "TASK-0001")[-1]["package"]
    sent_skill = next(s for s in package["content"]["available_skills"] if s["id"] == skill["id"])
    assert len(sent_skill["content"]) > 15000
    assert package["budget_exceeded"] is False
