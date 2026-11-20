"""The Goal Plan: what the Delivery PM proposes and what ccc-mcp is willing to store.

A model's answer is data, never a command. Whatever produced a plan (a Codex or Claude CLI run,
or a person importing JSON), it goes through the same two gates before it is stored:

1. the pydantic models below (every field typed, unknown fields refused), and
2. `validate_plan`, the semantic rules: phase ids are unique, every dependency exists and the
   dependency graph has no cycle, every phase has acceptance criteria that can be checked, a
   host-run criterion is an argument list (never a shell string) that does not publish or
   rewrite anything, and the sizes are bounded.

The number of phases is decided by the PM from the requirement. MAX_PHASES is a runaway guard,
not a template.

Nothing here runs anything. The budget block is a *declaration*: a phase run (core/phase_runner.py)
enforces it in code. The plan's own `enforced` flag is always False and cannot be set by a plan,
so a plan can never claim, or switch off, the enforcement.
"""

import hashlib
import os
import re
import unicodedata
from typing import Literal

from pydantic import Field, ValidationError

from ..infra.errors import BridgeError
from ..infra.security import canonical, reject_secrets
from .models import StrictModel

MAX_PHASES = 12
MAX_CRITERIA = 20
MAX_OUTPUTS = 20
MAX_LIST_ITEMS = 30
MAX_ARGS = 50
PLAN_BYTES_MAX = 200_000
TEXT_LIMITS = {"title": 120, "scope": 2000, "statement": 500, "summary": 3000, "item": 500,
               "question": 500, "message": 2000, "arg": 500}
ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,39}")
PROVIDER_HINTS = ("codex", "claude")
VERIFY_KINDS = ("host_test", "host_command", "reviewer_check", "manual")

# Budget caps (declared, not yet enforced) and the defaults Stage B/C intend to use.
DEFAULT_BUDGET = {"max_attempts_per_phase": 3, "max_agent_dispatches": 20,
                  "max_wall_clock_minutes": 60}
BUDGET_CAPS = {"max_attempts_per_phase": 5, "max_agent_dispatches": 100,
               "max_wall_clock_minutes": 240}

_SHELLS = {"sh", "bash", "zsh", "dash", "fish", "csh", "tcsh", "ksh", "cmd", "cmd.exe",
           "powershell", "powershell.exe", "pwsh", "pwsh.exe"}
_SHELL_TOKENS = {";", "&&", "||", "|", "&", ">", ">>", "<", "`", "$(", "${"}
_GIT_READ_ONLY = {"diff", "status", "log", "show", "ls-files", "rev-parse", "grep", "cat-file",
                  "describe", "blame"}
_NEVER = {"curl", "wget", "ssh", "scp", "sftp", "rsync", "nc", "ncat", "telnet", "sudo", "su",
          "dd", "mkfs", "docker", "podman", "kubectl", "helm", "terraform", "gh", "glab",
          "twine", "aws", "gcloud", "az", "rm", "chmod", "chown"}
_PUBLISHERS = {"npm": {"publish", "login", "adduser", "token"},
               "yarn": {"publish", "npm"}, "pnpm": {"publish"},
               "pip": {"upload"}, "cargo": {"publish", "login"}, "gem": {"push"}}


class Verify(StrictModel):
    """How a criterion is checked. Only `host_test` and `host_command` are decided by the host;
    `reviewer_check` is advice from a reviewer and `manual` always waits for a person."""

    kind: Literal["host_test", "host_command", "reviewer_check", "manual"]
    command: list[str] | None = None
    expect_exit_code: int | None = None
    description: str | None = None


class Criterion(StrictModel):
    id: str
    statement: str
    verify: Verify


class PhaseProposal(StrictModel):
    id: str
    title: str
    scope: str
    outputs: list[str]
    depends_on: list[str]
    acceptance: list[Criterion]
    workflow: str
    provider_hint: str | None = None
    risk: Literal["low", "medium", "high"]


class DecisionNeeded(StrictModel):
    question: str
    options: list[str]


class GoalPlanProposal(StrictModel):
    summary: str
    in_scope: list[str]
    out_of_scope: list[str]
    phases: list[PhaseProposal]
    final_acceptance: list[Criterion]
    decisions_needed: list[DecisionNeeded]
    assumptions: list[str]
    risks: list[str]


class DeliveryPMReport(StrictModel):
    """What the Delivery PM returns: a short chat message and the plan."""

    message: str
    plan: GoalPlanProposal


class Budget(StrictModel):
    max_attempts_per_phase: int = DEFAULT_BUDGET["max_attempts_per_phase"]
    max_agent_dispatches: int = DEFAULT_BUDGET["max_agent_dispatches"]
    max_wall_clock_minutes: int = DEFAULT_BUDGET["max_wall_clock_minutes"]
    enforced: Literal[False] = False


class StoredGoalPlan(GoalPlanProposal):
    schema_version: Literal[1] = 1
    budget: Budget = Field(default_factory=Budget)


