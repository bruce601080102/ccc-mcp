"""The fixed execution contract that surrounds every role's own instructions, and how to tell
whether a change to it is safe for a task that was started under an earlier version.

A custom role edits only its own part of what an Agent is sent. Everything else -- the operator
policy, the Meeting Mode and handoff rules, the gate report format, the strictness wording and
the structured report schemas -- is fixed by ccc-mcp. Each piece is a *component* with a
`major.minor` version and the sha256 of its exact text. A snapshot records the manifest it was
pinned under, so a later upgrade can be classified instead of silently applied:

  identical     nothing changed.
  compatible    wording changed inside the same major version: safe to run with the current
                text, and the difference is recorded.
  incompatible  a major version changed, or a component was added or removed: the task must
                not continue until a person has dealt with it.

Whoever edits a component's text decides which it is by bumping `minor` (same meaning, new
words) or `major` (the meaning changed). tests/test_role_contract.py compares every component
with tests/fixtures/contract_golden.json, so a text change cannot slip in without that decision.
"""

import json

from ..adapters.agents.base import meeting_mode_policy as _meeting_mode_policy
from ..adapters.agents.base import operator_policy
from ..infra.security import canonical, digest
from .flows import _AR_BRIEF, _FORMAT
from .models import AgentReport, ClarificationRequest, ClarificationResponse
from .role_prompt import BASE_CONTRACTS, CONTRACT_LEAD, FRAMING_TEXT, SECTION_HEADINGS, markers
from .roles import AGENT_ROLES

LANGUAGES = ("zh-TW", "ch", "en")
COMPATIBLE = "compatible"
INCOMPATIBLE = "incompatible"
IDENTICAL = "identical"


def _context_policy(language):
    # Imported late: core.context imports this package's neighbours during start-up.
    from .context import context_policy

    return context_policy(language)


def _codex_extra(_language):
    from ..adapters.agents.codex import CodexAdapter

    return CodexAdapter.EXTRA_INSTRUCTION


def _schema(model):
    return lambda _language: json.dumps(model.model_json_schema(), sort_keys=True)


# name -> {"major", "minor", "text": language -> str, "additive": bool}
# `additive` components may appear in a newer contract without making an older snapshot
# incompatible; everything else is treated conservatively.
COMPONENTS = {
    "operator_policy": {"major": 1, "minor": 0, "text": operator_policy},
    "meeting_clarification_request": {
        "major": 1, "minor": 0,
        "text": lambda language: _meeting_mode_policy("clarification_request", language),
    },
    "meeting_clarification_response": {
        "major": 1, "minor": 0,
        "text": lambda language: _meeting_mode_policy("clarification_response", language),
    },
    "context_handoff_policy": {"major": 1, "minor": 0, "text": _context_policy},
    "gate_report_format": {"major": 1, "minor": 0, "text": lambda _language: _FORMAT},
    "ar_strictness_briefs": {
        "major": 1, "minor": 0, "text": lambda _language: canonical(_AR_BRIEF),
    },
    "codex_extra_instruction": {"major": 1, "minor": 0, "text": _codex_extra},
    "agent_report_schema": {"major": 1, "minor": 0, "text": _schema(AgentReport)},
    "clarification_request_schema": {
        "major": 1, "minor": 0, "text": _schema(ClarificationRequest),
    },
    "clarification_response_schema": {
        "major": 1, "minor": 0, "text": _schema(ClarificationResponse),
    },
}


def _framing_text(_language):
    """Everything fixed in how an operator's text is framed: the wording, the section
    headings and the shape of the markers."""
    return canonical({
        "framing": FRAMING_TEXT, "lead": CONTRACT_LEAD, "headings": SECTION_HEADINGS,
        "markers": markers("<body>"),
    })


COMPONENTS["custom_role_framing"] = {"major": 1, "minor": 0, "text": _framing_text}
BASE_PREFIX = "base_contract:"
for _role in AGENT_ROLES:
    COMPONENTS[BASE_PREFIX + _role] = {
        "major": 1, "minor": 0, "text": lambda _language, role=_role: BASE_CONTRACTS[role],
    }


def contract_manifest(language, base_role=None):
    """The manifest a snapshot records: every component's version and text hash for one
    language, plus one hash over all of it.

    A snapshot is for one base role, so it carries the shared components and only that role's
    own `base_contract:` component; a change to another role's contract does not touch it.
    Called without a base role it lists every component. Compare a pinned manifest only with
    one built for the same base role."""
    if language not in LANGUAGES:
        raise ValueError(f"Unknown language {language!r}")
    if base_role is not None and base_role not in AGENT_ROLES:
        raise ValueError(f"Unknown base role {base_role!r}")
    components = {
        name: {
            "major": spec["major"],
            "minor": spec["minor"],
            "sha256": digest(spec["text"](language).encode("utf-8")),
        }
        for name, spec in sorted(COMPONENTS.items())
        if base_role is None or not name.startswith(BASE_PREFIX) or name == BASE_PREFIX + base_role
    }
    return {
        "language": language,
        "base_role": base_role,
        "components": components,
        "contract_hash": digest(canonical(components).encode("utf-8")),
    }


def assess_contract(pinned, current):
    """Compare the manifest a snapshot was pinned under with the current one.

    Returns {"status": identical|compatible|incompatible, "changes": [...]}. Each change names
    the component, what happened (`text`, `major`, `added`, `removed`, `malformed`) and whether
    it blocks. A missing or malformed pinned manifest is incompatible: nothing can be
    vouched for."""
    if (
        not isinstance(pinned, dict)
        or not isinstance(pinned.get("components"), dict)
        or not pinned["components"]
    ):
        return {
            "status": INCOMPATIBLE,
            "changes": [{"component": None, "change": "malformed", "blocking": True}],
        }
    old, new = pinned["components"], current["components"]
    changes = []
    for name in sorted({*old, *new}):
        if name not in new:
            changes.append({"component": name, "change": "removed", "blocking": True})
        elif name not in old:
            additive = bool(COMPONENTS.get(name, {}).get("additive"))
            changes.append({"component": name, "change": "added", "blocking": not additive})
        elif old[name].get("sha256") == new[name]["sha256"] and (
            old[name].get("major"), old[name].get("minor"),
        ) == (new[name]["major"], new[name]["minor"]):
            continue
        elif old[name].get("major") != new[name]["major"]:
            changes.append({
                "component": name, "change": "major", "blocking": True,
                "pinned": f"{old[name].get('major')}.{old[name].get('minor')}",
                "current": f"{new[name]['major']}.{new[name]['minor']}",
            })
        else:
            changes.append({
                "component": name, "change": "text", "blocking": False,
                "pinned": f"{old[name].get('major')}.{old[name].get('minor')}",
                "current": f"{new[name]['major']}.{new[name]['minor']}",
            })
    if any(change["blocking"] for change in changes):
        status = INCOMPATIBLE
    elif changes:
        status = COMPATIBLE
    else:
        status = IDENTICAL
    return {"status": status, "changes": changes}
