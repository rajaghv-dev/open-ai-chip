#!/usr/bin/env python3
"""Build build/grafana/chip.db (SQLite) from the committed evidence, for the local Grafana dashboards.

Sources (all read-only): designs/*/output/{metrics,resources}.json, designs/FROZEN.json, build/agent/memory/runs.md,
build/agent/jobs/*.log, build/whatif/*, examples/hermes_desktop/eval_tools/results_summary_*.json,
build/agent/proof/calls.jsonl. Stdlib only, offline, idempotent: the db is rebuilt from scratch each run.
Docs: docs/GRAFANA.md. Make target: make grafana-db.
"""
import argparse
import glob
import json
import os
import re
import sqlite3
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_DB = os.path.join(ROOT, "build", "grafana", "chip.db")

SCHEMA = """
CREATE TABLE designs (name TEXT PRIMARY KEY, family TEXT, cells INTEGER, ff INTEGER, die_um2 REAL, util REAL,
  setup_ws REAL, setup_corner TEXT, hold_ws REAL, hold_corner TEXT, drc INTEGER, lvs INTEGER, xor INTEGER,
  antenna INTEGER, slew_viol INTEGER, power_w REAL, flow_s REAL, peak_gb REAL, frozen INTEGER, signoff TEXT);
CREATE TABLE runs (id TEXT PRIMARY KEY, ts INTEGER, time TEXT, source TEXT, design TEXT, cmd TEXT, result TEXT,
  secs REAL, log TEXT);
CREATE TABLE whatifs (tag TEXT PRIMARY KEY, design TEXT, changes TEXT, cells INTEGER, die_um2 REAL, util REAL,
  setup_ws REAL, hold_ws REAL, wall_s REAL, peak_gb REAL, base_cells INTEGER, base_setup_ws REAL, base_hold_ws REAL,
  d_setup_ws REAL, d_hold_ws REAL, d_cells INTEGER);
CREATE TABLE eval_scores (backend TEXT, model TEXT, date TEXT, grp TEXT, cases INTEGER, passed INTEGER,
  accuracy_pct REAL);
CREATE TABLE proof_calls (ts INTEGER, time TEXT, tool TEXT, status INTEGER, ms REAL, bytes INTEGER, internal INTEGER);
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
"""


def jload(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def family(name):
    for pre, fam in (("kv_attn_", "kv_attn"), ("prec_", "precision"), ("soc_", "soc"),
                     ("user_project_wrapper", "wrapper"), ("vision_", "tiny_ai"), ("text_", "tiny_ai"),
                     ("tiny_ai", "tiny_ai"), ("audio_", "audio"), ("image_text", "tiny_ai"),
                     ("user_proj", "template")):
        if name.startswith(pre):
            return fam
    return "other"


def worst(m, kind):
    """Worst (min) slack over all corners and the corner name."""
    best = (None, "")
    for k, v in m.items():
        mm = re.match(r"timing__%s__ws__corner:(.+)$" % kind, k)
        if mm and isinstance(v, (int, float)) and v == v and abs(v) != float("inf"):
            if best[0] is None or v < best[0]:
                best = (v, mm.group(1))
    if best[0] is None and isinstance(m.get("timing__%s__ws" % kind), (int, float)):
        best = (m["timing__%s__ws" % kind], "all")
    return best


def metrics_row(m):
    s, sc = worst(m, "setup")
    h, hc = worst(m, "hold")
    g = m.get
    return dict(cells=g("design__instance__count__stdcell"), ff=g("design__instance__count__class:sequential_cell"),
                die_um2=g("design__die__area"), util=g("design__instance__utilization"),
                setup_ws=s, setup_corner=sc, hold_ws=h, hold_corner=hc,
                drc=(g("magic__drc_error__count") or 0) + (g("klayout__drc_error__count") or 0),
                lvs=(g("design__lvs_error__count") or 0) + (g("design__lvs_net_difference__count") or 0)
                + (g("design__lvs_device_difference__count") or 0),
                xor=g("design__xor_difference__count") or 0,
                antenna=(g("antenna__violating__nets") or 0) + (g("route__antenna_violation__count") or 0),
                slew_viol=g("design__max_slew_violation__count") or 0, power_w=g("power__total"))


def do_designs(db):
    frozen = (jload(os.path.join(ROOT, "designs", "FROZEN.json")) or {}).get("designs", {})
    n = 0
    for d in sorted(glob.glob(os.path.join(ROOT, "designs", "*", "output", "metrics.json"))):
        name = d.split(os.sep)[-3]
        m = jload(d)
        if not m:
            continue
        r = metrics_row(m)
        res = jload(os.path.join(os.path.dirname(d), "resources.json")) or {}
        # slew/cap counts are informational (check_signoff.py never fails on them), so they are not in the verdict
        ok = (r["drc"] == 0 and r["lvs"] == 0 and r["xor"] == 0 and r["antenna"] == 0
              and (r["setup_ws"] or 0) >= 0 and (r["hold_ws"] or 0) >= 0)
        db.execute("INSERT INTO designs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (name, family(name), r["cells"], r["ff"], r["die_um2"], r["util"], r["setup_ws"],
                    r["setup_corner"], r["hold_ws"], r["hold_corner"], r["drc"], r["lvs"], r["xor"], r["antenna"],
                    r["slew_viol"], r["power_w"], res.get("wall_s_total"), res.get("container_peak_mem_gb"),
                    1 if name in frozen else 0, "PASS" if ok else "FAIL"))
        n += 1
    return n


