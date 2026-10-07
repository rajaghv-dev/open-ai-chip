#!/usr/bin/env python3
"""GUI tools for the Hermes tool server: operate the REAL KLayout and Magic windows from the chat (read-only).

Defines `router` (FastAPI APIRouter), auto-mounted by tool_server.py _mount_extensions. operationId == tool name.
  gui_start {tool, design}   open the window (KLayout via examples/hermes_klayout_gui/start_live.sh, bridge 127.0.0.1:8765;
                             Magic in the LibreLane container on XQuartz with examples/hermes_desktop/magic_bridge, 127.0.0.1:8766)
  gui_stop {tool}            close only the window this module started
  gui_status                 pid, port, ready for both
  gui_command {text, tool?}  ONE plain-English sentence -> one or more of the actions below, by a regex parser (no model); starts the
                             window if needed; returns did / results / markdown / understood / hint (the 8B model routes this reliably)
  GET /gui                   "Layout tools" control panel (layout_panel.html, model-free; the desktop app opens it in a second window)
  gui_examples               the example sentences gui_command understands (what you type -> what happens)
  klayout_live {action, ...} open|zoom|layers|markers|measure|snapshot|state on the live KLayout window (LiveBackend)
  magic_live {action, ...}   open|zoom|layers|drc|find|measure|snapshot|state on the live Magic window (magic_bridge)
Every klayout_live / magic_live reply carries png_url and `markdown` (paste verbatim); PNGs go to build/agent/klayout_gui/
and are served by the tool server's GET /img/<name>. Safety: localhost only, allow-listed commands, never saves or writes
a layout, one window per tool, gui_stop kills only the pid / container it recorded. Does not import tool_server.py.
Docs: examples/hermes_desktop/magic_bridge/README.md, docs/HERMES_DESKTOP.md ("Operating KLayout and Magic from the chat",
"Operate KLayout and Magic by text"), examples/hermes_desktop/demos/GUI_DEMO.md
Tests: tests/tools/test_gui_tools.py
"""
import json
import os
import re
import secrets
import signal
import socket
import subprocess
import sys
import threading
import time
from typing import Any, Dict, List, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
KL_DIR = os.path.join(REPO, "examples", "hermes_klayout_gui")
MB_DIR = os.path.join(REPO, "examples", "hermes_desktop", "magic_bridge")
for _p in (os.path.join(REPO, "tools"), KL_DIR):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import eda_tools  # noqa: E402
import importlib.util  # noqa: E402


def _load_mb():
    """magic_bridge/client.py under a unique module name (a bare `client` would collide with other modules)."""
    spec = importlib.util.spec_from_file_location("magic_bridge_client", os.path.join(MB_DIR, "client.py"))
    m = importlib.util.module_from_spec(spec)
    sys.modules["magic_bridge_client"] = m
    spec.loader.exec_module(m)
    return m


mb = _load_mb()

HOST = "127.0.0.1"
TOOL_PORT = int(os.environ.get("CHIP_TOOLS_PORT", "8770"))
PUBLIC_URL = os.environ.get("CHIP_TOOLS_PUBLIC_URL", "http://%s:%d" % (HOST, TOOL_PORT)).rstrip("/")
IMG_DIR = os.path.join(REPO, "build", "agent", "klayout_gui")
GUI_DIR = os.path.join(REPO, "build", "agent", "gui")
STATE_FILE = os.path.join(GUI_DIR, "gui_state.json")
KLAYOUT_PORT = int(os.environ.get("KLAYOUT_AGENT_PORT", "8765"))
MAGIC_PORT = int(os.environ.get("MAGIC_BRIDGE_PORT", "8766"))
TOOLS = ("klayout", "magic")
START_TIMEOUT_S = 60
LOAD_TIMEOUT_S = 120
DRC_TIMEOUT_S = 180

router = APIRouter()
_LOCK = {"klayout": threading.Lock(), "magic": threading.Lock()}
_PROCS: Dict[str, Any] = {}          # tool -> Popen this process started (kept so stdin stays open for Magic)


# ---------------------------------------------------------------- state (what THIS module started)
def _load_state() -> dict:
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_state(st: dict):
    os.makedirs(GUI_DIR, exist_ok=True)
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(st, f)
    os.chmod(tmp, 0o600)           # holds the Magic bridge token
    os.replace(tmp, STATE_FILE)


def _set(tool: str, rec: Optional[dict]):
    st = _load_state()
    if rec is None:
        st.pop(tool, None)
    else:
        st[tool] = rec
    _save_state(st)


