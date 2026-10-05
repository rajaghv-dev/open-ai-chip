#!/usr/bin/env bash
# run_capped.sh — run one design under a resource profile and write resources.json
#
# Usage: scripts/flow/run_capped.sh --design <name> [--profile tight|actions] [--out FILE]
#
# Runs `make flow DESIGN=<design> PROFILE=<profile>` with the LibreLane container named,
# samples the container's cgroup memory.peak while it runs, then reads the
# flow's own per-step statistics from the newest run directory and writes
# resources.json (default designs/<design>/output/resources.json).
# Honors DOCKER_HOST / DOCKER_CONTEXT from the environment. FLOW_TIMEOUT (default 600 s) stops a runaway run.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"

DESIGN=""; PROFILE="tight"; OUT=""; CPUSET=""
while [ $# -gt 0 ]; do
  case "$1" in
    --design)  DESIGN="$2"; shift 2 ;;
    --profile) PROFILE="$2"; shift 2 ;;
    --out)     OUT="$2"; shift 2 ;;
    --cpuset)  CPUSET="$2"; shift 2 ;;
    -h|--help) sed -n 2,11p "$0"; exit 0 ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
done
[ -n "$DESIGN" ] || { echo "usage: $0 --design <name> [--profile tight|actions] [--out FILE] [--cpuset 2-3]" >&2; exit 2; }
case "$PROFILE" in tight|actions) ;; *) echo "profile must be tight or actions" >&2; exit 2 ;; esac

# any design directory under designs/ with a config.json: `make flow DESIGN=<dir>` runs LibreLane on it
[ -f "designs/$DESIGN/config.json" ] || { echo "unknown design: $DESIGN (no designs/$DESIGN/config.json)" >&2; exit 2; }
[ -n "$OUT" ] || OUT="designs/$DESIGN/output/resources.json"
mkdir -p "$(dirname "$OUT")"

CNAME="oac_cap_${DESIGN}_$$"
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"; docker rm -f "$CNAME" >/dev/null 2>&1 || true' EXIT
MARK="$WORK/start.mark"; touch "$MARK"
T0=$(date +%s)

make flow DESIGN="$DESIGN" PROFILE="$PROFILE" CPUSET="$CPUSET" DOCKER_EXTRA="--name $CNAME" &
MAKE_PID=$!

# Sample the container's cgroup while it runs: "epoch current_bytes peak_bytes"
: > "$WORK/samples.txt"
FLOW_TIMEOUT="${FLOW_TIMEOUT:-600}"   # seconds; these designs are tiny, so a run past 10 minutes is a runaway (e.g. repair thrash)
while kill -0 "$MAKE_PID" 2>/dev/null; do
  if [ $(( $(date +%s) - T0 )) -gt "$FLOW_TIMEOUT" ]; then
    echo "run_capped: TIMEOUT after ${FLOW_TIMEOUT} s: stopping container $CNAME (raise FLOW_TIMEOUT only if the design really needs it)" >&2
    docker rm -f "$CNAME" >/dev/null 2>&1; kill "$MAKE_PID" 2>/dev/null; break
  fi
  line=$(docker exec "$CNAME" sh -c \
    'cat /sys/fs/cgroup/memory.current /sys/fs/cgroup/memory.peak 2>/dev/null || cat /sys/fs/cgroup/memory/memory.usage_in_bytes /sys/fs/cgroup/memory/memory.max_usage_in_bytes 2>/dev/null' \
    2>/dev/null | tr '\n' ' ') || line=""
  [ -n "$line" ] && echo "$(date +%s) $line" >> "$WORK/samples.txt"
  sleep 2
done
set +e; wait "$MAKE_PID"; RC=$?; set -e
T1=$(date +%s)

RUN_DIR=$(find "designs/$DESIGN/runs" -maxdepth 1 -type d -name 'RUN_*' -newer "$MARK" 2>/dev/null | sort | tail -1 || true)