def epoch(s, fmt):
    try:
        return int(datetime.strptime(s, fmt).timestamp())
    except ValueError:
        return None


def do_runs(db):
    seen = set()
    for p in sorted(glob.glob(os.path.join(ROOT, "build", "agent", "jobs", "*.log"))):
        rel = os.path.relpath(p, ROOT)
        base = os.path.basename(p)[:-4]
        try:
            lines = open(p, errors="replace").read().splitlines()
        except OSError:
            continue
        cmd = lines[0][2:] if lines and lines[0].startswith("$ ") else ""
        res, secs = "UNKNOWN", None
        for ln in reversed(lines[-5:]):
            mm = re.match(r"\[exit (-?\d+) after ([\d.]+)s\]", ln)
            if mm:
                res = "PASS" if mm.group(1) == "0" else "FAIL"
                secs = float(mm.group(2))
                break
        dm = re.search(r"DESIGN=(\w+)", cmd) or re.search(r"--design (\w+)", cmd)
        ts = epoch(base[:15], "%Y%m%d_%H%M%S")
        db.execute("INSERT OR REPLACE INTO runs VALUES (?,?,?,?,?,?,?,?,?)",
                   (base, ts, datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S") if ts else "", "job",
                    dm.group(1) if dm else "", cmd, res, secs, rel))
        seen.add(rel)
    runs_md = os.path.join(ROOT, "build", "agent", "memory", "runs.md")
    if os.path.exists(runs_md):
        for ln in open(runs_md, errors="replace"):
            mm = re.match(r"- \[(\w+)\] (\d{4}-\d\d-\d\d \d\d:\d\d) design=(\S+) \| cmd=(.*?) \| result=(\w+)"
                          r"(?: \| numbers=(.*?))?(?: \| log=(\S+))?\s*$", ln)
            if not mm or (mm.group(7) in seen):
                continue
            rid, t, design, cmd, res = mm.group(1), mm.group(2), mm.group(3), mm.group(4), mm.group(5)
            db.execute("INSERT OR REPLACE INTO runs VALUES (?,?,?,?,?,?,?,?,?)",
                       (rid, epoch(t, "%Y-%m-%d %H:%M"), t + ":00", "memory", "" if design == "-" else design,
                        cmd, res, None, mm.group(7) or ""))
    return db.execute("SELECT COUNT(*) FROM runs").fetchone()[0]


