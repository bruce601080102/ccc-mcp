import json

from conftest import dispatch

from ccc_mcp.adapters.skills import install_skill


def _install_demo_skill(bridge):
    source = bridge.home.parent / "skill-source"
    source.mkdir(exist_ok=True)
    (source / "SKILL.md").write_text(
        "---\nname: demo-skill\ndescription: Use for demo tasks.\n---\nAlways say hi.\n",
        encoding="utf-8",
    )
    return install_skill(bridge.home, str(source))


def test_build_handoff_includes_available_skills_for_every_role(bridge):
    _install_demo_skill(bridge)
    bridge.create_task("demo", "Add API", ["Returns 200"])
    for role in ("PM", "SA"):
        package = bridge.build_handoff("demo", role, "TASK-0001")
        skills_seen = package["content"]["available_skills"]
        assert skills_seen == [
            {
                "id": "demo-skill",
                "name": "demo-skill",
                "description": "Use for demo tasks.",
                "path": str(bridge.home / "skills" / "demo-skill"),
                "content": "Always say hi.",
            }
        ]


def test_dispatched_execution_package_carries_installed_skill(bridge, human):
    _install_demo_skill(bridge)
    bridge.create_task("demo", "Add API", ["Returns 200"])
    execution_id = dispatch(bridge, "SA")["execution_id"]
    row = bridge.store.one("SELECT package FROM agent_executions WHERE id=?", (execution_id,))
    package = json.loads(row["package"])
    assert package["content"]["available_skills"][0]["id"] == "demo-skill"


def test_chatgpt_interactive_handoff_also_carries_installed_skill(bridge, human):
    _install_demo_skill(bridge)
    bridge.authorize_project_local("demo")
    task = bridge.create_task("demo", "Add API", ["Returns 200"])
    result = bridge.dispatch_agent(
        "demo", task["id"], "chatgpt", "SA", bridge.latest("demo")["version"], "Analyze",
    )
    assert result["context_package"]["content"]["available_skills"][0]["id"] == "demo-skill"
