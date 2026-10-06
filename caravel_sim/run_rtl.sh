#!/usr/bin/env bash
# Native full-Caravel RTL sim of user_project_wrapper (tiny_ai_core) with real VexRiscv management-core firmware.
# Needs: build/caravel/{caravel,mgmt_core_wrapper} (see VERSIONS.txt), riscv64-elf-gcc (brew), iverilog >= 11, sky130A PDK at ~/.volare.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"; ROOT="$HERE/.."
B="$ROOT/build/caravel"; W="$B/work"; mkdir -p "$W"
export PDK_ROOT="${PDK_ROOT:-$HOME/.volare}" PDK=sky130A
export CARAVEL_PATH="$B/caravel/verilog" VERILOG_PATH="$B/mgmt_core_wrapper/verilog" USER_PROJECT_VERILOG="$ROOT/caravel_sim"
FW="$VERILOG_PATH/dv/firmware"; GEN="$VERILOG_PATH/dv/generated"
CC=riscv64-elf-gcc
$CC -g -I"$FW" -I"$GEN" -I"$VERILOG_PATH/dv/" -I"$VERILOG_PATH/common" -march=rv32i_zicsr -mabi=ilp32 -D__vexriscv__ ${EXTRA_CFLAGS:-} \
  -Wl,-Bstatic,-T,"$FW/sections.lds",--strip-debug -ffreestanding -nostdlib -o "$W/tiny_ai_wb.elf" \
  "$FW/crt0_vex.S" "$FW/isr.c" "$HERE/tiny_ai_wb.c"
riscv64-elf-objcopy -O verilog "$W/tiny_ai_wb.elf" "$W/tiny_ai_wb.hex"
sed -i.bak -e 's/@10/@00/g' "$W/tiny_ai_wb.hex"
cd "$W"
SIMDEF="-DFUNCTIONAL -DSIM -DUSE_POWER_PINS -DUNIT_DELAY=#1"
sed -e "s#\$(VERILOG_PATH)#$VERILOG_PATH#g" -e "s#\$(CARAVEL_PATH)#$CARAVEL_PATH#g" -e "s#\$(PDK_ROOT)/\$(PDK)#$PDK_ROOT/$PDK#g" "$VERILOG_PATH/includes/includes.rtl.caravel" > inc_caravel.f
# use THIS project's GPIO startup modes, not Caravel's default user_defines.v
sed -i.bak "s#$CARAVEL_PATH/rtl/user_defines.v#$(cd "$ROOT" && pwd)/designs/user_project_wrapper/rtl/user_defines.v#" inc_caravel.f && rm -f inc_caravel.f.bak
sed -e "s#@ROOT@#$(cd "$ROOT" && pwd)#g" "$HERE/includes.rtl.user" > inc_user.f
iverilog -g2012 -Ttyp $SIMDEF -f inc_caravel.f -f inc_user.f -o tiny_ai_wb_rtl.vvp "$HERE/tiny_ai_wb_tb.v"
vvp tiny_ai_wb_rtl.vvp | tee run_rtl.log | grep -E "Monitor|FAIL|PASS|Error"
