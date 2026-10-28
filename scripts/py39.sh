#!/usr/bin/env bash
set -e

# 確認已安裝 Python 3.9；失敗時不移動現有環境。
py -3.9 --version

# 備份舊測試環境
VENV="$HOME/.ccc-mcp/venvs/astropy__astropy-12907"
if [ -d "$VENV" ]; then
  mv "$VENV" "${VENV}-backup-$(date +%Y%m%d-%H%M%S)"
fi

# 用 Python 3.9 建立測試環境
py -3.9 -m venv "$VENV"

# 確認版本
"$VENV/Scripts/python.exe" --version
