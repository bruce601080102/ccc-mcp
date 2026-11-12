"""Tests for benchmarks/toy-agents/run.py's Arm B_retry (`run_b_retry`) and `--arms` CLI
parsing — the toy-agents benchmark harness changes made for ACQE Phase 1 (see
docs/development/ACQE-GAP-ANALYSIS.md §11). These exercise real control flow (retry
counting, bounded follow-ups, token summation, arm-name validation) without spending
real Codex quota: the `codex exec` subprocess call is stubbed, but the host-run `pytest`
calls against the generated toy repository are real.

`run_b_retry` is explicitly Test-Guided Retry, not a review — see the module docstring
in run.py and §11.6 of the Gap Analysis doc. These tests verify its actual behavior
(retries gated on the same visible test suite, nothing else) so that behavior can never
accidentally be mistaken for review effectiveness.
"""

import importlib.util
import json
import socket
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
RUN_PATH = ROOT / "benchmarks" / "toy-agents" / "run.py"


def _load_run_module():
    spec = importlib.util.spec_from_file_location("toy_agents_run", RUN_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def run_module():
    return _load_run_module()


def _fake_codex(write_source_by_round, real_run):
    """Builds a stand-in for `subprocess.run` that intercepts only the codex exec
    invocation — writing `write_source_by_round[call_index]` (or the last entry, if
    fewer entries than calls) into the repo's mod.py instead of actually calling Codex,
    and returning a canned `turn.completed` usage line — while passing every other
    command (the host's own `pytest`/`git` calls `run_b_retry` also makes) through to the
    real `subprocess.run` unchanged."""
    calls = {"n": 0}

    def fake_run(cmd, *args, **kwargs):
        if "codex" not in str(cmd[0]):
            return real_run(cmd, *args, **kwargs)
        cd_index = cmd.index("--cd")
        repo = Path(cmd[cd_index + 1])
        index = min(calls["n"], len(write_source_by_round) - 1)
        (repo / "mod.py").write_text(write_source_by_round[index])
        calls["n"] += 1

        class Result:
            stdout = '{"type":"turn.completed","usage":{"input_tokens":10,"output_tokens":5}}\n'
            stderr = ""
            returncode = 0

        return Result()

    return fake_run, calls


def test_merge_tokens_sums_rounds_and_skips_missing(run_module):
    merged = run_module._merge_tokens([
        {"input_tokens": 10, "output_tokens": 5},
        None,
        {"input_tokens": 3, "output_tokens": 1, "cached_input_tokens": 2},
    ])
    assert merged == {"input_tokens": 13, "output_tokens": 6, "cached_input_tokens": 2}


def test_merge_tokens_all_missing_returns_none(run_module):
    assert run_module._merge_tokens([None, None]) is None


def test_run_b_retry_stops_after_first_pass(tmp_path, monkeypatch, run_module):
    monkeypatch.setattr(run_module, "native_executable", lambda name: "codex")
    fixed = run_module.TASKS["clamp"]["fixed"]
    fake_run, calls = _fake_codex([fixed], run_module.subprocess.run)
    monkeypatch.setattr(run_module.subprocess, "run", fake_run)

    case = run_module.run_b_retry(str(tmp_path), "clamp")

    assert calls["n"] == 1
    assert case["rounds_used"] == 1
    assert case["passed_per_round"] == [True]
    assert case["resolved"] is True
    assert case["tokens_total"] == 15
    assert case["arm"] == "b_retry"


def test_run_b_retry_does_not_retry_once_visible_tests_pass_even_with_a_real_defect(tmp_path, monkeypatch, run_module):
    """The behavior that makes this arm Test-Guided Retry rather than a review: "partial"
    passes clamp's own visible tests (both only check the lower bound) but is still wrong
    for the hidden upper-bound case. b_retry must stop at round 1 regardless — it never
    looks past the visible test suite, by design."""
    monkeypatch.setattr(run_module, "native_executable", lambda name: "codex")
    partial = run_module.TASKS["clamp"]["partial"]
    fake_run, calls = _fake_codex([partial], run_module.subprocess.run)
    monkeypatch.setattr(run_module.subprocess, "run", fake_run)

    case = run_module.run_b_retry(str(tmp_path), "clamp")

    assert calls["n"] == 1
    assert case["rounds_used"] == 1
    assert case["passed_per_round"] == [True]
    # Visible tests passed, so b_retry stopped — but the hidden (hold-out) test still
    # fails, because nothing ever reviewed the implementation.
    assert case["resolved"] is False


def test_run_b_retry_retries_once_then_succeeds(tmp_path, monkeypatch, run_module):
    monkeypatch.setattr(run_module, "native_executable", lambda name: "codex")
    # "broken" fails clamp's own *visible* tests (unlike "buggy", which only fails the
    # hidden one — the whole point of this toy benchmark) so the first round genuinely
    # fails and a retry is triggered.
    broken = run_module.TASKS["clamp"]["broken"]
    fixed = run_module.TASKS["clamp"]["fixed"]
    fake_run, calls = _fake_codex([broken, fixed], run_module.subprocess.run)
    monkeypatch.setattr(run_module.subprocess, "run", fake_run)

    case = run_module.run_b_retry(str(tmp_path), "clamp")

    assert calls["n"] == 2
    assert case["rounds_used"] == 2
    assert case["passed_per_round"] == [False, True]
    assert case["resolved"] is True
    assert case["tokens_total"] == 30  # both rounds' usage summed, not just the last


def test_run_b_retry_never_exceeds_the_bounded_retry(tmp_path, monkeypatch, run_module):
    monkeypatch.setattr(run_module, "native_executable", lambda name: "codex")
    broken = run_module.TASKS["clamp"]["broken"]
    fake_run, calls = _fake_codex([broken], run_module.subprocess.run)
    monkeypatch.setattr(run_module.subprocess, "run", fake_run)

    case = run_module.run_b_retry(str(tmp_path), "clamp", max_follow_ups=1)

    # One initial attempt + exactly one follow-up, never a third try, even though it
    # never passes.
    assert calls["n"] == 2
    assert case["rounds_used"] == 2
    assert case["passed_per_round"] == [False, False]
    assert case["resolved"] is False


def test_case_from_base_shape_is_unchanged(run_module, tmp_path):
    """Neither run_b_retry nor this round's CLI changes touch case_from() itself — its
    base shape must stay exactly what run_direct/run_ccc already produce (consumed by
    evaluation_engine/benchmark.py and any other JSONL reader)."""
    repo = run_module.make_repo(str(tmp_path), "clamp", "shape-check")
    case = run_module.case_from("clamp", "direct", repo, None)
    assert set(case) == {
        "instance_id", "resolved", "role", "diff", "test_result", "report",
        "acceptance", "static_findings", "tokens", "tokens_total", "usage_complete", "arm",
    }
    assert case["arm"] == "direct"


def test_parse_arms_default_is_unchanged_from_before_b_retry(run_module):
    assert run_module.parse_arms("direct,ccc") == ["direct", "ccc"]


def test_parse_arms_accepts_b_retry():
    run_module = _load_run_module()
    assert run_module.parse_arms("direct,b_retry,ccc") == ["direct", "b_retry", "ccc"]


def test_parse_arms_rejects_unknown_arm(run_module):
    with pytest.raises(SystemExit):
        run_module.parse_arms("direct,not-a-real-arm")


def test_parse_arms_drops_blank_entries_from_stray_commas(run_module):
    assert run_module.parse_arms("direct,,ccc,") == ["direct", "ccc"]
    assert run_module.parse_arms(" direct , ccc ") == ["direct", "ccc"]


def test_parse_arms_rejects_all_blank_input(run_module):
    with pytest.raises(SystemExit):
        run_module.parse_arms("")
    with pytest.raises(SystemExit):
        run_module.parse_arms(" , , ")


def test_parse_arms_rejects_duplicate_arms(run_module):
    with pytest.raises(SystemExit):
        run_module.parse_arms("direct,direct,ccc")


def test_arms_dict_keys_match_what_parse_arms_validates_against(run_module):
    # Guards against the two ever drifting apart silently.
    assert set(run_module.ARMS) == {"direct", "b_retry", "ccc"}


# --- Run metadata (ACQE Phase 1 reliability/reproducibility) --------------------------


def test_model_record_never_upgrades_requested_to_observed(run_module):
    # No model requested, none observed: status is "unknown", not a guess.
    rec = run_module.model_record("codex")
    assert rec == {
        "provider": "codex", "requested_model": None, "observed_model": None,
        "requested_effort": None, "model_verification_status": "unknown",
        "cli_version": run_module.codex_cli_version(),
    }
    # Requested but never confirmed from the CLI's own output: "requested_unconfirmed",
    # never silently promoted to "confirmed".
    rec = run_module.model_record("codex", requested_model="gpt-5-codex")
    assert rec["model_verification_status"] == "requested_unconfirmed"
    assert rec["observed_model"] is None
    # Only an actually-observed model (extracted from real CLI output, never invented
    # here) may produce "confirmed".
    rec = run_module.model_record("codex", requested_model="gpt-5-codex", observed_model="gpt-5-codex")
    assert rec["model_verification_status"] == "confirmed"


def test_run_envelope_carries_run_case_arm_ids_and_repo_commit(run_module, tmp_path):
    repo = run_module.make_repo(str(tmp_path), "clamp", "envelope-check")
    envelope = run_module.run_envelope(
        run_id="RUN-1", case_id="clamp", arm_id="direct", repo=repo,
        budget={"max_agent_calls": 1}, tool_permissions={"x": "y"},
        stages=[{"name": "implement", "seconds": 1.23}], provider="codex",
    )["run_envelope"]
    assert envelope["run_id"] == "RUN-1"
    assert envelope["case_id"] == "clamp"
    assert envelope["arm_id"] == "direct"
    assert envelope["repo_commit"]  # make_repo always leaves exactly one commit
    assert len(envelope["repo_commit"]) == 40  # a real git SHA, not a placeholder
    assert envelope["budget"] == {"max_agent_calls": 1}
    assert envelope["stages"] == [{"name": "implement", "seconds": 1.23}]
    assert envelope["model_verification_status"] == "unknown"


def test_run_b_retry_envelope_has_one_stage_per_round_with_a_shared_run_id(tmp_path, monkeypatch, run_module):
    monkeypatch.setattr(run_module, "native_executable", lambda name: "codex")
    broken = run_module.TASKS["clamp"]["broken"]
    fixed = run_module.TASKS["clamp"]["fixed"]
    fake_run, calls = _fake_codex([broken, fixed], run_module.subprocess.run)
    monkeypatch.setattr(run_module.subprocess, "run", fake_run)

    case = run_module.run_b_retry(str(tmp_path), "clamp", run_id="FIXED-RUN-ID")

    envelope = case["run_envelope"]
    assert envelope["run_id"] == "FIXED-RUN-ID"
    assert envelope["case_id"] == "clamp"
    assert envelope["arm_id"] == "b_retry"
    assert len(envelope["stages"]) == 2  # one per round, matching rounds_used
    assert [s["visible_tests_passed"] for s in envelope["stages"]] == [False, True]
    assert all(isinstance(s["seconds"], (int, float)) for s in envelope["stages"])
    assert envelope["budget"] == {"max_agent_calls": 2, "max_retries": 1}


def test_run_b_retry_generates_a_run_id_when_none_given(tmp_path, monkeypatch, run_module):
    monkeypatch.setattr(run_module, "native_executable", lambda name: "codex")
    fixed = run_module.TASKS["clamp"]["fixed"]
    fake_run, _ = _fake_codex([fixed], run_module.subprocess.run)
    monkeypatch.setattr(run_module.subprocess, "run", fake_run)

    case = run_module.run_b_retry(str(tmp_path), "clamp")
    assert case["run_envelope"]["run_id"]  # non-empty, auto-generated


# --- Model pinning (round 4) -----------------------------------------------------------


def test_codex_exec_argv_defaults_are_byte_identical_to_before_pinning_existed(run_module, tmp_path, monkeypatch):
    monkeypatch.setattr(run_module, "native_executable", lambda name: "codex")
    repo = tmp_path / "repo"
    argv = run_module.codex_exec_argv(repo)
    assert argv == [
        "codex", "-a", "never", "exec", "--ignore-user-config", "--skip-git-repo-check",
        "--sandbox", "workspace-write", "--json", "--cd", str(repo), "-",
    ]


def test_codex_exec_argv_inserts_model_and_effort_like_the_real_adapter_does(run_module, tmp_path, monkeypatch):
    monkeypatch.setattr(run_module, "native_executable", lambda name: "codex")
    repo = tmp_path / "repo"
    argv = run_module.codex_exec_argv(repo, model="gpt-5-codex", effort="high")
    # Same flag syntax and same insertion point (right after "exec") as
    # adapters/agents/codex.py's real argv[4:4] insertion.
    assert argv[3] == "exec"
    assert argv[4:8] == ["-c", "model_reasoning_effort=high", "--model", "gpt-5-codex"]


def test_run_b_retry_records_a_pinned_model_as_requested_not_observed(tmp_path, monkeypatch, run_module):
    monkeypatch.setattr(run_module, "native_executable", lambda name: "codex")
    fixed = run_module.TASKS["clamp"]["fixed"]
    fake_run, _ = _fake_codex([fixed], run_module.subprocess.run)
    monkeypatch.setattr(run_module.subprocess, "run", fake_run)

    case = run_module.run_b_retry(str(tmp_path), "clamp", model="gpt-5-codex", effort="high")

    envelope = case["run_envelope"]
    assert envelope["requested_model"] == "gpt-5-codex"
    assert envelope["requested_effort"] == "high"
    # Pinning a request never fabricates an observation: Codex's own --json output still
    # doesn't expose model identity, so this must stay "requested_unconfirmed", not
    # silently promoted to "confirmed".
    assert envelope["observed_model"] is None
    assert envelope["model_verification_status"] == "requested_unconfirmed"


# --- Isolation / fairness (ACQE Phase 1 reliability/reproducibility) ------------------


def test_each_arm_gets_its_own_independent_working_directory(tmp_path, run_module):
    """Task 2, items 1-2: every arm starts from its own copy of the same clean source,
    and one arm's changes cannot leak into another's directory."""
    direct_repo = run_module.make_repo(str(tmp_path), "clamp", "direct")
    b_retry_repo = run_module.make_repo(str(tmp_path), "clamp", "b_retry")
    assert direct_repo != b_retry_repo
    assert (direct_repo / "mod.py").read_text() == (b_retry_repo / "mod.py").read_text()
    # Mutate one arm's copy as if an agent had edited it.
    (direct_repo / "mod.py").write_text("def clamp(x, lo, hi):\n    return 'mutated'\n")
    assert (b_retry_repo / "mod.py").read_text() != "def clamp(x, lo, hi):\n    return 'mutated'\n"


def test_hidden_test_file_does_not_exist_while_run_b_retry_is_working(tmp_path, monkeypatch, run_module):
    """Task 2, item 3: the hidden acceptance test must not be readable by the agent.
    Confirmed structurally — judge() (which creates the hidden test file) is only ever
    called from case_from(), which every arm calls strictly after its own agent process
    has already finished — by asserting the file is absent at the one point during
    run_b_retry's own execution where a test is read: inside the stubbed codex call and
    the subsequent visible-test run."""
    monkeypatch.setattr(run_module, "native_executable", lambda name: "codex")
    broken = run_module.TASKS["clamp"]["broken"]
    seen_hidden_file = []

    real_run = run_module.subprocess.run

    def fake_run(cmd, *args, **kwargs):
        if "codex" not in str(cmd[0]):
            return real_run(cmd, *args, **kwargs)
        cd_index = cmd.index("--cd")
        repo = Path(cmd[cd_index + 1])
        seen_hidden_file.append((repo / "tests" / "test_hidden.py").exists())
        (repo / "mod.py").write_text(broken)

        class Result:
            stdout = '{"type":"turn.completed","usage":{"input_tokens":1,"output_tokens":1}}\n'
            stderr = ""
            returncode = 0

        return Result()

    monkeypatch.setattr(run_module.subprocess, "run", fake_run)
    run_module.run_b_retry(str(tmp_path), "clamp")
    assert seen_hidden_file == [False, False]  # never present on either round


def test_retry_feedback_prompt_is_built_only_from_the_visible_test_output(tmp_path, monkeypatch, run_module):
    """Task 2, item 4: a failing hidden-test result must never leak into the retry
    prompt — because the prompt is built from the *visible* pytest run's own stdout, and
    the hidden test does not exist on disk at that point (previous test). This test pins
    that the feedback text actually comes from the visible suite's own failure, which for
    clamp's "broken" patch is a `test_inside` AssertionError — not anything referencing
    the hidden file's own test names (`test_hidden`)."""
    monkeypatch.setattr(run_module, "native_executable", lambda name: "codex")
    broken = run_module.TASKS["clamp"]["broken"]
    fixed = run_module.TASKS["clamp"]["fixed"]
    prompts_seen = []
    real_run = run_module.subprocess.run

    def fake_run(cmd, *args, **kwargs):
        if "codex" not in str(cmd[0]):
            return real_run(cmd, *args, **kwargs)
        prompts_seen.append(kwargs.get("input", ""))
        cd_index = cmd.index("--cd")
        repo = Path(cmd[cd_index + 1])
        source = broken if len(prompts_seen) == 1 else fixed
        (repo / "mod.py").write_text(source)

        class Result:
            stdout = '{"type":"turn.completed","usage":{"input_tokens":1,"output_tokens":1}}\n'
            stderr = ""
            returncode = 0

        return Result()

    monkeypatch.setattr(run_module.subprocess, "run", fake_run)
    run_module.run_b_retry(str(tmp_path), "clamp")

    assert len(prompts_seen) == 2
    retry_prompt = prompts_seen[1]
    assert "test_hidden" not in retry_prompt
    assert "test_inside" in retry_prompt or "test_low" in retry_prompt  # visible test names only


# --- Controlled Review Experiment: B0/B1/B2 (ACQE Decision 2, round 5) ----------------


def _codex_stream(agent_text, input_tokens=20, output_tokens=8):
    """A minimal but realistic --json exec stream: one agent_message item (what
    extract_agent_text() reads) plus one turn.completed usage line — the same two event
    types the real captured stream in smoke-test-evidence/ actually contains."""
    msg = json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": agent_text}})
    usage = json.dumps({"type": "turn.completed", "usage": {"input_tokens": input_tokens, "output_tokens": output_tokens}})
    return msg + "\n" + usage + "\n"


def _implement_handler(source):
    def handler(repo, prompt):
        (repo / "mod.py").write_text(source)
        return _codex_stream("Implemented the fix.")
    return handler


def _review_handler(finding_lines, prompts_seen=None):
    def handler(repo, prompt):
        if prompts_seen is not None:
            prompts_seen.append(prompt)
        return _codex_stream("\n".join(finding_lines))
    return handler


def _fix_handler(source):
    def handler(repo, prompt):
        (repo / "mod.py").write_text(source)
        return _codex_stream("Applied the reviewer's fix.")
    return handler


def _fake_codex_sequence(handlers, real_run):
    """Like `_fake_codex`, but each call in sequence is handled by its own
    `handlers[i](repo, prompt) -> raw stdout string`, for tests that need different
    canned codex output per call (implement vs. review vs. fix)."""
    calls = {"n": 0, "cmds": []}

    def fake_run(cmd, *args, **kwargs):
        if "codex" not in str(cmd[0]):
            return real_run(cmd, *args, **kwargs)
        cd_index = cmd.index("--cd")
        repo = Path(cmd[cd_index + 1])
        calls["cmds"].append(cmd)
        index = min(calls["n"], len(handlers) - 1)
        stdout = handlers[index](repo, kwargs.get("input", ""))
        calls["n"] += 1

        class Result:
            returncode = 0
            stderr = ""

        Result.stdout = stdout
        return Result()

    return fake_run, calls


class FakeIsolation:
    """Unit-test runner that preserves the controlled experiment's call boundary."""

    def __init__(self, run_module, base):
        self.run_module = run_module
        self.base = Path(base)
        self.model = None
        self.effort = None

    def run_codex(self, repo, prompt, sandbox):
        return self.run_module.subprocess.run(
            self.run_module.codex_exec_argv(repo, sandbox=sandbox),
            input=prompt, capture_output=True, text=True, timeout=900, check=False,
        )


def test_extract_agent_text_pulls_agent_message_items_in_order(run_module):
    stream = (
        '{"type":"thread.started","thread_id":"x"}\n'
        '{"type":"item.completed","item":{"type":"command_execution","aggregated_output":"ignored"}}\n'
        '{"type":"item.completed","item":{"type":"agent_message","text":"first"}}\n'
        '{"type":"item.completed","item":{"type":"agent_message","text":"second"}}\n'
        '{"type":"turn.completed","usage":{"input_tokens":1,"output_tokens":1}}\n'
    )
    assert run_module.extract_agent_text(stream) == "first\nsecond"


def test_extract_agent_text_against_the_real_captured_codex_stream(run_module):
    """Not synthetic: the actual raw stream saved from the approved real-Codex smoke
    test (benchmarks/toy-agents/smoke-test-evidence/), so this parser is verified
    against genuine Codex output, not just a hand-written fixture."""
    evidence = (
        ROOT / "benchmarks" / "toy-agents" / "smoke-test-evidence"
        / "2026-11-10-clamp-raw-codex-stream.jsonl"
    )
    text = run_module.extract_agent_text(evidence.read_text(encoding="utf-8"))
    assert "clamp" in text.lower()
    assert len(text) > 0


def test_host_verify_findings_confirms_a_single_evidenced_finding_when_the_probe_fails(run_module, tmp_path):
    # clamp's "broken" patch (always returns lo) is exactly what own_good's
    # clamp(15, 0, 10) == 10 assertion catches.
    repo = run_module.make_repo(str(tmp_path), "clamp", "verify-broken")
    (repo / "mod.py").write_text(run_module.TASKS["clamp"]["broken"])
    findings = run_module.parse_findings([
        "[blocker] clamp never enforces the upper bound evidence: clamp(15, 0, 10) returns 0, not 10",
    ])
    verified = run_module.host_verify_findings(findings, "clamp", repo)
    assert len(verified) == 1
    assert verified[0]["verification_status"] == "confirmed"


def test_host_verify_findings_stays_supported_with_multiple_evidenced_findings(run_module, tmp_path):
    """A probe confirms only the finding that cites its exact witness."""
    repo = run_module.make_repo(str(tmp_path), "clamp", "verify-ambiguous")
    (repo / "mod.py").write_text(run_module.TASKS["clamp"]["broken"])
    findings = run_module.parse_findings([
        "[blocker] upper bound not enforced evidence: clamp(15, 0, 10) returns 0",
        "[major] lower bound handling looks suspicious evidence: unclear from the diff alone",
    ])
    verified = run_module.host_verify_findings(findings, "clamp", repo)
    assert len(verified) == 2
    assert [f["verification_status"] for f in verified] == ["confirmed", "supported"]
    assert verified[0]["host_confirmation"]["witness"] == "clamp(15, 0, 10)"
    assert "host_confirmation" not in verified[1]


def test_host_verify_findings_never_confirms_without_cited_evidence(run_module, tmp_path):
    repo = run_module.make_repo(str(tmp_path), "clamp", "verify-no-evidence")
    (repo / "mod.py").write_text(run_module.TASKS["clamp"]["broken"])
    findings = run_module.parse_findings(["[blocker] this looks wrong to me"])
    verified = run_module.host_verify_findings(findings, "clamp", repo)
    assert verified[0]["verification_status"] == "hypothesis"


def test_host_verify_findings_stays_supported_when_the_probe_does_not_target_that_defect(run_module, tmp_path):
    # clamp's "partial" patch passes visible tests but mishandles negative ranges — a
    # defect own_good's upper-bound-only assertion was never designed to catch, so even
    # a well-evidenced finding about it cannot be auto-Confirmed from this probe alone.
    repo = run_module.make_repo(str(tmp_path), "clamp", "verify-partial")
    (repo / "mod.py").write_text(run_module.TASKS["clamp"]["partial"])
    findings = run_module.parse_findings([
        "[major] negative ranges may be mishandled evidence: clamp(5, -10, -1) looks suspicious",
    ])
    verified = run_module.host_verify_findings(findings, "clamp", repo)
    assert verified[0]["verification_status"] == "supported"


def test_host_verify_findings_no_false_confirm_on_a_correct_implementation(run_module, tmp_path):
    """Case 3 of §13.3's validation set: no real bug. Even if a reviewer wrongly claims
    one with cited "evidence", host verification must not confirm it, since the
    independent probe does not fail against a genuinely correct implementation."""
    repo = run_module.make_repo(str(tmp_path), "clamp", "verify-fixed")
    (repo / "mod.py").write_text(run_module.TASKS["clamp"]["fixed"])
    findings = run_module.parse_findings([
        "[major] fabricated problem evidence: this is not actually true",
    ])
    verified = run_module.host_verify_findings(findings, "clamp", repo)
    assert verified[0]["verification_status"] == "supported"  # never "confirmed"


def test_host_verify_findings_rejects_unrelated_probe_failure(run_module, tmp_path, monkeypatch):
    repo = run_module.make_repo(str(tmp_path), "clamp", "unrelated-probe")
    (repo / "mod.py").write_text(run_module.TASKS["clamp"]["broken"])
    spec = dict(run_module.REVIEW_TASKS["clamp"])
    spec["own_good"] = "def test_unrelated():\n    assert False\n"
    monkeypatch.setitem(run_module.REVIEW_TASKS, "clamp", spec)
    findings = run_module.parse_findings([
        "[major] upper bound fails evidence: clamp(15, 0, 10) returns 0",
    ])
    verified = run_module.host_verify_findings(findings, "clamp", repo)
    assert verified[0]["verification_status"] == "supported"
    assert "host_confirmation" not in verified[0]


def test_host_verify_findings_does_not_promote_duplicate_or_unrelated_claims(run_module, tmp_path):
    repo = run_module.make_repo(str(tmp_path), "clamp", "duplicate-probe")
    (repo / "mod.py").write_text(run_module.TASKS["clamp"]["broken"])
    findings = run_module.parse_findings([
        "[major] upper bound fails evidence: clamp(15, 0, 10) returns 0",
        "[major] upper bound looks wrong evidence: clamp(15, 0, 10) returns 0",
        "[major] unrelated styling issue evidence: clamp(15, 0, 10) returns 0",
    ])
    verified = run_module.host_verify_findings(findings, "clamp", repo)
    assert [f["verification_status"] for f in verified] == ["supported"] * 3


# --- ACQE Phase 3 scoring-fairness regression (2026-11-11) ----------------------------
#
# Reproduces and fixes the real offset_bucket pilot's false negative: host_verify_findings()
# used to require the witness inside the *evidence* half only. A reviewer who wrote the
# same concrete example before the literal word "evidence:" (so parse_findings() put it in
# the *claim* half instead) got "supported" for an otherwise-identical, otherwise-matching
# finding that an identically-worded "evidence:"-first version would have gotten
# "confirmed" for. See docs/development/ACQE-GAP-ANALYSIS.md §21 for the full audit.


def test_host_verify_findings_confirms_regardless_of_witness_position_in_claim_or_evidence(
    run_module, tmp_path,
):
    """The exact bug: the same defect, witness, and claim-term match must score the
    same whether the reviewer's concrete example lands before or after "evidence:"."""
    repo = run_module.make_repo(str(tmp_path), "clamp", "verify-position-invariance")
    (repo / "mod.py").write_text(run_module.TASKS["clamp"]["broken"])

    witness_in_evidence = run_module.parse_findings([
        "[major] upper bound not enforced "
        "evidence: clamp(15, 0, 10) returns 0, not 10",
    ])
    witness_in_claim = run_module.parse_findings([
        "[major] upper bound not enforced; for example, clamp(15, 0, 10) returns 0 "
        "instead of 10 evidence: the implementation never compares against hi at all",
    ])

    confirmed_from_evidence = run_module.host_verify_findings(witness_in_evidence, "clamp", repo)
    confirmed_from_claim = run_module.host_verify_findings(witness_in_claim, "clamp", repo)

    assert confirmed_from_evidence[0]["verification_status"] == "confirmed"
    assert confirmed_from_claim[0]["verification_status"] == "confirmed"
    assert confirmed_from_claim[0]["host_confirmation"]["probe"] == "own_good"


def test_host_verify_findings_does_not_confirm_an_unrelated_finding_for_merely_containing_the_witness(
    run_module, tmp_path,
):
    """Widening the witness search to claim+evidence must not turn "contains the
    witness string anywhere" into a free pass -- the claim_terms relevance check
    (matched against the claim only) still has to hold."""
    repo = run_module.make_repo(str(tmp_path), "clamp", "verify-unrelated-claim")
    (repo / "mod.py").write_text(run_module.TASKS["clamp"]["broken"])
    findings = run_module.parse_findings([
        "[minor] the function is missing a docstring "
        "evidence: clamp(15, 0, 10) has no accompanying documentation",
    ])
    verified = run_module.host_verify_findings(findings, "clamp", repo)
    assert verified[0]["verification_status"] == "supported"
    assert "host_confirmation" not in verified[0]


def test_host_verify_findings_stays_conservative_on_correct_code_with_witness_in_claim(
    run_module, tmp_path,
):
    """A matching claim+witness is not enough by itself: against a genuinely correct
    implementation, the independent probe does not fail, so even the previously-missed
    claim-half phrasing must still stay Supported, never Confirmed."""
    repo = run_module.make_repo(str(tmp_path), "clamp", "verify-position-on-fixed-code")
    (repo / "mod.py").write_text(run_module.TASKS["clamp"]["fixed"])
    findings = run_module.parse_findings([
        "[major] upper bound not enforced; for example, clamp(15, 0, 10) returns 0 "
        "instead of 10 evidence: the implementation never compares against hi at all",
    ])
    verified = run_module.host_verify_findings(findings, "clamp", repo)
    assert verified[0]["verification_status"] == "supported"
    assert "host_confirmation" not in verified[0]


def test_review_only_pilot_scorer_never_writes_the_raw_witness_into_its_report(run_module, tmp_path):
    """benchmarks/toy-agents/review-only-pilot/offset_bucket/score_review.py's redact()
    is the layer responsible for keeping the private witness out of any saved report,
    log, or printed output -- confirm it still does that after the host_verify_findings
    fix above (host_verify_findings itself still legitimately carries the witness in
    its return value for the Host's own bookkeeping; redact() is what strips it)."""
    import importlib.util

    scorer_path = (
        ROOT / "benchmarks/toy-agents/review-only-pilot/offset_bucket/score_review.py"
    )
    spec = importlib.util.spec_from_file_location("acqe_review_only_scorer", scorer_path)
    scorer = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(scorer)

    repo = run_module.make_repo(str(tmp_path), "clamp", "verify-redaction")
    (repo / "mod.py").write_text(run_module.TASKS["clamp"]["broken"])
    findings = run_module.parse_findings([
        "[major] upper bound not enforced evidence: clamp(15, 0, 10) returns 0, not 10",
    ])
    verified = run_module.host_verify_findings(findings, "clamp", repo)
    assert verified[0]["host_confirmation"]["witness"] == "clamp(15, 0, 10)"  # unredacted here

    redacted = scorer.redact(verified)
    report_text = "\n".join(
        f"{item['claim']} {item.get('host_confirmation')}" for item in redacted
    )
    assert "clamp(15, 0, 10)" not in report_text
    assert redacted[0]["host_confirmation"] == {"probe": "own_good", "witness_matched": True}


def test_b2_lens_uses_only_public_issue_and_never_private_fixture_data(
    run_module, tmp_path, monkeypatch,
):
    marker = "PRIVATE_ANSWER_DO_NOT_SHOW"
    spec = dict(run_module.REVIEW_TASKS["clamp"])
    spec["hidden"] += f"\n# {marker}\n"
    spec["private_metadata"] = {"planted_defect": marker}
    monkeypatch.setitem(run_module.REVIEW_TASKS, "clamp", spec)
    prompt = run_module.contextual_review_prompt("clamp", "public diff", "2 passed", True)
    assert marker not in prompt
    assert "lo > hi" not in prompt
    assert "Use the Data Integrity review lens:" in prompt
    assert run_module.select_review_lens("shared state under concurrent calls") == "Concurrency"
    assert run_module.select_review_lens("module cache lifecycle") == "Architecture"
    assert run_module.select_review_lens("validate record values") == "Data Integrity"


def test_architecture_risk_review_fixture_preserves_uncertainty(run_module, tmp_path, monkeypatch):
    """The fourth case is a real review-only repo, with a risk and no proven defect."""
    monkeypatch.setattr(run_module, "native_executable", lambda name: "codex")
    spec = run_module.REVIEW_TASKS["architecture_risk"]
    prompts = []
    handlers = [
        _implement_handler(spec["buggy"]),
        _review_handler([
            "[assumption] cache invalidation may matter if callers change files later "
            "evidence: mod.py caches by path; current caller behavior is unspecified beyond reuse"
        ], prompts),
        _review_handler(["No confirmed defect; cache lifecycle needs a caller contract."], prompts),
    ]
    fake_run, calls = _fake_codex_sequence(handlers, run_module.subprocess.run)
    monkeypatch.setattr(run_module.subprocess, "run", fake_run)

    results = run_module.run_controlled_review_experiment(
        str(tmp_path), "architecture_risk", max_fix_attempts=1,
        isolation=FakeIsolation(run_module, tmp_path),
    )
    assert calls["n"] == 3
    assert all(case["resolved"] for case in results.values())
    assert all(not results[arm]["fix_attempted"] for arm in ("b1", "b2"))
    assert results["b1"]["findings"][0]["verification_status"] == "supported"
    assert results["b2"]["findings"] == []
    assert "Use the Architecture review lens:" in prompts[1]
    assert "test_returns_independent_values" not in "\n".join(prompts)


def test_review_experiment_rejects_unbounded_fix_budget_before_agent_call(run_module, tmp_path):
    with pytest.raises(ValueError, match="must be 0 or 1"):
        run_module.run_controlled_review_experiment(
            str(tmp_path), "clamp", max_fix_attempts=2,
        )


def test_review_experiment_fails_closed_without_matching_isolation(run_module, tmp_path):
    with pytest.raises(RuntimeError, match="verified OS isolation"):
        run_module.run_controlled_review_experiment(str(tmp_path), "clamp")
    with pytest.raises(RuntimeError, match="model and effort differ"):
        run_module.run_controlled_review_experiment(
            str(tmp_path), "clamp", model="gpt-6.1-sol", effort="medium",
            isolation=FakeIsolation(run_module, tmp_path),
        )


def test_review_cli_rejects_missing_model_before_any_agent_call(run_module, tmp_path, monkeypatch):
    monkeypatch.setattr(
        run_module.sys, "argv",
        ["run.py", "review-experiment", "clamp", "--out", str(tmp_path / "results")],
    )
    monkeypatch.setattr(
        run_module, "quota", lambda: pytest.fail("quota lookup must not run before validation")
    )
    with pytest.raises(SystemExit) as error:
        run_module.main()
    assert error.value.code == 2


def test_review_cli_refuses_committed_hidden_answer_before_agent_call(run_module, monkeypatch):
    assert run_module.REVIEW_TASKS["clamp"]["hidden"]
    # "architecture_risk" is committed in this repo's own Git history (toybench.py's own
    # comment on it: public Smoke Test / no-defect control, never a withheld private
    # answer -- see docs/development/ACQE-GAP-ANALYSIS.md §23). The guard must refuse it
    # exactly like any other committed case; it must NOT pass silently as if its answer
    # were still private.
    with pytest.raises(RuntimeError, match="Private Fixture for architecture_risk has committed"):
        run_module.assert_fixture_not_in_committed_head("architecture_risk")
    monkeypatch.setattr(
        run_module.sys, "argv",
        [
            "run.py", "review-experiment", "clamp", "--out",
            str(Path.home() / "acqe-published-fixture-rejection"),
            "--model", "gpt-6.1-sol", "--effort", "medium",
        ],
    )
    monkeypatch.setattr(run_module, "quota", lambda: pytest.fail("must not query quota"))
    monkeypatch.setattr(run_module, "ACQEIsolation", lambda *args: pytest.fail("must not create runner"))
    with pytest.raises(RuntimeError, match="Private Fixture for clamp has committed"):
        run_module.main()


def test_review_artifacts_preserve_frozen_input_and_call_streams(run_module, tmp_path):
    repo = run_module.make_repo(tmp_path, "clamp", "frozen")
    (repo / "mod.py").write_text(run_module.TASKS["clamp"]["fixed"])
    run_module.git(repo, "add", "-A")
    run_module.git(repo, "commit", "-q", "-m", "frozen implementation")
    out = tmp_path / "host-output"
    out.mkdir()

    class Isolation:
        base = tmp_path
        model = "gpt-6.1-sol"
        effort = "medium"
        calls = [{"repo": repo.name, "prompt": "public issue", "stdout": "raw stream"}]

    run_module.save_review_artifacts(Isolation, "clamp", out, "test-run", 0)
    manifest = json.loads((out / "clamp-manifest.json").read_text())
    assert manifest["frozen_commit"] == run_module.repo_commit(repo)
    assert manifest["agent_calls"] == 1
    assert (out / "clamp-frozen.bundle").is_file()
    replay = tmp_path / "replay"
    run_module.subprocess.run(
        ["git", "clone", "-q", str(out / "clamp-frozen.bundle"), str(replay)], check=True,
    )
    assert run_module.repo_commit(replay) == manifest["frozen_commit"]
    assert json.loads((out / "clamp-agent-calls.jsonl").read_text())["stdout"] == "raw stream"
    assert "hidden" not in (out / "clamp-manifest.json").read_text()


def test_review_experiment_stops_after_failed_frozen_call(run_module, tmp_path, monkeypatch):
    monkeypatch.setattr(run_module, "native_executable", lambda name: "codex")
    real_run = run_module.subprocess.run
    calls = {"codex": 0}

    def fail_first(cmd, *args, **kwargs):
        if "codex" not in str(cmd[0]):
            return real_run(cmd, *args, **kwargs)
        calls["codex"] += 1

        class Result:
            returncode = 1
            stdout = ""
            stderr = "model unavailable"

        return Result()

    monkeypatch.setattr(run_module.subprocess, "run", fail_first)
    with pytest.raises(RuntimeError, match="frozen implementation Codex call failed"):
        run_module.run_controlled_review_experiment(
            str(tmp_path), "clamp", isolation=FakeIsolation(run_module, tmp_path)
        )
    assert calls["codex"] == 1


def test_controlled_review_experiment_shares_one_frozen_implementation(tmp_path, monkeypatch, run_module):
    monkeypatch.setattr(run_module, "native_executable", lambda name: "codex")
    fixed = run_module.TASKS["clamp"]["fixed"]
    handlers = [
        _implement_handler(fixed),             # shared implement
        _review_handler(["No issues found."]),  # b1 review
        _review_handler(["No issues found."]),  # b2 review
    ]
    fake_run, calls = _fake_codex_sequence(handlers, run_module.subprocess.run)
    monkeypatch.setattr(run_module.subprocess, "run", fake_run)

    results = run_module.run_controlled_review_experiment(
        str(tmp_path), "clamp", max_fix_attempts=1,
        isolation=FakeIsolation(run_module, tmp_path),
    )

    assert set(results) == {"b0", "b1", "b2"}
    # Exactly one implement call (shared), one review call each for b1/b2, no fix calls
    # (no blocking findings) — 3 total codex calls for all three arms combined.
    assert calls["n"] == 3
    for arm_id, case in results.items():
        assert case["arm"] == arm_id
        assert case["run_envelope"]["case_id"] == "clamp"
    assert len({case["run_envelope"]["repo_commit"] for case in results.values()}) == 1
    assert results["b0"]["run_envelope"]["stages"] == [
        {"name": "implement_frozen", "seconds": results["b0"]["run_envelope"]["stages"][0]["seconds"],
         "tokens": {"input_tokens": 20, "output_tokens": 8}},
    ]
    assert len(results["b1"]["run_envelope"]["stages"]) == 2  # implement + review, no fix
    assert len(results["b2"]["run_envelope"]["stages"]) == 2
    assert results["b1"]["run_envelope"]["tool_permissions"]["review_sandbox_mode"] == "read-only"
    assert results["b1"]["run_envelope"]["stages"][1]["tokens"] == {"input_tokens": 20, "output_tokens": 8}
    assert not list(tmp_path.rglob("*test_hidden*"))
    assert not list(tmp_path.rglob("*test_own_good_probe*"))


def test_review_runs_even_when_the_frozen_implementation_already_passes_visible_tests(tmp_path, monkeypatch, run_module):
    """Decision 2's explicit requirement: B1/B2 review unconditionally, unlike
    B_retry's test-guided stop."""
    monkeypatch.setattr(run_module, "native_executable", lambda name: "codex")
    fixed = run_module.TASKS["clamp"]["fixed"]  # passes visible tests immediately
    handlers = [_implement_handler(fixed), _review_handler(["No issues found."]), _review_handler(["No issues found."])]
    fake_run, calls = _fake_codex_sequence(handlers, run_module.subprocess.run)
    monkeypatch.setattr(run_module.subprocess, "run", fake_run)

    run_module.run_controlled_review_experiment(
        str(tmp_path), "clamp", max_fix_attempts=0,
        isolation=FakeIsolation(run_module, tmp_path),
    )
    assert calls["n"] == 3  # implement + b1 review + b2 review, review never skipped


def test_b1_and_b2_prompts_differ_only_by_the_contextual_focus_sentence(tmp_path, monkeypatch, run_module):
    monkeypatch.setattr(run_module, "native_executable", lambda name: "codex")
    fixed = run_module.TASKS["clamp"]["fixed"]
    b1_prompts, b2_prompts = [], []

    def route(repo, prompt):
        # Distinguish b1 vs b2 clones by directory name, since both reviews are
        # otherwise identical calls in sequence.
        target = b2_prompts if "-b2" in str(repo) else b1_prompts
        target.append(prompt)
        return _codex_stream("No issues found.")

    handlers = [_implement_handler(fixed), route, route]
    fake_run, _ = _fake_codex_sequence(handlers, run_module.subprocess.run)
    monkeypatch.setattr(run_module.subprocess, "run", fake_run)

    run_module.run_controlled_review_experiment(
        str(tmp_path), "clamp", max_fix_attempts=0,
        isolation=FakeIsolation(run_module, tmp_path),
    )

    assert len(b1_prompts) == 1 and len(b2_prompts) == 1
    assert "Use the Data Integrity review lens:" not in b1_prompts[0]
    assert "Use the Data Integrity review lens:" in b2_prompts[0]
    # Everything else about the instructions is shared, not just coincidentally similar.
    assert "Do not edit any files" in b1_prompts[0] and "Do not edit any files" in b2_prompts[0]


def test_review_arm_attempts_a_bounded_fix_only_for_blocking_findings(tmp_path, monkeypatch, run_module):
    monkeypatch.setattr(run_module, "native_executable", lambda name: "codex")
    broken = run_module.TASKS["clamp"]["broken"]
    fixed = run_module.TASKS["clamp"]["fixed"]
    handlers = [
        _implement_handler(broken),  # frozen implementation has a real, host-verifiable bug
        _review_handler(["[blocker] upper bound not enforced evidence: clamp(15, 0, 10) returns 0"]),  # b1
        _fix_handler(fixed),  # b1's fix attempt
        _review_handler(["[blocker] upper bound not enforced evidence: clamp(15, 0, 10) returns 0"]),  # b2
        _fix_handler(fixed),  # b2's fix attempt
    ]
    fake_run, calls = _fake_codex_sequence(handlers, run_module.subprocess.run)
    monkeypatch.setattr(run_module.subprocess, "run", fake_run)

    results = run_module.run_controlled_review_experiment(
        str(tmp_path), "clamp", max_fix_attempts=1,
        isolation=FakeIsolation(run_module, tmp_path),
    )

    assert calls["n"] == 5  # 1 implement + (1 review + 1 fix) * 2
    for arm_id in ("b1", "b2"):
        case = results[arm_id]
        assert case["run_envelope"]["stages"][-1]["name"] == "fix"
        assert case["findings"][0]["verification_status"] == "confirmed"
        assert case["resolved"] is True  # the fix made the hidden test pass too
    assert results["b0"]["resolved"] is False  # the un-reviewed frozen attempt stays broken


def test_review_arm_never_exposes_the_hidden_test_to_the_reviewer(tmp_path, monkeypatch, run_module):
    monkeypatch.setattr(run_module, "native_executable", lambda name: "codex")
    marker = "PRIVATE_REVIEW_ANSWER"
    spec = dict(run_module.REVIEW_TASKS["clamp"])
    spec["hidden"] += f"\n# {marker}\n"
    spec["private_metadata"] = {"answer": marker}
    monkeypatch.setitem(run_module.REVIEW_TASKS, "clamp", spec)
    fixed = run_module.TASKS["clamp"]["fixed"]
    seen_hidden_file = []
    review_prompts = []

    def review_route(repo, prompt):
        seen_hidden_file.append((Path(repo) / "tests" / "test_hidden.py").exists())
        review_prompts.append(prompt)
        return _codex_stream("No issues found.")

    handlers = [_implement_handler(fixed), review_route, review_route]
    fake_run, _ = _fake_codex_sequence(handlers, run_module.subprocess.run)
    monkeypatch.setattr(run_module.subprocess, "run", fake_run)

    run_module.run_controlled_review_experiment(
        str(tmp_path), "clamp", max_fix_attempts=0,
        isolation=FakeIsolation(run_module, tmp_path),
    )
    assert seen_hidden_file == [False, False]
    assert all(marker not in prompt for prompt in review_prompts)
    assert all("test_hidden" not in prompt for prompt in review_prompts)
    assert not list(tmp_path.rglob("*test_hidden*"))


@pytest.mark.real_agents
def test_os_isolation_canary_without_model_call(run_module):
    """Opt-in host preflight: exercise macOS file access and fail-closed canary."""
    if run_module.sys.platform != "darwin":
        pytest.skip("macOS sandbox-exec is required")
    with run_module.ACQEIsolation("gpt-6.1-sol", "medium") as isolation:
        repo = run_module.make_repo(isolation.base, "clamp", "candidate")
        sibling = run_module.make_repo(isolation.base, "clamp", "sibling")
        profile = isolation._profile(repo)
        isolation._canary(repo, profile)
        login = run_module.subprocess.run(
            isolation._wrapped(profile, [run_module.native_executable("codex"), "login", "status"]),
            cwd=repo, env=isolation.env, capture_output=True, text=True, timeout=30, check=False,
        )
        assert login.returncode == 0
        visible = run_module.pytest(repo, "tests")
        assert visible.returncode == 0
        hidden = run_module.host_test_in_shadow(
            repo, "tests/test_hidden.py", run_module.REVIEW_TASKS["clamp"]["hidden"]
        )
        assert hidden.returncode == 1
        assert not list(repo.rglob("*hidden*"))
        paths = {
            "fixture": str(ROOT / "tests" / "toybench.py"),
            "fixture_data_volume": "/System/Volumes/Data" + str(ROOT / "tests" / "toybench.py"),
            "private": str(isolation.private / "canary"),
            "private_data_volume": "/System/Volumes/Data" + str(isolation.private / "canary"),
            "sibling": str(sibling / "mod.py"),
            "sibling_data_volume": "/System/Volumes/Data" + str(sibling / "mod.py"),
        }
        test_profile = isolation._test_profile(repo, private=False)
        probe = run_module.subprocess.run(
            isolation._wrapped(test_profile, [
                "/usr/bin/python3", "-c", isolation._ACCESS_PROBE, json.dumps(paths),
            ]),
            cwd=repo, env=isolation.env, capture_output=True, text=True, timeout=30, check=False,
        )
        assert probe.returncode == 0
        assert json.loads(probe.stdout) == {key: False for key in paths}
        for candidate_profile in (profile, test_profile):
            write_check = run_module.subprocess.run(
                isolation._wrapped(candidate_profile, [
                    "/usr/bin/python3", "-c",
                    "import pathlib,sys; p=pathlib.Path(sys.argv[1]); "
                    "\ntry: p.write_text('tamper'); print('writable')"
                    "\nexcept OSError: print('denied')",
                    str(isolation.profiles / "tamper.sb"),
                ]),
                cwd=repo, env=isolation.env, capture_output=True, text=True, timeout=30, check=False,
            )
            assert write_check.returncode == 0
            assert write_check.stdout.strip() == "denied"
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            listener.listen()
            port = listener.getsockname()[1]
            network = run_module.subprocess.run(
                isolation._wrapped(profile, [
                    "/usr/bin/python3", "-c",
                    "import socket,sys; s=socket.socket(); print(s.connect_ex(('127.0.0.1', int(sys.argv[1]))))",
                    str(port),
                ]),
                cwd=repo, env=isolation.env, capture_output=True, text=True, timeout=30, check=False,
            )
            assert network.returncode == 0
            assert int(network.stdout.strip()) != 0
        with tempfile.TemporaryDirectory(prefix="acqe-unix-", dir="/private/tmp") as folder:
            endpoint = str(Path(folder) / "socket")
            with socket.socket(socket.AF_UNIX) as listener:
                listener.bind(endpoint)
                listener.listen()
                local_socket = run_module.subprocess.run(
                    isolation._wrapped(profile, [
                        "/usr/bin/python3", "-c",
                        "import socket,sys; s=socket.socket(socket.AF_UNIX); print(s.connect_ex(sys.argv[1]))",
                        endpoint,
                    ]),
                    cwd=repo, env=isolation.env, capture_output=True, text=True, timeout=30, check=False,
                )
                assert local_socket.returncode == 0
                assert int(local_socket.stdout.strip()) != 0
        weak = isolation.profiles / "intentionally-weak.sb"
        weak.write_text("(version 1)\n(allow default)\n")
        with pytest.raises(RuntimeError, match="OS isolation canary failed"):
            isolation._canary(repo, weak)


@pytest.mark.real_agents
def test_run_b_retry_against_real_codex_smoke(tmp_path):
    """Opt-in only (`pytest -m real_agents`): actually exercises Arm B_retry against a
    real, installed, logged-in Codex CLI on one toy task. Not run by default, and not
    run as part of this change — see docs/development/ACQE-GAP-ANALYSIS.md §11.7's
    reporting on what was/wasn't verified against real Codex this round."""
    run_module = _load_run_module()
    case = run_module.run_b_retry(str(tmp_path), "clamp")
    assert case["rounds_used"] >= 1
    assert isinstance(case["resolved"], bool)
