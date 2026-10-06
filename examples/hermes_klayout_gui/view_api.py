#!/usr/bin/env python3
"""THE shared interface between the Hermes agent and a KLayout VIEW (read-only).

Two backends implement ViewBackend:
  OffscreenBackend (offscreen_backend.py)  klayout.lay.LayoutView, no window, renders PNGs
  LiveBackend      (live_backend.py)       drives a real KLayout desktop window

Contract (every method): returns a JSON-serialisable dict {"ok": bool, ...}; on failure
{"ok": False, "error": "..."}; never raises for bad input; never modifies a layout file
(the only files written are PNGs under build/agent/klayout_gui/).

Units: micrometres. bbox = [x1, y1, x2, y2] (x1 < x2, y1 < y2).
Layers: "met1".."met5", "li1", "poly", "diff", "mcon", "via".."via4", "met4/drawing",
"met2 pin", or "68/20" (GDS layer/datatype). Purposes: drawing=20, pin=16, label=5.

  open_design(design)        -> {ok, design, top_cell, bbox_um, n_layers}
  zoom_to(target)            -> {ok, view_bbox_um}      target: {"cell": n} | {"bbox": [..]} | {"full": true}
  show_layers(layers, only)  -> {ok, visible}           visible = ["68/20 met1 drawing", ...]
  highlight_drc(design, max_items=200, demo_markers=None)
                             -> {ok, n_markers, source|note, ...}
  measure(a, b)              -> {ok, dx_um, dy_um, distance_um, manhattan_um}
  snapshot(path=None, width=1200, height=900)
                             -> {ok, png, view_bbox_um, visible_layers}
  state()                    -> {ok, design, view_bbox_um, visible_layers}
"""
import glob
import json
import math
import os
import re
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "tools"))
import eda_tools  # noqa: E402  (layer parsing, design names, GDS lookup are reused, not copied)

OUT_DIR = os.path.join(REPO, "build", "agent", "klayout_gui")
PURPOSES = {"drawing": 20, "pin": 16, "label": 5}
MIN_PX, MAX_PX = 200, 2400


class ViewError(Exception):
    pass


# ------------------------------------------------------------------ shared helpers
def find_gds(design):
    """designs/<d>/output/*.gds if committed, else the collected build/results/<d>/<top>.gds, else newest run final gds."""
    eda_tools._check_design(design)
    c = sorted(glob.glob(os.path.join(REPO, "designs", design, "output", "*.gds")))
    if c:
        return c[0]
    try:
        return eda_tools._gds(design)
    except eda_tools.ToolError:
        pass
    runs = sorted(glob.glob(os.path.join(REPO, "designs", design, "runs", "RUN_*", "final", "gds", "*.gds")))
    if runs:
        return runs[-1]
    raise ViewError("no GDS for %s (local and git-ignored; run: make collect DESIGN=%s)" % (design, design))


def top_cell_name(design):
    return eda_tools._top(design)


def parse_layer_spec(spec):
    """One layer spec -> list of (layer, datatype). Reuses eda_tools aliases; adds 'name/purpose' and 'name purpose'."""
    if not isinstance(spec, str) or not spec.strip():
        raise ViewError("bad layer %r" % (spec,))
    s = spec.strip().lower().replace("_", " ")
    m = re.fullmatch(r"(\d+)\s*/\s*(\d+)", s)
    if m:
        return [(int(m.group(1)), int(m.group(2)))]
    m = re.fullmatch(r"([a-z0-9]+)\s*[/ ]\s*(drawing|pin|label)", s)
    if m:
        l, _ = _alias(m.group(1))
        return [(l, PURPOSES[m.group(2)])]
    l, d = _alias(s)
    if d == 20:        # a bare metal/li/poly name means drawing + pin shapes
        return [(l, 20), (l, 16)]
    return [(l, d)]


def _alias(name):
    try:
        return eda_tools._parse_layer(name)
    except eda_tools.ToolError as e:
        raise ViewError(str(e))


def parse_layers(layers):
    if isinstance(layers, str):
        layers = [x for x in re.split(r"[,+;]|\band\b", layers) if x.strip()]
    if not isinstance(layers, (list, tuple)) or not layers:
        raise ViewError("layers must be a non-empty list such as [\"met1\", \"met2\"]")
    out = []
    for x in layers:
        for ld in parse_layer_spec(x):
            if ld not in out:
                out.append(ld)
    return out


def layer_label(l, d):
    return ("%d/%d %s" % (l, d, eda_tools.LAYER_NAMES.get((l, d), ""))).strip()


def check_bbox(b):
    if (not isinstance(b, (list, tuple)) or len(b) != 4
            or not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in b)):
        raise ViewError("bbox must be [x1, y1, x2, y2] in um (4 finite numbers)")
    x1, y1, x2, y2 = [float(v) for v in b]
    if x2 <= x1 or y2 <= y1:
        raise ViewError("bbox needs x1 < x2 and y1 < y2")
    return [x1, y1, x2, y2]


