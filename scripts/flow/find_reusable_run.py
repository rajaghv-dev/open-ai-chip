#!/usr/bin/env python3
"""find_reusable_run.py <design> -- print the newest complete run directory of a design that is
still current, or nothing.

Complete = final/metrics.json, final/gds/*.gds and flow.log ending in "Flow complete." exist, and no LibreLane
           container for the design is running.
Current  = no input file was modified after the run STARTED (the timestamp in the run directory name), so an edit made
           while the run was in progress counts as stale. Inputs: every repository file under a path named in the run's
           resolved.json (for a file: all files in its directory, so include files and .mif/.dat data are covered; for a
           directory: everything under it) plus the files under the CURRENT config.json dir:: paths and designs/<design>/ except runs/, gds/, output/, model/, tb/.
Exit status 0 and a path when reusable, 1 and no output otherwise (reason on stderr).

find_reusable_run.py --resources <design> <run> <dst>   write dst (resources.json) for a reused run: copy the one
recorded for that run if there is one, else build a minimal one from the step runtime.txt files.
"""
import calendar, glob, json, os, re, subprocess, sys, time

def write_resources(d, run, dst):
    for c in ["build/results/%s/resources.json" % d, "designs/%s/output/resources.json" % d,
              "build/results/_flow_stages/%s/resources.json" % d]:
        try:
            r = json.load(open(c))
        except Exception:
            continue
        if r.get("run_dir") and os.path.basename(r["run_dir"]) == os.path.basename(run):
            if os.path.abspath(c) != os.path.abspath(dst):
                json.dump(r, open(dst, "w"), indent=2)
            return
    def secs(p):
        try: t = open(p).read().strip()
        except OSError: return None
        m = re.match(r"(\d+):(\d+):([\d.]+)", t)
        return int(m[1]) * 3600 + int(m[2]) * 60 + float(m[3]) if m else None
    steps = [{"step": os.path.basename(x), "wall_s": secs(os.path.join(x, "runtime.txt")) or 0}
             for x in sorted(glob.glob(os.path.join(run, "[0-9]*-*"))) if os.path.isdir(x)]
    json.dump({"design": d, "profile": "unknown (reused run)", "exit_code": 0, "run_dir": run,
               "wall_s_total": round(sum(x["wall_s"] for x in steps)), "container_peak_mem_gb": "n/a", "steps": steps,
               "note": "built from the run's runtime.txt files; no resources.json was recorded for this run"},
              open(dst, "w"), indent=2)

if sys.argv[1] == "--resources":
    write_resources(*sys.argv[2:5]); sys.exit(0)

d = sys.argv[1]
root = os.environ.get("FIND_RUN_ROOT") or os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
home = os.path.expanduser("~")
SKIP = ("runs", "gds", "output", "model", "tb", ".git", "__pycache__")   # design dir: only config, pin order, SDC and rtl/ are inputs


def run_start(run):
    """Start of the run = the timestamp LibreLane puts in the directory name (RUN_YYYY-MM-DD_HH-MM-SS, UTC),
    taken when the flow starts; an edit made while the run is in progress is therefore newer than this."""
    m = re.match(r"RUN_(\d+)-(\d+)-(\d+)_(\d+)-(\d+)-(\d+)$", os.path.basename(run))
    if m:
        return calendar.timegm(tuple(int(x) for x in m.groups()))   # the name is in UTC
    return None


def container_running(design):
    """True if a LibreLane container for this design is up (named oac_cap_<design>_*, or --design-dir .../<design>)."""
    try:
        out = subprocess.run(["docker", "ps", "--no-trunc", "--format", "{{.Names}}\t{{.Command}}"],
                             capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return False
    for line in out.splitlines():
        if ("oac_cap_%s_" % design) in line or ("/designs/%s " % design) in line or ("/designs/%s/" % design) in line:
            return True
    return False


def walk(path, out):
    if os.path.isfile(path):
        out.add(path)
    elif os.path.isdir(path):
        for dp, dn, fn in os.walk(path):
            dn[:] = [x for x in dn if x not in SKIP]
            for f in fn:
                out.add(os.path.join(dp, f))


def inputs_of(run):
    """Every file the flow could have read from the repository: for each path-valued key of the run's resolved.json
    that points into the repository, the file itself, and for a file also every file in its directory (include files,
    $readmemh/.mif data) and for a directory every file under it; plus everything under designs/<design>/."""
    files = set()
    paths = []
    try:
        r = json.load(open(os.path.join(run, "resolved.json")))
    except Exception:
        r = {}

    def collect(v):
        if isinstance(v, str):
            paths.append(v.split("::", 1)[-1])
        elif isinstance(v, (list, tuple)):
            for x in v: collect(x)
        elif isinstance(v, dict):
            for x in v.values(): collect(x)
    collect(list(r.values()))
    base = os.path.realpath(root)
    rec = r.get("DESIGN_DIR")                      # repository root as recorded by the run (differs only in FIND_RUN_ROOT tests)
    rec = os.path.dirname(os.path.dirname(rec)) if rec else None
    for p in paths:
        if rec and p.startswith(rec + os.sep):
            p = base + p[len(rec):]
        if not p.startswith("/"):
            continue                                # resolved.json records absolute paths; plain strings are not paths
        rp = os.path.realpath(p)
        if not rp.startswith(base + os.sep) or not os.path.exists(rp):
            continue
        if os.path.isdir(rp):
            walk(rp, files)
        else:
            walk(os.path.dirname(rp), files)
    walk(os.path.join(root, "designs", d), files)
    # the CURRENT config.json: its dir:: targets (a design may take its RTL from another design or from shared/)
    cfg = os.path.join(root, "designs", d, "config.json")
    try:
        c = json.load(open(cfg))
    except Exception:
        c = {}
    vals = []
    collect_cfg = lambda v: vals.extend([v] if isinstance(v, str) else (v if isinstance(v, list) else []))
    for k, v in c.items():
        collect_cfg(v)
    for v in vals:
        if isinstance(v, str) and v.startswith("dir::"):
            rp = os.path.normpath(os.path.join(os.path.dirname(cfg), v[5:]))
            if os.path.isdir(rp): walk(rp, files)
            elif os.path.isfile(rp): walk(os.path.dirname(rp), files)
    # a design dir's own run/gds directories are skipped by walk(); resolved.json lives in the run
    return files


runs = sorted(glob.glob(os.path.join(root, "designs", d, "runs", "RUN_*")), reverse=True)
if not runs:
    print("no run directories", file=sys.stderr); sys.exit(1)
if container_running(d):
    print("a flow container for %s is running; not reusing" % d, file=sys.stderr); sys.exit(1)
for run in runs:
    m = os.path.join(run, "final", "metrics.json")
    log = os.path.join(run, "flow.log")
    ok = (os.path.isfile(m) and glob.glob(os.path.join(run, "final", "gds", "*.gds")) and os.path.isfile(log)
          and "Flow complete." in open(log, errors="replace").read()[-2000:])
    if not ok:
        continue                                    # incomplete, failed or still running
    start = run_start(run)
    if start is None:
        start = min((os.path.getmtime(os.path.join(run, x)) for x in os.listdir(run)), default=0)
    newer = sorted(f for f in inputs_of(run) if os.path.isfile(f) and os.path.getmtime(f) > start)
    if newer:
        print("newest complete run %s started before %s was modified" % (os.path.basename(run), os.path.relpath(newer[0], root)),
              file=sys.stderr)
        sys.exit(1)
    print(run); sys.exit(0)
print("no complete run", file=sys.stderr); sys.exit(1)
