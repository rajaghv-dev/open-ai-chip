#!/usr/bin/env bash
# Docs: hermes-agents.md, docs/HERMES_CLASS_SHOWCASE.md
# make_demo_sessions.sh -- create one pinned Hermes session per demo card in the "chip" profile, titled "Demo N: <title>", so a
# class demo starts by picking the right session in Hermes.app's sidebar (Pinned). Each session holds the card: the commands to
# type, sentences to try, and what to say (quick_tools.DEMO_CARDS, the same text as `/demo <name>`).
#   bash scripts/hermes/make_demo_sessions.sh            (re)create all nine (about 10 to 60 s each: the model ends each turn)
#   bash scripts/hermes/make_demo_sessions.sh layout     only the named card(s)
# Rerun-safe: sessions this script made before (ids in build/agent/demo_sessions.json) are deleted first; no other session is
# touched. Needs the tool server and the chip profile: run bash scripts/hermes_start.sh first.
set -uo pipefail
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH"
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
STORE="$REPO/build/agent/demo_sessions.json"
CARDS=(tour search layout names signoff experiments whatif agent model)
[ $# -gt 0 ] && CARDS=("$@")
mkdir -p "$REPO/build/agent"
curl -fsS -m 3 http://127.0.0.1:8770/health >/dev/null 2>&1 || { echo "tool server not running: bash scripts/hermes_start.sh --no-app first"; exit 1; }
[ -f "$HOME/.hermes/profiles/chip/config.yaml" ] || { echo "profile chip missing: bash scripts/hermes_agent_setup.sh --apply"; exit 1; }

# remove earlier demo sessions made as source "oneshot" (invisible in the app's sidebar): pinned, titled "/demo ..." or "Demo N: ..."
hermes -p chip sessions pinned 2>/dev/null | awk '/oneshot/ && ($1=="/demo" || $1=="Demo") {print $NF}' | while read -r sid; do
  hermes -p chip sessions delete "$sid" --yes >/dev/null 2>&1 && echo "deleted hidden (oneshot) $sid"
done

# delete the sessions this script created before (only those ids), for the cards being rebuilt
if [ -f "$STORE" ]; then
  python3 - "$STORE" "${CARDS[@]}" <<'E' | while read -r sid; do hermes -p chip sessions delete "$sid" --yes >/dev/null 2>&1 && echo "deleted old $sid"; done
import json, sys
d = json.load(open(sys.argv[1]))
for k in sys.argv[2:]:
    if d.get(k):
        print(d[k])
E
fi

TITLES=()   # "sid|title" of the sessions made in this run; renamed at the end (a one-shot run finishes its own title write
            # after it returns, which overwrote an immediate rename)

for key in "${CARDS[@]}"; do
  t0=$(date +%s)
  # --source cli: Hermes.app's sidebar hides source "oneshot" sessions, even pinned ones (SIDEBAR_EXCLUDED_SOURCES)
  out="$(cd "$REPO" && hermes -p chip chat -q "/demo $key" --oneshot --source cli --pass-session-id --in "$REPO" 2>&1 | tr -d '\r')"   # CR in titles is refused
  sid="$(echo "$out" | sed -n 's/^Session: *\([0-9_a-f]*\).*/\1/p' | tail -1)"
  title="$(echo "$out" | sed -n 's/^## \(Demo [0-9]*: .*\)$/\1/p' | head -1)"
  if [ -z "$sid" ] || [ -z "$title" ]; then echo "FAILED $key (no session id or card in the output)"; continue; fi
  hermes -p chip sessions pin "$sid" >/dev/null 2>&1
  TITLES+=("$sid|$title")
  python3 - "$STORE" "$key" "$sid" <<'E'
import json, os, sys
p, k, sid = sys.argv[1:4]
d = json.load(open(p)) if os.path.exists(p) else {}
d[k] = sid
json.dump(d, open(p, "w"), indent=1)
E
  echo "made $sid for '$key' ($(( $(date +%s)-t0 )) s)"
done

sleep 5
for pass in 1 2 3; do
  missing=0
  pinned="$(hermes -p chip sessions pinned 2>/dev/null)"
  for st in "${TITLES[@]}"; do
    sid="${st%%|*}"; title="${st#*|}"
    if ! echo "$pinned" | grep "$sid" | grep -q "^Demo "; then
      hermes -p chip sessions rename "$sid" "$title" >/dev/null 2>&1 || echo "  rename failed: $sid"; missing=$((missing+1))
    fi
  done
  [ $missing = 0 ] && break
  sleep 3
done
hermes -p chip sessions pinned 2>/dev/null | grep '^Demo ' | sed 's/^/ok  /'
echo
echo "In Hermes.app: the sidebar's Pinned section lists the Demo sessions; open one and type its commands."
