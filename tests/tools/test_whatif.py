"""Tests for examples/hermes_desktop/tool_server/whatif_tools.py (param_info, propose_change, whatif_run, whatif_result, whatif_sweep,
whatif_list, whatif_clean) and the data files and skills behind them.

Covered: HARD RULE enforcement (loosening, DELAY, LVS, synth checks, weakening, wrapper geometry, unknown keys, types), patch text, the
dir:: rewriting of the copy, the two-step confirmation gate and the one-flow rule (no Docker, no job: the job start is faked), result
comparison on fixtures built from the committed vision_block metrics, sweeps, cleanup limited to build/whatif/, and that every LibreLane key
named in the four skills exists in the 3.0.2 variable list. FastAPI TestClient only; nothing under designs/ is written.

Run: build/agent/venv/bin/python -m pytest -q tests/tools/test_whatif.py
Pass: every test passes.
Docs: tests/tools/TEST_MATRIX_TOOLS.md, docs/HERMES_DESKTOP.md (section "What-if experiments: change constraints safely")
"""
import hashlib
import json
import os
import re
import sys

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
HD = os.path.join(REPO, "examples", "hermes_desktop")
sys.path.insert(0, os.path.join(HD, "tool_server"))
pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

import tool_server as ts  # noqa: E402
import whatif_tools as W  # noqa: E402

client = TestClient(ts.app)


MODELS = {"param_info": W.ParamReq, "propose_change": W.ChangeReq, "whatif_run": W.RunReq, "whatif_result": W.ResultReq,
          "whatif_sweep": W.SweepReq, "whatif_list": W.Empty, "whatif_clean": W.CleanReq}


def post(name, body=None):
    """The tool function of THIS module instance (tests monkeypatch it; the app mounts its own copy); validation errors become {error}."""
    from pydantic import ValidationError
    try:
        return getattr(W, name)(MODELS[name](**(body or {})))
    except ValidationError as e:
        return {"error": str(e)}


def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


@pytest.fixture(autouse=True)
def sandbox(monkeypatch, tmp_path):
    """All what-if output goes to a temp dir; no Docker; no real job."""
    base = tmp_path / "whatif"
    monkeypatch.setattr(W, "BASE", str(base))
    monkeypatch.setattr(W, "PATCH_DIR", str(base / "patches"))
    monkeypatch.setattr(W, "SWEEP_DIR", str(base / "sweeps"))
    monkeypatch.setattr(W, "DOCKER_BUSY_HOOK", lambda: None)
    monkeypatch.delenv("CHIP_TOOLS_NO_CONFIRM", raising=False)
    monkeypatch.setattr(W, "_committed_current", lambda d: True)
    started = []

    class FakeJob:
        id = "20260101_000000-01"
        state = "running"
        log = str(tmp_path / "job.log")

    def fake_start(cmd, label, confirmed):
        started.append((cmd, label))
        srv = W._server()
        if confirmed:
            srv._consume_confirm(confirmed)
        return FakeJob(), None

    monkeypatch.setattr(W, "_start_job", fake_start)
    return {"base": str(base), "started": started}


# ---------------------------------------------------------------- mounting and data
def test_tools_mounted_with_descriptions():
    spec = client.get("/openapi.json").json()
    for name in ("param_info", "propose_change", "whatif_run", "whatif_result", "whatif_sweep", "whatif_list", "whatif_clean"):
        op = spec["paths"]["/" + name]["post"]
        assert op["operationId"] == name and op.get("description")
    assert "whatif_tools.py" in ts.EXTENSIONS


def test_catalog_and_notes_are_consistent():
    cat = W.catalog()
    assert len(cat) > 400 and "CLOCK_PERIOD" in cat and "PL_TARGET_DENSITY_PCT" in cat
    assert W.canonical("PL_RESIZER_MAX_SLEW_MARGIN") == "DESIGN_REPAIR_MAX_SLEW_PCT"
    assert [k for k in W.notes() if k not in cat] == []
    for k, n in W.notes().items():
        assert n["engine"] and n["safe"] and n["doc"], k


