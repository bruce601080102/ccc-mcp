"""Workflow definitions and review-gate rules.

A workflow is an ordered list of steps; each step runs one role. A *producer* step (SA, SD,
RD, SD/RD) makes something; a *gate* step (CR, SR, AR, SEC, QA) judges it. When a gate sends
work back, the nearest earlier producer redoes it and every later step runs again, so a
reviewer never judges a stale artifact.

Gates are bounded: a gate may send work back at most ``max_rounds`` times (never more than
GATE_ROUNDS_HARD_CAP). After that, or when AR reports a fundamental flaw, the decision goes
to the PM instead of looping.

Only pure rules live here. Dispatching and persistence are in ``core/flow_engine.py``.
"""

import re

from ..infra.errors import BridgeError
from .roles import AGENT_ROLES

PRODUCER_ROLES = frozenset({"SA", "SD", "RD", "SD/RD", "SW"})
# Producers that change the working tree and therefore get host tests afterwards.
BUILDER_ROLES = frozenset({"RD", "SD/RD"})
GATE_ROLES = frozenset(AGENT_ROLES) - PRODUCER_ROLES
# Gates that judge the finished implementation, so failing host tests always fail them.
VERIFYING_GATES = frozenset({"REVIEW", "QA"})

STRICTNESS_LEVELS = ("normal", "strict", "extreme")
GATE_ROUNDS_HARD_CAP = 5
GATE_ESCALATIONS = {"ask_user", "auto_pm"}
DEFAULT_REVIEW_GATE = {"ar_strictness": "normal", "max_rounds": 2, "escalation": "ask_user"}
DEFAULT_WORKFLOW = "classic"
# What a project added through the CLI or the console starts with. A project that never
# chose keeps Classic, so existing projects behave exactly as before.
NEW_PROJECT_WORKFLOW = "standard"
MAX_FLOW_STEPS = 12

# Severity an AR finding carries, and the lowest one each strictness level lets block.
SEVERITY_RANK = {"blocker": 4, "major": 3, "minor": 2, "assumption": 1}
BLOCKING_RANK = {"normal": 3, "strict": 2, "extreme": 1}

BUILTIN_FLOWS = {
    "classic": {
        "name": "Classic",
        "summary": "SA analyses, SD/RD builds, CR reviews. The original workflow.",
        # The Classic workflow runs on the original state machine, not the step engine.
        "legacy": True,
        "steps": [{"role": "SA"}, {"role": "SD/RD"}, {"role": "REVIEW"}],
    },
    "standard": {
        "name": "Standard",
        "summary": "SA analyses, SD designs, RD builds, CR reviews. Design and development are separate steps.",
        "legacy": False,
        "steps": [{"role": "SA"}, {"role": "SD"}, {"role": "RD"}, {"role": "REVIEW"}],
    },
    "full": {
        "name": "Full",
        "summary": "SA, SD, adversarial and spec review, security review, then RD, CR, SEC and QA.",
        "legacy": False,
        "steps": [
            {"role": "SA"},
            {"role": "SD"},
            {"role": "AR"},
            {"role": "SR"},
            {"role": "SEC"},
            {"role": "RD"},
            {"role": "REVIEW"},
            {"role": "SEC"},
            {"role": "QA"},
        ],
    },
}


SELECTION_MODES = ("fixed", "pm", "auto")
DEFAULT_SELECTION = "fixed"
MAX_CUSTOM_WORKFLOWS = 20
WORKFLOW_NAME_MAX = 40


def validated_workflow(value, custom=None):
    """A workflow id: a built-in one or one of the project's own (`custom`)."""
    known = {**BUILTIN_FLOWS, **(custom or {})}
    if not isinstance(value, str) or value not in known:
        raise BridgeError(f"workflow must be one of {sorted(known)}")
    return value


def validated_selection(value):
    if value not in SELECTION_MODES:
        raise BridgeError(f"workflow_selection must be one of {list(SELECTION_MODES)}")
    return value


def validated_workflow_name(name):
    if not isinstance(name, str):
        raise BridgeError("Workflow name must be text")
    cleaned = " ".join(name.split())
    if not cleaned or len(cleaned) > WORKFLOW_NAME_MAX:
        raise BridgeError(f"Workflow name must be 1 to {WORKFLOW_NAME_MAX} characters")
    if any(not char.isprintable() for char in cleaned):
        raise BridgeError("Workflow name contains unsupported characters")
    return cleaned


