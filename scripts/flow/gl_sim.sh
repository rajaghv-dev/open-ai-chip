#!/usr/bin/env bash
# gl_sim.sh -- gate-level simulation of a design's sky130 netlist with its own self-checking testbench.
#
#   gl_sim.sh prepare <design>                     write build/gl/<design>/config.json: the design's config with every
#                                                  dir:: path re-expressed relative to the copy, ERROR_ON_SYNTH_CHECKS false (a netlist is
#                                                  wanted even when synthesis reports check errors; the count is kept in
#                                                  build/gl/<design>/synth_checks.txt), and any old synthesis run removed
#   gl_sim.sh run <design> --tb FILE --top MODULE [options]
#   gl_sim.sh checks <design>                      print synthesis__check_error__count of the synthesis-only run
#
# run options:
#   --source synth|final   netlist origin (default synth: build/gl/<design>/runs/gl/final/nl/;
#                          final: newest designs/<design>/runs/*/final/nl/, else build/results/<design>/)
#   --plus ARG             plusarg for vvp (repeatable), e.g. --plus +VEC=/abs/file.hex
#   --flag ARG             extra iverilog flag (repeatable), e.g. --flag -g2012
#   --pre FILE             file compiled first (macro definitions the testbench uses)
#   -I DIR                 include directory (repeatable)
#   --tb-extra FILE        extra source compiled after the testbench (repeatable)
#   --netlist-extra FILE   extra gate-level source compiled right after the design's netlist (repeatable): the macro netlists
#                          of an elaborate-only wrapper, whose own netlist only instantiates them
#   --timeout SECONDS      kill the simulation after this long (default 900)
#   --delay-cell NAME[:NS] give the combinational cell sky130_fd_sc_hd__NAME a propagation delay (default 1 ns): the
#                          library's gates have none, so a ring oscillator built from cells would loop at time 0
#   --name SHORT           make name of the design (gl-SHORT) for hints
#   --desc TEXT            vectors description recorded in build/gl/<design>/result.txt
#   --pass-re REGEX        a line that must appear in the output (default: PASS|passed)
# The testbench is compiled against the sky130_fd_sc_hd functional models read from
# $PDK_ROOT/sky130A/libs.ref/sky130_fd_sc_hd/verilog/ (-DFUNCTIONAL, unit gate delay $UNIT_DELAY) with the host's iverilog; the
# LibreLane container is only used to make the netlist, so the same script works with USE_DOCKER=0.
# Exit 0 only if vvp exits 0, the output shows no FAIL/FATAL/ERROR line, the pass line appears and no timeout hit.
set -uo pipefail
REPO="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO"
PDK_ROOT="${PDK_ROOT:-$HOME/.volare}"
# Unit delay per cell. Must be > 0 (zero delay races between flip-flops) but small: the committed testbenches sample
# 1 ns after a clock edge, and a 1 ns cell delay lands exactly on that instant (a testbench then fails on every check).
UNIT_DELAY="${GL_UNIT_DELAY:-#0.01}"
LIB="$PDK_ROOT/sky130A/libs.ref/sky130_fd_sc_hd/verilog"

die() { echo "gl_sim: $*" >&2; exit 1; }
[ $# -ge 2 ] || { sed -n 2,28p "$0"; exit 2; }
CMD="$1"; DESIGN="$2"; shift 2
DDIR="designs/$DESIGN"
[ -f "$DDIR/config.json" ] || die "no designs/$DESIGN/config.json"
GL="build/gl/$DESIGN"

case "$CMD" in
prepare)
  rm -rf "$GL"; mkdir -p "$GL"
  python3 - "$DDIR/config.json" "$GL/config.json" <<'PY'
import json, os, sys
src, dst = sys.argv[1:3]
base = os.path.dirname(os.path.abspath(src))
def fix(v):
    if isinstance(v, str) and v.startswith("dir::"):
        # dir:: only takes relative paths: re-express the target relative to the copy's directory
        tgt = os.path.normpath(os.path.join(base, v[5:]))
        return "dir::" + os.path.relpath(tgt, os.path.dirname(os.path.abspath(dst)))
    if isinstance(v, list): return [fix(x) for x in v]
    if isinstance(v, dict): return {k: fix(x) for k, x in v.items()}
    return v