def test_skill_key_names_exist_in_librelane_3_0_2():
    """Every LibreLane-looking variable named in the four skills is a real 3.0.2 variable (or alias), or a documented non-variable."""
    known = set(W.catalog()) | set(W.aliases())
    allowed_other = {"DISABLE_LVS", "FALLBACK_SDC_FILE", "FLOW_TIMEOUT", "CHIP_TOOLS_NO_CONFIRM", "DOCKER_HOST", "PDK_ROOT", "MAX_TRANSITION",
                     "OPENLANE_SDC_IDEAL_CLOCKS", "CLOCK_PERIOD_PS", "DELAY_0", "PROFILE", "NETGEN_SETUP"}
    pat = re.compile(r"(?<![A-Za-z0-9_/.<-])(?:SYNTH|PL|GPL|DPL|GRT|DRT|CTS|FP|IO_PIN|PDN|DESIGN_REPAIR|RT|DIODE|HEURISTIC|MAX|CLOCK|RUN|ERROR_ON|"
                     r"TIME|OUTPUT|IO|TOP|BOTTOM|LEFT|RIGHT|DIE|CORE|RSZ|STA|PNR|SIGNOFF|MAGIC|KLAYOUT|LVS|RCX|VERILOG)(?:_[A-Z0-9]+)+(?![*A-Za-z0-9_<])")
    bad, checked = {}, set()
    for skill in ("tune-synthesis", "tune-timing-sdc", "tune-openroad-engines", "whatif-experiment"):
        for fn in ("SKILL.md", "reference.md"):
            text = open(os.path.join(REPO, ".claude", "skills", skill, fn), encoding="utf-8").read()
            text = re.sub(r"`[^`]*\.(?:rpt|json|py|sh|md|tcl|abc|sdc|cfg|txt)`", " ", text)
            for m in pat.finditer(text):
                tok = m.group(0)
                checked.add(tok)
                if tok not in known and tok not in allowed_other and not tok.startswith(("DELAY", "SYNTH_ELABORATE")):
                    bad.setdefault(tok, []).append("%s/%s" % (skill, fn))
    assert bad == {}, bad
    assert len(checked) > 120, len(checked)      # the pattern really found the keys


def test_skills_have_frontmatter():
    for skill in ("tune-synthesis", "tune-timing-sdc", "tune-openroad-engines", "whatif-experiment"):
        txt = open(os.path.join(REPO, ".claude", "skills", skill, "SKILL.md"), encoding="utf-8").read()
        m = re.match(r"---\nname: (.+)\ndescription: (.+)\n---\n", txt)
        assert m and m.group(1) == skill and len(m.group(2)) > 100
        assert os.path.isfile(os.path.join(REPO, ".claude", "skills", skill, "reference.md"))
        for rule in ("CLOCK_PERIOD", "MAX_TRANSITION_CONSTRAINT", "DISABLE_LVS", "DELAY", "ERROR_ON_SYNTH_CHECKS"):
            assert rule in txt, (skill, rule)


# ---------------------------------------------------------------- rules
@pytest.mark.parametrize("key,value,rule", [
    ("CLOCK_PERIOD", 30, "R1-CLOCK"),
    ("CLOCK_PERIOD", 25.5, "R1-CLOCK"),
    ("MAX_TRANSITION_CONSTRAINT", 1.0, "R1-SLEW"),
    ("MAX_TRANSITION_CONSTRAINT", "1.5", "R1-SLEW"),
    ("MAX_FANOUT_CONSTRAINT", 12, "R1-TIGHTEN"),
    ("CLOCK_UNCERTAINTY_CONSTRAINT", 0.1, "R1-TIGHTEN"),
    ("DISABLE_LVS", True, "R2-LVS"),
    ("RUN_LVS", False, "R2-LVS"),
    ("ERROR_ON_LVS_ERROR", False, "R2-LVS"),
    ("SYNTH_STRATEGY", "DELAY 3", "R3-DELAY"),
    ("ERROR_ON_SYNTH_CHECKS", False, "R4-SYNTH-CHECKS"),
    ("RUN_MAGIC_DRC", False, "R5-WEAKEN"),
    ("RUN_ANTENNA_REPAIR", False, "R5-WEAKEN"),
    ("ERROR_ON_MAGIC_DRC", False, "R5-WEAKEN"),
    ("MAGIC_DRC_USE_GDS", False, "R5-WEAKEN"),
    ("TIMING_VIOLATION_CORNERS", [], "R5-WEAKEN"),
    ("KLAYOUT_DRC_RUNSET", "x", "R5-WEAKEN"),
    ("SYNTH_ELABORATE_ONLY", True, "R7-ELABORATE"),
    ("VERILOG_FILES", ["x.v"], "R8-DESIGN-FILE"),
    ("PNR_SDC_FILE", "x.sdc", "R8-DESIGN-FILE"),
    ("TOTALLY_MADE_UP_KEY", 1, "R9-UNKNOWN"),
    ("PL_TARGET_DENSITY_PCT", "dense", "R10-TYPE"),
    ("SYNTH_STRATEGY", "AREA 9", "R10-TYPE"),
    ("PL_RESIZER_SETUP_SLACK_MARGIN", -0.1, "R11-MARGIN"),
    ("GRT_RESIZER_ALLOW_SETUP_VIOS", True, "R11-MARGIN"),
])
def test_blocked_with_rule(key, value, rule):
    r = W.check_key("vision_block", key, value)
    assert r["status"] == "blocked" and r["rule"] == rule, r
    assert r["reason"] and r["rule_text"]


