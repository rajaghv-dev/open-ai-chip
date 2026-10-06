#!/usr/bin/env bash
# Full-chip gate-level (no SDF): caravel-lite CC2509 gl/ netlists (caravel, chip_io, caravel_core, housekeeping, gpio blocks, mgmt_protect...),
# mgmt_core_wrapper CC2509 gl netlist (VexRiscv SoC), RAM128/RAM256 gl, and OUR user_project_wrapper + tiny_ai_core power-aware pnl.
# sky130 cell models: functional, unit delay (override with MODE=timing to use the specify-block models, no SDF, for run_sdf.sh).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; ROOT="$(cd "$HERE/.." && pwd)"
B="$ROOT/build/caravel"; W="$B/work"; mkdir -p "$W"
export PDK_ROOT="${PDK_ROOT:-$HOME/.volare}" PDK=sky130A
export CARAVEL_PATH="$B/caravel/verilog" VERILOG_PATH="$B/mgmt_core_wrapper/verilog"
WRAP="${WRAP_PNL:-$(ls -t "$ROOT"/designs/user_project_wrapper/runs/*/final/pnl/user_project_wrapper.pnl.v 2>/dev/null | head -1)}"
[ -n "$WRAP" ] && [ -s "$WRAP" ] || { echo "run_fullgl: no powered wrapper netlist (set WRAP_PNL)" >&2; exit 2; }
MACRO="${MACRO_PNL:-$ROOT/build/macros/tiny_ai_core/pnl/tiny_ai_core.pnl.v}"
[ -f "$W/tiny_ai_wb.hex" ] || "$HERE/run_rtl.sh" >/dev/null 2>&1 || true
cd "$W"
# the mgmt-core GL include list, with the caravel_core netlist enabled (the repo list comments it out) and the PDK paths filled in
sed -e "s#\$(VERILOG_PATH)#$VERILOG_PATH#g" -e "s#\$(CARAVEL_PATH)#$CARAVEL_PATH#g" -e "s#\$(PDK_ROOT)/\$(PDK)#$PDK_ROOT/$PDK#g" \
    -e "s@^#-v \(.*gl/caravel_core.v\)@-v \1@" "$VERILOG_PATH/includes/includes.gl.caravel" > inc_fullgl.f
grep -q "gl/caravel_core.v" inc_fullgl.f
sed -i.bak "s#$CARAVEL_PATH/rtl/user_defines.v#$ROOT/designs/user_project_wrapper/rtl/user_defines.v#" "inc_fullgl.f" && rm -f "inc_fullgl.f.bak"   # this project's GPIO startup modes
# testbench: same as the RTL/hybrid one but no whole-design VCD (a full-chip netlist VCD is multi-GB)
sed -e 's#^\t\t\$dumpfile.*##' -e 's#^\t\t\$dumpvars.*##' "$HERE/tiny_ai_wb_tb.v" > tb_fullgl.v
FUNC="-DFUNCTIONAL -DUNIT_DELAY=#1"; [ "${MODE:-func}" = timing ] && FUNC=""
iverilog -g2012 -Ttyp $FUNC -DSIM -DGL -DUSE_POWER_PINS -o tiny_ai_wb_fullgl.vvp -f inc_fullgl.f \
  "$WRAP" "$MACRO" tb_fullgl.v 2>&1 | tee iverilog_fullgl.log | tail -20
echo "wrapper: $WRAP" ; date +%s > fullgl_start.txt
vvp tiny_ai_wb_fullgl.vvp | tee run_fullgl.log | grep -E "Monitor|FAIL|PASS|Error"
