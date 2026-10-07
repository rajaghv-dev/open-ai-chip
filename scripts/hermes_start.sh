#!/usr/bin/env bash
# Docs: hermes-agents.md, docs/HERMES_CLASS_SHOWCASE.md
# hermes_start.sh -- start the Hermes Agent desktop app (Nous Hermes.app, profile "chip") for this repo, after checking and
# starting everything it needs. Run it from a terminal:
#   bash scripts/hermes_start.sh               check, start what is missing, warm the models, smoke-test, open Hermes.app
#   bash scripts/hermes_start.sh --check       check only: start nothing, change nothing (exit 1 if a required part is missing)
#   bash scripts/hermes_start.sh --no-app      everything except opening Hermes.app
#   bash scripts/hermes_start.sh --no-docker   do not start Colima (flows, /whatif, /rebuild, /experiment flow-all need it)
#   bash scripts/hermes_start.sh --restart-server   restart the repo tool server even if its code did not change
#   bash scripts/hermes_start.sh --demo-sessions    also (re)create the nine pinned "Demo N: ..." sessions (scripts/hermes/make_demo_sessions.sh)
# Also: make hermes-app, bash scripts/hermes.sh app.
# What it does, in order (each line of the final table is one of these):
#   1 hermes CLI and Hermes.app installed           6 Docker (Colima VM "osl") and the LibreLane image (optional: flows)
#   2 profile "chip" is the default, plugin linked  7 KLayout app (GUI), XQuartz on TCP 6000 for Magic (optional)
#     and enabled, hook and MCP server configured   8 Grafana (optional, read-only dashboards)
#   3 Ollama running (starts Ollama.app)            9 repo tool server on 127.0.0.1:8770 (started, or restarted when its
#   4 models present: qwen3.5-64k:9b (required),       code is newer and no job is running), health and RAG warmed
#     qwen3-embedding:0.6b (RAG; else BM25 only)   10 smoke tests: /chip, /timing kv8, router, RAG, MCP bridge
#   5 both models loaded and kept warm 30 min      11 Hermes.app opened (restarted when the plugin changed since it started)
# Never: pulls models, changes ~/.hermes (run scripts/hermes_agent_setup.sh for that), edits repo files, reads secrets.
set -uo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH"
REPO="$(cd "$(dirname "$0")/.." && pwd)"
. "$REPO/scripts/lib/common.sh"
PY="$REPO/build/agent/venv/bin/python"
TS="$REPO/examples/hermes_desktop/tool_server"
PORT="${CHIP_TOOLS_PORT:-8770}"
BASE="http://127.0.0.1:$PORT"
OLLAMA="http://127.0.0.1:11434"
MODEL="qwen3.5-64k:9b"
EMBED="${RAG_EMBED_MODEL:-qwen3-embedding:0.6b}"
PROFILE_DIR="$HOME/.hermes/profiles/chip"
PLUGIN_LINK="$PROFILE_DIR/plugins/open-ai-chip"
MARK="$REPO/build/agent/toolserver.started"
CHECK=0; APP=1; DOCKER=1; RESTART=0; DEMOS=0
for a in "$@"; do
  case "$a" in
    --check) CHECK=1 ;; --no-app) APP=0 ;; --no-docker) DOCKER=0 ;; --restart-server) RESTART=1 ;; --demo-sessions) DEMOS=1 ;;
    -h|--help) sed -n 2,22p "$0"; exit 0 ;;
    *) echo "unknown option $a (see --help)"; exit 2 ;;
  esac
done
mkdir -p "$REPO/build/agent"

