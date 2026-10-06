#!/usr/bin/env bash
# test_full.sh -- the heavy-but-local checks that are NOT part of `make test` (make test-full). Sequential, one PASS/FAIL/SKIP/STALE
# line per item, summary at the end. It never starts a physical LibreLane flow: `make gds` is never called. A design whose newest
# run is not current (scripts/flow/find_reusable_run.py) is reported STALE and its signoff/gate-level items are skipped.
#
#   per design (all of Makefile ALL_DESIGNS):  run-state, make simulate, make check (signoff on committed/run metrics),
#                                              make gl-final (routed-netlist gate-level sim with the host iverilog; needs the run's netlist)
#   once:  make test (fast gate, optional), adapter-test, soc-sim, soc-kv, caravel-rtl, caravel-gl (need build/caravel)
#   opt-in flags:  --synth-gl   make gl per design (synthesis-only LibreLane run in Docker: needs the daemon; ~minutes each)
#                  --precheck   make precheck (Docker, ~1 min)      --fullgl  make caravel-fullgl (~14 min)
#                  --sdf        make caravel-sdf-wrapper (CVC amd64 image)      --with-test  run make test first
#                  --quick      per-design simulate only (skip check and gl-final)      --only "a b"  restrict the designs
# Exit 0 when nothing FAILed (SKIP and STALE are not failures; STALE is listed so the owner can re-run make gds).
cd "$(dirname "$0")/.." || exit 1
SYNTH=0; PRE=0; FULL=0; SDF=0; WT=0; QUICK=0; ONLY=""
while [ $# -gt 0 ]; do case $1 in
  --synth-gl) SYNTH=1;; --precheck) PRE=1;; --fullgl) FULL=1;; --sdf) SDF=1;; --with-test) WT=1;; --quick) QUICK=1;;
  --only) ONLY="$2"; shift;; -h|--help) sed -n 2,16p "$0"; exit 0;; *) echo "unknown option $1"; exit 2;; esac; shift; done
LOG=build/test_full; mkdir -p $LOG
DESIGNS=$(python3 - <<'PY'
import re
t = open("Makefile").read()
print(re.search(r"^ALL_DESIGNS\s*:=((?:.*\\\n)*.*)$", t, re.M).group(1).replace("\\", " "))
PY
)
[ -n "$ONLY" ] && DESIGNS="$ONLY"
P=0; F=0; S=0; ST=0; FAILED=""
res() { # res <PASS|FAIL|SKIP|STALE> <label> <detail>
  case $1 in PASS) P=$((P+1));; FAIL) F=$((F+1)); FAILED="$FAILED $2";; SKIP) S=$((S+1));; STALE) ST=$((ST+1));; esac
  printf '  %-5s %-34s %s\n' "$1" "$2" "$3"; }
run() { # run <label> <logname> <cmd...>: PASS/FAIL by exit status
  local label=$1 lg=$LOG/$2.log; shift 2; local t0=$(date +%s)
  if "$@" >"$lg" 2>&1; then res PASS "$label" "$(( $(date +%s)-t0 )) s"; else res FAIL "$label" "exit $?, $(( $(date +%s)-t0 )) s, log $lg: $(tail -1 "$lg" | cut -c1-80)"; fi; }
DOCKER=0; docker info >/dev/null 2>&1 && DOCKER=1
T0=$(date +%s)
echo "== test-full: $(echo $DESIGNS | wc -w) designs, docker=$([ $DOCKER = 1 ] && echo up || echo down), logs in $LOG/"
[ $WT = 1 ] && { echo "== make test"; run "make test" test bash tests/run_tests.sh; }
echo "== per design"
for d in $DESIGNS; do
  if run_dir=$(python3 scripts/flow/find_reusable_run.py $d 2>$LOG/$d.reuse); then cur=1; res PASS "$d run-state" "current: $(basename $run_dir)"
  else cur=0; res STALE "$d run-state" "$(tail -1 $LOG/$d.reuse | cut -c1-100): re-run make gds DESIGN=$d (not done here)"; fi
  run "$d simulate" sim_$d make --no-print-directory simulate DESIGN=$d
  [ $QUICK = 1 ] && continue
  if [ $cur = 0 ]; then res SKIP "$d check/gl-final" "stale run"; continue; fi
  run "$d check (signoff)" check_$d make --no-print-directory check DESIGN=$d
  run "$d gl-final (routed netlist)" glf_$d make --no-print-directory gl-final DESIGN=$d
  if [ $SYNTH = 1 ]; then
    if [ $DOCKER = 1 ]; then run "$d gl (synth netlist)" gls_$d make --no-print-directory gl DESIGN=$d; else res SKIP "$d gl (synth netlist)" "docker daemon not reachable"; fi
  fi
done
echo "== system level"
run "adapter-test (14 engines)" adapter make --no-print-directory adapter-test
if command -v riscv64-elf-gcc >/dev/null 2>&1; then
  run "soc-sim (PicoRV32 firmware)" socsim make --no-print-directory soc-sim
  run "soc-kv (KV firmware)" sockv make --no-print-directory soc-kv
else res SKIP "soc-sim / soc-kv" "riscv64-elf-gcc not found"; fi
if [ -d build/caravel/caravel ] && [ -d build/caravel/mgmt_core_wrapper ]; then
  run "caravel-rtl" cvrtl make --no-print-directory caravel-rtl
  run "caravel-gl (hybrid)" cvgl make --no-print-directory caravel-gl
  [ $FULL = 1 ] && run "caravel-fullgl (~14 min)" cvfull make --no-print-directory caravel-fullgl || res SKIP "caravel-fullgl" "opt-in: --fullgl (~14 min)"
else res SKIP "caravel-rtl / caravel-gl / fullgl" "build/caravel missing (docs/CARAVEL_SIM.md)"; fi
if [ $SDF = 1 ]; then run "caravel-sdf-wrapper" cvsdf make --no-print-directory caravel-sdf-wrapper; else res SKIP "caravel-sdf-wrapper" "opt-in: --sdf (CVC image)"; fi
if [ $PRE = 1 ]; then
  if [ $DOCKER = 1 ]; then run "precheck (14 checks)" precheck make --no-print-directory precheck; else res SKIP "precheck" "docker daemon not reachable"; fi
else res SKIP "precheck" "opt-in: --precheck (Docker, ~1 min)"; fi
echo
echo "test-full: $P PASS, $F FAIL, $S SKIP, $ST STALE in $(( $(date +%s)-T0 )) s"
[ $ST -gt 0 ] && echo "test-full: STALE runs are not failures; physical flows are never started here"
[ $F = 0 ] && { echo "test-full: NO FAILURES"; exit 0; } || { echo "test-full: FAILED:$FAILED"; exit 1; }
