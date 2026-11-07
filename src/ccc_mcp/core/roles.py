"""The role catalog: which workflow roles exist, what each one is for, and how a role is
shown to people (its abbreviation by default, or a name the operator gave it)."""

import random

from ..infra.errors import BridgeError

# Roles that dispatch an Agent. "SD/RD" is the combined role today's automatic workflow
# dispatches; "REVIEW" is the Code Reviewer (CR) and keeps its stored id so existing
# projects, executions and settings continue to work unchanged.
# The everyday roles (PA, RS, AN, PL, CS, WR, ED, VC, RV) are for work that is not software:
# research, planning, writing, decisions. They never touch files; each producer hands its result
# over as a document. They default to ChatGPT, which can browse, since the CLI agents here read
# the project folder only.
GENERAL_PRODUCERS = ("PA", "RS", "AN", "PL", "CS", "WR", "ED")
GENERAL_GATES = ("VC", "RV")
GENERAL_ROLES = (*GENERAL_PRODUCERS, *GENERAL_GATES)
ENGINEERING_ROLES = ("SA", "SD", "RD", "SD/RD", "REVIEW", "SR", "AR", "SEC", "QA", "SW")
# When ChatGPT plays one of these roles inside a workflow, its saved report is enough to hand
# over to the next step: the PM round that would only restate the summary is skipped. Besides
# the everyday roles that is every read-only specialist (SR, AR, SEC, QA, SW), so a workflow
# that mixes in an adversarial reviewer does not stop at it. SA (it needs a plan to approve),
# SD/RD (they change files) and REVIEW (it follows host tests) keep their PM round.
INTERACTIVE_AUTO_ADVANCE_ROLES = frozenset({*GENERAL_ROLES, "SR", "AR", "SEC", "QA", "SW"})
AGENT_ROLES = (*ENGINEERING_ROLES, *GENERAL_ROLES)
# PM is the interactive ChatGPT conversation that drives the workflow; it has an identity
# in the chat room (name, avatar) but no provider to configure.
PM_ROLE = "PM"
NAMEABLE_ROLES = (PM_ROLE, *AGENT_ROLES)

# Reviewers judge; they never change the work tree.
READ_ONLY_ROLES = frozenset({"SA", "REVIEW", "SR", "AR", "SEC", "QA", "SW", *GENERAL_ROLES})
# The reviewers that read the implementation's worktree, never write to it.
REVIEWER_ROLES = READ_ONLY_ROLES - {"SA", "SW", *GENERAL_PRODUCERS}

# The task state a role's execution runs under. SD stays IMPLEMENTING so a design step can
# follow an approved plan whether or not it is read-only.
RUNNING_STATE = {"SA": "ANALYZING", **dict.fromkeys(REVIEWER_ROLES, "REVIEWING")}

# The artifact an execution's report is saved as; roles not listed use IMPLEMENTATION.md.
REPORT_FILES = {
    "SA": "SA-REPORT.md",
    "REVIEW": "REVIEW.md",
    "SR": "SR-REPORT.md",
    "AR": "AR-REPORT.md",
    "SEC": "SEC-REPORT.md",
    "QA": "QA-REPORT.md",
    "SW": "SW-REPORT.md",
    **{role: f"{role}-REPORT.md" for role in GENERAL_ROLES},
}
# Where a producer's `document` is saved. SW's is the specification; the everyday producers'
# are the deliverables a person reads.
DOCUMENT_FILES = {"SW": "SPEC.md", **{role: f"{role}-DOCUMENT.md" for role in GENERAL_PRODUCERS}}


def writes_files(role, options=None, report_schema="agent_report"):
    """Whether an execution may change the working tree. A non-AgentReport request (Meeting
    Mode's clarification check) and a design-only dispatch (options["read_only"]) never do."""
    return (
        role not in READ_ONLY_ROLES
        and report_schema == "agent_report"
        and not (options or {}).get("read_only")
    )

