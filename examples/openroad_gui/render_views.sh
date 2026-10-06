#!/usr/bin/env bash
# Render OpenROAD GUI views off-screen (Qt "offscreen" platform, no X server) from a finished run.
# usage: examples/openroad_gui/render_views.sh [design] [run_dir]      (default design: kv_attn_n8)
# Read-only: opens the final ODB inside the LibreLane image; no flow step is run.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
DESIGN="${1:-kv_attn_n8}"
IMAGE="${DOCKER_IMAGE:-ghcr.io/librelane/librelane:3.0.2}"
PDK_ROOT="${PDK_ROOT:-$HOME/.volare}"
TIMEOUT="${GUI_TIMEOUT:-170}"   # a Tcl error leaves the Qt event loop running; always bound the run
export DOCKER_HOST="${DOCKER_HOST:-unix://$HOME/.colima/osl/docker.sock}"

RUN="${2:-}"
if [ -z "$RUN" ]; then
  RUN="$(ls -d "$REPO/designs/$DESIGN"/runs/RUN_*/ 2>/dev/null | while read -r r; do
           [ -f "${r}final/odb/$DESIGN.odb" ] && echo "${r%/}"; done | tail -1)"
fi
[ -n "$RUN" ] && [ -f "$RUN/final/odb/$DESIGN.odb" ] || { echo "no finished run with final/odb/$DESIGN.odb for $DESIGN"; exit 2; }
RUN="$(cd "$RUN" && pwd)"
REL="${RUN#$REPO/}"
SDC="$RUN/final/sdc/$DESIGN.sdc"
CLK="$(sed -n 's/.*create_clock -name \([^ ]*\) .*/\1/p' "$SDC" | head -1)"; CLK="${CLK:-clk}"
LIBF="sky130_fd_sc_hd__tt_025C_1v80.lib"
[ -f "$PDK_ROOT/sky130A/libs.ref/sky130_fd_sc_hd/lib/$LIBF" ] || { echo "missing PDK liberty under $PDK_ROOT"; exit 2; }
OUT="$REPO/build/agent/openroad_gui/$DESIGN"
mkdir -p "$OUT"; rm -f "$OUT"/*.png "$OUT"/*.csv

# one container at a time; --stop-timeout/timeout bound the run
if [ -n "$(docker ps -q 2>/dev/null)" ]; then echo "another container is running; refusing to start a second one"; docker ps; exit 3; fi
docker run --rm -e QT_QPA_PLATFORM=offscreen \
  -e ODB="/w/$REL/final/odb/$DESIGN.odb" \
  -e SPEF="/w/$REL/final/spef/nom/$DESIGN.nom.spef" \
  -e SDC="/w/$REL/final/sdc/$DESIGN.sdc" \
  -e LIB="/pdk/sky130A/libs.ref/sky130_fd_sc_hd/lib/$LIBF" \
  -e OUT="/w/build/agent/openroad_gui/$DESIGN" -e CLK="$CLK" -e WIDTH="${WIDTH:-1100}" \
  -v "$REPO:/w" -v "$PDK_ROOT:/pdk:ro" "$IMAGE" \
  sh -c "timeout $TIMEOUT openroad -no_splash -gui /w/examples/openroad_gui/views.tcl 2>&1" | tee "$OUT/render.log" | grep -E "^(VIEW|design|spef|clock|worst|path|\[ERROR|\[WARNING GUI)" 
echo "images: $OUT"; ls -la "$OUT"/*.png 2>/dev/null
