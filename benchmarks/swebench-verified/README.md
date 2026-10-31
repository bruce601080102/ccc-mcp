# ccc-mcp: SWE-bench Verified — selected instances

**3/3 selected Astropy instances: SOLVED** using the official SWE-bench evaluation harness and the ccc-mcp **ChatGPT-only** workflow, **after multiple repair and evaluation iterations**.

This is **not** a one-shot score, not a result for all 500 Verified instances, not an official leaderboard submission, and not an independently certified evaluation. Each row is tied to its own local official-harness run.

| SWE-bench instance | Result | Evaluation run ID | Local harness report | ChatGPT development conversation |
|---|---|---|---|---|
| `astropy__astropy-12907` | SOLVED | `ccc_sop_12907_20261030_082922_e6f381` | [`12907-report.json`](./12907-report.json) | [ChatGPT 對話](https://chatgpt.com/share/6ae42e59-3cf4-83ee-b784-c2d1e784aeb8) |
| `astropy__astropy-13236` | SOLVED | `ccc_sop_13236_20261030_131736_40dfb9` | [`13236-report.json`](./13236-report.json) | [ChatGPT 對話](https://chatgpt.com/share/6ae42e69-bfe0-83e8-bdfd-c0a35da32b63) |
| `astropy__astropy-13398` | SOLVED | `ccc_sop_13398_20261031_031206_0ab9c4` | [`13398-report.json`](./13398-report.json) | [ChatGPT 對話](https://chatgpt.com/share/6ae42e7c-afd4-83ec-93b3-7dc2cb1d8597) |

## ChatGPT development conversations

The links above lead to **publicly shared ChatGPT conversations** documenting the iterative development process. They provide useful background, but are not a substitute for code patches, reproducible execution traces or official evaluator output. Only conversations explicitly shared by the author are linked here.

## Evidence and scope

The linked JSON files are copies of the **original local official-harness `report.json`** files; check the instance's `resolved: true` value. `MANIFEST.json` contains SHA-256 hashes and pass/fail counts. These files allow inspection of reported results but **do not by themselves let a third party reproduce the evaluation**. A reproducible release would also need the exact predictions patches, environment and commands.

The full Astropy repositories, Docker images, Python virtual environments and raw pytest logs are **not stored here**. The full local logs remain on the benchmark machine; their local hashes are recorded in the manifest for traceability.

Review JSON and Markdown content, **and especially the full content of public ChatGPT share links**, before making them public. Publishing local harness logs is not equivalent to official SWE-bench leaderboard certification.
