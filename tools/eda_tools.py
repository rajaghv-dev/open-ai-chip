#!/usr/bin/env python3
"""Read-only EDA tool layer for a local LLM agent (see tools/README.md).

Contract:  TOOLS  -> list of OpenAI/Ollama style tool schemas
           call(name, args) -> dict   (never raises; {"error": "..."} on bad input)
Nothing here modifies the repository; the only writes are PNG renders under build/agent/.
Docs: tools/README.md, docs/HERMES_AGENT.md
"""
import glob
import json
import math
import os
import re
import subprocess
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(REPO, "scripts", "lib"))
import repo  # noqa: E402  (scripts/lib/repo.py: design list, config.json, DOCKER_HOST default)

AGENT_DIR = os.path.join(REPO, "build", "agent")
RENDER_DIR = os.path.join(AGENT_DIR, "renders")
PDK_ROOT = os.environ.get("PDK_ROOT", os.path.expanduser("~/.volare"))
LYP = os.path.join(PDK_ROOT, "sky130A", "libs.tech", "klayout", "tech", "sky130A.lyp")
MAX_ITEMS = 50

# sky130 layer/datatype -> name (subset of the PDK map; enough for routing and cells)
# sky130A GDS (layer, datatype) -> human name, used only to label output; datatype 20 = drawing, 16 = pin, 5 = label,
# 44 = via/contact cut. Source: the sky130A layer map in the PDK (libs.tech/klayout).
LAYER_NAMES = {
    (64, 20): "nwell drawing", (65, 20): "diff drawing", (65, 44): "tap", (66, 20): "poly drawing",
    (66, 44): "licon1", (67, 20): "li1 drawing", (67, 16): "li1 pin", (67, 5): "li1 label",
    (67, 44): "mcon", (68, 20): "met1 drawing", (68, 16): "met1 pin", (68, 5): "met1 label",
    (68, 44): "via", (69, 20): "met2 drawing", (69, 16): "met2 pin", (69, 5): "met2 label",
    (69, 44): "via2", (70, 20): "met3 drawing", (70, 16): "met3 pin", (70, 5): "met3 label",
    (70, 44): "via3", (71, 20): "met4 drawing", (71, 16): "met4 pin", (71, 5): "met4 label",
    (71, 44): "via4", (72, 20): "met5 drawing", (72, 16): "met5 pin", (72, 5): "met5 label",
    (93, 44): "nsdm", (94, 20): "psdm", (95, 20): "npc", (78, 44): "pwell/ nwell contact",
    (235, 4): "areaid boundary", (81, 4): "areaid.sc", (83, 44): "areaid.lvt", (122, 16): "prBoundary-like",
    (66, 13): "poly fuse", (125, 44): "pnp/ id", (235, 0): "areaid", (236, 0): "areaid",
}
# Short names the model may pass as `layer`; anything else must be an explicit 'L/D' pair (see _parse_layer).
LAYER_ALIASES = {"li1": (67, 20), "met1": (68, 20), "met2": (69, 20), "met3": (70, 20),
                 "met4": (71, 20), "met5": (72, 20), "poly": (66, 20), "diff": (65, 20),
                 "mcon": (67, 44), "via": (68, 44), "via2": (69, 44), "via3": (70, 44), "via4": (71, 44)}

METRIC_HELP = [
    (r"^design__instance__count__stdcell$", "standard cells placed (incl. fill/tap/buffers)"),
    (r"^design__instance__count$", "total instances"),
    (r"^design__instance__area", "instance area, um^2"),
    (r"^design__die__area$", "die area, um^2"),
    (r"^design__core__area$", "core area, um^2"),
    (r"design__instance__count__class:sequential", "flip-flops/latches (sequential cells)"),
    (r"^timing__setup__ws", "worst setup slack, ns (negative = violation)"),
    (r"^timing__hold__ws", "worst hold slack, ns (negative = violation)"),
    (r"^timing__(setup|hold)__(tns|wns)", "total/worst negative slack, ns (0 = clean)"),
    (r"^timing__(setup|hold)_vio__count", "timing violation count"),
    (r"^magic__drc_error__count", "Magic DRC errors (0 = clean)"),
    (r"^klayout__drc_error__count", "KLayout DRC errors (0 = clean)"),
    (r"^route__drc_errors", "router DRC errors"),
    (r"^design__lvs_.*__count", "LVS mismatch count (0 = clean)"),
    (r"^design__xor_difference__count", "layout XOR difference count (0 = clean)"),
    (r"^route__antenna_violation__count", "antenna violations"),
    (r"wirelength", "wirelength, um"),
    (r"^power__", "power, W"),
    (r"^design__max_(slew|cap|fanout)_violation", "max slew/cap/fanout violation count"),
    (r"^clock__skew", "clock skew, ns"),
]


