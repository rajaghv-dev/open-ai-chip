"""Tests for the GUI tools (examples/hermes_desktop/tool_server/gui_tools.py and examples/hermes_desktop/magic_bridge).
Offline (default): allow-list refusals, protocol encode/decode, layer map, Docker command, no save command possible,
the Tcl bridge handler in tclsh (stubbed Magic, skipped if tclsh is missing), router mounted, tool refusals.
Opt-in live: GUI_TOOLS_LIVE=1 opens the real KLayout and Magic windows (needs XQuartz, Colima, the KLayout app).

Run: build/agent/venv/bin/python -m pytest -q tests/tools/test_gui_tools.py
Pass: every test passes or is skipped (the live test needs GUI_TOOLS_LIVE=1).
Docs: examples/hermes_desktop/magic_bridge/README.md, docs/HERMES_DESKTOP.md
"""
import os
import re
import shutil
import subprocess
import sys

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
TS = os.path.join(REPO, "examples", "hermes_desktop", "tool_server")
MB = os.path.join(REPO, "examples", "hermes_desktop", "magic_bridge")
sys.path.insert(0, TS)
pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

import gui_tools as gt  # noqa: E402

mb = gt.mb
client = TestClient(__import__("fastapi").FastAPI())
client.app.include_router(gt.router)


def test_router_operations():
    ops = {r.operation_id for r in gt.router.routes}
    assert ops == {"gui_start", "gui_stop", "gui_status", "klayout_live", "magic_live"}


def test_mounted_in_tool_server():
    import tool_server as ts
    assert "gui_tools.py" in ts.EXTENSIONS
    spec = TestClient(ts.app).get("/openapi.json").json()
    ops = {v["post"]["operationId"] for v in spec["paths"].values() if "post" in v}
    assert {"gui_start", "gui_stop", "gui_status", "klayout_live", "magic_live"} <= ops


# ------------------------------------------------------------------ protocol
def test_encode_request_allow_list():
    assert mb.encode_request("view", 0, 0, 50.0, 50) == b"VIEW 0 0 50.0 50\n"
    assert mb.encode_request("SEE", "m1,m2", token="t0k") == b"t0k SEE m1,m2\n"
    for bad in ("save", "writeall", "gds", "exec", "source", "cif", "flush", "extract", "shell", "quit; save"):
        with pytest.raises(mb.MagicError):
            mb.encode_request(bad)


def test_encode_rejects_unsafe_arguments():
    for arg in ("a;b", "a b", "$x", "{x}", 'a"b', "a\nsave", "", "x" * 300, "`id`"):
        with pytest.raises(mb.MagicError):
            mb.encode_request("FIND", arg)
    assert mb.encode_request("FIND", "wbs_dat_i[0]") == b"FIND wbs_dat_i[0]\n"
    with pytest.raises(mb.MagicError):
        mb.encode_request("VIEW", True)


def test_no_save_command_in_allow_list():
    assert not any(re.search(r"save|write|flush|extract|exec|shell", c, re.I) for c in mb.ALLOWED)
    tcl = open(os.path.join(MB, "bridge.tcl")).read()
    code = "\n".join(l for l in tcl.splitlines() if not l.lstrip().startswith("#"))
    for forbidden in ("gds write", "writeall", "cif write", "save ", "exec ", "open |", "eval ", "uplevel", "subst "):
        assert forbidden not in code, forbidden
    allowed = re.search(r"set ::bridge_allowed \{(.*?)\}", tcl).group(1).split()
    assert allowed == list(mb.ALLOWED)


def test_decode_reply():
    assert mb.decode_reply(b"OK a=1 b=2\n") == (True, "a=1 b=2")
    assert mb.decode_reply("ERR bad token") == (False, "bad token")
    assert mb.decode_reply("OK line1\\nline2") == (True, "line1\nline2")
    with pytest.raises(mb.MagicError):
        mb.decode_reply("hello")
    assert mb.parse_kv("loaded top=kv bbox_um=[0.0, 10.64] x=3") ["top"] == "kv"
    assert mb.nums("[0.0, 10.64, 260.0, -3]") == [0.0, 10.64, 260.0, -3.0]


def test_layer_map():
    assert mb.magic_layers(["met1", "met2"]) == "m1,m2"
    assert mb.magic_layers("li1 and met1") == "li,m1"
    assert mb.magic_layers(["diff"]) == "ndiff,pdiff"
    with pytest.raises(mb.MagicError):
        mb.magic_layers(["met9"])
    with pytest.raises(mb.MagicError):
        mb.magic_layers([])


def test_docker_command_localhost_only():
    cmd = mb.docker_command(8766, "tok")
    i = cmd.index("-p")
    assert cmd[i + 1] == "127.0.0.1:8766:8766"
    assert "magic" in cmd and "-noconsole" in cmd and "-T" in cmd
    assert any(c.startswith("DISPLAY=") for c in cmd)
    assert "0.0.0.0" not in " ".join(cmd)