ROWS=(); FAIL=0
row() {  # row <status ok|warn|fail> <part> <detail>
  ROWS+=("$1|$2|$3")
  case "$1" in ok) printf "  \033[32mok\033[0m    %-26s %s\n" "$2" "$3" ;; warn) printf "  \033[33mwarn\033[0m  %-26s %s\n" "$2" "$3" ;;
    *) printf "  \033[31mFAIL\033[0m  %-26s %s\n" "$2" "$3"; FAIL=1 ;; esac
}
step() { printf "\n\033[1m== %s\033[0m\n" "$*"; }
up() { curl -fsS -m "${2:-3}" "$1" >/dev/null 2>&1; }
json() { "$PY" -c "import sys,json; d=json.load(sys.stdin); print(eval(sys.argv[1]))" "$1" 2>/dev/null; }
post() { curl -fsS -m "${3:-60}" -X POST "$BASE/$1" -H 'Content-Type: application/json' -d "$2" 2>/dev/null; }
wait_for() { local url="$1" n="${2:-60}"; for _ in $(seq 1 "$n"); do up "$url" 2 && return 0; sleep 1; done; return 1; }

echo "Hermes Agent for open-ai-chip: $( [ $CHECK = 1 ] && echo 'check only' || echo 'check and start' )   (repo: $REPO)"

# 1 -------------------------------------------------------------------------------------------- Hermes itself
step "1. Hermes CLI and desktop app"
if command -v hermes >/dev/null; then row ok "hermes CLI" "$(hermes --version 2>/dev/null | head -1)"; else row fail "hermes CLI" "not on PATH (install Nous Hermes Agent)"; fi
if [ -d /Applications/Hermes.app ]; then row ok "Hermes.app" "/Applications/Hermes.app"; else row fail "Hermes.app" "not installed"; fi

# 2 -------------------------------------------------------------------------------------------- the chip profile
step "2. Profile 'chip' (setup: bash scripts/hermes_agent_setup.sh, review, then --apply)"
if [ -f "$PROFILE_DIR/config.yaml" ]; then
  row ok "profile chip" "$PROFILE_DIR"
  if hermes profile list 2>/dev/null | grep -q '◆chip'; then row ok "default profile" "chip (Hermes.app opens on it)"
  else row warn "default profile" "not chip: run 'hermes profile use chip' or pick chip in the app's profile rail"; fi
  if [ -L "$PLUGIN_LINK" ] && [ -f "$PLUGIN_LINK/__init__.py" ]; then
    if grep -q 'open-ai-chip' "$PROFILE_DIR/config.yaml"; then row ok "plugin open-ai-chip" "linked and enabled (slash commands)"
    else row fail "plugin open-ai-chip" "linked but not in plugins.enabled: re-run the setup with --apply"; fi
  else row fail "plugin open-ai-chip" "not linked: re-run bash scripts/hermes_agent_setup.sh --apply"; fi
  grep -q 'hermes_mcp_bridge.py' "$PROFILE_DIR/config.yaml" && row ok "MCP server chip" "configured (tools/hermes_mcp_bridge.py)" || row fail "MCP server chip" "missing in config: re-run the setup"
  grep -q 'pre_tool_call.py' "$PROFILE_DIR/config.yaml" && row ok "safety hook" "pre_tool_call + confirm guard" || row fail "safety hook" "missing in config: re-run the setup"
else
  row fail "profile chip" "not set up: bash scripts/hermes_agent_setup.sh (dry run), then --apply"
fi