c = {k: fix(v) for k, v in json.load(open(src)).items()}
c["ERROR_ON_SYNTH_CHECKS"] = False
json.dump(c, open(dst, "w"), indent=2)
PY
  ;;
checks)
  m="$GL/runs/gl/final/metrics.json"
  [ -f "$m" ] || die "no synthesis metrics at $m (run make gl-$DESIGN first)"
  python3 -c "import json,sys; print(json.load(open(sys.argv[1])).get('synthesis__check_error__count','missing'))" "$m"
  ;;
run)
  MAKENAME=""; TB=""; TOP=""; SRC=synth; TMO=900; PASS_RE='PASS|passed'; DESC=""; PLUS=(); FLAGS=(); EXTRA=(); NLX=(); DCELLS=(); PRE=(); INCS=()
  while [ $# -gt 0 ]; do
    case "$1" in
      --tb) TB="$2"; shift 2 ;;        --top) TOP="$2"; shift 2 ;;
      --source) SRC="$2"; shift 2 ;;   --plus) PLUS+=("$2"); shift 2 ;;
      --flag) FLAGS+=("$2"); shift 2 ;; --tb-extra) EXTRA+=("$2"); shift 2 ;; --netlist-extra) NLX+=("$2"); shift 2 ;; --pre) PRE+=("$2"); shift 2 ;; -I) INCS+=("-I$2"); shift 2 ;;
      --name) MAKENAME="gl-$2"; shift 2 ;; --timeout) TMO="$2"; shift 2 ;; --desc) DESC="$2"; shift 2 ;; --delay-cell) DCELLS+=("$2"); shift 2 ;;  --pass-re) PASS_RE="$2"; shift 2 ;;
      *) die "unknown option $1" ;;
    esac
  done
  [ -f "$TB" ] || die "testbench not found: '$TB'"
  [ -n "$TOP" ] || die "--top is required"
  command -v iverilog >/dev/null || die "iverilog not found"
  [ -f "$LIB/sky130_fd_sc_hd.v" ] && [ -f "$LIB/primitives.v" ] || die "cell models not found in $LIB (PDK_ROOT=$PDK_ROOT)"
  for x in "${NLX[@]+"${NLX[@]}"}"; do [ -s "$x" ] || die "--netlist-extra file not found: $x (run make views DESIGN=<macro>)"; done
  DNAME=$(python3 -c "import json,sys; print(json.load(open(sys.argv[1]))['DESIGN_NAME'])" "$DDIR/config.json")
  NL=""
  case "$SRC" in
    synth) NL=$(ls -t "$GL"/runs/gl/final/nl/*.nl.v 2>/dev/null | head -1) ;;
    final) # Only a routed netlist of a CURRENT run is simulated. Candidates: kept runs (designs/<d>/runs/<run>/final/nl) and
           # the results collector (build/results/<d>/final/nl, run recorded in meta.json). A candidate whose run directory
           # still exists must be the run scripts/flow/find_reusable_run.py reports as current; one whose run directory was
           # deleted after collection is judged by the same rule (no input modified after the run started, the timestamp
           # in the run name), applied to the design's config, its source directories and designs/<d>/.
           # GL_ALLOW_STALE=1 overrides (deliberate use only).
           CUR=$(basename "$(python3 scripts/flow/find_reusable_run.py "$DESIGN" 2>/dev/null)" 2>/dev/null)
           NL=$(python3 - "$DESIGN" "$DNAME" "$CUR" "${GL_ALLOW_STALE:-0}" <<'PY'
import calendar, glob, json, os, re, sys
d, top, cur, allow = sys.argv[1:5]
cands = sorted(glob.glob("designs/%s/runs/*/final/nl/*.nl.v" % d) + glob.glob("build/results/%s/final/nl/*.nl.v" % d),
               key=os.path.getmtime, reverse=True)
def run_of(f):
    if f.startswith("designs/"):
        return f.split("/")[3] if len(f.split("/")) > 3 else None, os.path.dirname(os.path.dirname(os.path.dirname(f)))
    try:
        rd = json.load(open("build/results/%s/meta.json" % d))["run_dir"]
    except Exception:
        return None, None
    return os.path.basename(rd), os.path.join("designs", d, "runs", os.path.basename(rd))
def inputs():
    cfg = "designs/%s/config.json" % d
    c = json.load(open(cfg)); base = os.path.dirname(cfg); dirs = {base}
    for k in ("VERILOG_FILES", "VERILOG_INCLUDE_DIRS"):
        for v in c.get(k, []):
            if isinstance(v, str) and v.startswith("dir::"):
                p = os.path.normpath(os.path.join(base, v[5:])); dirs.add(p if os.path.isdir(p) else os.path.dirname(p))
    for dd in dirs:
        for dp, dn, fn in os.walk(dd):
            dn[:] = [x for x in dn if x not in ("runs", "gds", "output", "model", "tb", ".git")]
            for f in fn:
                if not f.endswith(".md"): yield os.path.join(dp, f)   # docs cannot change the silicon
def stale_after_name(name):
    m = re.match(r"RUN_(\d+)-(\d+)-(\d+)_(\d+)-(\d+)-(\d+)$", name or "")
    if not m: return "cannot read the run start time from its name"
    start = calendar.timegm(tuple(int(x) for x in m.groups()))
    for f in inputs():
        if os.path.getmtime(f) > start: return "%s was modified after the run started" % f
    return None
first_valid = None; why = []
for f in cands:
    if not open(f, errors="replace").read().count("module " + top):
        continue
    name, rdir = run_of(f)
    if first_valid is None: first_valid = f
    if rdir and os.path.isdir(rdir):
        if name == cur: print(f); sys.exit(0)
        why.append("%s: run %s is not the current reusable run (%s)" % (f, name, cur or "none")); continue
    r = stale_after_name(name)
    if r is None: print(f); sys.exit(0)
    why.append("%s: %s" % (f, r))
if allow == "1" and first_valid:
    sys.stderr.write("gl_sim: WARNING GL_ALLOW_STALE=1: using %s although it is not verified current\n" % first_valid)
    print(first_valid); sys.exit(0)
sys.stderr.write("\n".join(why) + ("\n" if why else ""))
PY
           ) ;;
    *) die "--source must be synth or final" ;;
  esac
  [ -n "$NL" ] && [ -s "$NL" ] || if [ "$SRC" = final ]; then die "no CURRENT post-route netlist for $DESIGN (none found, or the RTL/config changed after the run that made it). Produce one with: make gds   (or: make collect). To simulate a possibly stale one on purpose: GL_ALLOW_STALE=1"; else die "no synthesized netlist for $DESIGN: run make ${MAKENAME:-gl-$DESIGN}"; fi
  grep -q "^module $DNAME\b" "$NL" || die "netlist $NL does not define module $DNAME"
  mkdir -p "$GL/sim"
  rm -f "$GL/result.txt"
  T0=$(date +%s)
  # record one result line per run: design | netlist source | vectors | result | seconds
  rec() { printf '%s | %s | %s | %s | %s s\n' "$DESIGN" "$SRC:$(basename "$NL")" "$DESC" "$1" "$(( $(date +%s) - T0 ))" > "$GL/result.txt"; }
  die() { rec "FAIL: $*"; echo "gl_sim: $*" >&2; exit 1; }
  VVP="$GL/sim/gl_tb.vvp"; LOG="$GL/sim/gl.log"
  echo "gl_sim: $DESIGN netlist=$NL ($(grep -c '^ *sky130_fd_sc_hd__' "$NL") cells) tb=$TB"
  # cell models first (they carry the 1ns/1ps timescale), then netlist, then the testbench
  # The synthesized netlist is flattened and has no parameters, but testbenches instantiate the RTL top with
  # overrides (#(.BASE_ADDR(...))). Strip the overrides from a copy of the testbench; the RTL defaults are what
  # was synthesized, so a testbench that needed other values fails loudly instead of passing.
  TBG="$GL/sim/tb_gl.v"
  python3 - "$TB" "$TBG" "$DNAME" <<'PY'
import re, sys
src, dst, top = sys.argv[1:4]
t = open(src).read()
n = 0
for m in list(re.finditer(r"\b" + re.escape(top) + r"\s*#\s*\(", t))[::-1]:
    i = m.end(); depth = 1
    while depth:
        depth += {"(": 1, ")": -1}.get(t[i], 0); i += 1
    t = t[:m.start() + len(top)] + " " + t[i:]
    n += 1
open(dst, "w").write(t)
if n: print("gl_sim: removed %d parameter override(s) on %s from the testbench copy" % (n, top))
PY
  LIBV="$LIB/sky130_fd_sc_hd.v"
  if [ "${#DCELLS[@]}" -gt 0 ]; then
    LIBV="$GL/sim/sky130_fd_sc_hd_delayed.v"
    python3 - "$LIB/sky130_fd_sc_hd.v" "$LIBV" "${DCELLS[@]}" <<'PY'
import re, sys
src, dst, *cells = sys.argv[1:]
t = open(src).read()
for c in cells:
    name, _, ns = c.partition(":")
    ns = ns or "1"
    pat = re.compile(r"module sky130_fd_sc_hd__" + re.escape(name) + r"\b.*?endmodule", re.S)
    t, n = pat.subn(lambda m: re.sub(r"^(\s*)buf(\s+)(buf0\b)", r"\1buf #%s\2\3" % ns, m.group(0), flags=re.M), t)
    if not n: sys.exit("no such cell: " + name)
open(dst, "w").write(t)
PY
    [ $? -eq 0 ] || die "could not build the delayed cell library"
  fi
  if ! iverilog -g2012 -DFUNCTIONAL "-DUNIT_DELAY=$UNIT_DELAY" "${FLAGS[@]+"${FLAGS[@]}"}" -s "$TOP" -o "$VVP" \
        "${INCS[@]+"${INCS[@]}"}" "${PRE[@]+"${PRE[@]}"}" "$LIB/primitives.v" "$LIBV" "$NL" "${NLX[@]+"${NLX[@]}"}" "$TBG" "${EXTRA[@]+"${EXTRA[@]}"}" > "$GL/sim/iverilog.log" 2>&1; then
    cat "$GL/sim/iverilog.log"; die "iverilog failed for $DESIGN"
  fi
  # Python's subprocess timeout kills vvp and leaves no helper process (a `sleep` watchdog child outlived the
  # script and kept `make gl-x | tail` waiting).
  python3 - "$TMO" "$LOG" "$VVP" "${PLUS[@]+"${PLUS[@]}"}" <<'PY'
import subprocess, sys
tmo, log, vvp, *plus = sys.argv[1:]
with open(log, "w") as f:
    p = subprocess.Popen(["vvp", "-n", vvp] + plus, stdout=f, stderr=subprocess.STDOUT)
    try:
        sys.exit(p.wait(timeout=float(tmo)))
    except subprocess.TimeoutExpired:
        p.kill(); p.wait()
        f.write("gl_sim: TIMEOUT after %ss\n" % tmo)
        sys.exit(124)
PY
  RC=$?
  T1=$(date +%s)
  tail -n 12 "$LOG"
  echo "gl_sim: $DESIGN vvp exit=$RC, $((T1-T0)) s, log $LOG"
  [ "$RC" -eq 0 ] || die "$DESIGN: simulation failed (exit $RC)"
  grep -q 'gl_sim: TIMEOUT' "$LOG" && die "$DESIGN: timeout"
  # a bare $finish with exit 0 and a FAIL line must not pass; neither must a testbench that printed nothing
  if grep -Eq '^(FAIL|FATAL|ERROR)|: (FAIL|FATAL)|FAILED|[^0-9]*[1-9][0-9]* (errors?|check\(s\) failed)' "$LOG"; then
    grep -En '^(FAIL|FATAL|ERROR)|: (FAIL|FATAL)|FAILED|[1-9][0-9]* (errors?|check\(s\) failed)' "$LOG" | head -5
    die "$DESIGN: testbench reported failures"
  fi
  grep -Eq "$PASS_RE" "$LOG" || die "$DESIGN: no line matching /$PASS_RE/ in the output"
  grep -Eqi '\bx\b.*unknown|undefined' "$LOG" && echo "gl_sim: note: output mentions unknown values"
  rec PASS
  echo "gl_sim: $DESIGN PASS ($((T1-T0)) s)"
  ;;
*) die "unknown command $CMD" ;;
esac
