#!/usr/bin/env bash
# Purpose: Generate the caravel_core SDF (not shipped by caravel-lite) with OpenSTA.
# Run: caravel_sim/gen_caravel_sdf.sh (also called by run_sdf.sh); CORNER=... selects the corner.
# In: build/caravel netlists + SPEF, wrapper netlist. Out: build/caravel/sta/out/caravel_core.<corner>.sdf.
# Docs: docs/CARAVEL_SIM.md, caravel_sim/README.md
# caravel-lite CC2509 ships SPEF (signoff/*/openlane-signoff/spef) but NO SDF for caravel_core / housekeeping / chip_io / gpio blocks.
# This generates a caravel_core SDF with OpenSTA (inside the pinned librelane image, STA only - no physical flow) from the
# shipped gate netlist (flat, includes the management SoC) + shipped nom SPEF + sky130_fd_sc_hd liberty of the chosen corner.
#   CORNER=nom_tt_025C_1v80 (default) | nom_ss_100C_1v60 | nom_ff_n40C_1v95
# Output: build/caravel/sta/out/caravel_core.<CORNER>.sdf   (takes ~20 s)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; ROOT="$(cd "$HERE/.." && pwd)"; B="$ROOT/build/caravel"
CORNER="${CORNER:-nom_tt_025C_1v80}"; PDK_ROOT="${PDK_ROOT:-$HOME/.volare}"
case "$CORNER" in nom_tt_025C_1v80) LIB=sky130_fd_sc_hd__tt_025C_1v80;; nom_ss_100C_1v60) LIB=sky130_fd_sc_hd__ss_100C_1v60;;
  nom_ff_n40C_1v95) LIB=sky130_fd_sc_hd__ff_n40C_1v95;; *) echo "unknown CORNER" >&2; exit 2;; esac
. "$ROOT/scripts/lib/common.sh"; oac_docker_host   # DOCKER_HOST default; OAC_LIBRELANE_IMAGE
mkdir -p "$B/sta/gl_clean" "$B/sta/out"
# OpenSTA's verilog reader chokes on arrayed instances (decap_12[1815:0]) -> strip fill/decap arrays; user_project_wrapper becomes
# a port-only stub (our wrapper has its own SDF); simple_por is behavioural RTL -> port-only stub.
WRAP="$(ls -t "$ROOT"/designs/user_project_wrapper/runs/*/final/pnl/user_project_wrapper.pnl.v | head -1)"
python3 - "$B/caravel/verilog/gl" "$B/sta/gl_clean" "$WRAP" <<'PY'
import re,sys
src,dst,wrap=sys.argv[1:4]
for f in "housekeeping gpio_logic_high gpio_defaults_block spare_logic_block xres_buf user_id_programming mprj_logic_high mprj2_logic_high mprj_io_buffer mgmt_protect_hv caravel_clocking caravel_core empty_macro manual_power_connections".split():
    s=open("%s/%s.v"%(src,f)).read()
    s=re.sub(r'\n\s*sky130_ef_sc_hd__\w+\s+\S+\[\d+:\d+\]\s*\(.*?\);','',s,flags=re.S)
    open("%s/%s.v"%(dst,f),"w").write(s)
w=open(wrap).read().split("\n")
i=[k for k,l in enumerate(w) if re.match(r'\s*tiny_ai_core\s+mprj\b|\s*\w+\s+mprj\s*\(',l)][0]
open(dst+"/__user_project_wrapper.v","w").write("\n".join(w[:i])+"\nendmodule\n")
open(dst+"/simple_por_stub.v","w").write("module simple_por(inout vdd3v3, inout vdd1v8, inout vss3v3, inout vss1v8, output porb_h, output porb_l, output por_l);\nendmodule\n")
PY
cp "$HERE/caravel_core_sdf.tcl" "$B/sta/caravel_core_sdf.tcl"
docker run --rm --entrypoint bash -v "$ROOT:$ROOT" -v "$PDK_ROOT:$PDK_ROOT" -e ROOT="$ROOT" -e PDK_ROOT="$PDK_ROOT" \
  -e CORNER_LIB="$LIB" -e SPEF_CORNER=nom -e OUT_SDF="$B/sta/out/caravel_core.$CORNER.sdf" "$OAC_LIBRELANE_IMAGE" \
  -c "sta -no_init -exit $B/sta/caravel_core_sdf.tcl" 2>&1 | tee "$B/sta/out/sta_$CORNER.log" | grep -v "^Warning.*not found. Creating black box" | tail -12