class ToolError(Exception):
    pass


# ----------------------------------------------------------------- helpers
def _designs():
    return repo.design_dirs()


def _check_design(design):
    if not isinstance(design, str) or not re.fullmatch(r"[A-Za-z0-9_]+", design) or design not in _designs():
        raise ToolError("unknown design %r; valid: %s" % (design, ", ".join(_designs())))
    return design


def _config(design):
    return repo.config(design)


def _top(design):
    return _config(design).get("DESIGN_NAME", design)


def _metrics(design):
    p = os.path.join(REPO, "designs", design, "output", "metrics.json")
    if not os.path.isfile(p):
        raise ToolError("design %s is not hardened (no output/metrics.json)" % design)
    with open(p) as f:
        return json.load(f)


def _hardened():
    return [d for d in _designs() if os.path.isfile(os.path.join(REPO, "designs", d, "output", "metrics.json"))]


# json.dumps would emit bare Infinity/NaN (invalid JSON for the model client); turn them into strings.
def _jsonable(v):
    if isinstance(v, float) and (math.isinf(v) or math.isnan(v)):
        return str(v)
    return v


def _gds(design):
    top = _top(design)
    p = os.path.join(REPO, "build", "results", design, top + ".gds")
    if not os.path.isfile(p):
        alt = glob.glob(os.path.join(REPO, "build", "results", design, "*.gds"))
        if len(alt) == 1:
            return alt[0]
        raise ToolError("GDS not found: build/results/%s/%s.gds (local, git-ignored; run the flow to create it)" % (design, top))
    return p


# One parsed GDS kept in memory, keyed by (path, mtime): a re-run flow invalidates it, and the clear() keeps
# at most one big layout alive (the agent asks many questions about the same design in a row).
_LAYOUT_CACHE = {}


def _layout(design):
    import klayout.db as kdb
    path = _gds(design)
    key = (path, os.path.getmtime(path))
    if key not in _LAYOUT_CACHE:
        _LAYOUT_CACHE.clear()
        ly = kdb.Layout()
        ly.read(path)
        _LAYOUT_CACHE[key] = ly
    ly = _LAYOUT_CACHE[key]
    top = ly.top_cell()
    return ly, top


def _lname(l, d):
    return LAYER_NAMES.get((l, d), "")


def _parse_layer(layer):
    if isinstance(layer, str):
        s = layer.strip().lower()
        if s in LAYER_ALIASES:
            return LAYER_ALIASES[s]
        m = re.fullmatch(r"(\d+)\s*/\s*(\d+)", s)
        if m:
            return int(m.group(1)), int(m.group(2))
    raise ToolError("bad layer %r; use met1..met5, li1, poly, diff, mcon, via..via4 or 'L/D'" % (layer,))


def _truncate(items, n=MAX_ITEMS):
    return items[:n], (len(items) > n)


# ----------------------------------------------------------------- tools
def list_designs():
    out = []
    for d in _designs():
        desc = ""
        rp = os.path.join(REPO, "designs", d, "README.md")
        if os.path.isfile(rp):
            with open(rp) as f:
                lines = [l.strip() for l in f if l.strip()]
            desc = lines[0].lstrip("# ").strip() if lines else ""
            for l in lines[1:3]:  # prefer the "Property" line when present
                if l.startswith("**Property"):
                    desc += " - " + l.replace("**Property:**", "").strip()
                    break
        out.append({"design": d, "description": desc[:200],
                    "hardened": os.path.isfile(os.path.join(REPO, "designs", d, "output", "metrics.json")),
                    "design_name": _top(d)})
    return {"designs": out, "count": len(out)}


def _explain(key):
    for pat, txt in METRIC_HELP:
        if re.search(pat, key):
            return txt
    return None


