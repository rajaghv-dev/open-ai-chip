#!/usr/bin/env bash
# Docs: .claude/skills/whatif-experiment/SKILL.md, docs/HERMES_DESKTOP.md
# whatif_flow.sh -- one LibreLane run on a COPY of a design (build/whatif/<design>__<tag>/), never on designs/<design>/.
#
# Usage: scripts/flow/whatif_flow.sh --dir build/whatif/<design>__<tag> --design <design> --tag <tag> [--profile tight|actions] [--cpuset 0-1]
#
# The copy is made by examples/hermes_desktop/tool_server/whatif_tools.py (config.json already carries the what-if changes and
# every dir:: path rewritten). This script uses the same container setup as the Makefile (run_librelane: Docker image from
# versions.lock, --cpus/--memory/--cpuset by PROFILE, the home directory and the repo mounted, PDK_ROOT, `--manual-pdk`), names
# the container oac_whatif_<design>_<tag>, caps the run with FLOW_TIMEOUT (default 600 s), then writes <dir>/whatif_resources.json
# (exit code, wall seconds, container peak memory) and runs scripts/flow/check_signoff.py on the run's metrics.json
# (output <dir>/signoff.txt). Run directory: <dir>/runs/<tag>/. Honors DOCKER_HOST (Colima socket when it exists).
# One physical flow at a time: check `docker ps` first.
set -uo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"
DIR=""; DESIGN=""; TAG=""; PROFILE="tight"; CPUSET=""
while [ $# -gt 0 ]; do
  case "$1" in
    --dir) DIR="$2"; shift 2 ;; --design) DESIGN="$2"; shift 2 ;; --tag) TAG="$2"; shift 2 ;;
    --profile) PROFILE="$2"; shift 2 ;; --cpuset) CPUSET="$2"; shift 2 ;;
    -h|--help) sed -n 2,16p "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
[ -n "$DIR" ] && [ -n "$DESIGN" ] && [ -n "$TAG" ] || { echo "usage: $0 --dir <copy> --design <d> --tag <t>" >&2; exit 2; }
case "$DIR" in build/whatif/*) ;; *) echo "refusing: --dir must be under build/whatif/ (got $DIR)" >&2; exit 2 ;; esac
[ -f "$DIR/config.json" ] || { echo "no $DIR/config.json" >&2; exit 2; }
case "$PROFILE" in tight) CPUS=2; MEM=8g; : "${CPUSET:=0-1}" ;; actions) CPUS=4; MEM=16g; : "${CPUSET:=0-3}" ;; *) echo "profile must be tight or actions" >&2; exit 2 ;; esac
if [ -z "${DOCKER_HOST:-}" ] && [ -S "$HOME/.colima/osl/docker.sock" ]; then export DOCKER_HOST="unix://$HOME/.colima/osl/docker.sock"; fi
IMAGE="$(sed -n 's/^LIBRELANE_IMAGE=//p' versions.lock | head -1 | tr -d ' ')"
PDK_ROOT="${PDK_ROOT:-$HOME/.volare}"
CNAME="oac_whatif_${DESIGN}_${TAG}"
FLOW_TIMEOUT="${FLOW_TIMEOUT:-600}"
WORK="$(mktemp -d)"
cleanup() { docker rm -f "$CNAME" >/dev/null 2>&1 || true; rm -rf "$WORK"; }
trap cleanup EXIT
trap 'echo "whatif_flow: terminated" >&2; cleanup; exit 143' TERM INT
T0=$(date +%s)
docker run --rm -i --cpus=$CPUS --memory=$MEM --memory-swap=$MEM --cpuset-cpus="$CPUSET" --name "$CNAME" \
  -v "$HOME:$HOME" -v "$REPO_ROOT:$REPO_ROOT" -e PDK_ROOT="$PDK_ROOT" -e PDK=sky130A -w "$REPO_ROOT" "$IMAGE" \
  python3 -m librelane --manual-pdk --pdk-root "$PDK_ROOT" --design-dir "$REPO_ROOT/$DIR" \
    -c DRT_THREADS=$CPUS -c KLAYOUT_DRC_THREADS=$CPUS -c KLAYOUT_XOR_THREADS=$CPUS \
    --run-tag "$TAG" "$REPO_ROOT/$DIR/config.json" &
FPID=$!
: > "$WORK/peak.txt"
while kill -0 "$FPID" 2>/dev/null; do
  if [ $(( $(date +%s) - T0 )) -gt "$FLOW_TIMEOUT" ]; then
    echo "whatif_flow: TIMEOUT after ${FLOW_TIMEOUT} s: stopping container $CNAME" >&2
    docker rm -f "$CNAME" >/dev/null 2>&1; kill "$FPID" 2>/dev/null; break
  fi
  docker exec "$CNAME" sh -c 'cat /sys/fs/cgroup/memory.peak 2>/dev/null || cat /sys/fs/cgroup/memory/memory.max_usage_in_bytes 2>/dev/null' >> "$WORK/peak.txt" 2>/dev/null
  sleep 2
done
wait "$FPID"; RC=$?
T1=$(date +%s)
PEAK=$(sort -n "$WORK/peak.txt" | tail -1)
python3 - "$DIR/whatif_resources.json" "$DESIGN" "$TAG" "$RC" "$((T1-T0))" "${PEAK:-0}" "$PROFILE" <<'PY'
import json, sys
dst, design, tag, rc, wall, peak, profile = sys.argv[1:8]
json.dump({"design": design, "tag": tag, "profile": profile, "exit_code": int(rc), "wall_s_total": int(wall),
           "container_peak_mem_gb": round(int(peak or 0) / 1e9, 3)}, open(dst, "w"), indent=1)
PY
M="$DIR/runs/$TAG/final/metrics.json"
if [ "$RC" -eq 0 ] && [ -f "$M" ]; then
  python3 scripts/flow/check_signoff.py "$DESIGN" --metrics "$M" > "$DIR/signoff.txt" 2>&1
  echo "whatif_flow: check_signoff exit $? (see $DIR/signoff.txt)"
fi
echo "whatif_flow: librelane exit $RC after $((T1-T0)) s"
exit "$RC"
