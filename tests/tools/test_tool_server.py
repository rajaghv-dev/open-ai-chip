"""Tests for examples/hermes_desktop/tool_server (FastAPI TestClient; no Ollama, Claude or Docker needed).
Opt-in live test: CLAUDE_LIVE=1 build/agent/venv/bin/python -m pytest tests/tools/test_tool_server.py -k live

Run: build/agent/venv/bin/python -m pytest -q tests/tools/test_tool_server.py (also part of `make test`, section == tools)
Pass: every test passes or is skipped (opt-in tests need their env flag).
Docs: tests/tools/TEST_MATRIX_TOOLS.md, examples/hermes_desktop/README.md, docs/HERMES_DESKTOP.md
"""
import os
import re
import sys
import time

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "examples", "hermes_desktop", "tool_server"))
pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

import tool_server as ts  # noqa: E402

client = TestClient(ts.app)

EXPECTED_OPS = {"list_designs", "read_metrics", "compare_designs", "layout_summary", "layer_stats", "find_pins",
                "render_png", "signoff_summary", "classify_slew", "precheck_summary", "search_docs", "klayout_view",
                "list_skills", "get_skill", "run_make", "job_status", "job_list", "job_cancel", "claude_task", "health"}


def wait_job(jid, timeout=20):
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = client.post("/job_status", json={"job_id": jid}).json()
        if r["state"] != "running":
            return r
        time.sleep(0.1)
    raise AssertionError("job did not finish")


def test_openapi_operations():
    """Pins down: openapi operations."""
    spec = client.get("/openapi.json").json()
    ops = {}
    for path, item in spec["paths"].items():
        for method, op in item.items():
            ops[op["operationId"]] = (path, method)
    assert set(ops) == EXPECTED_OPS
    for name, (path, method) in ops.items():
        assert path == "/" + name and method == "post"
    assert "/img/{name}" not in spec["paths"]
    for op in (v["post"] for v in spec["paths"].values()):
        assert op.get("summary") and op.get("description")


def test_eda_tools():
    """Pins down: eda tools."""
    r = client.post("/list_designs", json={}).json()
    assert any(d["design"] == "vision_block" for d in r["designs"])
    r = client.post("/list_designs").json()          # empty body also fine
    assert "designs" in r
    r = client.post("/read_metrics", json={"design": "vision_block", "keys": ["design__instance__count__stdcell"]}).json()
    assert r["metrics"]["design__instance__count__stdcell"]["value"] > 0
    assert "error" in client.post("/read_metrics", json={"design": "nope"}).json()
    r = client.post("/compare_designs", json={"metric": "design__instance__count__stdcell",
                                              "designs": ["vision_block", "text_sentiment"]}).json()
    assert r["min"]["value"] <= r["max"]["value"]
    assert client.post("/read_metrics", json={}).status_code == 422


def test_search_docs():
    """Pins down: search docs."""
    r = client.post("/search_docs", json={"query": "how do I run a flow", "k": 2}).json()
    assert 1 <= len(r["hits"]) <= 2 and "file" in r["hits"][0]


def test_skills():
    """Pins down: skills."""
    r = client.post("/list_skills", json={}).json()
    names = {s["name"] for s in r["skills"]}
    assert {"harden-design", "soc-run", "add-tiny-engine"} <= names and len(names) == 6
    r = client.post("/get_skill", json={"name": "harden-design"}).json()
    assert r["body"].startswith("# ") and "description" in r
    assert "error" in client.post("/get_skill", json={"name": "../../CLAUDE"}).json()


