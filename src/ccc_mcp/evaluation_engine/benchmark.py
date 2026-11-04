"""Compare two ways of doing the same tasks by what matters in the end: how many tokens
it takes to get one task resolved, and how often it gets resolved at all.

Each arm is a JSONL file with one object per task attempt:

    {"instance_id": "astropy__astropy-13453", "tokens_total": 41200, "resolved": true}

`ccc-mcp task export-cases` writes this shape for the ccc-mcp arm (`tokens_total` is
input + output tokens as the CLI reported them; cache tokens are listed separately and
not summed, because Codex counts cached input inside input_tokens and Claude does not).
For the single-agent arm, record the same three fields from that run. `resolved` may be
omitted in favour of a SWE-bench report passed as labels.

Cheaper only counts if it still solves the task, so the headline number is tokens per
resolved task (all tokens spent, including failed attempts, divided by tasks resolved).
"""

from pathlib import Path

from ..infra.errors import BridgeError
from .validation import load_cases, load_labels  # noqa: F401 - load_labels re-exported for the CLI


def _arm(cases, labels):
    rows = {}
    for case in cases:
        key = case["instance_id"]
        resolved = labels.get(key) if labels else case.get("resolved")
        tokens = case.get("tokens_total")
        if not isinstance(resolved, bool):
            raise BridgeError(f"{key}: no ground-truth label (resolved)")
        if key in rows:
            raise BridgeError(f"{key}: appears twice in one arm")
        rows[key] = {
            "resolved": resolved,
            "tokens": tokens if isinstance(tokens, int) and not isinstance(tokens, bool) else None,
            "complete": case.get("usage_complete", tokens is not None),
        }
    return rows


def _summary(rows):
    measured = [r for r in rows.values() if r["tokens"] is not None]
    resolved = sum(r["resolved"] for r in rows.values())
    spent = sum(r["tokens"] for r in measured)
    return {
        "tasks": len(rows),
        "resolved": resolved,
        "resolve_rate": round(resolved / len(rows), 4) if rows else None,
        "tasks_with_token_data": len(measured),
        "tasks_with_incomplete_token_data": sum(1 for r in measured if not r["complete"]),
        "tokens_total": spent,
        "tokens_per_resolved": round(spent / resolved) if resolved and measured else None,
    }


def compare(ccc_cases, direct_cases, ccc_labels=None, direct_labels=None):
    ccc, direct = _arm(ccc_cases, ccc_labels), _arm(direct_cases, direct_labels)
    shared = sorted(set(ccc) & set(direct))
    paired = {k: (ccc[k], direct[k]) for k in shared}
    result = {
        "ccc_mcp": _summary(ccc),
        "direct": _summary(direct),
        "shared_tasks": len(shared),
        "only_ccc_mcp_resolved": sum(1 for a, b in paired.values() if a["resolved"] and not b["resolved"]),
        "only_direct_resolved": sum(1 for a, b in paired.values() if b["resolved"] and not a["resolved"]),
        "both_resolved": sum(1 for a, b in paired.values() if a["resolved"] and b["resolved"]),
    }
    result["verdict"] = _verdict(result, len(ccc), len(direct))
    return result


def _verdict(result, ccc_n, direct_n):
    a, b = result["ccc_mcp"], result["direct"]
    if result["shared_tasks"] < 20 or ccc_n != direct_n:
        return (
            f"Only {result['shared_tasks']} task(s) appear in both arms: too few to compare "
            "reliably; treat this as a smoke test. Run the same tasks through both arms."
        )
    if a["tokens_per_resolved"] is None or b["tokens_per_resolved"] is None:
        return "An arm resolved nothing or has no token data, so tokens per resolved task is undefined."
    notes = []
    if a["tasks_with_incomplete_token_data"] or a["tasks_with_token_data"] < a["tasks"]:
        notes.append(
            "Some ccc-mcp rounds have no token data (ChatGPT web/MCP rounds report none), so its "
            "cost is understated."
        )
    cheaper = a["tokens_per_resolved"] < b["tokens_per_resolved"]
    text = (
        f"Per resolved task, ccc-mcp used {a['tokens_per_resolved']} tokens vs "
        f"{b['tokens_per_resolved']} direct ({'cheaper' if cheaper else 'not cheaper'}); "
        f"resolve rate {a['resolve_rate']} vs {b['resolve_rate']}."
    )
    return " ".join([text, *notes])


def load_arm(path):
    return load_cases(Path(path))


__all__ = ["compare", "load_arm", "load_labels"]
