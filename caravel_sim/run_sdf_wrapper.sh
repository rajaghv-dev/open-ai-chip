#!/usr/bin/env bash
# Purpose: Gate-level plus SDF simulation of our wrapper and macro only (Open Verilog CVC in an amd64 container).
# Run: make caravel-sdf-wrapper.
# In: wrapper run pnl and SDF, tiny_ai_wrapper_sdf_tb.v. Out: build/caravel/work/ logs.
# Docs: docs/CARAVEL_SIM.md, caravel_sim/README.md
# Gate-level + SDF of OUR blocks only (user_project_wrapper + tiny_ai_core), Wishbone testbench, Open Verilog CVC (cvc64, x86_64,
# run in an amd64 container: the only SDF-capable open simulator; iverilog's $sdf_annotate is unusable). Takes ~1-2 minutes.
#   CLK_HALF=<ns half period> (default 12.5 = 40 MHz) for a too-fast-clock negative check.
#   CORNER=nom_tt_025C_1v80 (default) | nom_ss_100C_1v60 | nom_ff_n40C_1v95 | max_ss_100C_1v60 | min_ff_n40C_1v95 ...
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; ROOT="$(cd "$HERE/.." && pwd)"
B="$ROOT/build/caravel"; W="$B/work"; mkdir -p "$W"; cd "$W"
VP="$B/mgmt_core_wrapper/verilog"; CVC="$B/cvc_src/build64/cvc64"; CORNER="${CORNER:-nom_tt_025C_1v80}"
. "$ROOT/scripts/lib/common.sh"; oac_docker_host; export PDK_ROOT="${PDK_ROOT:-$HOME/.volare}"
WRAP_RUN="${WRAP_RUN:-$(dirname "$(dirname "$(dirname "$(dirname "$(ls -t "$ROOT"/designs/user_project_wrapper/runs/*/final/sdf/$CORNER/*.sdf | head -1)")")")")}"
WRAP="$WRAP_RUN/final/pnl/user_project_wrapper.pnl.v"; WSDF="$WRAP_RUN/final/sdf/$CORNER/user_project_wrapper__$CORNER.sdf"
MACRO="${MACRO_PNL:-$ROOT/build/macros/tiny_ai_core/pnl/tiny_ai_core.pnl.v}"
[ -s "$WRAP" ] && [ -s "$WSDF" ] && [ -x "$CVC" ] || { echo "missing $WRAP / $WSDF / $CVC" >&2; exit 2; }
mkdir -p cvc-pdk   # CVC 7.00b lexer fixes: UDP edge '(0x)' in primitives, '@(*)' in cell models
sed -E 's/\(([01xXbB?])([01xXbB?])\)/(\1 \2)/g' "$VP/cvc-pdk/primitives_hd.v" > cvc-pdk/primitives_hd.v
sed -E 's/@\(\*\)/@*/g' "$VP/cvc-pdk/sky130_fd_sc_hd.v" > cvc-pdk/sky130_fd_sc_hd.v
# CVC 7.00b aborts (ARG INTERNAL) on INTERCONNECT whose destination is a top-level port (cell output -> wrapper output pin); drop
# those entries from a work copy and count them (reported below).
CSDF="$W/wrapper_$CORNER.cvc.sdf"
awk '$1=="(INTERCONNECT" && $3 !~ /\./ && $2 ~ /\./ {n++; next} {print} END{print n+0 > "/dev/stderr"}' "$WSDF" > "$CSDF" 2> "$W/sdf_dropped_$CORNER.txt"
echo "INTERCONNECT entries to top-level output ports dropped from SDF copy: $(cat "$W/sdf_dropped_$CORNER.txt") of $(grep -c INTERCONNECT "$WSDF")"
START=$(date +%s)
docker run --rm --platform linux/amd64 -v "$ROOT:$ROOT" -w "$W" openchip-cvc64-base stdbuf -oL "$CVC" +interp \
  +define+SIM +define+FUNCTIONAL +define+GL +define+USE_POWER_PINS +define+ENABLE_SDF ${CLK_HALF:+"+define+CLK_HALF=$CLK_HALF"} "+define+SDF_FILE=\"$CSDF\"" \
  +nointeractive +notimingchecks +mipdopt +sdf_verbose "$HERE/tiny_ai_wrapper_sdf_tb.v" "$WRAP" "$MACRO" cvc-pdk/primitives_hd.v cvc-pdk/sky130_fd_sc_hd.v \
  > "run_sdf_wrapper_$CORNER.log" 2>&1 || true
echo "wall $(( $(date +%s) - START )) s, corner $CORNER, wrapper run $(basename "$WRAP_RUN")"
grep -E "Monitor|MISMATCH" "run_sdf_wrapper_$CORNER.log"
