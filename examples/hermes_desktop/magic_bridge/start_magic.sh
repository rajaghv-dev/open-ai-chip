#!/usr/bin/env bash
# Start Magic (in the LibreLane container, on XQuartz) with the read-only bridge, and load a design. Foreground.
#   bash examples/hermes_desktop/magic_bridge/start_magic.sh <design> [port]     (default port 8766, 127.0.0.1 only)
# One-time macOS setup: see the header of scripts/gui/open_gui.sh (XQuartz listening on TCP, xhost +localhost).
# Env: MAGIC_BRIDGE_TOKEN (optional), GUI_DISPLAY, DOCKER_HOST. Stop: Ctrl-C (sends QUIT, then docker kill).
# Docs: examples/hermes_desktop/magic_bridge/README.md
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
[ -n "${1:-}" ] || { echo "usage: start_magic.sh <design> [port]"; exit 2; }
if [ "$(uname)" = Darwin ] && ! lsof -nP -iTCP:6000 -sTCP:LISTEN >/dev/null 2>&1; then
  echo "XQuartz is not listening on TCP 6000: open -a XQuartz (one-time setup: header of scripts/gui/open_gui.sh)"; exit 1
fi
exec python3 "$HERE/client.py" start "$@"
