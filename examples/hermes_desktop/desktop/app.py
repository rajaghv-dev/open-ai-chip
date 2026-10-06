"""Hermes Chip Agent desktop window: starts start.sh, shows Open WebUI in a native WKWebView (pywebview),
runs stop.sh when the window closes. Usage: app.py <repo_root> [--check-only] [--no-stop]
Proof: the window title and a small badge (bottom right of the page) show "Local - hermes3:8b - repo <sha> - offline", refreshed every
30 s from the tool server's proof_local; clicking the badge or the menu Proof > Show context opens a "Context" window with
show_context for the last turn (system prompt sha, tools prompt sha, memory digest, retrieved passages and files).
Layout tools: a second window "Layout tools" (the tool server's GET /gui panel: open KLayout/Magic, layers, zoom, DRC, measure, text command;
no model needed) opens next to the chat unless HERMES_LAYOUT_PANEL=0; menu Layout: Layout tools, Open in KLayout..., Open in Magic..., Close layout windows.
Docs: examples/hermes_desktop/README.md, docs/HERMES_DESKTOP.md (sections "Layout tools window", "Proof: local, repo, context")
Tests: tests/tools/test_proof.py"""
import html
import json
import os
import signal
import subprocess
import sys
import time
import urllib.request

URL = "http://127.0.0.1:8080"
PRESET_URL = URL + "/?models=hermes-chip-agent"   # opens straight into the preset
MODEL = "hermes3:8b"
TOOLS_URL = "http://127.0.0.1:8770"
REFRESH_S = 30


