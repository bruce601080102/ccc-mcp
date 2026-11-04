"""Run real Codex agents on small self-made bug-fix tasks, two ways, and compare.

  direct    one `codex exec` on the issue text
  ccc-mcp   the SA -> SD/RD -> host tests -> REVIEW workflow, all roles on Codex

Ground truth is a hidden test file the agent never sees. Everything runs in a throwaway
directory and a throwaway ccc-mcp home. This SPENDS Codex quota; run one task first:

    python benchmarks/toy-agents/run.py run --out OUT_DIR clamp
    python benchmarks/toy-agents/run.py run --out OUT_DIR       # every task

Manual run (e.g. ChatGPT-only through the ccc-mcp UI, no Codex quota used here):

    python benchmarks/toy-agents/run.py prepare WORK_DIR   # makes the buggy repos + ISSUES.md
    ... register each repo as a ccc-mcp project and let the agent fix it ...
    python benchmarks/toy-agents/run.py judge WORK_DIR     # hidden test + score per task

Writes direct.jsonl and ccc.jsonl (cases with tokens_total and resolved) to OUT_DIR, ready
for `ccc-mcp compare-tokens` and `ccc-mcp validate-scoring`.
"""

import argparse
import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tests"))

from toybench import TASKS  # noqa: E402

from ccc_mcp.adapters.agents.base import native_executable  # noqa: E402
from ccc_mcp.infra.codex_account import read_account_usage  # noqa: E402
from ccc_mcp.infra.telemetry import usage_from_output  # noqa: E402
from ccc_mcp.service import CCCMCP  # noqa: E402
from ccc_mcp.worker import run_execution  # noqa: E402

ISSUES = {
    "clamp": "clamp(x, lo, hi) in mod.py should keep x within the range [lo, hi], but values above hi are not limited. Fix it.",
    "slugify": "slugify in mod.py should return a lowercase slug with words joined by single hyphens. Extra spaces currently produce extra hyphens. Fix it.",
    "average": "average in mod.py crashes on an empty list. It should return None for an empty list. Fix it.",
    "first_word": "first_word in mod.py should return the first word of the string, or an empty string if there is none. It misbehaves with leading spaces and empty input. Fix it.",
    "parse_bool": "parse_bool in mod.py should treat 'true', '1' and 'yes' (any letter case, surrounding whitespace ignored) as True and everything else as False. Fix it.",
    "dedupe": "dedupe in mod.py should remove duplicates while keeping the order in which items first appear. Fix it.",
}


def git(repo, *args, capture=False):
    done = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-C", str(repo), *args],
        check=True, capture_output=True, text=True,
    )
    return done.stdout if capture else None


def pytest(repo, *paths):
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *paths],
        cwd=repo, capture_output=True, text=True, check=False,
    )


def make_repo(base, name, label):
    spec = TASKS[name]
    repo = Path(base) / f"{name}-{label}"
    (repo / "tests").mkdir(parents=True)
    (repo / "conftest.py").write_text("import sys\nsys.path.insert(0, '.')\n")
    (repo / "mod.py").write_text(spec["buggy"])
    (repo / "tests" / "test_visible.py").write_text(spec["visible"])
    git(repo, "init", "-q")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    return repo


def single_module(name):
    return f"{name}_mod"


def judge(repo, name, sub=None):
    """Diff, the host-visible test run, and the hidden-test verdict, in that order, so the
    hidden file is never present while the agent works. `sub` is the task's folder inside
    a single combined repository (None for one repository per task)."""
    git(repo, "add", "-A")
    diff = git(repo, "diff", "--cached", "HEAD", "--", sub or ".", capture=True)
    tests = f"{sub}/tests" if sub else "tests"
    local = pytest(repo, tests)
    hidden = Path(repo) / tests / (f"test_{name}_hidden.py" if sub else "test_hidden.py")
    text = TASKS[name]["hidden"]
    hidden.write_text(text.replace("from mod import", f"from {single_module(name)} import") if sub else text)
    try:
        resolved = pytest(repo, str(hidden.relative_to(repo))).returncode == 0
    finally:
        hidden.unlink(missing_ok=True)
    return diff, local, resolved