def check_point(p, what="point"):
    if (not isinstance(p, (list, tuple)) or len(p) != 2
            or not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in p)):
        raise ViewError("%s must be [x, y] in um" % what)
    return [float(p[0]), float(p[1])]


def check_png_path(path, design="view", seq=0):
    """PNG must live under build/agent/klayout_gui/ (the only place this tool writes)."""
    os.makedirs(OUT_DIR, exist_ok=True)
    if path is None:
        return os.path.join(OUT_DIR, "%s_%02d.png" % (design or "view", seq))
    if not isinstance(path, str):
        raise ViewError("path must be a string")
    p = os.path.abspath(path if os.path.isabs(path) else os.path.join(REPO, path))
    if not p.startswith(OUT_DIR + os.sep) or not p.endswith(".png"):
        raise ViewError("path must be a .png under build/agent/klayout_gui/")
    os.makedirs(os.path.dirname(p), exist_ok=True)
    return p


def clamp_px(v, default):
    try:
        v = int(v)
    except (TypeError, ValueError):
        v = default
    return max(MIN_PX, min(MAX_PX, v))


def find_drc_report(design):
    """Newest KLayout DRC report database (*.lyrdb) of the design's runs, or None."""
    eda_tools._check_design(design)
    c = sorted(glob.glob(os.path.join(REPO, "designs", design, "runs", "RUN_*", "*klayout-drc", "reports", "*.lyrdb")))
    c += sorted(glob.glob(os.path.join(REPO, "designs", design, "output", "reports", "*.lyrdb")))
    return c[-1] if c else None


def drc_summary_json(design):
    """Committed per-rule counts (output/reports/drc_klayout.json) -> total error count, or None."""
    p = os.path.join(REPO, "designs", design, "output", "reports", "drc_klayout.json")
    if not os.path.isfile(p):
        return None
    with open(p) as f:
        d = json.load(f)
    return sum(v for v in d.values() if isinstance(v, (int, float)))


def rel(p):
    return os.path.relpath(p, REPO)


def demo_boxes(view_bbox, n=5):
    """Deterministic demo marker set: n small boxes on a diagonal inside view_bbox (NOT real DRC errors)."""
    x1, y1, x2, y2 = view_bbox
    w, h = x2 - x1, y2 - y1
    s = max(min(w, h) * 0.04, 0.5)
    return [[round(x1 + w * f, 3), round(y1 + h * f, 3), round(x1 + w * f + s, 3), round(y1 + h * f + s, 3)]
            for f in [(i + 1) / (n + 1) for i in range(n)]]


def check_markers(m):
    if not isinstance(m, (list, tuple)) or not m:
        raise ViewError("demo_markers must be true or a non-empty list of [x1,y1,x2,y2] boxes (um)")
    return [check_bbox(b) for b in m[:500]]


# ------------------------------------------------------------------ the interface
class ViewBackend:
    """Abstract backend. Subclasses implement the _impl methods; the public methods wrap them so that
    every call returns {"ok": ...} and never raises."""

    name = "abstract"

    def _wrap(self, fn, *a, **kw):
        try:
            r = fn(*a, **kw)
            r.setdefault("ok", True)
            return r
        except ViewError as e:
            return {"ok": False, "error": str(e)}
        except Exception as e:  # noqa: BLE001  (tool errors go back to the model as data)
            return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}

    # public API (what the agent calls)
    def open_design(self, design):
        return self._wrap(self._open_design, design)

    def zoom_to(self, target):
        return self._wrap(self._zoom_to, target)

    def show_layers(self, layers, only=True):
        return self._wrap(self._show_layers, layers, only)

    def highlight_drc(self, design, max_items=200, demo_markers=None):
        return self._wrap(self._highlight_drc, design, max_items, demo_markers)

    def measure(self, a, b):
        return self._wrap(self._measure, a, b)

    def snapshot(self, path=None, width=1200, height=900):
        return self._wrap(self._snapshot, path, width, height)

    def state(self):
        return self._wrap(self._state)

    def close(self):
        pass

    # default measure is pure geometry; backends may keep it
    def _measure(self, a, b):
        a, b = check_point(a, "a"), check_point(b, "b")
        dx, dy = b[0] - a[0], b[1] - a[1]
        return {"a": a, "b": b, "dx_um": round(dx, 4), "dy_um": round(dy, 4),
                "distance_um": round(math.hypot(dx, dy), 4), "manhattan_um": round(abs(dx) + abs(dy), 4)}

    # to implement
    def _open_design(self, design):
        raise NotImplementedError

    def _zoom_to(self, target):
        raise NotImplementedError

    def _show_layers(self, layers, only):
        raise NotImplementedError

    def _highlight_drc(self, design, max_items, demo_markers):
        raise NotImplementedError

    def _snapshot(self, path, width, height):
        raise NotImplementedError

    def _state(self):
        raise NotImplementedError


