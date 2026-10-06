#!/usr/bin/env bash
# One-time install of Open WebUI (host, no Docker) into build/webui/venv. Large: see docs/HERMES_DESKTOP.md.
# Docs: examples/hermes_desktop/README.md, docs/HERMES_DESKTOP.md
set -euo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
PY312="${PY312:-/opt/homebrew/bin/python3.12}"
V="$REPO/build/webui/venv"
mkdir -p "$REPO/build/webui/logs" "$REPO/build/webui/data"
[ -x "$PY312" ] || { echo "need python3.12 (brew install python@3.12), or set PY312=" >&2; exit 1; }
[ -x "$V/bin/python" ] || "$PY312" -m venv "$V"
t0=$(date +%s)
"$V/bin/pip" install open-webui 2>&1 | tee "$REPO/build/webui/logs/install.log" | tail -3
echo "installed in $(( $(date +%s) - t0 )) s, $(du -sh "$V" | cut -f1)"
# desktop window needs pywebview in the agent venv
if [ -x "$REPO/build/agent/venv/bin/pip" ]; then "$REPO/build/agent/venv/bin/pip" install -q pywebview; fi
