"""A tiny SWE-bench-shaped benchmark that needs no Docker and no LLM.

Each task is a one-function module with a visible test the "agent" can see and a hidden
test it cannot; a candidate patch counts as resolved only if the hidden test passes.
Patches are hand-written, so this checks whether the scorer's *rules* rank good patches
above bad ones. It says nothing about which mistakes real agents make.

Five patch kinds per task:
  good        correct fix, no new test (visible tests only)
  good_tested correct fix plus a new test of its own
  overfit     passes the visible tests but is wrong for the hidden case, no new test
  selftest    same wrong fix, plus a new test the wrong fix happens to pass
  broken      breaks a visible test
"""

import subprocess
import sys
from pathlib import Path

TASKS = {
    "clamp": dict(
        buggy="def clamp(x, lo, hi):\n    return max(x, lo)\n",
        fixed="def clamp(x, lo, hi):\n    return max(lo, min(x, hi))\n",
        partial="def clamp(x, lo, hi):\n    return max(lo, x) if x <= hi else hi if hi > 0 else x\n",
        broken="def clamp(x, lo, hi):\n    return lo\n",
        visible="from mod import clamp\n\n\ndef test_low():\n    assert clamp(-3, 0, 10) == 0\n\n\ndef test_inside():\n    assert clamp(4, 0, 10) == 4\n",
        hidden="from mod import clamp\n\n\ndef test_hidden():\n    assert clamp(15, 0, 10) == 10\n    assert clamp(5, -10, -1) == -1\n",
        own_good="from mod import clamp\n\n\ndef test_upper():\n    assert clamp(15, 0, 10) == 10\n",
        own_partial="from mod import clamp\n\n\ndef test_upper():\n    assert clamp(15, 0, 10) == 10\n",
    ),
    "slugify": dict(
        buggy='def slugify(text):\n    return text.lower().replace(" ", "-")\n',
        fixed='def slugify(text):\n    return "-".join(text.lower().split())\n',
        partial='def slugify(text):\n    return "-".join(text.lower().split(" "))\n',
        broken="def slugify(text):\n    return text\n",
        visible='from mod import slugify\n\n\ndef test_basic():\n    assert slugify("Hello World") == "hello-world"\n',
        hidden='from mod import slugify\n\n\ndef test_hidden():\n    assert slugify("a  b") == "a-b"\n    assert slugify(" a b ") == "a-b"\n',
        own_good='from mod import slugify\n\n\ndef test_spaces():\n    assert slugify("a  b") == "a-b"\n',
        own_partial='from mod import slugify\n\n\ndef test_two_words():\n    assert slugify("A B") == "a-b"\n',
    ),
    "average": dict(
        buggy="def average(nums):\n    return sum(nums) / len(nums)\n",
        fixed="def average(nums):\n    return sum(nums) / len(nums) if nums else None\n",
        partial="def average(nums):\n    try:\n        return sum(nums) / len(nums)\n    except ZeroDivisionError:\n        return 0\n",
        broken="def average(nums):\n    return sum(nums)\n",
        visible="from mod import average\n\n\ndef test_basic():\n    assert average([1, 2, 3]) == 2\n",
        hidden="from mod import average\n\n\ndef test_hidden():\n    assert average([]) is None\n",
        own_good="from mod import average\n\n\ndef test_empty():\n    assert average([]) is None\n",
        own_partial="from mod import average\n\n\ndef test_empty():\n    assert average([]) == 0\n",
    ),
    "first_word": dict(
        buggy='def first_word(s):\n    return s.split(" ")[0]\n',
        fixed='def first_word(s):\n    parts = s.split()\n    return parts[0] if parts else ""\n',
        partial='def first_word(s):\n    return s.split(" ")[0] if s else ""\n',
        broken="def first_word(s):\n    return s\n",
        visible='from mod import first_word\n\n\ndef test_basic():\n    assert first_word("hi there") == "hi"\n',
        hidden='from mod import first_word\n\n\ndef test_hidden():\n    assert first_word("") == ""\n    assert first_word("  hi there") == "hi"\n',
        own_good='from mod import first_word\n\n\ndef test_leading_space():\n    assert first_word("  hi") == "hi"\n',
        own_partial='from mod import first_word\n\n\ndef test_empty():\n    assert first_word("") == ""\n',
    ),
    "parse_bool": dict(
        buggy='def parse_bool(s):\n    return s == "true"\n',
        fixed='def parse_bool(s):\n    return s.strip().lower() in {"true", "1", "yes"}\n',
        partial='def parse_bool(s):\n    return s.lower() == "true"\n',
        broken="def parse_bool(s):\n    return True\n",
        visible='from mod import parse_bool\n\n\ndef test_basic():\n    assert parse_bool("true") is True\n    assert parse_bool("false") is False\n',
        hidden='from mod import parse_bool\n\n\ndef test_hidden():\n    assert parse_bool(" Yes ") is True\n    assert parse_bool("no") is False\n',
        own_good='from mod import parse_bool\n\n\ndef test_one():\n    assert parse_bool("1") is True\n',
        own_partial='from mod import parse_bool\n\n\ndef test_upper():\n    assert parse_bool("TRUE") is True\n',
    ),
    "dedupe": dict(
        buggy="def dedupe(items):\n    return list(set(items))\n",
        fixed="def dedupe(items):\n    return list(dict.fromkeys(items))\n",
        partial="def dedupe(items):\n    return sorted(set(items))\n",
        broken="def dedupe(items):\n    return items\n",
        visible="from mod import dedupe\n\n\ndef test_basic():\n    assert dedupe([1, 1]) == [1]\n",
        hidden="from mod import dedupe\n\n\ndef test_hidden():\n    assert dedupe([3, 1, 3, 2, 1]) == [3, 1, 2]\n",
        own_good="from mod import dedupe\n\n\ndef test_order():\n    assert dedupe([3, 1, 3]) == [3, 1]\n",
        own_partial="from mod import dedupe\n\n\ndef test_sorted_equal():\n    assert sorted(dedupe([2, 2, 1])) == [1, 2]\n",
    ),
}