def _alive(pid: Optional[int], needle: str) -> bool:
    """pid exists and its command line contains needle (guards against pid reuse)."""
    if not pid:
        return False
    try:
        out = subprocess.run(["ps", "-p", str(int(pid)), "-o", "command="], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return False
    return needle in out


def _port_open(port: int) -> bool:
    try:
        socket.create_connection((HOST, port), timeout=1).close()
        return True
    except OSError:
        return False


def _docker_env() -> dict:
    return mb.docker_env()


# ---------------------------------------------------------------- image helpers
def _img_name(prefix: str) -> str:
    return "%s_%s_%03d.png" % (prefix, time.strftime("%Y%m%d_%H%M%S"), int(time.time() * 1000) % 1000)


def _img_reply(path: str, alt: str) -> dict:
    name = os.path.basename(path)
    url = "%s/img/%s" % (PUBLIC_URL, name)
    return {"png_url": url, "markdown": "![%s](%s)" % (alt, url)}


# ---------------------------------------------------------------- KLayout
def _klayout_cmd_env(design: str) -> (list, dict):
    env = dict(os.environ, KLAYOUT_AGENT_PORT=str(KLAYOUT_PORT), KLAYOUT_AGENT_ALLOW_QUIT="1")
    return ["bash", os.path.join(KL_DIR, "start_live.sh"), design], env


def _klayout_pid(script_pid: int) -> Optional[int]:
    out = subprocess.run(["pgrep", "-P", str(script_pid)], capture_output=True, text=True).stdout.split()
    for p in out:
        if _alive(int(p), "klayout"):
            return int(p)
    return None


def _start_klayout(design: str) -> dict:
    if _port_open(KLAYOUT_PORT):
        rec = _load_state().get("klayout")
        if rec and _alive(rec.get("pid"), "klayout"):
            out = {"ok": True, "already_running": True, "pid": rec["pid"], "port": KLAYOUT_PORT, "design": rec.get("design"),
                   "note": "the KLayout window is already open; use klayout_live"}
            if rec.get("design") != design:       # same window, other design: reuse it (one window per tool)
                be = _kl_backend()
                try:
                    r = be.open_design(design)
                finally:
                    be.close()
                out["ok"] = bool(r.get("ok"))
                if r.get("ok"):
                    out["design"] = rec["design"] = design
                    _set("klayout", rec)
                else:
                    out["error"] = r.get("error")
            return out
        return {"ok": False, "error": "port %d is in use by something this tool did not start; close that KLayout window "
                                      "first (it will not be touched)" % KLAYOUT_PORT}
    os.makedirs(GUI_DIR, exist_ok=True)
    cmd, env = _klayout_cmd_env(design)
    log = open(os.path.join(GUI_DIR, "klayout.log"), "ab")
    t0 = time.time()
    p = subprocess.Popen(cmd, cwd=REPO, env=env, stdout=log, stderr=log, stdin=subprocess.DEVNULL, start_new_session=True)
    _PROCS["klayout"] = p
    pid = None
    for _ in range(START_TIMEOUT_S * 4):
        pid = pid or _klayout_pid(p.pid)
        if _port_open(KLAYOUT_PORT):
            break
        if p.poll() is not None:
            return {"ok": False, "error": "start_live.sh exited with %s; see build/agent/gui/klayout.log" % p.returncode}
        time.sleep(0.25)
    else:
        _kill_pids(p.pid, pid)
        return {"ok": False, "error": "KLayout bridge did not come up in %d s; see build/agent/gui/klayout.log" % START_TIMEOUT_S}
    pid = pid or _klayout_pid(p.pid)
    _set("klayout", {"pid": pid, "script_pid": p.pid, "port": KLAYOUT_PORT, "design": design, "t0": t0})
    # start_live.sh opens the design itself; confirm through the bridge
    try:
        from live_backend import LiveBackend
        be = LiveBackend(port=KLAYOUT_PORT, timeout=60)
        st = be.state()
        if not st.get("design"):                 # start_live.sh opens the design in the background; make sure it is there
            st = be.open_design(design)
            st = {**be.state(), "ok": st.get("ok"), "error": st.get("error")}
        be.close()
    except Exception as e:  # noqa: BLE001
        st = {"ok": False, "error": str(e)}
    return {"ok": True, "pid": pid, "port": KLAYOUT_PORT, "ready": bool(st.get("ok")), "design": st.get("design") or design,
            "seconds": round(time.time() - t0, 1)}


def _kill_pids(*pids):
    for pid in pids:
        if pid:
            try:
                os.kill(int(pid), signal.SIGKILL)          # KLayout ignores SIGTERM
            except OSError:
                pass


def _stop_klayout() -> dict:
    rec = _load_state().get("klayout")
    if not rec:
        return {"ok": True, "stopped": False, "note": "no KLayout window was started by this tool"}
    pid, via = rec.get("pid"), None
    if _alive(pid, "klayout"):
        try:
            from live_backend import LiveBackend
            be = LiveBackend(port=rec.get("port", KLAYOUT_PORT), timeout=5)
            be.quit_window()                               # admin quit (window was started with ALLOW_QUIT=1)
            via = "quit"
        except Exception:  # noqa: BLE001
            via = None
        for _ in range(12):
            if not _alive(pid, "klayout"):
                break
            time.sleep(0.25)
        if _alive(pid, "klayout"):
            _kill_pids(pid)
            via = "SIGKILL"
            time.sleep(0.5)
    p = _PROCS.pop("klayout", None)
    if p is not None:
        _kill_pids(p.pid) if p.poll() is None else None
        try:
            p.wait(timeout=2)
        except Exception:  # noqa: BLE001
            pass
    _set("klayout", None)
    return {"ok": True, "stopped": True, "pid": pid, "via": via or "already gone"}


def _kl_backend():
    from live_backend import LiveBackend, LiveError
    try:
        return LiveBackend(port=KLAYOUT_PORT, timeout=60)
    except LiveError as e:
        raise RuntimeError("%s -- call gui_start {tool: klayout, design: ...} first" % e)


# ---------------------------------------------------------------- Magic
def _display_check() -> Optional[str]:
    """macOS only: XQuartz must listen on TCP 6000 (the Colima VM connects over TCP)."""
    if sys.platform != "darwin":
        return None
    if not subprocess.run(["lsof", "-nP", "-iTCP:6000", "-sTCP:LISTEN"], capture_output=True).stdout:
        return ("XQuartz is not listening on TCP 6000. Run: open -a XQuartz (one-time setup is in the header of "
                "scripts/gui/open_gui.sh: nolisten_tcp false, then DISPLAY=:0 /opt/X11/bin/xhost +localhost)")
    x = "/opt/X11/bin/xhost"
    if os.path.exists(x):
        out = subprocess.run([x], capture_output=True, text=True, env=dict(os.environ, DISPLAY=":0")).stdout
        if "localhost" not in out:
            return ("XQuartz does not allow localhost clients (xhost shows: %s). Run once: DISPLAY=:0 /opt/X11/bin/xhost +localhost"
                    % out.strip().replace("\n", " | ")[:160])
    return None


def _start_magic(design: str) -> dict:
    rec = _load_state().get("magic")
    if rec and _port_open(rec.get("port", MAGIC_PORT)):
        out = {"ok": True, "already_running": True, "port": rec["port"], "design": rec.get("design"), "pid": rec.get("pid"),
               "note": "the Magic window is already open; use magic_live"}
        if rec.get("design") != design:
            import view_api
            r = mb.MagicClient(rec["port"], rec.get("token")).request(
                "LOAD", view_api.find_gds(design), view_api.top_cell_name(design), timeout=LOAD_TIMEOUT_S)
            out["ok"] = bool(r.get("ok"))
            if r.get("ok"):
                out["design"] = rec["design"] = design
                _set("magic", rec)
            else:
                out["error"] = r.get("error")
        return out
    if _port_open(MAGIC_PORT):
        return {"ok": False, "error": "port %d is in use by something this tool did not start" % MAGIC_PORT}
    msg = _display_check()
    if msg:
        return {"ok": False, "error": msg}
    import view_api
    gds, top = view_api.find_gds(design), view_api.top_cell_name(design)
    token = secrets.token_hex(8)
    os.makedirs(GUI_DIR, exist_ok=True)
    log = open(os.path.join(GUI_DIR, "magic.log"), "ab")
    t0 = time.time()
    p = subprocess.Popen(mb.docker_command(MAGIC_PORT, token), cwd=REPO, env=_docker_env(), stdin=subprocess.PIPE,
                         stdout=log, stderr=log, start_new_session=True)
    _PROCS["magic"] = p
    p.stdin.write(mb.bridge_source_line().encode())     # stdin stays open: Magic quits at EOF (e.g. if this server dies)
    p.stdin.flush()
    c = mb.MagicClient(MAGIC_PORT, token)
    ready = False
    for _ in range(START_TIMEOUT_S * 2):
        if p.poll() is not None:
            return {"ok": False, "error": "docker run exited with %s; see build/agent/gui/magic.log" % p.returncode}
        if c.request("PING", timeout=2).get("ok"):
            ready = True
            break
        time.sleep(0.5)
    if not ready:
        _stop_magic_proc(p, MAGIC_PORT, token)
        return {"ok": False, "error": "Magic bridge did not answer in %d s; see build/agent/gui/magic.log" % START_TIMEOUT_S}
    _set("magic", {"pid": p.pid, "port": MAGIC_PORT, "token": token, "design": design, "t0": t0,
                   "container": mb.container_name(MAGIC_PORT)})
    r = c.request("LOAD", gds, top, timeout=LOAD_TIMEOUT_S)
    out = {"ok": bool(r.get("ok")), "pid": p.pid, "port": MAGIC_PORT, "ready": True, "design": design,
           "container": mb.container_name(MAGIC_PORT), "seconds": round(time.time() - t0, 1)}
    if r.get("ok"):
        out["top_cell"], out["bbox_um"] = top, mb.nums(r.get("bbox_um"))
    else:
        out["error"] = r.get("error")
    return out


def _stop_magic_proc(p, port, token):
    try:
        mb.MagicClient(port, token).request("QUIT", timeout=5)      # quit -noprompt inside Magic
    except Exception:  # noqa: BLE001
        pass
    if p is not None:
        try:
            p.stdin.close()
        except Exception:  # noqa: BLE001
            pass
        try:
            p.wait(timeout=8)
        except Exception:  # noqa: BLE001
            pass
    # container left over (Magic did not exit): kill only the container this module named
    subprocess.run(["docker", "kill", mb.container_name(port)], env=_docker_env(), capture_output=True, timeout=30)
    if p is not None and p.poll() is None:
        p.kill()


def _stop_magic() -> dict:
    rec = _load_state().get("magic")
    if not rec:
        return {"ok": True, "stopped": False, "note": "no Magic window was started by this tool"}
    p = _PROCS.pop("magic", None)
    _stop_magic_proc(p, rec["port"], rec.get("token"))
    gone = not _port_open(rec["port"])
    _set("magic", None)
    return {"ok": True, "stopped": True, "container": rec.get("container"), "port_closed": gone}


def _mg() -> "mb.MagicClient":
    rec = _load_state().get("magic")
    if not rec or not _port_open(rec["port"]):
        raise RuntimeError("no Magic window is open -- call gui_start {tool: magic, design: ...} first")
    return mb.MagicClient(rec["port"], rec.get("token"))


def _magic_snapshot(c, out_path: str) -> dict:
    """Magic's own `plot pnm` of the region the window was last pointed at (bridge PLOT) -> PNG.
    xwd of the XQuartz window is only tried with GUI_MAGIC_XWD=1: on this Mac XQuartz returns a blank (black) image for
    GPU-composited windows, so it is off by default."""
    if os.environ.get("GUI_MAGIC_XWD") == "1":
        try:
            w, h = mb.window_png(out_path)
            if _png_has_content(out_path):
                return {"ok": True, "snapshot_source": "window", "size_px": [w, h]}
        except mb.MagicError:
            pass
    pnm = os.path.join(REPO, "build", "agent", "magic_plot.pnm")
    r = c.request("PLOT", pnm, 1000, timeout=120)
    if not r.get("ok"):
        return {"ok": False, "error": "plot failed: %s" % r.get("error")}
    w, h = mb.pnm_to_png(pnm, out_path)
    return {"ok": True, "snapshot_source": "plot_pnm", "size_px": [w, h]}


def _png_has_content(path: str) -> bool:
    return os.path.getsize(path) > 6000


# ---------------------------------------------------------------- request models
D = Field(..., description="design directory name, e.g. kv_attn_n8 or user_project_wrapper_soc_kv")


class GuiStartReq(BaseModel):
    tool: str = Field(..., description="\"klayout\" or \"magic\"", examples=["magic"])
    design: str = D


class GuiStopReq(BaseModel):
    tool: str = Field(..., description="\"klayout\" or \"magic\"", examples=["magic"])


class Empty(BaseModel):
    pass


class LiveReq(BaseModel):
    action: str = Field(..., description="what to do with the open window; see the tool description for the list")
    design: Optional[str] = Field(None, description="design name (needed for open; markers/drc use the open design)")
    layers: Optional[List[str]] = Field(None, description="layers such as [\"met1\",\"met2\"]", examples=[["met1", "met2"]])
    only: Optional[bool] = Field(None, description="layers: true (default) = hide all other layers")
    bbox: Optional[List[float]] = Field(None, description="[x1,y1,x2,y2] in micrometres, x1<x2 and y1<y2; the lower-left "
                                        "50 um is [0,0,50,50]", min_length=4, max_length=4)
    x1: Optional[float] = Field(None, description="flat alternative to bbox/measure points: x1 in um")
    y1: Optional[float] = Field(None, description="y1 in um")
    x2: Optional[float] = Field(None, description="x2 in um")
    y2: Optional[float] = Field(None, description="y2 in um")
    cell: Optional[str] = Field(None, description="zoom: cell or instance name, e.g. mprj (KLayout only)")
    full: Optional[bool] = Field(None, description="zoom: true = whole design")
    a: Optional[List[float]] = Field(None, description="measure: first point [x, y] in um", min_length=2, max_length=2)
    b: Optional[List[float]] = Field(None, description="measure: second point [x, y] in um", min_length=2, max_length=2)
    label: Optional[str] = Field(None, description="magic find: pin or net label, e.g. clk or wbs_dat_i[0]")
    demo_markers: Optional[bool] = Field(None, description="klayout markers: true draws 5 DEMO boxes (not real errors)")
    width: Optional[int] = Field(None, description="klayout snapshot width px 200..2400", ge=200, le=2400)
    height: Optional[int] = Field(None, description="klayout snapshot height px 200..2400", ge=200, le=2400)


def _bbox(req: LiveReq):
    if req.bbox:
        return req.bbox
    if None not in (req.x1, req.y1, req.x2, req.y2):
        return [req.x1, req.y1, req.x2, req.y2]
    return None


def _points(req: LiveReq):
    if req.a and req.b:
        return req.a, req.b
    if None not in (req.x1, req.y1, req.x2, req.y2):
        return [req.x1, req.y1], [req.x2, req.y2]
    return None, None


@router.get("/gui", include_in_schema=False)
def gui_panel():
    """The Layout tools control panel: one self-contained HTML page that calls the GUI endpoints of this server (no model)."""
    from fastapi.responses import HTMLResponse
    with open(os.path.join(HERE, "layout_panel.html"), encoding="utf-8") as f:
        return HTMLResponse(f.read())


def post(name: str, summary: str):
    return router.post("/" + name, operation_id=name, summary=summary, response_model=None)


# ---------------------------------------------------------------- tools: start / stop / status
@post("gui_start", "Open the real KLayout or Magic window")
def gui_start(req: GuiStartReq) -> dict:
    """Open a real desktop window on the user's screen for one design: tool "klayout" or "magic". It takes 5 to 30 seconds.
    Returns pid, port and ready. Afterwards drive it with klayout_live or magic_live. Read-only; one window per tool."""
    t = (req.tool or "").lower().strip()
    if t not in TOOLS:
        return {"ok": False, "error": "tool must be klayout or magic"}
    try:
        eda_tools._check_design(req.design)
        with _LOCK[t]:
            return _start_klayout(req.design) if t == "klayout" else _start_magic(req.design)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}