def read_metrics(design, keys=None, pattern=None):
    _check_design(design)
    m = _metrics(design)
    sel = {}
    if keys:
        if isinstance(keys, str):
            keys = [keys]
        for k in keys:
            if k in m:
                sel[k] = m[k]
    if pattern:
        for k, v in m.items():
            if pattern.lower() in k.lower():
                sel[k] = v
    if not keys and not pattern:
        return {"design": design, "total_keys": len(m),
                "hint": "pass keys or a substring pattern (e.g. 'setup__ws', 'drc', 'power__total', 'stdcell')",
                "sample_keys": sorted(m)[:MAX_ITEMS]}
    items = sorted(sel.items())
    shown, trunc = _truncate(items, 60)
    res = {"design": design, "metrics": {k: {"value": _jsonable(v), **({"meaning": _explain(k)} if _explain(k) else {})}
                                         for k, v in shown}, "matched": len(items)}
    if trunc:
        res["truncated"] = "showing 60 of %d; narrow the pattern" % len(items)
    if keys:
        missing = [k for k in keys if k not in m]
        if missing:
            res["missing_keys"] = missing
    return res


def compare_designs(metric, designs=None):
    if not isinstance(metric, str) or not metric:
        raise ToolError("metric required")
    names = [_check_design(d) for d in designs] if designs else _hardened()
    rows, missing = [], []
    for d in names:
        try:
            m = _metrics(d)
        except ToolError:
            missing.append(d)
            continue
        v = m.get(metric)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            rows.append({"design": d, "value": _jsonable(v), "_v": v})
        else:
            missing.append(d)
    if not rows:
        raise ToolError("metric %r not found numerically in any selected design (use read_metrics to find the exact key)" % metric)
    rows.sort(key=lambda r: r["_v"])
    res = {"metric": metric, "meaning": _explain(metric), "sorted_ascending": [{"design": r["design"], "value": r["value"]} for r in rows],
           "min": {"design": rows[0]["design"], "value": rows[0]["value"]},
           "max": {"design": rows[-1]["design"], "value": rows[-1]["value"]}}
    if re.search(r"__ws|slack", metric):
        res["note"] = "for slack metrics the worst design is the min, the best is the max"
    if missing:
        res["missing_in"] = missing
    return res


def layout_summary(design):
    _check_design(design)
    ly, top = _layout(design)
    dbu = ly.dbu
    bb = top.dbbox()
    per = []
    for li in ly.layer_indexes():
        info = ly.get_info(li)
        import klayout.db as kdb
        n = kdb.Region(top.begin_shapes_rec(li)).count()
        per.append((n, info.layer, info.datatype))
    per.sort(reverse=True)
    total = sum(p[0] for p in per)
    macros = {}
    for inst in top.each_inst():
        c = ly.cell(inst.cell_index)
        if c.name.startswith("sky130_fd_sc_") or c.name.startswith("sky130_ef_sc_"):
            continue
        e = macros.setdefault(c.name, {"cell": c.name, "count": 0, "instances": []})
        e["count"] += 1
        if len(e["instances"]) < 5:
            b = inst.dbbox()
            e["instances"].append({
                                   "bbox_um": [round(b.left, 3), round(b.bottom, 3), round(b.right, 3), round(b.top, 3)]})
    return {"design": design, "gds": os.path.relpath(_gds(design), REPO), "top_cell": top.name,
            "die_bbox_um": [round(bb.left, 3), round(bb.bottom, 3), round(bb.right, 3), round(bb.top, 3)],
            "die_size_um": [round(bb.width(), 3), round(bb.height(), 3)],
            "cells": ly.cells(), "total_shapes": total,
            "top_layers_by_shapes": [{"layer": "%d/%d" % (l, d), "name": _lname(l, d), "shapes": n} for n, l, d in per[:10]],
            "layer_count": len(per),
            "top_level_macro_instances": list(macros.values())[:MAX_ITEMS]}


def layer_stats(design, layer):
    import klayout.db as kdb
    _check_design(design)
    l, d = _parse_layer(layer)
    ly, top = _layout(design)
    li = ly.find_layer(l, d)
    if li is None:
        return {"design": design, "layer": "%d/%d" % (l, d), "shapes": 0, "area_um2": 0.0, "bbox_um": None,
                "note": "layer not present in the GDS"}
    r = kdb.Region(top.begin_shapes_rec(li))
    n = r.count()
    r.merge()
    bb = r.bbox()
    dbu = ly.dbu
    return {"design": design, "layer": "%d/%d" % (l, d), "name": _lname(l, d), "shapes": n,
            "merged_polygons": r.count(), "area_um2": round(r.area() * dbu * dbu, 3),
            "bbox_um": None if bb.empty() else [round(bb.left * dbu, 3), round(bb.bottom * dbu, 3),
                                                 round(bb.right * dbu, 3), round(bb.top * dbu, 3)]}


