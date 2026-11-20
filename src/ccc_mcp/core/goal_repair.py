"""The Delivery PM's repair spec: what a model may say about a failed attempt, and what is kept.

When a phase's host test or review fails, the Delivery PM reads the evidence and answers with a
`RepairSpec`. Like a plan, the answer is data and passes two gates before anything acts on it:

1. the pydantic models below (every field typed, unknown fields refused), and
2. `validate_repair`, the rules that hold whatever the model says:

   * it has no field through which the approved acceptance criteria, the requirement, the test
     command or any permission could be changed, and it must account for every approved criterion
     by its id, neither dropping nor inventing one;
   * the tests to re-run are the project's configured test command, exactly, or the spec is refused;
   * the files it wants changed are relative paths that are not protected (git metadata, ccc-mcp
     metadata, CI definitions, the test runner's own configuration);
   * a root cause called "confirmed" must quote the evidence it was given. A quote that is not in
     the evidence is not evidence, and the cause is recorded as a hypothesis instead.

The message the developer receives is composed here, not copied from the model: the fixed rules
(what may not be changed) are added by ccc-mcp and cannot be left out by the model.
"""

import hashlib
import re
from pathlib import PurePosixPath
from typing import Literal

from ..infra.errors import BridgeError
from ..infra.security import canonical, redact, reject_secrets
from .models import StrictModel

MAX_ITEMS = 20
QUOTE_MIN, QUOTE_MAX = 3, 500
INSTRUCTIONS_MAX = 3000
EVIDENCE_TEXT_MAX = 12_000
DIFF_TEXT_MAX = 30_000

# What a repair attempt may not touch, whoever asks. The test runner's own configuration is here
# because weakening it is the cheapest way for an automatic loop to make a failing test pass.
PROTECTED_DIRECTORIES = (".git", ".ccc-mcp", ".github", ".gitlab")
PROTECTED_FILES = (
    "pytest.ini", "tox.ini", "setup.cfg", "pyproject.toml", "conftest.py", ".coveragerc",
    "noxfile.py", ".gitlab-ci.yml", "Jenkinsfile",
)


class EvidenceQuote(StrictModel):
    source: Literal["test_output", "diff", "review", "requirement", "acceptance", "history"]
    quote: str


class RootCause(StrictModel):
    statement: str
    confidence: Literal["confirmed", "hypothesis"]
    evidence: list[EvidenceQuote]


class CompletionCondition(StrictModel):
    criterion_id: str
    how_verified: str


class RepairSpec(StrictModel):
    decision: Literal["repair", "needs_human"]
    diagnosis: str
    root_causes: list[RootCause]
    change_scope: list[str]
    must_not_change: list[str]
    rerun_tests: list[str]
    completion_conditions: list[CompletionCondition]
    needs_reanalysis: bool
    reanalysis_reason: str | None = None
    instructions: str
    needs_human_reason: str | None = None
    # Stage C. The Delivery PM may say that the failure cannot be fixed inside the approved scope
    # (the requirement or the architecture would have to change). That is only a signal: a revised
    # plan is a new version that a person has to approve; nothing here changes the approved plan.
    plan_change_needed: bool = False
    plan_change_summary: str | None = None


class DeliveryPMRepair(StrictModel):
    """The whole structured answer of one repair analysis."""

    message: str
    spec: RepairSpec


class RepairInvalid(BridgeError):
    """The repair spec is refused. `problems` lists every reason found."""

    def __init__(self, problems):
        self.problems = list(problems)
        shown = "; ".join(self.problems[:6])
        more = f" (and {len(self.problems) - 6} more)" if len(self.problems) > 6 else ""
        super().__init__(f"Invalid repair spec: {shown}{more}")


# ---- paths and commands ---------------------------------------------------------------------


def protected_reason(path):
    """Why a repository path is off limits to a repair, or None."""
    if not isinstance(path, str) or not path.strip():
        return "empty path"
    clean = path.strip().replace("\\", "/")
    pure = PurePosixPath(clean)
    if pure.is_absolute() or (len(clean) > 1 and clean[1] == ":"):
        return "absolute path"
    if ".." in pure.parts:
        return "path leaves the project"
    parts = [part for part in pure.parts if part != "."]
    if not parts:
        return "empty path"
    if parts[0] in PROTECTED_DIRECTORIES:
        return f"{parts[0]}/ is protected"
    if parts[-1] in PROTECTED_FILES:
        return f"{parts[-1]} is protected (test and CI configuration)"
    return None