@post("gui_stop", "Close the KLayout or Magic window this tool opened")
def gui_stop(req: GuiStopReq) -> dict:
    """Close the window opened by gui_start (tool "klayout" or "magic"). Only touches what gui_start started."""
    t = (req.tool or "").lower().strip()
    if t not in TOOLS:
        return {"ok": False, "error": "tool must be klayout or magic"}
    try:
        with _LOCK[t]:
            return _stop_klayout() if t == "klayout" else _stop_magic()
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}


@post("gui_status", "Which GUI windows are open")
def gui_status(req: Empty = Empty()) -> dict:
    """Report for KLayout and Magic: running, pid, port, ready (bridge answers), design, seconds since start."""
    st, out = _load_state(), {}
    for t in TOOLS:
        rec = st.get(t)
        port = (rec or {}).get("port", KLAYOUT_PORT if t == "klayout" else MAGIC_PORT)
        listening = _port_open(port)
        running = bool(rec) and (_alive(rec.get("pid"), "klayout") if t == "klayout" else listening)
        out[t] = {"running": running, "port": port, "ready": running and listening,
                  "pid": (rec or {}).get("pid"), "design": (rec or {}).get("design"),
                  "seconds": round(time.time() - rec["t0"], 1) if running and rec.get("t0") else None}
        if listening and not rec:
            out[t]["note"] = "port is in use by a window this tool did not start (left alone)"
    return {"ok": True, **out}