def validated_steps(steps):
    """A workflow's steps as [{"role": ...}]. The first step must make something (a gate
    needs an earlier producer to send work back to) and later steps may be anything."""
    if isinstance(steps, list):
        steps = [step.get("role") if isinstance(step, dict) else step for step in steps]
    if not isinstance(steps, list) or not 1 <= len(steps) <= MAX_FLOW_STEPS:
        raise BridgeError(f"A workflow needs 1 to {MAX_FLOW_STEPS} steps")
    for role in steps:
        if role not in AGENT_ROLES:
            raise BridgeError(f"Unknown role in workflow: {role!r}")
    if steps[0] not in PRODUCER_ROLES:
        raise BridgeError(
            "A workflow must start with SA, SD, RD or SD/RD: a reviewer needs something to review"
        )
    return [{"role": role} for role in steps]


def validated_custom_workflows(value):
    """{id: {"name": str, "steps": [{"role": ...}]}}: the project's own workflows."""
    if not isinstance(value, dict) or len(value) > MAX_CUSTOM_WORKFLOWS:
        raise BridgeError(f"workflows must be an object of at most {MAX_CUSTOM_WORKFLOWS} entries")
    flows = {}
    for flow_id, flow in value.items():
        if (
            not isinstance(flow_id, str)
            or not re.fullmatch(r"[a-z0-9][a-z0-9-]{0,31}", flow_id)
            or flow_id in BUILTIN_FLOWS
            or not isinstance(flow, dict)
        ):
            raise BridgeError("workflows contains an invalid entry")
        flows[flow_id] = {
            "name": validated_workflow_name(flow.get("name")),
            "steps": validated_steps(flow.get("steps")),
        }
    return flows


def next_custom_id(existing):
    number = 2
    while f"flow-{number}" in existing:
        number += 1
    return f"flow-{number}"


def next_custom_name(existing, language):
    """"工作流 2", "工作流 3", ...: the default workflow is number 1, so the first one a
    person saves is 2. Numbers already taken, even by a renamed workflow, are skipped."""
    prefix = "Workflow" if language == "en" else "工作流"
    taken = {flow["name"] for flow in existing.values()}
    number = 2
    while f"{prefix} {number}" in taken:
        number += 1
    return f"{prefix} {number}"


def flow_definition(flow_id, custom=None):
    """A copy of a workflow with `id` filled in."""
    flow_id = validated_workflow(flow_id, custom)
    if flow_id in BUILTIN_FLOWS:
        flow = {**BUILTIN_FLOWS[flow_id], "builtin": True}
    else:
        flow = {
            "name": custom[flow_id]["name"], "summary": "", "legacy": False, "builtin": False,
            "steps": custom[flow_id]["steps"],
        }
    return {"id": flow_id, **flow, "steps": [dict(step) for step in flow["steps"]]}


# What a task's text asks for, used by the "auto" selection mode.
_SECURITY = re.compile(
    r"auth|login|log in|password|token|secret|permission|encrypt|payment|oauth|session|csrf|xss|"
    r"security|登入|登錄|密碼|權限|加密|付款|資安|安全|驗證",
    re.IGNORECASE,
)
_BIG = re.compile(
    r"refactor|migrat|architect|redesign|rewrite|schema|database|new module|from scratch|"
    r"重構|遷移|架構|重新設計|重寫|資料庫|從零",
    re.IGNORECASE,
)
_SMALL = re.compile(
    r"typo|rename|comment|readme|docs?|wording|copy change|格式|錯字|註解|文案|文件|改名|重新命名",
    re.IGNORECASE,
)


