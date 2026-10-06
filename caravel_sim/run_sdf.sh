#!/usr/bin/env bash
# Purpose: Full-chip gate-level plus SDF back-annotation with Open Verilog CVC in an amd64 container.
# Run: caravel_sim/run_sdf.sh (CORNER, SCOPE, MINIMAL, CORE_SDF env switches below); needs the CVC build.
# In: build/caravel downloads, wrapper SDF, caravel_core SDF. Out: build/caravel/work/ logs.
# Docs: docs/CARAVEL_SIM.md, caravel_sim/README.md
# Full-chip gate-level + SDF back-annotation with Open Verilog CVC (cvc64 7.00b, x86_64 only) run in an amd64 container
# (colima 'osl' profile; image openchip-cvc64-base = ubuntu 22.04 amd64 + gcc + zlib; the cvc64 binary is built from
# github.com/cambridgehackers/open-src-cvc into build/caravel/cvc_src/build64/, see docs/CARAVEL_SIM.md).
# iverilog cannot do this: its $sdf_annotate takes only 2 args, mis-parses INTERCONNECT paths and has no timing checks.
#   CORNER=nom_tt_025C_1v80 (default) | nom_ss_100C_1v60 | nom_ff_n40C_1v95   -> SDF of OUR blocks (wrapper+macro)
#   SCOPE=hybrid (default) | full : hybrid = RTL Caravel + gate-level wrapper/macro with their SDF (smallest, fits the time cap);
#                full = flat gate-level caravel_core (+ management SoC) too.   MINIMAL=1 (default): firmware = ID read + one case.
#   CORE_SDF=1 (default) also annotate the flat caravel_core netlist (Caravel + management SoC) with the SDF made by gen_caravel_sdf.sh
#                (OpenSTA from the shipped caravel_core SPEF; caravel-lite ships no SDF). CORE_SDF=0: only our blocks are annotated.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; ROOT="$(cd "$HERE/.." && pwd)"
B="$ROOT/build/caravel"; W="$B/work"; mkdir -p "$W"
export PDK_ROOT="${PDK_ROOT:-$HOME/.volare}" PDK=sky130A
CARAVEL_PATH="$B/caravel/verilog"; VERILOG_PATH="$B/mgmt_core_wrapper/verilog"
CORNER="${CORNER:-nom_tt_025C_1v80}"; SCOPE="${SCOPE:-hybrid}"; MINIMAL="${MINIMAL:-1}"; CORE_SDF="${CORE_SDF:-1}"
[ "$SCOPE" = hybrid ] && CORE_SDF=0
CSDF="$B/sta/out/caravel_core.$CORNER.sdf"
if [ "$CORE_SDF" = 1 ] && [ ! -s "$CSDF" ]; then CORNER="$CORNER" "$HERE/gen_caravel_sdf.sh" >/dev/null; fi
WRAP_RUN="${WRAP_RUN:-$(dirname "$(dirname "$(dirname "$(ls -t "$ROOT"/designs/user_project_wrapper/runs/*/final/pnl/user_project_wrapper.pnl.v | head -1)")")")}"
WRAP="$WRAP_RUN/final/pnl/user_project_wrapper.pnl.v"
WSDF="$WRAP_RUN/final/sdf/$CORNER/user_project_wrapper__$CORNER.sdf"
MACRO="${MACRO_PNL:-$ROOT/build/macros/tiny_ai_core/pnl/tiny_ai_core.pnl.v}"
[ -s "$WRAP" ] && [ -s "$WSDF" ] || { echo "run_sdf: missing $WRAP or $WSDF" >&2; exit 2; }
[ -f "$W/tiny_ai_wb.hex" ] || "$HERE/run_rtl.sh" >/dev/null 2>&1 || true
CVC="$B/cvc_src/build64/cvc64"; [ -x "$CVC" ] || { echo "run_sdf: $CVC missing (build cvc, see docs/CARAVEL_SIM.md)" >&2; exit 2; }
. "$ROOT/scripts/lib/common.sh"; oac_docker_host   # Colima osl socket if present (Linux: /var/run/docker.sock)
cd "$W"
TAG="sdf_${SCOPE}_${CORNER}_c${CORE_SDF}_m${MINIMAL}"
# firmware (own hex so run_rtl/run_gl are untouched)
FW="$VERILOG_PATH/dv/firmware"; GEN="$VERILOG_PATH/dv/generated"; HEX="tiny_ai_$TAG.hex"
riscv64-elf-gcc -g -I"$FW" -I"$GEN" -I"$VERILOG_PATH/dv/" -I"$VERILOG_PATH/common" -march=rv32i_zicsr -mabi=ilp32 -D__vexriscv__ $([ "$MINIMAL" = 1 ] && echo -DMINIMAL) \
  -Wl,-Bstatic,-T,"$FW/sections.lds",--strip-debug -ffreestanding -nostdlib -o "tiny_ai_$TAG.elf" "$FW/crt0_vex.S" "$FW/isr.c" "$HERE/tiny_ai_wb.c"
riscv64-elf-objcopy -O verilog "tiny_ai_$TAG.elf" "$HEX"; sed -i.bak -e 's/@10/@00/g' "$HEX"
# file list: the repo's mgmt-core GL list, caravel_core enabled, PDK -> CVC-flavoured sky130 models (specify blocks with zero default
# delays that $sdf_annotate overrides, functional core), '#' and blank lines dropped (cvc -f has no '#' comments)
LISTSRC=includes.gl.caravel; [ "$SCOPE" = hybrid ] && LISTSRC=includes.rtl.caravel
sed -E -e "s#[\$][(]VERILOG_PATH[)]#$VERILOG_PATH#g" -e "s#[\$][(]CARAVEL_PATH[)]#$CARAVEL_PATH#g" \
    -e "s@^#-v (.*gl/caravel_core.v)@-v \1@" \
    -e "/^ *-v .*libs.ref/d" -e "/^#.*libs.ref/d" "$VERILOG_PATH/includes/$LISTSRC" \
  | grep -v '^ *#' | grep -v '^ *$' | { [ "$SCOPE" = hybrid ] && cat || sed -E 's/^ *-v +//'; } | grep -v 'caravan' > "inc_$TAG.f"
sed -i.bak "s#$CARAVEL_PATH/rtl/user_defines.v#$ROOT/designs/user_project_wrapper/rtl/user_defines.v#" "inc_$TAG.f" && rm -f "inc_$TAG.f.bak"   # this project's GPIO startup modes
# fill cells are not in the CVC models; empty modules are enough (no function)
cat > cells_stub.v <<'STUB'
module sky130_ef_sc_hd__fill_4(VPWR,VGND,VPB,VNB); input VPWR,VGND,VPB,VNB; endmodule
module sky130_ef_sc_hd__fill_8(VPWR,VGND,VPB,VNB); input VPWR,VGND,VPB,VNB; endmodule
STUB
echo "$W/cells_stub.v" >> "inc_$TAG.f"
# CVC 7.00b lexes '@(*)' as an attribute start and UDP edges '(0x)' as a number: fix both in a work copy of the models
mkdir -p cvc-pdk; for f in "$VERILOG_PATH"/cvc-pdk/*.v; do b=$(basename "$f")
  case "$b" in primitives_*) sed -E -e 's/\(([01xXbB?])([01xXbB?])\)/(\1 \2)/g' "$f" ;; *) sed -E -e 's/@\(\*\)/@*/g' "$f" ;; esac > "cvc-pdk/$b"; done
for f in sky130_ef_io sky130_fd_io primitives_hd sky130_fd_sc_hd primitives_hvl sky130_fd_sc_hvl sky130_ef_sc_hd__decap_12; do echo "$W/cvc-pdk/$f.v" >> "inc_$TAG.f"; done
# testbench: replace the template's ENABLE_SDF block (stale paths) with our annotate list, no VCD
python3 - "$HERE/tiny_ai_wb_tb.v" "$HEX" "$WSDF" "$CORE_SDF" "$CSDF" > "tb_$TAG.v" <<'PY'
import sys,re
s=open(sys.argv[1]).read().replace('tiny_ai_wb.hex',sys.argv[2]); hexf=sys.argv[2]; wsdf,core,csdf=sys.argv[3:6]
i=s.index('`ifdef ENABLE_SDF'); j=s.index('`endif',i)+6
blk='`ifdef ENABLE_SDF\n\tinitial begin\n'
blk+='\t\t$sdf_annotate("%s", uut.chip_core.mprj, , "sdf_wrapper.log");\n'%wsdf
if core=='1':
    blk+='\t\t$sdf_annotate("%s", uut.chip_core, , "sdf_core.log");\n'%csdf
blk+='\tend\n`endif'
s=s[:i]+blk+s[j:]
s=re.sub(r'\t\t\$dump(file|vars).*\n','',s)
s=s.replace('endmodule\n`default_nettype wire','\talways #50000 $display("HEARTBEAT sim time %0t", $time);\nendmodule\n`default_nettype wire')
print(s)
PY
IMG=openchip-cvc64-base
DEFS="+define+SIM +define+FUNCTIONAL +define+GL +define+USE_POWER_PINS +define+ENABLE_SDF"
echo "run_sdf: $TAG wrapper=$WRAP_RUN sdf=$WSDF"; date +%s > "start_$TAG.txt"
docker run --rm --platform linux/amd64 -v "$ROOT:$ROOT" -v "$PDK_ROOT:$PDK_ROOT" -w "$W" "$IMG" timeout "${SIM_CAP:-2700}" stdbuf -oL -eL \
  "$CVC" +interp $DEFS +change_port_type +nointeractive +notimingchecks +mipdopt +sdf_verbose \
  -f "inc_$TAG.f" "$WRAP" "$MACRO" "tb_$TAG.v" > "run_$TAG.log" 2>&1 || true
grep -E "Monitor|FAIL|PASS|Error" "run_$TAG.log" | grep -v "^ *\*\*WARN" | head -20
