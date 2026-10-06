#!/usr/bin/env bash
# Stage a template-shaped copy of the Caravel user project under build/precheck/project/ (never touches designs/).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
P="${PRECHECK_PROJECT:-$ROOT/build/precheck/project}"
G="$ROOT/build/precheck/caravel_golden"
rm -rf "$P"; mkdir -p "$P"/{gds,lef,def,verilog/rtl,verilog/gl,lvs/user_project_wrapper}

R="$ROOT/build/results"
cp "$R/user_project_wrapper/user_project_wrapper.gds" "$P/gds/"          # the ONLY wrapper-type GDS (precheck requires exactly one)
cp "$R/tiny_ai_core/tiny_ai_core.gds" "$P/gds/"                          # macro GDS (informational; the wrapper GDS embeds it)
cp "$ROOT/designs/user_project_wrapper/output/user_project_wrapper.lef" "$P/lef/"
cp "$ROOT/designs/tiny_ai_core/output/tiny_ai_core.lef" "$P/lef/"
cp "$ROOT/designs/user_project_wrapper/fixed_dont_change/user_project_wrapper.def" "$P/def/"

# RTL: wrapper + defines/user_defines + the macro's sources (tiny_ai_core and the engines it instantiates)
cp "$ROOT"/designs/user_project_wrapper/rtl/{defines.v,user_defines.v,user_project_wrapper.v} "$P/verilog/rtl/"
for f in tiny_ai_core/rtl/tiny_ai_core.v vision_all_lit/rtl/vision_all_lit_rom.v vision_all_lit/rtl/vision_all_lit.v \
         vision_block/rtl/vision_block_rom.v vision_block/rtl/vision_block.v text_sentiment/rtl/text_sentiment_rom.v \
         text_sentiment/rtl/text_sentiment.v; do cp "$ROOT/designs/$f" "$P/verilog/rtl/"; done

# EXPERIMENT ONLY (never release evidence): GPIO_MODE=GPIO_MODE_USER_STD_INPUT_NOPULL etc. rewrites every `GPIO_MODE_INVALID in the
# STAGED user_defines.v so that we can see what the other checks say once the GPIO modes are set. designs/ is not touched.
if [ -n "${GPIO_MODE:-}" ]; then
  sed -i.bak "s/\`GPIO_MODE_INVALID/\`${GPIO_MODE}/" "$P/verilog/rtl/user_defines.v" && rm -f "$P/verilog/rtl/user_defines.v.bak"
fi

# GL: powered netlists (pnl) of the macro and the wrapper
cp "$R/user_project_wrapper/final/pnl/user_project_wrapper.pnl.v" "$P/verilog/gl/user_project_wrapper.v"
cp "$ROOT/build/macros/tiny_ai_core/pnl/tiny_ai_core.pnl.v" "$P/verilog/gl/tiny_ai_core.v"

# LVS config: our prepared designs/user_project_wrapper/lvs_config.json, with the macro netlist pointing at the staged powered netlist
sed 's#\$UPRJ_ROOT/build/macros/tiny_ai_core/nl/tiny_ai_core.nl.v#$UPRJ_ROOT/verilog/gl/tiny_ai_core.v#' \
  "$ROOT/designs/user_project_wrapper/lvs_config.json" > "$P/lvs/user_project_wrapper/lvs_config.json"

# Golden Caravel root: precheck needs gds/user_project_wrapper_empty.gds (XOR) and verilog/gl/caravel.v (OEB); gunzip a copy.
rm -rf "$G"; mkdir -p "$G"
rsync -a --exclude .git "$ROOT/build/caravel/caravel/" "$G/"
for z in "$G"/gds/*.gds.gz; do gunzip -f "$z"; done
echo "staged: $P ; golden caravel: $G"
