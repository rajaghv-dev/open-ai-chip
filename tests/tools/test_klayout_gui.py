"""pytest tests/tools/test_klayout_gui.py  (examples/hermes_klayout_gui: no Ollama, no display; offscreen KLayout only)

Run: build/agent/venv/bin/python -m pytest -q tests/tools/test_klayout_gui.py (also part of `make test`, section == tools)
Pass: every test passes or is skipped (opt-in tests need their env flag).
Docs: tests/tools/TEST_MATRIX_TOOLS.md, examples/hermes_klayout_gui/README.md
"""
import hashlib
import inspect
import json
import os
import struct
import sys

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "examples", "hermes_klayout_gui"))
sys.path.insert(0, os.path.join(REPO, "tools"))
import view_api as va  # noqa: E402

METHODS = ["open_design", "zoom_to", "show_layers", "highlight_drc", "measure", "snapshot", "state"]
GDS = os.path.join(REPO, "build", "results", "tiny_ai_core", "tiny_ai_core.gds")
needs_gds = pytest.mark.skipif(not os.path.isfile(GDS), reason="build/results/tiny_ai_core GDS not collected (git-ignored)")


def png_size(p):
    with open(os.path.join(REPO, p) if not os.path.isabs(p) else p, "rb") as f:
        h = f.read(24)
    assert h[:8] == b"\x89PNG\r\n\x1a\n"
    return struct.unpack(">II", h[16:24])


