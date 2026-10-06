#!/bin/bash
# run.sh [firmware.hex] -- SoC sim for the KV-cache firmware: PicoRV32 + RAM + wb_stream_adapter + kv_attn_n8.
# Usage: make -C firmware/kv sim   (builds the hex, then calls this).  Exit 1 unless the firmware printed PASS.
set -u
HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
HEX="${1:-$REPO/firmware/kv/build/firmware.hex}"
OUT="$HERE/build"
mkdir -p "$OUT"
D="$REPO/designs"
iverilog -g2012 -Wall -Wno-timescale -o "$OUT/soc.vvp" \
  "$REPO/shared/rtl/wb_stream_adapter.v" "$REPO/shared/rtl/kv_attn_core.v" \
  "$D/kv_attn_n8/rtl/kv_attn_n8_rom.v" "$D/kv_attn_n8/rtl/kv_attn_n8.v" \
  "$REPO/soc_sim/third_party/picorv32/picorv32.v" "$HERE/kv_soc_tb.v" || exit 1
start=$(date +%s)
vvp -n "$OUT/soc.vvp" +hex="$HEX" | tee "$OUT/sim.log"
echo "soc_sim/kv: wall time $(( $(date +%s) - start )) s"
grep -q '^PASS' "$OUT/sim.log" && grep -q 'firmware exit PASS' "$OUT/sim.log" && ! grep -q '^FAIL' "$OUT/sim.log"
