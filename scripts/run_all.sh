#!/usr/bin/env bash
# run_all.sh -- run everything of this repo from a macOS or Linux terminal, in order, with one PASS/FAIL/SKIP line per
# stage and a log per stage under build/run_all/<timestamp>/. Guides: docs/RUN_ON_MAC.md, docs/RUN_ON_LINUX.md.
# (scripts/run_all_mac.sh is a thin wrapper that calls this script.)
#
#   bash scripts/run_all.sh                     # verify everything without re-running physical flows (~10 min)
#   bash scripts/run_all.sh --all               # plus flows, precheck, Hermes agents and the GUI windows
#   options: --flows     make all-designs (reuses current runs; a stale design is re-hardened, ~34 min if all are)
#            --precheck  ChipFoundry precheck, 14 checks (~1 min)      --fullgl  full-chip gate-level Caravel (~14 min)
#            --agents    Hermes live (needs Ollama + hermes3:8b)       --gui     open KLayout, OpenROAD heat maps, Magic
#            --design D  design for the pictures and windows (default kv_attn_n8)
#            --keep-going  continue after a failed stage (default: stop at the first failure)
#            --no-docker   host without Docker: only make test, the plumbing dry run and the tools pytest (Docker stages SKIP)
# macOS: Colima profile osl, XQuartz, /Applications KLayout. Linux: DOCKER_HOST unix:///var/run/docker.sock, DISPLAY + X
# socket, `klayout` on PATH; the run is wrapped in systemd-inhibit (no suspend) when it is available.
# Nothing here publishes or uploads anything; physical flows run one at a time (as the Makefile does).
# Docs: docs/RUN_ON_MAC.md, docs/RUN_ON_LINUX.md
set -uo pipefail
cd "$(dirname "$0")/.."
FLOWS=0 PRECHECK=0 FULLGL=0 AGENTS=0 GUI=0 KEEP=0 NODOCKER=0 D=kv_attn_n8
ORIG_ARGS=("$@")
OS=$(uname -s)
while [ $# -gt 0 ]; do
  case "$1" in
    --all) FLOWS=1 PRECHECK=1 AGENTS=1 GUI=1 ;;
    --flows) FLOWS=1 ;; --precheck) PRECHECK=1 ;; --fullgl) FULLGL=1 ;; --agents) AGENTS=1 ;; --gui) GUI=1 ;;
    --keep-going) KEEP=1 ;; --no-docker) NODOCKER=1 ;; --design) [ $# -ge 2 ] || { echo "--design needs a name"; exit 2; }; D=$2; shift ;;
    -h|--help) sed -n 2,17p "$0"; exit 0 ;;
    *) echo "unknown option $1"; exit 2 ;;
  esac
  shift
