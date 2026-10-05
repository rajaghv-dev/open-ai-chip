#!/usr/bin/env bash
# check_generated.sh -- regenerate the tiny AI model outputs (make generate) in a scratch copy of the repository and
# fail if any generated file differs from the one on disk: weights.json, designs/*/rtl/*_rom.v, designs/*/tb/vectors.hex.
set -uo pipefail
cd "$(dirname "$0")/.."
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/model" "$TMP/designs"
cp -R model/tiny_ai "$TMP/model/"
rm -f "$TMP/model/tiny_ai/weights.json"
GEN=(model/tiny_ai/weights.json)
for d in vision_all_lit vision_block text_sentiment; do
  mkdir -p "$TMP/designs/$d/rtl" "$TMP/designs/$d/tb"
  GEN+=("designs/$d/rtl/${d}_rom.v" "designs/$d/tb/vectors.hex")
done
mkdir -p "$TMP/designs/tiny_ai_core/tb"; GEN+=("designs/tiny_ai_core/tb/vectors.hex")
(cd "$TMP/model/tiny_ai" && python3 train.py >/dev/null && python3 gen_rom.py >/dev/null) || { echo "check-generated: FAIL (generator failed)"; exit 1; }
bad=0
for f in "${GEN[@]}"; do
  if cmp -s "$f" "$TMP/$f"; then echo "  same     $f"; else echo "  CHANGED  $f"; bad=1; fi
done
[ $bad = 0 ] && echo "check-generated: PASS (regeneration reproduces every file)" || echo "check-generated: FAIL (run make generate and review)"
exit $bad
