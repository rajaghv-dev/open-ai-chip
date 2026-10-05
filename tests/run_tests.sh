#!/usr/bin/env bash
# run_tests.sh -- fast repository checks, no Docker (a few seconds):
#   structure     required files present, no symlinks, no absolute home paths, no leftovers from the reference repository
#   upstream      RTL and LICENSE byte-identical to chipfoundry/caravel_user_project @ TEMPLATE_COMMIT (tests/upstream.sha256)
#   config        every design's config.json parses, required keys and guard settings present, every dir:: path exists
#   rtl           iverilog -Wall elaborates RTL + testbench of every design with no warnings (-Wno-timescale: the upstream
#                 RTL has no `timescale and must stay byte-identical; it contains no delays, so the default unit does not matter)
#   model         golden.py --check has zero mismatches; scripts/check_generated.sh: regeneration reproduces every generated file
#   sim           each tiny AI engine's testbench passes on the RTL with its generated vectors (PASS line, exit 0)
#   negative      the testbenches and the model FAIL on deliberately broken input (they can catch a bug): the counter with
#                 +2 increments; per tiny engine a corrupted expected value in vectors.hex and a broken RTL copy; a mutated
#                 threshold in a copy of model/tiny_ai. All mutations are made in a temp dir and asserted to have applied.
set -uo pipefail
cd "$(dirname "$0")/.."
D=designs/user_proj_example
TINY="vision_all_lit vision_block text_sentiment"
CORE=tiny_ai_core                      # the three engines behind the Wishbone register block
ALL="user_proj_example $TINY $CORE"
FAILS=0
pass() { echo "  PASS  $*"; }
fail() { echo "  FAIL  $*"; FAILS=$((FAILS+1)); }
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

echo "== structure"
for f in Makefile versions.lock README.md provenance/SOURCES.md \
         $D/config.json $D/pin_order.cfg $D/base_user_proj_example.sdc \
         $D/rtl/user_proj_example.v $D/rtl/defines.v $D/rtl/LICENSE $D/rtl/UPSTREAM.txt $D/tb/user_proj_example_tb.v \
         scripts/doctor.sh scripts/flow/{run_capped.sh,find_reusable_run.py,gl_sim.sh,check_signoff.py,collect.sh,summary.py,design_info.py,signoff_allowances.json,tiny_table.py} \
         scripts/check_generated.sh shared/tb/stream_tb.vh \
         model/tiny_ai/{common.py,train.py,golden.py,gen_rom.py,spec.json,weights.json}; do
  [ -f "$f" ] || fail "missing $f"
done
for d in $TINY; do
  for f in designs/$d/config.json designs/$d/rtl/$d.v designs/$d/rtl/${d}_rom.v designs/$d/tb/${d}_tb.v designs/$d/tb/vectors.hex; do
    [ -f "$f" ] || fail "missing $f"
  done
done
[ "$FAILS" = 0 ] && pass "required files present"
links=$(find . -type l -not -path './build/*' -not -path "./$D/runs/*" | head -3)
[ -z "$links" ] && pass "no symlinks" || fail "symlinks: $links"
src=(Makefile scripts tests designs/*/config.json designs/*/rtl/*.v designs/*/tb model shared)   # rtl/UPSTREAM.txt names the reference on purpose
hits=$(grep -rIl -e "$HOME" -e 'ci_user_proj_example' -e 'ci-upe' -e 'osl_cap_' "${src[@]}" 2>/dev/null | grep -v '^tests/run_tests.sh$' || true)
[ -z "$hits" ] && pass "no absolute home paths or reference-repo names in sources" || fail "found in: $hits"

echo "== upstream"
if shasum -a 256 -c tests/upstream.sha256 >"$TMP/sha.txt" 2>&1; then pass "RTL and LICENSE match tests/upstream.sha256"
else fail "upstream files changed:"; grep -v ': OK$' "$TMP/sha.txt"; fi

echo "== config"
for d in $ALL; do
if python3 - "designs/$d/config.json" "$d" <<'PY'
import json, os, sys
p = sys.argv[1]; name = sys.argv[2]; c = json.load(open(p)); base = os.path.dirname(p); bad = []
for k in ("DESIGN_NAME", "VERILOG_FILES", "CLOCK_PERIOD", "CLOCK_PORT", "DIE_AREA", "VDD_NETS", "GND_NETS") + (("IO_PIN_ORDER_CFG",) if name == "user_proj_example" else ()):   # tiny engines use automatic pin placement
    if k not in c: bad.append("missing key " + k)
if c.get("DESIGN_NAME") != name: bad.append("DESIGN_NAME is not " + name)
if c.get("ERROR_ON_SYNTH_CHECKS") is not True: bad.append("ERROR_ON_SYNTH_CHECKS must be true")
for k in ("MAX_TRANSITION_CONSTRAINT", "RUN_LINT_CHECK", "DISABLE_LVS"):
    if k in c: bad.append(k + " must not be set")
