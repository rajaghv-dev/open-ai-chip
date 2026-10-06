# Educational, read-only bridge: lets a local agent drive THIS KLayout window.
# Run it inside the KLayout GUI (start_live.sh does that). Plain ASCII, no network beyond 127.0.0.1.
#
# Threads: socket threads only parse JSON and put requests in a queue. A pya.QTimer (GUI thread)
# drains the queue every 50 ms and does all pya / window work. Never touch pya from a socket thread.
#
# Protocol: one JSON object per line.  request  {"id": 1, "method": "zoom_to", "params": {...}, "token": "..."}
#                                       response {"id": 1, "result": {"ok": true, ...}}  or {"id": 1, "error": "..."}
# Docs: examples/hermes_klayout_gui/README_live.md, docs/HERMES_AGENT.md
import hmac
import json
import os
import queue
import socket
import sys
import threading

try:
    import pya
except Exception:  # imported by the unit tests outside KLayout
    pya = None

HOST = "127.0.0.1"          # localhost only, never 0.0.0.0
MAX_LINE = 1 << 20
REQUEST_TIMEOUT_S = 60.0
VIEW_METHODS = ("open_design", "zoom_to", "show_layers", "highlight_drc", "measure", "snapshot", "state")
ADMIN_METHODS = ("ping", "quit")                # quit only if KLAYOUT_AGENT_ALLOW_QUIT=1
ALLOWED = VIEW_METHODS + ADMIN_METHODS


def log(msg):
    line = "[agent_bridge] " + str(msg)
    try:
        if pya is not None:
            pya.Logger.info(line)
    except Exception:
        pass
    print(line, flush=True)


# ---------------------------------------------------------------- protocol (no pya needed)
def encode(obj):
    return (json.dumps(obj, separators=(",", ":")) + "\n").encode("ascii")


def process_line(line, execute, token=None, allow_quit=False):
    """One request line -> one response dict. execute(method, params) runs on the right thread."""
    rid = None
    try:
        if len(line) > MAX_LINE:
            return {"id": None, "error": "request too long"}
        req = json.loads(line)
        if not isinstance(req, dict):
            return {"id": None, "error": "request must be a JSON object"}
        rid = req.get("id")
        if token:
            # constant-time compare, so the token cannot be guessed byte by byte from response timing
            if not hmac.compare_digest(str(req.get("token", "")).encode(), token.encode()):
                return {"id": rid, "error": "bad or missing token"}
        method = req.get("method")
        if method not in ALLOWED:
            return {"id": rid, "error": "method %r not allowed; allowed: %s" % (method, ", ".join(VIEW_METHODS))}
        if method == "quit" and not allow_quit:
            return {"id": rid, "error": "quit is disabled"}
        params = req.get("params", {})
        if not isinstance(params, dict):
            return {"id": rid, "error": "params must be an object"}
        return {"id": rid, "result": execute(method, params)}
    except ValueError as e:
        return {"id": rid, "error": "bad JSON: %s" % e}
    except Exception as e:  # noqa: BLE001
        return {"id": rid, "error": "%s: %s" % (type(e).__name__, e)}


