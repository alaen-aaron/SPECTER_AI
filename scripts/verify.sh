#!/usr/bin/env bash
# SPECTER_AI verification toolkit — Linux/macOS launcher.
#
# All check logic lives in scripts/doctor.py (stdlib-only Python) so it
# is defined exactly once; this script's only job is finding a Python
# interpreter on this platform and delegating to it.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

PYTHON_BIN="${PYTHON_BIN:-}"
if [ -z "$PYTHON_BIN" ]; then
  if command -v python3 >/dev/null 2>&1; then
    PYTHON_BIN="python3"
  elif command -v python >/dev/null 2>&1; then
    PYTHON_BIN="python"
  else
    echo "error: no python3/python interpreter found on PATH." >&2
    echo "Install Python 3.12+ and re-run scripts/verify.sh" >&2
    exit 1
  fi
fi

exec "$PYTHON_BIN" "$SCRIPT_DIR/doctor.py" "$@"