def _lef_pins(design, rx):
    p = os.path.join(REPO, "designs", design, "output", _top(design) + ".lef")
    if not os.path.isfile(p):
        return []
    pins, cur, layer = [], None, None
    with open(p) as f:
        for line in f:
            t = line.split()
            if not t:
                continue
            if t[0] == "PIN":
                cur, layer = t[1], None
            elif t[0] == "LAYER" and cur:
                layer = t[1].rstrip(";")
            elif t[0] == "RECT" and cur and layer and rx.search(cur):
                x0, y0, x1, y1 = map(float, t[1:5])
                pins.append({"name": cur, "layer": layer, "pos_um": [round((x0 + x1) / 2, 3), round((y0 + y1) / 2, 3)]})
                cur = cur  # keep first rect per layer only below
            elif t[0] == "END" and len(t) > 1 and t[1] == cur:
                cur = None
    seen, out = set(), []
    for p_ in pins:
        if p_["name"] not in seen:
            seen.add(p_["name"])
            out.append(p_)
    return out


def find_pins(design, pattern):
    import klayout.db as kdb
    _check_design(design)
    try:
        rx = re.compile(pattern)
    except re.error as e:
        raise ToolError("bad regex: %s" % e)
    source, pins = "gds", {}
    try:
        ly, top = _layout(design)
        for li in ly.layer_indexes():
            info = ly.get_info(li)
            for sh in top.shapes(li).each(kdb.Shapes.STexts):
                s = sh.text_string
                if rx.search(s) and s not in pins:
                    pins[s] = {"name": s, "layer": "%d/%d" % (info.layer, info.datatype),
                               "layer_name": _lname(info.layer, info.datatype),
                               "pos_um": [round(sh.text.x * ly.dbu, 3), round(sh.text.y * ly.dbu, 3)]}
    except ToolError:
        pass
    found = list(pins.values())
    if not found:
        found, source = _lef_pins(design, rx), "lef"
        for f in found:
            f["layer_name"] = f["layer"]
    found.sort(key=lambda p: [int(x) if x.isdigit() else x for x in re.split(r"(\d+)", p["name"])])
    shown, trunc = _truncate(found)
    res = {"design": design, "pattern": pattern, "source": source, "total_matches": len(found), "pins": shown}
    if trunc:
        res["truncated"] = "showing %d of %d" % (len(shown), len(found))
    return res


def render_png(design, out=None, width_px=1200):
    _check_design(design)
    try:
        width_px = int(width_px)
    except (TypeError, ValueError):
        raise ToolError("width_px must be an integer")
    width_px = max(200, min(width_px, 4000))
    if out:
        out_abs = os.path.abspath(out if os.path.isabs(out) else os.path.join(REPO, out))
        if not out_abs.startswith(AGENT_DIR + os.sep) or not out_abs.endswith(".png"):
            raise ToolError("out must be a .png path under build/agent/")
    else:
        out_abs = os.path.join(RENDER_DIR, design + ".png")
    fallback = os.path.join(REPO, "designs", design, "output", "layout.png")
    try:
        import klayout.lay as lay
        gds = _gds(design)
        if not os.path.isfile(LYP):
            raise ToolError("layer properties not found: %s" % LYP)
        os.makedirs(os.path.dirname(out_abs), exist_ok=True)
        lv = lay.LayoutView()
        lv.load_layout(gds, True)
        lv.load_layer_props(LYP)
        lv.max_hier()
        lv.zoom_fit()
        h = width_px
        lv.save_image(out_abs, width_px, h)
        if not os.path.isfile(out_abs) or os.path.getsize(out_abs) == 0:
            raise ToolError("render produced an empty file")
        return {"design": design, "path": os.path.relpath(out_abs, REPO), "bytes": os.path.getsize(out_abs),
                "size_px": [width_px, h], "fallback": False}
    except Exception as e:  # noqa: BLE001
        if os.path.isfile(fallback):
            return {"design": design, "path": os.path.relpath(fallback, REPO), "fallback": True,
                    "note": "KLayout render failed (%s); returning the committed designs/%s/output/layout.png" % (e, design)}
        raise ToolError("render failed: %s" % e)