def tool_call(name, body=None, timeout=40):
    """POST a tool-server endpoint; the parsed JSON or None when it is down."""
    try:
        req = urllib.request.Request(TOOLS_URL + "/" + name, data=json.dumps(body or {}).encode(), headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except Exception:
        return None


def fetch_status():
    """(status_line, ok): from proof_local; a clear 'not verified' line when the tool server does not answer."""
    r = tool_call("proof_local")
    if not r or "status_line" not in r:
        return "NOT VERIFIED - tool server not answering", False
    return r["status_line"], r.get("verdict", "").startswith("LOCAL")


def badge_js(line, ok):
    """JavaScript that creates or updates the small status badge in the page (green when verified local, red otherwise)."""
    return ("(function(){var d=document.getElementById('chip-proof');if(!d){d=document.createElement('div');d.id='chip-proof';"
            "d.style.cssText='position:fixed;right:10px;bottom:8px;z-index:99999;font:11px -apple-system,sans-serif;padding:3px 9px;"
            "border-radius:10px;color:#fff;cursor:pointer;opacity:.9';d.title='Click: show the context of the last turn';"
            "d.onclick=function(){if(window.pywebview&&window.pywebview.api)window.pywebview.api.show_context();};document.body.appendChild(d);}"
            "d.textContent=%s;d.style.background=%s;})();" % (json.dumps(line), json.dumps("#1a7f37" if ok else "#b42318")))


def context_html(text):
    """The show_context text as a small self-contained HTML page for the Context window."""
    return ("<!doctype html><meta charset=utf-8><title>Context</title><body style=\"font:13px -apple-system,sans-serif;margin:14px\">"
            "<h3 style=\"margin:0 0 8px\">Context sent for the last turn</h3><pre style=\"white-space:pre-wrap;font:12px Menlo,monospace\">%s</pre>"
            "<p style=\"color:#666\">Source: tool server show_context (examples/hermes_desktop/tool_server/proof_tools.py). "
            "Verify by hand: shasum -a 256 tools/prompts/master_prompt.txt</p>" % html.escape(text))


def design_names():
    r = tool_call("list_designs") or {}
    L = r.get("designs") or r.get("result") or r
    names = [x if isinstance(x, str) else (x.get("design") or x.get("name")) for x in L] if isinstance(L, list) else list(L) if isinstance(L, dict) else []
    return sorted(n for n in names if isinstance(n, str)) or ["kv_attn_n8"]


def choose_design(tool):
    """Native list dialog (osascript) -> design name or None (cancel)."""
    names = design_names()
    items = ", ".join('"%s"' % n for n in names)
    r = subprocess.run(["osascript", "-e", 'choose from list {%s} with title "Open in %s" with prompt "Design:" default items {"kv_attn_n8"}' % (items, tool)],
                       capture_output=True, text=True)
    out = r.stdout.strip()
    return out if out and out != "false" else None


def healthy(url=URL + "/health"):
    try:
        with urllib.request.urlopen(url, timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def alert(msg):
    """Clear message to the user: native dialog (osascript) plus stderr (desktop_app.log)."""
    print("ERROR: " + msg, file=sys.stderr)
    subprocess.run(["osascript", "-e", 'display dialog "%s" with title "Hermes Chip Agent" buttons {"OK"} '
                    'default button "OK" with icon stop' % msg.replace('"', "'")], check=False)


def preflight():
    """Returns a problem text, or None. Ollama must be installed; hermes3:8b is checked after start.sh brought Ollama up."""
    os.environ["PATH"] = "/opt/homebrew/bin:/usr/local/bin:" + os.environ.get("PATH", "")
    if subprocess.run(["bash", "-c", "command -v ollama"], capture_output=True).returncode != 0:
        return "Ollama is not installed. Install it from https://ollama.com, then run: ollama pull " + MODEL
    return None


def model_missing():
    import json
    try:
        with urllib.request.urlopen("http://127.0.0.1:11434/api/tags", timeout=5) as r:
            tags = [m["name"] for m in json.load(r)["models"]]
    except Exception:
        return "Ollama is not answering on 127.0.0.1:11434. Start it (ollama serve) and reopen the app."
    if MODEL not in tags:
        return "The model %s is not installed. Open Terminal and run: ollama pull %s" % (MODEL, MODEL)
    return None


def main():
    repo = sys.argv[1]
    start = repo + "/examples/hermes_desktop/start.sh"
    stop = repo + "/examples/hermes_desktop/stop.sh"
    log = open(repo + "/build/webui/logs/desktop_app.log", "a")
    bad = preflight()
    if bad:
        alert(bad)
        return 1
    p = subprocess.run(["bash", start], stdout=log, stderr=subprocess.STDOUT)
    if p.returncode != 0 or not healthy():
        alert("Start failed. See build/webui/logs (desktop_app.log, webui.log) in the open-ai-chip folder.")
        return 1
    bad = model_missing()
    if bad:
        alert(bad)
        subprocess.run(["bash", stop], stdout=log, stderr=subprocess.STDOUT)
        return 1
    if "--check-only" in sys.argv:
        print("ok")
        return 0
    def _bye(*_):  # Dock "Quit" or kill: still stop what start.sh started
        if "--no-stop" not in sys.argv:
            subprocess.run(["bash", stop], stdout=log, stderr=subprocess.STDOUT)
        os._exit(0)
    # The Cocoa run loop never returns to Python signal handlers, so a thread waits for the signals instead.
    import threading
    sigs = {signal.SIGTERM, signal.SIGINT}
    signal.pthread_sigmask(signal.SIG_BLOCK, sigs)   # blocked in main and in the threads created below

    def _wait():
        signal.sigwait(sigs)
        _bye()
    threading.Thread(target=_wait, daemon=True).start()
    import webview
    import webview.menu as wm

    class Api:
        def show_context(self):
            open_context()

    box = {}

    def open_context(*_):
        r = tool_call("show_context", timeout=30)
        page = context_html(r["text"] if r else "The tool server did not answer.")
        w = box.get("ctx")
        try:
            if w is not None:
                w.load_html(page)
                w.show()
                return
        except Exception:
            pass
        box["ctx"] = webview.create_window("Context", html=page, width=780, height=640)

    win = webview.create_window("Hermes Chip Agent", PRESET_URL, width=1280, height=860, js_api=Api())

    def open_layout(*_):
        """The Layout tools window (one instance; shown again if it was closed)."""
        w = box.get("layout")
        try:
            if w is not None:
                w.show()
                return
        except Exception:
            box.pop("layout", None)
        box["layout"] = webview.create_window("Layout tools", TOOLS_URL + "/gui", width=600, height=900, x=60, y=60)

    def open_in(tool):
        def go(*_):
            d = choose_design(tool.capitalize() if tool == "magic" else "KLayout")
            if d:
                open_layout()
                threading.Thread(target=lambda: tool_call("gui_start", {"tool": tool, "design": d}, timeout=180), daemon=True).start()
        return go

    def close_layout_windows(*_):
        for t in ("magic", "klayout"):
            tool_call("gui_stop", {"tool": t}, timeout=60)

    def chat_closed():                      # closing the chat window ends the app (and runs stop.sh) even if the panel is open
        for w in list(webview.windows):
            try:
                w.destroy()
            except Exception:
                pass

    win.events.closed += chat_closed
    if os.environ.get("HERMES_LAYOUT_PANEL", "1") != "0" and tool_call("gui_status"):
        open_layout()

    def refresh():
        """Title and badge: re-measured every REFRESH_S seconds from proof_local (best effort, never raises)."""
        time.sleep(4)
        while True:
            try:
                line, ok = fetch_status()
                win.set_title("Hermes Chip Agent - " + line)
                win.evaluate_js(badge_js(line, ok))
            except Exception:
                pass
            time.sleep(REFRESH_S)

    def refresh_now(*_):
        line, ok = fetch_status()
        win.set_title("Hermes Chip Agent - " + line)
        win.evaluate_js(badge_js(line, ok))

    menu = [wm.Menu("Proof", [wm.MenuAction("Show context of the last turn", open_context),
                              wm.MenuAction("Refresh local status", refresh_now)]),
            wm.Menu("Layout", [wm.MenuAction("Layout tools", open_layout),
                               wm.MenuAction("Open in KLayout...", open_in("klayout")),
                               wm.MenuAction("Open in Magic...", open_in("magic")),
                               wm.MenuAction("Close layout windows", close_layout_windows)])]
    try:
        webview.start(refresh, menu=menu)
    finally:
        if "--no-stop" not in sys.argv:
            subprocess.run(["bash", stop], stdout=log, stderr=subprocess.STDOUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
