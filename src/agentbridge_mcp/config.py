import os
from pathlib import Path


def bridge_home(home: str | Path | None = None) -> Path:
    path = Path(home or os.environ.get("AGENTBRIDGE_HOME", Path.home() / ".agentbridge"))
    path = path.expanduser().resolve()
    path.mkdir(parents=True, exist_ok=True)
    return path
