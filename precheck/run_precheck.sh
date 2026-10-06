#!/usr/bin/env bash
# One command: stage the project and run the local cf-precheck 1.3.7 (all checks incl. Magic DRC and LVS) in the osl-precheck image.
# Local only: no cf login/init/push/submit, no uploads. Needs Colima/docker (DOCKER_HOST) and the sky130A PDK in ~/.volare.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export DOCKER_HOST="${DOCKER_HOST:-unix://$HOME/.colima/osl/docker.sock}"
IMAGE="${PRECHECK_IMAGE:-osl-precheck:nix-klayout}"
PDK_ROOT="${PDK_ROOT:-$ROOT/build/precheck/pdk_cf}"   # PDK_ROOT=$HOME/.volare to use the LibreLane-pinned 8afc8346 PDK instead
[ -d "$PDK_ROOT/sky130A" ] || "$ROOT/precheck/fetch_pdk.sh"
docker image inspect "$IMAGE" >/dev/null 2>&1 || docker build -f "$ROOT/precheck/docker/Dockerfile.nixklayout" -t "$IMAGE" "$ROOT/precheck/docker"
PRECHECK_PROJECT="${PRECHECK_PROJECT:-$ROOT/build/precheck/project}" "$ROOT/precheck/stage_project.sh"
mkdir -p "${PRECHECK_PROJECT:-$ROOT/build/precheck/project}"; PROJ="$(cd "${PRECHECK_PROJECT:-$ROOT/build/precheck/project}" && pwd)"
OUT="$ROOT/build/precheck/results_${RUN_TAG:-}$(date +%Y%m%d_%H%M%S)"
mkdir -p "$OUT"
START=$SECONDS
CHECKS="${*:-topcell_check gpio_defines xor magic_drc klayout_feol klayout_beol klayout_offgrid klayout_met_min_ca_density klayout_pin_label_purposes_overlapping_drawing klayout_zeroarea spike_check illegal_cellname_check lvs oeb}"
CAP="${CHECK_TIMEOUT_S:-2700}"   # per-check wall cap (45 min); a check that hits it is reported NOT RUN, the others continue
: > "$OUT/summary.tsv"
for c in $CHECKS; do
  t0=$SECONDS
  # --magic-drc is needed for magic_drc and harmless for the others (it only adds the optional check to the default sequence)
  rc=0
  docker run --rm -v "$ROOT/build/precheck":"$ROOT/build/precheck" -v "$PDK_ROOT":"$PDK_ROOT":ro -e PDK_ROOT="$PDK_ROOT" \
    -w "$ROOT/build/precheck" "$IMAGE" timeout "$CAP" \
    cf-precheck -i "$PROJ" -p "$PDK_ROOT/sky130A" -c "$ROOT/build/precheck/caravel_golden" -o "$OUT/$c" --magic-drc -v "$c" \
    > "$OUT/$c.console.log" 2>&1 || rc=$?
  case $rc in 0) st=PASS;; 124) st="NOT RUN (timeout ${CAP}s)";; *) st=FAIL;; esac
  printf '%s\t%s\t%ss\n' "$c" "$st" "$((SECONDS-t0))" | tee -a "$OUT/summary.tsv"
done
echo "total wall time: $((SECONDS-START)) s ; results: $OUT"