if "DELAY" in str(c.get("SYNTH_STRATEGY", "")): bad.append("SYNTH_STRATEGY DELAY is not allowed")
def paths(v):
    if isinstance(v, str) and v.startswith("dir::"): yield v
    elif isinstance(v, list):
        for x in v: yield from paths(x)
for k, v in c.items():
    for d in paths(v):
        if not os.path.exists(os.path.join(base, d[5:])): bad.append("%s: %s does not exist" % (k, d))
for b in bad: print("    " + b)
sys.exit(1 if bad else 0)
PY
then pass "$d/config.json valid"; else fail "$d/config.json"; fi
done

for f in designs/$CORE/config.json designs/$CORE/rtl/$CORE.v designs/$CORE/tb/${CORE}_tb.v designs/$CORE/tb/vectors.hex; do
  [ -f "$f" ] || fail "missing $f"
done

echo "== rtl"
for d in $ALL; do
  RTL=$(python3 scripts/flow/design_info.py $d files)
  if iverilog -g2012 -Wall -Wno-timescale -I shared/tb -o "$TMP/tb_$d.vvp" $RTL designs/$d/tb/${d}_tb.v >"$TMP/iv_$d.log" 2>&1; then
    if grep -qi 'warning' "$TMP/iv_$d.log"; then fail "$d iverilog warnings:"; head -5 "$TMP/iv_$d.log"; else pass "$d iverilog -Wall -Wno-timescale: clean"; fi
  else fail "$d iverilog failed:"; head -10 "$TMP/iv_$d.log"; fi
done

echo "== model"
if python3 model/tiny_ai/golden.py --check >"$TMP/golden.log" 2>&1; then
  if grep -qv ' 0 mismatches' "$TMP/golden.log"; then fail "golden.py --check reported mismatches:"; cat "$TMP/golden.log"
  else pass "golden.py --check: zero mismatches ($(grep -c mismatches "$TMP/golden.log") designs)"; fi
else fail "golden.py --check:"; cat "$TMP/golden.log"; fi
if scripts/check_generated.sh >"$TMP/gen.log" 2>&1; then pass "regeneration reproduces every generated file"
else fail "check_generated.sh:"; cat "$TMP/gen.log"; fi

echo "== sim"
# vsim <dir-name> <design> <vvp> <vec>: run a compiled testbench; prints exit code in $rc, log in $TMP/<name>.log
vsim() { (cd "$TMP" && vvp -n "$3" +VEC="$4" > "$1.log" 2>&1); rc=$?; }
for d in $TINY $CORE; do
  VEC="$PWD/designs/$d/tb/vectors.hex"
  vsim "sim_$d" "$d" "tb_$d.vvp" "$VEC"
  if [ $rc -eq 0 ] && grep -q '^PASS' "$TMP/sim_$d.log"; then pass "$d: $(grep -m1 '^PASS' "$TMP/sim_$d.log" | cut -c1-90)"
  else fail "$d testbench (exit $rc): $(tail -2 "$TMP/sim_$d.log" | tr '\n' ' ' | cut -c1-120)"; fi
done

echo "== negative"
# tiny_ai_core: flip the expected class (byte 11) of the first case record; the Wishbone testbench must fail
python3 - designs/$CORE/tb/vectors.hex "$TMP/core_bad.hex" <<'PYX'
import sys
src, dst = sys.argv[1:3]
out, seen = [], 0
for ln in open(src):
    s = ln.strip()
    if s and not s.startswith("//"):
        seen += 1
        if seen == 2:                                   # record 0 is the header; record 1 is the first case
            w = s.split(); w[11] = "%02x" % (int(w[11], 16) ^ 1); ln = " ".join(w) + "\n"
    out.append(ln)
open(dst, "w").writelines(out)
PYX
if cmp -s "$TMP/core_bad.hex" designs/$CORE/tb/vectors.hex; then fail "$CORE: vector corruption did not apply"
else
  vsim "neg_core" "$CORE" "tb_$CORE.vvp" "$TMP/core_bad.hex"
  if [ $rc -ne 0 ] && ! grep -q '^PASS' "$TMP/neg_core.log"; then pass "$CORE: testbench rejects a corrupted expected class ($(grep -m1 FAIL "$TMP/neg_core.log" | cut -c1-70))"
  else fail "$CORE: testbench passed a corrupted vector (exit $rc)"; fi
