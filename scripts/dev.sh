#!/usr/bin/env bash
# GRAFT dev launcher wrapper for macOS/Linux.
#
# The actual startup logic lives in scripts/dev.py so the same code runs on
# Windows, macOS, and Linux. Keep this file as the familiar Unix entry point.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ -n "${PYTHON:-}" ]]; then
  exec "$PYTHON" "$SCRIPT_DIR/dev.py" "$@"
fi
if command -v python3 >/dev/null 2>&1; then
  exec python3 "$SCRIPT_DIR/dev.py" "$@"
fi
if command -v python >/dev/null 2>&1; then
  exec python "$SCRIPT_DIR/dev.py" "$@"
fi

echo "[dev] error: python not found; install Python 3" >&2
exit 1
