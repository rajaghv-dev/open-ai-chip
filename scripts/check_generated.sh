#!/usr/bin/env bash
# check_generated.sh -- regenerate the outputs of every model directory (make generate) in a scratch copy of the repository and
# fail if any generated file differs from the one on disk:
#   model/{tiny_ai,audio_pitch,audio_onset,image_text_match}/weights.json  (re-fitted by train.py; deleted in the copy first)
#   designs/<d>/rtl/<d>_rom.v and designs/<d>/tb/vectors.hex for every design a generator writes
#   (tiny_ai_core: vectors.hex only; model/precision_hw has no fitting step, gen.py writes the ROMs and vectors).
set -uo pipefail
cd "$(dirname "$0")/.."
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT
mkdir -p "$TMP/model" "$TMP/designs"
FIT="tiny_ai audio_pitch audio_onset image_text_match"      # train.py + gen_rom.py
GEN=()
for m in $FIT precision_hw; do cp -R model/$m "$TMP/model/"; done
find "$TMP/model" -name __pycache__ -prune -exec rm -rf {} +
for m in $FIT; do rm -f "$TMP/model/$m/weights.json"; GEN+=("model/$m/weights.json"); done
for d in vision_all_lit vision_block text_sentiment audio_pitch audio_onset image_text_match \
         prec_bin prec_tern prec_int4 prec_int8 prec_fp8 prec_fp16 prec_bf16; do
  mkdir -p "$TMP/designs/$d/rtl" "$TMP/designs/$d/tb"
  GEN+=("designs/$d/rtl/${d}_rom.v" "designs/$d/tb/vectors.hex")
done
mkdir -p "$TMP/designs/tiny_ai_core/tb"; GEN+=("designs/tiny_ai_core/tb/vectors.hex")
for m in $FIT; do
  (cd "$TMP/model/$m" && python3 train.py >/dev/null && python3 gen_rom.py >/dev/null) || { echo "check-generated: FAIL ($m generator failed)"; exit 1; }
done
(cd "$TMP/model/precision_hw" && python3 gen.py >/dev/null) || { echo "check-generated: FAIL (precision_hw generator failed)"; exit 1; }
bad=0
for f in "${GEN[@]}"; do
  if cmp -s "$f" "$TMP/$f"; then echo "  same     $f"; else echo "  CHANGED  $f"; bad=1; fi
done
[ $bad = 0 ] && echo "check-generated: PASS (regeneration reproduces ${#GEN[@]} files)" || echo "check-generated: FAIL (run make generate and review)"
exit $bad
