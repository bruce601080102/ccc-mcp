"""examples/skills/demo-multilevel-skill: SKILL.md -> level2.md -> deeper/level3.md.

Agents only receive SKILL.md's text plus the skill's absolute `path`; every deeper
document must be reachable by following references relative to that path. These tests
prove the chain resolves and that the canary exists ONLY at the deepest level, so a real
run that reports the canary must have read all the way down (docs/en/6-skills.md).
"""

import re
from pathlib import Path

from ccc_mcp.adapters import skills

EXAMPLE = Path(__file__).resolve().parent.parent / "examples" / "skills" / "demo-multilevel-skill"
CANARY = "MULTILEVEL-OK-9c41"
REFERENCE = re.compile(r"`(references/[A-Za-z0-9_./-]+\.md)`")


def installed(tmp_path):
    skills.install_skill(tmp_path, str(EXAMPLE))
    return next(s for s in skills.available_skills(tmp_path) if s["id"] == "demo-multilevel-skill")


def test_every_level_is_installed(tmp_path):
    root = Path(installed(tmp_path)["path"])
    assert (root / "SKILL.md").is_file()
    assert (root / "references" / "level2.md").is_file()
    assert (root / "references" / "deeper" / "level3.md").is_file()


def test_references_resolve_level_by_level_relative_to_the_skill_path(tmp_path):
    skill = installed(tmp_path)
    root = Path(skill["path"])
    assert root.is_absolute()  # the base agents are told to resolve relative paths against

    visited, frontier = [], [skill["content"]]
    while frontier:
        text = frontier.pop()
        for relative in REFERENCE.findall(text):
            target = root / relative
            assert target.is_file(), f"dangling reference: {relative}"
            visited.append(relative)
            frontier.append(target.read_text(encoding="utf-8"))
    assert visited == ["references/level2.md", "references/deeper/level3.md"]


def test_canary_exists_only_at_the_deepest_level(tmp_path):
    skill = installed(tmp_path)
    root = Path(skill["path"])
    assert CANARY not in skill["content"]  # all an agent gets embedded in its prompt
    assert CANARY not in (root / "references" / "level2.md").read_text(encoding="utf-8")
    assert CANARY in (root / "references" / "deeper" / "level3.md").read_text(encoding="utf-8")
