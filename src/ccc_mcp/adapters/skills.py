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


def install_skill(home: Path, source: str) -> dict:
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
