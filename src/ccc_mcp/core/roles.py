"""The role catalog: which workflow roles exist, what each one is for, and how a role is
shown to people (its abbreviation by default, or a name the operator gave it)."""

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
