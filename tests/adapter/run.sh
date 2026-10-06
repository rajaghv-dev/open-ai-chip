#!/bin/bash
# tests/adapter/run.sh -- run shared/rtl/wb_stream_adapter.v with every stream engine behind it, driven only through
# Wishbone, against each engine's own tb/vectors.hex (tests/adapter/adapter_tb.v). One PASS/FAIL line per engine;
# exits non-zero if any engine fails. Run from anywhere. Needs iverilog and vvp.
# Run: bash tests/adapter/run.sh (make adapter-test; also section == adapter of tests/run_tests.sh). PASS = one PASS line per engine, exit 0.
# Docs: tests/TEST_MATRIX.md, docs/SOC_PLAN.md, docs/ARCHITECTURE.md
# Env: VEC_DIR=<dir>  read <dir>/<engine>.hex instead of designs/<engine>/tb/vectors.hex (negative tests with corrupted vectors);
#      ONLY="a b"  run only these engines;  NLIM_AUDIO_ONSET=<n>  cap on audio_onset input beats (default: all 68,829).
cd "$(dirname "$0")/../.." || exit 1
OUT=build/adapter_tests
mkdir -p "$OUT"
# engine  format
ENGINES="vision_all_lit:FRAME vision_block:FRAME text_sentiment:FRAME image_text_match:FRAME audio_pitch:PITCH audio_onset:ONSET
         prec_bin:FRAME prec_tern:FRAME prec_int4:FRAME prec_int8:FRAME prec_fp8:FRAME prec_fp16:FRAME prec_bf16:FRAME kv_attn_n8:KV"
fail=0; n=0
for e in $ENGINES; do
  d=${e%%:*}; f=${e##*:}
  if [ -n "${ONLY:-}" ] && [[ " $ONLY " != *" $d "* ]]; then continue; fi
  n=$((n+1))
  nl=""
  if [ "$d" = audio_onset ] && [ -n "${NLIM_AUDIO_ONSET:-}" ]; then nl="+NLIM=$NLIM_AUDIO_ONSET"; fi
  core=""; if [ "$f" = KV ]; then core=shared/rtl/kv_attn_core.v; fi   # the KV engines share one core
  vec="$PWD/designs/$d/tb/vectors.hex"; [ -n "${VEC_DIR:-}" ] && vec="$(cd "$VEC_DIR" && pwd)/$d.hex"
  vvp_file="$OUT/$d.vvp"; log="$OUT/$d.log"
  if iverilog -g2012 -Wall -Wno-timescale -D"ENG=$d" -D"$f" -D"NAME=\"$d\"" -o "$vvp_file" \
        shared/rtl/wb_stream_adapter.v $core designs/$d/rtl/*.v tests/adapter/adapter_tb.v >"$log" 2>&1 \
     && (cd "$OUT" && vvp -n "$d.vvp" +VEC="$vec" $nl) >>"$log" 2>&1 \
     && grep -q '^PASS' "$log"; then
    grep -h '^PASS' "$log" | head -1
  else
    echo "FAIL $d: $(grep -m1 -E 'FAIL|error|Error' "$log") (log: $log)"
    fail=1
  fi
done
[ "$fail" = 0 ] && echo "adapter tests: $n engines PASS" || echo "adapter tests: FAILED"
exit $fail