def _corner_vals(m, prefix):
    out = {}
    for k, v in m.items():
        if k.startswith(prefix + "__corner:") and isinstance(v, (int, float)):
            out[k.split("corner:")[1]] = v
    return out


def _worst(m, prefix):
    vals = _corner_vals(m, prefix)
    if not vals:
        return None
    c = min(vals, key=lambda k: vals[k])
    return {"slack_ns": round(vals[c], 4), "corner": c}


# Used only for read-only helper scripts (e.g. scripts/flow/check_signoff.py); cwd is pinned to the repo root.
def _run(cmd, timeout, env=None):
    e = dict(os.environ)
    if env:
        e.update(env)
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, cwd=REPO, env=e)


def signoff_summary(design):
    _check_design(design)
    m = _metrics(design)
    g = m.get
    res = {"design": design,
           "drc": {"magic": g("magic__drc_error__count"), "klayout": g("klayout__drc_error__count"),
                   "route": g("route__drc_errors")},
           "lvs": {k.replace("design__lvs_", "").replace("__count", ""): v for k, v in m.items() if k.startswith("design__lvs_")},
           "xor_difference": g("design__xor_difference__count"),
           "antenna_violations": g("route__antenna_violation__count"),
           "setup_worst": _worst(m, "timing__setup__ws"), "hold_worst": _worst(m, "timing__hold__ws"),
           "max_slew_violations": g("design__max_slew_violation__count"),
           "max_cap_violations": g("design__max_cap_violation__count"),
           "max_fanout_violations": g("design__max_fanout_violation__count")}
    env = repo.docker_env()     # Colima osl socket if present, else /var/run/docker.sock on Linux (scripts/lib/repo.py)
    try:
        r = _run([sys.executable, os.path.join(REPO, "scripts", "flow", "check_signoff.py"), design], 120, env)
        lines = [l for l in (r.stdout + r.stderr).splitlines() if l.strip()]
        res["check_signoff"] = {"exit_code": r.returncode, "verdict": lines[-1] if lines else "",
                                "output_tail": lines[-8:]}
        if r.returncode != 0 and re.search(r"docker|yosys", r.stdout + r.stderr, re.I):
            res["check_signoff"]["note"] = "RTL register check may have been skipped/failed (Docker/Yosys unavailable); metrics above are still valid"
    except subprocess.TimeoutExpired:
        res["check_signoff"] = {"note": "timed out after 120 s; RTL register check skipped, metrics only"}
    except Exception as e:  # noqa: BLE001
        res["check_signoff"] = {"note": "could not run check_signoff.py (%s); RTL register check skipped" % e}
    return res


def classify_slew(design, corner="max_ss_100C_1v60"):
    _check_design(design)
    if not isinstance(corner, str) or not re.fullmatch(r"[A-Za-z0-9_]+", corner):
        raise ToolError("bad corner")
    if not glob.glob(os.path.join(REPO, "designs", design, "runs", "RUN_*")):
        raise ToolError("no designs/%s/runs/RUN_* (runs are local and git-ignored); classify_slew needs a flow run" % design)
    try:
        r = _run([sys.executable, os.path.join(REPO, ".claude", "skills", "harden-design", "classify_slew.py"), design, corner], 90)
    except subprocess.TimeoutExpired:
        raise ToolError("classify_slew timed out")
    out = (r.stdout + r.stderr).strip()
    if r.returncode != 0:
        raise ToolError("classify_slew failed: %s" % out[-400:])
    return {"design": design, "corner": corner, "output": out.splitlines()[:20]}


def precheck_summary():
    cands = sorted(glob.glob(os.path.join(REPO, "build", "precheck", "results_[0-9]*_[0-9]*", "summary.tsv")))
    cands = [c for c in cands if re.search(r"results_\d{8}_\d{6}", c)]
    if not cands:
        cands = [os.path.join(REPO, "precheck", "results", "summary.tsv")]
    p = cands[-1]
    if not os.path.isfile(p):
        raise ToolError("no precheck summary.tsv found under build/precheck/results_*/ or precheck/results/")
    checks = {}
    with open(p) as f:
        for line in f:
            t = line.rstrip("\n").split("\t")
            if len(t) >= 2:
                checks[t[0]] = t[1]
    npass = sum(1 for v in checks.values() if v == "PASS")
    return {"source": os.path.relpath(p, REPO), "checks": checks, "pass": npass, "fail": len(checks) - npass}


