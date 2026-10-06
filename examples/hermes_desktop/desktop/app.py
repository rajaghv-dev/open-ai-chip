"""Hermes Chip Agent desktop window: starts start.sh, shows Open WebUI in a native WKWebView (pywebview),
runs stop.sh when the window closes. Usage: app.py <repo_root> [--check-only] [--no-stop]
Docs: examples/hermes_desktop/README.md, docs/HERMES_DESKTOP.md"""
import os
import signal
import subprocess
import sys
import time
import urllib.request

URL = "http://127.0.0.1:8080"
PRESET_URL = URL + "/?models=hermes-chip-agent"   # opens straight into the preset
MODEL = "hermes3:8b"


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
    webview.create_window("Hermes Chip Agent", PRESET_URL, width=1280, height=860)
    try:
        webview.start()
    finally:
        if "--no-stop" not in sys.argv:
            subprocess.run(["bash", stop], stdout=log, stderr=subprocess.STDOUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
