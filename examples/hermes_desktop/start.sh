#!/usr/bin/env bash
# Start Ollama (if needed), the chip tool server (127.0.0.1:8770) and Open WebUI (127.0.0.1:8080).
# Logs: build/webui/logs. Pid files: build/webui/pids (stop.sh uses them).
# Docs: examples/hermes_desktop/README.md, docs/HERMES_DESKTOP.md
set -uo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:$PATH"   # apps started from Finder have a minimal PATH
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
B="$REPO/build/webui"; L="$B/logs"; P="$B/pids"; D="$B/data"
mkdir -p "$L" "$P" "$D"
AGENT_PY="$REPO/build/agent/venv/bin/python"
WEBUI="$B/venv/bin/open-webui"
[ -x "$WEBUI" ] || { echo "Open WebUI not installed: run bash examples/hermes_desktop/setup_webui.sh" >&2; exit 1; }
up() { curl -fsS -m 3 "$1" >/dev/null 2>&1; }
wait_for() {  # url name seconds
  for _ in $(seq 1 "$3"); do up "$1" && return 0; sleep 1; done
  echo "TIMEOUT waiting for $2 ($1); see $L" >&2; return 1
}
# Background a service and record its pid so stop.sh kills only what this script started.
start() {  # name logfile cmd...
  local n="$1" lg="$2"; shift 2
  nohup "$@" >>"$lg" 2>&1 &
  echo $! >"$P/$n.pid"
}

# 1. Ollama
if up http://127.0.0.1:11434/api/tags; then echo "ollama: already running"
else
  OLL="$(command -v ollama || echo /usr/local/bin/ollama)"
  start ollama "$L/ollama.log" "$OLL" serve
  wait_for http://127.0.0.1:11434/api/tags ollama 60 || exit 1
fi

# 2. chip tool server
TS="$REPO/examples/hermes_desktop/tool_server/tool_server.py"
if up http://127.0.0.1:8770/health; then echo "tool server: already running"
else
  [ -f "$TS" ] || { echo "missing $TS" >&2; exit 1; }
  start tool_server "$L/tool_server.log" "$AGENT_PY" "$TS"
  wait_for http://127.0.0.1:8770/health "tool server" 60 || exit 1
fi

# (Everything below is privacy/offline hardening: no auth screen because it is single-user on 127.0.0.1, no telemetry,
#  no model or update downloads, config comes only from these env vars so reruns are reproducible.)
# 3. Open WebUI (single local user, telemetry off, no model downloads)
if up http://127.0.0.1:8080/health; then echo "open webui: already running"
else
  [ -f "$B/secret.key" ] || { umask 077; head -c 24 /dev/urandom | base64 >"$B/secret.key"; }
  export DATA_DIR="$D" WEBUI_AUTH=False WEBUI_SECRET_KEY="$(cat "$B/secret.key")"
  export HOST=127.0.0.1 PORT=8080
  export OLLAMA_BASE_URL=http://127.0.0.1:11434 ENABLE_OPENAI_API=False
  export ANONYMIZED_TELEMETRY=false DO_NOT_TRACK=true SCARF_NO_ANALYTICS=true
  export OFFLINE_MODE=True HF_HUB_OFFLINE=1 ENABLE_VERSION_UPDATE_CHECK=False ENABLE_COMMUNITY_SHARING=False
  export RAG_EMBEDDING_MODEL_AUTO_UPDATE=False RAG_RERANKING_MODEL_AUTO_UPDATE=False
  export ENABLE_PERSISTENT_CONFIG=False
  # Lock-down (scoped to this repo; audited by audit_config.py). The preset is the default and, via install_preset.py,
  # the only listed model; no code execution, web search, image generation, direct connections, signup or sharing.
  export DEFAULT_MODELS=hermes-chip-agent ENABLE_SIGNUP=False
  export ENABLE_CODE_EXECUTION=False ENABLE_CODE_INTERPRETER=False ENABLE_WEB_SEARCH=False ENABLE_IMAGE_GENERATION=False
  export ENABLE_DIRECT_CONNECTIONS=False ENABLE_EVALUATION_ARENA_MODELS=False ENABLE_CHANNELS=False ENABLE_API_KEYS=False
  export ENABLE_USER_WEBHOOKS=False ENABLE_MEMORIES=False
  export USER_PERMISSIONS_FEATURES_CODE_INTERPRETER=False USER_PERMISSIONS_FEATURES_WEB_SEARCH=False
  export USER_PERMISSIONS_FEATURES_IMAGE_GENERATION=False USER_PERMISSIONS_FEATURES_DIRECT_TOOL_SERVERS=False
  export TOOL_SERVER_CONNECTIONS="$(cat "$REPO/examples/hermes_desktop/tool_server_connection.json")"
  export TOOLS_FUNCTION_CALLING_PROMPT_TEMPLATE="$(cat "$REPO/examples/hermes_desktop/tools_prompt.txt")"
  export DEFAULT_PROMPT_SUGGESTIONS="$(cat "$REPO/examples/hermes_desktop/prompt_suggestions.json")"
  export USER_AGENT=hermes-chip-agent
  start webui "$L/webui.log" "$WEBUI" serve --host 127.0.0.1 --port 8080
  wait_for http://127.0.0.1:8080/health "open webui" 240 || exit 1
fi

# (install_preset.py talks to the running WebUI, so it must come after step 3)
# 4. model preset (idempotent); the preset system prompt starts with the generated master prompt, so refresh it first
python3 "$REPO/scripts/docs/make_master_prompt.py" >/dev/null || echo "master prompt generation failed (see above)" >&2
"$AGENT_PY" "$REPO/examples/hermes_desktop/install_preset.py" || echo "preset install failed (see above)" >&2

python3 "$REPO/examples/hermes_desktop/install_prompts.py" >/dev/null || echo "prompt install failed" >&2
# 5. config audit (warning only here; scripts/hermes.sh restarts once on drift)
"$AGENT_PY" "$REPO/examples/hermes_desktop/audit_config.py" | tail -1 || echo "config audit: DRIFT (run audit_config.py)" >&2
echo "Open WebUI : http://127.0.0.1:8080/?models=hermes-chip-agent   (model: Hermes chip agent)"
echo "Tool server: http://127.0.0.1:8770/docs"
echo "Logs       : $L"