# ---------------------------------------------------------------- tool: klayout_live
KL_ACTIONS = ("open", "zoom", "layers", "markers", "measure", "snapshot", "state")


@post("klayout_live", "Operate the open KLayout window and get a picture")
def klayout_live(req: LiveReq) -> dict:
    """Operate the REAL KLayout window opened by gui_start. action is one of: open (design), zoom (bbox [x1,y1,x2,y2] in um,
    or cell like mprj, or full=true), layers (layers like ["met4","met5"]; only=true hides the rest), markers (DRC markers
    of the open design; these designs are DRC clean so 0 is the honest answer; demo_markers=true draws 5 fake demo boxes),
    measure (a and b = [x,y] in um), snapshot, state. EVERY reply has png_url and markdown: paste the markdown line
    verbatim in your answer so the user sees the picture."""
    a = (req.action or "").lower().strip()
    if a not in KL_ACTIONS:
        return {"ok": False, "error": "action must be one of: " + ", ".join(KL_ACTIONS)}
    with _LOCK["klayout"]:
        try:
            be = _kl_backend()
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e)}
        try:
            t0 = time.time()
            if a == "open":
                if not req.design:
                    return {"ok": False, "error": "open needs design"}
                r = be.open_design(req.design)
                if r.get("ok"):
                    rec = _load_state().get("klayout")
                    if rec:
                        rec["design"] = req.design
                        _set("klayout", rec)
            elif a == "zoom":
                bb = _bbox(req)
                target = {"bbox": bb} if bb else ({"cell": req.cell} if req.cell else {"full": True})
                r = be.zoom_to(target)
            elif a == "layers":
                if not req.layers:
                    return {"ok": False, "error": "layers needs a list such as [\"met4\",\"met5\"]"}
                r = be.show_layers(req.layers, True if req.only is None else req.only)
            elif a == "markers":
                design = req.design or (_load_state().get("klayout") or {}).get("design")
                if not design:
                    return {"ok": False, "error": "markers needs design"}
                r = be.highlight_drc(design, 200, True if req.demo_markers else None)
            elif a == "measure":
                p1, p2 = _points(req)
                if not p1:
                    return {"ok": False, "error": "measure needs a and b ([x,y] in um) or x1,y1,x2,y2"}
                r = be.measure(p1, p2)
            elif a == "state":
                r = be.state()
            else:
                r = {"ok": True}
            if not r.get("ok"):
                return r
            name = _img_name("live_klayout")
            s = be.snapshot(os.path.join(IMG_DIR, name), req.width or 1200, req.height or 900)
            if not s.get("ok"):
                return {**r, "snapshot_error": s.get("error")}
            r = dict(r)
            r.pop("png", None)
            r.update(_img_reply(os.path.join(IMG_DIR, name), "KLayout " + a))
            r["view_bbox_um"] = s.get("view_bbox_um")
            r["visible_layers"] = s.get("visible_layers")
            r["seconds"] = round(time.time() - t0, 2)
            return r
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": "%s: %s" % (type(e).__name__, e)}
        finally:
            be.close()


