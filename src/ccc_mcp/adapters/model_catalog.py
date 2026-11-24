"""Models and effort levels the installed Codex / Claude Code CLIs say they support.

The console shows these as a drop-down so nobody has to know a model id by heart:

* Codex has a real catalog: `codex debug models` prints every model with its display name and
  the reasoning levels that model accepts.
* Claude Code has no list command. Its `--help` text names the model aliases it accepts
  ('opus', 'sonnet', ...) and the effort levels, so those are read from that text.

Anything the CLI does not answer (not installed, older version, unexpected output) falls back to a
short built-in list and says so (`source`), so the console never ends up with an empty drop-down it
cannot explain. What is stored and later passed to the CLI is still validated by
core/configuration.py; this module only offers choices.
"""

import json
import re
import subprocess
import threading
import time
import tomllib
from pathlib import Path

from ..infra.errors import BridgeError
from .agents.base import native_executable

CACHE_SECONDS = 600
COMMAND_TIMEOUT = 25

# Used when a CLI cannot be asked. Aliases are accepted by `claude --model`; effort names are the
# ones core/configuration.py accepts for the provider.
FALLBACK = {
    "claude": {
        "models": ["opus", "sonnet", "haiku"],
        "efforts": ["low", "medium", "high", "xhigh", "max"],
    },
    "codex": {"models": [], "efforts": ["low", "medium", "high", "xhigh"]},
}

_cache: dict[str, tuple[float, dict]] = {}
_lock = threading.Lock()


def _run(argv):
    result = subprocess.run(
        argv, capture_output=True, text=True, timeout=COMMAND_TIMEOUT, check=False,
        stdin=subprocess.DEVNULL, cwd=Path.home(),
    )
    if result.returncode != 0:
        raise BridgeError(f"{Path(argv[0]).name} exited with status {result.returncode}")
    return result.stdout


def parse_codex_models(text):
    """Models Codex lists for use (hidden ones are internal), in the CLI's own order."""
    try:
        catalog = json.loads(text)
    except ValueError as exc:
        raise BridgeError("Codex returned a model list that is not JSON") from exc
    models = []
    for item in catalog.get("models", []) if isinstance(catalog, dict) else []:
        slug = item.get("slug") if isinstance(item, dict) else None
        if not isinstance(slug, str) or not slug or item.get("visibility") not in (None, "list"):
            continue
        levels = [
            level for level in item.get("supported_reasoning_levels") or []
            if isinstance(level, dict) and isinstance(level.get("effort"), str)
        ]
        models.append({
            "id": slug,
            "label": item.get("display_name") or slug,
            "description": item.get("description") or "",
            "efforts": [level["effort"] for level in levels],
            "effort_notes": {level["effort"]: level.get("description") or "" for level in levels},
            "default_effort": item.get("default_reasoning_level") or "",
        })
    if not models:
        raise BridgeError("Codex listed no models")
    return models


def parse_claude_help(text):
    """(model aliases, effort levels) named in `claude --help`; either may be empty."""
    flat = re.sub(r"\s+", " ", text)
    aliases = re.search(r"alias for the latest model \(e\.g\. ([^)]*)\)", flat)
    efforts = re.search(r"--effort <level> .*?\(([^)]*)\)", flat)
    return (
        re.findall(r"'([A-Za-z0-9][A-Za-z0-9._-]*)'", aliases.group(1)) if aliases else [],
        [part.strip() for part in efforts.group(1).split(",") if part.strip()] if efforts else [],
    )


def _claude(executable):
    aliases, efforts = parse_claude_help(_run([executable, "--help"]))
    if not aliases and not efforts:
        raise BridgeError("Claude Code's help did not name models or effort levels")
    models = [
        {
            "id": alias, "label": f"{alias.capitalize()} (latest)",
            "description": f"The latest {alias} model", "efforts": [], "effort_notes": {},
            "default_effort": "",
        }
        for alias in aliases or FALLBACK["claude"]["models"]
    ]
    return models, efforts or FALLBACK["claude"]["efforts"]


def _codex(executable):
    models = parse_codex_models(_run([executable, "debug", "models"]))
    levels = []
    for model in models:
        levels.extend(level for level in model["efforts"] if level not in levels)
    return models, levels or FALLBACK["codex"]["efforts"]


def _fallback(provider, reason):
    spec = FALLBACK[provider]
    models = [
        {"id": name, "label": name.capitalize(), "description": "", "efforts": [],
         "effort_notes": {}, "default_effort": ""}
        for name in spec["models"]
    ]
    return {"source": "builtin", "models": models, "efforts": list(spec["efforts"]), "reason": reason}


def _discover(provider):
    try:
        executable = native_executable(provider)
    except BridgeError:
        return _fallback(provider, "not_installed")
    try:
        models, efforts = (_codex if provider == "codex" else _claude)(executable)
    except (BridgeError, OSError, subprocess.SubprocessError) as exc:
        return _fallback(provider, str(exc))
    return {"source": "cli", "models": models, "efforts": efforts, "reason": ""}


def _text(value):
    return value.strip() if isinstance(value, str) and value.strip() else ""


def configured_defaults(provider):
    """What the CLI uses when ccc-mcp passes no --model / effort: {"model": ..., "effort": ...}.

    Read from the user's own CLI settings (Codex: ~/.codex/config.toml; Claude Code:
    ~/.claude/settings.json). An empty string means the file does not say, so the CLI's built-in
    default applies and only the CLI knows it. Read on every call: the files change without us.
    """
    home = Path.home()
    try:
        if provider == "codex":
            data = tomllib.loads((home / ".codex" / "config.toml").read_text(encoding="utf-8"))
            return {"model": _text(data.get("model")), "effort": _text(data.get("model_reasoning_effort"))}
        data = json.loads((home / ".claude" / "settings.json").read_text(encoding="utf-8"))
        data = data if isinstance(data, dict) else {}
        return {"model": _text(data.get("model")), "effort": _text(data.get("effortLevel"))}
    except (OSError, ValueError):
        return {"model": "", "effort": ""}


def provider_models(refresh=False):
    """{"codex": {...}, "claude": {...}}: models, effort levels and where they came from.

    Asking a CLI takes a second or more, so an answer is kept for ten minutes; `refresh` asks again.
    """
    now = time.monotonic()
    result = {}
    for provider in ("codex", "claude"):
        with _lock:
            hit = _cache.get(provider)
        if hit and not refresh and now - hit[0] < CACHE_SECONDS:
            result[provider] = hit[1]
            continue
        found = _discover(provider)
        with _lock:
            _cache[provider] = (time.monotonic(), found)
        result[provider] = found
    return {name: {**found, "defaults": configured_defaults(name)} for name, found in result.items()}