python3 - "$DESIGN" "$PROFILE" "$RC" "$T0" "$T1" "$WORK/samples.txt" "${RUN_DIR:-}" "$OUT" "$CNAME" "$CPUSET" <<'PY'
import json, os, re, sys, glob
design, profile, rc, t0, t1, samples, run_dir, out, cname, cpuset = sys.argv[1:11]
t0, t1, rc = int(t0), int(t1), int(rc)
S = []
for l in open(samples):
    p = l.split()
    if len(p) >= 3: S.append((int(p[0]), int(p[1]), int(p[2])))
peak = max((s[2] for s in S), default=0)
peak = max(peak, max((s[1] for s in S), default=0))

def mem_between(a, b):
    v = [s[1] for s in S if a <= s[0] <= b]
    return max(v) if v else None

def parse_runtime(path):
    """LibreLane runtime.txt / process_stats time: 'HH:MM:SS.mmm' -> seconds."""
    try: txt = open(path).read().strip()
    except OSError: return None
    m = re.match(r'(\d+):(\d+):([\d.]+)', txt)
    return int(m[1])*3600 + int(m[2])*60 + float(m[3]) if m else None

def parse_size(txt):
    m = re.match(r'([\d.]+)\s*([KMGT]?i?B)', str(txt))
    if not m: return None
    return int(float(m[1]) * {'B':1,'KiB':2**10,'MiB':2**20,'GiB':2**30,'TiB':2**40}.get(m[2], 1))

steps = []
if run_dir and os.path.isdir(run_dir):
    for d in sorted(glob.glob(os.path.join(run_dir, '[0-9]*-*'))):
        if not os.path.isdir(d): continue
        e = {"step": os.path.basename(d)}
        wall = parse_runtime(os.path.join(d, 'runtime.txt'))
        if wall is not None: e["wall_s"] = wall
        # LibreLane's own per-subprocess statistics (<step>.process_stats.json)
        rss = []
        for f in glob.glob(os.path.join(d, '*.process_stats.json')):
            try:
                v = parse_size(json.load(open(f)).get('peak_resources', {}).get('memory_rss'))
                if v: rss.append(v)
            except (OSError, ValueError): pass
        if rss: e["peak_rss_bytes_flow_stats"] = max(rss)
        # window of the step from file mtimes (dir ctime .. last file mtime)
        try:
            files = [os.path.join(d, f) for f in os.listdir(d)]
            ts = [os.path.getmtime(f) for f in files] or [os.path.getmtime(d)]
            a, b = int(os.path.getctime(d)), int(max(ts)) + 1
            m = mem_between(a, b)
            if m is not None: e["peak_mem_bytes_sampled"] = m
        except OSError: pass
        steps.append(e)

doc = {
  "design": design, "profile": profile,
  "limits": {"tight": {"cpus": 2, "memory_gb": 8}, "actions": {"cpus": 4, "memory_gb": 16}}[profile],
  "cpuset": cpuset or {"tight": "0-1", "actions": "0-3"}[profile],
  "exit_code": rc,
  "wall_s_total": t1 - t0,
  "container_peak_mem_bytes": peak,
  "container_peak_mem_gb": round(peak / 2**30, 3),
  "samples": len(S),
  "run_dir": run_dir or None,
  "peak_rss_bytes_flow_stats_max": max((x.get("peak_rss_bytes_flow_stats", 0) for x in steps), default=0),
  "steps": steps,
  "note": "wall_s and peak_rss_bytes_flow_stats come from LibreLane per-step runtime.txt / process_stats.json (steps with no subprocess have no rss). container peak = cgroup memory.peak (sampled every 2 s; the last sample before exit); per-step memory is the max of memory.current samples inside the step's file-time window, so it is a lower bound.",
}
json.dump(doc, open(out, 'w'), indent=2)
print(f"resources.json -> {out}: rc={rc} wall={doc['wall_s_total']}s peak={doc['container_peak_mem_gb']} GB steps={len(steps)}")
PY
exit "$RC"
