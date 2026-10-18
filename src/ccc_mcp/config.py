import os
from pathlib import Path


def bridge_home(home: str | Path | None = None) -> Path:
    default = Path.home() / ".ccc-mcp"
    legacy = Path.home() / ".agentbridge"
    if not default.exists() and legacy.exists():
        default = legacy
    path = Path(home or setting_env("HOME") or default)
    path = path.expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path


def setting_env(name, default=None):
    """New names take precedence; existing operator environments remain usable."""
    return os.environ.get("CCC_MCP_" + name, os.environ.get("AGENTBRIDGE_" + name, default))


def metadata_name(root):
    """Keep historical report paths intact; new projects use the new brand."""
    root = Path(root)
    if not (root / ".ccc-mcp").exists() and (root / ".agentbridge").exists():
        return ".agentbridge"
    return ".ccc-mcp"
