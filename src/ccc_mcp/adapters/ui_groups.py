"""Local console-only project organization; never changes registered projects."""

import json
import secrets
import threading
from pathlib import Path

from ..core.roles import AVATAR_IDS
from ..infra.errors import BridgeError
from ..infra.security import atomic_write

_lock = threading.RLock()

# How a project shows in the console's lists: a pixel character or a short text/emoji, in one
# of these colors. A project with none shows its first letter.
ICON_TONES = ("green", "blue", "violet", "rose", "orange", "amber", "teal", "slate")
ICON_TEXT_MAX = 4


def _path(home: Path) -> Path:
    return Path(home) / "ui" / "project_groups.json"


def _read(home: Path) -> dict:
    try:
        data = json.loads(_path(home).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {"groups": [], "assignments": {}, "icons": {}}
    if not isinstance(data, dict):
        return {"groups": [], "assignments": {}, "icons": {}}
    return {
        "groups": data.get("groups") if isinstance(data.get("groups"), list) else [],
        "assignments": data.get("assignments") if isinstance(data.get("assignments"), dict) else {},
        "icons": data.get("icons") if isinstance(data.get("icons"), dict) else {},
    }


def _write(home: Path, data: dict) -> None:
    atomic_write(_path(home), json.dumps(data, ensure_ascii=False, sort_keys=True) + "\n")


def project_groups(home: Path, project_ids: set[str]) -> dict:
    with _lock:
        data = _read(home)
        groups = [
            g
            for g in data["groups"]
            if isinstance(g, dict)
            and isinstance(g.get("id"), str)
            and isinstance(g.get("name"), str)
        ]
        valid = {g["id"] for g in groups}
        assignments = {
            p: g for p, g in data["assignments"].items() if p in project_ids and g in valid
        }
        icons = {
            p: icon
            for p, icon in data["icons"].items()
            if p in project_ids and isinstance(icon, dict) and icon
        }
        return {"groups": groups, "assignments": assignments, "icons": icons}


def forget_project_group(home: Path, project_id: str) -> None:
    """Remove a retired registration's UI assignment before its ID can be reused."""
    with _lock:
        data = _read(home)
        changed = data["assignments"].pop(project_id, None) is not None
        changed = data["icons"].pop(project_id, None) is not None or changed
        if changed:
            _write(home, data)


def change_project_groups(home: Path, project_ids: set[str], body: dict) -> dict:
    action = body.get("action")
    with _lock:
        data = project_groups(home, project_ids)
        groups = data["groups"]
        assignments = data["assignments"]
        icons = data["icons"]
        if action == "create":
            names = _names(body)
            _check_duplicate(groups, names)
            groups.append({"id": "grp_" + secrets.token_hex(6), **names})
        elif action == "rename":
            group = _group(groups, body.get("group_id"))
            names = _names(body)
            _check_duplicate(groups, names, current=group)
            group.update(names)
            for key in ("name_zh", "name_en"):
                if key not in names:
                    group.pop(key, None)
        elif action == "delete":
            group = _group(groups, body.get("group_id"))
            groups.remove(group)
            assignments = {p: g for p, g in assignments.items() if g != group["id"]}
        elif action == "move":
            project_id = body.get("project_id")
            if project_id not in project_ids:
                raise BridgeError("Project not found")
            group_id = body.get("group_id")
            if group_id:
                _group(groups, group_id)
                assignments[project_id] = group_id
            else:
                assignments.pop(project_id, None)
        elif action == "icon":
            project_id = body.get("project_id")
            if project_id not in project_ids:
                raise BridgeError("Project not found")
            icon = _icon(body)
            if icon:
                icons[project_id] = icon
            else:
                icons.pop(project_id, None)
        elif action == "reorder":
            ids = body.get("group_ids")
            if (
                not isinstance(ids, list)
                or set(ids) != {g["id"] for g in groups}
                or len(ids) != len(groups)
            ):
                raise BridgeError("Invalid group order")
            by_id = {g["id"]: g for g in groups}
            groups = [by_id[i] for i in ids]
        else:
            raise BridgeError("Unknown group action")
        result = {"groups": groups, "assignments": assignments, "icons": icons}
        _write(home, result)
        return result


def _icon(body: dict) -> dict:
    """The icon a request describes; empty (the first-letter default) when it names nothing."""
    icon = {}
    avatar, text, tone = body.get("avatar"), body.get("text"), body.get("tone")
    if avatar:
        if avatar not in AVATAR_IDS:
            raise BridgeError("Unknown avatar")
        icon["avatar"] = avatar
    elif text:
        if not isinstance(text, str) or not text.strip() or len(text.strip()) > ICON_TEXT_MAX:
            raise BridgeError(f"Icon text must be 1–{ICON_TEXT_MAX} characters")
        if any(not ch.isprintable() for ch in text.strip()):
            raise BridgeError("Icon text cannot contain control characters")
        icon["text"] = text.strip()
    if tone:
        if tone not in ICON_TONES:
            raise BridgeError("Unknown icon color")
        icon["tone"] = tone
    return icon


def _name(value) -> str:
    if not isinstance(value, str) or not value.strip() or len(value.strip()) > 50:
        raise BridgeError("Group name must be 1–50 characters")
    return value.strip()


def _names(body: dict) -> dict:
    if "name_zh" not in body and "name_en" not in body:
        return {"name": _name(body.get("name"))}
    aliases = {}
    for key in ("name_zh", "name_en"):
        value = body.get(key, "")
        if not isinstance(value, str):
            raise BridgeError("Group name must be 1–50 characters")
        if value.strip():
            aliases[key] = _name(value)
    if not aliases:
        raise BridgeError("Enter a Chinese or English group name")
    return {"name": aliases.get("name_zh") or aliases["name_en"], **aliases}


def _check_duplicate(groups: list, names: dict, current: dict | None = None) -> None:
    for group in groups:
        if group is current:
            continue
        if group["name"].casefold() == names["name"].casefold():
            raise BridgeError("A group with this name already exists")
        for key in ("name_zh", "name_en"):
            existing = group.get(key)
            if not isinstance(existing, str) or not existing:
                existing = group["name"]
            if key in names and existing.casefold() == names[key].casefold():
                raise BridgeError("A group with this name already exists")


def _group(groups: list, group_id) -> dict:
    for group in groups:
        if group["id"] == group_id:
            return group
    raise BridgeError("Group not found")
