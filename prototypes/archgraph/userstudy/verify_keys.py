"""Verify every answer key against independent evidence: git, the approved plan, the host test
re-run, and import statements. None of these checks reads the ccc-mcp TaskDelta to decide a key,
so a bug in the TaskDelta cannot hide itself here.

Usage: python verify_keys.py <scratch-dir-with-cases> [--no-tests]

Exit code 0 only if every check passes for every case.
"""

import json
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
MATERIALS = HERE / "materials"
CASES = ["case1_notes", "case2_bookmarks", "case3_todos"]
PLANS = HERE.parent / "fixtures" / "notes_cli" / "plans"
PLAN_FILE = {"TASK-0001": "plan1.json", "TASK-0002": "plan2.json", "TASK-0003": "plan3.json"}
COMMIT_KEYS = {"TASK-0002": "C2"}


def git(repo, *args):
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, check=True)
    return result.stdout.decode("utf-8", "replace")


def git_name_status(repo, task, refs):
    """Git's own view of what changed in each Task (not the TaskDelta)."""
    if task == "TASK-0001":
        out = git(repo, "diff-tree", "--root", "-r", "--name-status", "--no-commit-id", refs["C1"])
    elif task == "TASK-0002":
        out = git(repo, "diff", "--name-status", refs["C1"], refs["C2"])
    else:
        out = git(repo, "status", "--porcelain=v1", "--untracked-files=all")
        rows = {}
        for line in out.splitlines():
            code, path = line[:2], line[3:]
            if "D" in code:
                rows[path] = "D"
            elif code.strip() == "??" or "A" in code:
                rows[path] = "A"
            else:
                rows[path] = "M"
        return rows
    rows = {}
    for line in out.splitlines():
        parts = line.split("\t")
        rows[parts[-1]] = parts[0][0]
    return rows


def feature_expectation(plan, changed):
    """ADD when a member file is new and the feature was not renamed; MODIFY when any member
    file changed; otherwise UNCHANGED. Uses only git's name-status and the approved plan."""
    expected = {}
    for feature in plan["features"]:
        files = {m.split("::")[0] for m in feature["members"]}
        kinds = {changed[f] for f in files if f in changed}
        if "A" in kinds and not feature.get("renamed_from"):
            verdict = "ADD"
        elif kinds:
            verdict = "MODIFY"
        else:
            verdict = "UNCHANGED"
        expected[feature["title_plain"]] = verdict
    return expected


def run_tests_in_copy(repo, task, refs, excluded):
    """Re-run the Task's host test command in a copy of the same scope and return its summary."""
    scratch = Path(tempfile.mkdtemp(prefix="verify-keys-"))
    try:
        target = scratch / "tree"
        if task == "TASK-0003":
            def skip(directory, names):
                base = Path(directory)
                return [
                    n for n in names
                    if n == ".git" or (base / n).relative_to(repo).as_posix() in excluded
                ]

            shutil.copytree(repo, target, ignore=skip)
        else:
            git_ref = refs["C1"] if task == "TASK-0001" else refs["C2"]
            target.mkdir()
            archive = subprocess.run(
                ["git", "-C", str(repo), "archive", git_ref], capture_output=True, check=True
            ).stdout
            subprocess.run(["tar", "-x", "-C", str(target)], input=archive, check=True)
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider"],
            cwd=target,
            capture_output=True,
            text=True,
            env={"PYTHONDONTWRITEBYTECODE": "1", "PATH": "/usr/bin:/bin"},
            timeout=180,
            check=False,
        )
        lines = [line for line in proc.stdout.splitlines() if line.strip()]
        return ("PASSED" if proc.returncode == 0 else "FAILED"), (lines[-1] if lines else "")
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


