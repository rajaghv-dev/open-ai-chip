"""Tests for examples/hermes_desktop/tool_server/experiments_tools.py (list_experiments, run_experiment, experiment_result,
engine_pictures, list_demos, demo_steps), demo_defs.py, demos.py and the demo slash prompts. FastAPI TestClient and committed
logs/fixtures (tests/tools/fixtures/*.txt: console output of make soc-kv, soc-sim and adapter-test, paths made relative);
no Ollama, Docker or Claude needed, no job is ever started (the confirmation gate is checked, not passed).

Run: build/agent/venv/bin/python -m pytest -q tests/tools/test_experiments.py
Pass: every test passes.
Docs: tests/tools/TEST_MATRIX_TOOLS.md, docs/HERMES_DESKTOP.md (section "Experiments and demos")
"""
import importlib.util
import os
import re
import sys

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
HD = os.path.join(REPO, "examples", "hermes_desktop")
sys.path.insert(0, os.path.join(HD, "tool_server"))
sys.path.insert(0, HD)
pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

import tool_server as ts  # noqa: E402
import experiments_tools as X  # noqa: E402
import demo_defs  # noqa: E402

client = TestClient(ts.app)
FIX = os.path.join(REPO, "tests", "tools", "fixtures")


def fx(name):
    return open(os.path.join(FIX, name), encoding="utf-8").read()


@pytest.fixture(autouse=True)
def hook(monkeypatch, tmp_path):
    monkeypatch.setattr(X, "JOB_DIR", str(tmp_path / "jobs"))          # no real job log leaks into the committed-results tests
    monkeypatch.delenv("CHIP_TOOLS_NO_CONFIRM", raising=False)
    monkeypatch.setattr(X, "CALL_HOOK", lambda name, body: client.post("/" + name, json=body).json())


MODELS = {"list_experiments": X.ListReq, "run_experiment": X.RunExpReq, "experiment_result": X.ResultReq,
          "engine_pictures": X.PicReq, "demo_steps": X.DemoReq, "list_demos": X.Empty}


def post(name, body=None):
    """The tool function of THIS module instance (so tests can monkeypatch it); other tools go to the real app."""
    if name in MODELS:
        from pydantic import ValidationError
        try:
            return getattr(X, name)(MODELS[name](**(body or {})))
        except ValidationError as e:
            return {"error": str(e)}
    return client.post("/" + name, json=body or {}).json()


def test_tools_mounted_with_descriptions():
    """Pins down: the six tools are served by the real tool server with operationId == name and a description."""
    spec = client.get("/openapi.json").json()
    ops = {op["operationId"]: op for item in spec["paths"].values() for op in item.values()}
    for n in ("list_experiments", "run_experiment", "experiment_result", "engine_pictures", "list_demos", "demo_steps"):
        assert n in ops and ops[n].get("description"), n


def test_catalog_covers_all_designs_and_system_targets():
    """Pins down: every Makefile design is runnable via the per-design experiments, and every system target has an entry."""
    cat = X.build_catalog()
    ids = [e["id"] for e in cat]
    assert len(ids) == len(set(ids))
    alld = set(ts.valid_designs())
    assert len(alld) == 25
    for i in ("flow-all", "simulate", "check", "gl", "gl-final"):
        e = next(e for e in cat if e["id"] == i)
        assert set(e["designs"]) == alld
    fam = set()
    for e in cat:
        if e.get("family"):
            fam |= set(e["designs"])
    assert fam == alld, "families must partition all 25 designs"
    for i in ("soc-sim", "soc-kv", "adapter-test", "caravel-rtl", "caravel-gl", "precheck", "test-full", "model-check",
              "check-generated", "openroad-views", "heatmaps-live", "klayout-live", "magic-live", "precision", "kv-cache"):
        assert i in ids, i
    assert {e["group"] for e in cat} == {"design", "family", "system", "pictures"}


def test_catalog_entries_complete_and_docs_exist():
    """Pins down: every entry has the fields the model shows, the docs link exists, make targets are allow-listed."""
    for e in X.build_catalog():
        for k in ("id", "title", "shows", "command", "expected_time", "physical_flow", "docs"):
            assert e[k] not in (None, ""), (e["id"], k)
        assert os.path.exists(os.path.join(REPO, e["docs"])), (e["id"], e["docs"])
        if e["kind"] == "make":
            assert e["target"] in ts.MAKE_ALLOW, e["id"]
            assert e["physical_flow"] == (e["target"] in ts.PHYSICAL), e["id"]
        if e["kind"] == "terminal":
            assert e["target"] not in ts.MAKE_ALLOW or e["id"] != "precheck"