def protected_changes(paths):
    """The protected paths among `paths` (changed or new files), with why."""
    found = []
    for path in paths or []:
        reason = protected_reason(path)
        if reason and reason not in {"empty path"}:
            found.append({"path": path, "reason": reason})
    return found


_PYTHON = re.compile(r"python(?:\d+(?:\.\d+)?)?(?:\.exe)?", re.IGNORECASE)


def normalize_command(argv):
    """An argv with its interpreter name made comparable (python3.12, python.exe -> python)."""
    items = [str(item) for item in argv or []]
    if not items:
        return []
    name = re.split(r"[\\/]", items[0])[-1]
    if _PYTHON.fullmatch(name):
        name = "python"
    elif name.lower().endswith(".exe"):
        name = name[:-4]
    return [name, *items[1:]]


def same_command(left, right):
    return bool(left) and normalize_command(left) == normalize_command(right)


def coverage(criterion, test_command):
    """How a criterion of an approved plan is decided: "host_test" when the host's own, operator
    configured test command decides it, "needs_person" otherwise. Only that one command ever runs,
    so a criterion counts as machine-verified only if its command *is* that command and it expects
    the exit code the host's pass means (0). A criterion that expects any other exit code, names a
    different command, or is a reviewer check, a manual check or a command that is never run, is a
    person's to decide. This can only send a criterion to a person, never away from one."""
    verify = criterion["verify"]
    expected = verify.get("expect_exit_code")
    if (verify["kind"] == "host_test" and expected in (None, 0)
            and same_command(verify.get("command") or [], test_command)):
        return "host_test"
    return "needs_person"


# ---- the evidence the Delivery PM is given ----------------------------------------------------

_NOISE = [
    (re.compile(r"\bin \d+(?:\.\d+)?s\b"), "in Ns"),
    (re.compile(r"0x[0-9a-fA-F]+"), "0xADDR"),
    (re.compile(r"(?:/[\w.\-@+]+){3,}"), "/PATH"),
    (re.compile(r"[A-Za-z]:\\(?:[\w.\- ]+\\)+"), "C:\\PATH\\"),
    (re.compile(r"\b\d+(?:\.\d+)?\s*(?:ms|s|seconds)\b"), "N"),
]
_FAILURE_LINE = re.compile(
    r"^(?:FAILED|ERROR)\b|^E\s{2,}|\b\d+ (?:failed|error)|AssertionError|Traceback|Error:",
)


def _denoise(line):
    for pattern, replacement in _NOISE:
        line = pattern.sub(lambda _match, text=replacement: text, line)
    return line


def failure_signature(stdout, stderr):
    """A stable fingerprint of *what* failed, not when or where: the failing test ids and error
    lines with timings, addresses and paths removed. Two attempts that fail the same way give the
    same signature, which is how a loop that is going nowhere is told apart from one that moves.
    Returns (fingerprint, the normalised lines it is made of, the same lines as they were written,
    for a person to read)."""
    lines, raw = [], []
    for source in f"{stdout or ''}\n{stderr or ''}".splitlines():
        line = source.strip()
        if not line or not _FAILURE_LINE.search(line):
            continue
        raw.append(redact(line)[:200])
        lines.append(_denoise(line)[:300])
    if not lines:
        tail = [line.strip() for line in f"{stdout or ''}\n{stderr or ''}".splitlines() if line.strip()]
        raw = [redact(line)[:200] for line in tail[-8:]]
        lines = [_denoise(line)[:300] for line in tail[-8:]]
    unique = sorted(set(lines))
    digest = hashlib.sha256("\n".join(unique).encode("utf-8", "replace")).hexdigest()[:16]
    return digest, unique[:30], raw[:8]


def clip(text, limit, *, keep="tail"):
    """`text` cut to `limit` characters, and whether it was cut. A failure is usually at the end
    of a test log, so by default the tail is kept."""
    text = redact(text or "")
    if len(text) <= limit:
        return text, False
    return (text[-limit:] if keep == "tail" else text[:limit]), True