class GoalPlanInvalid(BridgeError):
    """The plan is refused. `problems` lists every reason found, not just the first."""

    def __init__(self, problems):
        self.problems = list(problems)
        shown = "; ".join(self.problems[:8])
        more = f" (and {len(self.problems) - 8} more)" if len(self.problems) > 8 else ""
        super().__init__(f"Invalid Goal Plan: {shown}{more}")


def _text(value, label, limit, problems, *, required=True):
    if value is None:
        value = ""
    if not isinstance(value, str):
        problems.append(f"{label} must be text")
        return ""
    value = value.replace("\r\n", "\n").replace("\r", "\n").strip()
    for char in value:
        if char in "\n\t":
            continue
        if unicodedata.category(char) in {"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"}:
            problems.append(f"{label} contains unsupported characters")
            break
    if required and not value:
        problems.append(f"{label} is required")
    if len(value) > limit:
        problems.append(f"{label} must be at most {limit} characters")
    return value


def forbidden_command(argv):
    """Why a host-run command must not appear in a plan, or None. A best-effort screen of what a
    plan may propose, not a sandbox: whatever Stage B runs must still be confined where it runs."""
    program = os.path.basename(argv[0]).lower()
    if program in _SHELLS:
        return f"{argv[0]!r} is a shell; give the program and its arguments directly"
    if " " in argv[0] or "\t" in argv[0]:
        return "the first item must be one program, not a command line"
    if program in _NEVER:
        return f"{program!r} is not allowed in a plan (network, publishing or destructive use)"
    for token in argv[1:]:
        if token in _SHELL_TOKENS:
            return f"shell operator {token!r} is not allowed; a command is a plain argument list"
    if program == "git":
        subcommand = next((arg for arg in argv[1:] if not arg.startswith("-")), None)
        if subcommand not in _GIT_READ_ONLY:
            return f"git {subcommand or ''} can change the repository; only read-only git is allowed"
    if program in _PUBLISHERS and any(arg in _PUBLISHERS[program] for arg in argv[1:]):
        return f"{program} publishing or login commands are not allowed"
    return None


def _check_verify(verify, label, problems):
    if verify.kind in {"host_test", "host_command"}:
        command = verify.command
        if not command:
            problems.append(f"{label}: a host check needs a command (an argument list)")
        else:
            if len(command) > MAX_ARGS:
                problems.append(f"{label}: command has more than {MAX_ARGS} arguments")
            if any(not isinstance(arg, str) or not arg.strip() or len(arg) > TEXT_LIMITS["arg"]
                   for arg in command):
                problems.append(f"{label}: every command argument must be non-empty text")
            elif (reason := forbidden_command(command)) is not None:
                problems.append(f"{label}: {reason}")
        code = verify.expect_exit_code
        if code is not None and not 0 <= code <= 255:
            problems.append(f"{label}: expect_exit_code must be 0 to 255")
    else:
        if verify.command:
            problems.append(f"{label}: only a host check has a command")
        if verify.expect_exit_code is not None:
            problems.append(f"{label}: only a host check has expect_exit_code")
        _text(verify.description, f"{label}: description", TEXT_LIMITS["statement"], problems)


def _check_criteria(criteria, label, seen, problems, *, minimum=1):
    if len(criteria) < minimum:
        problems.append(f"{label} needs at least {minimum} acceptance criterion")
    if len(criteria) > MAX_CRITERIA:
        problems.append(f"{label} has more than {MAX_CRITERIA} criteria")
    for index, criterion in enumerate(criteria):
        where = f"{label} criterion {index + 1}"
        if not ID_PATTERN.fullmatch(criterion.id or ""):
            problems.append(f"{where}: id must be letters, digits, '-' or '_' (up to 40)")
        elif criterion.id.lower() in seen:
            problems.append(f"{where}: duplicate criterion id {criterion.id!r}")
        seen.add((criterion.id or "").lower())
        criterion.statement = _text(
            criterion.statement, f"{where}: statement", TEXT_LIMITS["statement"], problems)
        _check_verify(criterion.verify, where, problems)


def _find_cycle(graph):
    """One dependency cycle as a list of ids, or None. Iterative, so depth is no limit."""
    state = {}
    for start in graph:
        if state.get(start):
            continue
        stack = [(start, iter(graph[start]))]
        path = [start]
        state[start] = 1
        while stack:
            node, children = stack[-1]
            advanced = False
            for child in children:
                if child not in graph:
                    continue
                if state.get(child) == 1:
                    return [*path[path.index(child):], child]
                if not state.get(child):
                    state[child] = 1
                    path.append(child)
                    stack.append((child, iter(graph[child])))
                    advanced = True
                    break
            if not advanced:
                state[node] = 2
                path.pop()
                stack.pop()
    return None


