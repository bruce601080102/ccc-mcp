"""A free, local check of a custom role's instructions.

It reads the text with plain rules: no model is called, nothing is sent anywhere, no token is
spent. It reports what looks missing, what looks contradictory, and wording that cannot work
because ccc-mcp fixes it elsewhere. It deliberately gives **no score**. Whether a role's
instructions are complete says nothing about how well the role will do its job, and a number
would be read as if it did.

Two limits are part of the contract and are repeated in every result:

* Detection is by keywords. A prompt can say the same thing in other words, so a finding may
  be wrong in either direction and "no warnings" is not a clean bill of health.
* Matching words cannot stop prompt injection. Wording such as "ignore the system rules" is
  flagged as a warning because it will not work, not because flagging it makes it safe. What
  protects a project is enforced by code (see role_prompt.enforced_by_code).
"""

import re
import unicodedata

from ..infra.security import redact
from .flows import GATE_ROLES
from .role_prompt import SECTION_HEADINGS, enforced_by_code
from .roles import READ_ONLY_ROLES

ERROR, WARNING, INFO = "error", "warning", "info"
AREAS = ("goal", "duties", "output", "constraints", "collaboration", "failure_handling")
THIN = 20  # a section shorter than this is present but unlikely to say much
SHORT_TOTAL = 40
LONG_TOTAL = 12000
LIMITS = {"section": 4000, "total": 16000}

DISCLAIMER = (
    "Checked with local text rules only; no model was called and no score is given. "
    "Keyword detection can miss things and flag harmless text. Matching words cannot prevent "
    "prompt injection: what keeps a project safe is enforced by code, not by this check."
)

_AREA_PATTERNS = {
    "goal": (
        r"\b(goal|objective|purpose|mission|aim|your (job|task|role)|you are|you will act)\b"
        r"|目標|目的|任務|你是|你的角色|負責"
    ),
    "duties": (
        r"\b(duties|responsibilit\w*|you (must|should|will)|steps?|checklist|for each|"
        r"analy[sz]e|review|check|write|compare|list)\b|職責|步驟|需要|必須|檢查|分析|整理|列出|比較"
    ),
    "output": (
        r"\b(output|format|respond|report|return|deliver|template|json|markdown|table|field|"
        r"summary|bullet)\b|輸出|格式|回報|回覆|報告|表格|欄位|摘要|條列"
    ),
    "constraints": (
        r"\b(do not|don't|never|must not|only|limit|avoid|forbid|within|at most|no more than|"
        r"out of scope)\b|不得|不要|禁止|限制|只能|不可|避免|最多|範圍"
    ),
    "collaboration": (
        r"\b(hand ?off|hand over|pass (it )?(on )?to|next role|previous (step|role)|reviewer|"
        r"the pm|escalate|collaborat\w*|coordinate|upstream|downstream|other roles?)\b"
        r"|交接|交給|協作|前一|下一|升級|詢問|PM|其他角色"
    ),
    "failure_handling": (
        r"\b(if (you|it|something|anything) (cannot|can't|fails?|is (unclear|missing|unknown))|"
        r"unable to|uncertain|unclear|when stuck|not enough information|cannot find|"
        r"report (the )?(problem|blocker)|say so)\b"
        r"|失敗|無法|不確定|不清楚|缺少|遇到問題|卡住|找不到|資訊不足|如果.{0,6}(沒有|不能)"
    ),
}
_AREA_LABELS = {
    "goal": "goal", "duties": "duties", "output": "output requirements",
    "constraints": "constraints", "collaboration": "collaboration rules",
    "failure_handling": "failure handling",
}
_SECTION_FOR_AREA = {
    "goal": "goal", "duties": "duties", "output": "io_format", "constraints": "constraints",
    "collaboration": "collaboration", "failure_handling": "failure_handling",
}
_AREA_ADVICE = {
    "goal": "State in one or two sentences what this role is for.",
    "duties": "List what the role does, step by step if the order matters.",
    "output": "Describe what the role's findings and summary should contain. The report "
    "structure itself is fixed by ccc-mcp.",
    "constraints": "Say what the role must not do or must stay within.",
    "collaboration": "Say who the role hands its result to, who checks it, and when to ask the PM.",
    "failure_handling": "Say what to do when information is missing, unclear or the work "
    "cannot be done.",
}