# 3-5 ------------------------------------------------------------------------------------------ Ollama and models
step "3-5. Ollama and the local models"
if ! command -v ollama >/dev/null; then row fail "Ollama" "not installed (https://ollama.com)"
else
  if ! up "$OLLAMA/api/tags"; then
    if [ $CHECK = 1 ]; then row fail "Ollama" "not running (start: open -a Ollama)"
    else echo "  starting Ollama..."; open -a Ollama 2>/dev/null || (ollama serve >/dev/null 2>&1 &); wait_for "$OLLAMA/api/tags" 60 || true; fi
  fi
  if up "$OLLAMA/api/tags"; then
    row ok "Ollama" "$(curl -fsS -m 3 $OLLAMA/api/version | json 'd["version"]') on 127.0.0.1:11434"
    TAGS="$(curl -fsS -m 5 $OLLAMA/api/tags)"
    for m in "$MODEL" "$EMBED"; do
      if echo "$TAGS" | grep -q "\"$m\""; then row ok "model $m" "installed"
      elif [ "$m" = "$MODEL" ]; then row fail "model $m" "missing: ollama pull qwen3.5:9b, then create the 64k variant (docs/HERMES_AGENT.md)"
      else row warn "model $m" "missing: RAG falls back to BM25 (ollama pull $m)"; fi
    done
    if [ $CHECK = 0 ]; then
      echo "  loading models into memory (keep_alive 30m)..."
      t0=$(date +%s)
      curl -fsS -m 300 $OLLAMA/api/generate -d "{\"model\":\"$MODEL\",\"keep_alive\":\"30m\"}" >/dev/null 2>&1 && row ok "warm $MODEL" "loaded in $(( $(date +%s)-t0 )) s, kept 30 min" || row warn "warm $MODEL" "could not load (first answer will be slow)"
      curl -fsS -m 120 $OLLAMA/api/embed -d "{\"model\":\"$EMBED\",\"input\":\"warm\",\"keep_alive\":\"30m\"}" >/dev/null 2>&1 && row ok "warm $EMBED" "loaded, kept 30 min" || row warn "warm $EMBED" "could not load (RAG uses BM25)"
    fi
  fi
fi

# 6 -------------------------------------------------------------------------------------------- Docker for flows
step "6. Docker (Colima 'osl') and LibreLane image: needed for /run gds|flow-all, /whatif, /rebuild"
oac_docker_host
if docker info >/dev/null 2>&1; then row ok "Docker" "${DOCKER_HOST:-default}"
elif [ $DOCKER = 1 ] && [ $CHECK = 0 ] && command -v colima >/dev/null; then
  echo "  starting Colima profile osl (about 30 s)..."; colima start osl >/dev/null 2>&1
  docker info >/dev/null 2>&1 && row ok "Docker" "Colima osl started" || row warn "Docker" "Colima did not start: flows unavailable (colima start osl)"
else row warn "Docker" "not running: flows unavailable (colima start osl)"; fi
if docker info >/dev/null 2>&1; then
  docker image inspect "$OAC_LIBRELANE_IMAGE" >/dev/null 2>&1 && row ok "LibreLane image" "$OAC_LIBRELANE_IMAGE" || row warn "LibreLane image" "missing: make doctor shows how to pull $OAC_LIBRELANE_IMAGE"
  busy="$(docker ps --format '{{.Names}}' 2>/dev/null | head -3 | tr '\n' ' ')"
  [ -n "$busy" ] && row warn "running containers" "$busy(one physical flow at a time)"
fi
[ -d "$OAC_PDK_ROOT/sky130A" ] && row ok "PDK sky130A" "$OAC_PDK_ROOT" || row warn "PDK sky130A" "missing under $OAC_PDK_ROOT (make doctor)"

# 7-8 ------------------------------------------------------------------------------------------ GUIs and Grafana
step "7-8. KLayout, XQuartz (Magic), Grafana"
[ -d /Applications/KLayout/klayout.app ] && row ok "KLayout" "/Applications/KLayout/klayout.app (/klayout, /gds, /drc live)" || row warn "KLayout" "not installed: /klayout and /gds unavailable"
if [ -d /Applications/Utilities/XQuartz.app ] || [ -d /Applications/XQuartz.app ]; then
  if ! lsof -nP -iTCP:6000 -sTCP:LISTEN >/dev/null 2>&1 && [ $CHECK = 0 ]; then
    if [ "$(defaults read org.xquartz.X11 nolisten_tcp 2>/dev/null)" = "0" ]; then
      echo "  starting XQuartz..."; open -a XQuartz; for _ in $(seq 1 15); do lsof -nP -iTCP:6000 -sTCP:LISTEN >/dev/null 2>&1 && break; sleep 1; done
      DISPLAY=:0 /opt/X11/bin/xhost +localhost >/dev/null 2>&1 || true
    fi
  fi
  if lsof -nP -iTCP:6000 -sTCP:LISTEN >/dev/null 2>&1; then row ok "XQuartz" "listening on TCP 6000 (/magic works; undo access: DISPLAY=:0 /opt/X11/bin/xhost -localhost)"
  else row warn "XQuartz" "not on TCP 6000: one-time setup in the header of scripts/gui/open_gui.sh (Magic only)"; fi