def validate_plan(plan, *, known_workflows):
    """Return the normalised StoredGoalPlan, or raise GoalPlanInvalid with every problem found.

    `plan` is a dict (or a model) from any source. `known_workflows` is the set of workflow ids
    a phase may name; the Classic workflow, which has no step limit of its own, is never in it."""
    problems = []
    try:
        if isinstance(plan, StoredGoalPlan | GoalPlanProposal):
            plan = plan.model_dump()
        model = StoredGoalPlan.model_validate(plan)
    except ValidationError as exc:
        details = [
            f"{'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
            for error in exc.errors()[:10]
        ]
        raise GoalPlanInvalid(details) from None
    try:
        reject_secrets(model.model_dump())
    except BridgeError as exc:
        raise GoalPlanInvalid([str(exc)]) from None
    if len(canonical(model.model_dump()).encode("utf-8")) > PLAN_BYTES_MAX:
        raise GoalPlanInvalid([f"the plan is larger than {PLAN_BYTES_MAX} bytes"])

    model.summary = _text(model.summary, "summary", TEXT_LIMITS["summary"], problems)
    for name in ("in_scope", "out_of_scope", "assumptions", "risks"):
        items = getattr(model, name)
        if len(items) > MAX_LIST_ITEMS:
            problems.append(f"{name} has more than {MAX_LIST_ITEMS} items")
        setattr(model, name, [
            _text(item, f"{name} item", TEXT_LIMITS["item"], problems) for item in items])
    if not model.in_scope:
        problems.append("in_scope must say what the goal covers")
    for index, decision in enumerate(model.decisions_needed):
        decision.question = _text(
            decision.question, f"decisions_needed {index + 1}: question",
            TEXT_LIMITS["question"], problems)
        decision.options = [
            _text(option, f"decisions_needed {index + 1}: option", TEXT_LIMITS["item"], problems)
            for option in decision.options]

    phases = model.phases
    if not 1 <= len(phases) <= MAX_PHASES:
        problems.append(f"a plan needs 1 to {MAX_PHASES} phases, not {len(phases)}")
    ids, seen_criteria = [], set()
    for index, phase in enumerate(phases):
        where = f"phase {index + 1}"
        if not ID_PATTERN.fullmatch(phase.id or ""):
            problems.append(f"{where}: id must be letters, digits, '-' or '_' (up to 40)")
        elif phase.id.lower() in ids:
            problems.append(f"{where}: duplicate phase id {phase.id!r}")
        ids.append((phase.id or "").lower())
        phase.title = _text(phase.title, f"{where}: title", TEXT_LIMITS["title"], problems)
        phase.scope = _text(phase.scope, f"{where}: scope", TEXT_LIMITS["scope"], problems)
        if not phase.outputs:
            problems.append(f"{where}: outputs must name what the phase delivers")
        if len(phase.outputs) > MAX_OUTPUTS:
            problems.append(f"{where}: more than {MAX_OUTPUTS} outputs")
        phase.outputs = [
            _text(item, f"{where}: output", TEXT_LIMITS["item"], problems)
            for item in phase.outputs]
        if phase.workflow not in known_workflows:
            problems.append(
                f"{where}: workflow {phase.workflow!r} is not one this project can run "
                f"({', '.join(sorted(known_workflows)) or 'none'})")
        if phase.provider_hint is not None and phase.provider_hint not in PROVIDER_HINTS:
            problems.append(f"{where}: provider_hint must be one of {list(PROVIDER_HINTS)}")
        _check_criteria(phase.acceptance, where, seen_criteria, problems)

    by_id = {phase.id.lower(): phase for phase in phases if ID_PATTERN.fullmatch(phase.id or "")}
    graph = {}
    for index, phase in enumerate(phases):
        where = f"phase {index + 1}"
        if len({dep.lower() for dep in phase.depends_on}) != len(phase.depends_on):
            problems.append(f"{where}: depends_on lists a phase twice")
        for dep in phase.depends_on:
            if dep.lower() == (phase.id or "").lower():
                problems.append(f"{where}: depends on itself")
            elif dep.lower() not in by_id:
                problems.append(f"{where}: depends on {dep!r}, which is not a phase of this plan")
        graph[(phase.id or "").lower()] = [dep.lower() for dep in phase.depends_on]
    cycle = _find_cycle(graph)
    if cycle:
        problems.append("phases depend on each other in a cycle: " + " -> ".join(cycle))

    _check_criteria(model.final_acceptance, "final_acceptance", seen_criteria, problems)
    for name, cap in BUDGET_CAPS.items():
        value = getattr(model.budget, name)
        if not 1 <= value <= cap:
            problems.append(f"budget.{name} must be 1 to {cap}")
    if problems:
        raise GoalPlanInvalid(problems)
    return model


def plan_content(model):
    """The canonical text that is stored and hashed, and the hash that binds an approval to it."""
    text = canonical(model.model_dump())
    return text, hashlib.sha256(text.encode("utf-8")).hexdigest()


def phase_rows(model):
    """(ordinal, phase id, title, depends_on, content) for every phase, in plan order."""
    return [
        (index + 1, phase.id, phase.title, list(phase.depends_on), phase.model_dump())
        for index, phase in enumerate(model.phases)
    ]