# ---------------------------------------------------------------- tool: magic_live
MG_ACTIONS = ("open", "zoom", "layers", "drc", "find", "measure", "snapshot", "state")


def _magic_do(req: LiveReq, a: str, c) -> dict:
    if a == "open":
        if not req.design:
            return {"ok": False, "error": "open needs design"}
        import view_api
        r = c.request("LOAD", view_api.find_gds(req.design), view_api.top_cell_name(req.design), timeout=LOAD_TIMEOUT_S)
        if r.get("ok"):
            rec = _load_state().get("magic")
            if rec:
                rec["design"] = req.design
                _set("magic", rec)
            return {"ok": True, "design": req.design, "bbox_um": mb.nums(r.get("bbox_um"))}
        return r
    if a == "zoom":
        bb = _bbox(req)
        if bb:
            import view_api
            bb = view_api.check_bbox(bb)
            r = c.request("VIEW", *bb)
        elif req.full or not req.cell:
            r = c.request("FULL")
        else:
            return {"ok": False, "error": "magic zoom takes bbox [x1,y1,x2,y2] in um or full=true (cell is KLayout only)"}
        return {"ok": True, "view_bbox_um": mb.nums(r.get("view_bbox_um"))} if r.get("ok") else r
    if a == "layers":
        if not req.layers:
            return {"ok": False, "error": "layers needs a list such as [\"met1\",\"met2\"]"}
        r = c.request("SEE", mb.magic_layers(req.layers))
        return {"ok": True, "visible": req.layers, "magic_layers": r.get("visible")} if r.get("ok") else r
    if a == "drc":
        r = c.request("DRC", timeout=DRC_TIMEOUT_S)
        if not r.get("ok"):
            return r
        m = re.search(r"errors=(\d+)", r["text"])
        why = re.search(r" why=(.*?) counts=", r["text"] + " ")
        n = int(m.group(1)) if m else None
        reasons = (why.group(1).strip() if why else "")
        return {"ok": True, "drc_errors": n, "reasons": reasons or ("none: Magic reports 0 DRC errors" if n == 0 else ""),
                "note": "Magic's own DRC of the loaded view (sky130A rules), run live in the window"}
    if a == "find":
        if not req.label:
            return {"ok": False, "error": "find needs label"}
        r = c.request("FIND", req.label)
        if not r.get("ok"):
            return r
        return {"ok": True, "label": req.label, "box_um": mb.nums(r.get("box_um")), "moved": r.get("moved") == "1"}
    if a == "measure":
        p1, p2 = _points(req)
        if not p1:
            return {"ok": False, "error": "measure needs a and b ([x,y] in um) or x1,y1,x2,y2"}
        r = c.request("MEASURE", p1[0], p1[1], p2[0], p2[1])
        return {"ok": True, **{k: float(r[k]) for k in ("dx_um", "dy_um", "distance_um") if k in r}} if r.get("ok") else r
    if a == "state":
        r = c.request("STATE")
        return {"ok": True, "top": r.get("top"), "view_bbox_um": mb.nums(r.get("view_bbox_um"))} if r.get("ok") else r
    return {"ok": True}


@post("magic_live", "Operate the open Magic window and get a picture")
def magic_live(req: LiveReq) -> dict:
    """Operate the REAL Magic window opened by gui_start. action is one of: open (design), zoom (bbox [x1,y1,x2,y2] in um
    from the lower-left corner, e.g. [0,0,50,50] = lower-left 50 um; or full=true), layers (layers like ["met1","met2"],
    others hidden), drc (runs Magic DRC and returns the error count and reasons; these designs are clean so 0 is the honest
    answer), find (label = pin or net name), measure (a and b = [x,y] in um), snapshot, state. EVERY reply has png_url and
    markdown: paste the markdown line verbatim in your answer so the user sees the picture."""
    a = (req.action or "").lower().strip()
    if a not in MG_ACTIONS:
        return {"ok": False, "error": "action must be one of: " + ", ".join(MG_ACTIONS)}
    with _LOCK["magic"]:
        try:
            c = _mg()
            t0 = time.time()
            r = _magic_do(req, a, c)
            if not r.get("ok"):
                return r
            name = _img_name("live_magic")
            path = os.path.join(IMG_DIR, name)
            os.makedirs(IMG_DIR, exist_ok=True)
            s = _magic_snapshot(c, path)
            if not s.get("ok"):
                return {**r, "snapshot_error": s.get("error")}
            r = dict(r)
            r.update(_img_reply(path, "Magic " + a))
            r["snapshot_source"], r["size_px"] = s["snapshot_source"], s["size_px"]
            r["seconds"] = round(time.time() - t0, 2)
            return r
        except Exception as e:  # noqa: BLE001
            return {"ok": False, "error": str(e) if isinstance(e, (RuntimeError, mb.MagicError)) else "%s: %s" % (type(e).__name__, e)}