@pytest.mark.parametrize("key,value", [
    ("CLOCK_PERIOD", 20), ("CLOCK_PERIOD", 25), ("MAX_TRANSITION_CONSTRAINT", 0.5), ("MAX_FANOUT_CONSTRAINT", 6),
    ("CLOCK_UNCERTAINTY_CONSTRAINT", 0.4), ("SYNTH_STRATEGY", "AREA 2"), ("PL_TARGET_DENSITY_PCT", "70"), ("DESIGN_REPAIR_MAX_SLEW_PCT", 30),
    ("PL_RESIZER_HOLD_SLACK_MARGIN", 0.3), ("GRT_ADJUSTMENT", 0.4), ("DIE_AREA", [0, 0, 100, 100]), ("CTS_SINK_CLUSTERING_SIZE", 12),
    ("DRT_OPT_ITERS", 80), ("SYNTH_SIZING", "true"), ("ERROR_ON_SYNTH_CHECKS", True), ("RUN_POST_GRT_RESIZER_TIMING", True),
])
def test_allowed(key, value):
    r = W.check_key("vision_block", key, value)
    assert r["status"] == "allowed", r


def test_coercion_and_alias_notes():
    r = W.check_key("vision_block", "PL_TARGET_DENSITY_PCT", "70")
    assert r["value"] == 70
    r = W.check_key("vision_block", "PL_RESIZER_MAX_SLEW_MARGIN", 70)
    assert r["status"] == "allowed" and r["canonical"] == "DESIGN_REPAIR_MAX_SLEW_PCT"
    assert any("deprecated alias" in n for n in r["notes"]) and any("out of memory" in n for n in r["notes"])


def test_tightening_notes_and_current_value():
    r = W.check_key("vision_block", "CLOCK_PERIOD", 20)
    assert r["current"] == 25 and any("tightens the clock" in n for n in r["notes"])
    # the effective slew limit of a wrapper is its own 1.5 ns, of a macro the PDK 0.75 ns
    assert W.effective("vision_block", "MAX_TRANSITION_CONSTRAINT")["value"] == 0.75
    assert W.effective("user_project_wrapper", "MAX_TRANSITION_CONSTRAINT")["value"] == 1.5


def test_wrapper_geometry_is_fixed():
    for k, v in (("DIE_AREA", [0, 0, 1, 1]), ("FP_SIZING", "relative"), ("PDN_VPITCH", 100), ("IO_PIN_PLACEMENT_MODE", "annealing"), ("RT_MAX_LAYER", "met5")):
        r = W.check_key("user_project_wrapper", k, v)
        assert r["status"] == "blocked" and r["rule"] == "R6-GEOMETRY", (k, r)
    # a macro may change them on its copy
    assert W.check_key("tiny_ai_core", "FP_SIZING", "relative")["status"] == "allowed"


