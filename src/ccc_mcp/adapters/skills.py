"""Local Skill install/list/remove: a SKILL.md (+ optional files) that gets
embedded into every dispatch's handoff package, so Codex, Claude Code and the
interactive ChatGPT PM path all see the same operator-installed guidance and
decide for themselves whether it's relevant.

Skills are global (~/.ccc-mcp/skills/<name>/), not per-project.
"""

import re
import shutil
import tempfile
import urllib.request
import zipfile
from pathlib import Path

from ..infra.errors import BridgeError
from ..infra.security import safe_path

FRONTMATTER = re.compile(r"\A---\s*\n(.*?\n)---\s*\n?", re.DOTALL)
IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}")


def identifier(value: str) -> str:
    if not IDENTIFIER.fullmatch(value):
        raise BridgeError("Identifier must contain only letters, digits, underscore or hyphen")
    return value


def _parse_skill_md(text: str) -> tuple[dict, str]:
    match = FRONTMATTER.match(text)
    if not match:
        return {}, text.strip()
    meta = {}
    for line in match.group(1).splitlines():
        if ":" in line:
            key, _, value = line.partition(":")
            meta[key.strip()] = value.strip()
    return meta, text[match.end():].strip()


def _skills_dir(home: Path) -> Path:
    return Path(home) / "skills"


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "-", text).strip("-") or "skill"


def _write_skill(home: Path, name: str, skill_md_text: str, extra_files: dict[str, bytes]) -> dict:
    meta, body = _parse_skill_md(skill_md_text)
    skill_id = identifier(_slug(meta.get("name", name)))
    skill_dir = _skills_dir(home) / skill_id
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(skill_md_text, encoding="utf-8")
    for relative, content in extra_files.items():
        target = safe_path(skill_dir, relative)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    return {
        "id": skill_id,
        "name": skill_id,
        "description": meta.get("description", ""),
        "path": str(skill_dir),
    }


def _install_from_path(home: Path, source: Path) -> dict:
    skill_md = source / "SKILL.md"
    if not skill_md.is_file():
        raise BridgeError(f"{source} does not contain a SKILL.md")
    extra_files = {}
    for path in source.rglob("*"):
        if path.is_file() and path != skill_md:
            extra_files[path.relative_to(source).as_posix()] = path.read_bytes()
    return _write_skill(home, source.name, skill_md.read_text(encoding="utf-8"), extra_files)


def _download(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=120) as response:
        return response.read()


def _install_from_url(home: Path, url: str) -> dict:
    name = url.split("?")[0].rsplit("/", 1)[-1]
    if url.lower().endswith(".md"):
        content = _download(url).decode("utf-8")
        return _write_skill(home, Path(name).stem, content, {})
    if url.lower().endswith(".zip"):
        archive = _download(url)
        with tempfile.TemporaryDirectory() as tmp:
            archive_path = Path(tmp) / "skill.zip"
            archive_path.write_bytes(archive)
            extracted = Path(tmp) / "extracted"
            extracted.mkdir()
            with zipfile.ZipFile(archive_path) as zf:
                for entry in zf.infolist():
                    if entry.is_dir():
                        continue
                    target = safe_path(extracted, entry.filename)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(zf.read(entry))
            skill_md = next(extracted.rglob("SKILL.md"), None)
            if skill_md is None:
                raise BridgeError("Downloaded archive does not contain a SKILL.md")
            return _install_from_path(home, skill_md.parent)
    raise BridgeError("Skill URL must end in .md (single file) or .zip (archive)")


SNAPSHOT_PREFIX = "codebase-memory:"


def _install_codebase_snapshot(home, source, repo_root, cbm):
    from .codebase_snapshot import build_snapshot, cli_runner, resolve_binary

    target = source[len(SNAPSHOT_PREFIX):].strip() or repo_root
    if not target:
        raise BridgeError(
            "Give a repo path (codebase-memory:/path/to/repo) or select a project with --project"
        )
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "snapshot"
        built = build_snapshot(cli_runner(resolve_binary(cbm)), target, out)
        return {**_install_from_path(home, out), "indexed_now": built["indexed_now"]}


def install_skill(
    home: Path, source: str, *, repo_root: str | None = None, cbm: str | None = None
) -> dict:
    if source.startswith(SNAPSHOT_PREFIX):
        return _install_codebase_snapshot(home, source, repo_root, cbm)
    if source.startswith(("http://", "https://")):
        return _install_from_url(home, source)
    path = Path(source).expanduser()
    if not path.is_dir():
        raise BridgeError(f"{source} is not a directory")
    return _install_from_path(home, path)


