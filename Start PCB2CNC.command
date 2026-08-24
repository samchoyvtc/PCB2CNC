#!/bin/bash
# Double-click in Finder (macOS) to start PCB2CNC.
cd "$(dirname "$0")" || exit 1

pick_python() {
  if command -v python3 >/dev/null 2>&1; then
    echo python3
    return 0
  fi
  if command -v python >/dev/null 2>&1; then
    echo python
    return 0
  fi
  return 1
}

PYTHON="$(pick_python)" || {
  echo "Python 3 is required. Install it from https://www.python.org/downloads/ then double-click this file again."
  read -r -p "Press Return to close this window."
  exit 1
}

"$PYTHON" "scripts/start_server.py"
status=$?
if [ "$status" -ne 0 ]; then
  echo
  read -r -p "Press Return to close this window."
fi
exit "$status"
