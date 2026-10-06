#!/usr/bin/env python3
# Docs: .claude/skills/harden-design/SKILL.md, docs/GUI_AND_LOGS.md
"""find_reusable_run.py <design> -- print the newest complete run directory of a design that is
still current, or nothing.

Complete = final/metrics.json, final/gds/*.gds and flow.log ending in "Flow complete." exist, and no LibreLane
           container for the design is running.
Current  = no input file was modified after the run STARTED (the timestamp in the run directory name), so an edit made
           while the run was in progress counts as stale. Inputs: every repository file under a path named in the run's
           resolved.json (for a file: all files in its directory, so include files and .mif/.dat data are covered; for a
           directory: everything under it) plus the files under the CURRENT config.json dir:: paths and designs/<design>/ except runs/, gds/, output/, model/, tb/.
Exit status 0 and a path when reusable, 1 and no output otherwise (reason on stderr).

Macros: a design whose config.json has MACROS (an elaborate-only wrapper) also depends on every file referenced by the dir::
paths inside MACROS (the exported views under build/macros/<macro>/), so a wrapper run is stale once a macro view changed.
find_reusable_run.py --macros <design>                      print the MACROS keys that are designs of this repository
find_reusable_run.py --export-views [--if-needed] <macro>   copy the macro's views from its current run into build/macros/<macro>/
                                                            (gds lef nl pnl spef lib + SOURCE.txt with run dir and sha256s);
                                                            --if-needed: skip when SOURCE.txt already names the current run and
                                                            every file still matches its recorded hash. Exit 1 with a message
                                                            ("run make gds DESIGN=<macro>") when the macro has no current run.

find_reusable_run.py --resources <design> <run> <dst>   write dst (resources.json) for a reused run: copy the one
recorded for that run if there is one, else build a minimal one from the step runtime.txt files.
"""
import calendar, glob, hashlib, json, os, re, shutil, subprocess, sys, time

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
        if ("oac_cap_%s_" % design) in line:
            return True
        # only a LibreLane flow on this design counts (other containers, e.g. a simulator reading this design's
        # run files, must not make the finished runs look in-progress)
        if "librelane" in line and (("/designs/%s " % design) in line or ("/designs/%s/" % design) in line):
            return True
    return False


def walk(path, out):
    if os.path.isfile(path):
        out.add(path)
    elif os.path.isdir(path):
        # SKIP (runs, gds, tb, ...) applies to design directories only: build/macros/<m>/gds holds real inputs
        skip = SKIP if os.path.realpath(path).startswith(os.path.realpath(os.path.join(root, "designs")) + os.sep) else ()
        for dp, dn, fn in os.walk(path):
            dn[:] = [x for x in dn if x not in skip]
            for f in fn:
                if f.endswith(".md"):          # documentation (README.md, NOTES.md) cannot change the silicon
                    continue
                out.add(os.path.join(dp, f))


def inputs_of(run, d):
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

    def collect_cfg(v):                              # nested too: MACROS is a dict of dicts of lists
        if isinstance(v, str): vals.append(v)
        elif isinstance(v, (list, tuple)):
            for x in v: collect_cfg(x)
        elif isinstance(v, dict):
            for x in v.values(): collect_cfg(x)
    collect_cfg(list(c.values()))
    for v in vals:
        if isinstance(v, str) and v.startswith("dir::"):
            rp = os.path.normpath(os.path.join(os.path.dirname(cfg), v[5:]))
            if os.path.isdir(rp): walk(rp, files)
            elif os.path.isfile(rp): walk(os.path.dirname(rp), files)
    # a design dir's own run/gds directories are skipped by walk(); resolved.json lives in the run
    return files


def find_run(d):
    """(run_dir, None) for the newest complete, current run of design d, else (None, reason)."""
    runs = sorted(glob.glob(os.path.join(root, "designs", d, "runs", "RUN_*")), reverse=True)
    if not runs:
        return None, "no run directories"
    if container_running(d):
        return None, "a flow container for %s is running; not reusing" % d
    for run in runs:
        m = os.path.join(run, "final", "metrics.json")
        log = os.path.join(run, "flow.log")
        ok = (os.path.isfile(m) and glob.glob(os.path.join(run, "final", "gds", "*.gds")) and os.path.isfile(log)
              and "Flow complete." in open(log, errors="replace").read()[-2000:])
        if not ok:
            continue                                # incomplete, failed or still running
        start = run_start(run)
        if start is None:
            start = min((os.path.getmtime(os.path.join(run, x)) for x in os.listdir(run)), default=0)
        newer = sorted(f for f in inputs_of(run, d) if os.path.isfile(f) and os.path.getmtime(f) > start)
        if newer:
            return None, "newest complete run %s started before %s was modified" % (os.path.basename(run), os.path.relpath(newer[0], root))
        return run, None
    return None, "no complete run"