def test_propose_change_endpoint_blocks_and_allows():
    r = post("propose_change", {"design": "vision_block", "changes": {"CLOCK_PERIOD": 30}})
    assert r["verdict"] == "all blocked" and r["patch_text"] == "" and r["patch_file"] is None and r["applied"] is False
    assert r["keys"][0]["rule"] == "R1-CLOCK" and "25" in r["keys"][0]["reason"]
    r = post("propose_change", {"design": "vision_block", "changes": {"MAX_TRANSITION_CONSTRAINT": 1.0, "CLOCK_PERIOD": 20}})
    assert r["verdict"] == "partly blocked"
    st = {k["key"]: k["status"] for k in r["keys"]}
    assert st == {"MAX_TRANSITION_CONSTRAINT": "blocked", "CLOCK_PERIOD": "allowed"}
    assert "-    \"CLOCK_PERIOD\": 25," in r["patch_text"] and "+    \"CLOCK_PERIOD\": 20," in r["patch_text"]
    assert "MAX_TRANSITION" not in r["patch_text"]
    assert post("propose_change", {"design": "nope", "changes": {"A": 1}})["error"].startswith("unknown design")


def test_patch_text_and_file_never_touch_the_design(sandbox):
    cfg = os.path.join(REPO, "designs", "vision_block", "config.json")
    before = sha(cfg)
    r = post("propose_change", {"design": "vision_block", "changes": {"PL_TARGET_DENSITY_PCT": 70, "DESIGN_REPAIR_MAX_SLEW_PCT": 20}})
    assert sha(cfg) == before
    assert r["patch_file"].startswith(os.path.relpath(sandbox["base"], REPO)) or os.path.isabs(r["patch_file"]) or "patches" in r["patch_file"]
    text = open(os.path.join(REPO, r["patch_file"]) if not os.path.isabs(r["patch_file"]) else r["patch_file"]).read()
    assert text == r["patch_text"]
    assert text.startswith("--- a/designs/vision_block/config.json\n+++ b/designs/vision_block/config.json\n")
    # the config spells the slew margin with the old name: changed in place, not duplicated
    assert "PL_RESIZER_MAX_SLEW_MARGIN\": 20" in text and "\"DESIGN_REPAIR_MAX_SLEW_PCT\"" not in text
    assert "+    \"PL_TARGET_DENSITY_PCT\": 70" in text


# ---------------------------------------------------------------- param_info
def test_param_info_key_and_groups():
    r = post("param_info", {"design": "vision_block", "key": "PL_TARGET_DENSITY_PCT"})
    assert r["engine"] == "gpl" and r["unit"] == "%" and r["current"]["value"] is None
    assert "OPENROAD_ENGINES.md" in r["doc"] and r["safe_range"] and r["rule"]
    r = post("param_info", {"design": "vision_block", "key": "PL_RESIZER_MAX_SLEW_MARGIN"})
    assert r["key"] == "DESIGN_REPAIR_MAX_SLEW_PCT" and r["current"]["value"] == 40 and r["alias_of"] == "PL_RESIZER_MAX_SLEW_MARGIN"
    assert "design_comment" not in r or "SLEW" in r["design_comment"]["key"]
    r = post("param_info", {"design": "image_text_match", "key": "DESIGN_REPAIR_MAX_SLEW_PCT"})
    assert "//SLEW" == r["design_comment"]["key"] and "memory" in r["design_comment"]["text"]
    r = post("param_info", {"design": "vision_block", "key": "CLOCK_PERIOD"})
    assert r["current"]["value"] == 25 and "never loosen" in r["rule"]
    r = post("param_info", {"design": "vision_block", "key": "NOT_A_KEY"})
    assert "error" in r and "R9" in r["rule"] or "LibreLane" in r["rule"]
    g = post("param_info", {"design": "vision_block"})
    assert {"synth", "constraints", "gpl", "rsz-gpl", "cts", "grt", "drt"} <= set(g["engines"]) and g["count"] > 100
    only = post("param_info", {"design": "vision_block", "engine": "cts"})
    assert list(only["engines"]) == ["cts"]


