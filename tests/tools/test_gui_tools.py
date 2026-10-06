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
    assert ops >= {"gui_start", "gui_stop", "gui_status", "klayout_live", "magic_live", "gui_command", "gui_examples"}


def test_mounted_in_tool_server():
    import tool_server as ts
    assert "gui_tools.py" in ts.EXTENSIONS
    spec = TestClient(ts.app).get("/openapi.json").json()
    ops = {v["post"]["operationId"] for v in spec["paths"].values() if "post" in v}
    assert {"gui_start", "gui_stop", "gui_status", "klayout_live", "magic_live", "gui_command", "gui_examples"} <= ops


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


# ------------------------------------------------------------------ gui_command parser (no model, no window)
def P(text, **kw):
    return gt.parse_text(text, kw.get("tool"), kw.get("running", []), designs=gt._designs_list())


def ops(text, **kw):
    p = P(text, **kw)
    assert not p["unparsed"], (text, p)
    return [(a["op"], a["tool"]) for a in p["actions"]]


@pytest.mark.parametrize("text,expect", [
    ("open kv_attn_n8", [("open", "klayout")]),
    ("open kv_attn_n8 in magic", [("open", "magic")]),
    ("Open kv_attn_n8 in KLayout please", [("open", "klayout")]),
    ("open kv_attn_n8 and show only met1 and met2", [("open", "klayout"), ("layers", "klayout")]),
    ("show met4 and met5", [("layers", "klayout")]),
    ("show only li1", [("layers", "klayout")]),
    ("show all", [("layers", "klayout")]),
    ("hide met5", [("layers", "klayout")]),
    ("hide met4 and met5", [("layers", "klayout")]),
    ("zoom to the lower-left 50 um", [("zoom", "klayout")]),
    ("zoom to the upper-right 80 microns", [("zoom", "klayout")]),
    ("zoom to the center 100 um", [("zoom", "klayout")]),
    ("zoom to 0 0 100 100", [("zoom", "klayout")]),
    ("zoom to the macro mprj", [("zoom", "klayout")]),
    ("zoom out", [("zoom", "klayout")]),
    ("zoom to fit", [("zoom", "klayout")]),
    ("run drc", [("drc", "klayout")]),
    ("run drc in magic", [("drc", "magic")]),
    ("measure from 0,0 to 100,0", [("measure", "klayout")]),
    ("find clk", [("find", "magic")]),
    ("take a snapshot", [("snapshot", "klayout")]),
    ("status", [("status", "klayout")]),
    ("close klayout", [("close", "klayout")]),
    ("close all", [("close", "klayout"), ("close", "magic")]),
    ("open kv_attn_n8 in magic, then run drc", [("open", "magic"), ("drc", "magic")]),
    ("open user_project_wrapper_soc_kv in klayout and zoom to the macro mprj", [("open", "klayout"), ("zoom", "klayout")]),
])
def test_parser_sentences(text, expect):
    assert ops(text) == expect


def test_parser_details():
    a = P("open kv_attn_n8 and show only met1 and met2")["actions"]
    assert a[0]["design"] == "kv_attn_n8" and a[1]["layers"] == ["met1", "met2"] and a[1]["only"] is True
    assert P("zoom to the lower-left 50 um")["actions"][0] == {"op": "zoom", "tool": "klayout", "corner": "lower-left", "size": 50.0}
    assert P("zoom to 100 100 0 0")["actions"][0]["bbox"] == [0.0, 0.0, 100.0, 100.0]
    assert P("zoom to the macro mprj")["actions"][0]["cell"] == "mprj"
    m = P("measure from 0,0 to 100,0")["actions"][0]
    assert m["a"] == [0.0, 0.0] and m["b"] == [100.0, 0.0]
    assert P("show met4 and also met5")["actions"][0]["only"] is False
    assert P("hide met5")["actions"][0]["hide"] == ["met5"]
    assert P("show all")["actions"][0]["layers"] == gt.ALL_LAYERS


def test_parser_default_tool():
    assert ops("run drc", running=["magic"]) == [("drc", "magic")]
    assert ops("zoom out", running=["klayout", "magic"], tool=None) in ([("zoom", "klayout")], [("zoom", "magic")])
    assert ops("zoom out", tool="magic") == [("zoom", "magic")]          # /magic prompt
    assert ops("open kv_attn_n8 in klayout", tool="magic") == [("open", "klayout")]   # the sentence wins


def test_corner_bbox():
    die = [0, 0, 260, 260]
    assert gt._corner_bbox("lower-left", 50, die) == [0, 0, 50, 50]
    assert gt._corner_bbox("upper-right", 50, die) == [210, 210, 260, 260]
    assert gt._corner_bbox("center", 100, die) == [80, 80, 180, 180]
    assert gt._die_bbox("kv_attn_n8") == [0.0, 0.0, 260.0, 260.0]


