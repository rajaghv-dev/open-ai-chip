#!/usr/bin/env bash
# ONE command for the Hermes chip agent (Open WebUI + local Ollama hermes3:8b + chip tool server, this repo only).
#   scripts/hermes.sh          set up what is missing, start, open the desktop app (if built) or the browser
#   scripts/hermes.sh demo     numbered menu of demos (examples/hermes_desktop/demos.py)
#   scripts/hermes.sh demo 2   start everything if needed, run demo 2 (or a short name: kv, precision, ...)
#   scripts/hermes.sh demo proof      demo 8: proof that it is local, tied to this repo, and what context it got (make demo-proof)
#   scripts/hermes.sh demo showcase   the scripted showcase (demo.py): saved as an Open WebUI chat + demo_transcript.md
#   scripts/hermes.sh stop     stop what start.sh started
#   scripts/hermes.sh status   what is running and whether the config audit passes
# Docs: docs/HERMES_DESKTOP.md, examples/hermes_desktop/README.md
set -uo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
HD="$REPO/examples/hermes_desktop"
PY="$REPO/build/agent/venv/bin/python"
APP="$REPO/build/desktop/Hermes Chip Agent.app"
URL="http://127.0.0.1:8080/?models=hermes-chip-agent"
MODEL="hermes3:8b"
say() { echo "[hermes] $*"; }
die() { echo "[hermes] ERROR: $*" >&2; exit 1; }
up() { curl -fsS -m 3 "$1" >/dev/null 2>&1; }

setup() {
  command -v ollama >/dev/null || die "Ollama is not installed (https://ollama.com), then rerun"
  [ -x "$PY" ] || die "agent venv missing: $PY (see docs/HERMES_AGENT.md: python3 -m venv build/agent/venv && build/agent/venv/bin/pip install -r tools/requirements.txt)"
  if [ ! -x "$REPO/build/webui/venv/bin/open-webui" ]; then say "installing Open WebUI (one time, large)"; bash "$HD/setup_webui.sh" || die "setup_webui.sh failed"; fi
  "$PY" -c "import webview" 2>/dev/null || "$REPO/build/agent/venv/bin/pip" install -q pywebview || say "pywebview not installed: the desktop app will not build, the browser is used"
}

start_all() {
  bash "$HD/start.sh" || die "start.sh failed (logs: build/webui/logs)"
  if ! ollama list 2>/dev/null | awk 'NR>1{print $1}' | grep -qx "$MODEL"; then
    say "pulling $MODEL (only model this repo uses)"; ollama pull "$MODEL" || die "ollama pull $MODEL failed"
  fi
  if ! "$PY" "$HD/audit_config.py" >/dev/null; then   # env only applies at Open WebUI start: restart once on drift
    say "config drift: restarting Open WebUI once"; bash "$HD/stop.sh" >/dev/null; bash "$HD/start.sh" >/dev/null || die "restart failed"
  fi
  "$PY" "$HD/audit_config.py" | tail -1
}

open_ui() {
  if [ -d "$APP" ] || { [ -d "$HOME/Applications/Hermes Chip Agent.app" ]; }; then
    say "opening the desktop app (closing its window stops the services)"
    open "$([ -d "$APP" ] && echo "$APP" || echo "$HOME/Applications/Hermes Chip Agent.app")"
  else
    say "opening $URL (build the app with: bash examples/hermes_desktop/desktop/make_app.sh --install)"
    open "$URL" 2>/dev/null || echo "open $URL"
  fi
}

case "${1:-up}" in
  up)     setup; start_all; open_ui ;;
  demo)
    if [ -f "$HD/demos.py" ] && [ "${2:-}" != "showcase" ]; then
      if [ -z "${2:-}" ]; then "$PY" "$HD/demos.py" --list; exit $?; fi
      setup; start_all; "$PY" "$HD/demos.py" "${@:2}"; exit $?
    fi
    setup; start_all; "$PY" "$HD/demo.py" "${@:2}"; rc=$?; open_ui; exit $rc ;;
  stop)   bash "$HD/stop.sh" ;;
  status)
    for u in "ollama http://127.0.0.1:11434/api/tags" "tool_server http://127.0.0.1:8770/health" "open_webui http://127.0.0.1:8080/health"; do
      set -- $u; if up "$2"; then say "$1: up"; else say "$1: down"; fi
    done
    ollama list 2>/dev/null | awk 'NR>1{print $1}' | grep -qx "$MODEL" && say "$MODEL: installed" || say "$MODEL: missing"
    [ -d "$APP" ] && say "app: $APP" || say "app: not built"
    up http://127.0.0.1:8080/health && "$PY" "$HD/audit_config.py" | tail -1
    up http://127.0.0.1:8770/health && say "proof: $(curl -fsS -m 20 -X POST -H 'Content-Type: application/json' -d '{}' http://127.0.0.1:8770/proof_local | python3 -c 'import sys,json; print(json.load(sys.stdin)["status_line"])' 2>/dev/null)" ;;
  *) echo "usage: scripts/hermes.sh [demo|stop|status]" >&2; exit 2 ;;
esac