ROLE_CATALOG = {
    "PM": ("PM", "Project Manager", "Requirements, task assignment and progress control"),
    "SA": ("SA", "System Analyst", "System requirements, business logic and functional specs"),
    "SD": ("SD", "System Designer", "Architecture, APIs, database and technical design"),
    "RD": ("RD", "Developer", "Implementation and bug fixing"),
    "SD/RD": ("SD/RD", "Designer + Developer", "Design and implement in one pass"),
    "REVIEW": ("CR", "Code Reviewer", "Code review, architecture soundness and code quality"),
    "SR": ("SR", "Specification Reviewer", "Spec review: contradictions, gaps and feasibility"),
    "AR": ("AR", "Adversarial Reviewer", "Challenges assumptions, hunts for flaws, demands evidence"),
    "SEC": ("SEC", "Security Reviewer", "Vulnerabilities, permissions and security risk"),
    "QA": ("QA", "Quality Assurance", "Functional and integration testing, acceptance checks"),
    "SW": (
        "SW", "Specification Writer",
        "Turns the discussion's conclusions into a specification an AI can implement from",
    ),
    "PA": ("PA", "Personal Assistant", "Understands the request, organises the work and hands over the result"),
    "RS": ("RS", "Researcher", "Looks things up and collects information on a topic"),
    "AN": ("AN", "Analyst", "Compares options, works through the numbers and weighs pros and cons"),
    "PL": ("PL", "Planner", "Turns the findings into a plan with steps, order, time and cost"),
    "CS": ("CS", "Consultant", "Gives expert advice and judges whether something is workable"),
    "WR": ("WR", "Writer", "Writes the article, proposal, report or copy"),
    "ED": ("ED", "Editor", "Polishes wording, tightens structure and makes it easy to read"),
    "VC": ("VC", "Fact Checker", "Checks claims, sources and figures against evidence"),
    "RV": ("RV", "Reviewer", "Checks the result and points out problems and gaps"),
}

# Every role has a score from 0 to 5 on six abilities, drawn as a radar chart in the console.
# The abilities are the same for software and everyday work so any mix of roles can be compared.
STAT_AXES = ("research", "analysis", "planning", "creation", "verification", "risk")
STAT_MAX = 5
ROLE_STATS = {
    #        research analysis planning creation verification risk
    "SA": (3, 5, 3, 1, 2, 2),
    "SD": (2, 3, 5, 2, 1, 2),
    "RD": (1, 2, 2, 5, 3, 1),
    "SD/RD": (1, 3, 4, 4, 3, 1),
    "REVIEW": (1, 3, 1, 0, 5, 4),
    "SR": (1, 4, 2, 0, 4, 3),
    "AR": (2, 4, 1, 0, 4, 5),
    "SEC": (2, 3, 1, 0, 3, 5),
    "QA": (1, 2, 1, 0, 5, 3),
    "SW": (2, 4, 3, 5, 2, 1),
    "PA": (2, 2, 4, 2, 1, 1),
    "RS": (5, 3, 1, 1, 3, 0),
    "AN": (2, 5, 3, 1, 3, 2),
    "PL": (1, 3, 5, 3, 1, 2),
    "CS": (3, 4, 4, 1, 2, 4),
    "WR": (2, 2, 2, 5, 1, 0),
    "ED": (0, 2, 2, 4, 3, 1),
    "VC": (4, 3, 0, 0, 5, 3),
    "RV": (1, 4, 1, 0, 5, 4),
}
# A member who scores at least this on an ability backs up the team's best on that ability.
STAT_STRONG = 3
STAT_BACKUP_BONUS = 0.5
STAT_GAP = 1


def role_stats(role):
    """{ability: 0-5} for one role; an unknown role has none."""
    values = ROLE_STATS.get(role)
    return dict(zip(STAT_AXES, values)) if values else None


def team_stats(roles):
    """The combined ability chart of a workflow's roles (each role counts once, however many
    steps it has). The strongest member sets an ability; every other member who is strong at
    it adds STAT_BACKUP_BONUS, up to STAT_MAX. `gaps` are abilities nobody is good at."""
    distinct = [role for role in dict.fromkeys(roles) if role in ROLE_STATS]
    team = {}
    for index, axis in enumerate(STAT_AXES):
        scores = sorted((ROLE_STATS[role][index] for role in distinct), reverse=True)
        best = scores[0] if scores else 0
        backups = sum(1 for score in scores[1:] if score >= STAT_STRONG)
        team[axis] = min(STAT_MAX, round(best + STAT_BACKUP_BONUS * backups, 1))
    return {
        "axes": list(STAT_AXES),
        "max": STAT_MAX,
        "team": team,
        "power": round(sum(team.values()), 1),
        "gaps": [axis for axis in STAT_AXES if team[axis] <= STAT_GAP],
        "roles": {role: role_stats(role) for role in distinct},
    }


ROLE_NAME_MAX = 24