else row warn "XQuartz" "not installed: /magic unavailable (KLayout still works)"; fi
if up "http://127.0.0.1:3000/api/health"; then row ok "Grafana" "http://127.0.0.1:3000 (optional, docs/GRAFANA.md)"
else row warn "Grafana" "not running (optional: brew services start grafana)"; fi

# 9 -------------------------------------------------------------------------------------------- repo tool server
step "9. Repo tool server on $BASE"
if [ ! -x "$PY" ] || ! "$PY" -c "import fastapi, uvicorn" 2>/dev/null; then
  row fail "agent venv" "missing: python3 -m venv build/agent/venv && build/agent/venv/bin/pip install -r tools/requirements.txt"
else
  newest="$(find "$TS" "$REPO/tools/hermes_mcp_bridge.py" "$REPO/tools/eda_tools.py" -name '*.py' -newer "$MARK" 2>/dev/null | head -1)"
  [ -f "$MARK" ] || newest="(no start marker)"
  pid="$(lsof -t -nP -iTCP:$PORT -sTCP:LISTEN 2>/dev/null | head -1)"
  if [ -n "$pid" ] && { [ $RESTART = 1 ] || [ -n "$newest" ]; } && [ $CHECK = 0 ]; then
    running="$(post job_list '{}' 10 | json 'sum(1 for j in d.get("jobs",[]) if j.get("state")=="running")')"
    if [ "${running:-0}" = "0" ]; then
      echo "  restarting the tool server (code changed: ${newest#$REPO/})..."; kill "$pid"; sleep 1; pid=""
    else row warn "tool server restart" "skipped: $running job(s) running; restart later with --restart-server"; fi
  fi
  if [ -z "$pid" ] && [ $CHECK = 0 ]; then
    env -u PYTHONPATH -u PYTHONHOME -u VIRTUAL_ENV CHIP_TOOLS_PORT="$PORT" nohup "$PY" "$TS/tool_server.py" >>"$REPO/build/agent/hermes_plugin_toolserver.log" 2>&1 &
    touch "$MARK"
    wait_for "$BASE/health" 40 || true
  fi
  if up "$BASE/health" 5; then
    H="$(curl -fsS -m 5 $BASE/health)"
    row ok "tool server" "pid $(lsof -t -nP -iTCP:$PORT -sTCP:LISTEN | head -1), klayout $(echo "$H" | json 'd.get("klayout")'), ollama $(echo "$H" | json 'd["ollama"]["reachable"]'), docker $(echo "$H" | json 'd["docker"]["reachable"]')"
  else row fail "tool server" "not answering on $BASE (log: build/agent/hermes_plugin_toolserver.log)"; fi
fi

# 10 ------------------------------------------------------------------------------------------- smoke tests
step "10. Smoke tests (no model involved)"
if up "$BASE/health" 3; then
  t0=$(python3 -c 'import time;print(time.time())')
  r="$(post quick '{"cmd":"timing","args":"kv8"}' 30 | json 'd["reply"].splitlines()[0]')"
  [[ "$r" == *"kv_attn_n8 timing"* ]] && row ok "/timing kv8" "$r" || row fail "/timing kv8" "unexpected: $r"
  r="$(post quick '{"cmd":"timing","args":"audio"}' 30 | json 'd["reply"].splitlines()[0]')"
  [[ "$r" == "Which design?"* ]] && row ok "ambiguous name asks" "$r" || row fail "ambiguous name asks" "unexpected: $r"
  post quick '{"text":"hello"}' 10 >/dev/null   # clears the open question again
  r="$(post quick '{"text":"how was the wrapper hold violation fixed?"}' 60 | json 'd.get("kind")')"
  [ "$r" = "rag" ] && row ok "RAG (why/how)" "quoted answer from the repo" || row warn "RAG (why/how)" "not handled (kind=$r)"
  r="$(post quick '{"cmd":"harness","args":"names"}' 30 | json 'd["reply"].splitlines()[0]')"
  [[ "$r" == *"gate **PASS**"* ]] && row ok "name harness" "${r//\*/}" || row fail "name harness" "$r"
