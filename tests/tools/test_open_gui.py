"""Tests for scripts/gui/open_gui.sh (OpenROAD GUI / Magic from the LibreLane container on XQuartz or X11).
Always on: shell syntax, usage errors, unknown tool. Opt-in (OPEN_GUI_LIVE=1, XQuartz listening, Docker up): opens
Magic for a few seconds and OpenROAD (killed after it reports the loaded block) and checks the LOADED lines."""
import os
import shutil
import subprocess

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SCRIPT = os.path.join(REPO, "scripts", "gui", "open_gui.sh")


def run(args, **kw):
    return subprocess.run(["bash", SCRIPT] + args, cwd=REPO, capture_output=True, text=True, stdin=subprocess.DEVNULL, **kw)


def test_script_syntax():
    assert subprocess.run(["bash", "-n", SCRIPT]).returncode == 0


def test_usage_errors():
    r = run([])
    assert r.returncode != 0 and "usage" in r.stderr
    r = run(["openroad"])
    assert r.returncode != 0 and "usage" in r.stderr


def test_unknown_design_refused():
    r = run(["magic", "no_such_design_xyz"])
    assert r.returncode != 0


def test_ascii_and_no_home_paths():
    s = open(SCRIPT).read()
    assert s.isascii() and "/Users/" not in s


def _xquartz_listening():
    if shutil.which("lsof") is None:
        return False
    return subprocess.run(["lsof", "-nP", "-iTCP:6000", "-sTCP:LISTEN"], capture_output=True).returncode == 0


live = pytest.mark.skipif(os.environ.get("OPEN_GUI_LIVE") != "1" or not _xquartz_listening(),
                          reason="set OPEN_GUI_LIVE=1 with XQuartz listening on TCP 6000 to open real windows")


@live
def test_magic_window_opens():
    r = run(["magic", "kv_attn_n8"], env=dict(os.environ, GUI_SECONDS="8"), timeout=180)
    assert "MAGIC_GUI_LOADED kv_attn_n8" in r.stdout + r.stderr


@live
def test_openroad_window_opens():
    p = subprocess.Popen(["bash", SCRIPT, "openroad", "kv_attn_n8"], cwd=REPO, stdin=subprocess.DEVNULL,
                         stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    seen = False
    try:
        for line in p.stdout:
            if "OPENROAD_GUI_LOADED kv_attn_n8" in line:
                seen = True
                break
    finally:
        # the GUI stays open until closed: stop the container (DOCKER_HOST as the script sets it on macOS)
        env = dict(os.environ)
        env.setdefault("DOCKER_HOST", "unix://" + os.path.expanduser("~/.colima/osl/docker.sock"))
        ids = subprocess.run(["docker", "ps", "-q", "--filter", "ancestor=ghcr.io/librelane/librelane:3.0.2"],
                             capture_output=True, text=True, env=env).stdout.split()
        for cid in ids:
            info = subprocess.run(["docker", "inspect", "-f", "{{.Config.Cmd}}", cid], capture_output=True, text=True, env=env).stdout
            if "openroad_open.tcl" in info:
                subprocess.run(["docker", "kill", cid], capture_output=True, env=env)
        p.kill()
    assert seen


def test_live_heatmaps_script():
    t = open(os.path.join(REPO, "examples", "openroad_gui", "live_heatmaps.tcl")).read()
    assert t.count("{") == t.count("}") and t.count("[") == t.count("]")
    assert "gui::pause" in t and "after " not in t.replace("after the", "")   # after timers never fire in the OpenROAD GUI
    for ctrl in ("Placement Density", "Routing Congestion", "Power Density", "IR Drop"):
        assert ctrl in t
    assert "heatmaps)" in open(SCRIPT).read()
