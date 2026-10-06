"""Live-window bridge tests (option B). pytest tests/tools/test_klayout_live.py

Default (no GUI, no Docker): protocol, allow-list, token, a fake in-process server for LiveBackend, and the bridge
methods run against an offscreen klayout.lay.LayoutView standing in for the GUI window (needs a local GDS, else skipped).
Opt-in: KLAYOUT_LIVE=1 starts the real KLayout desktop app (opens a window on your screen).

Run: build/agent/venv/bin/python -m pytest -q tests/tools/test_klayout_live.py (also part of `make test`, section == tools)
Pass: every test passes or is skipped (opt-in tests need their env flag).
Docs: tests/tools/TEST_MATRIX_TOOLS.md, examples/hermes_klayout_gui/README.md
"""
import importlib.util
import json
import os
import socket
import subprocess
import sys
import threading
import types

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
GUI = os.path.join(REPO, "examples", "hermes_klayout_gui")
sys.path.insert(0, GUI)
os.environ["KLAYOUT_AGENT_NOSTART"] = "1"          # importing the macro must not start a server


def _load_bridge():
    spec = importlib.util.spec_from_file_location("agent_bridge", os.path.join(GUI, "klayout_macro", "agent_bridge.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


ab = _load_bridge()
import live_backend as lb  # noqa: E402
import view_api as va  # noqa: E402


# ------------------------------------------------------------------ protocol
def test_encode_decode_roundtrip():
    """Pins down: encode decode roundtrip."""
    line = lb.encode_request(7, "zoom_to", {"target": {"full": True}}, token="t")
    req = json.loads(line)
    assert line.endswith(b"\n") and req == {"id": 7, "method": "zoom_to", "params": {"target": {"full": True}}, "token": "t"}
    assert lb.decode_response('{"id":7,"result":{"ok":true,"x":1}}') == (7, {"ok": True, "x": 1})
    assert lb.decode_response('{"id":7,"error":"nope"}') == (7, {"ok": False, "error": "nope"})
    with pytest.raises(lb.LiveError):
        lb.decode_response("not json")


def test_allow_list_refuses_everything_else():
    """Pins down: allow list refuses everything else."""
    calls = []
    ex = lambda m, p: calls.append(m) or {"ok": True}
    for bad in ("exec", "save", "save_layout", "eval", "__import__", "quit_now", None, 5):
        r = ab.process_line(json.dumps({"id": 1, "method": bad, "params": {}}), ex)
        assert "error" in r and "not allowed" in r["error"], bad
    assert calls == []
    for good in ab.VIEW_METHODS:
        assert ab.process_line(json.dumps({"id": 2, "method": good, "params": {}}), ex)["result"]["ok"]
    assert set(ab.VIEW_METHODS) == {"open_design", "zoom_to", "show_layers", "highlight_drc", "measure", "snapshot", "state"}


def test_quit_is_disabled_by_default_and_bad_input():
    """Pins down: quit is disabled by default and bad input."""
    ex = lambda m, p: {"ok": True}
    assert "disabled" in ab.process_line('{"id":1,"method":"quit"}', ex)["error"]
    assert ab.process_line('{"id":1,"method":"quit"}', ex, allow_quit=True)["result"]["ok"]
    assert "bad JSON" in ab.process_line("{oops", ex)["error"]
    assert "object" in ab.process_line("[1,2]", ex)["error"]
    assert "params" in ab.process_line('{"id":1,"method":"state","params":[1]}', ex)["error"]


def test_token_checked_per_request():
    """Pins down: token checked per request."""
    ex = lambda m, p: {"ok": True}
    assert "token" in ab.process_line('{"id":1,"method":"state"}', ex, token="s3")["error"]
    assert "token" in ab.process_line('{"id":1,"method":"state","token":"no"}', ex, token="s3")["error"]
    assert ab.process_line('{"id":1,"method":"state","token":"s3"}', ex, token="s3")["result"]["ok"]


def test_bridge_binds_localhost_only():
    """Pins down: bridge binds localhost only."""
    assert ab.HOST == "127.0.0.1"
    src = open(os.path.join(GUI, "klayout_macro", "agent_bridge.py")).read()
    assert "0.0.0.0" not in src.replace('never 0.0.0.0', "")
    for forbidden in (".save(", "write_layout", "os.system", "subprocess", "eval(", "exec("):
        assert forbidden not in src, forbidden


# ------------------------------------------------------------------ fake in-process server
class FakeServer:
    """Speaks the bridge protocol on 127.0.0.1 with the real process_line, answering from a canned table."""

    def __init__(self, token=None, drop_first=False):
        self.srv = socket.socket()
        self.srv.bind(("127.0.0.1", 0))
        self.srv.listen(2)
        self.port = self.srv.getsockname()[1]
        self.token, self.seen, self.drop = token, [], drop_first
        threading.Thread(target=self.loop, daemon=True).start()

    def execute(self, method, params):
        self.seen.append((method, params))
        if method == "open_design":
            return {"ok": True, "design": params["design"], "top_cell": "top", "bbox_um": [0, 0, 10, 10], "n_layers": 3}
        if method == "zoom_to" and params["target"].get("cell") == "nope":
            return {"ok": False, "error": "no cell or instance matches 'nope'"}
        return {"ok": True, "view_bbox_um": [0, 0, 1, 1], "visible": [], "method": method}

    def loop(self):
        while True:
            try:
                c, _ = self.srv.accept()
            except OSError:
                return
            if self.drop:
                self.drop = False
                c.close()
                continue
            threading.Thread(target=self.conn, args=(c,), daemon=True).start()

    def conn(self, c):
        f = c.makefile("rb")
        for line in f:
            c.sendall(ab.encode(ab.process_line(line.decode(), self.execute, self.token)))

    def close(self):
        self.srv.close()


def test_live_backend_round_trips_against_fake_server():
    """Pins down: live backend round trips against fake server."""
    s = FakeServer()
    b = lb.LiveBackend(port=s.port)
    assert b.open_design("kv_attn_n8")["top_cell"] == "top"
    assert b.zoom_to({"full": True})["ok"]
    assert b.show_layers(["met1"], True)["ok"]
    assert b.snapshot(None, 800, 600)["ok"]
    assert b.state()["ok"]
    m = b.measure([0, 0], [3, 4])
    assert m["distance_um"] == 5.0
    assert [x[0] for x in s.seen[:6]] == ["ping", "open_design", "zoom_to", "show_layers", "snapshot", "state"]
    assert s.seen[3][1] == {"layers": ["met1"], "only": True}
    bad = b.zoom_to({"cell": "nope"})
    assert bad["ok"] is False and "no cell" in bad["error"]
    # dispatch() from view_api validates then reaches the same backend
    assert va.dispatch(b, "zoom_to", {"target": {"full": True}})["ok"]
    assert va.dispatch(b, "bogus", {})["ok"] is False
    b.close()
    s.close()


def test_live_backend_token_and_reconnect():
    """Pins down: live backend token and reconnect."""
    s = FakeServer(token="abc")
    with pytest.raises(lb.LiveError):
        lb.LiveBackend(port=s.port, token="wrong")
    b = lb.LiveBackend(port=s.port, token="abc")
    b._sock.close()                                  # simulate the window restarting: one transparent reconnect
    assert b.state()["ok"]
    s.close()


def test_live_backend_clear_error_when_klayout_not_running():
    """Pins down: live backend clear error when klayout not running."""
    sk = socket.socket()
    sk.bind(("127.0.0.1", 0))
    port = sk.getsockname()[1]
    sk.close()
    with pytest.raises(lb.LiveError) as e:
        lb.LiveBackend(port=port)
    assert "not running" in str(e.value) and "start_live.sh" in str(e.value)


def test_live_backend_timeout():
    """Pins down: live backend timeout."""
    sk = socket.socket()
    sk.bind(("127.0.0.1", 0))
    sk.listen(1)
    b = lb.LiveBackend(port=sk.getsockname()[1], timeout=0.3, connect=False)
    with pytest.raises(lb.LiveError) as e:
        b._call("ping", {})
    assert "did not answer" in str(e.value)
    sk.close()


# ------------------------------------------------------------------ bridge methods on an offscreen view (GUI stand-in)
def _shim_pya():
    import klayout.db as db
    import klayout.lay as lay
    import klayout.rdb as rdb

    class MW:
        def __init__(self):
            self.v = lay.LayoutView()

        def load_layout(self, path, mode):
            self.v.clear_layers()
            self.v.load_layout(path, False)
            self.v.add_missing_layers()

        def current_view(self):
            return self.v

    mw = MW()

    class App:
        def main_window(self):
            return mw

        def version(self):
            return "offscreen-shim"

    app = App()
    return types.SimpleNamespace(DBox=db.DBox, Marker=lay.Marker, ReportDatabase=rdb.ReportDatabase,
                                 Application=types.SimpleNamespace(instance=lambda: app))


def _gds_or_skip(design):
    try:
        va.find_gds(design)
    except Exception:
        pytest.skip("no local GDS for %s" % design)


def test_bridge_methods_on_offscreen_stand_in():
    """Pins down: bridge methods on offscreen stand in."""
    pytest.importorskip("klayout.lay")
    _gds_or_skip("kv_attn_n8")
    ab.pya = _shim_pya()
    try:
        b = ab.Bridge()
        run = b.run_gui
        r = run("open_design", {"design": "kv_attn_n8"})
        assert r["ok"] and r["top_cell"] == "kv_attn_n8" and r["n_layers"] > 5
        w = r["bbox_um"][2] - r["bbox_um"][0]
        r = run("zoom_to", {"target": {"bbox": [10, 10, 60, 60]}})
        assert r["ok"] and (r["view_bbox_um"][2] - r["view_bbox_um"][0]) < w
        assert not run("zoom_to", {"target": {"cell": "nope_nope"}})["ok"]
        r = run("show_layers", {"layers": ["met1", "met2"], "only": True})
        assert r["ok"] and r["visible"] and all(x.split()[0].split("/")[0] in ("68", "69") for x in r["visible"])
        assert not run("show_layers", {"layers": ["bogus9"], "only": True})["ok"]
        r = run("highlight_drc", {"design": "kv_attn_n8", "demo_markers": True})
        assert r["ok"] and r["n_markers"] == 5 and "NOT real" in r["note"]
        r = run("highlight_drc", {"design": "kv_attn_n8"})
        assert r["ok"] and r["n_markers"] == 0
        r = run("snapshot", {"path": "build/agent/klayout_gui/_test_live.png", "width": 400, "height": 300})
        assert r["ok"] and os.path.getsize(os.path.join(REPO, r["png"])) > 1000
        os.remove(os.path.join(REPO, r["png"]))
        assert not run("snapshot", {"path": "/etc/x.png"})["ok"]
        assert run("state", {})["design"] == "kv_attn_n8"
    finally:
        ab.pya = None


# ------------------------------------------------------------------ opt-in: the real desktop app
@pytest.mark.skipif(os.environ.get("KLAYOUT_LIVE") != "1", reason="set KLAYOUT_LIVE=1 to start the real KLayout window")
def test_real_klayout_window():
    """Pins down: real klayout window."""
    port = int(os.environ.get("KLAYOUT_AGENT_PORT", "8765"))
    env = dict(os.environ, KLAYOUT_AGENT_PORT=str(port), KLAYOUT_AGENT_ALLOW_QUIT="1")
    env.pop("KLAYOUT_AGENT_NOSTART", None)     # set at module import for the offline tests; the real window must start the bridge
    os.makedirs(os.path.join(REPO, "build", "agent", "klayout_gui"), exist_ok=True)
    klog = open(os.path.join(REPO, "build", "agent", "klayout_gui", "live_test_klayout.log"), "w")   # bridge log, for debugging
    p = subprocess.Popen(["bash", os.path.join(GUI, "start_live.sh")], env=env, stdin=subprocess.DEVNULL,
                         stdout=klog, stderr=subprocess.STDOUT)
    b = None
    try:
        import time
        t0 = time.time()
        while b is None:
            try:
                b = lb.LiveBackend(port=port)
            except lb.LiveError:
                assert time.time() - t0 < 120, "bridge did not come up in 120 s (Gatekeeper dialog on the app?)"
                time.sleep(1)
        assert b.open_design("kv_attn_n8")["ok"]
        assert b.zoom_to({"full": True})["ok"]
        assert b.show_layers(["met1", "met2"])["ok"]
        r = b.snapshot("build/agent/klayout_gui/live_test.png", 800, 600)
        assert r["ok"] and os.path.getsize(os.path.join(REPO, r["png"])) > 1000
    finally:
        if b:
            try:
                b.quit_window()
            except Exception:
                pass
            b.close()
        try:
            p.wait(timeout=15)
        except subprocess.TimeoutExpired:
            p.kill()
