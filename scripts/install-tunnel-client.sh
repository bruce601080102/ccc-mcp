#!/usr/bin/env bash
# Deprecated: this logic now lives in `ccc-mcp connect`, which downloads and
# verifies tunnel-client itself before use. Kept only as a thin forwarder for
# anyone still invoking this script directly.
set -euo pipefail

root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
python="$root/.venv/Scripts/python.exe"

printf 'scripts/install-tunnel-client.sh is deprecated; ccc-mcp connect now installs tunnel-client itself.\n' >&2

if [[ ! -f "$python" ]]; then
  printf 'Python virtual environment is missing. Run first:\n' >&2
  printf 'source scripts/setup-real-agents.sh codex\n' >&2
  exit 1
fi

"$python" -c "
from ccc_mcp.adapters.tunnel_client import ensure_tunnel_client
from ccc_mcp.infra.config import bridge_home
path = ensure_tunnel_client(bridge_home())
print(f'Installed and verified: {path}')
"