_NEGATORS = re.compile(
    r"(not|never|n't|no|without|avoid|forbid\w*|don't|do not|cannot|can't|不得|不要|不可|不用|"
    r"禁止|勿|別|無需|不需)\W{0,3}$", re.I,
)


def _negated(text, start):
    return bool(_NEGATORS.search(text[max(0, start - 16):start].lower()))


def _first(pattern, text, *, skip_negated=False):
    for match in re.finditer(pattern, text, re.I):
        if not (skip_negated and _negated(text, match.start())):
            return match
    return None


def _quote(text, match):
    start = max(0, match.start() - 20)
    return text[start:match.end() + 20].replace("\n", " ").strip()


# (id, pattern, message). All are warnings: such wording cannot work, and some of it is an
# attempt to override the rules ccc-mcp fixes.
_OVERRIDE = [
    ("override_instructions",
     r"\b(ignore|disregard|forget|override)\b[^.\n]{0,30}\b(previous|prior|above|earlier|system|"
     r"operator|safety|all)\b[^.\n]{0,30}\b(instructions?|rules?|polic\w+|prompts?|"
     r"restrictions?|guardrails?)\b"
     r"|(忽略|無視|無视|不要理會|不用遵守|覆蓋|覆盖)[^。\n]{0,12}(系統|系统|以上|先前|之前|規則|规则|指令|政策|限制)",
     "Asks the model to ignore or override the rules around it."),
    ("skip_approval",
     r"\b(skip|bypass|circumvent|without|no need for|don't wait for)\b[^.\n]{0,20}\b(approval|"
     r"authori[sz]ation|review gate|sandbox|permissions?)\b"
     r"|(跳過|略過|绕过|繞過|跳过|不需要|無需|无需)[^。\n]{0,8}(核准|批准|授權|授权|審查|审查|沙盒|sandbox|權限|权限)",
     "Asks to skip approval, authorization or the sandbox."),
    ("disable_safety",
     r"\b(disable|turn off|remove)\b[^.\n]{0,20}\b(sandbox|safety|polic\w+|guard\w*|"
     r"restrictions?|permissions?|approval)\b"
     r"|(關閉|关闭|停用)[^。\n]{0,8}(沙盒|安全|限制|權限|权限|核准)",
     "Asks to turn off a safety control."),
    ("reveal_prompt",
     r"\b(reveal|print|show|repeat|leak|output)\b[^.\n]{0,30}\b(system prompt|operator polic\w+|"
     r"hidden instructions?)\b|(洩漏|泄露|透露|輸出|显示|顯示)[^。\n]{0,8}(系統提示|系统提示|隱藏指令)",
     "Asks the model to reveal its hidden instructions."),
    ("action_outside_policy",
     r"\b(always|must|should|please)\s+(git\s+)?(push|merge|deploy|publish|release)\b"
     r"|(一律|必須|必须|請|请)[^。\n]{0,4}(推送|部署|發布|发布|合併|合并)",
     "Asks for push, merge, deploy or release, which the operator policy forbids."),
    ("marker_lookalike",
     r"<<<|OPERATOR ROLE INSTRUCTIONS|Fixed role contract set by ccc-mcp",
     "Contains text that looks like ccc-mcp's own markers. It can't take their place (the "
     "real markers carry a hash of the text), but it may confuse a reader or the model."),
]