def list_skills(home: Path) -> list[dict]:
    skills_dir = _skills_dir(home)
    if not skills_dir.is_dir():
        return []
    results = []
    for entry in sorted(skills_dir.iterdir()):
        skill_md = entry / "SKILL.md"
        if entry.is_dir() and skill_md.is_file():
            meta, _ = _parse_skill_md(skill_md.read_text(encoding="utf-8"))
            results.append(
                {
                    "id": entry.name,
                    "name": meta.get("name", entry.name),
                    "description": meta.get("description", ""),
                    "path": str(entry),
                }
            )
    return results


def remove_skill(home: Path, skill_id: str) -> dict:
    skill_dir = _skills_dir(home) / identifier(skill_id)
    if not skill_dir.is_dir():
        raise BridgeError(f"Skill not installed: {skill_id}")
    shutil.rmtree(skill_dir)
    return {"id": skill_id, "removed": True}


def available_skills(home: Path) -> list[dict]:
    """id/name/description/content for every installed skill, for embedding in a handoff package."""
    results = []
    for skill in list_skills(home):
        _, body = _parse_skill_md((Path(skill["path"]) / "SKILL.md").read_text(encoding="utf-8"))
        results.append({**skill, "content": body})
    return results


def _provider_skill_roots() -> list[tuple[str, Path]]:
    """Global skill directories other tools install into: `~/.claude/skills/<id>/SKILL.md`
    for Claude Code, `~/.codex/skills/<id>/SKILL.md` for Codex. Both follow the same Agent
    Skills convention ccc-mcp's own store uses, so a discovered skill's SKILL.md can be
    installed into ccc-mcp unchanged via `install_skill(home, <its directory>)`."""
    home = Path.home()
    return [
        ("Claude Code", home / ".claude" / "skills"),
        ("Codex", home / ".codex" / "skills"),
    ]


def discover_skills(home: Path) -> list[dict]:
    """Skills already installed on this machine for Claude Code / Codex, outside ccc-mcp's
    own store (for example by a third-party installer run as `npx <tool> init --global`).

    ccc-mcp cannot see these automatically: a dispatched Claude Code/Codex CLI run is locked
    down (no --setting-sources, slash commands disabled) and never reads either directory, so
    a skill only reaches an agent once an operator installs it into ccc-mcp's own store. This
    lists what is findable so that decision can be made from the UI/CLI instead of by hand.

    Each entry reports `usable` (a readable SKILL.md with real content — the only thing that
    determines whether ccc-mcp *can* install it; which provider it came from doesn't matter,
    since the content is copied as-is) and `already_installed` (same id already in ccc-mcp's
    store). Entries sharing one resolved SKILL.md path (installed for both providers at once,
    as `--providers=claude,codex` tools do) are reported once.
    """
    installed_ids = {skill["id"] for skill in list_skills(home)}
    found = []
    seen_paths = set()
    for provider, skills_dir in _provider_skill_roots():
        if not skills_dir.is_dir():
            continue
        for entry in sorted(skills_dir.iterdir()):
            if not entry.is_dir() or entry.name.startswith("."):
                continue
            skill_md = entry / "SKILL.md"
            if not skill_md.is_file():
                found.append(
                    {
                        "id": _slug(entry.name), "name": entry.name, "description": "",
                        "provider": provider, "path": str(entry),
                        "usable": False, "reason": "No SKILL.md in this directory",
                        "already_installed": False,
                    }
                )
                continue
            resolved = skill_md.resolve()
            if resolved in seen_paths:
                continue
            seen_paths.add(resolved)
            try:
                text = skill_md.read_text(encoding="utf-8")
            except OSError as exc:
                found.append(
                    {
                        "id": _slug(entry.name), "name": entry.name, "description": "",
                        "provider": provider, "path": str(entry),
                        "usable": False, "reason": f"Could not read SKILL.md: {exc}",
                        "already_installed": False,
                    }
                )
                continue
            meta, body = _parse_skill_md(text)
            skill_id = _slug(meta.get("name", entry.name))
            usable = bool(body.strip())
            found.append(
                {
                    "id": skill_id,
                    "name": meta.get("name", entry.name),
                    "description": meta.get("description", ""),
                    "provider": provider,
                    "path": str(entry),
                    "usable": usable,
                    "reason": None if usable else "SKILL.md has no content below its frontmatter",
                    "already_installed": skill_id in installed_ids,
                }
            )
    return found