# ---- macros and their exported views (build/macros/<macro>/) ----
def macros_of(d):
    try:
        c = json.load(open(os.path.join(root, "designs", d, "config.json")))
    except Exception:
        return []
    return [k for k in (c.get("MACROS") or {}) if os.path.isfile(os.path.join(root, "designs", k, "config.json"))]


def view_files(m, final):
    """[(source, destination relative to build/macros/<m>)] for every view of macro m in a run's final/ directory."""
    items = [("gds/%s.gds" % m, "gds/%s.gds" % m), ("lef/%s.lef" % m, "lef/%s.lef" % m),
             ("nl/%s.nl.v" % m, "nl/%s.nl.v" % m), ("pnl/%s.pnl.v" % m, "pnl/%s.pnl.v" % m)]
    items += [("spef/%s/%s.%s.spef" % (c, m, c),) * 2 for c in ("min", "nom", "max")]
    libs = sorted(glob.glob(os.path.join(final, "lib", "*", m + "__*.lib")))
    items += [(os.path.relpath(x, final),) * 2 for x in libs]
    missing = [s for s, _ in items if not os.path.isfile(os.path.join(final, s))]
    if missing or not libs:
        raise SystemExit("export-views: %s is missing from %s" % (", ".join(missing) or "lib/<corner>/%s__<corner>.lib" % m, final))
    return [(os.path.join(final, s), dst) for s, dst in items]


def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(1 << 20), b""): h.update(b)
    return h.hexdigest()


def views_current(m, run):
    """True when build/macros/<m>/SOURCE.txt names this run and every recorded file still has its recorded hash."""
    dst = os.path.join(root, "build", "macros", m)
    try:
        lines = open(os.path.join(dst, "SOURCE.txt")).read().splitlines()
    except OSError:
        return False
    if not any(l == "run: " + os.path.relpath(run, root) for l in lines):
        return False
    ents = [l.split(None, 1) for l in lines if re.match(r"[0-9a-f]{64}  ", l)]
    if not ents:
        return False
    return all(os.path.isfile(os.path.join(dst, f)) and sha256(os.path.join(dst, f)) == h for h, f in ents)


def export_views(m, if_needed):
    if not os.path.isfile(os.path.join(root, "designs", m, "config.json")):
        sys.exit("export-views: unknown design '%s'" % m)
    run, why = find_run(m)
    if not run:
        sys.exit("export-views: %s has no current run (%s).\nRun:  make gds DESIGN=%s   then   make views DESIGN=%s" % (m, why, m, m))
    if if_needed and views_current(m, run):
        print("views: build/macros/%s is current (%s)" % (m, os.path.basename(run))); return
    final = os.path.join(run, "final")
    dst = os.path.join(root, "build", "macros", m)
    tmp = dst + ".tmp"
    shutil.rmtree(tmp, ignore_errors=True)
    rows = []
    for src, rel_dst in view_files(m, final):
        os.makedirs(os.path.dirname(os.path.join(tmp, rel_dst)), exist_ok=True)
        shutil.copyfile(src, os.path.join(tmp, rel_dst))
        rows.append((sha256(os.path.join(tmp, rel_dst)), rel_dst))
    with open(os.path.join(tmp, "SOURCE.txt"), "w") as f:
        f.write("macro: %s\nrun: %s\nexported: %s\nsha256 of every file below (relative to this directory):\n"
                % (m, os.path.relpath(run, root), time.strftime("%Y-%m-%dT%H:%M:%S%z")))
        for h, r in rows: f.write("%s  %s\n" % (h, r))
    shutil.rmtree(dst, ignore_errors=True)
    os.rename(tmp, dst)
    print("views: build/macros/%s <- %s (%d files)" % (m, os.path.relpath(run, root), len(rows)))


if __name__ == "__main__":
    a = sys.argv[1:]
    if not a:
        sys.exit(__doc__)
    if a[0] == "--resources":
        write_resources(*a[1:4]); sys.exit(0)
    if a[0] == "--macros":
        print("\n".join(macros_of(a[1]))); sys.exit(0)
    if a[0] == "--export-views":
        need = "--if-needed" in a
        export_views([x for x in a[1:] if x != "--if-needed"][0], need); sys.exit(0)
    run, why = find_run(a[0])
    if run:
        print(run); sys.exit(0)
    print(why, file=sys.stderr); sys.exit(1)