def build_bundle(
    *, goal, plan, phase, criteria, test_command, attempts, repairs, budget, failure, operator_note
):
    """Everything the Delivery PM is told, as one dictionary, plus its hash and a short summary.
    The text of the evidence is redacted and clipped here; what was clipped is said so."""
    bundle = {
        "goal": {"id": goal["id"], "requirement": goal["requirement"]},
        "approved_plan": {"version": plan["version"], "sha256": plan["sha256"]},
        "phase": phase,
        "acceptance_criteria": criteria,
        "host_test_command": list(test_command),
        "attempts_so_far": attempts,
        "earlier_repairs": repairs,
        "budget_remaining": budget,
        "failure": failure,
        "fixed_by_the_approved_plan": [
            "the requirement and the acceptance criteria above",
            "the host test command (it is run by ccc-mcp, not by you)",
            "the roles, providers and permissions of the team",
            "no commit, push, reset, rebase, release or deploy",
        ],
    }
    if operator_note:
        bundle["operator_note"] = operator_note
    text = canonical(bundle)
    return bundle, hashlib.sha256(text.encode("utf-8")).hexdigest(), text


def evidence_text(bundle):
    """The raw text of everything the Delivery PM was shown, for checking its quotes against. Built
    from the original strings, not from the JSON, whose escaping would stop a verbatim quote that
    spans a newline or contains a quotation mark from matching."""
    parts = [bundle["goal"]["requirement"], bundle["phase"].get("scope", "")]
    parts += [item["statement"] for item in bundle["acceptance_criteria"]]
    failure = bundle["failure"]
    host = failure.get("host_test") or {}
    parts += [host.get("stdout", ""), host.get("stderr", ""), host.get("reason", "") or ""]
    parts += [failure.get("diff", "")]
    review = failure.get("review") or {}
    parts += [review.get("reason", "") or ""]
    parts += [
        item.get("claim", "") + " " + (item.get("evidence") or "")
        for item in review.get("blocking", [])
    ]
    for item in bundle.get("earlier_repairs", []):
        parts += [item.get("diagnosis", ""), item.get("instructions", "")]
    return "\n".join(str(part) for part in parts if part)


# ---- validating the answer ---------------------------------------------------------------------


def _squash(text):
    return re.sub(r"\s+", " ", text or "").strip()


def _clean(value, label, limit, problems, *, required=True):
    if not isinstance(value, str):
        problems.append(f"{label} must be text")
        return ""
    value = value.replace("\r\n", "\n").strip()
    if required and not value:
        problems.append(f"{label} is required")
    if len(value) > limit:
        problems.append(f"{label} must be at most {limit} characters")
    return value