# ---------------------------------------------------------------- the copy
def test_dir_paths_are_rewritten_so_they_resolve(sandbox):
    """tiny_ai_core's config points into other designs (dir::../vision_all_lit/...): after the move they must still resolve."""
    for design in ("tiny_ai_core", "soc_kv_attn_n8", "vision_block", "kv_attn_n8"):
        prep = W.prepare_copy(design, "t1", {"CLOCK_PERIOD": 20})
        assert prep["missing"] == [], (design, prep["missing"])
        cfg = json.load(open(os.path.join(prep["dir"], "config.json")))
        assert cfg["CLOCK_PERIOD"] == 20
        for p in W._walk_dirs(cfg):
            assert os.path.exists(os.path.normpath(os.path.join(prep["dir"], p))), p
        assert not os.path.exists(os.path.join(prep["dir"], "runs")) and not os.path.exists(os.path.join(prep["dir"], "output"))
    cfg = json.load(open(os.path.join(sandbox["base"], "tiny_ai_core__t1", "config.json")))
    outside = [p for p in W._walk_dirs(cfg) if "vision_all_lit" in p]
    assert outside and all(p.startswith("../") and p.count("../") >= 3 for p in outside)      # relative to build/whatif-like location
    assert "rtl/tiny_ai_core.v" in list(W._walk_dirs(cfg))                                      # inside the design: unchanged
    assert os.path.isfile(os.path.join(sandbox["base"], "tiny_ai_core__t1", "pin_order.cfg"))


def test_prepare_copy_leaves_designs_alone(sandbox):
    files = [os.path.join(REPO, "designs", "vision_block", "config.json"), os.path.join(REPO, "designs", "vision_block", "rtl", "vision_block.v")]
    before = [sha(f) for f in files]
    W.prepare_copy("vision_block", "t2", {"PL_TARGET_DENSITY_PCT": 70})
    assert [sha(f) for f in files] == before


def test_copy_path_cannot_escape(sandbox):
    with pytest.raises(ValueError):
        W.prepare_copy("vision_block", "../../escape", {})
    with pytest.raises(ValueError):
        W.prepare_copy("vision_block", "Bad Tag", {})


# ---------------------------------------------------------------- gate
def test_whatif_run_first_call_starts_nothing(sandbox):
    r = post("whatif_run", {"design": "vision_block", "changes": {"CLOCK_PERIOD": 20}, "tag": "clk20"})
    assert r["needs_confirmation"] and r["started"] is False and re.fullmatch(r"[0-9a-f]{6}", r["confirm_id"])
    assert "yes, run" in r["say"] and r["plan"]["changes"] == {"CLOCK_PERIOD": 20}
    assert r["plan"]["copy"].endswith("whatif/vision_block__clk20") and "designs/vision_block/ is not modified" in r["plan"]["designs_untouched"]
    assert sandbox["started"] == [] and not os.path.exists(os.path.join(sandbox["base"], "vision_block__clk20"))


def test_whatif_run_confirm_creates_copy_and_job(sandbox):
    r = post("whatif_run", {"design": "vision_block", "changes": {"PL_TARGET_DENSITY_PCT": 70}, "tag": "dens70"})
    r2 = post("whatif_run", {"confirm_id": r["confirm_id"]})
    assert r2["job_id"] == "20260101_000000-01" and r2["changes"] == {"PL_TARGET_DENSITY_PCT": 70}
    cmd, label = sandbox["started"][0]
    assert cmd[:2] == ["bash", "scripts/flow/whatif_flow.sh"] and "--design" in cmd and "vision_block" in cmd and "dens70" in cmd
    d = os.path.join(sandbox["base"], "vision_block__dens70")
    assert json.load(open(os.path.join(d, "config.json")))["PL_TARGET_DENSITY_PCT"] == 70
    assert json.load(open(os.path.join(d, "whatif_meta.json")))["job_id"] == "20260101_000000-01"
    # single use
    assert "error" in post("whatif_run", {"confirm_id": r["confirm_id"]})
    # same tag again is refused
    assert "already exists" in post("whatif_run", {"design": "vision_block", "changes": {"CLOCK_PERIOD": 20}, "tag": "dens70"})["error"]