def verify_case(case, scratch, run_tests):
    case_dir = scratch / case
    refs = json.loads((case_dir / "repo.refs.json").read_text(encoding="utf-8"))
    repo = case_dir / "repo"
    keys = json.loads((MATERIALS / case / "keys.json").read_text(encoding="utf-8"))
    facts = json.loads((MATERIALS / case / "facts.json").read_text(encoding="utf-8"))
    results = []

    def check(name, ok, detail=""):
        results.append((name, bool(ok), detail))

    excluded = {e["path"] for t in facts["tasks"].values() for e in t["excluded"]}
    for task in ("TASK-0001", "TASK-0002", "TASK-0003"):
        # The case's own (word-replaced) plan, as the pipeline received it.
        plan = json.loads((case_dir / "plans" / PLAN_FILE[task]).read_text(encoding="utf-8"))
        changed = {p: k for p, k in git_name_status(repo, task, refs).items() if p not in excluded}
        delta_paths = {c["path"] for c in facts["tasks"][task]["changed_files"]}
        check(
            f"{task} changed files equal git's view",
            delta_paths == set(changed),
            f"git={sorted(changed)} facts={sorted(delta_paths)}",
        )
        if task == "TASK-0001":
            check("TASK-0001 every feature is ADD (base is empty)", all(
                v == "ADD" for v in feature_expectation(plan, changed).values()
            ))
        if task == "TASK-0002":
            expected = feature_expectation(plan, changed)
            check(
                "TASK-0002 ADD set equals git-derived ADD set",
                sorted(k for k, v in expected.items() if v == "ADD") == keys["T2_added"],
                f"git={sorted(k for k, v in expected.items() if v == 'ADD')}",
            )
            check(
                "TASK-0002 MODIFY set equals git-derived MODIFY set",
                sorted(k for k, v in expected.items() if v == "MODIFY") == keys["T2_modified"],
                f"git={sorted(k for k, v in expected.items() if v == 'MODIFY')}",
            )
        if task == "TASK-0003":
            expected = feature_expectation(plan, changed)
            check(
                "TASK-0003 ADD set equals git-derived ADD set",
                sorted(k for k, v in expected.items() if v == "ADD") == keys["T3_added"],
                f"git={sorted(k for k, v in expected.items() if v == 'ADD')}",
            )
            check(
                "TASK-0003 MODIFY set equals git-derived MODIFY set",
                sorted(k for k, v in expected.items() if v == "MODIFY") == keys["T3_modified"],
                f"git={sorted(k for k, v in expected.items() if v == 'MODIFY')}",
            )
            planned = set(plan["files"].get("add", []) + plan["files"].get("modify", []) + plan["files"].get("delete", []) + plan["files"].get("affected", []))
            out_of_plan = sorted(p for p in changed if p not in planned)
            check(
                "TASK-0003 out-of-plan files equal git minus plan",
                out_of_plan == keys["O1_out_of_plan_files"],
                f"git={out_of_plan}",
            )
            search_impl = keys["G2_search_implementation_file"]
            text = (repo / search_impl).read_text(encoding="utf-8") if (repo / search_impl).exists() else ""
            check("G2 implementation file defines the search function", re.search(r"def search_", text) is not None, search_impl)
            dependents = set()
            for path in repo.rglob("*.py"):
                rel = path.relative_to(repo).as_posix()
                if rel.startswith(".git"):
                    continue
                module = search_impl[:-3].replace("/", ".")
                pattern = re.compile(rf"^\s*from\s+(?:\.|{re.escape(module.split('.')[0])}\.)?(?:[\w.]*\.)?search\s+import", re.M)
                if pattern.search(path.read_text(encoding="utf-8", errors="replace")):
                    dependents.add(rel)
            check(
                "I1 dependents equal import evidence in the worktree",
                sorted(dependents) == keys["I1_dependents_of_search"],
                f"import evidence={sorted(dependents)}",
            )
        if run_tests:
            status, summary = run_tests_in_copy(repo, task, refs, excluded)
            recorded = facts["tasks"][task]["host_test"]["state"]
            check(f"{task} host test re-run agrees with recorded state", status == recorded, f"re-run={status} {summary}")
            check(f"{task} re-run summary reports passing tests", " passed" in summary and "failed" not in summary, summary)
    gate_status = None
    t3 = json.loads((scratch / case / "TASK-0003" / "architecture.json").read_text(encoding="utf-8"))
    gate_status = t3["alerts"]["gate"]
    blocker_codes = {b["code"] for b in gate_status["blockers"]}
    check(
        "E1 blocking reasons match the gate blockers",
        ("OUT_OF_PLAN" in blocker_codes) == ("超出計畫" in keys["E1_ready_to_merge"]["blocking_reasons"])
        and ("PENDING_IDENTITY" in blocker_codes) == ("待確認" in keys["E1_ready_to_merge"]["blocking_reasons"]),
        str(sorted(blocker_codes)),
    )
    check("E1 answer is NO because the gate is BLOCKED", gate_status["status"] == "BLOCKED" and keys["E1_ready_to_merge"]["answer"] == "NO")
    check(
        "E1 pending action is the commit, and it is not a blocker",
        [a["code"] for a in gate_status["actions"]] == ["COMMIT_REQUIRED"] and "COMMIT_REQUIRED" not in blocker_codes,
    )
    return results


def main():
    scratch = Path(sys.argv[1]).resolve()
    run_tests = "--no-tests" not in sys.argv
    failed = 0
    for case in CASES:
        print(f"== {case}")
        for name, ok, detail in verify_case(case, scratch, run_tests):
            print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  ({detail})" if (detail and not ok) else ""))
            failed += 0 if ok else 1
    print(f"\n{'ALL KEYS VERIFIED' if failed == 0 else f'{failed} CHECK(S) FAILED'}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
