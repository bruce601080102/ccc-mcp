#!/usr/bin/env bash
# One-time bootstrap: create the venv and install ccc-mcp itself. This is the
# only step `ccc-mcp` can't do for you yet, since the `ccc-mcp` command doesn't
# exist until this finishes. Everything else (installing/logging into Codex
# and Claude Code, connecting to ChatGPT) is `ccc-mcp setup` / `ccc-mcp connect`
# — this script does not call, and is not called by, any other script.
set -euo pipefail

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  printf 'Run from Git Bash with: source scripts/setup-real-agents.sh\n' >&2
  exit 2
fi

ccc_mcp_bootstrap() {
  local bridge_root bridge_python
  bridge_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)" || return 1
  bridge_python="$bridge_root/.venv/Scripts/python.exe"

  if [[ ! -f "$bridge_python" ]]; then
    if ! command -v python >/dev/null 2>&1; then
      printf 'Python is required. Install Python 3.11+ and run this command again.\n' >&2
      return 1
    fi
    printf 'Creating ccc-mcp virtual environment...\n'
    python -m venv "$bridge_root/.venv" || return 1
  fi

  printf 'Installing/updating ccc-mcp...\n'
  (
    cd "$bridge_root" || exit 1
    "$bridge_python" -m pip install -e ".[dev]"
  ) || return 1
  export PATH="$bridge_root/.venv/Scripts:$PATH"

  printf '\nccc-mcp is installed. Next steps:\n'
  printf '  ccc-mcp setup all                   # install/log in to Codex and Claude Code\n'
  printf '  ccc-mcp connect tunnel_你的實際ID    # connect ChatGPT\n'
}

if ccc_mcp_bootstrap; then
  unset -f ccc_mcp_bootstrap
else
  bootstrap_status=$?
  unset -f ccc_mcp_bootstrap
  return "$bootstrap_status"
fi