def test_whatif_run_refuses_blocked_changes_and_bad_inputs(sandbox):
    r = post("whatif_run", {"design": "vision_block", "changes": {"CLOCK_PERIOD": 30}, "tag": "x1"})
    assert "blocked by the HARD RULES" in r["error"] and r["blocked"][0]["rule"] == "R1-CLOCK" and "needs_confirmation" not in r
    assert "tag is required" in post("whatif_run", {"design": "vision_block", "changes": {"CLOCK_PERIOD": 20}})["error"]
    assert "tag is required" in post("whatif_run", {"design": "vision_block", "changes": {"CLOCK_PERIOD": 20}, "tag": "Bad Tag"})["error"]
    assert "changes is required" in post("whatif_run", {"design": "vision_block", "tag": "x2"})["error"]
    assert "unknown design" in post("whatif_run", {"design": "zzz", "changes": {"CLOCK_PERIOD": 20}, "tag": "x3"})["error"]
    w = post("whatif_run", {"design": "user_project_wrapper", "changes": {"CLOCK_PERIOD": 20}, "tag": "x4"})
    assert "wrapper" in w["error"]
    assert sandbox["started"] == []


def test_whatif_run_respects_a_running_flow(sandbox, monkeypatch):
    monkeypatch.setattr(W, "DOCKER_BUSY_HOOK", lambda: "oac_cap_kv_attn_n8_123")
    r = post("whatif_run", {"design": "vision_block", "changes": {"CLOCK_PERIOD": 20}, "tag": "busy"})
    assert "already running" in r["error"] and "oac_cap_kv_attn_n8_123" in r["error"]


def test_no_confirm_env_skips_the_gate(sandbox, monkeypatch):
    monkeypatch.setenv("CHIP_TOOLS_NO_CONFIRM", "1")
    r = post("whatif_run", {"design": "vision_block", "changes": {"CLOCK_PERIOD": 20}, "tag": "nog"})
    assert r["job_id"] and len(sandbox["started"]) == 1


# ---------------------------------------------------------------- results
def _fixture_run(sandbox, tag, mutate=None, exit_code=0, with_metrics=True):
    d = os.path.join(sandbox["base"], "vision_block__" + tag)
    os.makedirs(os.path.join(d, "runs", tag, "final"))
    meta = {"design": "vision_block", "tag": tag, "changes": {"CLOCK_PERIOD": 20}, "job_id": "20260101_000000-09"}
    json.dump(meta, open(os.path.join(d, "whatif_meta.json"), "w"))
    json.dump({"exit_code": exit_code, "wall_s_total": 66, "container_peak_mem_gb": 0.6}, open(os.path.join(d, "whatif_resources.json"), "w"))
    if with_metrics:
        m = json.load(open(os.path.join(REPO, "designs", "vision_block", "output", "metrics.json")))
        if mutate:
            mutate(m)
        json.dump(m, open(os.path.join(d, "runs", tag, "final", "metrics.json"), "w"))
    return d


def test_result_comparison_table_and_verdict(sandbox):
    def faster(m):
        for k in list(m):
            if k.startswith("timing__setup__ws"):
                m[k] = m[k] - 4.0
        m["design__instance__count__stdcell"] = m["design__instance__count__stdcell"] + 3
    _fixture_run(sandbox, "clk20", faster)
    r = post("whatif_result", {"tag": "clk20"})
    assert r["state"] == "done" and r["clean"] is True and r["violations"] == [] and r["designs_touched"] is False
    rows = {x["metric"]: x for x in r["table"]}
    assert rows["std cells"]["whatif"] == 300 and rows["std cells"]["committed"] == 297 and rows["std cells"]["delta"].startswith("+3")
    setup = [x for x in r["table"] if x["metric"].startswith("setup worst")][0]
    assert setup["delta"] == "-4.000"
    assert len(r["corners"]) == 9 and r["committed_run_still_current"] is True
    assert r["verdict"].startswith("CLEAN") and "setup slack -4.00 ns" in r["verdict"]
    assert "| metric | what-if | committed | delta |" in r["markdown"]


def test_result_flags_violations(sandbox):
    def bad(m):
        m["magic__drc_error__count"] = 2
        m["timing__hold__ws__corner:min_ff_n40C_1v95"] = -0.2
    _fixture_run(sandbox, "bad", bad)
    r = post("whatif_result", {"tag": "bad"})
    assert r["clean"] is False and r["verdict"].startswith("FAILS signoff") and any("magic__drc_error__count" in v for v in r["violations"])