# ---------------------------------------------------------------- tool: gui_command (text -> actions, regex parser, no model)
NUM = r"-?\d+(?:\.\d+)?"
ALL_LAYERS = ["diff", "poly", "li1", "met1", "met2", "met3", "met4", "met5", "mcon", "via", "via2", "via3"]
LAYER_RE = re.compile(r"\b(met[1-5]|li1|poly|diff|mcon|via[1-4]?|nwell)\b")
VERBS = r"open|load|launch|start|show|display|hide|zoom|run|check|measure|find|locate|snapshot|screenshot|take|status|close|quit|exit|stop|fit|save|write|export|dump|delete|edit|draw|paste|copy|rename|undo|erase"
SPLIT_RE = re.compile(r"\s*(?:;|\.\s+|,\s*(?:and\s+)?then\s+|\bthen\b|\band\s+then\b|\band\b(?=\s+(?:%s)\b)|,(?=\s*(?:%s)\b))\s*" % (VERBS, VERBS))
FILLER_RE = re.compile(r"^(?:(?:please|pls|now|next|also|first|finally|can you|could you|would you|i want to|i would like to|let'?s|go ahead and|and)\s+)+")
VISUAL_OPS = ("open", "zoom", "layers", "drc", "measure", "find", "snapshot")
EXAMPLES = [
    ("open kv_attn_n8 in klayout", "starts KLayout on kv_attn_n8 (or loads it into the open window)", "the whole engine, 260 x 260 um"),
    ("show only met1 and met2", "keeps only li1/met1-style layers you name visible, hides the rest", "rails and routing only"),
    ("hide met5", "hides one layer, keeps the others", "same view without met5"),
    ("show all", "all layers visible again", "every mask layer stacked"),
    ("zoom to the lower-left 50 um", "view = the 50 x 50 um corner at the die origin", "standard-cell rows"),
    ("zoom to the macro mprj", "KLayout zooms to the cell or instance (wrapper designs)", "our engine inside the Caravel user area"),
    ("zoom to 0 0 100 100", "view = that box in um", "a 100 x 100 um window"),
    ("zoom out", "whole design (also: fit, full)", "the whole die"),
    ("open kv_attn_n8 in magic", "starts Magic (in the container, on XQuartz) on kv_attn_n8", "a Magic window and its picture"),
    ("run drc", "Magic: its own DRC (count + reasons). KLayout: markers from the run's DRC report", "0 errors for these clean designs"),
    ("measure from 0,0 to 100,0", "ruler between two points in um", "dx, dy and distance"),
    ("find clk", "Magic: moves the box to the pin or net label", "the pin highlighted"),
    ("snapshot", "a picture of the open window", "the current view"),
    ("status", "which windows are open", "pid, port, design"),
    ("close all", "closes the windows this tool opened (close klayout / close magic for one)", "windows disappear"),
]
HINT = ("I did not understand. Say one or more of: " + "; ".join('"%s"' % e[0] for e in EXAMPLES[:12])
        + ". Layers: met1..met5, li1, poly, diff. Chain with 'and' or 'then'. Add 'in klayout' or 'in magic' to pick the tool.")


class GuiCommandReq(BaseModel):
    text: str = Field(..., description="the user's sentence, VERBATIM (do not rewrite or translate it), e.g. "
                      "\"open kv_attn_n8 and show only met1 and met2\"", examples=["zoom to the lower-left 50 um"])
    tool: Optional[str] = Field(None, description="\"klayout\" or \"magic\" only when the user's message starts with /klayout "
                                "or /magic; otherwise omit it (the open window, else KLayout, is used)")


def _designs_list() -> List[str]:
    try:
        return sorted(eda_tools._designs(), key=len, reverse=True)
    except Exception:  # noqa: BLE001
        return []


def _layers_in(clause: str) -> List[str]:
    ls = [m for m in LAYER_RE.findall(clause)]
    ls += ["met" + n for n in re.findall(r"\bmetal\s*([1-5])\b", clause)]
    return list(dict.fromkeys(ls))


def _tool_in(clause: str) -> Optional[str]:
    m = re.search(r"\b(klayout|k-layout|magic)\b", clause)
    return None if not m else ("magic" if m.group(1) == "magic" else "klayout")


def _design_in(clause: str, designs: List[str]) -> Optional[str]:
    toks = set(re.findall(r"[a-z0-9_]+", clause))
    for d in designs:
        if d.lower() in toks:
            return d
    try:   # loose names: "vision lit", "kv attention 16", "the kv_attn design" (normalize_tools.design_from_text)
        import normalize_tools
        return normalize_tools.design_from_text(clause, designs)
    except Exception:  # noqa: BLE001
        return None


def parse_text(text: str, tool: Optional[str] = None, running: Optional[List[str]] = None, designs: Optional[List[str]] = None,
               last_tool: Optional[str] = None) -> dict:
    """Pure parser. -> {"actions": [{"op", "tool", ...}], "unparsed": [clause, ...]}. ops: open, zoom, layers, drc, measure, find,
    snapshot, status, close. Never produces a save or write action (there is no such op)."""
    designs = designs if designs is not None else _designs_list()
    running = list(running or [])
    t = re.sub(r"\s+", " ", (text or "").lower().replace("µm", "um")).strip()
    t = re.sub(r"[?!]+$", "", t).strip()
    sentence_tool = tool if tool in TOOLS else _tool_in(t)
    default = sentence_tool or (last_tool if last_tool in running else (running[0] if running else "klayout"))
    clauses = [c for c in SPLIT_RE.split(t) if c.strip()]
    actions, unparsed = [], []
    sent_design = _design_in(t, designs)
    for raw in clauses:
        c = FILLER_RE.sub("", raw.strip()).strip(" ,.")
        if not c:
            continue
        own = _tool_in(c)
        tl = own or default
        nums = re.findall(NUM, c)
        a = None
        if re.match(r"(close|quit|exit|stop|shut ?down|kill)\b", c):
            tgt = own or (None if re.search(r"\b(all|everything|both|windows?)\b", c) else None)
            a = [{"op": "close", "tool": x} for x in ((tgt,) if tgt else TOOLS)]
        elif re.search(r"\bstatus\b|what(?:'s| is) open|which windows|is .*open", c):
            a = [{"op": "status", "tool": tl}]
        elif re.search(r"\b(snapshot|screenshot|screen shot|picture|photo|take a pic)\b", c) and not re.match(r"(zoom|open|show)", c):
            a = [{"op": "snapshot", "tool": tl}]
        elif re.search(r"\bdrc\b|design rule", c):
            a = [{"op": "drc", "tool": tl}]
        elif re.search(r"\b(measure|distance|ruler)\b", c) and len(nums) == 4:
            a = [{"op": "measure", "tool": tl, "a": [float(nums[0]), float(nums[1])], "b": [float(nums[2]), float(nums[3])]}]
        elif re.match(r"(find|locate|where is|highlight)\b", c) and not _layers_in(c):
            m = re.match(r"(?:find|locate|where is|highlight)\s+(?:the\s+)?(?:net|label|pin|signal)?\s*([a-z0-9_\[\]./<>-]+)$", c)
            if m and m.group(1) not in ("drc", "markers"):
                a = [{"op": "find", "tool": own or "magic", "label": m.group(1)}]
        elif re.match(r"(zoom|go to|look at|fit|focus)\b", c):
            a = _parse_zoom(c, nums, tl)
        elif re.match(r"(hide|remove|turn off|disable|drop)\b", c) and (_layers_in(c)):
            a = [{"op": "layers", "tool": tl, "hide": _layers_in(c)}]
        elif re.match(r"(show|display|turn on|enable|only|see|view|switch to|add|keep)\b", c) and (_layers_in(c) or re.search(r"\ball\b", c)):
            ls = _layers_in(c)
            if not ls:
                a = [{"op": "layers", "tool": tl, "layers": list(ALL_LAYERS), "only": False, "all": True}]
            else:
                add = bool(re.search(r"\b(also|add|in addition|too|as well)\b", c)) or c.startswith(("add", "turn on", "enable"))
                a = [{"op": "layers", "tool": tl, "layers": ls, "only": not add}]
        elif re.match(r"(open|load|launch|start|show|view|display|go)\b", c) or (own and re.match(r"(klayout|magic)\b", c)):
            d = _design_in(c, designs)
            if d or own or re.match(r"(open|load|launch|start)\b", c):
                a = [{"op": "open", "tool": tl, "design": d or (sent_design if re.match(r"(open|load|launch|start)\b", c) else None)}]
        if a:
            actions.extend(a)
        else:
            unparsed.append(raw.strip())
    return {"actions": actions, "unparsed": unparsed}