def case_from(name, label, repo, tokens, extra=None, sub=None):
    diff, local, resolved = judge(repo, name, sub)
    total = None
    if tokens:
        total = tokens.get("input_tokens", 0) + tokens.get("output_tokens", 0)
    case = {
        "instance_id": name,
        "resolved": resolved,
        "role": "SD/RD",
        "diff": diff,
        "test_result": {"executed_by_host": True, "passed": local.returncode == 0, "stdout": local.stdout},
        "report": {"summary": f"Fixed {name}", "next_action": "", "findings": []},
        "acceptance": [f"{name} behaves as the issue describes"],
        "static_findings": [] if diff else None,  # production has no lint target without a diff
        "tokens": tokens or {},
        "tokens_total": total,
        "usage_complete": total is not None,
        "arm": label,
    }
    case.update(extra or {})
    return case


def run_direct(base, name):
    repo = make_repo(base, name, "direct")
    prompt = ISSUES[name] + "\nMake the change in this repository. The existing tests should keep passing."
    started = time.time()
    done = subprocess.run(
        [native_executable("codex"), "-a", "never", "exec", "--ignore-user-config",
         "--skip-git-repo-check", "--sandbox", "workspace-write", "--json", "--cd", str(repo), "-"],
        input=prompt, capture_output=True, text=True, timeout=900, check=False,
    )
    usage = usage_from_output("codex", done.stdout)
    case = case_from(name, "direct", repo, usage.get("tokens") if usage["available"] else None)
    case["seconds"] = round(time.time() - started)
    return case


def pending_patch(bridge, execution_id):
    result = next(
        item for item in bridge.latest("demo")["snapshot"]["agent_results"]
        if item["execution_id"] == execution_id
    )
    return {
        "base_context_version": bridge.latest("demo")["version"],
        "changes": {"summary": f"PM reviewed {result['role']} evidence."},
        "source_execution_ids": [execution_id],
        "source_task_ids": [result["task_id"]],
        "source_document_refs": [result["source_document_ref"]],
        "update_reason": "PM verified the completed Agent result",
        "semantic_review_ack": True,
    }


