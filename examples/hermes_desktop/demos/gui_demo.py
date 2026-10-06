#!/usr/bin/env python3
"""Demo 7 (gui): a slow, narrated tour of the REAL KLayout and Magic windows, driven through the GUI tools of the tool server.

Each step prints a numbered narration line BEFORE it acts, acts (gui_start / klayout_live / magic_live / gui_stop), saves
the snapshot to build/agent/gui_demo/NN_<name>.png, prints its path and the seconds the act took, then pauses --pace seconds
so a presenter can talk. Read-only: nothing is saved or written to any layout.
  python3 examples/hermes_desktop/demos/gui_demo.py [--pace 6] [--server URL] [--chat] [--text] [--only N[,N]]
  --server URL  tool server to use (default http://127.0.0.1:8770 if it serves klayout_live, else a private one on 8783)
  --text        the same tour as plain-English SENTENCES sent to the gui_command tool (the text interface, no model: a regex parser);
                each step prints the sentence, what it did and the seconds; snapshots go to build/agent/gui_demo/NN_text.png
  --chat        the same steps as ONE Open WebUI conversation with the "Hermes chip agent" preset (narration + inline
                snapshots appear in the chat; needs Open WebUI on 8080 and Ollama); saved as a chat, as demo.py does
Needs: XQuartz running with `DISPLAY=:0 /opt/X11/bin/xhost +localhost` done once (see scripts/gui/open_gui.sh), the KLayout app,
the Colima docker (Magic), and the committed/collected GDS of kv_attn_n8 and user_project_wrapper_soc_kv.
Exit: 0 all steps ok, 1 a step failed (windows are still closed at the end), 2 no tool server.
Docs: examples/hermes_desktop/demos/GUI_DEMO.md, docs/HERMES_DESKTOP.md
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
OUT = os.path.join(REPO, "build", "agent", "gui_demo")
IMG = os.path.join(REPO, "build", "agent", "klayout_gui")
SMALL = "kv_attn_n8"
WRAP = "user_project_wrapper_soc_kv"
ROW_H = 2.72           # sky130_fd_sc_hd site height in um (SITE unithd, SIZE 0.46 BY 2.72 in sky130_fd_sc_hd__nom.tlef)
ALL_LAYERS = ["diff", "poly", "li1", "met1", "met2", "met3", "met4", "met5", "mcon", "via", "via2", "via3"]


def macro_geometry():
    """(x, y, width, height) of mprj in the wrapper: location from config.json, SIZE from the macro LEF; None if missing."""
    try:
        c = json.load(open(os.path.join(REPO, "designs", WRAP, "config.json")))
        (name, m), = c["MACROS"].items()
        x, y = m["instances"]["mprj"]["location"]
        lef = os.path.join(REPO, "build", "macros", name, "lef", name + ".lef")
        mo = re.search(r"SIZE\s+([\d.]+)\s+BY\s+([\d.]+)", open(lef).read())
        return x, y, float(mo.group(1)), float(mo.group(2))
    except (OSError, ValueError, KeyError, AttributeError):
        return None


def steps():
    g = macro_geometry()
    mw = g[2] if g else 0.0
    ms = [{"name": "klayout_measure_macro",
           "say": ("Step 9: measure the macro width with the ruler tool: from the macro's left edge to its right edge at "
                   "mid-height (%.2f um, from its LEF); the tool reports dx, dy and the distance" % mw),
           "tool": "klayout_live", "args": {"action": "measure", "a": [g[0], g[1] + g[3] / 2], "b": [g[0] + g[2], g[1] + g[3] / 2]},
           "prompt": "Use klayout_live measure from (%.2f, %.2f) to (%.2f, %.2f) and tell me the width in um."
                     % (g[0], g[1] + g[3] / 2, g[0] + g[2], g[1] + g[3] / 2) if g else "Use klayout_live state."}]
    return [
        {"name": "klayout_open", "say": "Step 1: open KLayout on %s, the whole chip: a real desktop window appears on your screen; "
                                        "everything you see is one tiny AI engine seen from above" % SMALL,
         "tool": "gui_start", "args": {"tool": "klayout", "design": SMALL}, "snap": ("klayout_live", {"action": "zoom", "full": True}),
         "prompt": "Use gui_start to open KLayout on %s, then klayout_live zoom full." % SMALL},
        {"name": "klayout_corner", "say": "Step 2: zoom to the lower-left 50 x 50 micrometres: now you can see the standard-cell "
                                          "rows, the repeated horizontal strips that every gate sits in",
         "tool": "klayout_live", "args": {"action": "zoom", "bbox": [0, 0, 50, 50]},
         "prompt": "Use klayout_live to zoom to the box x 0 to 50 and y 0 to 50 micrometres."},
        {"name": "klayout_li1_met1", "say": "Step 3: show only li1 and met1: the wiring inside the cells and the power rails "
                                            "along each row (the long horizontal lines, VDD and VSS in turn)",
         "tool": "klayout_live", "args": {"action": "layers", "layers": ["li1", "met1"]},
         "prompt": "Use klayout_live layers to show only li1 and met1."},
        {"name": "klayout_met2_met3", "say": "Step 4: show only met2 and met3: the signal routing between cells; met2 mostly "
                                             "vertical, met3 mostly horizontal, joined by vias",
         "tool": "klayout_live", "args": {"action": "layers", "layers": ["met2", "met3"]},
         "prompt": "Use klayout_live layers to show only met2 and met3."},
        {"name": "klayout_all", "say": "Step 5: show all layers again: transistors, poly, local interconnect and every metal "
                                       "stacked together, the real mask data of this corner",
         "tool": "klayout_live", "args": {"action": "layers", "layers": ALL_LAYERS},
         "prompt": "Use klayout_live layers to show all layers: " + ", ".join(ALL_LAYERS) + "."},
        {"name": "klayout_wrapper", "say": "Step 6: open %s, the Caravel user area with our engine placed inside it as the macro "
                                           "mprj" % WRAP,
         "tool": "klayout_live", "args": {"action": "open", "design": WRAP},
         "prompt": "Use klayout_live to open %s." % WRAP},
        {"name": "klayout_met4_met5", "say": "Step 7: show only met4 and met5, the two thickest top metals: this is the power "
                                              "grid, wide straps that carry current across the whole chip",
         "tool": "klayout_live", "args": {"action": "layers", "layers": ["met4", "met5"]},
         "prompt": "Use klayout_live layers to show only met4 and met5."},
        {"name": "klayout_mprj", "say": "Step 8: zoom to the macro mprj: our engine, a block placed as one piece; the straps "
                                         "pass over and connect to it",
         "tool": "klayout_live", "args": {"action": "zoom", "cell": "mprj"},
         "prompt": "Use klayout_live to zoom to the cell mprj."},
    ] + ms + [
        {"name": "magic_open", "say": "Step 10: open Magic, a second layout tool, on %s: a window opens through XQuartz from a "
                                      "container, and the agent talks to it over a small localhost-only bridge" % SMALL,
         "tool": "gui_start", "args": {"tool": "magic", "design": SMALL}, "snap": ("magic_live", {"action": "snapshot"}),
         "prompt": "Use gui_start to open Magic on %s, then magic_live snapshot." % SMALL},
        {"name": "magic_corner", "say": "Step 11: zoom Magic to the same lower-left 50 x 50 micrometres corner",
         "tool": "magic_live", "args": {"action": "zoom", "bbox": [0, 0, 50, 50]},
         "prompt": "Use magic_live to zoom to the box x 0 to 50 and y 0 to 50 micrometres."},
        {"name": "magic_layers", "say": "Step 12: show only met1 and met2 (Magic calls them m1 and m2): rails and routing, as in KLayout",
         "tool": "magic_live", "args": {"action": "layers", "layers": ["met1", "met2"]},
         "prompt": "Use magic_live layers to show only met1 and met2."},
        {"name": "magic_drc", "say": "Step 13: run Magic's design rule check: it compares every shape with the foundry minimum "
                                     "width, spacing and enclosure rules; a clean design reports 0 errors",
         "tool": "magic_live", "args": {"action": "drc"},
         "prompt": "Use magic_live drc and tell me how many DRC errors there are."},
        {"name": "magic_row", "say": "Step 14: measure one standard-cell row height, between two neighbouring power rails: "
                                      "%.2f um, the height of every sky130_fd_sc_hd cell" % ROW_H,
         "tool": "magic_live", "args": {"action": "measure", "a": [10, 10.88], "b": [10, round(10.88 + ROW_H, 2)]},
         "prompt": "Use magic_live measure from (10, 10.88) to (10, 13.6) and tell me the distance."},
        {"name": "stop_magic", "say": "Step 15: close the Magic window (quit, then remove its container)",
         "tool": "gui_stop", "args": {"tool": "magic"}, "prompt": "Use gui_stop for magic."},
        {"name": "stop_klayout", "say": "Step 16: close the KLayout window", "tool": "gui_stop", "args": {"tool": "klayout"},
         "prompt": "Use gui_stop for klayout."},
    ]


def text_steps():
    """(narration, sentence) pairs for --text: the whole tour through gui_command."""
    g = macro_geometry()
    ms = ("measure from %.2f,%.2f to %.2f,%.2f" % (g[0], g[1] + g[3] / 2, g[0] + g[2], g[1] + g[3] / 2)) if g else "snapshot"
    return [
        ("Step 1: open the whole engine in KLayout", "open %s in klayout" % SMALL),
        ("Step 2: the lower-left corner: standard-cell rows", "zoom to the lower-left 50 um"),
        ("Step 3: only the cell wiring and power rails", "show only li1 and met1"),
        ("Step 4: only the signal routing", "show only met2 and met3"),
        ("Step 5: hide met3, then all layers again", "hide met3"),
        ("Step 6: everything stacked", "show all"),
        ("Step 7: the Caravel user area, then its power grid", "open %s in klayout and show only met4 and met5" % WRAP),
        ("Step 8: our engine, the macro mprj", "zoom to the macro mprj"),
        ("Step 9: the ruler across the macro", ms),
        ("Step 10: the same engine in Magic, then its DRC", "open %s in magic and run drc" % SMALL),
        ("Step 11: Magic: lower-left corner, rails and routing", "zoom to the lower-left 50 um and show only met1 and met2"),
        ("Step 12: one standard-cell row height", "measure from 10,10.88 to 10,13.6"),
        ("Step 13: which windows are open", "status"),
        ("Step 14: close everything", "close all"),
    ]


def run_text(server, a, only):
    results, ok_all = [], True
    steps_ = text_steps()
    for n, (say, sentence) in enumerate(steps_, 1):
        if only and n not in only:
            continue
        print("\n" + say + "\n   you type: " + sentence, flush=True)
        t0 = time.time()
        r = post(server, "gui_command", {"text": sentence})
        dt = time.time() - t0
        mu = re.search(r"\((http[^)]*)\)", r.get("markdown") or "")
        path = keep_png({"png_url": mu.group(1)} if mu else None, n, "text")
        ok = bool(r.get("ok")) and r.get("understood")
        ok_all &= bool(ok)
        did = ", ".join("%s%s" % (d["op"], ("(%s)" % d.get("design") if d["op"] == "open" else "")) for d in r.get("did", []))
        res = "; ".join("%s=%s" % (x["action"], summary(x) or ("ok" if x["ok"] else "FAILED")) for x in r.get("results", []))
        print("   %s  %.1f s  did: %s\n   results: %s" % ("ok" if ok else "FAILED", dt, did or "-", res[:400]))
        if path:
            print("   snapshot: " + path)
        results.append({"step": n, "sentence": sentence, "ok": bool(ok), "seconds": round(dt, 2), "did": r.get("did"), "snapshot": path,
                        "error": r.get("error")})
        if not ok:
            print("   stopping the tour here; closing the windows")
            break
        if n < len(steps_) and a.pace:
            print("   (pause %.0f s: talk now)" % a.pace, flush=True)
            time.sleep(a.pace)
    return results, ok_all


def post(server, tool, args, timeout=300):
    req = urllib.request.Request("%s/%s" % (server, tool), data=json.dumps(args).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def serves(server):
    try:
        with urllib.request.urlopen(server + "/openapi.json", timeout=5) as r:
            spec = json.load(r)
        return "klayout_live" in {op.get("operationId") for p in spec["paths"].values() for op in p.values() if isinstance(op, dict)}
    except (OSError, ValueError):
        return False


def keep_png(r, n, name):
    """Copy the snapshot a reply points to into build/agent/gui_demo/NN_name.png; returns the repo-relative path or None."""
    url = (r or {}).get("png_url")
    if not url:
        return None
    src = os.path.join(IMG, os.path.basename(url))
    if not os.path.isfile(src):
        return None
    os.makedirs(OUT, exist_ok=True)
    dst = os.path.join(OUT, "%02d_%s.png" % (n, name))
    shutil.copyfile(src, dst)
    return os.path.relpath(dst, REPO)


def summary(r):
    keys = ("pid", "port", "ready", "design", "view_bbox_um", "visible", "drc_errors", "reasons", "dx_um", "dy_um",
            "distance_um", "snapshot_source", "stopped", "via", "container", "already_running", "error")
    return ", ".join("%s=%s" % (k, r[k]) for k in keys if k in r)


def run_direct(server, a, only):
    results, ok_all = [], True
    for n, s in enumerate(steps(), 1):
        if only and n not in only:
            continue
        print("\n" + s["say"], flush=True)
        t0 = time.time()
        r = post(server, s["tool"], s["args"])
        snap = None
        if r.get("ok") and s.get("snap"):          # a start step also takes its first picture
            snap = post(server, *s["snap"])
            if snap.get("ok"):
                r = {**r, "png_url": snap.get("png_url")}
        dt = time.time() - t0
        path = keep_png(r, n, s["name"])
        ok = bool(r.get("ok"))
        ok_all &= ok
        print("   %s  tool=%s  %.1f s  %s" % ("ok" if ok else "FAILED", s["tool"], dt, summary(r)))
        if path:
            print("   snapshot: " + path)
        results.append({"step": n, "name": s["name"], "tool": s["tool"], "args": s["args"], "ok": ok, "seconds": round(dt, 2),
                        "snapshot": path, "reply": {k: v for k, v in r.items() if k not in ("markdown",)}})
        if not ok and s["tool"] not in ("gui_stop",):
            print("   stopping the tour here; closing the windows")
            break
        if n < len(steps()) and a.pace:
            print("   (pause %.0f s: talk now)" % a.pace, flush=True)
            time.sleep(a.pace)
    return results, ok_all


def run_chat(server, a, only):
    sys.path.insert(0, os.path.join(REPO, "examples", "hermes_desktop"))
    import demo as D                                         # the showcase helpers: api, ask, save_chat
    try:
        token = D.api("/api/v1/auths/signin", body={"email": "admin@localhost", "password": "x"})["token"]
    except OSError as e:
        print("Open WebUI is not reachable (%s): run scripts/hermes.sh first, or run without --chat" % e, file=sys.stderr)
        return [], False
    turns, results, ok_all = [], [], True
    for n, s in enumerate(steps(), 1):
        if only and n not in only:
            continue
        print("\n" + s["say"], flush=True)
        t0, pos = time.time(), D.log_size()
        text = D.ask(token, s["prompt"], 600)
        dt = time.time() - t0
        tools = list(dict.fromkeys(D.tools_since(pos) + D.used_tools(text)))     # what the tool server really received
        want = [s["tool"]] + ([s["snap"][0]] if s.get("snap") else [])
        ok = any(t in tools for t in want) or "/img/live_" in text
        ok_all &= ok
        print("   %s  %.1f s  tools=%s" % ("ok" if ok else "tool not seen", dt, tools))
        print("   " + text.strip().replace("\n", "\n   ")[:600])
        turns.append((s["say"] + "\n\n" + s["prompt"], text))
        results.append({"step": n, "name": s["name"], "seconds": round(dt, 1), "tools": tools, "ok": ok})
        if a.pace:
            time.sleep(a.pace)
    if turns:
        print("\nsaved as Open WebUI chat id %s" % D.save_chat(token, turns))
    return results, ok_all


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pace", type=float, default=6.0, help="seconds to pause after each step (default 6)")
    ap.add_argument("--server", default=os.environ.get("TOOLS_URL", "http://127.0.0.1:8770"))
    ap.add_argument("--text", action="store_true", help="drive everything through gui_command sentences (no model)")
    ap.add_argument("--chat", action="store_true", help="run the steps as one Open WebUI conversation")
    ap.add_argument("--only", default="", help="comma list of step numbers")
    a = ap.parse_args()
    only = {int(x) for x in a.only.split(",") if x.strip().isdigit()}
    server, private = a.server.rstrip("/"), None
    if not serves(server):
        py = os.path.join(REPO, "build", "agent", "venv", "bin", "python")
        port = 8783
        server = "http://127.0.0.1:%d" % port
        private = subprocess.Popen([py if os.path.exists(py) else sys.executable, os.path.join(REPO, "examples", "hermes_desktop", "tool_server", "tool_server.py")],
                                   env=dict(os.environ, CHIP_TOOLS_PORT=str(port)), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(60):
            if serves(server):
                break
            time.sleep(0.5)
        else:
            private.kill()
            print("no tool server with the GUI tools (start scripts/hermes.sh)", file=sys.stderr)
            return 2
        print("started a private tool server on %s (pid %d)" % (server, private.pid))
    t0 = time.time()
    try:
        print("GUI demo: %s mode, %.0f s pause per step, tool server %s" % ("chat" if a.chat else "text" if a.text else "direct", a.pace, server))
        results, ok = (run_chat if a.chat else run_text if a.text else run_direct)(server, a, only)
    except urllib.error.URLError as e:
        print("tool server error: %s" % e, file=sys.stderr)
        results, ok = [], False
    finally:
        for t in ("magic", "klayout"):           # never leave a window behind (gui_stop only touches what gui_start opened)
            try:
                post(server, "gui_stop", {"tool": t}, timeout=60)
            except Exception:  # noqa: BLE001
                pass
        if private:
            private.terminate()
    total = time.time() - t0
    os.makedirs(OUT, exist_ok=True)
    with open(os.path.join(OUT, "run%s.json" % ("_chat" if a.chat else "_text" if a.text else "")), "w") as f:
        json.dump({"pace_s": a.pace, "total_s": round(total, 1), "ok": ok, "steps": results}, f, indent=1)
    print("\nTotal %.0f s (pace %.0f s), %s. Timings: %s" % (total, a.pace, "all steps ok" if ok else "SOME STEP FAILED",
                                                            os.path.relpath(os.path.join(OUT, "run%s.json" % ("_chat" if a.chat else "_text" if a.text else "")), REPO)))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
