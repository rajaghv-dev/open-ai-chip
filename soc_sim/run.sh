#!/bin/bash
# Purpose: Compile the SoC testbench with iverilog and run a firmware hex; exit 1 unless the firmware printed PASS.
# Run: make soc-sim (via make -C firmware sim) or soc_sim/run.sh [firmware.hex].
# In: firmware hex, soc_tb.v, designs/user_project_wrapper RTL. Out: soc_sim/build/soc.vvp, console log.
# Docs: firmware/README.md, docs/SOC_PLAN.md
# run.sh [firmware.hex] -- compile the SoC testbench with iverilog and run the firmware; exit 1 unless it printed PASS.
# Usage: make -C firmware sim   (builds the hex, then calls this)
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
HEX="${1:-$REPO/firmware/build/firmware.hex}"
OUT="$REPO/soc_sim/build"
mkdir -p "$OUT"
D="$REPO/designs"
iverilog -g2012 -Wall -Wno-timescale -I "$D/user_project_wrapper/rtl" -o "$OUT/soc.vvp" \
  "$D/user_project_wrapper/rtl/defines.v" \
  "$D/vision_all_lit/rtl/vision_all_lit_rom.v" "$D/vision_all_lit/rtl/vision_all_lit.v" \
  "$D/vision_block/rtl/vision_block_rom.v"     "$D/vision_block/rtl/vision_block.v" \
  "$D/text_sentiment/rtl/text_sentiment_rom.v" "$D/text_sentiment/rtl/text_sentiment.v" \
  "$D/tiny_ai_core/rtl/tiny_ai_core.v" "$D/user_project_wrapper/rtl/user_project_wrapper.v" \
  "$HERE/third_party/picorv32/picorv32.v" "$HERE/soc_tb.v" || exit 1
start=$(date +%s)
vvp -n "$OUT/soc.vvp" +hex="$HEX" | tee "$OUT/sim.log"
echo "soc_sim: wall time $(( $(date +%s) - start )) s"
grep -q '^PASS' "$OUT/sim.log" && grep -q 'firmware exit PASS' "$OUT/sim.log" && ! grep -q '^FAIL' "$OUT/sim.log"
