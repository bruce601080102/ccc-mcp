"""Freeze the internal pilot: SHA-256 manifest and integrity check. Offline only.

  python freeze.py write   record hashes of everything the pilot uses (writes pilot/FREEZE_MANIFEST.json)
  python freeze.py check   re-hash; fail on any changed, missing or unlisted file

Scope (the manifest lists every file here):
  materials/<case>/**        the source materials for the three cases (A, C, B_text.md, facts, keys, questions)
  pilot/tester_P1/**         what tester P1 receives
  pilot/tester_P2/**         what tester P2 receives
  pilot/rater_only/**        keys, scoring notes, assignment (never given to testers)
  score.py, build_pilot.py, freeze.py   the scoring and packet code

A passing check means the files are the same bytes as when the manifest was written. It does not
show that the materials are correct or understandable; that is the pilot's job.
"""

import hashlib
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
MANIFEST = HERE / "pilot" / "FREEZE_MANIFEST.json"
VERSION = "pilot-v1"
CASES = ["case1_notes", "case2_bookmarks", "case3_todos"]
SCRIPTS = ["score.py", "build_pilot.py", "freeze.py"]


def scope_files():
    """Relative POSIX paths (from userstudy/) of every file in scope, sorted."""
    files = set()
    for case in CASES:
        files.update(p for p in (HERE / "materials" / case).rglob("*") if p.is_file())
    for folder in ("tester_P1", "tester_P2", "rater_only"):
        files.update(p for p in (HERE / "pilot" / folder).rglob("*") if p.is_file())
    files.update(HERE / name for name in SCRIPTS)
    return sorted(p.relative_to(HERE).as_posix() for p in files)


def sha256(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def current_hashes():
    return {rel: sha256(HERE / rel) for rel in scope_files()}


def bundle_hash(hashes):
    """One digest over all the file hashes, so a single line can be quoted in a report."""
    text = "\n".join(f"{rel} {digest}" for rel, digest in sorted(hashes.items()))
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def write():
    hashes = current_hashes()
    manifest = {
        "version": VERSION,
        "bundle_sha256": bundle_hash(hashes),
        "file_count": len(hashes),
        "files": hashes,
    }
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {MANIFEST.relative_to(HERE)}: {VERSION}, {len(hashes)} files, bundle {manifest['bundle_sha256']}")


def check():
    if not MANIFEST.exists():
        return ["no FREEZE_MANIFEST.json: run `python freeze.py write` first"]
    recorded = json.loads(MANIFEST.read_text(encoding="utf-8"))["files"]
    now = current_hashes()
    problems = []
    for rel, digest in recorded.items():
        if rel not in now:
            problems.append(f"MISSING: {rel}")
        elif now[rel] != digest:
            problems.append(f"CHANGED: {rel}")
    for rel in now:
        if rel not in recorded:
            problems.append(f"UNLISTED (new since freeze): {rel}")
    return problems


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "check"
    if mode == "write":
        write()
    elif mode == "check":
        problems = check()
        for line in problems:
            print("FAIL:", line)
        print("FREEZE CHECK PASSED" if not problems else f"{len(problems)} FREEZE PROBLEM(S)")
        sys.exit(1 if problems else 0)
    else:
        print("usage: python freeze.py [write|check]")
        sys.exit(2)
