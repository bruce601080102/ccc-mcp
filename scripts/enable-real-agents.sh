#!/usr/bin/env bash
# Source this file from Git Bash before connecting the tunnel. No model is invoked.
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  printf 'Run: source scripts/enable-real-agents.sh\n' >&2
  exit 2
fi

ccc_mcp_find_cli_dir() {
  "$1" - "$2" <<'PY'
import shutil
import sys
import os
from pathlib import Path

name = sys.argv[1]
home = Path.home()
found = shutil.which(name)
candidates = [Path(found)] if found and Path(found).suffix.lower() == '.exe' else []
candidates += [home / '.local/bin' / (name + '.exe'),
               home / 'AppData/Local/Microsoft/WinGet/Links' / (name + '.exe')]
if name == 'claude':
    local_appdata = Path(os.environ.get('LOCALAPPDATA', home / 'AppData/Local'))
    roaming_appdata = Path(os.environ.get('APPDATA', home / 'AppData/Roaming'))
    npm_package = roaming_appdata / 'npm/node_modules/@anthropic-ai/claude-code'
    candidates += [npm_package / 'bin/claude.exe']
    if npm_package.is_dir():
        candidates += sorted(
            npm_package.glob('node_modules/@anthropic-ai/claude-code-win32-*/claude.exe'),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
    packages = local_appdata / 'Microsoft/WinGet/Packages'
    if packages.is_dir():
        candidates += sorted(packages.glob('Anthropic.ClaudeCode_*/claude.exe'),
                             key=lambda path: path.stat().st_mtime, reverse=True)
if name == 'codex':
    local_appdata = Path(os.environ.get('LOCALAPPDATA', home / 'AppData/Local'))
    codex_bins = local_appdata / 'OpenAI/Codex/bin'
    if codex_bins.is_dir():
        candidates += sorted(
            (path for path in codex_bins.glob('*/codex.*')
             if path.suffix.lower() == '.exe'),
            key=lambda path: path.stat().st_mtime,
            reverse=True,
        )
    extension_root = home / '.vscode/extensions'
    if extension_root.is_dir():
        candidates += sorted(extension_root.glob('openai.chatgpt-*/bin/windows-x86_64/codex.exe'),
                             key=lambda path: path.stat().st_mtime, reverse=True)
for candidate in candidates:
    if candidate.is_file():
        print(candidate.parent.resolve().as_posix())
        break
else:
    raise SystemExit(1)
PY
}

ccc_mcp_enable_real_agents() {
  local bridge_root bridge_python codex_dir claude_dir
  bridge_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)" || return 1
  bridge_python="$bridge_root/.venv/Scripts/python.exe"
  if [[ ! -f "$bridge_python" ]]; then
    printf 'Missing ccc-mcp Python virtual environment.\n' >&2
    return 1
  fi
  export PATH="$bridge_root/.venv/Scripts:$PATH"
  if codex_dir="$(PYTHONIOENCODING=utf-8 ccc_mcp_find_cli_dir "$bridge_python" codex)"; then
    codex_dir="${codex_dir//$'\r'/}"
    export PATH="$(/usr/bin/cygpath.exe -u "$codex_dir"):$PATH"
    hash -r
    if codex login status >/dev/null 2>&1; then
      printf 'Codex native CLI found; login available.\n'
    else
      printf 'Codex found; finish login with: codex login\n'
    fi
  else
    printf 'Codex unavailable. Run: source scripts/setup-real-agents.sh codex\n'
    printf 'Or configure roles to use Claude or ChatGPT.\n'
  fi

  if claude_dir="$(PYTHONIOENCODING=utf-8 ccc_mcp_find_cli_dir "$bridge_python" claude)"; then
    claude_dir="${claude_dir//$'\r'/}"
    export PATH="$(/usr/bin/cygpath.exe -u "$claude_dir"):$PATH"
    hash -r
    if claude auth status --text >/dev/null 2>&1; then
      printf 'Claude Code native CLI found; login available.\n'
    else
      printf 'Claude found; finish login with: claude auth login\n'
    fi
  else
    printf 'Claude unavailable. Run: source scripts/setup-real-agents.sh claude\n'
    printf 'Or configure roles to use Codex or ChatGPT.\n'
  fi
  export CCC_MCP_ENABLE_REAL_AGENTS=1
  printf 'Real dispatch enabled for programs started from this terminal. No model task was started.\n'
  if [[ "${CCC_MCP_CONNECTING:-0}" == "1" ]]; then
    printf 'Native CLI paths applied to this Tunnel process.\n'
  else
    printf 'Restart scripts/connect-chatgpt.sh from this same terminal to apply the environment.\n'
  fi
}

if ccc_mcp_enable_real_agents; then
  unset -f ccc_mcp_find_cli_dir ccc_mcp_enable_real_agents
else
  unset -f ccc_mcp_find_cli_dir ccc_mcp_enable_real_agents
  return 1
fi