fi
if [ -f "$PROFILE_DIR/config.yaml" ] && command -v hermes >/dev/null; then
  out="$(hermes -p chip plugins list 2>/dev/null | grep 'open-ai-chip' | head -1)"
  [[ "$out" == *enabled* ]] && row ok "Hermes sees the plugin" "open-ai-chip enabled" || row fail "Hermes sees the plugin" "not enabled: re-run the setup with --apply"
  n="$(hermes -p chip mcp test chip 2>&1 | sed -n 's/.*Tools discovered: \([0-9]*\).*/\1/p' | head -1)"
  [ -n "$n" ] && row ok "MCP bridge" "hermes -p chip mcp test chip: $n tools" || row warn "MCP bridge" "hermes mcp test chip did not list tools (log: ~/.hermes/profiles/chip/logs/mcp-stderr.log)"
fi

# 10b ------------------------------------------------------------------------------------------ pinned demo sessions
step "10b. Pinned demo sessions (Demo 1 to 9)"
if [ $DEMOS = 1 ] && [ $CHECK = 0 ] && [ $FAIL = 0 ]; then
  bash "$REPO/scripts/hermes/make_demo_sessions.sh" | sed 's/^/  /'
fi
nd="$(hermes -p chip sessions list 2>/dev/null | grep -c '^Demo [0-9]')"
[ "${nd:-0}" -ge 9 ] && row ok "demo sessions" "$nd pinned 'Demo N' sessions (sidebar: Pinned)" || row warn "demo sessions" "${nd:-0} found: run with --demo-sessions (about 2 minutes)"

# 11 ------------------------------------------------------------------------------------------- Hermes.app
step "11. Hermes.app"
if [ $APP = 1 ] && [ $CHECK = 0 ] && [ -d /Applications/Hermes.app ]; then
  app_pid="$(pgrep -f 'Hermes.app/Contents/MacOS/Hermes$' | head -1)"
  stale=""
  if [ -n "$app_pid" ]; then
    started="$(ps -o lstart= -p "$app_pid" | xargs -0 date -j -f '%a %b %d %T %Y' +%s 2>/dev/null || echo 0)"
    for f in "$REPO"/scripts/hermes/plugin/open-ai-chip/*.py "$PROFILE_DIR/config.yaml"; do
      [ -f "$f" ] && [ "$(stat -f %m "$f")" -gt "${started:-0}" ] && stale="$f"
    done
  fi
  if [ -n "$app_pid" ] && [ -n "$stale" ]; then
    echo "  restarting Hermes.app (it started before ${stale#$HOME/} changed)..."
    osascript -e 'tell application "Hermes" to quit' >/dev/null 2>&1; sleep 4
  fi
  open -a Hermes && row ok "Hermes.app" "$( [ -n "$app_pid" ] && [ -z "$stale" ] && echo 'already running, brought to front' || echo 'started' )"
else row warn "Hermes.app" "not opened (--check or --no-app)"; fi

# summary -------------------------------------------------------------------------------------------------------------
step "Summary"
nok=0; nwarn=0; nfail=0
for r in "${ROWS[@]}"; do case "${r%%|*}" in ok) nok=$((nok+1)) ;; warn) nwarn=$((nwarn+1)) ;; *) nfail=$((nfail+1)) ;; esac; done
echo "  $nok ok, $nwarn warnings (optional parts), $nfail failed"
if [ $FAIL = 0 ]; then
  cat <<EOF

Ready. In Hermes.app: open a pinned "Demo N" session (sidebar: Pinned), or Cmd+N for a new chat and type /chip or /demos.
Try: /klayout kv_attn show only met1   /timing kv8   /search hold violation   /experiment soc-kv   /loopdemo signoff kv
Class script: docs/HERMES_CLASS_SHOWCASE.md
EOF
  exit 0
fi
echo "  Fix the FAIL lines above, then run this script again."
exit 1