@pytest.mark.skipif(not shutil.which("tclsh"), reason="no tclsh")
def test_tcl_handler_refuses():
    """Run bridge_handle in plain tclsh: allow-list, token, character check, no eval of the request text."""
    src = open(os.path.join(MB, "bridge.tcl")).read().split("socket -server")[0]
    script = src + '''
set ::bridge_token tok
foreach l {"PING" "tok PING" "tok SAVE x" "tok gds write x.gds" "tok FIND a;b" "tok FIND $x" "tok FIND {a}" "tok LOAD /etc/passwd kv" "tok PLOT /tmp/x.pnm"} {
  puts "[bridge_handle $l]"
}
'''
    out = subprocess.run(["tclsh"], input=script, capture_output=True, text=True, timeout=20).stdout.splitlines()
    assert out[0] == "ERR bad token"
    assert out[1] == "OK pong bridge=1"
    assert out[2].startswith("ERR command not allowed: SAVE")
    assert out[3].startswith("ERR command not allowed: GDS")
    assert out[4].startswith("ERR bad character") and out[5].startswith("ERR bad character") and out[6].startswith("ERR bad character")
    assert out[7].startswith("ERR") and "under" in out[7] or "bad path" in out[7]
    assert out[8].startswith("ERR")


def test_xwd_and_pnm_to_png(tmp_path):
    import struct
    w, h = 3, 2
    hdr = struct.pack(">25I", 100, 7, 2, 24, w, h, 0, 0, 32, 0, 32, 32, w * 4, 4, 0xFF0000, 0xFF00, 0xFF, 8, 256, 0, w, h, 0, 0, 0)
    px = b"".join(struct.pack("<I", 0xFF0000 if (x + y) % 2 else 0x0000FF) for y in range(h) for x in range(w))
    png, ww, hh = mb.xwd_to_png(hdr + px)
    assert png[:8] == b"\x89PNG\r\n\x1a\n" and (ww, hh) == (3, 2)
    p = tmp_path / "a.pnm"
    p.write_bytes(b"P6\n2 2\n255\n" + bytes(range(12)))
    assert mb.pnm_to_png(str(p), str(tmp_path / "a.png")) == (2, 2)


# ------------------------------------------------------------------ tool refusals (no window needed)
def test_tools_refuse_bad_input():
    r = client.post("/gui_start", json={"tool": "firefox", "design": "kv_attn_n8"}).json()
    assert r["ok"] is False and "klayout or magic" in r["error"]
    r = client.post("/gui_start", json={"tool": "magic", "design": "../../etc"}).json()
    assert r["ok"] is False
    r = client.post("/gui_stop", json={"tool": "bash"}).json()
    assert r["ok"] is False
    r = client.post("/magic_live", json={"action": "save"}).json()
    assert r["ok"] is False and "action must be" in r["error"]
    r = client.post("/klayout_live", json={"action": "gds_write"}).json()
    assert r["ok"] is False and "action must be" in r["error"]
    assert client.post("/magic_live", json={"action": "zoom", "bbox": [1, 2, 3]}).status_code == 422


def test_status_shape():
    r = client.post("/gui_status", json={}).json()
    assert r["ok"] and set(r) >= {"klayout", "magic"} and "running" in r["magic"]


def test_images_under_img_dir():
    assert gt.IMG_DIR.endswith(os.path.join("build", "agent", "klayout_gui"))
    r = gt._img_reply(os.path.join(gt.IMG_DIR, "x.png"), "alt")
    assert r["markdown"] == "![alt](%s)" % r["png_url"] and r["png_url"].endswith("/img/x.png")


# ------------------------------------------------------------------ live (opt-in)
@pytest.mark.skipif(os.environ.get("GUI_TOOLS_LIVE") != "1", reason="set GUI_TOOLS_LIVE=1 (opens real windows)")
def test_live_windows():
    def post(n, **kw):
        r = client.post("/" + n, json=kw).json()
        assert r.get("ok"), r
        return r
    try:
        post("gui_start", tool="magic", design="kv_attn_n8")
        post("magic_live", action="open", design="kv_attn_n8")
        post("magic_live", action="zoom", bbox=[0, 0, 50, 50])
        post("magic_live", action="layers", layers=["met1", "met2"])
        d = post("magic_live", action="drc")
        assert d["drc_errors"] == 0
        s = post("magic_live", action="snapshot")
        assert os.path.getsize(os.path.join(gt.IMG_DIR, os.path.basename(s["png_url"]))) > 5000
        post("gui_start", tool="klayout", design="user_project_wrapper_soc_kv")
        post("klayout_live", action="layers", layers=["met4", "met5"])
        post("klayout_live", action="zoom", cell="mprj")
        post("klayout_live", action="snapshot")
    finally:
        client.post("/gui_stop", json={"tool": "magic"})
        client.post("/gui_stop", json={"tool": "klayout"})
    st = client.post("/gui_status", json={}).json()
    assert not st["magic"]["running"] and not st["klayout"]["running"]