def run_ccc(base, name, max_steps=8):
    os.environ["CCC_MCP_ENABLE_REAL_AGENTS"] = "1"
    repo = make_repo(base, name, "ccc")
    service = CCCMCP(Path(base) / f"{name}-home")
    service.spawn_worker = lambda execution_id: None
    service.register_project(repo, "demo", "toy benchmark")
    for role in ("SA", "SD/RD", "REVIEW"):
        service.configure_role("demo", role, "codex")
    service.configure_test_command("demo", [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"])
    service.authorize_project_local("demo")

    started = time.time()
    first = service.start_project_request("demo", ISSUES[name])
    task_id, execution_id = first["workstream"]["id"], first["sa_execution"]["execution_id"]
    run_execution(service, execution_id)
    instruction = "Approved plan: implement exactly the fix described in the issue."
    for _ in range(max_steps):
        step = service.advance_project("demo", pending_patch(service, execution_id), instruction)
        instruction = None
        following = step.get("next_execution")
        if not following:
            break
        execution_id = following["execution_id"]
        run_execution(service, execution_id)

    exported = service.export_scoring_cases("demo")
    usage = next((c for c in exported if c["task_id"] == task_id), {})
    tokens = usage.get("tokens") or None
    rounds = [r["role"] for r in service.get_task_result("demo", task_id)]
    case = case_from(
        name, "ccc-mcp", repo, tokens,
        {"rounds": rounds, "usage_complete": usage.get("usage_complete", False),
         "task_state": service.task("demo", task_id)["state"]},
    )
    if usage.get("tokens_total") is not None:
        case["tokens_total"] = usage["tokens_total"]
    case["seconds"] = round(time.time() - started)
    return case


def quota():
    data = asyncio.run(read_account_usage(20))
    return [
        {"window_mins": w["duration_mins"], "used_percent": w["used_percent"]}
        for w in data.get("rate_limits") or []
    ]


def prepare(folder):
    """Create one buggy repository per task, plus ISSUES.md with the text to give the
    agent. Use these as ccc-mcp projects (or hand them to any agent), then `judge`."""
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    lines = ["# Issues (give each one to the agent as the task)", ""]
    for name in TASKS:
        make_repo(folder, name, "task")
        lines += [f"## {name}  (project folder: {name}-task)", "", ISSUES[name], ""]
    (folder / "ISSUES.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"Prepared {len(TASKS)} repositories in {folder}; issue texts are in ISSUES.md")


def prepare_single(folder):
    """One git repository holding every task in its own folder (<name>/<name>_mod.py), so it
    can be registered as a single ccc-mcp project. Module names are unique per task so a
    plain `pytest` from the root runs all visible tests together."""
    folder = Path(folder)
    if folder.exists() and any(folder.iterdir()):
        sys.exit(f"{folder} is not empty; pick a new folder")
    folder.mkdir(parents=True)
    (folder / "conftest.py").write_text(
        "import sys\nfrom pathlib import Path\n\n"
        "for path in Path(__file__).parent.iterdir():\n"
        "    if (path / 'tests').is_dir():\n"
        "        sys.path.insert(0, str(path))\n"
    )
    lines = [
        "# Issues (one repository, one folder per task)",
        "",
        "Register this folder as ONE ccc-mcp project. Host test command: `python -m pytest -q`.",
        "Give the agent one issue at a time and keep changes inside that task's folder.",
        "",
    ]
    for name, spec in TASKS.items():
        module = single_module(name)
        (folder / name / "tests").mkdir(parents=True)
        (folder / name / f"{module}.py").write_text(spec["buggy"])
        (folder / name / "tests" / f"test_{name}_visible.py").write_text(
            spec["visible"].replace("from mod import", f"from {module} import")
        )
        issue = ISSUES[name].replace("mod.py", f"{name}/{module}.py")
        lines += [f"## {name}", "", issue, ""]
    (folder / "ISSUES.md").write_text("\n".join(lines), encoding="utf-8")
    git(folder, "init", "-q")
    git(folder, "add", "-A")
    git(folder, "commit", "-q", "-m", "base")
    print(f"Prepared one repository with {len(TASKS)} tasks in {folder}; issue texts are in ISSUES.md")


def judge_folder(folder):
    """After an agent has worked on the prepared repositories: run the hidden test and the
    rule-based scorer on whatever it left behind, and print one line per task."""
    from ccc_mcp.evaluation_engine import validation

    cases = []
    single = (Path(folder) / ".git").exists()
    for name in TASKS:
        if single:
            if (Path(folder) / name).is_dir():
                cases.append(case_from(name, "judged", Path(folder), None, sub=name))
            continue
        repo = Path(folder) / f"{name}-task"
        if not repo.is_dir():
            continue
        cases.append(case_from(name, "judged", repo, None))
    for case in cases:
        scored = validation.score_case(case)
        print(
            f"{case['instance_id']:<12} {'RESOLVED' if case['resolved'] else 'not resolved':<13} "
            f"score={scored['quality_score']} status={scored['task_status']}"
        )
    if len(cases) >= 2:
        print(json.dumps(validation.validate(cases)["verdict"]))
    out = Path(folder) / "cases.jsonl"
    out.write_text("\n".join(json.dumps(c, ensure_ascii=False) for c in cases) + "\n", encoding="utf-8")
    print("cases written to", out)


def main():
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="command", required=True)
    runner = sub.add_parser("run", help="Run Codex directly and through ccc-mcp (spends Codex quota)")
    runner.add_argument("tasks", nargs="*", default=list(TASKS))
    runner.add_argument("--out", type=Path, required=True)
    preparer = sub.add_parser("prepare", help="Create the buggy repositories for a manual run")
    preparer.add_argument("folder")
    preparer.add_argument("--single", action="store_true", help="One repository with a folder per task")
    sub.add_parser("judge", help="Score what an agent left in the prepared repositories").add_argument("folder")
    args = parser.parse_args()
    if args.command == "prepare":
        return prepare_single(args.folder) if args.single else prepare(args.folder)
    if args.command == "judge":
        return judge_folder(args.folder)
    args.out.mkdir(parents=True, exist_ok=True)
    print("quota before:", json.dumps(quota()), flush=True)
    with tempfile.TemporaryDirectory(prefix="ccc-toy-agents-") as base:
        for name in args.tasks:
            for label, run_one, target in (("direct", run_direct, "direct.jsonl"), ("ccc", run_ccc, "ccc.jsonl")):
                case = run_one(base, name)
                with open(args.out / target, "a", encoding="utf-8") as handle:
                    handle.write(json.dumps(case, ensure_ascii=False) + "\n")
                print(name, label, "resolved" if case["resolved"] else "NOT resolved",
                      "tokens", case["tokens_total"], f"{case['seconds']}s", flush=True)
    print("quota after:", json.dumps(quota()), flush=True)


if __name__ == "__main__":
    main()
