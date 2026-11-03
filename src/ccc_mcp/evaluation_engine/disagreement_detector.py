"""Rule-based, structured-data cross-checks between roles (brief #4): explainable and
testable, never a semantic judgment call. A detected mismatch is NOT automatically a
penalty — only CONFIRMED disagreements (backed by an independent host fact) feed the
quality score; everything else is reported as NEEDS_VERIFICATION and left alone.

An LLM-as-a-Judge could resolve the NEEDS_VERIFICATION cases in a future version; this
module takes an optional `llm_judge` callable for exactly that (unused/None today —
brief #4 "預留... 但不得強制依賴外部模型"), so wiring one in later never changes this
module's public shape.
"""

import re

from . import diffs
from .models import CONFIRMED, NEEDS_VERIFICATION, disagreement

_BACKTICK_PATH = re.compile(r"`([^`\s]+\.[A-Za-z0-9_]+)`")


def _declared_scope(prior_report):
    if not prior_report:
        return set()
    text = " ".join(
        [prior_report.get("summary", ""), prior_report.get("next_action", "")]
        + prior_report.get("findings", [])
    )
    return {m.group(1) for m in _BACKTICK_PATH.finditer(text)}


def _scope_mismatch(prior_report, diff_text):
    declared = _declared_scope(prior_report)
    if not declared or not diff_text:
        return None
    touched = diffs.touched_files(diff_text)
    outside = [p for p in touched if not any(p.endswith(d) or d.endswith(p) for d in declared)]
    if not outside:
        return None
    return disagreement(
        kind="scope_mismatch",
        description=(
            "SA's approved plan named specific files, but this round's diff also "
            f"touches files outside that declared scope: {outside}"
        ),
        roles=["SA", "SD/RD"],
        evidence_refs=["CHANGES.diff"],
        resolution_status=NEEDS_VERIFICATION,
    )


def _claim_vs_host(report, test_result):
    """AgentReport.tests (what the Agent itself claims) vs the host-executed command's
    actual outcome — the one case the brief explicitly calls out (#6/#7): an Agent's own
    "it passed" is never accepted as verification."""
    claims = (report or {}).get("tests") or []
    if not claims or not test_result or not test_result.get("executed_by_host"):
        return []
    host_passed = test_result.get("passed")
    if host_passed is None:
        return []
    found = []
    for claim in claims:
        claimed = str(claim.get("claimed_result", "")).strip().lower()
        claims_pass = claimed in {"passed", "pass", "ok", "success", "成功", "通過"}
        claims_fail = claimed in {"failed", "fail", "error", "失敗"}
        if (claims_pass and not host_passed) or (claims_fail and host_passed):
            found.append(
                disagreement(
                    kind="claimed_vs_verified_test_result",
                    description=(
                        f"Agent claimed test {claim.get('name')!r} result "
                        f"{claim.get('claimed_result')!r}, but the host-executed test "
                        f"command {'passed' if host_passed else 'failed'}."
                    ),
                    roles=["SD/RD"],
                    evidence_refs=["TEST-RESULT.md"],
                    resolution_status=CONFIRMED,
                )
            )
    return found


def _review_finding_paths(finding_text):
    return {m.group(1) for m in _BACKTICK_PATH.finditer(finding_text)}


def detect(bundle):
    """Returns {"disagreements": [...], "review_tally": {"verified","false","needs_verification"}}.
    `bundle` is evidence_collector.collect(...)'s return value."""
    disagreements = []
    mismatch = _scope_mismatch(bundle.get("prior_report"), bundle.get("diff_text"))
    if mismatch:
        disagreements.append(mismatch)
    disagreements.extend(_claim_vs_host(bundle.get("report"), bundle.get("test_result")))

    review_report = bundle.get("review_report")
    review_tally = {"verified": 0, "false": 0, "needs_verification": 0}
    if review_report and review_report.get("findings"):
        touched_later = set()
        for later_diff in bundle.get("later_diffs", []):
            touched_later |= set(diffs.touched_files(later_diff))
        host_ultimately_passed = bool(
            (bundle.get("test_result") or {}).get("executed_by_host")
            and (bundle.get("test_result") or {}).get("passed")
        )
        for finding in review_report["findings"]:
            paths = _review_finding_paths(finding)
            if not paths:
                review_tally["needs_verification"] += 1
                continue
            if paths & touched_later:
                review_tally["verified"] += 1
                disagreements.append(
                    disagreement(
                        kind="review_finding_confirmed",
                        description=f"REVIEW finding {finding!r} names files a later round actually touched.",
                        roles=["REVIEW", "SD/RD"],
                        evidence_refs=["CHANGES.diff"],
                        resolution_status=CONFIRMED,
                    )
                )
            elif host_ultimately_passed:
                review_tally["false"] += 1
                disagreements.append(
                    disagreement(
                        kind="review_finding_unaddressed",
                        description=(
                            f"REVIEW finding {finding!r} was never touched by a later round, "
                            "and the task still passed host tests."
                        ),
                        roles=["REVIEW"],
                        evidence_refs=["CHANGES.diff", "TEST-RESULT.md"],
                        resolution_status=NEEDS_VERIFICATION,
                    )
                )
            else:
                review_tally["needs_verification"] += 1

    return {"disagreements": disagreements, "review_tally": review_tally}
