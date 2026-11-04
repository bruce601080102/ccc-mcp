"""Tokens-per-resolved comparison between ccc-mcp and a single agent."""
import json

from typer.testing import CliRunner

from ccc_mcp.evaluation_engine import benchmark
from ccc_mcp.interfaces.cli import app


def arm(tokens, resolved, *, complete=True, start=0):
    return [
        {"instance_id": f"t{start + i}", "tokens_total": tokens, "resolved": r, "usage_complete": complete}
        for i, r in enumerate(resolved)
    ]


def test_tokens_per_resolved_counts_failed_attempts_too():
    # 4 tasks, 2 resolved, 100 tokens each: 400 spent / 2 resolved = 200 per resolved.
    result = benchmark.compare(
        arm(100, [True, True, False, False]), arm(100, [True, False, False, False])
    )
    assert result["ccc_mcp"]["tokens_per_resolved"] == 200
    assert result["direct"]["tokens_per_resolved"] == 400
    assert result["both_resolved"] == 1 and result["only_ccc_mcp_resolved"] == 1
    assert "smoke test" in result["verdict"]


def test_a_cheap_arm_that_resolves_nothing_has_no_per_resolved_cost():
    ccc, direct = arm(10, [True] * 25), arm(5, [False] * 25)
    result = benchmark.compare(ccc, direct)
    assert result["direct"]["tokens_per_resolved"] is None
    assert "undefined" in result["verdict"]


def test_verdict_names_the_cheaper_arm_and_warns_about_missing_token_data():
    ccc = arm(300, [True] * 20 + [False] * 5, complete=False)
    direct = arm(200, [True] * 15 + [False] * 10)
    result = benchmark.compare(ccc, direct)
    assert "not cheaper" in result["verdict"]
    assert "understated" in result["verdict"]
    assert result["ccc_mcp"]["resolve_rate"] == 0.8 and result["direct"]["resolve_rate"] == 0.6


def test_labels_file_supplies_ground_truth(tmp_path):
    cases = [{"instance_id": "a", "tokens_total": 10}, {"instance_id": "b", "tokens_total": 10}]
    labels = tmp_path / "r.json"
    labels.write_text(json.dumps({"resolved_ids": ["a"], "unresolved_ids": ["b"]}))
    result = benchmark.compare(cases, cases, benchmark.load_labels(labels), benchmark.load_labels(labels))
    assert result["ccc_mcp"]["resolved"] == 1


def test_cli_compares_two_files(tmp_path):
    for name, data in (("ccc.jsonl", arm(100, [True, False])), ("direct.jsonl", arm(80, [True, True]))):
        (tmp_path / name).write_text("\n".join(json.dumps(c) for c in data))
    result = CliRunner().invoke(
        app, ["--home", str(tmp_path / "home"), "compare-tokens", str(tmp_path / "ccc.jsonl"), str(tmp_path / "direct.jsonl")]
    )
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["shared_tasks"] == 2
