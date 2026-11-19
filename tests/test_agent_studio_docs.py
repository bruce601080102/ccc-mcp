"""The Agent Studio guide exists in both languages, says the same things, and links both ways."""

import re
from pathlib import Path

import pytest

DOCS = Path(__file__).parents[1] / "docs"
EN = DOCS / "en" / "14-agent-studio.md"
CH = DOCS / "ch" / "14-agent-studio.md"
LINK = re.compile(r"\]\(([^)\s]+)\)")


def slug(heading):
    text = heading.strip().lower()
    text = re.sub(r"[^\w\- ]", "", text, flags=re.UNICODE)
    return text.replace(" ", "-")


def headings(path):
    return [line[3:] for line in path.read_text(encoding="utf-8").splitlines()
            if line.startswith("## ")]


def anchors(path):
    return {slug(item) for item in headings(path)}


def test_both_guides_have_the_same_numbered_sections():
    numbers = [[h.split(".")[0] for h in headings(path)] for path in (EN, CH)]
    assert numbers[0] == numbers[1] == [str(n) for n in range(1, 10)]


@pytest.mark.parametrize("path", [EN, CH], ids=["en", "ch"])
def test_every_relative_link_and_anchor_resolves(path):
    for target in LINK.findall(path.read_text(encoding="utf-8")):
        if target.startswith(("http://", "https://", "#")):
            continue
        name, _, anchor = target.partition("#")
        resolved = (path.parent / name).resolve()
        assert resolved.is_file(), target
        if anchor:
            assert anchor in anchors(resolved), target


def test_the_two_languages_link_to_each_other():
    assert "../ch/14-agent-studio.md" in EN.read_text(encoding="utf-8")
    assert "../en/14-agent-studio.md" in CH.read_text(encoding="utf-8")


@pytest.mark.parametrize("language,title", [("en", "14. Agent Studio"), ("ch", "14. Agent Studio")])
def test_both_indexes_list_the_guide(language, title):
    index = (DOCS / language / "README.md").read_text(encoding="utf-8")
    assert "14-agent-studio.md" in index


def test_both_guides_state_that_prompt_scoring_by_a_model_does_not_exist():
    english = EN.read_text(encoding="utf-8")
    chinese = CH.read_text(encoding="utf-8")
    assert "no AI scoring of prompts" in english and "it is not available" in english
    assert "沒有 AI 評分 Prompt" in chinese and "目前不存在" in chinese
    for text in (english, chinese):
        assert "no score" in text.lower() or "不給分數" in text


@pytest.mark.parametrize("path", [EN, CH], ids=["en", "ch"])
def test_the_guide_covers_every_required_topic(path):
    text = path.read_text(encoding="utf-8")
    english = path == EN
    for needle in (
        ("Role Studio", "角色工作室"), ("Guided sections", "引導式章節"),
        ("Full text", "完整文字"), ("SHA-256", "SHA-256"),
        ("snapshot", "快照"), ("workflow", "工作流"), ("version 2", "版本 2"),
        ("Confirm and switch on", "確認並啟用"), ("fixed", "固定"),
    ):
        assert needle[0 if english else 1] in text, needle
