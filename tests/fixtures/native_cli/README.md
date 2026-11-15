# Native CLI fixtures (P0A)

These fixtures are inputs for telemetry tests. No model was called to produce them.

| File | Kind | Provenance |
|---|---|---|
| `codex_clamp_rawcapture_2026-11-10.jsonl` | real capture | Byte-identical copy of `benchmarks/toy-agents/smoke-test-evidence/2026-11-10-clamp-raw-codex-stream.jsonl` (sha256 `0f74324c…8e71`). The original is not modified. |
| `*_synthetic.*` | synthetic | Hand-written to exercise one rule each. Expected values in each `.expected.json` were computed by hand, not by the code under test. |

Important: the real capture is one turn from one run. It is evidence for `cached_input_tokens <= input_tokens`
(OBSERVED), and nothing more. Reasoning placement, cache-write placement, per-turn semantics and Claude cache
placement are UNKNOWN and must stay that way until a controlled capture is approved.
