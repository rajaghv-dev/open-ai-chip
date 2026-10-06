#!/usr/bin/env bash
# Start the KLayout desktop window with the agent bridge macro (option B).
#   bash examples/hermes_klayout_gui/start_live.sh [design]
# env: KLAYOUT_AGENT_PORT (8765), KLAYOUT_AGENT_TOKEN (optional), KLAYOUT_AGENT_ALLOW_QUIT=1 (test harness only),
#      KLAYOUT_BIN (override the binary)
# Flags used: -e edit mode (GUI), -rm <file> run this macro at startup and keep the GUI open.
# Docs: examples/hermes_klayout_gui/README_live.md, docs/HERMES_FROM_TERMINAL.md
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
if [ "$(uname -s)" = Darwin ]; then KL_DEFAULT=/Applications/KLayout/klayout.app/Contents/MacOS/klayout
else KL_DEFAULT="$(command -v klayout || echo klayout)"; [ -n "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ] || echo "no DISPLAY: the KLayout window needs a desktop session (or Xvfb)"; fi
KL="${KLAYOUT_BIN:-$KL_DEFAULT}"
export KLAYOUT_AGENT_PORT="${KLAYOUT_AGENT_PORT:-8765}"
export KLAYOUT_AGENT_REPO="$REPO"
[ -x "$KL" ] || command -v "$KL" >/dev/null 2>&1 || { echo "KLayout not found at $KL (set KLAYOUT_BIN)"; exit 1; }
if (exec 3<>"/dev/tcp/127.0.0.1/$KLAYOUT_AGENT_PORT") 2>/dev/null; then
  echo "port $KLAYOUT_AGENT_PORT is already in use (is the bridge already running?)"; exit 1
fi
echo "KLayout bridge: 127.0.0.1:$KLAYOUT_AGENT_PORT (localhost only, read-only). Close the window to stop."
echo "Agent: build/agent/venv/bin/python examples/hermes_klayout_gui/agent.py --backend live \"...\""
"$KL" -e -rm "$HERE/klayout_macro/agent_bridge.py" &
KPID=$!
echo "klayout pid $KPID"
if [ -n "${1:-}" ]; then
  for _ in $(seq 1 60); do
    if (exec 3<>"/dev/tcp/127.0.0.1/$KLAYOUT_AGENT_PORT") 2>/dev/null; then break; fi
    sleep 0.5
  done
  "$REPO/build/agent/venv/bin/python" - "$1" <<'PY'
import sys, os
sys.path.insert(0, os.path.join(os.environ["KLAYOUT_AGENT_REPO"], "examples", "hermes_klayout_gui"))
from live_backend import LiveBackend
print(LiveBackend().open_design(sys.argv[1]))
PY
fi
wait $KPID
