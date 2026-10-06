#!/usr/bin/env bash
# Create designs/user_project_wrapper_<tag>/ for macro <macro> by copying designs/user_project_wrapper_soc_itm and
# renaming the macro. Usage: bash .claude/skills/wrapper-build/new_wrapper.sh <macro> <tag>
# Refuses to overwrite. Does not run any flow, does not touch scripts/flow/signoff_allowances.json.
set -euo pipefail
cd "$(dirname "$0")/../../.."
macro=${1:?macro design name}; tag=${2:?short tag}
src=designs/user_project_wrapper_soc_itm; old=soc_image_text_match; dst=designs/user_project_wrapper_$tag
[ -f designs/$macro/config.json ] || { echo "designs/$macro/config.json missing: the macro must be a design here"; exit 1; }
[ -e "$dst" ] && { echo "$dst exists"; exit 1; }
mkdir -p "$dst"/rtl "$dst"/tb "$dst"/fixed_dont_change
cp "$src"/rtl/{defines.v,user_defines.v,LICENSE} "$dst"/rtl/          # fixed files, byte-identical
cp "$src"/fixed_dont_change/user_project_wrapper.def "$dst"/fixed_dont_change/
cp "$src"/signoff.sdc "$dst"/
perl -pe "s/\\b$old\\b/$macro/g" "$src"/rtl/user_project_wrapper.v > "$dst"/rtl/user_project_wrapper.v
perl -pe "s/\\b$old\\b/$macro/g" "$src"/config.json > "$dst"/config.json
perl -pe "s/\\b$old\\b/$macro/g; s/user_project_wrapper_soc_itm/user_project_wrapper_$tag/g" "$src"/tb/user_project_wrapper_soc_itm_tb.v > "$dst"/tb/user_project_wrapper_${tag}_tb.v
[ -f designs/$macro/tb/vectors.hex ] && cp designs/$macro/tb/vectors.hex "$dst"/tb/vectors.hex
cat <<MSG
Created $dst. By hand now:
  1. config.json: check the //3 comment; confirm MACROS has gds lef nl pnl spef lib for $macro.
  2. rtl/user_project_wrapper.v: the instance must be "$macro mprj" with the macro's real port names; fix header comment.
  3. tb: the testbench body was the soc_itm one; replace it with $macro's body (designs/$macro/tb) and its vectors.
  4. add "user_project_wrapper_$tag" to scripts/flow/signoff_allowances.json (undriven_outputs io_out io_oeb la_data_out).
  5. write UPSTREAM.txt, README.md, NOTES.md; add the design to Makefile ALL_DESIGNS if it should run in make all-designs.
  6. make gds DESIGN=$macro && make views DESIGN=$macro && make flow-all DESIGN=user_project_wrapper_$tag
MSG