fi
# break the counter (increment by 2); the testbench must exit non-zero and print no PASS line
sed 's/count <= count + 1'"'"'b1;/count <= count + 2'"'"'d2;/' $D/rtl/user_proj_example.v > "$TMP/broken.v"
if cmp -s "$TMP/broken.v" $D/rtl/user_proj_example.v; then fail "mutation did not apply (RTL changed?)"
else
  iverilog -g2012 -o "$TMP/neg.vvp" $D/rtl/defines.v "$TMP/broken.v" $D/tb/user_proj_example_tb.v >/dev/null 2>&1
  (cd "$TMP" && vvp -n neg.vvp > neg.log 2>&1); rc=$?
  if [ $rc -ne 0 ] && ! grep -q '^PASS' "$TMP/neg.log"; then pass "testbench rejects a broken counter ($(grep -m1 FAIL "$TMP/neg.log" | cut -c1-70))"
  else fail "testbench passed a broken counter (exit $rc)"; fi
fi

# tiny engines: (a) one expected value in vectors.hex corrupted, (b) the RTL broken
for d in $TINY; do
  python3 - "designs/$d/tb/vectors.hex" "$TMP/$d.bad.hex" <<'PY'
import sys
out, n, hit = [], 0, False
for ln in open(sys.argv[1]).read().split("\n"):
    if ln.strip() and not ln.startswith("//"):
        if n == 1:                        # data record 0 is the header, record 1 the first case: word 13 = expected beat 0
            w = ln.split(); w[13] = "%02x" % (int(w[13], 16) ^ 1); ln = " ".join(w); hit = True
        n += 1
    out.append(ln)
open(sys.argv[2], "w").write("\n".join(out))
sys.exit(0 if hit else 1)
PY
  if [ $? -ne 0 ] || cmp -s "$TMP/$d.bad.hex" designs/$d/tb/vectors.hex; then fail "$d: vector mutation did not apply"
  else
    vsim "negv_$d" "$d" "tb_$d.vvp" "$TMP/$d.bad.hex"
    if [ $rc -ne 0 ] && ! grep -q '^PASS' "$TMP/negv_$d.log"; then pass "$d: testbench rejects a corrupted expected value ($(grep -m1 FAIL "$TMP/negv_$d.log" | cut -c1-60))"
    else fail "$d: testbench passed a corrupted vector (exit $rc)"; fi
  fi
  case $d in
    vision_all_lit) from='(score >= threshold)'; to='(score > threshold)' ;;
    vision_block)   from='pooled <= pooled | fire;'; to='pooled <= fire;' ;;
    text_sentiment) from="acc > 5'sd0"; to="acc >= 5'sd0" ;;
  esac
  python3 - "designs/$d/rtl/$d.v" "$TMP/$d.broken.v" "$from" "$to" <<'PY'
import sys
t = open(sys.argv[1]).read()
if sys.argv[3] not in t: sys.exit(1)
open(sys.argv[2], "w").write(t.replace(sys.argv[3], sys.argv[4]))
PY
  if [ $? -ne 0 ] || cmp -s "$TMP/$d.broken.v" designs/$d/rtl/$d.v; then fail "$d: RTL mutation did not apply"
  else
    iverilog -g2012 -Wno-timescale -I shared/tb -o "$TMP/negr_$d.vvp" designs/$d/rtl/${d}_rom.v "$TMP/$d.broken.v" designs/$d/tb/${d}_tb.v >"$TMP/negr_$d.cl" 2>&1
    vsim "negr_$d" "$d" "negr_$d.vvp" "$PWD/designs/$d/tb/vectors.hex"
    if [ $rc -ne 0 ] && ! grep -q '^PASS' "$TMP/negr_$d.log"; then pass "$d: testbench rejects broken RTL ($(grep -m1 FAIL "$TMP/negr_$d.log" | cut -c1-60))"
    else fail "$d: testbench passed broken RTL (exit $rc)"; fi
  fi
done

# model: a copy of model/tiny_ai with the vision_all_lit threshold changed must fail golden.py --check
mkdir -p "$TMP/mm/model" && cp -R model/tiny_ai "$TMP/mm/model/"
python3 - "$TMP/mm/model/tiny_ai/weights.json" <<'PY'
import json, sys
w = json.load(open(sys.argv[1])); old = w["vision_all_lit"]["threshold"]
w["vision_all_lit"]["threshold"] = old - 1 if old > 0 else old + 1
json.dump(w, open(sys.argv[1], "w"), indent=2)
PY
if cmp -s "$TMP/mm/model/tiny_ai/weights.json" model/tiny_ai/weights.json; then fail "model: mutation did not apply"
elif (cd "$TMP/mm/model/tiny_ai" && python3 golden.py --check >"$TMP/negm.log" 2>&1); then fail "model: golden.py --check passed a mutated threshold"
else pass "golden.py --check rejects a mutated threshold ($(grep -m1 -v ' 0 mismatches' "$TMP/negm.log" | cut -c1-60))"; fi

echo
[ "$FAILS" = 0 ] && { echo "test: ALL PASSED"; exit 0; } || { echo "test: $FAILS FAILED"; exit 1; }