def _parse_zoom(c: str, nums: List[str], tl: str) -> Optional[List[dict]]:
    z = {"op": "zoom", "tool": tl}
    if re.search(r"\b(out|fit|full|whole|everything|entire|reset|all)\b", c):
        return [dict(z, full=True)]
    m = re.search(r"\b(lower|bottom|upper|top)[- ]?(left|right)\b[^0-9-]*(%s)(?:\s*x\s*(%s))?" % (NUM, NUM), c)
    if m:
        v, h = ("lower" if m.group(1) in ("lower", "bottom") else "upper"), m.group(2)
        return [dict(z, corner="%s-%s" % (v, h), size=float(m.group(3)))]
    m = re.search(r"\b(?:center|centre|middle)\b[^0-9-]*(%s)" % NUM, c)
    if m:
        return [dict(z, corner="center", size=float(m.group(1)))]
    if len(nums) == 4:
        x1, y1, x2, y2 = [float(n) for n in nums]
        return [dict(z, bbox=[min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)])]
    m = re.search(r"\b(?:macro|cell|instance|block|module)\s+([a-z0-9_.\[\]]+)", c)
    if m:
        return [dict(z, cell=m.group(1))]
    m = re.match(r"(?:zoom|go|look|focus)\s*(?:to|into|at|on)?\s*(?:the\s+)?([a-z][a-z0-9_]*)$", c)
    if m and m.group(1) not in ("in", "to", "zoom", "lower", "upper", "center"):
        return [dict(z, cell=m.group(1))]
    return None


def _die_bbox(design: str) -> List[float]:
    cfg = os.path.join(REPO, "designs", design, "config.json")
    try:
        d = json.load(open(cfg)).get("DIE_AREA")
        if isinstance(d, str):
            d = [float(x) for x in d.split()]
        if d and len(d) == 4:
            return [float(x) for x in d]
    except (OSError, ValueError, TypeError):
        pass
    raise RuntimeError("cannot read DIE_AREA of %s; give coordinates instead (zoom to x1 y1 x2 y2)" % design)


def _corner_bbox(corner: str, size: float, die: List[float]) -> List[float]:
    x1, y1, x2, y2 = die
    w, h = x2 - x1, y2 - y1
    sx, sy = min(size, w), min(size, h)
    if corner == "center":
        cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
        return [round(cx - sx / 2, 3), round(cy - sy / 2, 3), round(cx + sx / 2, 3), round(cy + sy / 2, 3)]
    left = corner.endswith("left")
    lower = corner.startswith("lower")
    bx = x1 if left else x2 - sx
    by = y1 if lower else y2 - sy
    return [round(bx, 3), round(by, 3), round(bx + sx, 3), round(by + sy, 3)]


_VIS: Dict[str, Optional[List[str]]] = {"klayout": None, "magic": None}   # layers last shown (None = all)
_LAST = {"tool": None}


def _slim(r: dict) -> dict:
    return {k: v for k, v in r.items() if k not in ("markdown", "png")}


def _running() -> List[str]:
    st = gui_status()
    return [t for t in TOOLS if st[t]["running"] and st[t]["ready"]]


def _gui_design(tool: str) -> Optional[str]:
    st = _load_state()
    return (st.get(tool) or {}).get("design")


