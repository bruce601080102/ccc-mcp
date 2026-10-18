#!/usr/bin/env bash
# Run from Git Bash. Credentials stay in this process's environment, never in a profile.
set -euo pipefail
export PYTHONIOENCODING=utf-8

root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
client="$root/.tools/tunnel-client/tunnel-client.exe"
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
if [[ ! -f "$client" ]]; then
  printf 'Official tunnel-client is missing; installing the latest verified Windows release...\n'
  bash "$root/scripts/install-tunnel-client.sh"
fi

# ChatGPT refresh sends server/discover, which requires the MCP 2.x SDK.
if ! "$python" -c 'from importlib.metadata import version; raise SystemExit(int(version("mcp").split(".")[0]) < 2)'; then
  printf 'MCP SDK is too old for ChatGPT tool refresh. Run from the ccc-mcp folder:\n' >&2
  printf './.venv/Scripts/python.exe -m pip install -e ".[dev]"\n' >&2
  exit 1
fi

if [[ -z "${CONTROL_PLANE_API_KEY:-}" ]]; then
  printf 'Enter the OpenAI runtime API key locally (hidden; do not paste into chat):\n'
  IFS= read -r -s CONTROL_PLANE_API_KEY
  printf '\n'
  if [[ -z "$CONTROL_PLANE_API_KEY" ]]; then
    printf 'No key entered.\n' >&2
    exit 1
  fi
  export CONTROL_PLANE_API_KEY
fi

if [[ "$CONTROL_PLANE_API_KEY" =~ [[:space:]] || "$CONTROL_PLANE_API_KEY" == tunnel_* ]]; then
  printf 'Invalid key input: enter only the runtime Secret Key, without spaces/newlines or a tunnel ID.\n' >&2
  exit 1
fi

# Verify the same runtime identity before entering the long-running polling loop.
# Exclude unrelated admin/API credentials from this read-only metadata check.
printf 'Checking runtime authentication and access to this tunnel...\n'
if ! MSYS_NO_PATHCONV=1 env -u OPENAI_ADMIN_KEY -u OPENAI_API_KEY \
    "$client" admin tunnels get "$tunnel_id"; then
  printf '\nAuthentication/access check failed; polling was not started.\n' >&2
  printf 'Check the runtime key, its Platform organization, and Tunnels Read + Use permissions.\n' >&2
  printf 'To re-enter a key without changing your parent shell, prefix this script with: env -u CONTROL_PLANE_API_KEY\n' >&2
  exit 1
fi

python_windows="$("$python" -c 'import sys; from pathlib import Path; print(Path(sys.executable).as_posix())' | tr -d '\r')"
home_windows="$("$python" -c 'from ccc_mcp.config import bridge_home; print(bridge_home().as_posix())' | tr -d '\r')"
profile_dir="$("$python" -c 'from pathlib import Path; import sys; print((Path(sys.argv[1]) / ".tunnel").as_posix())' "$root" | tr -d '\r')"
mcp_command="\"$python_windows\" -m ccc_mcp --home \"$home_windows\" serve"

# A profile per tunnel makes reruns safe and avoids replacing an existing profile.
profile="ccc-mcp-${tunnel_id#tunnel_}"
if [[ ! -f "$root/.tunnel/$profile.yaml" && ! -f "$root/.tunnel/$profile.yml" ]]; then
  MSYS_NO_PATHCONV=1 "$client" init --sample sample_mcp_stdio_local \
    --profile "$profile" --profile-dir "$profile_dir" \
    --tunnel-id "$tunnel_id" --mcp-command "$mcp_command" \
    --control-plane-api-key-ref env:CONTROL_PLANE_API_KEY \
    --health-listen-addr 127.0.0.1:0
fi

MSYS_NO_PATHCONV=1 "$client" doctor --profile "$profile" --profile-dir "$profile_dir" --explain
printf '\nKeep this terminal open while ChatGPT uses ccc-mcp. Press Ctrl+C to stop.\n'
MSYS_NO_PATHCONV=1 "$client" run --profile "$profile" --profile-dir "$profile_dir"
