"""Turn collected evidence into quantified features. A feature is None whenever the
underlying evidence is missing or unverified — never guessed as a perfect or zero
score (brief #2 "無法取得的特徵應標記為缺失，不得直接當成滿分或零分")."""

import re

from . import diffs

_SUMMARY_COUNT = re.compile(r"(\d+)\s+(passed|failed|error|skipped)\b")
_COLLECTED = re.compile(r"collected (\d+) item")
_FAILED_NODE = re.compile(r"^FAILED (\S+)", re.MULTILINE)


def parse_pytest_summary(stdout):
    """Best-effort parse of pytest's own `-q` output (counts line + the "short test
    summary info" FAILED lines it prints even in quiet mode — ccc-mcp's own
    DEFAULT_TEST_COMMAND is `pytest -q`). `collected` is only ever populated when
    pytest's (more verbose) collection banner happens to be present; `-q` never prints
    it, so total test count below is always derived from the counts line instead —
    never from this field. Returns None/{}/[] when the command plainly wasn't pytest
    or nothing matched — a non-pytest test_command is honestly out of scope for this
    heuristic, not a zero result."""
    if not stdout:
        return {"collected": None, "counts": {}, "failed_node_ids": []}
    counts = {}
    for count, label in _SUMMARY_COUNT.findall(stdout):
        counts[label] = counts.get(label, 0) + int(count)
    collected_match = _COLLECTED.search(stdout)
    return {
        "collected": int(collected_match.group(1)) if collected_match else None,
        "counts": counts,
        "failed_node_ids": [m.group(1) for m in _FAILED_NODE.finditer(stdout)],
    }


def _test_pass_rate(test_result):
    if not test_result or not test_result.get("executed_by_host"):
        return None
    if test_result.get("no_tests_collected"):
        return None
    counts = parse_pytest_summary(test_result.get("stdout"))["counts"]
    total = counts.get("passed", 0) + counts.get("failed", 0) + counts.get("error", 0)
    if total:
        return counts.get("passed", 0) / total
    # Not a parseable pytest summary (custom test_command): fall back to the one
    # boolean the host actually gives us for every command.
    return 1.0 if test_result.get("passed") else 0.0


def _fail_to_pass_and_regression(test_result, diff_text):
    """Best-effort proxy for SWE-bench's FAIL_TO_PASS/PASS_TO_PASS: which test names
    this round's diff newly introduced, cross-referenced against pytest's own FAILED
    list. ccc-mcp has no ground-truth test oracle outside a benchmark harness, so this
    is deliberately approximate and documented as such — never presented as ground
    truth. None (not 0/1) whenever the underlying pytest output can't be parsed."""
    if not test_result or not test_result.get("executed_by_host") or not diff_text:
        return None, None
    new_tests = diffs.newly_added_test_names(diff_text)
    parsed = parse_pytest_summary(test_result.get("stdout"))
    counts = parsed["counts"]
    total_run = counts.get("passed", 0) + counts.get("failed", 0) + counts.get("error", 0)
    if not new_tests or not total_run:
        return None, None
    failed_new = sum(1 for node in parsed["failed_node_ids"] if node.split("::")[-1] in new_tests)
    failed_total = len(parsed["failed_node_ids"])
    failed_old = failed_total - failed_new
    fail_to_pass_rate = 1 - (failed_new / len(new_tests))
    pre_existing_total = max(total_run - len(new_tests), 1)
    regression_pass_rate = 1 - (failed_old / pre_existing_total)
    return round(fail_to_pass_rate, 4), round(regression_pass_rate, 4)


_WORD = re.compile(r"[A-Za-z0-9_一-鿿]{3,}")


def _requirement_coverage(acceptance, text_blob):
    if not acceptance:
        return None
    haystack = text_blob.lower()
    covered = 0
    for bullet in acceptance:
        words = {w.lower() for w in _WORD.findall(bullet)}
        if words and any(word in haystack for word in words):
            covered += 1
    return covered / len(acceptance)


def _static_counts(static_analysis_evidence):
    findings = (static_analysis_evidence or {}).get("payload", {}).get("findings")
    if findings is None:
        return None, None
    security = sum(1 for f in findings if str(f.get("code", "")).startswith("S"))
    return len(findings), security


def extract(bundle, review_tally, disagreements):
    """`bundle` is evidence_collector.collect(...)'s return value; `review_tally` and
    `disagreements` are disagreement_detector.detect(...)'s two outputs. Returns a flat
    features dict — every value is either a number/bool or None (missing), per brief #2."""
    report = bundle.get("report") or {}
    test_result = bundle.get("test_result")
    diff_text = bundle.get("diff_text")
    review_report = bundle.get("review_report")
    static_evidence = next(
        (e for e in bundle["evidence"] if e["kind"] == "static_analysis"), None
    )
    issue_count, security_issue_count = _static_counts(static_evidence)
    fail_to_pass_rate, regression_pass_rate = _fail_to_pass_and_regression(test_result, diff_text)
    text_blob = " ".join(
        [report.get("summary", ""), report.get("next_action", ""), *report.get("findings", [])]
    )
    return {
        "test_pass_rate": _test_pass_rate(test_result),
        "fail_to_pass_rate": fail_to_pass_rate,
        "regression_pass_rate": regression_pass_rate,
        "requirement_coverage": _requirement_coverage(
            (bundle.get("task") or {}).get("acceptance"), text_blob
        ),
        "static_analysis_issue_count": issue_count,
        "security_issue_count": security_issue_count,
        "verified_review_findings": review_tally.get("verified", 0),
        "false_review_findings": review_tally.get("false", 0),
        "review_findings_needing_verification": review_tally.get("needs_verification", 0),
        "review_needs_revision": review_report.get("needs_revision") if review_report else None,
        "scope_mismatch_flagged": any(d["kind"] == "scope_mismatch" for d in disagreements),
        "only_self_authored_tests_passed": diffs.has_pre_existing_passing_test_only(diff_text)
        if diff_text
        else False,
        "has_changes": bool(diff_text),
        "host_tests_executed": bool(test_result and test_result.get("executed_by_host")),
        "host_test_passed": (test_result or {}).get("passed"),
        "host_no_tests_collected": bool((test_result or {}).get("no_tests_collected")),
        "host_runtime_blocked": bool((test_result or {}).get("runtime_blocked")),
    }
