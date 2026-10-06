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


def healthy(url=URL + "/health"):
    try:
        with urllib.request.urlopen(url, timeout=2) as r:
            return r.status == 200
    except Exception:
        return False


def main():
    repo = sys.argv[1]
    start = repo + "/examples/hermes_desktop/start.sh"
    stop = repo + "/examples/hermes_desktop/stop.sh"
    log = open(repo + "/build/webui/logs/desktop_app.log", "a")
    p = subprocess.run(["bash", start], stdout=log, stderr=subprocess.STDOUT)
    if p.returncode != 0 or not healthy():
        print("start.sh failed, see build/webui/logs", file=sys.stderr)
        return 1
    if "--check-only" in sys.argv:
        print("ok")
        return 0
    def _bye(*_):  # Dock "Quit" or kill: still stop what start.sh started
        if "--no-stop" not in sys.argv:
            subprocess.run(["bash", stop], stdout=log, stderr=subprocess.STDOUT)
        os._exit(0)
    signal.signal(signal.SIGTERM, _bye)
    signal.signal(signal.SIGINT, _bye)
    import webview
    webview.create_window("Hermes Chip Agent", URL, width=1280, height=860)
    try:
        webview.start()
    finally:
        if "--no-stop" not in sys.argv:
            subprocess.run(["bash", stop], stdout=log, stderr=subprocess.STDOUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
