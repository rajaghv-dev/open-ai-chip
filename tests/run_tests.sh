#!/usr/bin/env bash
# run_tests.sh -- fast repository checks, no Docker (a few seconds):
#   structure     required files present, no symlinks, no absolute home paths, no leftovers from the reference repository
#   upstream      RTL and LICENSE byte-identical to chipfoundry/caravel_user_project @ TEMPLATE_COMMIT (tests/upstream.sha256)
#   config        config.json parses, required keys and guard settings present, every dir:: path exists
#   rtl           iverilog -Wall elaborates RTL + testbench with no warnings (-Wno-timescale: the upstream RTL has no
#                 `timescale and must stay byte-identical; it contains no delays, so the default unit does not matter)
#   negative      the testbench FAILS on a deliberately broken copy of the RTL (it can catch a bug)
set -uo pipefail
cd "$(dirname "$0")/.."
D=designs/user_proj_example
FAILS=0
pass() { echo "  PASS  $*"; }
fail() { echo "  FAIL  $*"; FAILS=$((FAILS+1)); }
TMP="$(mktemp -d)"; trap 'rm -rf "$TMP"' EXIT

echo "== structure"
for f in Makefile versions.lock README.md provenance/SOURCES.md \
         $D/config.json $D/pin_order.cfg $D/base_user_proj_example.sdc \
         $D/rtl/user_proj_example.v $D/rtl/defines.v $D/rtl/LICENSE $D/rtl/UPSTREAM.txt $D/tb/user_proj_example_tb.v \
         scripts/doctor.sh scripts/flow/{run_capped.sh,find_reusable_run.py,gl_sim.sh,check_signoff.py,collect.sh,summary.py,design_info.py,signoff_allowances.json}; do
  [ -f "$f" ] || fail "missing $f"
done
[ "$FAILS" = 0 ] && pass "required files present"
links=$(find . -type l -not -path './build/*' -not -path "./$D/runs/*" | head -3)
[ -z "$links" ] && pass "no symlinks" || fail "symlinks: $links"
src=(Makefile scripts tests designs/*/config.json designs/*/rtl/*.v designs/*/tb)   # rtl/UPSTREAM.txt names the reference on purpose
hits=$(grep -rIl -e "$HOME" -e 'ci_user_proj_example' -e 'ci-upe' -e 'osl_cap_' "${src[@]}" 2>/dev/null | grep -v '^tests/run_tests.sh$' || true)
[ -z "$hits" ] && pass "no absolute home paths or reference-repo names in sources" || fail "found in: $hits"

echo "== upstream"
if shasum -a 256 -c tests/upstream.sha256 >"$TMP/sha.txt" 2>&1; then pass "RTL and LICENSE match tests/upstream.sha256"
else fail "upstream files changed:"; grep -v ': OK$' "$TMP/sha.txt"; fi

echo "== config"
if python3 - "$D/config.json" <<'PY'
import json, os, sys
p = sys.argv[1]; c = json.load(open(p)); base = os.path.dirname(p); bad = []
for k in ("DESIGN_NAME", "VERILOG_FILES", "CLOCK_PERIOD", "CLOCK_PORT", "DIE_AREA", "IO_PIN_ORDER_CFG", "VDD_NETS", "GND_NETS"):
    if k not in c: bad.append("missing key " + k)
if c.get("DESIGN_NAME") != "user_proj_example": bad.append("DESIGN_NAME is not user_proj_example")
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
then pass "config.json valid"; else fail "config.json"; fi

echo "== rtl"
RTL=$(python3 scripts/flow/design_info.py user_proj_example files)
if iverilog -g2012 -Wall -Wno-timescale -o "$TMP/tb.vvp" $RTL $D/tb/user_proj_example_tb.v >"$TMP/iv.log" 2>&1; then
  if grep -qi 'warning' "$TMP/iv.log"; then fail "iverilog warnings:"; head -5 "$TMP/iv.log"; else pass "iverilog -Wall -Wno-timescale: clean"; fi
else fail "iverilog failed:"; head -10 "$TMP/iv.log"; fi

echo "== negative"
# break the counter (increment by 2); the testbench must exit non-zero and print no PASS line
sed 's/count <= count + 1'"'"'b1;/count <= count + 2'"'"'d2;/' $D/rtl/user_proj_example.v > "$TMP/broken.v"
if cmp -s "$TMP/broken.v" $D/rtl/user_proj_example.v; then fail "mutation did not apply (RTL changed?)"
else
  iverilog -g2012 -o "$TMP/neg.vvp" $D/rtl/defines.v "$TMP/broken.v" $D/tb/user_proj_example_tb.v >/dev/null 2>&1
  (cd "$TMP" && vvp -n neg.vvp > neg.log 2>&1); rc=$?
  if [ $rc -ne 0 ] && ! grep -q '^PASS' "$TMP/neg.log"; then pass "testbench rejects a broken counter ($(grep -m1 FAIL "$TMP/neg.log" | cut -c1-70))"
  else fail "testbench passed a broken counter (exit $rc)"; fi
fi

echo
[ "$FAILS" = 0 ] && { echo "test: ALL PASSED"; exit 0; } || { echo "test: $FAILS FAILED"; exit 1; }