@pytest.mark.parametrize("text", ["save the layout", "write gds", "export gds to /tmp/x", "delete everything", "make me a sandwich",
                                   "open kv_attn_n8 and save it", "rm -rf /", ""])
def test_unknown_gives_hint_and_runs_nothing(text, monkeypatch):
    monkeypatch.setattr(gt, "_exec", lambda *a, **k: pytest.fail("must not run"))
    r = client.post("/gui_command", json={"text": text}).json()
    assert r["understood"] is False and r["ok"] is False and "open kv_attn_n8" in r["hint"] and r["did"] == []


def test_no_write_action_possible():
    for t in ("save", "write gds", "export", "gds write x", "writeall", "cif write"):
        assert not [a for a in P(t)["actions"] if a["op"] not in ("open", "zoom", "layers", "drc", "measure", "find", "snapshot", "status", "close")]
    assert not P("save the layout")["actions"] and not P("write gds")["actions"]
    src = open(os.path.join(TS, "gui_tools.py")).read().split("tool: gui_command")[1]
    assert not re.search(r"""request\(["'](?:SAVE|WRITE|GDS|EXEC)""", src, re.I)


def test_gui_command_runs_actions_in_order(monkeypatch):
    calls = []
    monkeypatch.setattr(gt, "_running", lambda: [])
    monkeypatch.setattr(gt, "_exec", lambda a, ctx, final: (calls.append((a["op"], final)), {"ok": True, "markdown": "![x](u)" if final else ""})[1])
    r = client.post("/gui_command", json={"text": "open kv_attn_n8 in klayout and show only met1 and zoom to the lower-left 50 um"}).json()
    assert r["ok"] and r["understood"] and [d["op"] for d in r["did"]] == ["open", "layers", "zoom"]
    assert calls == [("open", False), ("layers", False), ("zoom", True)] and r["markdown"] == "![x](u)"


def test_gui_command_stops_on_failure(monkeypatch):
    monkeypatch.setattr(gt, "_running", lambda: [])
    n = []
    monkeypatch.setattr(gt, "_exec", lambda a, ctx, final: (n.append(1), {"ok": False, "error": "boom"})[1])
    r = client.post("/gui_command", json={"text": "open kv_attn_n8 and run drc"}).json()
    assert r["ok"] is False and r["error"] == "boom" and len(n) == 1


def test_gui_command_bad_tool():
    assert client.post("/gui_command", json={"text": "status", "tool": "vim"}).json()["ok"] is False


def test_gui_examples():
    r = client.post("/gui_examples", json={}).json()
    assert r["ok"] and len(r["examples"]) >= 12 and "| what you type |" in r["markdown"]
    for e in r["examples"]:
        if e["say"] != "status":
            assert not P(e["say"])["unparsed"] and P(e["say"])["actions"], e


# ------------------------------------------------------------------ Layout tools panel (GET /gui)
PANEL_IDS = ["design", "open_klayout", "open_magic", "tool_klayout", "tool_magic", "close_klayout", "close_magic", "close_all",
             "layers", "zoom_full", "zoom_ll50", "zoom_center", "macro", "zoom_macro", "drc", "mx1", "my1", "mx2", "my2", "measure",
             "snapshot", "cmd", "run_cmd", "img", "log"]


def test_panel_served_with_every_control():
    r = client.get("/gui")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    for i in PANEL_IDS:
        assert 'id="%s"' % i in r.text, i
    for layer in ("li1", "met1", "met2", "met3", "met4", "met5", "all"):
        assert layer in r.text
    assert "http://" not in r.text.replace("http://www.w3.org", "") and "https://" not in r.text      # no external assets


def test_panel_endpoints_exist():
    import tool_server as ts
    spec = TestClient(ts.app).get("/openapi.json").json()
    have = {p.strip("/") for p in spec["paths"]}
    called = set(re.findall(r'call\("([a-z_]+)"', TestClient(ts.app).get("/gui").text))
    called |= {"klayout_live", "magic_live"}
    assert {"gui_start", "gui_stop", "gui_status", "gui_command", "gui_examples", "list_designs"} <= called
    assert called <= have, called - have
    assert "/gui" not in spec["paths"]            # include_in_schema=False


def test_app_has_layout_menu_and_window():
    src = open(os.path.join(REPO, "examples", "hermes_desktop", "desktop", "app.py")).read()
    for t in ('"Layout tools"', "Open in KLayout...", "Open in Magic...", "Close layout windows", "HERMES_LAYOUT_PANEL", "/gui"):
        assert t in src, t


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