def test_list_experiments_groups():
    r = post("list_experiments")
    assert r["count"] == len(X.build_catalog()) >= 25
    assert post("list_experiments", {"group": "system"})["count"] == 9
    assert "error" in post("list_experiments", {"group": "nope"})
    flow = next(e for e in r["experiments"] if e["id"] == "flow-all")
    assert flow["physical_flow"] is True and len(flow["designs"]) == 25


def test_run_experiment_returns_confirm_and_starts_nothing():
    """Pins down: the gate is respected: a run returns needs_confirmation and no job exists afterwards."""
    before = post("job_list")["jobs"]
    r = post("run_experiment", {"id": "soc-kv"})
    assert r["needs_confirmation"] is True and r["started"] is False and re.match(r"^[0-9a-f]{6}$", r["confirm_id"])
    assert r["will_run"] == "make soc-kv" and "yes, run" in r["say"] and "run_make" in r["next"]
    r = post("run_experiment", {"id": "flow-all", "design": "vision_block"})
    assert r["will_run"] == "make flow-all DESIGN=vision_block" and r["physical_flow"] is True
    assert post("job_list")["jobs"] == before


def test_run_experiment_honest_refusals():
    r = post("run_experiment", {"id": "simulate"})
    assert "error" in r and "vision_block" in r["designs"]                      # needs a design
    assert "error" in post("run_experiment", {"id": "simulate", "design": "nope"})
    assert "error" in post("run_experiment", {"id": "precision", "design": "vision_block"})   # not a member
    assert "error" in post("run_experiment", {"id": "does-not-exist"})
    r = post("run_experiment", {"id": "precheck"})
    assert r["runnable_here"] is False and "make precheck" in r["run_in_terminal"]
    r = post("run_experiment", {"id": "klayout-live"})
    assert [s["tool"] for s in r["tool_steps"]][0] == "gui_start"
    assert "error" in post("run_experiment", {"id": "soc-kv", "confirm_id": "abcdef"})      # unknown id: refused by the gate


def test_openroad_views_has_own_gate(monkeypatch):
    started = []
    monkeypatch.setattr(X, "_start_render", lambda d: started.append(d) or {"state": "running", "log": "x"})
    r = post("run_experiment", {"id": "openroad-views", "design": "kv_attn_n8"})
    assert r["needs_confirmation"] and not started
    assert "error" in post("run_experiment", {"id": "openroad-views", "design": "kv_attn_n8", "confirm_id": "000000"})
    r2 = post("run_experiment", {"id": "openroad-views", "confirm_id": r["confirm_id"]})
    assert started == ["kv_attn_n8"] and r2["state"] == "running"
    assert "error" in post("run_experiment", {"id": "openroad-views", "confirm_id": r["confirm_id"]})   # single use


def test_parse_soc_kv_console_and_readme():
    """Pins down: prefill/decode tables parse from a real console log and from the committed README copy, and agree."""
    a = X.parse_soc_kv(fx("soc_kv_console.txt"))
    b = X.parse_soc_kv(open(os.path.join(REPO, "firmware", "README.md"), encoding="utf-8").read())
    assert len(a["prefill"]) == 7 and len(a["decode"]) == 8 and a["pass"] and a["baseline_roundtrip"] == 328
    assert a["prefill"] == b["prefill"] and a["decode"] == b["decode"]
    assert a["prefill"][0]["per_token"] == 328.0 and a["prefill"][-1]["per_token"] == 90.6
    assert all(r["roundtrip"] == 670.0 and r["read"] == 502.0 for r in a["decode"])