def sha(p):
    with open(p, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


@pytest.fixture(scope="module")
def be():
    pytest.importorskip("klayout.lay")
    from offscreen_backend import OffscreenBackend
    return OffscreenBackend()


# ------------------------------------------------------------ interface conformance
def test_tools_match_methods():
    """Pins down: tools match methods."""
    assert va.TOOL_NAMES == METHODS
    for t in va.TOOLS:
        assert t["type"] == "function" and t["function"]["parameters"]["type"] == "object"


def _conforms(cls):
    for m in METHODS:
        assert callable(getattr(cls, m, None)), m
        want = list(inspect.signature(getattr(va.ViewBackend, m)).parameters)
        assert list(inspect.signature(getattr(cls, m)).parameters) == want, (cls.__name__, m)


def test_offscreen_conforms():
    """Pins down: offscreen conforms."""
    pytest.importorskip("klayout.lay")
    from offscreen_backend import OffscreenBackend
    assert issubclass(OffscreenBackend, va.ViewBackend)
    _conforms(OffscreenBackend)


def test_live_backend_conforms_if_present():
    """Pins down: live backend conforms if present."""
    try:
        from live_backend import LiveBackend
    except ImportError:
        pytest.skip("live_backend.py not present")
    _conforms(LiveBackend)


# ------------------------------------------------------------ dispatch validation (no layout needed)
class Rec(va.ViewBackend):
    calls = []

    def _open_design(self, design):
        Rec.calls.append(("open", design))
        return {"design": design}


@pytest.mark.parametrize("name,args,frag", [
    ("rm_rf", {}, "unknown tool"),
    ("open_design", {}, "missing required"),
    ("open_design", {"design": 5}, "wrong type"),
    ("open_design", {"design": "x", "force": True}, "unexpected argument"),
    ("snapshot", {"width": 10}, "out of range"),
    ("snapshot", {"width": 99999}, "out of range"),
    ("measure", {"a": [0], "b": [1, 2]}, "needs 2"),
    ("measure", {"a": "0,0", "b": [1, 2]}, "wrong type"),
    ("zoom_to", {"target": {}}, "exactly one"),
    ("zoom_to", {"target": {"full": True, "cell": "x"}}, "exactly one"),
    ("highlight_drc", {"design": "x", "max_items": 0}, "out of range"),
])
def test_dispatch_rejects(name, args, frag):
    """Pins down: dispatch rejects."""
    r = va.dispatch(Rec(), name, args)
    assert r["ok"] is False and frag in r["error"]
    assert not Rec.calls


def test_dispatch_ok_and_unknown_design():
    """Pins down: dispatch ok and unknown design."""
    r = va.dispatch(Rec(), "open_design", {"design": "tiny_ai_core"})
    assert r == {"design": "tiny_ai_core", "ok": True}
    json.dumps(r)
    assert va.find_gds is not None
    r = va.dispatch(Rec(), "state")
    assert r["ok"] is False                     # abstract _state: error dict, never an exception


def test_layer_parsing():
    """Pins down: layer parsing."""
    assert (71, 20) in va.parse_layers(["met4"]) and (71, 16) in va.parse_layers("met4")
    assert va.parse_layers(["met4/drawing"]) == [(71, 20)]
    assert va.parse_layers(["68/20"]) == [(68, 20)]
    assert va.parse_layers("met1 and met2") == [(68, 20), (68, 16), (69, 20), (69, 16)]
    with pytest.raises(va.ViewError):
        va.parse_layers(["metal9"])
    with pytest.raises(va.ViewError):
        va.parse_layers([])


def test_png_path_confined():
    """Pins down: png path confined."""
    with pytest.raises(va.ViewError):
        va.check_png_path("/etc/x.png")
    with pytest.raises(va.ViewError):
        va.check_png_path("designs/tiny_ai_core/output/layout.png")
    with pytest.raises(va.ViewError):
        va.check_png_path("build/agent/klayout_gui/x.txt")
    assert va.check_png_path("build/agent/klayout_gui/t.png").endswith("klayout_gui/t.png")


# ------------------------------------------------------------ router
def test_router_decisions():
    """Pins down: router decisions."""
    import agent
    r = agent.route("show only met4 and met5 of user_project_wrapper_soc_kv", None)
    assert r == {"tool": "open_design", "args": {"design": "user_project_wrapper_soc_kv"}}
    assert agent.route("show only met4 and met5", "kv_attn_n8") == {
        "tool": "show_layers", "args": {"layers": ["met4", "met5"], "only": True}}
    assert agent.route("zoom to the macro", "kv_attn_n8")["tool"] == "zoom_to"
    assert agent.route("highlight DRC markers on tiny_ai_core", "tiny_ai_core") == {
        "tool": "highlight_drc", "args": {"design": "tiny_ai_core"}}
    assert agent.route("take a snapshot", "tiny_ai_core") == {"tool": "snapshot", "args": {}}
    assert agent.route("how many std cells does vision_block have?", None) is None
    assert agent.design_in("open kv_attn_n8_int4 please") == "kv_attn_n8_int4"      # longest name wins


def test_guardrail_rejects_once_then_router_acts(be):
    """A 'model' that never calls a tool: rejected once, then the router makes the first call."""
    import agent
    lines = []
    model = lambda msgs: "Sure, here it is."           # noqa: E731
    r = agent.run_episode("open nonexistent_design_xyz and zoom", be, model, emit=lines.append)
    # no valid design named: the router falls back to zoom_to, which fails cleanly (no design open) instead of crashing
    assert r["rejected"] == 1 and r["router_used"]
    assert r["calls"][0]["name"] == "zoom_to" and r["calls"][0]["ok"] is False
    assert any("guardrail" in l for l in lines)


@needs_gds
def test_guardrail_router_with_real_design(be):
    """Pins down: guardrail router with real design."""
    import agent
    r = agent.run_episode("show only met1 of tiny_ai_core", be, lambda m: "Done.", emit=lambda l: None)
    assert r["rejected"] == 1 and r["router_used"]
    assert r["calls"][0] == {"name": "open_design", "args": {"design": "tiny_ai_core"}, "ok": True, "src": "router"}


@needs_gds
def test_non_view_question_not_forced(be):
    """Pins down: non view question not forced."""
    import agent
    r = agent.run_episode("what is the price of a shuttle?", be, lambda m: "unknown", emit=lambda l: None)
    assert r["rejected"] == 0 and not r["calls"] and r["answer"] == "unknown"


# ------------------------------------------------------------ offscreen behaviour (real GDS)
@needs_gds
def test_offscreen_session_and_read_only(be):
    """Pins down: offscreen session and read only."""
    before = sha(GDS)
    r = va.dispatch(be, "open_design", {"design": "tiny_ai_core"})
    assert r["ok"] and r["top_cell"] == "tiny_ai_core" and r["n_layers"] > 10
    assert len(r["bbox_um"]) == 4
    r = va.dispatch(be, "show_layers", {"layers": ["met1", "met2"]})
    assert r["ok"] and any("68/20" in v for v in r["visible"]) and not any("71/20" in v for v in r["visible"])
    r = va.dispatch(be, "zoom_to", {"target": {"bbox": [0, 0, 50, 50]}})
    assert r["ok"] and r["view_bbox_um"][1] == 0 and r["view_bbox_um"][3] == 50
    assert va.dispatch(be, "zoom_to", {"target": {"cell": "no_such_cell_xyz"}})["ok"] is False
    assert va.dispatch(be, "zoom_to", {"target": {"bbox": [5, 5, 1, 1]}})["ok"] is False
    s = va.dispatch(be, "snapshot", {"width": 640, "height": 480})
    assert s["ok"] and png_size(s["png"]) == (640, 480) and s["bytes"] < 300_000
    assert va.dispatch(be, "snapshot", {"path": "../../tmp/x.png"})["ok"] is False
    d = va.dispatch(be, "highlight_drc", {"design": "tiny_ai_core"})
    assert d["ok"] and d["n_markers"] == 0 and ("note" in d)          # honest: DRC-clean
    d = va.dispatch(be, "highlight_drc", {"design": "tiny_ai_core", "demo_markers": True})
    assert d["ok"] and d["n_markers"] == 5 and d["demo"] is True
    d = va.dispatch(be, "highlight_drc", {"design": "tiny_ai_core", "demo_markers": [[1, 1, 3, 3]]})
    assert d["n_markers"] == 1
    assert va.dispatch(be, "highlight_drc", {"design": "vision_block"})["ok"] is False   # not the open design
    m = va.dispatch(be, "measure", {"a": [0, 0], "b": [3, 4]})
    assert m["distance_um"] == 5.0 and m["manhattan_um"] == 7.0
    st = va.dispatch(be, "state")
    assert st["design"] == "tiny_ai_core" and st["markers"] == 1
    json.dumps([r, s, d, m, st])
    assert sha(GDS) == before                                          # read-only guarantee


def test_state_before_open():
    """Pins down: state before open."""
    pytest.importorskip("klayout.lay")
    from offscreen_backend import OffscreenBackend
    b = OffscreenBackend()
    assert va.dispatch(b, "state")["design"] is None
    assert va.dispatch(b, "zoom_to", {"target": {"full": True}})["ok"] is False
    assert va.dispatch(b, "open_design", {"design": "../etc"})["ok"] is False
