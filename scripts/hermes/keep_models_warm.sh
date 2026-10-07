#!/usr/bin/env bash
# Docs: hermes-agents.md, docs/HERMES_CLASS_SHOWCASE.md
# keep_models_warm.sh -- keep the Hermes models loaded in Ollama while Hermes.app runs, then unload them.
#   bash scripts/hermes/keep_models_warm.sh            (started in the background by scripts/hermes_start.sh)
#   bash scripts/hermes/keep_models_warm.sh --stop     stop the watcher and unload the models now
#   bash scripts/hermes/keep_models_warm.sh --status   is it running, and what Ollama has loaded
# Why a watcher: Ollama unloads a model after its keep_alive, and every request resets that timer to the request's own value
# (Hermes's chat requests use Ollama's default, 5 minutes; the RAG embeds use 30 minutes). Pinning once with keep_alive -1
# is therefore not enough: this loop re-pins every PERIOD seconds (default 120) while Hermes.app is running, so the first
# answer after a pause is never a cold load, and unloads both models (keep_alive 0) when the app quits.
# Models: qwen3.5-64k:9b (its Modelfile sets num_ctx 65536, the context Hermes asks for, so the pinned copy is the one Hermes
# uses) and qwen3-embedding:0.6b (RAG). Override with HERMES_WARM_MODELS="m1 m2" and RAG_EMBED_MODEL.
set -uo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
OLLAMA="http://127.0.0.1:11434"
CHAT="${HERMES_WARM_MODELS:-qwen3.5-64k:9b}"
EMBED="${RAG_EMBED_MODEL:-qwen3-embedding:0.6b}"
PERIOD="${HERMES_WARM_PERIOD:-120}"
PIDF="$REPO/build/agent/keep_models_warm.pid"
LOG="$REPO/build/agent/keep_models_warm.log"
mkdir -p "$REPO/build/agent"

app_running() { pgrep -f 'Hermes.app/Contents/MacOS/Hermes$' >/dev/null 2>&1; }
log() { echo "$(date '+%F %T') $*" >> "$LOG"; }
pin() {   # $1 = keep_alive value (-1 = until unloaded, 0 = unload now)
  # a one-token generation: an empty "load" request does not refresh the timer of an already-loaded model (Ollama 0.35.1);
  # warm, it costs about 0.14 s
  for m in $CHAT; do
    if [ "$1" = 0 ]; then body="{\"model\":\"$m\",\"keep_alive\":0}"
    else body="{\"model\":\"$m\",\"prompt\":\"ok\",\"stream\":false,\"think\":false,\"keep_alive\":$1,\"options\":{\"num_predict\":1}}"; fi
    curl -fsS -m 300 "$OLLAMA/api/generate" -d "$body" >/dev/null 2>&1 || log "pin $m $1 failed"
  done
  curl -fsS -m 120 "$OLLAMA/api/embed" -d "{\"model\":\"$EMBED\",\"input\":\"warm\",\"keep_alive\":$1}" >/dev/null 2>&1 || log "pin $EMBED $1 failed"
}
running_pid() { [ -f "$PIDF" ] && kill -0 "$(cat "$PIDF")" 2>/dev/null && cat "$PIDF"; }

case "${1:-}" in
  --stop)
    p="$(running_pid)"; [ -n "$p" ] && kill "$p" && echo "watcher $p stopped"
    pin 0; rm -f "$PIDF"; echo "models unloaded"; exit 0 ;;
  --status)
    p="$(running_pid)"; if [ -n "$p" ]; then echo "watcher: running (pid $p)"; else echo "watcher: not running"; fi
    curl -fsS -m 5 "$OLLAMA/api/ps" | python3 -c "import sys,json
for m in json.load(sys.stdin)['models']: print('  loaded: %s  ctx %s  %.1f GB  until %s' % (m['name'], m.get('context_length'), m.get('size_vram',0)/1e9, m.get('expires_at','')[:19]))" 2>/dev/null
    exit 0 ;;
  -h|--help) sed -n 2,6p "$0"; exit 0 ;;
  "") ;;
  *) echo "usage: $0 [--stop|--status]" >&2; exit 2 ;;
esac

if p="$(running_pid)"; then echo "already running (pid $p)"; exit 0; fi
echo $$ > "$PIDF"
trap 'rm -f "$PIDF"' EXIT
log "start: pin $CHAT $EMBED every ${PERIOD}s while Hermes.app runs"
pin -1
for _ in $(seq 1 60); do app_running && break; sleep 2; done      # wait up to 2 min for the app to appear
while app_running; do
  sleep "$PERIOD"
  app_running && pin -1
done
log "Hermes.app closed: unloading"
pin 0
