#!/usr/bin/env bash
# Step (v), hybrid: full Caravel RTL + real firmware, but user_project_wrapper and tiny_ai_core replaced by our routed
# power-aware gate-level netlists (sky130_fd_sc_hd functional models, unit delay). Caravel/mgmt core stay RTL.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; ROOT="$(cd "$HERE/.." && pwd)"
B="$ROOT/build/caravel"; W="$B/work"; mkdir -p "$W"
export PDK_ROOT="${PDK_ROOT:-$HOME/.volare}" PDK=sky130A
export CARAVEL_PATH="$B/caravel/verilog" VERILOG_PATH="$B/mgmt_core_wrapper/verilog"
# newest wrapper run that has a powered netlist (an elaborate-only wrapper netlist is one mprj instance, so any
# run of the same RTL gives the same netlist); override with WRAP_PNL=<file>
WRAP="${WRAP_PNL:-$(ls -t "$ROOT"/designs/user_project_wrapper/runs/*/final/pnl/user_project_wrapper.pnl.v 2>/dev/null | head -1)}"
[ -n "$WRAP" ] && [ -s "$WRAP" ] || { echo "run_gl: no powered wrapper netlist; run: make wrapper (or set WRAP_PNL)" >&2; exit 2; }
MACRO="${MACRO_PNL:-$ROOT/build/macros/tiny_ai_core/pnl/tiny_ai_core.pnl.v}"
[ -f "$W/tiny_ai_wb.hex" ] || "$HERE/run_rtl.sh" >/dev/null 2>&1 || true   # builds the hex (RTL run is a by-product)
cd "$W"
sed -e "s#\$(VERILOG_PATH)#$VERILOG_PATH#g" -e "s#\$(CARAVEL_PATH)#$CARAVEL_PATH#g" -e "s#\$(PDK_ROOT)/\$(PDK)#$PDK_ROOT/$PDK#g" "$VERILOG_PATH/includes/includes.rtl.caravel" > inc_caravel.f
# use THIS project's GPIO startup modes, not Caravel's default user_defines.v
sed -i.bak "s#$CARAVEL_PATH/rtl/user_defines.v#$(cd "$ROOT" && pwd)/designs/user_project_wrapper/rtl/user_defines.v#" inc_caravel.f && rm -f inc_caravel.f.bak
iverilog -g2012 -Ttyp -DFUNCTIONAL -DSIM -DGL -DUSE_POWER_PINS -DUNIT_DELAY=#1 -o tiny_ai_wb_gl.vvp -f inc_caravel.f \
  "$WRAP" "$MACRO" "$HERE/tiny_ai_wb_tb.v"
vvp tiny_ai_wb_gl.vvp | tee run_gl.log | grep -E "Monitor|FAIL|PASS|Error"
