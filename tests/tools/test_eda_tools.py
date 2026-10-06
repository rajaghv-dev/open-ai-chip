"""pytest tests/tools  (run with build/agent/venv/bin/python -m pytest tests/tools)"""
import glob
import json
import os
import sys

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "tools"))
import eda_tools as T  # noqa: E402


def need_gds(design):
    top = json.load(open(os.path.join(REPO, "designs", design, "config.json")))["DESIGN_NAME"]
    if not os.path.isfile(os.path.join(REPO, "build", "results", design, top + ".gds")):
        pytest.skip("build/results/%s/%s.gds missing (local, git-ignored)" % (design, top))


def test_schemas_match_dispatch():
    names = [t["function"]["name"] for t in T.TOOLS]
    assert len(names) == 10 and len(set(names)) == 10
    for t in T.TOOLS:
        assert t["type"] == "function" and t["function"]["parameters"]["type"] == "object"
    json.dumps(T.TOOLS)


def test_list_designs():
    r = T.call("list_designs", {})
    d = {x["design"]: x for x in r["designs"]}
    assert d["vision_block"]["hardened"] and d["vision_block"]["design_name"] == "vision_block"
    assert d["vision_block"]["description"]


def test_read_metrics_exact_and_pattern():
    m = json.load(open(os.path.join(REPO, "designs/vision_block/output/metrics.json")))
    r = T.call("read_metrics", {"design": "vision_block", "keys": ["design__instance__count__stdcell"]})
    assert r["metrics"]["design__instance__count__stdcell"]["value"] == m["design__instance__count__stdcell"]
    r = T.call("read_metrics", {"design": "vision_block", "pattern": "setup__ws"})
    assert r["matched"] >= 3 and all("setup__ws" in k for k in r["metrics"])


def test_compare_designs_all_hardened():
    r = T.call("compare_designs", {"metric": "timing__setup__ws__corner:nom_tt_025C_1v80", "designs": None})
    got = {x["design"] for x in r["sorted_ascending"]} | set(r.get("missing_in", []))
    assert got == set(T._hardened())
    vals = [x["value"] for x in r["sorted_ascending"]]
    assert vals == sorted(vals) and r["min"]["value"] == vals[0]


def test_precheck_summary():
    if not glob.glob(os.path.join(REPO, "build", "precheck", "results_*", "summary.tsv")):
        pytest.skip("no build/precheck results")
    r = T.call("precheck_summary", {})
    assert r["pass"] == 14 and r["fail"] == 0


def test_layout_summary_tiny_ai_core():
    need_gds("tiny_ai_core")
    r = T.call("layout_summary", {"design": "tiny_ai_core"})
    assert r["top_cell"] == "tiny_ai_core" and r["die_size_um"] == [250.0, 250.0]
    assert len(r["top_layers_by_shapes"]) == 10 and r["total_shapes"] > 0


def test_layout_summary_wrapper_macros():
    need_gds("user_project_wrapper_soc_itm")
    r = T.call("layout_summary", {"design": "user_project_wrapper_soc_itm"})
    assert "error" not in r and r["top_level_macro_instances"]


def test_layer_stats():
    need_gds("tiny_ai_core")
    r = T.call("layer_stats", {"design": "tiny_ai_core", "layer": "met4"})
    assert r["shapes"] > 0 and r["area_um2"] > 0 and r["bbox_um"]
    assert T.call("layer_stats", {"design": "tiny_ai_core", "layer": "71/20"})["shapes"] == r["shapes"]
    assert "error" in T.call("layer_stats", {"design": "tiny_ai_core", "layer": "bogus"})


def test_find_pins():
    need_gds("tiny_ai_core")
    r = T.call("find_pins", {"design": "tiny_ai_core", "pattern": r"^wbs_dat_i\["})
    assert r["total_matches"] == 32 and len(r["pins"]) == 32
    assert "error" in T.call("find_pins", {"design": "tiny_ai_core", "pattern": "("})


def test_render_png():
    need_gds("tiny_ai_core")
    r = T.call("render_png", {"design": "tiny_ai_core", "width_px": 600})
    assert not r["fallback"], r
    p = os.path.join(REPO, r["path"])
    assert os.path.getsize(p) > 1000 and open(p, "rb").read(8) == b"\x89PNG\r\n\x1a\n"
    assert "error" in T.call("render_png", {"design": "tiny_ai_core", "out": "/tmp/x.png"})


def test_signoff_summary():
    r = T.call("signoff_summary", {"design": "vision_block"})
    assert r["drc"]["magic"] == 0 and r["setup_worst"]["corner"] and "check_signoff" in r


def test_classify_slew():
    if not glob.glob(os.path.join(REPO, "designs", "vision_block", "runs", "RUN_*")):
        pytest.skip("no local flow runs")
    r = T.call("classify_slew", {"design": "vision_block"})
    assert "error" not in r and r["output"]


@pytest.mark.parametrize("bad", ["../etc", "nope", "", "vision_block/../tiny_ai_core", None, 3])
def test_bad_design_rejected(bad):
    for tool in ("read_metrics", "layout_summary", "signoff_summary", "render_png"):
        assert "error" in T.call(tool, {"design": bad})


def test_call_never_raises():
    assert "error" in T.call("nope", {})
    assert "error" in T.call("read_metrics", {})
    assert "error" in T.call("read_metrics", "x")
    assert "error" in T.call("list_designs", {"junk": 1})