# Pixel characters a role can wear in the chat room; the console draws them. A role with no
# choice wears its own default.
AVATAR_IDS = (
    "boss", "detective", "designer", "dev", "angel", "monk", "devil", "knight", "tester",
    "person", "cat", "robot", "bear", "frog", "writer",
    "assistant", "researcher", "analyst", "planner", "consultant", "author", "editor",
    "checker", "reviewer",
)
DEFAULT_AVATARS = {
    "PM": "boss", "SA": "detective", "SD": "designer", "RD": "dev", "SD/RD": "dev",
    "REVIEW": "angel", "SR": "monk", "AR": "devil", "SEC": "knight", "QA": "tester",
    "SW": "writer",
    "PA": "assistant", "RS": "researcher", "AN": "analyst", "PL": "planner", "CS": "consultant",
    "WR": "author", "ED": "editor", "VC": "checker", "RV": "reviewer",
}


def validated_role_avatars(value):
    """{role: avatar id}. Naming the role's own default is the same as no choice."""
    if not isinstance(value, dict):
        raise BridgeError("role_avatars must be an object")
    avatars = {}
    for role, avatar in value.items():
        if role not in NAMEABLE_ROLES:
            raise BridgeError("role_avatars contains an unsupported role")
        if avatar is None or avatar == "":
            continue
        if avatar not in AVATAR_IDS:
            raise BridgeError(f"Unknown avatar; choose one of {list(AVATAR_IDS)}")
        if avatar != DEFAULT_AVATARS.get(role):
            avatars[role] = avatar
    return avatars


def default_provider(role):
    """Everyday roles default to ChatGPT (they may need to browse); engineering reviewers to
    Codex and builders to Claude, the split `best-model` uses."""
    if role in GENERAL_ROLES:
        return "chatgpt"
    return "codex" if role in READ_ONLY_ROLES else "claude"


def role_abbreviation(role):
    return ROLE_CATALOG[role][0] if role in ROLE_CATALOG else role


def validated_role_names(value):
    """{role: display name}. A blank name is dropped, which brings back the default
    (the role's abbreviation)."""
    if not isinstance(value, dict):
        raise BridgeError("role_names must be an object")
    names = {}
    for role, name in value.items():
        if role not in NAMEABLE_ROLES:
            raise BridgeError("role_names contains an unsupported role")
        cleaned = validated_role_name(name)
        if cleaned:
            names[role] = cleaned
    return names


def validated_role_name(name):
    if name is None:
        return None
    if not isinstance(name, str):
        raise BridgeError("Role name must be text")
    cleaned = " ".join(name.split())
    if len(cleaned) > ROLE_NAME_MAX:
        raise BridgeError(f"Role name must be at most {ROLE_NAME_MAX} characters")
    if any(not char.isprintable() for char in cleaned):
        raise BridgeError("Role name contains unsupported characters")
    return cleaned or None


# What a role says when the boss sends it back to redo its work. It is shown in the chat as that
# role's own message, so the text never names the role.
RETURN_GREETINGS = {
    "zh-TW": (
        "我來了我來了，老大！馬上照你的意見重做～",
        "報告老大，我出場啦！這就重來一遍，等我一下下～",
        "收到收到！老大一聲令下，我立刻回來改！",
        "來啦來啦～老大別急，這次我會做得更好！",
        "老大，我回來啦！剛剛的意見我記在小本本上了，開工！",
    ),
    "ch": (
        "我来了我来了，老大！马上按你的意见重做～",
        "报告老大，我出场啦！这就重来一遍，等我一下下～",
        "收到收到！老大一声令下，我立刻回来改！",
        "来啦来啦～老大别急，这次我会做得更好！",
        "老大，我回来啦！刚才的意见我记在小本本上了，开工！",
    ),
    "en": (
        "I'm here, I'm here, boss! Redoing it with your notes right now.",
        "Reporting for duty, boss! Give me a moment and I'll take another swing.",
        "Got it, got it! Back on it with your feedback in hand.",
        "Coming, coming~ no worries boss, this round will be better!",
        "I'm back, boss! Your note is in my little notebook. Let's go!",
    ),
}


def return_greeting(language, pick=None):
    """A playful line for a role coming back to redo its work; `pick` makes it deterministic."""
    lines = RETURN_GREETINGS.get(language) or RETURN_GREETINGS["en"]
    index = (pick if pick is not None else random.randrange(len(lines))) % len(lines)
    return lines[index]
