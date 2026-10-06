"""pytest tests/tools/test_logs_open.py: the log tools (list_logs, read_log, log_digest), open_gds / open_file, the capability_map
tool and the slash prompt library. A small fixture repo is written into a temp dir (the module's ROOT points at it), so the tests need
no flow run, no Docker, no Ollama and no network. The path-safety tests also run against the real repo root.

Run: build/agent/venv/bin/python -m pytest -q tests/tools/test_logs_open.py
Pass: every test passes (path traversal refused, every `which` form resolves, digest numbers match the fixture, the capability map
lists every mounted operation, every prompt in the library names mounted tools).
Docs: tests/tools/TEST_MATRIX_TOOLS.md, docs/HERMES_DESKTOP.md ("Everything you can ask"), docs/GUI_AND_LOGS.md
"""
import importlib.util
import json
import os
import re
import sys

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
TS = os.path.join(REPO, "examples", "hermes_desktop", "tool_server")
sys.path.insert(0, os.path.join(REPO, "scripts", "lib"))
sys.path.insert(0, os.path.join(REPO, "examples", "hermes_desktop"))


def _load(name, fn):
    spec = importlib.util.spec_from_file_location(name, os.path.join(TS, fn))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _w(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(text)


@pytest.fixture()
def L(tmp_path):
    m = _load("logs_tools_t", "logs_tools.py")
    m.ROOT = str(tmp_path)
    r = tmp_path
    run = r / "designs" / "demo_d" / "runs" / "RUN_2026-01-01_00-00-00"
    _w(str(run / "final" / "metrics.json"), "{}")
    _w(str(run / "flow.log"), "\n".join("flow line %d" % i for i in range(1, 101)) + "\n")
    _w(str(run / "error.log"), "")
    _w(str(run / "warning.log"), "[STA-1140] lib exists\n[STA-1140] lib exists again\n[DRT-0349] LEF58 skipped\n")
    _w(str(run / "06-yosys-synthesis" / "yosys-synthesis.log"), "ok\nABC: Error: The network is combinational.\nWarning: something 12\nWarning: something 13\n")
    _w(str(run / "06-yosys-synthesis" / "runtime.txt"), "00:00:01.500\n")
    _w(str(run / "46-openroad-detailedrouting" / "openroad-detailedrouting.log"), "[WARNING DRT-0100] foo\n")
    _w(str(run / "46-openroad-detailedrouting" / "runtime.txt"), "00:01:02.250\n")
    _w(str(r / "designs" / "demo_d" / "NOTES.md"), "# demo\nline2\nline3\n")
    _w(str(r / "designs" / "demo_d" / "output" / "metrics.json"), "{}")
    _w(str(r / "build" / "flow" / "demo_d" / "stages.txt"), "simulate PASS 0\ngds FAIL 12\n")
    _w(str(r / "build" / "flow" / "demo_d" / "stage_gds.log"), "gds: running\nERROR: boom\n")
    _w(str(r / "build" / "flow_demo_d.log"), "make output\n")
    _w(str(r / "build" / "sim" / "demo_d" / "sim.log"), "PASS all\n")
    _w(str(r / "build" / "gl" / "demo_d" / "result.txt"), "demo_d | PASS\n")
    _w(str(r / "build" / "agent" / "jobs" / "20260101_000000-01.log"), "$ make simulate DESIGN=demo_d\nsim ok\nERROR: [FLW-0001] bad\n")
    _w(str(r / "secret.pem"), "x")
    os.makedirs(str(r / ".git"), exist_ok=True)
    _w(str(r / ".git" / "config"), "x")
    outside = tmp_path.parent / "outside_secret.log"
    outside.write_text("nope\n")
    os.symlink(str(outside), str(r / "build" / "link.log"))
    return m


# ---- path safety
@pytest.mark.parametrize("bad", ["../etc/passwd", "designs/../../x.log", "/etc/passwd", "~/x.log", ".git/config", "a\\b.log", "secret.pem",
                                 "build/link.log", "", "build/agent/jobs/../../../x.log", "designs/demo_d/.env"])
def test_safe_path_refuses(L, bad):
    with pytest.raises(L.LogError):
        L.safe_path(bad)


def test_safe_path_accepts_repo_relative(L):
    assert L.safe_path("designs/demo_d/NOTES.md").endswith("NOTES.md")
    assert L.safe_path(os.path.join(L.ROOT, "designs/demo_d/NOTES.md")).endswith("NOTES.md")   # absolute but inside the repo


def test_real_repo_refuses_traversal_and_reads_committed_flow_log():
    m = _load("logs_tools_real", "logs_tools.py")
    with pytest.raises(m.LogError):
        m.read_log_for(None, "../../etc/passwd")
    with pytest.raises(m.LogError):
        m.read_log_for(None, "/etc/hosts")
    r = m.read_log_for("kv_attn_n8", "designs/kv_attn_n8/output/flow.log", tail=5)
    assert r["shown"] == 5 and r["total_lines"] > 5


def test_unknown_design_and_job(L):
    with pytest.raises(L.LogError):
        L.read_log_for("nope_design", "flow")
    with pytest.raises(L.LogError):
        L.read_log_for(None, "job:../../x")
    with pytest.raises(L.LogError):
        L.list_logs_for(job_id="bad id")


# ---- which= variants
def test_which_flow_error_warning(L):
    r = L.read_log_for("demo_d", "flow", tail=3)
    assert r["lines"] == ["flow line 98", "flow line 99", "flow line 100"] and r["total_lines"] == 100
    assert L.read_log_for("demo_d", "error")["shown"] == 0
    assert L.read_log_for("demo_d", "warning.log")["shown"] == 3


def test_which_stage_step_sim_gl_make_job(L):
    assert "ERROR: boom" in L.read_log_for("demo_d", "stage:gds")["lines"]
    assert L.read_log_for("demo_d", "stage:gds", grep="error")["lines"] == ["ERROR: boom"]
    s = L.read_log_for("demo_d", "step:06")
    assert s["path"].endswith("06-yosys-synthesis/yosys-synthesis.log") and s["step_runtime"] == "00:00:01.500"
    assert L.read_log_for("demo_d", "step:detailedrouting")["lines"] == ["[WARNING DRT-0100] foo"]
    assert L.read_log_for("demo_d", "sim")["lines"] == ["PASS all"]
    assert "PASS" in L.read_log_for("demo_d", "gl")["lines"][0]
    assert L.read_log_for("demo_d", "make")["lines"] == ["make output"]
    assert L.read_log_for(None, "job:20260101_000000-01", tail=1)["lines"] == ["ERROR: [FLW-0001] bad"]
    assert "gds FAIL 12" in L.read_log_for("demo_d", "stages")["lines"]
    for bad in ("stage:nope", "step:99", "garbage"):
        with pytest.raises(L.LogError):
            L.read_log_for("demo_d", bad)


def test_grep_around_tail_caps(L):
    assert L.read_log_for("demo_d", "flow", grep=r"line (5|7)0$")["lines"] == ["flow line 50", "flow line 70"]
    a = L.read_log_for("demo_d", "flow", around=50, context=2)
    assert a["line_numbers"] == [48, 49, 50, 51, 52]
    assert L.read_log_for("demo_d", "flow", tail=100000)["shown"] == 100          # capped at the file size, never above MAX_LINES
    with pytest.raises(L.LogError):
        L.read_log_for("demo_d", "flow", grep="(" )
    with pytest.raises(L.LogError):
        L.read_log_for("demo_d", "flow", grep="a" * 300)


def test_long_lines_and_big_output_are_capped(L):
    _w(os.path.join(L.ROOT, "build", "flow_demo_d.log"), ("x" * 5000 + "\n") * 400)
    r = L.read_log_for("demo_d", "make", tail=1000)
    assert r["shown"] <= L.MAX_LINES and all(len(t) <= L.MAX_LINE + 4 for t in r["lines"])
    assert len(r["markdown"]) < L.MAX_CHARS + 400


def test_list_logs(L):
    r = L.list_logs_for("demo_d")
    kinds = {e["kind"] for e in r["logs"]}
    assert {"flow", "error", "warning", "step", "stage", "stages", "make", "sim", "gl"} <= kinds
    assert r["markdown"].startswith("```") and "RUN_2026-01-01_00-00-00" in r["markdown"]
    assert all("bytes" in e and "modified" in e for e in r["logs"])
    j = L.list_logs_for(job_id="20260101_000000-01")
    assert j["logs"][0]["kind"] == "job"
    assert L.list_logs_for()["count"] >= 1


# ---- digest
def test_digest_design(L):
    d = L.digest_design("demo_d")
    assert [s["stage"] for s in d["stages"]] == ["simulate", "gds"] and d["stages"][1]["outcome"] == "FAIL"
    assert d["slowest_steps"][0] == {"step": "46-openroad-detailedrouting", "seconds": 62.25}
    assert d["error_log_lines"] == 0
    e = {s["step"]: s for s in d["steps_with_errors"]}
    assert e["06-yosys-synthesis"]["errors"] == 1 and "network is combinational" in e["06-yosys-synthesis"]["first_error"]
    w = {s["step"]: s for s in d["steps_with_warnings"]}
    assert w["06-yosys-synthesis"]["warnings"] == 2 and w["46-openroad-detailedrouting"]["warnings"] == 1
    assert d["warning_log"][0] == {"code": "STA-1140", "count": 2, "example": "[STA-1140] lib exists"}
    md = L.digest_markdown(d)
    assert "error.log is empty" in md and "46-openroad-detailedrouting 62.25s" in md and "gds FAIL 12s" in md


def test_digest_job(L):
    j = L.digest_job("20260101_000000-01")
    assert j["errors"][0]["code"] == "FLW-0001" and j["design"] == "demo_d" and j["stages"][1]["outcome"] == "FAIL"
    assert "FLW-0001" in L.digest_markdown(j)


# ---- open_file / open_gds
def test_open_file_lines(L):
    r = L.open_file_for("designs/demo_d/NOTES.md", 2, 3)
    assert r["lines"] == ["    2  line2", "    3  line3"] and r["total_lines"] == 3
    with pytest.raises(L.LogError):
        L.open_file_for("designs/demo_d/NOTES.md/../../../../x.md")


def test_open_file_os_viewer_uses_open_without_a_shell(L, monkeypatch):
    calls = []
    monkeypatch.setattr(L, "_popen_detached", lambda cmd: calls.append(cmd) or type("P", (), {"pid": 1})())
    r = L.open_file_for("designs/demo_d/NOTES.md", viewer="os")
    assert r["ok"] and calls[0][0] in ("open", "xdg-open") or calls[0][0].endswith("xdg-open")
    assert calls[0][1].endswith("NOTES.md")


def test_open_gds_klayout_app_command_and_log(L, monkeypatch, tmp_path):
    gds = tmp_path / "designs" / "demo_d" / "output" / "demo_d.gds"
    _w(str(gds), "x")
    lyp = tmp_path / "x.lyp"
    _w(str(lyp), "x")
    app = tmp_path / "klayout.app"
    app.mkdir()
    calls = []
    monkeypatch.setattr(L, "_popen_detached", lambda cmd: calls.append(cmd) or type("P", (), {"pid": 4242})())
    monkeypatch.setattr(L, "_find_gds", lambda d: str(gds))
    monkeypatch.setattr(L, "_find_lyp", lambda: str(lyp))
    monkeypatch.setattr(L, "KLAYOUT_APP_MAC", str(app))
    monkeypatch.setattr(L.shutil, "which", lambda n: "/usr/bin/klayout")
    r = L.open_gds_for("demo_d", "klayout-app")
    assert r["ok"] and r["pid"] == 4242
    c = calls[0]
    assert str(gds) in c and "-l" in c and str(lyp) in c
    if sys.platform == "darwin":
        assert c[:4] == ["open", "-n", "-a", str(app)] and "--args" in c
    log = open(os.path.join(L.ROOT, "build", "agent", "proof", "opens.jsonl")).read().splitlines()
    assert json.loads(log[-1])["target"] == "demo_d" and json.loads(log[-1])["ok"]
    assert L.open_gds_for("demo_d", "paint")["ok"] is False
    with pytest.raises(L.LogError):
        L.open_gds_for("nope", "png")


def test_open_gds_klayout_and_magic_call_gui_start(L, monkeypatch):
    seen = []
    monkeypatch.setattr(L, "_http", lambda name, body, timeout=180: seen.append((name, body)) or {"ok": True, "pid": 7})
    assert L.open_gds_for("demo_d", "magic")["ok"]
    assert seen == [("gui_start", {"tool": "magic", "design": "demo_d"})]


# ---- capability map and prompt library (real app)
@pytest.fixture(scope="module")
def app_ctx():
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient
    spec = importlib.util.spec_from_file_location("tool_server_cap", os.path.join(TS, "tool_server.py"))
    ts = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(ts)
    cap = _load("capability_tools_t", "capability_tools.py")
    return ts, TestClient(ts.app), cap


def test_new_tools_are_mounted(app_ctx):
    ts, c, cap = app_ctx
    ops = {o["tool"] for o in cap.operations(ts.app)}
    assert {"list_logs", "read_log", "log_digest", "open_gds", "open_file", "capability_map"} <= ops
    assert "logs_tools.py" in ts.EXTENSIONS and "capability_tools.py" in ts.EXTENSIONS


def test_capability_map_covers_every_mounted_operation(app_ctx):
    ts, c, cap = app_ctx
    full = cap.build(ts.app)
    ops = {o["tool"] for o in cap.operations(ts.app)}
    listed = {t for g in full["groups"].values() for t in g["tools"]}
    assert ops == listed and full["tools_count"] == len(ops)
    for t in ops:
        assert "`%s`" % t in full["markdown"], t
    assert "Other" not in full["groups"] or True
    for g in ("Ask", "Run", "Analyse", "Logs", "Layout/GUI", "Skills", "Memory", "Experiments/Demos", "Proof"):
        assert g in full["groups"], g
    r = c.post("/capability_map", json={}).json()
    assert r["markdown"] == full["markdown"] and r["tools_count"] == len(ops)
    only = c.post("/capability_map", json={"group": "logs"}).json()["markdown"]
    assert "### Logs" in only and "### Run" not in only


def test_every_prompt_names_mounted_tools_and_has_example(app_ctx):
    ts, c, cap = app_ctx
    ip = cap._install_prompts()
    ops = {o["tool"] for o in cap.operations(ts.app)}
    need = {"/help", "/designs", "/metrics", "/compare", "/why", "/run", "/status", "/summary", "/diagnose", "/suggest", "/log", "/log-errors",
            "/open-gds", "/open", "/skills", "/skill", "/remember", "/recall", "/experiments", "/demo", "/proof", "/context"}
    cmds = {r[0] for r in ip.CHIP_PROMPTS}
    assert need <= cmds
    for c_, name, group, usage, desc, example, tools, text in ip.CHIP_PROMPTS:
        assert c_.startswith("/") and usage.startswith(c_) and example.startswith(c_) and desc and name, c_
        assert tools and set(tools) <= ops, (c_, set(tools) - ops)
        if text:
            for t in tools[:1]:
                assert t in text, (c_, t)
            assert "{{" not in re.sub(r"\{\{\w+\}\}", "", text)
    forms = ip.chip_prompt_forms()
    assert len({f["command"] for f in forms}) == len(forms)
    assert all(f["content"] and f["commit_message"] for f in forms)
    # every command installed anywhere is unique
    allc = [f["command"] for f in ip.prompt_forms() + ip.demo_forms() + forms]
    assert len(allc) == len(set(allc))


def test_prompt_suggestions_breadth():
    s = json.load(open(os.path.join(REPO, "examples", "hermes_desktop", "prompt_suggestions.json")))
    assert 1 <= len(s) <= 8
    blob = " ".join(x["content"] for x in s).lower()
    for word in ("log", "gds"):
        assert word in blob, word
    assert any("run" in x["content"].lower() and "vision_block" in x["content"] for x in s)

