#!/usr/bin/env bash
# collect.sh -- keep and view the hardened design's results (build/results/<design>/).
#
#   scripts/flow/collect.sh                      every design that has build/results/<d>/, one by one
#   scripts/flow/collect.sh user_proj_example    just that design
#   scripts/flow/collect.sh --list               which designs have results
#   scripts/flow/collect.sh --no-gui [d]         summary only (no image, no KLayout, no waiting)
#   scripts/flow/collect.sh --collect <d> [--cpuset 0-1] [--profile tight] [--force] [--keep-run]
#                                           store one design's results: reuse the newest complete, current run
#                                           (see scripts/flow/find_reusable_run.py), run a flow only if there is none (or --force)
#
# build/results/<design>/ holds: <top>.gds  <top>.lef  final/{nl,pnl}/  layout.png  layout_flow.png  metrics.json
#   resources.json  flow.log  steps.txt  final_listing.txt  meta.json  reports/*
# Wrapper runs (elaborate-only, no CTS/fill/...): rpt() matches report steps by name and skips the ones a run does not have, so a
# missing step is not an error. layout.png: KLayout renders with RENDER_PX as the LONG edge (default 2400, so the 2920 x 3520 um
# user_project_wrapper comes out 1991 x 2400); if that is slow, lower it:  RENDER_PX=1200 make collect DESIGN=user_project_wrapper
# (a failed render falls back to the flow's own layout_flow.png).
# Environment: PDK_ROOT (default ~/.volare), OPEN_CMD (override image opener), RENDER_PX (layout.png long edge, default 2400),
#   RESULTS_DIR (default build/results), DOCKER_HOST (used by --collect).
set -uo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"
RESULTS_DIR="${RESULTS_DIR:-build/results}"
case "$RESULTS_DIR" in    # the render container mounts only $HOME and the repository
  "$REPO_ROOT"/*) RESULTS_DIR="${RESULTS_DIR#"$REPO_ROOT"/}" ;;
  /*) echo "RESULTS_DIR must be inside the repository ($REPO_ROOT); got $RESULTS_DIR" >&2; exit 2 ;;
esac
PDK_ROOT="${PDK_ROOT:-$HOME/.volare}"
LYP="$PDK_ROOT/sky130A/libs.tech/klayout/tech/sky130A.lyp"
IMAGE="ghcr.io/librelane/librelane:3.0.2"
ORDER="user_proj_example"

# ---------------------------------------------------------------- collect ----
render_klayout() {   # render_klayout <gds> <png> ; KLayout in the LibreLane container, batch mode
  local gds="$1" png="$2" rs
  mkdir -p build; rs="$PWD/build/.osl_render.$$.py"   # must be under $HOME: the container mounts only $HOME
  cat > "$rs" <<'PY'
import os, pya
gds, lyp, out, long_px = os.environ["R_GDS"], os.environ["R_LYP"], os.environ["R_OUT"], int(os.environ["R_PX"])
lv = pya.LayoutView()
lv.load_layout(gds, True)
lv.load_layer_props(lyp)
lv.set_config("background-color", "#ffffff")
lv.set_config("grid-visible", "false")
lv.max_hier()
lv.zoom_fit()
bb = lv.active_cellview().layout().top_cell().bbox()
w, h = bb.width(), bb.height()
if w >= h: W, H = long_px, max(1, int(long_px * h / w))
else:      W, H = max(1, int(long_px * w / h)), long_px
lv.save_image(out, W, H)
print("rendered", out, W, H)
PY
  if [ "${USE_DOCKER:-1}" = 0 ]; then   # Docker-free mode: the Nix-installed klayout (on PATH via scripts/env/lib.sh oas_env)
    ( [ -f "$REPO_ROOT/scripts/env/lib.sh" ] && { . "$REPO_ROOT/scripts/env/pins.sh"; . "$REPO_ROOT/scripts/env/lib.sh"; oas_env; }   # subshell: oas_env clobbers $d
      R_GDS="$PWD/$gds" R_LYP="$LYP" R_OUT="$PWD/$png" R_PX="${RENDER_PX:-2400}" QT_QPA_PLATFORM=offscreen klayout -b -r "$rs" )
  else
  docker run --rm -v "$HOME:$HOME" -v "$PWD:$PWD" -w "$PWD" \
    -e R_GDS="$PWD/$gds" -e R_LYP="$LYP" -e R_OUT="$PWD/$png" -e R_PX="${RENDER_PX:-2400}" \
    "$IMAGE" klayout -b -r "$rs"
  fi
  local rc=$?; rm -f "$rs"; return $rc
}

collect() {
  local d="$1"; shift
  local cpuset="" profile="tight" reuse=0 force=0 created=0 keep=0
  while [ $# -gt 0 ]; do case "$1" in
    --reuse) reuse=1; shift ;;   # use the run named in the existing resources.json
    --force) force=1; shift ;;   # always run a new flow
    --keep-run) keep=1; shift ;; # keep the run directory this script created
    --cpuset) cpuset="$2"; shift 2 ;; --profile) profile="$2"; shift 2 ;; *) echo "bad arg $1" >&2; return 2 ;; esac; done
  local out="$RESULTS_DIR/$d"
  mkdir -p "$out/reports"
  local rc=0 run=""
  if [ "$reuse" = 1 ]; then
    run="$(python3 -c "import json;print(json.load(open('$out/resources.json')).get('run_dir') or '')" 2>/dev/null)"
  elif [ "$force" = 0 ]; then
    run="$(python3 scripts/flow/find_reusable_run.py "$d" 2>/dev/null)" || run=""
  fi
  if [ -n "$run" ]; then
    echo "$d: reused $run"
    # resources.json: take the one recorded for this run if there is one, otherwise build a minimal one
    python3 scripts/flow/find_reusable_run.py --resources "$d" "$run" "$out/resources.json"
  else
    echo "$d: ran flow (profile=$profile${cpuset:+, cpuset=$cpuset})"
    created=1
    bash scripts/flow/run_capped.sh --design "$d" --profile "$profile" ${cpuset:+--cpuset "$cpuset"} \
         --out "$out/resources.json" > "$out/flow_make.log" 2>&1
    rc=$?
    run="$(python3 -c "import json;print(json.load(open('$out/resources.json')).get('run_dir') or '')" 2>/dev/null)"
  fi
  case "$run" in /*|"") ;; *) run="$REPO_ROOT/$run" ;; esac
  if [ -z "$run" ] || [ ! -d "$run/final" ]; then
    echo "$d: no finished run directory (exit $rc); see $out/flow_make.log" >&2; return 1
  fi
  local top; top="$(python3 -c "import json;print(json.load(open('$run/resolved.json'))['DESIGN_NAME'])")"
  cp "$run/final/metrics.json" "$out/metrics.json"
  cp "$run/flow.log" "$out/flow.log"
  ls "$run" | grep -E '^[0-9]+-' > "$out/steps.txt"
  (cd "$run/final" && find . -maxdepth 2 | sort) > "$out/final_listing.txt"
  cp "$run"/final/gds/*.gds "$out/$top.gds"
  cp "$run"/final/lef/*.lef "$out/$top.lef" 2>/dev/null
  # final netlists (mk/checks.mk: make gl-<name> NETLIST=final); same layout as the run: final/nl, final/pnl
  for n in nl pnl; do
    if ls "$run"/final/$n/* >/dev/null 2>&1; then mkdir -p "$out/final/$n"; cp "$run"/final/$n/* "$out/final/$n/"; fi
  done
  [ -f "$run/final/render/$top.png" ] && cp "$run/final/render/$top.png" "$out/layout_flow.png"
  # human-readable reports the flow leaves behind
  # matched by step name, not number: the numbers shift with the flow configuration
  rpt() { local f; f=$(ls "$run"/[0-9]*-"$1"/$2 2>/dev/null | tail -1); if [ -n "$f" ]; then cp "$f" "$out/reports/$3"; fi; return 0; }
  rpt openroad-stapostpnr        summary.rpt                    timing_summary.rpt
  rpt openroad-irdropreport      irdrop.rpt                     irdrop.rpt
  rpt misc-reportmanufacturability manufacturability.rpt        manufacturability.rpt
  rpt magic-drc                  reports/drc.magic.rpt          drc_magic.rpt
  rpt klayout-drc                reports/drc.klayout.json       drc_klayout.json
  rpt netgen-lvs                 reports/lvs.netgen.rpt         lvs_netgen.rpt
  # synthesis, floorplan, placement, clock tree, routing (step logs kept as .txt: *.log is git-ignored)
  rpt yosys-synthesis            reports/stat.rpt               synth_stat.rpt
  rpt yosys-synthesis            reports/pre_synth_chk.rpt      synth_checks.rpt
  rpt openroad-floorplan         openroad-floorplan.log         floorplan.txt
  rpt openroad-globalplacement   openroad-globalplacement.log   placement_global.txt
  rpt openroad-detailedplacement openroad-detailedplacement.log placement_detailed.txt
  rpt openroad-cts               cts.rpt                        cts.rpt
  rpt openroad-globalrouting     openroad-globalrouting.log     routing_global.txt
  rpt openroad-detailedrouting   openroad-detailedrouting.log   routing_detailed.txt
  rpt odb-cellfrequencytables    cell.rpt                       cell_usage.rpt
  rpt openroad-stapostpnr        max_ss_100C_1v60/max.rpt       timing_paths_max_ss.rpt
  rpt openroad-stapostpnr        min_ff_n40C_1v95/min.rpt       timing_paths_min_ff.rpt
  # the full path reports are long: keep the worst paths (the head) only
  for f in timing_paths_max_ss.rpt timing_paths_min_ff.rpt; do
    [ -f "$out/reports/$f" ] && { head -n 300 "$out/reports/$f" > "$out/reports/$f.tmp"; mv "$out/reports/$f.tmp" "$out/reports/$f"; }
  done
  # no absolute home paths in reports that may be committed
  for f in "$out"/reports/*; do sed -i.bak "s#$HOME#~#g" "$f" && rm -f "$f.bak"; done
  python3 - "$d" "$top" "$profile" "$run" "$rc" "$out/meta.json" <<'PY'
import json, sys, datetime
d, top, profile, run, rc, path = sys.argv[1:7]
json.dump({"design": d, "top": top, "profile": profile, "run_dir": run, "flow_exit": int(rc),
           "collected": datetime.datetime.now().isoformat(timespec="seconds")}, open(path, "w"), indent=2)
PY
  # own render at >= 2000 px (the flow's render is only ~1000 px); keep both
  if render_klayout "$out/$top.gds" "$out/layout.png" > "$out/render.log" 2>&1; then
    echo "$d: layout.png rendered"
  else
    echo "$d: KLayout render failed, see $out/render.log; falling back to the flow render" >&2
    [ -f "$out/layout_flow.png" ] && cp "$out/layout_flow.png" "$out/layout.png"
  fi
  if [ "$created" = 1 ] && [ "$keep" = 0 ]; then
    rm -rf "$run"; rmdir "designs/$d/runs" 2>/dev/null
    echo "$d: done -> $out (run directory deleted)"
  else
    echo "$d: done -> $out ($([ "$created" = 1 ] && echo "new run directory kept" || echo "reused run directory left in place"))"
  fi
  return 0
}

# ---------------------------------------------------------------- summary ----
summary() {   # summary <design>
  python3 - "$RESULTS_DIR/$1" <<'PY'
import json, os, sys
p = sys.argv[1]
def load(n):
    try: return json.load(open(os.path.join(p, n)))
    except Exception: return {}
m, r, meta = load("metrics.json"), load("resources.json"), load("meta.json")
def g(k, default=None): return m.get(k, default)
bbox = g("design__die__bbox", "")
try: x0, y0, x1, y1 = [float(v) for v in bbox.split()]; die = f"{x1-x0:.0f} x {y1-y0:.0f} um"
except Exception: die = "n/a"
def f(v, fmt="{:+.2f} ns"): return fmt.format(v) if isinstance(v, (int, float)) else "n/a"
def n(k):
    v = g(k); return "n/a" if v is None else str(int(v))
def hms(s):
    s = int(round(s)); return f"{s//60}m{s%60:02d}s" if s >= 60 else f"{s}s"
util = g("design__instance__utilization")
print(f"  top module      : {meta.get('top', 'n/a')}      profile: {r.get('profile', meta.get('profile', 'n/a'))}")
print(f"  die             : {die}   std cells: {n('design__instance__count__stdcell')}   utilization: {f(util*100 if util is not None else None, '{:.1f} %')}")
print(f"  worst setup     : {f(g('timing__setup__ws'))}   worst hold: {f(g('timing__hold__ws'))}   (worst over all 9 corners)")
print(f"  DRC magic/KLayout: {n('magic__drc_error__count')} / {n('klayout__drc_error__count')}   LVS errors: {n('design__lvs_error__count')}   XOR: {n('design__xor_difference__count')}   antenna: {n('route__antenna_violation__count')}")
print(f"  max-slew viol.  : {n('design__max_slew_violation__count')}   max-cap viol.: {n('design__max_cap_violation__count')}")
if r:
    print(f"  peak memory     : {r.get('container_peak_mem_gb', 'n/a')} GB   wall time: {hms(r['wall_s_total']) if 'wall_s_total' in r else 'n/a'}   flow exit: {r.get('exit_code', 'n/a')}")
    steps = r.get("steps", [])
    print(f"  flow steps ({len(steps)}), wall time each:")
    row = []
    for s in steps:
        row.append(f"{s['step']:<38}{s.get('wall_s', 0):>7.1f}s")
    for i in range(0, len(row), 2):
        print("    " + "   ".join(row[i:i+2]))
PY
}

open_image() {
  local img="$1" cmd="${OPEN_CMD:-}"
  if [ -z "$cmd" ]; then case "$(uname -s)" in Darwin) cmd=open ;; *) cmd=xdg-open ;; esac; fi
  if command -v "$cmd" >/dev/null 2>&1; then
    echo "  image           : opening $img"
    "$cmd" "$img" >/dev/null 2>&1 &
  else
    echo "  image           : $img (no '$cmd' found; open it by hand)"
  fi
}

find_klayout() {
  if [ -x /Applications/klayout.app/Contents/MacOS/klayout ]; then echo /Applications/klayout.app/Contents/MacOS/klayout
  elif command -v klayout >/dev/null 2>&1; then command -v klayout
  fi
}

open_gds() {
  local gds="$1" kl; kl="$(find_klayout)"
  if [ -z "$kl" ]; then
    echo "  KLayout         : not installed; install with:  brew install --cask klayout"
    echo "                    (GDS is at $gds)"
  elif [ ! -f "$LYP" ]; then
    echo "  KLayout         : $kl, but layer properties not found at $LYP (opening without)"
    "$kl" "$gds" >/dev/null 2>&1 &
  else
    echo "  KLayout         : opening $gds with sky130A layer properties"
    "$kl" -l "$LYP" "$gds" >/dev/null 2>&1 &
  fi
}

show_one() {  # show_one <design> <gui 0/1> <wait 0/1>
  local d="$1" gui="$2" wait="$3" dir="$RESULTS_DIR/$1"
  echo
  echo "=================================================================="
  echo " $d"
  echo "=================================================================="
  if [ ! -f "$dir/metrics.json" ]; then
    echo "  no results yet (expected $dir/). Build them with:  make collect"
    return 0
  fi
  summary "$d"
  if [ "$gui" = 1 ]; then
    [ -f "$dir/layout.png" ] && open_image "$dir/layout.png" || echo "  image           : none in $dir"
    local gds; gds="$(ls "$dir"/*.gds 2>/dev/null | head -1)"
    [ -n "$gds" ] && open_gds "$gds"
  fi
  echo "  reports         : $dir/reports/ (timing_summary.rpt, manufacturability.rpt, drc_magic.rpt, lvs_netgen.rpt, irdrop.rpt)"
  if [ "$wait" = 1 ] && [ -t 0 ]; then
    read -r -p "  -- press Enter for the next design (Ctrl-C to stop) -- " _ || true
  fi
}

# ------------------------------------------------------------------- main ----
GUI=1; LIST=0; DESIGN=""
while [ $# -gt 0 ]; do case "$1" in
  --no-gui) GUI=0; shift ;;
  --list)   LIST=1; shift ;;
  --collect) shift; [ $# -ge 1 ] || { echo "usage: $0 --collect <design> [--cpuset X] [--profile P]" >&2; exit 2; }
             collect "$@"; exit $? ;;
  -h|--help) sed -n 2,14p "$0"; exit 0 ;;
  -*) echo "unknown option: $1" >&2; exit 2 ;;
  *) DESIGN="$1"; shift ;;
esac; done

if [ "$LIST" = 1 ]; then
  for d in $ORDER; do
    if [ -f "$RESULTS_DIR/$d/metrics.json" ]; then echo "$d   $RESULTS_DIR/$d"; else echo "$d   (no results)"; fi
  done
  exit 0
fi

WAIT=$GUI
if [ -n "$DESIGN" ]; then
  show_one "$DESIGN" "$GUI" 0
else
  have=""
  for d in $ORDER; do [ -f "$RESULTS_DIR/$d/metrics.json" ] && have="$have $d"; done
  if [ -z "$have" ]; then
    echo "No results in $RESULTS_DIR/. Build them with:  make collect"; exit 0
  fi
  for d in $have; do show_one "$d" "$GUI" "$WAIT"; done
  echo; echo "Done."
fi
exit 0
