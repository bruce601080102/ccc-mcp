#!/usr/bin/env bash
# One-command Git Bash setup for ccc-mcp and its optional local agent CLIs.
# Source this script so CCC_MCP_ENABLE_REAL_AGENTS remains available to the Tunnel.

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  printf 'Run from Git Bash with: source scripts/setup-real-agents.sh [codex|claude|all] [tunnel_id]\n' >&2
  exit 2
fi

ccc_mcp_setup_real_agents() {
  local provider="${1:-codex}"
  local tunnel_id="${2:-}"
  local bridge_root bridge_python need_npm=0 node_major

  case "$provider" in
    codex|claude|all) ;;
    *)
      printf 'Unsupported provider: %s (use codex, claude, or all)\n' "$provider" >&2
      return 2
      ;;
  esac

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

  # Reuse VS Code/WinGet installations already discoverable by the enable script.
  source "$bridge_root/scripts/enable-real-agents.sh" || return 1

  if [[ ("$provider" == "codex" || "$provider" == "all") ]] \
    && ! command -v codex >/dev/null 2>&1; then
    need_npm=1
  fi
  if [[ ("$provider" == "claude" || "$provider" == "all") ]] \
    && ! command -v claude >/dev/null 2>&1; then
    need_npm=1
  fi

  if [[ "$need_npm" == "1" ]] && ! command -v npm >/dev/null 2>&1; then
    if ! command -v winget.exe >/dev/null 2>&1; then
      printf 'npm is required, and WinGet is unavailable. Install Node.js, open a new Git Bash, and retry.\n' >&2
      return 1
    fi
    printf 'Installing Node.js LTS with WinGet...\n'
    winget.exe install --id OpenJS.NodeJS.LTS --exact --source winget \
      --accept-package-agreements --accept-source-agreements || return 1
    export PATH="/c/Program Files/nodejs:$PATH"
    hash -r
  fi

  if [[ "$need_npm" == "1" ]] && ! command -v npm >/dev/null 2>&1; then
    printf 'Node.js was installed, but npm is not visible yet. Open a new Git Bash and run the same setup command again.\n' >&2
    return 1
  fi

  if [[ "$provider" == "codex" || "$provider" == "all" ]]; then
    if ! command -v codex >/dev/null 2>&1; then
      printf 'Installing Codex CLI...\n'
      npm install -g @openai/codex@latest || return 1
      hash -r
      source "$bridge_root/scripts/enable-real-agents.sh" || return 1
    fi
    if ! codex login status >/dev/null 2>&1; then
      printf 'Opening Codex login...\n'
      codex login || return 1
    fi
    codex login status >/dev/null 2>&1 || {
      printf 'Codex login is still incomplete.\n' >&2
      return 1
    }
    printf 'Codex ready.\n'
  fi

  if [[ "$provider" == "claude" || "$provider" == "all" ]]; then
    if ! command -v claude >/dev/null 2>&1; then
      node_major="$(node -p 'process.versions.node.split(".")[0]' 2>/dev/null || true)"
      if [[ -z "$node_major" || "$node_major" -lt 22 ]]; then
        printf 'Claude Code npm installation requires Node.js 22+. Upgrade Node.js and retry.\n' >&2
        return 1
      fi
      printf 'Installing Claude Code...\n'
      npm install -g @anthropic-ai/claude-code || return 1
      hash -r
      source "$bridge_root/scripts/enable-real-agents.sh" || return 1
    fi
    if ! claude auth status --text >/dev/null 2>&1; then
      printf 'Opening Claude Code login...\n'
      claude auth login || return 1
    fi
    claude auth status --text >/dev/null 2>&1 || {
      printf 'Claude Code login is still incomplete.\n' >&2
      return 1
    }
    printf 'Claude Code ready.\n'
  fi

  source "$bridge_root/scripts/enable-real-agents.sh" || return 1
  printf 'ccc-mcp real-agent setup complete for: %s\n' "$provider"

  if [[ -n "$tunnel_id" ]]; then
    if [[ ! -f "$bridge_root/.tools/tunnel-client/tunnel-client.exe" ]]; then
      bash "$bridge_root/scripts/install-tunnel-client.sh" || return 1
    fi
    printf 'Starting ChatGPT Tunnel: %s\n' "$tunnel_id"
    bash "$bridge_root/scripts/connect-chatgpt.sh" "$tunnel_id"
  else
    printf 'Start the Tunnel from this terminal with: bash scripts/connect-chatgpt.sh tunnel_你的實際ID\n'
  fi
}

if ccc_mcp_setup_real_agents "$@"; then
  unset -f ccc_mcp_setup_real_agents
else
  setup_status=$?
  unset -f ccc_mcp_setup_real_agents
  return "$setup_status"
fi
