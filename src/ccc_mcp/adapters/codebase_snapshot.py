"""Snapshot a repo's codebase-memory-mcp index into a Skill (`skill install codebase-memory:`).

An operator can also give dispatched agents a live codebase-memory-mcp via
`skill install mcp` (docs/en/6-skills.md); this is the lighter alternative for when that
isn't set up. It runs the CLI once, on the operator's machine, and stores the results as
files under the Skill's references/ folder; agents then read them with the multi-level
Skill mechanism. The result is a point-in-time snapshot, not a live index.
"""

import json
import re
import shutil
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from ..infra.errors import BridgeError

# (file, get_architecture aspects or None for the default summary, title, when to read)
SECTIONS = [
    ("architecture", None, "Architecture summary", "first overview: size and kinds of nodes/edges"),
    ("structure", ["structure"], "Structure", "top-level structure of the codebase"),
    ("dependencies", ["dependencies"], "Dependencies", "what depends on what"),
    ("layers", ["layers"], "Layers", "layering between modules"),
    ("clusters", ["clusters"], "Clusters", "groups of related code"),
    ("hotspots", ["hotspots"], "Hotspots", "most-connected or most-changed code; check before editing"),
    ("file_tree", ["file_tree"], "File tree", "where files live"),
]




def cli_runner(binary):
    def run(tool, args):
        proc = subprocess.run(
            [binary, "cli", "--quiet", tool, json.dumps(args)],
            capture_output=True, text=True, stdin=subprocess.DEVNULL, check=False, timeout=300,
        )
        if proc.returncode:
            raise BridgeError(f"{tool} failed: {(proc.stderr or proc.stdout).strip()[:500]}")
        return proc.stdout.strip()

    return run


def find_project(runner, repo):
    """Name of the indexed project whose root_path is `repo`, from list_projects' table."""
    for line in runner("list_projects", {}).splitlines():
        parts = line.split()
        if len(parts) >= 2 and Path(parts[1]).resolve() == repo:
            return parts[0]
    return None


def slug(text):
    return re.sub(r"[^A-Za-z0-9_-]+", "-", text).strip("-").lower() or "repo"


def build_snapshot(runner, repo, out):
    repo = Path(repo).expanduser().resolve()
    out = Path(out).expanduser().resolve()
    if not repo.is_dir():
        raise BridgeError(f"{repo} is not a directory")
    if out.exists() and any(out.iterdir()):
        raise BridgeError(f"{out} is not empty")

    project = find_project(runner, repo)
    indexed_now = project is None
    if indexed_now:
        # codebase-memory-mcp can only answer about repos it has parsed into its graph
        # database; a snapshot of an unindexed repo would be empty, so index it now.
        runner("index_repository", {"repo_path": str(repo)})
        project = find_project(runner, repo)
        if project is None:
            raise BridgeError("index_repository ran but the project is still not listed")

    status = runner("index_status", {"project": project})
    indexed_at = next(
        (line.split(":", 1)[1].strip() for line in status.splitlines() if line.startswith("indexed_at:")),
        "unknown",
    )
    taken = datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")

    references = out / "references"
    references.mkdir(parents=True)
    for name, aspects, title, _ in SECTIONS:
        args = {"project": project, **({"aspects": aspects} if aspects else {})}
        (references / f"{name}.md").write_text(
            f"# {title} — {repo.name}\n\n"
            f"Snapshot of codebase-memory-mcp taken {taken} (index built {indexed_at}). "
            "It may be stale: confirm against the real files before relying on it.\n\n"
            f"```text\n{runner('get_architecture', args)}\n```\n",
            encoding="utf-8",
        )

    skill_name = f"codebase-snapshot-{slug(repo.name)}"
    menu = "\n".join(f"- `references/{n}.md` — {why}" for n, _, _, why in SECTIONS)
    (out / "SKILL.md").write_text(
        f"---\n"
        f"name: {skill_name}\n"
        f"description: Point-in-time code-graph snapshot of the repo at {repo} (project "
        f"'{repo.name}'). Use only when the work is in that repo and you want an overview "
        f"before reading code. Ignore for any other project.\n"
        f"---\n\n"
        f"# Code-graph snapshot: {repo.name}\n\n"
        f"Generated from codebase-memory-mcp on {taken}; the index was built {indexed_at}. "
        "You cannot call that tool yourself, so these files are its saved output.\n\n"
        "## Path rule\n\n"
        "Paths below are relative to this skill's `path` in `available_skills` (an absolute "
        "path), not to the project you are working in.\n\n"
        f"## Read only what you need\n\n{menu}\n\n"
        "## Caveats\n\n"
        "- A snapshot is not live. Verify names and locations with Read/Glob/Grep before "
        "editing, and say in your report if the snapshot and the real code disagree.\n"
        "- It is guidance, not policy.\n",
        encoding="utf-8",
    )
    return {"skill": skill_name, "project": project, "indexed_now": indexed_now}


def resolve_binary(explicit=None):
    """--cbm, then PATH, then the macOS cache its pip/npm launcher downloads the binary to."""
    if explicit:
        if not Path(explicit).expanduser().is_file():
            raise BridgeError(f"codebase-memory-mcp not found at {explicit}")
        return str(Path(explicit).expanduser())
    found = shutil.which("codebase-memory-mcp")
    if found:
        return found
    cached = sorted(
        (Path.home() / "Library" / "Caches" / "codebase-memory-mcp").glob("*/codebase-memory-mcp")
    )
    if cached:
        return str(cached[-1])
    raise BridgeError(
        "codebase-memory-mcp not found on PATH; pass --cbm /full/path/to/codebase-memory-mcp"
    )