done
if [ "$OS" = Darwin ]; then export DOCKER_HOST=${DOCKER_HOST:-unix://$HOME/.colima/osl/docker.sock}
else
  export DOCKER_HOST=${DOCKER_HOST:-unix:///var/run/docker.sock}
  # keep the machine awake during long runs (the Mac equivalent is `caffeinate -dimsu`)
  if [ -z "${RUN_ALL_INHIBITED:-}" ] && command -v systemd-inhibit >/dev/null 2>&1 && systemd-inhibit --what=sleep true >/dev/null 2>&1; then
    export RUN_ALL_INHIBITED=1
    exec systemd-inhibit --what=idle:sleep --who=open-ai-chip --why="run_all.sh" bash "$0" "${ORIG_ARGS[@]}"
  fi
fi
PY=build/agent/venv/bin/python
LOG=build/run_all/$(date +%Y%m%d_%H%M%S); mkdir -p "$LOG"
T0=$(date +%s); NPASS=0; NFAIL=0; NSKIP=0; SUMMARY=""

line() { printf '%-5s %-42s %6ss  %s\n' "$1" "$2" "$3" "$4"; SUMMARY+=$(printf '%-5s %-42s %6ss  %s' "$1" "$2" "$3" "$4")$'\n'; }
skip() { line SKIP "$1" 0 "$2"; NSKIP=$((NSKIP + 1)); }
stage() {   # stage <name> <command...>
  local name=$1; shift
  local f="$LOG/$(echo "$name" | tr ' /:' '___').log" s=$(date +%s)
  if "$@" >"$f" 2>&1; then line PASS "$name" $(( $(date +%s) - s )) "$f"; NPASS=$((NPASS + 1))
  else
    line FAIL "$name" $(( $(date +%s) - s )) "$f (tail: $(tail -1 "$f" | cut -c1-60))"; NFAIL=$((NFAIL + 1))
    [ "$KEEP" = 1 ] || { echo; echo "stopped at the first failure; rerun with --keep-going to continue"; finish; }
  fi
}
finish() {
  echo; echo "== summary ($(( ($(date +%s) - T0) / 60 )) min, logs in $LOG)"; printf '%s' "$SUMMARY"
  echo "PASS $NPASS  FAIL $NFAIL  SKIP $NSKIP"; printf '%s' "$SUMMARY" > "$LOG/summary.txt"
  [ "$NFAIL" = 0 ]; exit $?
}

echo "== run_all ($OS): logs in $LOG"
# ---- 0. the machine
if [ "$NODOCKER" = 1 ]; then skip "preflight: Docker, make doctor" "--no-docker"
else
  [ "$OS" = Darwin ] && stage "preflight: Colima VM osl running" sh -c 'colima status -p osl 2>&1 | grep -q running || colima start -p osl'
  stage "preflight: make doctor"            make doctor
fi
stage "preflight: agent venv"             test -x "$PY"
OLLAMA=0; curl -s --max-time 3 localhost:11434/api/tags | grep -q hermes3 && OLLAMA=1
XQ=0
if [ "$OS" = Darwin ]; then lsof -nP -iTCP:6000 -sTCP:LISTEN >/dev/null 2>&1 && XQ=1
else   # Linux: an X server (native, or XWayland) reachable through DISPLAY and its unix socket
  n=${DISPLAY:-}; n=${n#*:}; n=${n%%.*}
  [ -n "${DISPLAY:-}" ] && [ -S "/tmp/.X11-unix/X$n" ] && XQ=1
fi
if [ "$OS" = Darwin ]; then KL=/Applications/KLayout/klayout.app/Contents/MacOS/klayout; else KL=$(command -v klayout || true); fi
KL=${KLAYOUT_BIN:-$KL}
# ---- 1. fast gate (structure, configs, lint, models, sims, negative tests, docs, tools pytest)
stage "make test"                          make test
# ---- 2. physical flows (opt-in): all 25 designs; current runs are reused, stale ones re-hardened
if [ "$NODOCKER" = 1 ]; then skip "make all-designs" "--no-docker"
elif [ "$FLOWS" = 1 ]; then stage "make all-designs (flows, reuse current runs)" make all-designs
else skip "make all-designs" "add --flows (re-hardens only stale designs; all from scratch ~34 min)"; fi
# ---- 3. heavy local checks without flows: every design simulate/check/gl-final, adapter, SoC, Caravel RTL/GL
TF=""; [ "$PRECHECK" = 1 ] && TF="$TF --precheck"; [ "$FULLGL" = 1 ] && TF="$TF --fullgl"
if [ "$NODOCKER" = 1 ]; then skip "make test-full" "--no-docker"; else stage "make test-full$TF" make test-full FLAGS="$TF"; fi
# ---- 4. pictures without a window
if [ "$NODOCKER" = 1 ]; then skip "OpenROAD engine views offscreen ($D)" "--no-docker"
elif [ -n "$(docker ps -q 2>/dev/null)" ]; then   # render_views.sh refuses to run beside another container (often an open GUI window)
  skip "OpenROAD engine views offscreen ($D)" "a container is running (an open OpenROAD/Magic window?): close it, rerun"
else stage "OpenROAD engine views offscreen ($D)"  bash examples/openroad_gui/render_views.sh "$D"; fi
stage "KLayout + Hermes plumbing (dry run)"   "$PY" examples/hermes_klayout_gui/demo.py --dry-run
stage "agent/tools pytest"                    "$PY" -m pytest -q tests/tools
# ---- 5. Hermes agents with the local model (opt-in)
if [ "$AGENTS" = 1 ] && [ "$OLLAMA" = 1 ]; then
  stage "Hermes live smoke tests (6 agents)"  env HERMES_LIVE=1 "$PY" -m pytest -q tests/tools/test_live_smoke.py
  stage "Hermes chip Q&A"                     "$PY" tools/hermes_agent.py "How many standard cells does vision_block have?"
  stage "Hermes KLayout demo, offscreen"      "$PY" examples/hermes_klayout_gui/demo.py --live
elif [ "$AGENTS" = 1 ]; then skip "Hermes agents" "Ollama with hermes3:8b is not answering (ollama serve &)"
else skip "Hermes agents" "add --agents"; fi
# ---- 6. GUI windows (opt-in): they open on this screen; close each to continue where noted
if [ "$GUI" = 1 ]; then
  if [ -n "$KL" ] && [ -x "$KL" ]; then
    stage "KLayout live window tests"         env KLAYOUT_LIVE=1 "$PY" -m pytest -q tests/tools/test_klayout_live.py
  else skip "KLayout live window" "KLayout not installed (macOS: brew install --cask klayout; Linux: apt install klayout)"; fi
  if [ "$NODOCKER" = 1 ]; then skip "Magic / OpenROAD windows" "--no-docker"
  elif [ "$XQ" = 1 ]; then
    stage "Magic + OpenROAD windows (XQuartz)" env OPEN_GUI_LIVE=1 "$PY" -m pytest -q tests/tools/test_open_gui.py
    echo "     opening the OpenROAD heat-map window for $D (1 round; close it to finish)"
    stage "OpenROAD live heat maps ($D)"      env ROUNDS=1 bash scripts/gui/open_gui.sh heatmaps "$D"
  elif [ "$OS" = Darwin ]; then skip "Magic / OpenROAD windows" "XQuartz not listening on TCP 6000 (setup: docs/GUI_AND_LOGS.md section 3)"
  else skip "Magic / OpenROAD windows" "no X server: need DISPLAY and /tmp/.X11-unix/X<n>, then xhost +SI:localuser:root (docs/RUN_ON_LINUX.md)"; fi
else skip "GUI windows" "add --gui"; fi
finish