def test_parse_soc_sim_and_adapter_and_precheck():
    p = X.parse_soc_sim(fx("soc_sim_console.txt"))
    assert [r["mode"] for r in p["rows"]] == ["vision_all_lit", "vision_block", "text_sentiment"] and p["pass"]
    vb = p["rows"][1]
    assert vb["accel_roundtrip"] == 728.0 and vb["sw_over_accel"] == 1.8 and vb["bus_share_pct"] == 89.1
    a = X.parse_adapter(fx("adapter_console.txt"))
    assert len(a["rows"]) == 14 and a["engines_pass"] == 14 and not a["fail"]
    c = X.parse_precheck(open(os.path.join(REPO, "precheck", "results", "summary.tsv")).read())
    assert c["total"] == 14 and c["passed"] == 14


def test_result_tools_on_committed_data():
    r = post("experiment_result", {"id": "soc-kv"})
    assert "committed" in r["source"] and "328.0" in r["prefill_table"] and "bus-bound" in r["lesson"]
    r = post("experiment_result", {"id": "soc-sim"})
    assert "vision_block" in r["table"] and "bus" in r["lesson"]
    r = post("experiment_result", {"id": "precheck"})
    assert r["summary"] == "14/14 checks PASS"
    r = post("experiment_result", {"id": "precision"})
    for f in ("bin", "tern", "int4", "int8", "fp8", "fp16", "bf16"):
        assert "| %s |" % f in r["table"]
    assert "94.15" in r["table"] and "8.1x" in r["table"]                      # accuracy from the doc, area from metrics.json
    r = post("experiment_result", {"id": "kv-cache"})
    assert "kv_attn_n8_int4" in r["table"] and "| 256 | 96 |" in r["table"]
    r = post("experiment_result", {"id": "tiny-engines"})
    assert r["table"].count("\n") == 4 and "vision_block" in r["table"]
    assert "error" in post("experiment_result", {"id": "nonsense"})
    assert "error" in post("experiment_result", {"id": "soc-kv", "job_id": "no_such_job"})
    assert "error" in post("experiment_result", {"id": "caravel-rtl"})          # no finished job: honest message


def test_result_from_job_log(tmp_path, monkeypatch):
    """Pins down: a finished job's log is parsed in preference to the committed copy."""
    tmp_path = tmp_path / "jl"
    tmp_path.mkdir()
    monkeypatch.setattr(X, "JOB_DIR", str(tmp_path))
    (tmp_path / "j1.log").write_text("$ make soc-kv\n" + fx("soc_kv_console.txt"))
    r = post("experiment_result", {"id": "soc-kv", "job_id": "j1"})
    assert "job j1" in r["source"] and len(r["decode"]) == 8
    (tmp_path / "j2.log").write_text("$ make soc-kv\nstill running\n")
    assert "error" in post("experiment_result", {"id": "soc-kv", "job_id": "j2"})
    (tmp_path / "j3.log").write_text("$ make adapter-test\n" + fx("adapter_console.txt"))
    assert post("experiment_result", {"id": "adapter-test", "job_id": "j3"})["engines_pass"] == 14


def test_design_result_uses_run_summary():
    r = post("experiment_result", {"id": "flow-all", "design": "vision_block"})
    assert r["design"] == "vision_block" and ("run_summary" in r or "committed_key_numbers" in r)
    assert "error" in post("experiment_result", {"id": "flow-all"})


def test_engine_pictures():
    r = post("engine_pictures", {"design": "kv_attn_n8", "view": "congestion"})
    assert r["png_url"].endswith("/img/engine_kv_attn_n8_congestion.png") and r["markdown"].startswith("![")
    assert r["numbers_from_metrics_json"].get("global_route__wirelength") == 54889
    assert client.get("/img/engine_kv_attn_n8_congestion.png").status_code == 200
    assert "ir" in post("engine_pictures", {"design": "kv_attn_n8"})["views"]
    assert "error" in post("engine_pictures", {"design": "kv_attn_n8", "view": "zzz"})
    assert "error" in post("engine_pictures", {"design": "nope"})