# Wording that fights something ccc-mcp fixes.
_CONTRACT_CONFLICTS = [
    ("output_format_conflict",
     r"\b(do not|don't|never|without)\s+(use\s+|output\s+|return\s+|produce\s+)?(json|structured)"
     r"|\b(respond|reply|answer|output)\s+(only\s+)?(in|with|as)\s+(plain text|markdown|prose|"
     r"free[- ]?form text)\b|(不要|不用|禁止)[^。\n]{0,6}(json|JSON|結構化|结构化)"
     r"|(只|僅|仅)[^。\n]{0,4}(輸出|输出|回覆|回复|回答)[^。\n]{0,6}(純文字|纯文字|markdown|Markdown)",
     "The report must come back in the structured format ccc-mcp requests; instructions "
     "cannot change it."),
    ("run_tests_conflict",
     r"\b(run|execute)\s+(the\s+|all\s+)?(unit\s+|integration\s+)?tests?\b|(執行|运行|運行|跑)[^。\n]{0,4}測試",
     "Agents are told not to run tests. The host runs them afterwards and records the result."),
]
_GATE_CONFLICTS = [
    ("gate_always_pass",
     r"\b(always|never)\s+(approve|pass|accept|block|reject)\b"
     r"|\b(never|do not|don't)\s+(block|request (a )?revision|set needs_revision)\b"
     r"|(一律|全部|都)[^。\n]{0,4}(通過|通过|放行|核准)|(不要|不得|不用)[^。\n]{0,4}(阻擋|阻挡|退回|要求修改)",
     "A reviewing role's verdict follows its evidence-backed findings and the host tests, so "
     "a fixed verdict is ignored by the rules."),
]
_READ_ONLY_WRITE = (
    "read_only_asked_to_write",
    r"\b(edit|modify|change|rewrite|patch|write|create|delete|refactor)\b[^.\n]{0,25}\b(files?|"
    r"code|source|repo|repository|tests?)\b|(修改|編輯|编辑|寫入|写入|重寫|刪除|删除|新增|建立)[^。\n]{0,6}(檔案|文件|程式|代码|原始碼|檔)",
    "This role's base role is read-only. Code enforces that, so such instructions cannot make "
    "it change files.",
)

_CONTRADICTION_PAIRS = [
    ("ask the user",
     r"\b(always|must)\s+ask\b|(一定要?|務必|务必)[^。\n]{0,3}(詢問|询问|請問|请问)",
     r"\b(never|do not|don't)\s+ask\b|(不要|不得|不可)[^。\n]{0,3}(詢問|询问|問|问)"),
    ("run tests",
     r"\b(must|always|should)\s+(run|execute)\s+(the\s+)?tests?\b",
     r"\b(do not|don't|never)\s+(run|execute)\s+(the\s+)?tests?\b"),
    ("length",
     r"\b(brief|concise|short|one paragraph|terse)\b|簡短|简短|精簡|精简",
     r"\b(exhaustive|comprehensive|as detailed as possible|very detailed)\b|詳盡|详尽|越詳細越好|越详细越好"),
    ("language",
     r"\b(respond|reply|answer|write|use)\s+in\s+english\b|用英文|使用英文|英文回答",
     r"\b(respond|reply|answer|write|use)\s+in\s+(chinese|mandarin)\b|用(繁體|繁体|簡體|简体|中文)"
     r"|使用(繁體|繁体|簡體|简体)?中文"),
]


def _finding(finding_id, severity, area, message, *, why=None, suggestion=None, evidence=None):
    return {
        "id": finding_id, "severity": severity, "area": area, "message": message,
        **({"why": why} if why else {}),
        **({"suggestion": suggestion} if suggestion else {}),
        **({"evidence": evidence} if evidence else {}),
    }


def _bad_characters(text):
    return any(
        unicodedata.category(char) in {"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp"}
        and char not in "\n\t\r"
        for char in text
    )