# ---------------------------------------------------------------- GUI-thread work
class Bridge:
    def __init__(self):
        here = os.path.dirname(os.path.abspath(globals().get("__file__", ".")))
        repo = os.environ.get("KLAYOUT_AGENT_REPO") or os.path.abspath(os.path.join(here, "..", "..", ".."))
        gui_dir = os.path.join(repo, "examples", "hermes_klayout_gui")
        sys.path.insert(0, gui_dir)
        import view_api            # shared helpers: gds lookup, layer parsing, validators (stdlib only)
        self.va = view_api
        self.design = None
        self.markers = []
        self.seq = 0
        self.q = queue.Queue()
        self.token = os.environ.get("KLAYOUT_AGENT_TOKEN") or None
        self.allow_quit = os.environ.get("KLAYOUT_AGENT_ALLOW_QUIT") == "1"
        self.port = int(os.environ.get("KLAYOUT_AGENT_PORT", "8765"))

    # ---- helpers
    def view(self):
        mw = pya.Application.instance().main_window()
        v = mw.current_view()
        if v is None:
            raise self.va.ViewError("no design open: call open_design first")
        return v

    def layout(self, v):
        cv = v.active_cellview()
        if not cv.is_valid():
            raise self.va.ViewError("no design open: call open_design first")
        return cv.layout()

    def box(self, b):
        return [round(b.left, 3), round(b.bottom, 3), round(b.right, 3), round(b.top, 3)]

    def view_bbox(self, v):
        return self.box(v.box())

    def visible(self, v):
        out = []
        for lp in v.each_layer():
            if lp.visible and lp.source_layer >= 0:
                out.append(self.va.layer_label(lp.source_layer, max(lp.source_datatype, 0)))
        return out

    def clear_markers(self):
        for m in self.markers:
            try:
                m._destroy()
            except Exception:
                pass
        self.markers = []

    def add_marker(self, v, dbox, color=0xFF0000):
        m = pya.Marker(v)
        m.set(dbox)
        m.color = color
        m.frame_color = color
        m.line_width = 2
        m.vertex_size = 0
        self.markers.append(m)

    # ---- methods
    def ping(self, p):
        return {"ok": True, "klayout": pya.Application.instance().version(), "design": self.design}

    def quit(self, p):
        log("quit requested")
        pya.QTimer.singleShot(100, lambda: pya.Application.instance().exit(0))
        return {"ok": True}

    def open_design(self, p):
        va = self.va
        path = va.find_gds(p["design"])
        mw = pya.Application.instance().main_window()
        self.clear_markers()
        mw.load_layout(path, 0)      # mode 0: replace current view. Read only: this bridge never saves.
        v = mw.current_view()
        top = va.top_cell_name(p["design"])
        ly = v.active_cellview().layout()
        cell = ly.cell(top) or ly.top_cell()
        v.select_cell(cell.cell_index(), 0)
        v.max_hier()
        v.zoom_fit()
        self.design = p["design"]
        n_layers = sum(1 for lp in v.each_layer() if lp.source_layer >= 0)
        return {"design": p["design"], "top_cell": cell.name, "bbox_um": self.box(cell.dbbox()), "n_layers": n_layers}

    def zoom_to(self, p):
        va = self.va
        v = self.view()
        ly = self.layout(v)
        t = p["target"]
        if t.get("full"):
            v.zoom_fit()
        elif "bbox" in t:
            b = va.check_bbox(t["bbox"])
            v.zoom_box(pya.DBox(*b))
        elif "cell" in t:
            name = str(t["cell"])
            top = v.active_cellview().cell
            found = None
            for inst in top.each_inst():                  # instance name first (e.g. mprj), as offscreen_backend does
                if name in [str(val) for val in inst.properties().values()]:
                    found = inst.dbbox()
                    break
            c = ly.cell(name) if found is None else None
            if found is not None:
                pass
            elif c is not None:
                found = c.dbbox()
            else:
                low = name.lower()
                for inst in top.each_inst():
                    cn = ly.cell(inst.cell_index).name
                    if cn.lower() == low or low in cn.lower():
                        found = inst.dbbox()
                        break
            if found is None:
                raise va.ViewError("no cell or instance matches %r" % name)
            v.zoom_box(found.enlarged(found.width() * 0.05, found.height() * 0.05))
        else:
            raise va.ViewError("zoom_to target needs cell, bbox or full")
        return {"view_bbox_um": self.view_bbox(v)}

    def show_layers(self, p):
        va = self.va
        v = self.view()
        want = set(va.parse_layers(p["layers"]))
        only = bool(p.get("only", True))
        for lp in v.each_layer():
            if lp.source_layer < 0:
                continue
            hit = (lp.source_layer, max(lp.source_datatype, 0)) in want
            lp.visible = hit if only else (lp.visible or hit)
        v.update_content()
        return {"visible": self.visible(v)}

    def highlight_drc(self, p):
        va = self.va
        v = self.view()
        design = p["design"]
        maxn = int(p.get("max_items", 200))
        demo = p.get("demo_markers")
        self.clear_markers()
        if demo:
            boxes = va.check_markers(demo) if isinstance(demo, list) else va.demo_boxes(self.view_bbox(v))
            for b in boxes:
                self.add_marker(v, pya.DBox(*b), 0xFF8000)
            return {"n_markers": len(boxes), "source": "demo", "note": "demo boxes, NOT real DRC errors"}
        rep = va.find_drc_report(design)
        if rep is None:
            total = va.drc_summary_json(design)
            return {"n_markers": 0, "note": "no .lyrdb report; committed drc_klayout.json total errors = %s" % total}
        rdb = pya.ReportDatabase("agent")
        rdb.load(rep)
        n = 0
        for item in rdb.each_item():
            for val in item.each_value():
                if n >= maxn:
                    break
                if val.is_box():
                    b = val.box()
                elif val.is_polygon():
                    b = val.polygon().bbox()
                else:
                    continue
                self.add_marker(v, b)
                n += 1
        return {"n_markers": n, "source": va.rel(rep)}

    def measure(self, p):
        return self.va.ViewBackend()._measure(p["a"], p["b"])

    def snapshot(self, p):
        va = self.va
        v = self.view()
        self.seq += 1
        path = va.check_png_path(p.get("path"), self.design or "view", self.seq)
        w, h = va.clamp_px(p.get("width"), 1200), va.clamp_px(p.get("height"), 900)
        v.save_image(path, w, h)
        return {"png": va.rel(path), "view_bbox_um": self.view_bbox(v), "visible_layers": self.visible(v)}

    def state(self, p):
        try:
            v = self.view()
        except self.va.ViewError:
            return {"design": None, "view_bbox_um": None, "visible_layers": []}
        return {"design": self.design, "view_bbox_um": self.view_bbox(v), "visible_layers": self.visible(v)}

    # ---- GUI thread side
    # Errors become {"ok": false} replies instead of exceptions: an exception in a QTimer callback would only be
    # printed in KLayout's log and the waiting socket thread would hang until REQUEST_TIMEOUT_S.
    def run_gui(self, method, params):
        try:
            r = getattr(self, method)(params)
            if isinstance(r, dict):
                r.setdefault("ok", True)
            return r
        except self.va.ViewError as e:
            return {"ok": False, "error": str(e)}
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}

    # Runs on the GUI thread every 50 ms (pya.QTimer). At most 8 requests per tick so a burst cannot freeze the window.
    def tick(self):
        for _ in range(8):
            try:
                method, params, slot = self.q.get_nowait()
            except queue.Empty:
                return
            log("run %s" % method)
            slot["res"] = self.run_gui(method, params)
            slot["ev"].set()

    # ---- socket thread side (never touches pya)
    # Socket thread: hand the request to the GUI thread through the queue and block on an Event for the answer.
    def submit(self, method, params):
        slot = {"ev": threading.Event(), "res": None}
        self.q.put((method, params, slot))
        if not slot["ev"].wait(REQUEST_TIMEOUT_S):
            return {"ok": False, "error": "GUI thread did not answer in %d s (modal dialog open?)" % REQUEST_TIMEOUT_S}
        return slot["res"]

    def serve_conn(self, conn):
        with conn:
            f = conn.makefile("rb")
            while True:
                line = f.readline(MAX_LINE + 1)
                if not line:
                    return
                resp = process_line(line.decode("utf-8", "replace"), self.submit, self.token, self.allow_quit)
                conn.sendall(encode(resp))

    def serve(self):
        srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        # HOST is 127.0.0.1: the bridge can move the window and read files, so it must never be reachable off-host.
        srv.bind((HOST, self.port))
        srv.listen(4)
        log("listening on %s:%d (token %s, quit %s)" % (HOST, self.port, "on" if self.token else "off",
                                                       "on" if self.allow_quit else "off"))
        while True:
            conn, _ = srv.accept()
            threading.Thread(target=self.serve_conn, args=(conn,), daemon=True).start()


def start():
    global _BRIDGE, _TIMER
    b = Bridge()
    threading.Thread(target=b.serve, daemon=True).start()
    t = pya.QTimer()
    t.interval = 50
    t.timeout = b.tick
    t.start()
    _BRIDGE, _TIMER = b, t     # keep references alive, or the timer is garbage collected
    log("bridge started")


def _main():
    try:
        start()
    except Exception:  # noqa: BLE001
        import traceback
        msg = traceback.format_exc()
        log("START FAILED\n" + msg)
        dbg = os.environ.get("KLAYOUT_AGENT_LOG")
        if dbg:
            with open(dbg, "a") as f:
                f.write(msg)


if (os.environ.get("KLAYOUT_AGENT_NOSTART") != "1" and pya is not None
        and getattr(pya, "Application", None) is not None and pya.Application.instance() is not None):
    _main()