def test_img_name_validation():
    """Pins down: img name validation."""
    for bad in ["..%2f..%2fCLAUDE.md", "x.txt", ".png", "a%2fb.png", "..png", "%2e%2e%2fMakefile"]:
        r = client.get("/img/" + bad)
        assert r.status_code in (400, 404), bad
    assert client.get("/img/does_not_exist.png").status_code == 404
    os.makedirs(ts.IMG_DIR, exist_ok=True)
    p = os.path.join(ts.IMG_DIR, "zz_test_probe.png")
    with open(p, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
    try:
        r = client.get("/img/zz_test_probe.png")
        assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    finally:
        os.remove(p)


def test_klayout_view_bad_design():
    """Pins down: klayout view bad design."""
    assert "error" in client.post("/klayout_view", json={"design": "nope"}).json()


def test_klayout_view_png():
    """Pins down: klayout view png."""
    if not (os.path.isfile(os.path.join(REPO, "designs", "kv_attn_n8", "output", "kv_attn_n8.gds"))
            or os.path.isdir(os.path.join(REPO, "build", "results", "kv_attn_n8"))):
        pytest.skip("no kv_attn_n8 GDS locally")
    r = client.post("/klayout_view", json={"design": "kv_attn_n8", "layers": ["met4"], "zoom": {"full": True}}).json()
    if "error" in r:
        pytest.skip("render unavailable: %s" % r["error"])
    assert r["png_url"].startswith("http://127.0.0.1:") and r["png_url"].endswith(".png")
    assert client.get("/img/" + r["png_url"].rsplit("/", 1)[1]).status_code == 200
    assert any("met4" in v for v in r["visible_layers"])


def test_run_make_allow_list():
    """Pins down: run make allow list."""
    for t in ["clean", "generate", "table", "precheck", "all-designs", "doctor; id", "$(id)", ""]:
        r = client.post("/run_make", json={"target": t}).json()
        assert "not allowed" in r["error"], t
    assert set(ts.MAKE_ALLOW) == set("doctor test simulate check gl gl-final gds flow-all views collect view soc-sim "
                                     "soc-kv adapter-test model-check check-generated test-full caravel-rtl "
                                     "caravel-gl".split())


def test_run_make_design_validation():
    """Pins down: run make design validation."""
    for d in ["nope", "vision_block; rm -rf /", "../x", "vision_block DESIGN=x", ""]:
        r = client.post("/run_make", json={"target": "simulate", "design": d}).json()
        assert "unknown design" in r["error"], d
    assert "vision_block" in ts.valid_designs() and len(ts.valid_designs()) >= 25


def test_job_lifecycle_and_caps(monkeypatch):
    """Pins down: job lifecycle and caps."""
    mgr = ts.JobManager()
    monkeypatch.setattr(ts, "JOBS", mgr)
    j, err = mgr.start("make", ["sh", "-c", "echo hello; seq 1 100"], "fake")
    assert err is None
    r = wait_job(j.id)
    assert r["state"] == "done" and r["rc"] == 0
    assert len(r["log_tail"]) <= ts.LOG_TAIL and r["log_tail"][-2].strip() == "100"
    j, _ = mgr.start("make", ["sh", "-c", "exit 3"], "fail")
    assert wait_job(j.id)["state"] == "failed"
    # physical lock, total cap, cancel
    p1, e1 = mgr.start("make", ["sleep", "30"], "p1", physical=True)
    p2, e2 = mgr.start("make", ["sleep", "30"], "p2", physical=True)
    assert e1 is None and p2 is None and "physical" in e2
    n1, e3 = mgr.start("make", ["sleep", "30"], "n1")
    assert e3 is None
    n2, e4 = mgr.start("make", ["sleep", "30"], "n2")
    assert n2 is None and "cap" in e4
    mgr.cancel(p1.id)
    mgr.cancel(n1.id)
    t0 = time.time()
    while (p1.state == "running" or n1.state == "running") and time.time() - t0 < 10:
        time.sleep(0.1)
    assert p1.state == "failed" and n1.state == "failed"
    # job endpoints on unknown id
    assert "error" in client.post("/job_status", json={"job_id": "nope"}).json()
    assert "error" in client.post("/job_cancel", json={"job_id": "nope"}).json()


def test_one_claude_job_at_a_time():
    """Pins down: one claude job at a time."""
    mgr = ts.JobManager()
    a, e = mgr.start("claude", ["sleep", "20"], "c1", claude=False)
    a.kind = "claude"
    b, e2 = mgr.start("claude", ["sleep", "1"], "c2", claude=True)
    assert b is None and "claude_task is already running" in e2
    mgr.cancel(a.id)


def test_claude_command_construction():
    """Pins down: claude command construction."""
    cmd = ts.build_claude_command(ts.build_claude_prompt("harden-design", "vision_block", "check status"), 12)
    assert cmd[1] == "-p" and "Use the harden-design skill." in cmd[2] and "Design: vision_block." in cmd[2]
    assert cmd[cmd.index("--output-format") + 1] == "stream-json" and "--verbose" in cmd
    assert cmd[cmd.index("--permission-mode") + 1] == "dontAsk"
    assert cmd[cmd.index("--max-turns") + 1] == "12"
    assert "--strict-mcp-config" in cmd and "--dangerously-skip-permissions" not in cmd
    dis = cmd[cmd.index("--disallowedTools") + 1].split(",")
    for t in ["Edit", "Write", "NotebookEdit", "Bash(git *)", "Bash(rm *)", "Bash(make clean*)"]:
        assert t in dis
    allowed = cmd[cmd.index("--allowedTools") + 1].split(",")
    assert not set(allowed) & {"Edit", "Write", "NotebookEdit"} and "Bash(make *)" in allowed
    tools = cmd[cmd.index("--tools") + 1].split(",")
    assert "Edit" not in tools and "Write" not in tools
    assert "must NOT" in cmd[cmd.index("--append-system-prompt") + 1]


def test_claude_task_validation_and_job(monkeypatch):
    """Pins down: claude task validation and job."""
    assert "error" in client.post("/claude_task", json={"instructions": "x", "skill": "nope"}).json()
    assert "error" in client.post("/claude_task", json={"instructions": "x", "design": "nope"}).json()
    # swap the real CLI for a fake that emits stream-json
    fake = os.path.join(ts.JOB_DIR, "fake_claude.sh")
    os.makedirs(ts.JOB_DIR, exist_ok=True)
    with open(fake, "w") as f:
        f.write("#!/bin/sh\n"
                "echo '{\"type\":\"system\",\"subtype\":\"init\",\"model\":\"m\",\"tools\":[1]}'\n"
                "echo '{\"type\":\"assistant\",\"message\":{\"content\":[{\"type\":\"tool_use\",\"name\":\"Read\",\"input\":{\"file_path\":\"a\"}}]}}'\n"
                "echo '{\"type\":\"assistant\",\"message\":{\"content\":[{\"type\":\"text\",\"text\":\"count is 297\"}]}}'\n"
                "echo '{\"type\":\"result\",\"is_error\":false,\"result\":\"count is 297\",\"num_turns\":2,\"total_cost_usd\":0.01,\"permission_denials\":[{\"tool_name\":\"Write\",\"tool_input\":{}}]}'\n")
    os.chmod(fake, 0o755)
    monkeypatch.setattr(ts, "CLAUDE_BIN", fake)
    monkeypatch.setattr(ts, "JOBS", ts.JobManager())
    r = client.post("/claude_task", json={"instructions": "do it", "skill": "soc-run"}).json()
    assert "--disallowedTools" in r["command"]          # exact command line is recorded
    s = wait_job(r["job_id"])
    text = "\n".join(s["log_tail"])
    assert s["state"] == "done" and "[tool_use] Read" in text and "[assistant] count is 297" in text
    assert "[permission_denied] Write" in text and "cost_usd=0.01" in text
    os.remove(fake)


def test_health():
    """Pins down: health."""
    r = client.post("/health", json={}).json()
    assert r["ok"] and r["skills"] == 6 and "ollama" in r and "docker" in r and "claude" in r


def test_cors():
    """Pins down: cors."""
    for origin in ["http://127.0.0.1:8080", "http://localhost:8080"]:
        r = client.options("/list_designs", headers={"Origin": origin, "Access-Control-Request-Method": "POST",
                                                      "Access-Control-Request-Headers": "content-type"})
        assert r.headers.get("access-control-allow-origin") == origin
        r = client.post("/list_designs", json={}, headers={"Origin": origin})
        assert r.headers.get("access-control-allow-origin") == origin
    r = client.post("/list_designs", json={}, headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in r.headers


def test_bind_is_localhost():
    """Pins down: bind is localhost."""
    src = open(os.path.join(REPO, "examples", "hermes_desktop", "tool_server", "tool_server.py")).read()
    assert ts.HOST == "127.0.0.1" and 'host=HOST' in src and not re.search(r'host\s*=\s*["\']0\.0\.0\.0', src)


@pytest.mark.skipif(os.environ.get("CLAUDE_LIVE") != "1", reason="opt-in: CLAUDE_LIVE=1 (uses the owner's Claude plan)")
def test_live_claude_task_cannot_edit():
    """Pins down: live claude task cannot edit."""
    probe = os.path.join(REPO, "build", "agent", "claude_edit_probe.txt")
    if os.path.exists(probe):
        os.remove(probe)
    r = client.post("/claude_task", json={"max_turns": 8, "instructions":
                    "Read designs/vision_block/output/metrics.json and report design__instance__count__stdcell. Then run "
                    "the Bash command: echo hi > build/agent/claude_edit_probe.txt and report what happened."}).json()
    s = wait_job(r["job_id"], timeout=240)
    assert s["state"] == "done" and "297" in "\n".join(s["log_tail"])
    assert not os.path.exists(probe)