# ------------------------------------------------------------------ tool schemas + dispatch
def _fn(name, desc, props, required=()):
    return {"type": "function", "function": {"name": name, "description": desc,
            "parameters": {"type": "object", "properties": props, "required": list(required)}}}


_BBOX = {"type": "array", "items": {"type": "number"}, "minItems": 4, "maxItems": 4}
_PT = {"type": "array", "items": {"type": "number"}, "minItems": 2, "maxItems": 2}

TOOLS = [
    _fn("open_design", "Open the GDS layout of one design in the viewer (replaces the current one). Returns top cell, bounding box in um and layer count.",
        {"design": {"type": "string", "description": "design directory name, e.g. kv_attn_n8"}}, ["design"]),
    _fn("zoom_to", "Zoom the view. Give exactly one of: cell (a cell name or an instance name such as mprj), bbox ([x1,y1,x2,y2] in um), or full=true for the whole design.",
        {"target": {"type": "object", "properties": {"cell": {"type": "string"}, "bbox": _BBOX, "full": {"type": "boolean"}}}}, ["target"]),
    _fn("show_layers", "Choose which layers are visible. layers like [\"met4\",\"met5\"], \"met4/drawing\" or \"68/20\". only=true hides all others, false adds them.",
        {"layers": {"type": ["array", "string"], "items": {"type": "string"}}, "only": {"type": "boolean"}}, ["layers"]),
    _fn("highlight_drc", "Mark DRC violations from the design's KLayout DRC report on the view. These designs are DRC-clean, so 0 markers is the honest answer. demo_markers=true draws 5 labelled demo boxes (not real errors); a list of [x1,y1,x2,y2] boxes draws those.",
        {"design": {"type": "string"}, "max_items": {"type": "integer", "minimum": 1, "maximum": 1000},
         "demo_markers": {"type": ["boolean", "array"], "items": _BBOX}}, ["design"]),
    _fn("measure", "Distance between two points a and b, each [x, y] in um.", {"a": _PT, "b": _PT}, ["a", "b"]),
    _fn("snapshot", "Render the current view to a PNG file (under build/agent/klayout_gui/) and return its path.",
        {"path": {"type": ["string", "null"]}, "width": {"type": "integer", "minimum": MIN_PX, "maximum": MAX_PX},
         "height": {"type": "integer", "minimum": MIN_PX, "maximum": MAX_PX}}),
    _fn("state", "Current design, view bounding box (um) and visible layers.", {}),
]
TOOL_NAMES = [t["function"]["name"] for t in TOOLS]
_SCHEMAS = {t["function"]["name"]: t["function"]["parameters"] for t in TOOLS}

_TYPES = {"string": str, "integer": int, "number": (int, float), "boolean": bool, "array": (list, tuple),
          "object": dict, "null": type(None)}


def _type_ok(v, t):
    ts = t if isinstance(t, list) else [t]
    for x in ts:
        if x == "integer" and isinstance(v, bool):
            continue
        if x == "number" and isinstance(v, bool):
            continue
        if isinstance(v, _TYPES[x]):
            return True
    return False


def validate_args(name, args):
    """Return an error string or None. Allow-list of tool names, required/unknown keys, types, ranges."""
    if name not in _SCHEMAS:
        return "unknown tool %r; allowed: %s" % (name, ", ".join(TOOL_NAMES))
    if not isinstance(args, dict):
        return "arguments must be a JSON object"
    sch = _SCHEMAS[name]
    props = sch["properties"]
    for k in sch["required"]:
        if k not in args:
            return "%s: missing required argument %r" % (name, k)
    for k, v in args.items():
        if k not in props:
            return "%s: unexpected argument %r (allowed: %s)" % (name, k, ", ".join(props) or "none")
        p = props[k]
        if "type" in p and not _type_ok(v, p["type"]):
            return "%s: argument %r has the wrong type" % (name, k)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            if "minimum" in p and v < p["minimum"] or "maximum" in p and v > p["maximum"]:
                return "%s: argument %r out of range [%s, %s]" % (name, k, p.get("minimum"), p.get("maximum"))
        if p.get("type") == "array" and isinstance(v, (list, tuple)):
            if "minItems" in p and len(v) < p["minItems"] or "maxItems" in p and len(v) > p["maxItems"]:
                return "%s: argument %r needs %s numbers" % (name, k, p.get("minItems"))
    return None


def dispatch(backend, name, args=None):
    """Validate, then call exactly one of the 7 backend methods. Never raises."""
    args = {} if args is None else args
    err = validate_args(name, args)
    if err:
        return {"ok": False, "error": err}
    if name == "zoom_to":
        t = args["target"]
        keys = [k for k in ("cell", "bbox", "full") if k in t and t[k] not in (None, False)]
        if len(keys) != 1 or set(t) - {"cell", "bbox", "full"}:
            return {"ok": False, "error": "zoom_to target needs exactly one of cell, bbox, full=true"}
    if name == "show_layers":
        args = {"layers": args["layers"], "only": args.get("only", True)}
    return getattr(backend, name)(**args)
