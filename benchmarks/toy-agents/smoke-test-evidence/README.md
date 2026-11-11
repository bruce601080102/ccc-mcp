# ACQE Phase 1 real-Codex smoke test evidence

Durable copies of the real (non-mocked) Codex integration check referenced in
`docs/development/ACQE-GAP-ANALYSIS.md` §11/§12/§13. These were produced by an actual
`codex exec` run against a real, logged-in Codex CLI — not a unit test with a stubbed
subprocess call — so they're kept here instead of only in a temp directory.

## 2026-11-10-clamp-b_retry

Command run (see `ACQE-GAP-ANALYSIS.md` §12.3 for why only a single, bounded case was
run rather than a full batch):

```bash
python3 benchmarks/toy-agents/run.py run --out /tmp/acqe-smoke --arms b_retry clamp
```

- `2026-11-10-clamp-b_retry.console.log` — the command's raw stdout and exit code.
- `2026-11-10-clamp-b_retry.jsonl` — the one case record `b_retry.jsonl` contained:
  real diff, real host test stdout, `resolved: true`, and this round's `run_envelope`
  (token usage, timing, `cli_version` as actually read from `codex --version`, and the
  honest `model_verification_status: "unknown"` — Codex's own `--json` output never
  exposed which model served the request; see the Gap Analysis doc's §11.7 item c and
  §12.1 for why that's a known, unfixed limitation rather than an oversight here).

Outcome: Codex fixed the planted `clamp` bug correctly on its first attempt (no retry
round needed), in 22 seconds and 66,659 total tokens (66,370 input — of which 61,568
were cache hits — plus 289 output). Both the agent-visible test suite and the separate,
never-shown hidden acceptance test passed. Exit code 0.

## 2026-11-10-clamp-raw-codex-stream.jsonl

A second, separate bounded call (same tiny `clamp` task, one `codex exec` invocation, no
retries) — this one's purpose was to inspect Codex's **raw** `--json` stream directly,
to verify (not assume) `infra/telemetry.py`'s token-field semantics for
`ACQE-GAP-ANALYSIS.md` §14 Decision 3. The local scratch temp-directory path that
appeared in one command's output was redacted (replaced with `<tmp-repo>`) before this
file was added to the repository; no keys, credentials, or personal information were
present in the original capture. Findings this file supports:

- Exactly one `turn.completed` event occurred for this whole `codex exec` call (not
  several internal turns summed, as an earlier, unverified hypothesis in this doc
  assumed — corrected in §14).
- The `usage` object contains `cache_write_input_tokens`, a field `usage_from_output()`
  does not currently sum — a real, minor completeness gap, documented rather than
  silently left for someone else to find.
