# ACQE Review-only Pilot — `offset_bucket` (manual, no local tools)

This folder is a plain set of generated files, not new Benchmark
infrastructure. It reuses `benchmarks/toy-agents/run.py`'s existing
`general_review_prompt`, `contextual_review_prompt`, `select_review_lens`,
`parse_findings` (`core/flows.py`), `host_verify_findings`, and
`load_private_fixture` exactly as they already are — nothing in `run.py` or
any core module was changed to produce this.

## What's here

- `b1_prompt.txt` / `b2_prompt.txt` — the two prompts, ready to paste.
- `diff.txt` / `visible_stdout.txt` — the public diff and the Host's own
  visible-test output already embedded in both prompts (kept separately for
  your own reference).
- `build_prompts.py` — regenerates the two prompt files above. No model call.
- `score_review.py` — the offline Host scorer. No model call.
- `mock_b1_response.txt` / `mock_b2_response.txt` / `mock_verification_output.txt`
  — this round's **Mock** verification of the scoring flow (see below). Not
  real model output.

## 1–2. B1 / B2 Prompt file paths

```
benchmarks/toy-agents/review-only-pilot/offset_bucket/b1_prompt.txt
benchmarks/toy-agents/review-only-pilot/offset_bucket/b2_prompt.txt
```

Paste each into its **own, separate, fresh ChatGPT conversation** (no local
tools / no code interpreter / no browsing enabled — plain chat). Do not let
one conversation see the other's prompt or answer. Both prompts carry the
exact same public issue, buggy code, and visible-test result; only B2 names
a specific review lens. Neither contains a hidden test, a reference fix, a
defect label, or a confirmation witness — only `PUBLIC_CASES["offset_bucket"]`
(`issue`/`buggy`/`visible`) ever went into them.

## 3. Saving the two replies

Copy each conversation's full reply, verbatim, into:

```
benchmarks/toy-agents/review-only-pilot/offset_bucket/b1_response.txt
benchmarks/toy-agents/review-only-pilot/offset_bucket/b2_response.txt
```

(Empty placeholders for these two files are already in this folder — overwrite
them with the real replies.) Also jot down, next to them or in a notes file,
which model/version you selected in that chat and any effort/thinking setting
you picked — this harness cannot verify that independently, and it must be
disclosed with the result (same `requested_unconfirmed` caveat already used
for Codex's own served-model identity throughout `ACQE-GAP-ANALYSIS.md`).

## 4. Host offline-scoring command

```bash
.venv/bin/python benchmarks/toy-agents/review-only-pilot/offset_bucket/score_review.py \
  benchmarks/toy-agents/review-only-pilot/offset_bucket/b1_response.txt \
  benchmarks/toy-agents/review-only-pilot/offset_bucket/b2_response.txt
```

Run this from the `ccc-mcp` repository root. It loads the real private
Fixture (default path: `~/.local/share/ccc-mcp/acqe-phase2/private_fixture.json`,
override with `--private-fixture`) **on the Host only**, rebuilds the same
frozen public repo the prompts were generated from, parses each reply's
`[severity] claim evidence: ...` lines with the existing Finding grammar, and
host-verifies them with `host_verify_findings` — the same function, doing the
same thing, §17/§18 already tested it with. The report
(`score_report.txt`, written next to this file) never contains the Fixture's
own secret strings: a confirmed Finding's `host_confirmation` is written as
`{"probe": "own_good", "witness_matched": true}`, never the witness text
itself.

## 5. Mock verification result (this round; no real model call)

`score_review.py --mock` was run against two self-authored, clearly-labeled
placeholder replies (`mock_b1_response.txt`/`mock_b2_response.txt`) to
exercise the whole scoring path end to end without touching the real private
Fixture. Result (also saved in `mock_verification_output.txt`):

```
=== B1 (general_review_prompt) ===
  [major] confirmed  the boundary check uses a strict greater-than comparison so the next bucket's first offset is misassigned
      evidence: bucket_for_offset(10, 10)
      host_confirmation: {'probe': 'own_good', 'witness_matched': True}

=== B2 (contextual_review_prompt) ===
  [minor] supported  the function raises ValueError with a generic message instead of naming which argument was invalid
      evidence: raise ValueError('offset and width must be valid') does not say which of offset/width failed

=== Summary ===
B1 findings: 1 | confirmed: 1
B2 findings: 1 | confirmed: 0
```

This confirms: parsing, host-verification (including the `confirmed` path
through a real host-run probe), and witness redaction all work end to end.
It says nothing about real B1/B2 review quality — that still needs the two
real replies above.

## 6. Blockers

None for this plan. Everything through step 5 ran successfully with no model
call. The only remaining step is the one the design has always required a
human for: pasting the two prompts into ChatGPT yourself and saving the
replies — there is no blocker on this harness's side.
