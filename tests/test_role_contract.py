"""The fixed contract around a role's own instructions, and how its changes are classified."""

import json
from pathlib import Path

import pytest

from ccc_mcp.core import role_contract
from ccc_mcp.core.role_contract import (
    COMPATIBLE,
    IDENTICAL,
    INCOMPATIBLE,
    LANGUAGES,
    assess_contract,
    contract_manifest,
)

GOLDEN = Path(__file__).parent / "fixtures" / "contract_golden.json"


def snapshot_of_components():
    return {
        language: {
            name: [info["major"], info["minor"], info["sha256"]]
            for name, info in contract_manifest(language)["components"].items()
        }
        for language in LANGUAGES
    }


def test_the_fixed_contract_matches_its_recorded_versions():
    """Changing any fixed text must be a deliberate decision. If this fails, you changed text
    every custom role runs under: bump `minor` in role_contract.COMPONENTS when the meaning is
    the same, `major` when it is not (a major change pauses tasks pinned before it), then
    regenerate tests/fixtures/contract_golden.json."""
    assert json.loads(GOLDEN.read_text(encoding="utf-8")) == snapshot_of_components()


def test_a_manifest_is_deterministic_and_covers_every_component():
    first, second = contract_manifest("en"), contract_manifest("en")
    assert first == second
    assert set(first["components"]) == set(role_contract.COMPONENTS)
    assert first["contract_hash"] != contract_manifest("zh-TW")["contract_hash"]
    with pytest.raises(ValueError):
        contract_manifest("fr")


def with_changed_component(monkeypatch, name, *, text=None, major=None, minor=None, remove=False,
                           add=None, additive=False):
    components = {key: dict(value) for key, value in role_contract.COMPONENTS.items()}
    if remove:
        del components[name]
    if text is not None:
        components[name]["text"] = lambda _language: text
    if major is not None:
        components[name]["major"] = major
    if minor is not None:
        components[name]["minor"] = minor
    if add:
        components[add] = {"major": 1, "minor": 0, "text": lambda _l: "new", "additive": additive}
    monkeypatch.setattr(role_contract, "COMPONENTS", components)
    return contract_manifest("en")


def test_an_unchanged_contract_is_identical():
    pinned = contract_manifest("en")
    assert assess_contract(pinned, contract_manifest("en")) == {"status": IDENTICAL, "changes": []}


def test_new_wording_inside_the_same_major_version_is_compatible(monkeypatch):
    pinned = contract_manifest("en")
    current = with_changed_component(monkeypatch, "operator_policy", text="reworded", minor=1)
    result = assess_contract(pinned, current)
    assert result["status"] == COMPATIBLE
    assert result["changes"] == [{
        "component": "operator_policy", "change": "text", "blocking": False,
        "pinned": "1.0", "current": "1.1",
    }]


def test_a_new_major_version_is_incompatible(monkeypatch):
    pinned = contract_manifest("en")
    current = with_changed_component(monkeypatch, "agent_report_schema", text="x", major=2, minor=0)
    result = assess_contract(pinned, current)
    assert result["status"] == INCOMPATIBLE
    assert result["changes"][0]["change"] == "major" and result["changes"][0]["blocking"]


def test_a_removed_component_is_incompatible(monkeypatch):
    pinned = contract_manifest("en")
    current = with_changed_component(monkeypatch, "gate_report_format", remove=True)
    result = assess_contract(pinned, current)
    assert result["status"] == INCOMPATIBLE
    assert result["changes"] == [
        {"component": "gate_report_format", "change": "removed", "blocking": True}
    ]


def test_a_new_component_blocks_unless_it_is_declared_additive(monkeypatch):
    pinned = contract_manifest("en")
    blocking = with_changed_component(monkeypatch, "operator_policy", add="new_rule")
    assert assess_contract(pinned, blocking)["status"] == INCOMPATIBLE
    additive = with_changed_component(monkeypatch, "operator_policy", add="new_rule",
                                      additive=True)
    result = assess_contract(pinned, additive)
    assert result["status"] == COMPATIBLE and not result["changes"][0]["blocking"]


def test_changed_text_without_a_version_bump_is_still_a_visible_change(monkeypatch):
    """A developer who forgot to bump the version cannot make a text change invisible."""
    pinned = contract_manifest("en")
    current = with_changed_component(monkeypatch, "context_handoff_policy", text="quietly new")
    assert assess_contract(pinned, current)["status"] == COMPATIBLE


@pytest.mark.parametrize("pinned", [None, {}, {"components": {}}, {"components": "x"}, "text"])
def test_a_missing_or_malformed_pinned_manifest_is_incompatible(pinned):
    result = assess_contract(pinned, contract_manifest("en"))
    assert result["status"] == INCOMPATIBLE
    assert result["changes"][0]["change"] == "malformed"


def test_translating_the_policies_is_a_compatible_change():
    """A project that switches report language changes the text, not the contract."""
    result = assess_contract(contract_manifest("en"), contract_manifest("zh-TW"))
    assert result["status"] == COMPATIBLE
    assert all(not change["blocking"] for change in result["changes"])