def _read(definition):
    """The prompt dict from a possibly unfinished draft, tolerant of missing pieces."""
    prompt = definition.get("prompt") if isinstance(definition, dict) else None
    if not isinstance(prompt, dict):
        return None
    sections = prompt.get("sections") if isinstance(prompt.get("sections"), dict) else {}
    return {
        "mode": prompt.get("mode", "sections"),
        "sections": {key: str(sections.get(key) or "") for key in SECTION_HEADINGS},
        "freeform": str(prompt.get("freeform") or ""),
    }


def lint_role(base_role, definition, *, provider="codex"):
    """Check a role's instructions. `definition` may be an unsaved draft; nothing is stored.

    Returns {"findings", "areas", "counts", "enforced_by_code", "method", "disclaimer"}. There
    is no score on purpose."""
    prompt = _read(definition)
    findings, areas = [], []
    if prompt is None or prompt["mode"] not in {"sections", "freeform"}:
        findings.append(_finding(
            "invalid_prompt", ERROR, None, "The instructions are not in a form that can be saved.",
            suggestion="Use mode `sections` or `freeform` and give the text.",
        ))
        return _result(findings, areas, base_role, provider)

    freeform = prompt["mode"] == "freeform"
    body = prompt["freeform"] if freeform else "\n\n".join(prompt["sections"].values())
    other = "\n\n".join(prompt["sections"].values()) if freeform else prompt["freeform"]

    if not body.strip():
        findings.append(_finding(
            "empty_instructions", ERROR, None, "The instructions are empty.",
            why="A role with no instructions would run on its fixed contract alone.",
            suggestion="Write what the role is for and what it should do.",
        ))
    if _bad_characters(body) or _bad_characters(other):
        findings.append(_finding(
            "unsupported_characters", ERROR, None,
            "The text contains control, invisible or direction-changing characters.",
            why="They can hide text from the person reading it, so saving is refused.",
            suggestion="Remove them, or paste the text as plain text.",
        ))
    if redact(body) != body or redact(other) != other:
        findings.append(_finding(
            "possible_credential", ERROR, None,
            "Part of the text looks like a password, token or key.",
            why="Instructions are sent to the provider and stored; saving is refused.",
            suggestion="Remove the secret. Keep credentials out of instructions.",
        ))
    for key in SECTION_HEADINGS:
        if len(prompt["sections"][key]) > LIMITS["section"]:
            findings.append(_finding(
                "section_too_long", ERROR, _AREA_LABELS.get(key, key),
                f"The {SECTION_HEADINGS[key]} section is over {LIMITS['section']} characters.",
                suggestion="Shorten it, or switch to free-form instructions.",
            ))
    limit = LIMITS["total"]
    if len(body) > limit:
        findings.append(_finding(
            "too_long", ERROR, None, f"The instructions are over {limit} characters.",
            suggestion="Shorten them.",
        ))
    elif len(body) > LONG_TOTAL:
        findings.append(_finding(
            "very_long", INFO, None, "These instructions are long.",
            why="They are sent on every run and cost tokens each time.",
            suggestion="Keep what changes the result and drop the rest.",
        ))
    elif body.strip() and len(body.strip()) < SHORT_TOTAL:
        findings.append(_finding(
            "very_short", WARNING, None, "The instructions are very short.",
            why="There is little for the role to go on beyond its fixed contract.",
            suggestion="Add the role's goal, what it does and what it must not do.",
        ))
    if other.strip():
        findings.append(_finding(
            "other_mode_text_kept", INFO, None,
            "There is text for the other mode. It is kept in the role and is not sent.",
            suggestion="Delete it when you no longer need it, or switch back to use it.",
        ))

    for area in AREAS:
        if freeform:
            hit = re.search(_AREA_PATTERNS[area], body, re.I) is not None
            areas.append({"area": area, "status": "found" if hit else "not_detected",
                          "how": "keyword"})
        else:
            text = prompt["sections"][_SECTION_FOR_AREA[area]].strip()
            status = "found" if len(text) >= THIN else "thin" if text else "missing"
            areas.append({"area": area, "status": status, "how": "section"})
        status = areas[-1]["status"]
        if status in {"missing", "thin", "not_detected"} and body.strip():
            label = _AREA_LABELS[area]
            severity = WARNING if area in {"goal", "duties"} and status != "thin" else INFO
            findings.append(_finding(
                f"{'thin' if status == 'thin' else 'missing'}_{area}", severity, label,
                f"No {label} found." if status != "thin" else f"The {label} is very brief.",
                why="Detected by keywords; if you cover this in other words, ignore it."
                if freeform else "The matching section is empty or nearly empty.",
                suggestion=_AREA_ADVICE[area],
            ))

    for finding_id, pattern, message in _OVERRIDE:
        match = _first(pattern, body)
        if match:
            findings.append(_finding(
                finding_id, WARNING, "constraints", message,
                why="This wording cannot change what ccc-mcp enforces, and it is not a "
                "protection by itself: matching words cannot detect all prompt injection.",
                suggestion="Remove it. If the role really needs different permissions, change "
                "the project's authorization or choose another base role.",
                evidence=_quote(body, match),
            ))
    for finding_id, pattern, message in _CONTRACT_CONFLICTS:
        match = _first(pattern, body, skip_negated=finding_id == "run_tests_conflict")
        if match:
            findings.append(_finding(
                finding_id, WARNING, "output requirements", message,
                suggestion="Describe what the findings should say, not the report's format.",
                evidence=_quote(body, match),
            ))
    if base_role in READ_ONLY_ROLES or base_role == "SD":
        finding_id, pattern, message = _READ_ONLY_WRITE
        match = _first(pattern, body, skip_negated=True)
        if match:
            findings.append(_finding(
                finding_id, WARNING, "constraints", message,
                suggestion="Ask for analysis or a document instead, or choose a base role "
                "that may write.", evidence=_quote(body, match),
            ))
    if base_role in GATE_ROLES:
        for finding_id, pattern, message in _GATE_CONFLICTS:
            match = _first(pattern, body)
            if match:
                findings.append(_finding(
                    finding_id, WARNING, "constraints", message,
                    suggestion="Tell the role what to examine and what counts as a problem.",
                    evidence=_quote(body, match),
                ))
    for topic, left, right in _CONTRADICTION_PAIRS:
        a, b = _first(left, body, skip_negated=True), _first(right, body)
        if a and b:
            findings.append(_finding(
                "possible_contradiction", WARNING, "constraints",
                f"The instructions may contradict themselves about: {topic}.",
                why="Both a requirement and its opposite appear.",
                suggestion="Keep one, or say when each applies.",
                evidence=f"{_quote(body, a)}  ↔  {_quote(body, b)}",
            ))
    # "always X" and "never X" with exactly the same X; a narrower "never X from Y" is not one.
    always = {m.group(1).strip().lower() for m in re.finditer(r"\balways\s+([^.;,\n]{3,60})", body, re.I)}
    never = {m.group(1).strip().lower() for m in re.finditer(r"\bnever\s+([^.;,\n]{3,60})", body, re.I)}
    for phrase in sorted(always & never):
        findings.append(_finding(
            "possible_contradiction", WARNING, "constraints",
            f"The instructions say both always and never: {phrase!r}.",
            suggestion="Keep one, or say when each applies.",
        ))
    return _result(findings, areas, base_role, provider)


def _result(findings, areas, base_role, provider):
    order = {ERROR: 0, WARNING: 1, INFO: 2}
    findings.sort(key=lambda item: order[item["severity"]])
    counts = {severity: sum(1 for f in findings if f["severity"] == severity) for severity in order}
    return {
        "method": "local rules; no model was called",
        "findings": findings,
        "areas": areas,
        "counts": counts,
        "can_be_saved": counts[ERROR] == 0,
        "enforced_by_code": enforced_by_code(base_role, provider),
        "disclaimer": DISCLAIMER,
    }
