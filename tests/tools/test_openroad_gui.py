"""pytest tests/tools/test_openroad_gui.py  (examples/openroad_gui)

Always on: scripts exist and parse, README paths exist, committed images are small PNGs.
Opt-in: OPENROAD_GUI=1 renders one view with the LibreLane image (needs docker and a finished kv_attn_n8 run).
"""
import glob
import os
import re
import struct
import subprocess

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
EX = os.path.join(REPO, "examples", "openroad_gui")
SH = os.path.join(EX, "render_views.sh")
TCL = os.path.join(EX, "views.tcl")
README = os.path.join(EX, "README.md")


def test_files_exist():
    for p in (SH, TCL, README):
        assert os.path.isfile(p), p
    assert os.access(SH, os.X_OK)


def test_shell_parses():
    r = subprocess.run(["bash", "-n", SH], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


def test_tcl_braces_balanced_and_key_commands():
    s = open(TCL).read()
    body = re.sub(r"#.*", "", s)
    body = re.sub(r'"[^"\n]*"', '""', body)
    assert body.count("{") == body.count("}")
    for needle in ("save_image -area", "gui::set_heatmap", "analyze_power_grid", "save_clocktree_image",
                   "find_timing_paths", "exit"):
        assert needle in s, needle
    assert "try" not in re.findall(r"^proc (\w+)", s, re.M)  # Tcl 8.6 builtin


def test_script_is_read_only_and_bounded():
    s = open(SH).read()
    assert "QT_QPA_PLATFORM=offscreen" in s and "timeout" in s
    assert "/Users/" not in s and "/Users/" not in open(TCL).read()
    assert "librelane" not in s.replace("ghcr.io/librelane/librelane", "")


def test_readme_paths_exist_and_ascii():
    t = open(README, encoding="utf-8").read()
    t.encode("ascii")
    for m in re.findall(r"\]\(([^)#]+)(?:#[^)]*)?\)", t):
        if m.startswith("http"):
            continue
        assert os.path.exists(os.path.normpath(os.path.join(EX, m))), m
    for m in re.findall(r"`((?:examples|tests|docs|scripts)/[\w./-]+)`", t):
        if "<" in m or "*" in m:
            continue
        assert os.path.exists(os.path.join(REPO, m)), m
    assert "/Users/" not in t


def test_committed_images_small_pngs():
    imgs = glob.glob(os.path.join(EX, "img", "*.png"))
    assert len(imgs) >= 4
    for p in imgs:
        assert os.path.getsize(p) < 300_000, p
        with open(p, "rb") as f:
            h = f.read(24)
        assert h[:8] == b"\x89PNG\r\n\x1a\n"
        w, hh = struct.unpack(">II", h[16:24])
        assert w >= 300 and hh >= 300


def test_engines_page_links_example():
    assert "examples/openroad_gui/README.md" in open(os.path.join(REPO, "docs", "OPENROAD_ENGINES.md")).read()


def _docker_ok():
    sock = os.path.expanduser("~/.colima/osl/docker.sock")
    env = dict(os.environ)
    if "DOCKER_HOST" not in env and os.path.exists(sock):
        env["DOCKER_HOST"] = "unix://" + sock
    try:
        return subprocess.run(["docker", "ps"], capture_output=True, env=env, timeout=20).returncode == 0, env
    except Exception:
        return False, env


@pytest.mark.skipif(os.environ.get("OPENROAD_GUI") != "1", reason="set OPENROAD_GUI=1 to render with docker")
def test_render_one_view():
    ok, env = _docker_ok()
    if not ok:
        pytest.skip("docker not reachable")
    runs = [r for r in glob.glob(os.path.join(REPO, "designs", "kv_attn_n8", "runs", "RUN_*"))
            if os.path.isfile(os.path.join(r, "final", "odb", "kv_attn_n8.odb"))]
    if not runs:
        pytest.skip("no finished kv_attn_n8 run")
    r = subprocess.run(["bash", SH, "kv_attn_n8"], capture_output=True, text=True, env=env, timeout=400)
    png = os.path.join(REPO, "build", "agent", "openroad_gui", "kv_attn_n8", "02_placement_density.png")
    assert os.path.isfile(png), r.stdout[-2000:] + r.stderr[-500:]
    assert os.path.getsize(png) > 5000
    with open(png, "rb") as f:
        assert f.read(8) == b"\x89PNG\r\n\x1a\n"