def do_whatifs(db):
    n = 0
    for meta in sorted(glob.glob(os.path.join(ROOT, "build", "whatif", "*", "whatif_meta.json"))):
        d = os.path.dirname(meta)
        mt = jload(meta) or {}
        mets = glob.glob(os.path.join(d, "runs", "*", "final", "metrics.json"))
        if not mets:
            continue
        r = metrics_row(jload(mets[0]) or {})
        res = jload(os.path.join(d, "whatif_resources.json")) or {}
        base = jload(os.path.join(ROOT, "designs", mt.get("design", ""), "output", "metrics.json"))
        b = metrics_row(base) if base else None
        dsu = None if not b or r["setup_ws"] is None or b["setup_ws"] is None else r["setup_ws"] - b["setup_ws"]
        dho = None if not b or r["hold_ws"] is None or b["hold_ws"] is None else r["hold_ws"] - b["hold_ws"]
        dce = None if not b or r["cells"] is None or b["cells"] is None else r["cells"] - b["cells"]
        db.execute("INSERT OR REPLACE INTO whatifs VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                   (mt.get("tag", os.path.basename(d)), mt.get("design"), json.dumps(mt.get("changes", {})),
                    r["cells"], r["die_um2"], r["util"], r["setup_ws"], r["hold_ws"], res.get("wall_s_total"),
                    res.get("container_peak_mem_gb"), b and b["cells"], b and b["setup_ws"], b and b["hold_ws"],
                    dsu, dho, dce))
        n += 1
    return n


def do_evals(db):
    n = 0
    for p in sorted(glob.glob(os.path.join(ROOT, "examples", "hermes_desktop", "eval_tools",
                                           "results_summary_*.json"))):
        d = jload(p) or {}
        backend = os.path.basename(p)[len("results_summary_"):-5]
        model = d.get("model", "")
        date = d.get("date", "")
        db.execute("INSERT INTO eval_scores VALUES (?,?,?,?,?,?,?)",
                   (backend, model, date, "ALL", d.get("scored"), d.get("passed"), d.get("accuracy_pct")))
        n += 1
        for g, v in (d.get("groups") or {}).items():
            db.execute("INSERT INTO eval_scores VALUES (?,?,?,?,?,?,?)",
                       (backend, model, date, g, v.get("cases"), v.get("passed"), v.get("accuracy_pct")))
            n += 1
    return n


def do_proof(db):
    p = os.path.join(ROOT, "build", "agent", "proof", "calls.jsonl")
    n = 0
    if os.path.exists(p):
        for ln in open(p, errors="replace"):
            try:
                c = json.loads(ln)
            except ValueError:
                continue
            db.execute("INSERT INTO proof_calls VALUES (?,?,?,?,?,?,?)",
                       (int(c.get("ts", 0)), c.get("time", ""), c.get("tool", ""), c.get("status"), c.get("ms"),
                        c.get("result_bytes"), 1 if c.get("internal") else 0))
            n += 1
    return n


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default=DEFAULT_DB)
    a = ap.parse_args()
    os.makedirs(os.path.dirname(a.db), exist_ok=True)
    tmp = a.db + ".tmp"
    if os.path.exists(tmp):
        os.remove(tmp)
    db = sqlite3.connect(tmp)
    db.executescript(SCHEMA)
    counts = dict(designs=do_designs(db), runs=do_runs(db), whatifs=do_whatifs(db), eval_scores=do_evals(db),
                  proof_calls=do_proof(db))
    db.execute("INSERT INTO meta VALUES ('built', ?)", (datetime.now().strftime("%Y-%m-%d %H:%M:%S"),))
    db.commit()
    db.close()
    os.replace(tmp, a.db)
    os.chmod(a.db, 0o644)
    print("grafana db: %s" % os.path.relpath(a.db, ROOT) if a.db.startswith(ROOT) else a.db)
    print("  " + ", ".join("%s=%d" % kv for kv in counts.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