def choose_workflow(requirement, catalog, default_id):
    """Pick a workflow for a task from its text. Returns (workflow id, reason).

    A security-flavoured task needs a workflow with SEC; a large one needs design plus AR;
    a small one gets the shortest workflow; anything else keeps the project's own choice.
    Among workflows that qualify the shortest wins, so a task never pays for review it
    does not need."""
    text = requirement or ""
    needs = set()
    if _SECURITY.search(text):
        needs.add("SEC")
    if _BIG.search(text):
        needs |= {"SD", "AR"}
    ids = list(catalog)

    def roles(flow_id):
        return {step["role"] for step in catalog[flow_id]["steps"]}

    if needs:
        covering = [i for i in ids if needs <= roles(i)]
        if covering:
            pick = min(covering, key=lambda i: len(catalog[i]["steps"]))
            return pick, f"The task mentions {'/'.join(sorted(needs))}-level work"
        best = max(ids, key=lambda i: (len(needs & roles(i)), -len(catalog[i]["steps"])))
        return best, "No workflow covers everything the task needs; used the closest one"
    if _SMALL.search(text) and len(text) < 160:
        pick = min(ids, key=lambda i: len(catalog[i]["steps"]))
        return pick, "A small change needs only the shortest workflow"
    return default_id, "No special requirement detected; used the project's workflow"


def validated_review_gate(value):
    """{"ar_strictness": str, "max_rounds": int, "escalation": str}."""
    if not isinstance(value, dict):
        raise BridgeError("review_gate must be an object")
    merged = {**DEFAULT_REVIEW_GATE, **value}
    if merged["ar_strictness"] not in STRICTNESS_LEVELS:
        raise BridgeError(f"review_gate.ar_strictness must be one of {list(STRICTNESS_LEVELS)}")
    rounds = merged["max_rounds"]
    if not isinstance(rounds, int) or isinstance(rounds, bool):
        raise BridgeError("review_gate.max_rounds must be an integer")
    if not 1 <= rounds <= GATE_ROUNDS_HARD_CAP:
        raise BridgeError(f"review_gate.max_rounds must be between 1 and {GATE_ROUNDS_HARD_CAP}")
    if merged["escalation"] not in GATE_ESCALATIONS:
        raise BridgeError(f"review_gate.escalation must be one of {sorted(GATE_ESCALATIONS)}")
    return {key: merged[key] for key in DEFAULT_REVIEW_GATE}


def bounce_index(steps, index):
    """The producer step a failing gate at `index` sends work back to (None when no producer
    precedes it, so there is nobody to ask for a fix)."""
    for earlier in range(index - 1, -1, -1):
        if steps[earlier]["role"] in PRODUCER_ROLES:
            return earlier
    return None


_FINDING = re.compile(r"^\s*\[(blocker|major|minor|assumption)\]\s*(.+)$", re.IGNORECASE | re.DOTALL)
_EVIDENCE = re.compile(r"evidence\s*[:：]\s*(\S.{6,})", re.IGNORECASE | re.DOTALL)


def parse_findings(findings):
    """Split AR findings into (severity, claim, evidence-or-None) triples; anything not
    written as `[severity] claim | evidence: ...` is returned with severity None."""
    parsed = []
    for text in findings or []:
        match = _FINDING.match(text or "")
        if not match:
            parsed.append((None, (text or "").strip(), None))
            continue
        severity, rest = match.group(1).lower(), match.group(2)
        found = _EVIDENCE.search(rest)
        claim = rest[: found.start()].rstrip(" |-—–").strip() if found else rest.strip()
        parsed.append((severity, claim, found.group(1).strip() if found else None))
    return parsed


def judge_adversarial(report, strictness):
    """AR's verdict, decided here and not by the model: only findings that name their
    evidence and reach the strictness level's severity can block, so a hostile reviewer
    cannot hold the workflow up with unsupported claims. A [blocker] means the approach
    itself is unsound, which goes to the PM (REJECT) instead of back to the producer."""
    parsed = parse_findings(report.get("findings"))
    threshold = BLOCKING_RANK[strictness]
    supported = [(s, c, e) for s, c, e in parsed if s and e]
    blocking = [item for item in supported if SEVERITY_RANK[item[0]] >= threshold]
    discarded = len(parsed) - len(supported)
    if not blocking:
        verdict = "PASS"
    elif any(severity == "blocker" for severity, _, _ in blocking):
        verdict = "REJECT"
    else:
        verdict = "NEEDS_REVISION"
    return {
        "verdict": verdict,
        "blocking": [
            {"severity": s, "claim": c, "evidence": e} for s, c, e in blocking
        ],
        "discarded_unsupported": discarded,
        "reported_revision": bool(report.get("needs_revision")),
    }


