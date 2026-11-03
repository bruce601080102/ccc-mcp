"""Unified-diff parsing shared by evidence collection and feature extraction. Pure
string processing — no git subprocess here; the diff text itself is already
host-captured evidence (see worker.py's CHANGES.diff, written from `git diff HEAD`
after the Agent's own turn, never from the Agent's self-report)."""

import re

_DIFF_HEADER = re.compile(r"^diff --git a/(.*) b/(.*)$")
_NEW_FILE = re.compile(r"^new file mode")
_TEST_DEF = re.compile(r"^\+\s*(?:async\s+)?def (test_\w+)\s*\(")
_TEST_PATH = re.compile(r"(^|/)test_[^/]+\.py$|_test\.py$")


def touched_files(diff_text):
    """Every path this diff touches (added, modified or deleted), in order of
    appearance. Used both for "what did this round touch" (feature_extractor's
    requirement/scope signals) and to pick which files to run static analysis on."""
    paths = []
    for line in (diff_text or "").splitlines():
        match = _DIFF_HEADER.match(line)
        if match:
            paths.append(match.group(2))
    return paths


def changed_python_files(diff_text):
    return [p for p in touched_files(diff_text) if p.endswith(".py")]


def per_file_hunks(diff_text):
    """Split a multi-file diff into {path: {"added": [...], "is_new_file": bool}} —
    just enough structure for the newly-added-test-function heuristic below; never a
    full patch parser."""
    files = {}
    current = None
    for line in (diff_text or "").splitlines():
        header = _DIFF_HEADER.match(line)
        if header:
            current = header.group(2)
            files[current] = {"added": [], "is_new_file": False}
            continue
        if current is None:
            continue
        if _NEW_FILE.match(line):
            files[current]["is_new_file"] = True
        elif line.startswith("+++") or line.startswith("---"):
            continue
        elif line.startswith("+"):
            files[current]["added"].append(line[1:])
    return files


def newly_added_test_names(diff_text):
    """Test function names this round's diff actually introduced (added lines only,
    in files that look like Python test files) — the closest honest proxy this
    project has for SWE-bench's FAIL_TO_PASS set, since ccc-mcp has no ground-truth
    ticket-level test oracle. See feature_extractor._fail_to_pass_rate for how this is
    combined with the host pytest run's own failing-test list."""
    names = set()
    for path, hunk in per_file_hunks(diff_text).items():
        if not _TEST_PATH.search(path):
            continue
        for added_line in hunk["added"]:
            match = _TEST_DEF.match("+" + added_line)
            if match:
                names.add(match.group(1))
    return names


def synthesize_untracked_diff(new_file_contents):
    """worker.py's `git diff HEAD` never shows brand-new, still-untracked files (git
    doesn't diff what isn't added yet) — it records them separately as
    extras["new_file_contents"] instead (see worker.py's `implementation` branch).
    Turning each into a synthetic "entirely added" diff block lets every other
    function in this module treat a new file exactly like a modified one, instead of
    every caller needing a second, parallel code path for "brand new" files.
    """
    blocks = []
    for path, content in (new_file_contents or {}).items():
        lines = content.splitlines() or [""]
        blocks.append(
            "\n".join(
                [
                    f"diff --git a/{path} b/{path}",
                    "new file mode 100644",
                    "--- /dev/null",
                    f"+++ b/{path}",
                    f"@@ -0,0 +1,{len(lines)} @@",
                    *[f"+{line}" for line in lines],
                    "",
                ]
            )
        )
    return "\n".join(blocks)


def has_pre_existing_passing_test_only(diff_text):
    """True when every touched test file is itself new this round (no pre-existing
    test file was modified) — i.e. nothing here could have regression-tested
    anything that existed before. Mirrors the local-test-downweight signal from the
    astropy__astropy-13398 incident (see docs/design/scorer-role.md on the
    feature/score-role branch): a round whose ONLY passing evidence is tests it wrote
    itself has not actually been independently checked."""
    test_files = {path: hunk for path, hunk in per_file_hunks(diff_text).items() if _TEST_PATH.search(path)}
    if not test_files:
        return False
    return all(hunk["is_new_file"] for hunk in test_files.values())