def _exec(act: dict, ctx: dict, final: bool) -> dict:
    """Run one parsed action; returns a reply dict (ok, ...). final=True: take the picture (Magic skips it otherwise)."""
    op, tool = act["op"], act["tool"]
    if op == "status":
        return gui_status()
    if op == "close":
        r = gui_stop(GuiStopReq(tool=tool))
        _VIS[tool] = None
        return r
    started = None
    if op != "open" and tool not in _running():
        d = ctx.get("design") or _gui_design("magic" if tool == "klayout" else "klayout")
        if not d:
            return {"ok": False, "error": "no %s window is open and no design was named; say e.g. \"open kv_attn_n8 in %s\" first" % (tool, tool)}
        started = gui_start(GuiStartReq(tool=tool, design=d))
        if not started.get("ok"):
            return started
        ctx["design"] = d
        _VIS[tool] = None
    if op == "open":
        d = act.get("design") or ctx.get("design") or _gui_design(tool) or _gui_design("magic" if tool == "klayout" else "klayout")
        if not d:
            return {"ok": False, "error": "which design? say e.g. \"open kv_attn_n8 in %s\"" % tool}
        r = gui_start(GuiStartReq(tool=tool, design=d))
        ctx["design"] = d
        _VIS[tool] = None
        act["design"] = d
        if r.get("ok") and final:
            s = klayout_live(LiveReq(action="zoom", full=True)) if tool == "klayout" else magic_live(LiveReq(action="snapshot"))
            r = {**r, **{k: s[k] for k in ("png_url", "markdown", "view_bbox_um") if k in s}}
        return r
    d = ctx.get("design") or _gui_design(tool)
    req = None
    if op == "zoom":
        if "corner" in act:
            act["bbox"] = _corner_bbox(act["corner"], act["size"], _die_bbox(d))
        req = LiveReq(action="zoom", bbox=act.get("bbox"), cell=act.get("cell"), full=True if act.get("full") else None)
    elif op == "layers":
        if act.get("hide"):
            cur = list(_VIS[tool] or ALL_LAYERS)
            keep = [x for x in cur if x not in act["hide"]]
            if not keep:
                return {"ok": False, "error": "that would hide every layer"}
            act["layers"], act["only"] = keep, True
        elif not act.get("only") and not act.get("all"):
            act["layers"] = list(dict.fromkeys(list(_VIS[tool] or []) + act["layers"])) if _VIS[tool] else act["layers"]
            act["only"] = True
        req = LiveReq(action="layers", layers=act["layers"], only=True if act.get("only", True) else False)
        if tool == "klayout" and act.get("all"):
            req.only = True
    elif op == "drc":
        req = LiveReq(action="markers" if tool == "klayout" else "drc", design=d)
    elif op == "measure":
        req = LiveReq(action="measure", a=act["a"], b=act["b"])
    elif op == "find":
        req = LiveReq(action="find", label=act["label"])
    elif op == "snapshot":
        req = LiveReq(action="snapshot")
    if tool == "klayout":
        if op == "find":
            return {"ok": False, "error": "find works in Magic only; say \"find %s in magic\"" % act["label"]}
        r = klayout_live(req)
    elif final or op == "snapshot":
        r = magic_live(req)
    else:
        with _LOCK["magic"]:
            try:
                r = _magic_do(req, req.action, _mg())
            except Exception as e:  # noqa: BLE001
                r = {"ok": False, "error": str(e)}
    if r.get("ok") and op == "layers":
        _VIS[tool] = list(act["layers"])
    if started:
        r = dict(r, started_window=True)
    return r


@post("gui_command", "Operate KLayout or Magic with one plain-English sentence")
def gui_command(req: GuiCommandReq) -> dict:
    """Use for ANY request about the KLayout or Magic windows, layers, zoom, DRC on a layout, or opening a design in KLayout/Magic.
    Pass the user's sentence VERBATIM in text (do not rewrite it); pass tool only for a /klayout or /magic message. It understands:
    open <design> [in klayout|magic]; show [only] met1 and met2 (met1..met5, li1, poly, diff, all); hide met5; zoom to the
    lower-left|upper-right|center 50 um; zoom to x1 y1 x2 y2; zoom to the macro mprj; zoom out; run drc; measure from 0,0 to 100,0;
    find clk (Magic); snapshot; status; close klayout|magic|all. Chain with 'and' / 'then'. Starts the window when needed. Returns
    did, results, markdown (a picture of the last view: paste it verbatim), understood, and a hint with examples when not understood."""
    t0 = time.time()
    tool = (req.tool or "").lower().strip() or None
    if tool and tool not in TOOLS:
        return {"ok": False, "understood": False, "error": "tool must be klayout or magic"}
    try:
        running = _running()
    except Exception:  # noqa: BLE001
        running = []
    p = parse_text(req.text, tool, running, last_tool=_LAST["tool"])
    acts, unparsed = p["actions"], p["unparsed"]
    if not acts or unparsed:
        return {"ok": False, "understood": False, "did": [], "results": [], "unparsed": unparsed or [req.text], "hint": HINT,
                "markdown": "", "note": "nothing was run" + ("" if not acts else " (part of the sentence was not understood)")}
    ctx = {"design": _design_in((req.text or "").lower(), _designs_list())}
    final = None
    for i in range(len(acts) - 1, -1, -1):          # the last visual action whose window is not closed afterwards
        if acts[i]["op"] in VISUAL_OPS and not any(b["op"] == "close" and b["tool"] == acts[i]["tool"] for b in acts[i + 1:]):
            final = i
            break
    did, results, md, ok = [], [], "", True
    for i, a in enumerate(acts):
        t1 = time.time()
        try:
            r = _exec(a, ctx, i == final)
        except Exception as e:  # noqa: BLE001
            r = {"ok": False, "error": str(e) if isinstance(e, (RuntimeError, mb.MagicError)) else "%s: %s" % (type(e).__name__, e)}
        if r.get("ok") and a["op"] in VISUAL_OPS:
            _LAST["tool"] = a["tool"]
        did.append({k: v for k, v in a.items() if v is not None})
        results.append({"action": a["op"], "tool": a["tool"], "ok": bool(r.get("ok")), "seconds": round(time.time() - t1, 1),
                        **_slim(r)})
        if r.get("markdown"):
            md = r["markdown"]
        if not r.get("ok"):
            ok = False
            break
    return {"ok": ok, "understood": True, "did": did, "results": results, "markdown": md,
            "seconds": round(time.time() - t0, 1), **({} if ok else {"error": results[-1].get("error")})}


@post("gui_examples", "Example sentences for gui_command")
def gui_examples(req: Empty = Empty()) -> dict:
    """List the sentences gui_command understands, what each does and what you see. Show the table to the user."""
    rows = ["| what you type | what happens | what you see |", "|---|---|---|"]
    rows += ["| `%s` | %s | %s |" % e for e in EXAMPLES]
    return {"ok": True, "examples": [{"say": a, "does": b, "see": c} for a, b, c in EXAMPLES], "markdown": "\n".join(rows),
            "note": "Add 'in klayout' or 'in magic' to pick a tool; chain with 'and'. Or type /klayout <sentence> or /magic <sentence>."}