def test_demo_definitions_reference_real_tools_and_experiments():
    """Pins down: every demo step names a served tool (or the known ones), existing experiment ids and valid designs."""
    spec = client.get("/openapi.json").json()
    served = {op["operationId"] for item in spec["paths"].values() for op in item.values()}
    cat = {e["id"]: e for e in X.build_catalog()}
    assert [d["num"] for d in demo_defs.DEMOS] == [1, 2, 3, 4, 5, 6, 7, 8]
    assert [d["name"] for d in demo_defs.DEMOS] == ["precision", "kv", "rtl2gds", "int4", "heatmaps", "soc", "gui", "proof"]
    for d in demo_defs.DEMOS:
        assert d["blurb"] and d["minutes"] > 0 and os.path.exists(os.path.join(REPO, d["docs"]))
        if d.get("runner"):
            continue
        assert d["steps"]
        for s in d["steps"]:
            assert s["tool"] in served, (d["name"], s["tool"])
            assert s["say"] and s["ask"] and s["look"]
            a = s["args"]
            if s["tool"] in ("run_experiment", "experiment_result"):
                assert a["id"] in cat, a
            if "design" in a:
                assert a["design"] in ts.valid_designs()
    assert [d["name"] for d in demo_defs.DEMOS if d["physical"]] == ["rtl2gds"]
    for d in demo_defs.DEMOS:
        assert d["name"] == "rtl2gds" or d["minutes"] <= 2.0


def test_demo_lookup_by_number_name_and_slash():
    for k in ("2", 2, "kv", "KV", "/demo-kv", "demo-kv"):
        assert demo_defs.find(k)["name"] == "kv"
    assert demo_defs.find("9") is None and demo_defs.find("") is None
    r = post("demo_steps", {"name": "2"})
    assert r["name"] == "kv" and r["steps"][0]["needs_confirmation"] and r["steps"][0]["tool"] == "run_experiment"
    assert "error" in post("demo_steps", {"name": "zzz"})
    assert post("demo_steps", {"name": "gui"})["run_in_terminal"].endswith("gui_demo.py")
    dl = post("list_demos")["demos"]
    assert [d["number"] for d in dl] == [1, 2, 3, 4, 5, 6, 7, 8]
    assert [d["name"] for d in dl if d["physical_flow"]] == ["rtl2gds"]


def test_demos_runner_menu_and_unknown(capsys):
    spec = importlib.util.spec_from_file_location("demos_t", os.path.join(HD, "demos.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    assert m.main([]) == 0
    out = capsys.readouterr().out
    assert "1  precision" in out and "7  gui" in out
    assert m.main(["--list"]) == 0
    assert m.main(["99"]) == 2
    assert "unknown demo" in capsys.readouterr().err


def test_demo_prompts_cover_every_demo():
    import install_prompts as IP
    cmds = [f["command"] for f in IP.demo_forms()]
    assert cmds[:2] == ["/experiments", "/demo"]
    for d in demo_defs.DEMOS:
        assert "/demo-" + d["name"] in cmds
    texts = {f["command"]: f["content"] for f in IP.demo_forms()}
    assert "list_experiments" in texts["/experiments"] and "list_demos" in texts["/demo"]
    assert "demo_steps" in texts["/demo-kv"] and "yes, run" in texts["/demo-kv"]
    assert [f["command"] for f in IP.prompt_forms()][:1] == ["/harden"]         # skill prompts untouched


def test_start_screen_suggestions_include_demos():
    import json
    s = json.load(open(os.path.join(HD, "prompt_suggestions.json")))
    assert 4 <= len(s) <= 8      # the start screen shows breadth (queries, run, logs, GUI), not only demos


def test_no_home_paths_in_new_files():
    files = [os.path.join(HD, "tool_server", "experiments_tools.py"), os.path.join(HD, "demos.py"), os.path.join(HD, "demo_defs.py"),
             __file__] + [os.path.join(FIX, f) for f in os.listdir(FIX)]
    for f in files:
        assert "/Users/" not in open(f, encoding="utf-8").read().replace('"/Users/" not in', ""), f


def test_run_make_accepts_pending_id_in_target():
    """Pins down: 'yes, run <id>' misrouted into `target` still needs an issued id (a made-up one is refused, nothing starts)."""
    before = post("job_list")["jobs"]
    r = post("run_make", {"target": "abcdef"})
    assert "error" in r
    assert post("job_list")["jobs"] == before


def test_run_experiment_ignores_placeholder_design():
    """Pins down: a fixed experiment ignores a stray design ('-' from the 8B model) and still returns the confirm step."""
    for d in ("-", "vision_block", "none"):
        r = post("run_experiment", {"id": "soc-kv", "design": d})
        assert r["needs_confirmation"] and r["will_run"] == "make soc-kv"
    r = post("run_experiment", {"id": "simulate", "design": "-"})
    assert "error" in r and "designs" in r