def judge_gate(role, report, strictness):
    """Verdict for any gate role. AR is judged by evidence; every other gate trusts the
    reviewer's own needs_revision flag."""
    if role == "AR":
        return judge_adversarial(report, strictness)
    revise = bool(report.get("needs_revision"))
    return {
        "verdict": "NEEDS_REVISION" if revise else "PASS",
        "blocking": [
            {"severity": "major", "claim": text, "evidence": None}
            for text in (report.get("findings") or [])
        ]
        if revise
        else [],
        "discarded_unsupported": 0,
        "reported_revision": revise,
    }


_FORMAT = (
    "Write every finding as `[blocker|major|minor|assumption] <claim> | evidence: <file:line, "
    "quoted text or observed fact>`. A finding without evidence is discarded. "
    "Set needs_revision=true only when at least one finding should block."
)

_AR_BRIEF = {
    "normal": (
        "Report only flaws that would plausibly cause wrong behaviour, data loss or a security "
        "problem. Do not raise style or preference points."
    ),
    "strict": (
        "Also report minor correctness and maintainability problems, ambiguous requirements and "
        "missing edge cases, each with evidence."
    ),
    "extreme": (
        "Challenge every assumption the work relies on and look for any way it can fail. "
        "Label an unverified assumption `[assumption]` and state what evidence would settle it."
    ),
}

ROLE_BRIEFS = {
    "SA": "Analyse the requirement and propose an implementation plan.",
    "SD": (
        "Produce the system design: architecture, APIs and data model, with the decisions and "
        "trade-offs behind them. Design only: do not change source files."
    ),
    "RD": "Implement the approved plan and the design. Keep changes within the planned scope.",
    "SD/RD": "Design and implement the approved plan.",
    "REVIEW": "Review the code changes for correctness, architecture soundness and code quality.",
    "SR": (
        "Review the specification and design for contradictions, omissions and whether it can "
        "actually be implemented."
    ),
    "AR": "You are the adversarial reviewer: your job is to find what the others missed.",
    "SEC": "Review for security: vulnerabilities, permission and trust boundaries, secrets and unsafe input handling.",
    "SW": (
        "Write the specification for this work from everything decided so far: the requirement, "
        "the analysis, the design, and what the reviewers raised and how it was resolved. Put the "
        "complete Markdown document in the `document` field; `summary` is one paragraph about it. "
        "Write for an AI that will implement from this text alone and cannot ask questions: "
        "(1) Purpose and scope, plus explicit non-goals. (2) Glossary of every term used. "
        "(3) Decisions, each with its reason and the alternatives that were rejected. "
        "(4) Requirements as short numbered statements (REQ-001, REQ-002, ...), each one testable, "
        "using MUST / SHOULD / MAY. (5) Interfaces and data shapes written out exactly (names, "
        "types, errors). (6) Acceptance criteria as Given / When / Then, each pointing at the "
        "requirement it checks. (7) Open questions, and anything the conclusions contradict, listed "
        "rather than guessed. No filler, no marketing language, no vague words such as 'appropriate'. "
        "Record only what was concluded; if the discussion never settled a point, put it under open "
        "questions."
    ),
    "QA": (
        "Check the result against the acceptance criteria, using the host test evidence and the "
        "code. State which criteria are met and which are not."
    ),
}


def step_instruction(role, requirement, *, strictness="normal", max_rounds=None, feedback=None):
    """The text a flow step sends its agent."""
    parts = [ROLE_BRIEFS[role]]
    if role == "AR":
        parts.append(_AR_BRIEF[strictness])
    if role in GATE_ROLES:
        if role != "AR":
            parts.append("Block only on problems you can point to.")
        parts.append(_FORMAT)
        if max_rounds:
            parts.append(f"This gate can send work back at most {max_rounds} time(s).")
    if feedback:
        parts.append("The previous round was sent back. Address these points:\n" + feedback)
    if requirement:
        parts.append("Requirement:\n" + requirement)
    return "\n\n".join(parts)


def feedback_text(gate):
    """The blocking points of a gate result, ready to hand to the producer that redoes the work."""
    lines = []
    for item in gate.get("blocking") or []:
        evidence = f" (evidence: {item['evidence']})" if item.get("evidence") else ""
        lines.append(f"- [{item['severity']}] {item['claim']}{evidence}")
    if gate.get("reason"):
        lines.append("- " + gate["reason"])
    return "\n".join(lines) or "- The reviewer asked for a revision."
