#!/usr/bin/env bash
# Deprecated: this logic now lives in `ccc-mcp connect`, which installs/verifies
# tunnel-client, resolves native codex/claude CLIs, and manages the connection
# itself without needing a prior `source` of enable-real-agents.sh. Kept only
# as a thin forwarder for anyone still invoking this script directly.
set -euo pipefail
export PYTHONIOENCODING=utf-8

root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
python="$root/.venv/Scripts/python.exe"
tunnel_id="${1:-}"

if [[ ! "$tunnel_id" =~ ^tunnel_[a-z0-9]{32}$ ]]; then
  printf 'Usage: bash scripts/connect-chatgpt.sh tunnel_YOUR_ID\n' >&2
  exit 2
fi
if [[ ! -f "$python" ]]; then
  printf 'Python virtual environment is missing. Run first:\n' >&2
  printf 'source scripts/setup-real-agents.sh codex\n' >&2
  exit 1
fi

printf 'scripts/connect-chatgpt.sh is deprecated; use: ccc-mcp connect %s\n' "$tunnel_id" >&2
exec "$python" -m ccc_mcp connect "$tunnel_id"
