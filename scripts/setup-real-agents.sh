#!/usr/bin/env bash
# One-time bootstrap: create the venv and install ccc-mcp itself. This is the
# only step `ccc-mcp` can't do for you yet, since the `ccc-mcp` command doesn't
# exist until this finishes. Everything else (installing/logging into Codex
# and Claude Code, connecting to ChatGPT) is `ccc-mcp setup` / `ccc-mcp connect`
# — this script does not call, and is not called by, any other script.
#
# Works sourced from bash or zsh (macOS's default login shell), on Windows Git
# Bash, macOS and Linux.
#
# Deliberately does not `set -e`/`-u`/`-o pipefail`: this file is sourced
# directly into the caller's interactive shell, so any shell option set here
# would stick around afterwards and can break unrelated things in that shell
# (prompt plugins referencing normally-unset variables, etc). Every step below
# checks its own exit status instead.

_ccc_mcp_sourced() {
  if [ -n "${BASH_VERSION:-}" ]; then
    [ "${BASH_SOURCE[0]}" != "$0" ]
  elif [ -n "${ZSH_VERSION:-}" ]; then
    # Called from inside a function, so ZSH_EVAL_CONTEXT has a trailing
    # ":shfunc" (e.g. "toplevel:file:shfunc"); look for "file" as any
    # colon-separated component, not just the last one.
    case ":${ZSH_EVAL_CONTEXT:-}:" in *:file:*) return 0 ;; *) return 1 ;; esac
  else
    return 0 # unknown shell: skip the direct-execution check below
  fi
}

if ! _ccc_mcp_sourced; then
  printf 'Run with: source scripts/setup-real-agents.sh\n' >&2
  unset -f _ccc_mcp_sourced
  exit 2
fi

ccc_mcp_bootstrap() {
  local bridge_root bridge_python venv_dir

  if [ ! -f pyproject.toml ] || ! grep -q '^name = "ccc-mcp"' pyproject.toml 2>/dev/null; then
    printf 'Run this from the ccc-mcp repository root: cd ccc-mcp && source scripts/setup-real-agents.sh\n' >&2
    return 1
  fi
  bridge_root="$(pwd)"
  venv_dir="$bridge_root/.venv"

  case "$(uname -s 2>/dev/null)" in
    MINGW* | MSYS* | CYGWIN*) bridge_python="$venv_dir/Scripts/python.exe" ;;
    *) bridge_python="$venv_dir/bin/python" ;;
  esac

  if [ ! -f "$bridge_python" ]; then
    local candidate py_cmd=""
    for candidate in python3.13 python3.12 python3.11 python3 python; do
      if command -v "$candidate" >/dev/null 2>&1 &&
        "$candidate" -c 'import sys; sys.exit(0 if sys.version_info[:2] >= (3, 11) else 1)' 2>/dev/null; then
        py_cmd="$candidate"
        break
      fi
    done
    if [ -z "$py_cmd" ]; then
      printf 'Python 3.11+ is required but was not found on PATH.\n' >&2
      printf 'Install it (e.g. "brew install python@3.12" on macOS) and run this command again.\n' >&2
      return 1
    fi
    printf 'Creating ccc-mcp virtual environment with %s...\n' "$py_cmd"
    "$py_cmd" -m venv "$venv_dir" || return 1
  fi

  printf 'Installing/updating ccc-mcp...\n'
  (
    cd "$bridge_root" || exit 1
    "$bridge_python" -m pip install -e ".[dev]"
  ) || return 1

  case "$(uname -s 2>/dev/null)" in
    MINGW* | MSYS* | CYGWIN*) export PATH="$venv_dir/Scripts:$PATH" ;;
    *) export PATH="$venv_dir/bin:$PATH" ;;
  esac

  printf '\nccc-mcp is installed. Next steps:\n'
  printf '  ccc-mcp setup all                   # install/log in to Codex and Claude Code\n'
  printf '  ccc-mcp connect tunnel_你的實際ID    # connect ChatGPT\n'
}

if ccc_mcp_bootstrap; then
  bootstrap_status=0
else
  bootstrap_status=$?
fi
unset -f ccc_mcp_bootstrap _ccc_mcp_sourced
return "$bootstrap_status"