# ----------------------------------------------------------------- schemas / dispatch
def _fn(name, desc, props, required=()):
    return {"type": "function", "function": {"name": name, "description": desc,
            "parameters": {"type": "object", "properties": props, "required": list(required)}}}


_D = {"type": "string", "description": "design directory name under designs/ (see list_designs)"}
TOOLS = [
    _fn("list_designs", "List all designs with a short description, whether hardened (has metrics.json) and DESIGN_NAME.", {}),
    _fn("read_metrics", "Read values from a design's metrics.json by exact keys and/or a substring pattern (e.g. 'setup__ws', 'drc', 'stdcell'). Adds units/meaning for common keys.",
        {"design": _D, "keys": {"type": ["array", "null"], "items": {"type": "string"}, "description": "exact metric keys"},
         "pattern": {"type": ["string", "null"], "description": "case-insensitive substring of metric keys"}}, ["design"]),
    _fn("compare_designs", "Compare one exact metrics.json key across designs; returns a sorted table with min/max.",
        {"metric": {"type": "string", "description": "exact metric key, e.g. timing__setup__ws__corner:nom_tt_025C_1v80 or design__instance__count__stdcell"},
         "designs": {"type": ["array", "null"], "items": {"type": "string"}, "description": "designs to compare; null = all hardened"}}, ["metric"]),
    _fn("layout_summary", "From the GDS (KLayout): top cell, die bbox (um), cell count, shape count, 10 busiest layers, macro instances.", {"design": _D}, ["design"]),
    _fn("layer_stats", "Shape count, merged area (um^2) and bbox of one GDS layer.",
        {"design": _D, "layer": {"type": "string", "description": "met1..met5, li1, poly, diff, mcon, via..via4, or 'L/D' like 68/20"}}, ["design", "layer"]),
    _fn("find_pins", "Find pins/labels by regex in the GDS text labels (or the LEF if none); max 50.",
        {"design": _D, "pattern": {"type": "string", "description": "regular expression, e.g. wbs_dat_i"}}, ["design", "pattern"]),
    _fn("render_png", "Render the GDS to a PNG with KLayout (sky130 layer colors) into build/agent/renders/; returns the path.",
        {"design": _D, "out": {"type": ["string", "null"], "description": "optional .png path under build/agent/"},
         "width_px": {"type": "integer", "description": "image width/height in pixels (default 1200)"}}, ["design"]),
    _fn("signoff_summary", "DRC (Magic/KLayout), LVS, XOR, antenna, worst setup/hold slack with corner, max slew/cap/fanout, plus the scripts/flow/check_signoff.py verdict.", {"design": _D}, ["design"]),
    _fn("classify_slew", "Split a run's max-slew violations into port-driven vs internal (needs a local flow run).",
        {"design": _D, "corner": {"type": "string", "description": "default max_ss_100C_1v60"}}, ["design"]),
    _fn("precheck_summary", "Newest ChipFoundry precheck result: check name -> PASS/FAIL.", {}),
]

_IMPL = {"list_designs": list_designs, "read_metrics": read_metrics, "compare_designs": compare_designs,
         "layout_summary": layout_summary, "layer_stats": layer_stats, "find_pins": find_pins,
         "render_png": render_png, "signoff_summary": signoff_summary, "classify_slew": classify_slew,
         "precheck_summary": precheck_summary}


# Single entry point for every client (Ollama loop, MCP server, tool_server). The error contract is the point:
# a small model must always get a readable {"error": ...} back to self-correct, never a traceback or an exception.
def call(name, args=None):
    """Dispatch a tool call. Never raises."""
    try:
        if name not in _IMPL:
            return {"error": "unknown tool %r; available: %s" % (name, ", ".join(_IMPL))}
        if args is None:
            args = {}
        if not isinstance(args, dict):
            return {"error": "args must be an object"}
        args = {k: v for k, v in args.items()}
        return _IMPL[name](**args)
    except ToolError as e:
        return {"error": str(e)}
    except TypeError as e:
        return {"error": "bad arguments for %s: %s" % (name, e)}
    except Exception as e:  # noqa: BLE001
        return {"error": "%s: %s" % (type(e).__name__, e)}


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(json.dumps([t["function"]["name"] for t in TOOLS]))
    else:
        print(json.dumps(call(sys.argv[1], json.loads(sys.argv[2]) if len(sys.argv) > 2 else {}), indent=1))