def validate_repair(report, *, evidence_text, criteria_ids, test_command):
    """Check a `DeliveryPMRepair` (a dict from the provider) and return `(stored, notes)`.

    `stored` is the spec as it is kept and acted on (quotes marked verified or not, causes whose
    quotes are not in the evidence downgraded to hypotheses); `notes` says what was downgraded.
    Raises RepairInvalid, with every problem found, when the spec is refused."""
    problems, notes = [], []
    try:
        parsed = DeliveryPMRepair.model_validate(report)
    except Exception as exc:
        raise RepairInvalid([f"not a valid repair spec: {str(exc)[:500]}"]) from None
    spec = parsed.spec.model_dump()
    message = _clean(parsed.message, "message", 2000, problems)
    spec["diagnosis"] = _clean(spec["diagnosis"], "diagnosis", 3000, problems)
    spec["instructions"] = _clean(
        spec["instructions"], "instructions", INSTRUCTIONS_MAX, problems,
        required=spec["decision"] == "repair",
    )
    for key in ("change_scope", "must_not_change", "root_causes", "completion_conditions"):
        if len(spec[key]) > MAX_ITEMS:
            problems.append(f"{key} has more than {MAX_ITEMS} items")
    try:
        reject_secrets({"message": message, "spec": spec})
    except BridgeError as exc:
        problems.append(str(exc))

    if spec["plan_change_needed"]:
        if spec["decision"] != "needs_human":
            problems.append(
                "a change of plan can only be asked for with decision=needs_human; a repair works "
                "inside the approved plan")
        spec["plan_change_summary"] = _clean(
            spec["plan_change_summary"], "plan_change_summary", 1500, problems)
    elif spec["plan_change_summary"]:
        problems.append("plan_change_summary is only for plan_change_needed=true")
    if spec["decision"] == "needs_human":
        spec["needs_human_reason"] = _clean(
            spec["needs_human_reason"], "needs_human_reason", 2000, problems)
    else:
        # The tests that decide are the project's configured command, nothing the model chooses.
        if not same_command(spec["rerun_tests"], test_command):
            problems.append(
                "rerun_tests must be exactly the project's configured test command; the model "
                "cannot choose, narrow or replace the tests that decide"
            )
        ids = [item["criterion_id"] for item in spec["completion_conditions"]]
        missing = [item for item in criteria_ids if item not in ids]
        unknown = [item for item in ids if item not in criteria_ids]
        if missing:
            problems.append(f"completion_conditions must cover every approved criterion; missing {missing}")
        if unknown:
            problems.append(f"completion_conditions names criteria that are not approved: {unknown}")
        if len(set(ids)) != len(ids):
            problems.append("completion_conditions lists a criterion twice")
        for item in spec["completion_conditions"]:
            item["how_verified"] = _clean(item["how_verified"], "how_verified", 500, problems)
        for path in spec["change_scope"]:
            reason = protected_reason(path)
            if reason:
                problems.append(f"change_scope {path!r}: {reason}")
        if not spec["root_causes"]:
            problems.append("a repair needs at least one root cause (a hypothesis is allowed)")
        if spec["needs_reanalysis"]:
            spec["reanalysis_reason"] = _clean(
                spec["reanalysis_reason"], "reanalysis_reason", 1000, problems)

    haystack = _squash(evidence_text)
    for cause in spec["root_causes"]:
        cause["statement"] = _clean(cause["statement"], "root cause", 1000, problems)
        verified = 0
        for item in cause["evidence"]:
            item["quote"] = _clean(item["quote"], "evidence quote", QUOTE_MAX, problems)
            quote = _squash(item["quote"])
            item["verified"] = bool(
                len(quote) >= QUOTE_MIN and quote in haystack
            )
            verified += item["verified"]
        if cause["confidence"] == "confirmed" and not verified:
            cause["confidence"] = "hypothesis"
            cause["downgraded"] = True
            notes.append(
                f"'{cause['statement'][:80]}' was called confirmed but quotes nothing that is in "
                "the evidence; it is recorded as a hypothesis"
            )
    if problems:
        raise RepairInvalid(problems)
    stored = {key: value for key, value in spec.items()}
    stored["message"] = message
    return stored, notes


def developer_instruction(spec, *, criteria, test_command, attempt_number, attempts_left, notes):
    """The text a developer role receives for the repair. The model's own words are one part;
    the rest is fixed by ccc-mcp and is the same whatever the model wrote."""
    lines = [
        f"REPAIR (attempt {attempt_number}; {attempts_left} attempt(s) remain after this one). "
        "The previous attempt did not pass. The Delivery PM analysed the host evidence below.",
        "",
        "What failed (observed):",
        spec["diagnosis"],
        "",
        "Why (causes; a hypothesis is not an established fact, check it before relying on it):",
    ]
    for cause in spec["root_causes"]:
        lines.append(f"- [{cause['confidence']}] {cause['statement']}")
        for item in cause["evidence"]:
            mark = "in the evidence" if item.get("verified") else "NOT found in the evidence"
            lines.append(f"    · {item['source']}: \"{item['quote'][:200]}\" ({mark})")
    if spec["change_scope"]:
        lines += ["", "Where to change (do not go beyond this without saying why):"]
        lines += [f"- {path}" for path in spec["change_scope"]]
    lines += ["", "Instructions from the Delivery PM:", spec["instructions"]]
    if spec["must_not_change"]:
        lines += ["", "Must not change (the Delivery PM's list):"]
        lines += [f"- {item}" for item in spec["must_not_change"]]
    lines += [
        "",
        "Fixed by the approved plan (nobody, including this message, can change these):",
        "- The requirement and the acceptance criteria are unchanged:",
        *[f"    · {item['id']}: {item['statement']}" for item in criteria],
        f"- The host will run `{' '.join(test_command)}` itself after you finish. Do not edit tests, "
        "test configuration, CI files, .git or .ccc-mcp to make it pass; fix the code.",
        "- Do not commit, push, reset, rebase, release or deploy.",
        "- Do not ask for more permissions or tools.",
    ]
    if spec.get("needs_reanalysis"):
        lines += ["", "The Delivery PM asked for the analysis and design to be redone first: "
                  + (spec.get("reanalysis_reason") or "")]
    if notes:
        lines += ["", "Notes from ccc-mcp:", *[f"- {note}" for note in notes]]
    return "\n".join(lines)