def test_result_failed_and_unknown(sandbox):
    _fixture_run(sandbox, "dead", exit_code=1, with_metrics=False)
    r = post("whatif_result", {"tag": "dead"})
    assert r["state"] == "failed" and r["flow_exit_code"] == 1
    assert "no what-if copy" in post("whatif_result", {"tag": "never"})["error"]
    assert "give tag" in post("whatif_result", {})["error"]
    assert post("whatif_result", {"job_id": "20260101_000000-09"})["tag"] == "dead"


def test_compare_is_a_pure_function():
    m = json.load(open(os.path.join(REPO, "designs", "vision_block", "output", "metrics.json")))
    c = W.compare("vision_block", m, m, {"wall_s_total": 51, "container_peak_mem_gb": 0.69}, 51, 0.69)
    assert c["clean"] and "cells +0" in c["verdict"] and all(r["delta"] in ("", "+0", "+0.000", "+0.00", "+0.0000", "+0 (+0.0%)") for r in c["table"])


# ---------------------------------------------------------------- sweep
def test_sweep_plan_drops_blocked_values_and_gate(sandbox):
    r = post("whatif_sweep", {"design": "vision_block", "key": "CLOCK_PERIOD", "values": [30, 22, 20]})
    assert r["needs_confirmation"] and r["plan"]["values"] == [22, 20] and r["plan"]["blocked_values"][0]["rule"] == "R1-CLOCK"
    assert sandbox["started"] == []
    r2 = post("whatif_sweep", {"confirm_id": r["confirm_id"]})
    assert r2["job_id"] and r2["sweep_id"].startswith("sw") and len(r2["runs"]) == 2
    cmd = sandbox["started"][0][0]
    assert cmd[-2] == "sweep" and cmd[-1].endswith(r2["sweep_id"] + ".json")
    man = json.load(open(os.path.join(sandbox["base"], "sweeps", r2["sweep_id"] + ".json")))
    for run in man["runs"]:
        cfg = json.load(open(os.path.join(REPO, run["dir"] if os.path.isabs(run["dir"]) else run["dir"], "config.json")))
        assert cfg["CLOCK_PERIOD"] == run["value"]
    res = post("whatif_result", {"sweep_id": r2["sweep_id"]})
    assert res["key"] == "CLOCK_PERIOD" and res["rows"][-1][0] == "committed" and res["rows"][0][1] == "not finished"


def test_sweep_input_validation(sandbox):
    assert "2..6" in post("whatif_sweep", {"design": "vision_block", "key": "CLOCK_PERIOD", "values": [20]})["error"]
    r = post("whatif_sweep", {"design": "vision_block", "key": "CLOCK_PERIOD", "values": [26, 30]})
    assert "every value is blocked" in r["error"]
    assert "unknown design" in post("whatif_sweep", {"design": "q", "key": "CLOCK_PERIOD", "values": [20, 21]})["error"]


# ---------------------------------------------------------------- list / clean
def test_list_and_clean_only_touch_build_whatif(sandbox):
    _fixture_run(sandbox, "a1")
    _fixture_run(sandbox, "a2")
    patch = post("propose_change", {"design": "vision_block", "changes": {"CLOCK_PERIOD": 20}})["patch_file"]
    r = post("whatif_list")
    assert {x["tag"] for x in r["runs"]} == {"a1", "a2"} and all(x["state"] == "done" for x in r["runs"]) and len(r["patches"]) == 1
    assert "give a tag" in post("whatif_clean", {})["error"]
    c = post("whatif_clean", {"tag": "a1"})
    assert len(c["removed"]) == 1 and not os.path.exists(os.path.join(sandbox["base"], "vision_block__a1"))
    assert os.path.exists(os.path.join(sandbox["base"], "vision_block__a2"))
    post("whatif_clean", {"all": True})
    assert post("whatif_list")["runs"] == [] and post("whatif_list")["patches"] == []
    assert os.path.isfile(os.path.join(REPO, "designs", "vision_block", "config.json"))


def test_committed_design_files_unchanged_by_everything_above():
    """Last line of defence: the committed vision_block directory still matches git (no change from these tests)."""
    import subprocess
    r = subprocess.run(["git", "status", "--porcelain", "--", "designs/vision_block"], cwd=REPO, capture_output=True, text=True)
    if r.returncode == 0:
        assert r.stdout.strip() == ""