# kind -> (which source to write, which new test to add or None, resolved?)
KINDS = {
    "good": ("fixed", None, True),
    "good_tested": ("fixed", "own_good", True),
    "overfit": ("partial", None, False),
    "selftest": ("partial", "own_partial", False),
    "broken": ("broken", None, False),
}


def _git(repo, *args):
    subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.invalid", "-C", str(repo), *args],
        check=True, capture_output=True,
    )


def _pytest(repo, *paths):
    return subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *paths],
        cwd=repo, capture_output=True, text=True, check=False,
    )


def build_case(workdir, name, kind):
    spec = TASKS[name]
    source_key, new_test, resolved = KINDS[kind]
    repo = Path(workdir) / f"{name}-{kind}"
    (repo / "tests").mkdir(parents=True)
    (repo / "conftest.py").write_text("import sys\nsys.path.insert(0, '.')\n")
    (repo / "mod.py").write_text(spec["buggy"])
    (repo / "tests" / "test_visible.py").write_text(spec["visible"])
    _git(repo, "init", "-q")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "base")

    (repo / "mod.py").write_text(spec[source_key])
    if new_test:
        (repo / "tests" / "test_own.py").write_text(spec[new_test])
    _git(repo, "add", "-A")
    diff = subprocess.run(
        ["git", "-C", str(repo), "diff", "--cached", "HEAD"], check=True, capture_output=True, text=True
    ).stdout
    local = _pytest(repo, "tests")
    (repo / "tests" / "test_hidden.py").write_text(spec["hidden"])
    hidden_passed = _pytest(repo, "tests/test_hidden.py").returncode == 0
    assert hidden_passed == resolved, f"{name}/{kind}: hidden-test outcome differs from its label"
    return {
        "instance_id": f"{name}-{kind}",
        "resolved": hidden_passed,
        "role": "SD/RD",
        "diff": diff,
        "test_result": {
            "executed_by_host": True, "passed": local.returncode == 0, "stdout": local.stdout,
        },
        "report": {"summary": f"Fixed {name} edge cases", "next_action": "", "findings": []},
        "acceptance": [f"{name} handles the reported edge cases"],
        "static_findings": [],
    }


def build_cases(workdir):
    return [build_case(workdir, name, kind) for name in TASKS for kind in KINDS]


if __name__ == "__main__":
    import json
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        for case in build_cases(tmp):
            print(json.dumps(case))
