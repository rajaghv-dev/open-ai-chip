"""pytest tests/tools/test_proof.py: the proof features of the Hermes desktop agent. Call log (path, sha256, git blob, chat id),
citations from results, the receipt filter (builds the block from a stub log, never raises, edits content and output, emits
sources), proof_local (parsers on fixtures, verdict), show_context, the memory filter's record, demo 8 definition and prompt,
the desktop app helpers. No Ollama, no Open WebUI, no network: stub data and a temporary proof directory.

Run: build/agent/venv/bin/python -m pytest -q tests/tools/test_proof.py
Pass: every test passes.
Docs: tests/tools/TEST_MATRIX_TOOLS.md, docs/HERMES_DESKTOP.md (section "Proof: local, repo, context")
"""
import asyncio
import hashlib
import importlib.util
import json
import os
import subprocess
import sys

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
HD = os.path.join(REPO, "examples", "hermes_desktop")
sys.path.insert(0, os.path.join(REPO, "scripts", "lib"))
sys.path.insert(0, HD)


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


@pytest.fixture()
def pt(tmp_path, monkeypatch):
    m = load("proof_tools_t", os.path.join(HD, "tool_server", "proof_tools.py"))
    monkeypatch.setattr(m, "PROOF_DIR", str(tmp_path / "proof"))
    yield m
    m.uninstall_open_tracking()


@pytest.fixture(scope="module")
def rf():
    return load("receipt_filter_t", os.path.join(HD, "receipt_filter.py"))


def served_app(pt):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()

    @app.post("/read_readme")
    def read_readme():
        with open(os.path.join(REPO, "README.md"), encoding="utf-8") as f:
            return {"first": f.readline().strip(), "file": "docs/RESULTS.md", "lines": "20-21"}

    app.include_router(pt.router)
    pt.install(app)
    return TestClient(app)


# ---------------------------------------------------------------- call log
def test_call_log_records_path_sha_blob_and_chat_id(pt):
    """Pins down: a tool call that opens a repo file is logged with its repo-relative path, sha256, git blob id, chat and
    message id from the Open WebUI headers, result size, and written to calls.jsonl."""
    c = served_app(pt)
    r = c.post("/read_readme", json={}, headers={"X-OpenWebUI-Chat-Id": "chat-1", "X-OpenWebUI-Message-Id": "msg-9"})
    assert r.status_code == 200
    rec = [x for x in pt.CALLS if x["tool"] == "read_readme"][-1]
    assert rec["chat_id"] == "chat-1" and rec["message_id"] == "msg-9" and rec["result_bytes"] == len(r.content)
    f = next(x for x in rec["files"] if x["path"] == "README.md")
    data = open(os.path.join(REPO, "README.md"), "rb").read()
    assert f["sha256"] == hashlib.sha256(data).hexdigest() and f["bytes"] == len(data)
    assert f["git"]["blob"] == pt.git_blob_id(data)
    if f["git"]["tracked"]:
        assert f["git"]["blob"] == subprocess.run(["git", "-C", REPO, "hash-object", "README.md"], capture_output=True, text=True).stdout.strip()
    assert rec["commit"] == pt.git_info()["commit"]
    line = json.loads(open(os.path.join(pt.PROOF_DIR, "calls.jsonl")).read().splitlines()[-1])
    assert line["tool"] == "read_readme" and line["files"][0]["path"] == "README.md"
    assert {"path": "docs/RESULTS.md", "lines": "20-21", "commit": rec["commit"][:7] if rec["commit"] else None} in rec["citations"]


def test_hidden_endpoints_are_not_logged_and_internal_calls_flagged(pt):
    c = served_app(pt)
    c.post("/proof_record", json={"chat_id": "x", "part": "turn", "data": {}})
    c.post("/proof_turn", json={})
    assert not [r for r in pt.CALLS if r["tool"] in ("proof_record", "proof_turn")]
    c.post("/call_log", json={})
    assert [r for r in pt.CALLS if r["tool"] == "call_log"][-1]["internal"] is True


def test_file_provenance_scope(pt, tmp_path):
    assert pt.file_provenance(os.path.join(REPO, "README.md"))["kind"] == "repo" or pt.git_info()["commit"] is None
    assert pt.file_provenance(str(tmp_path / "nope.txt")) is None
    assert pt.file_provenance("/etc/hosts") is None                                  # outside the repo
    assert pt.file_provenance(os.path.join(REPO, "build", "webui", "x.db")) is None  # noise dir


def test_extract_citations_and_span(pt):
    res = {"hits": [{"file": "docs/A.md", "lines": "98-112,113-116"}, {"file": "docs/B.md", "lines": "7"}],
           "x": {"source": "designs/d/NOTES.md", "lines": "224"}}
    assert pt.extract_citations(res) == [{"path": "docs/A.md", "lines": "98-116"}, {"path": "docs/B.md", "lines": "7"},
                                         {"path": "designs/d/NOTES.md", "lines": "224"}]
    assert pt.extract_citations({"source": "tool", "lines": ""}) == []
    assert "Halving the cache bits" in pt.excerpt("designs/kv_attn_n8_int4/NOTES.md", "224")


def test_calls_filter_by_message_chat_and_time(pt):
    base = dict(args="{}", status=200, size=1, ms=1, paths=set(), result=None)
    pt.record_call("a", chat_id="c1", message_id="m1", ts=100.0, **base)
    pt.record_call("b", chat_id="c1", message_id="m2", ts=200.0, **base)
    pt.record_call("c", chat_id="c2", message_id="m3", ts=300.0, **base)
    assert [r["tool"] for r in pt.calls(message_id="m2")] == ["b"]
    assert [r["tool"] for r in pt.calls(chat_id="c1", since_ts=150)] == ["b"]
    assert [r["tool"] for r in pt.calls(since_ts=250)] == ["c"]


# ---------------------------------------------------------------- receipt filter
STUB = {"model": {"base": "hermes3:8b", "digest": "4f6b83f30b62", "endpoint": "127.0.0.1:11434"},
        "repo": {"commit": "811c87d9fb380de5844d2987703f6528849c3bb4", "dirty": True, "dirty_count": 3},
        "calls": [{"tool": "read_metrics", "files": [{"path": "designs/kv_attn_n8/output/metrics.json", "sha256": "5ebfd66cd327" + "0" * 52, "clean": True}],
                   "citations": []},
                  {"tool": "search_docs", "files": [{"path": "docs/LLM_INFERENCE.md", "sha256": "ab" * 32, "clean": False}],
                   "citations": [{"path": "docs/LLM_INFERENCE.md", "lines": "98-116"}]}],
        "context": {"system": {"tokens_est": 1468}, "memory": {"entries": 7, "recorded": True, "sha256": "97b7d4e8" + "0" * 56},
                    "master_prompt": {"sha256": "5d0efe38" + "0" * 56}},
        "sources": [{"source": {"name": "docs/LLM_INFERENCE.md:98-116 @ 811c87d"}, "document": ["x"], "metadata": [{"source": "docs/LLM_INFERENCE.md"}]}]}


def test_receipt_built_from_stub_log(rf):
    """Pins down: the receipt names model digest, endpoint, commit and dirty state, tools with files and short sha256, passages,
    context and speed (from Ollama eval stats)."""
    t = rf.build_receipt(STUB, {"response_token/s": 44.59, "total_duration": 7_070_090_500})
    assert t.startswith("---\n**Receipt**")
    for needle in ("hermes3:8b digest 4f6b83f30b62", "127.0.0.1:11434", "commit 811c87d (dirty: 3 changed files)", "read_metrics, search_docs",
                   "designs/kv_attn_n8/output/metrics.json sha256 5ebfd66c", "docs/LLM_INFERENCE.md:98-116", "master prompt sha256 5d0efe38",
                   "~1468 tokens", "memory digest 7 entries sha256 97b7d4e8", "44.6 tokens/s", "7.1 s", "differs from the committed"):
        assert needle in t, needle
    none = rf.build_receipt(dict(STUB, calls=[]))
    assert "Tools called: none" in none and "Speed" not in none


def test_receipt_filter_outlet_appends_edits_output_and_emits_sources(rf, monkeypatch):
    f = rf.Filter()
    seen = []
    monkeypatch.setattr(f, "_post", lambda path, body: seen.append((path, body)) or STUB)
    msg = {"id": "m1", "role": "assistant", "content": "answer", "usage": {"eval_count": 100, "eval_duration": 2_000_000_000},
           "output": [{"type": "message", "content": [{"type": "output_text", "text": "answer"}]}]}
    body = {"model": "hermes-chip-agent", "id": "m1", "chat_id": "c1", "messages": [{"role": "user", "content": "q"}, msg]}
    events = []

    async def emit(e):
        events.append(e)

    out = asyncio.run(f.outlet(body, __event_emitter__=emit, __metadata__={"chat_id": "c1", "system_prompt": "SYS\n[agent memory digest]\nx"}))
    assert out["messages"][1]["content"].startswith("answer\n\n---\n**Receipt**")
    assert out["messages"][1]["output"][0]["content"][0]["text"].count("50.0 tokens/s") == 1
    assert events and events[0]["type"] == "source" and events[0]["data"]["metadata"][0]["source"] == "docs/LLM_INFERENCE.md"
    assert seen[0][0] == "/proof_record" and seen[0][1]["data"]["text"] == "SYS" and seen[1][0] == "/proof_turn" and seen[1][1]["chat_id"] == "c1"
    again = asyncio.run(f.outlet(out, __metadata__={"chat_id": "c1"}))              # idempotent
    assert again["messages"][1]["content"].count("**Receipt**") == 1


def test_receipt_filter_never_raises_and_leaves_reply_unchanged_when_server_down(rf):
    f = rf.Filter()
    f.valves.tool_server_url = "http://127.0.0.1:9"        # nothing listens
    f.valves.timeout_s = 0.3
    body = {"id": "m1", "messages": [{"role": "assistant", "id": "m1", "content": "answer"}]}
    assert asyncio.run(f.outlet(json.loads(json.dumps(body))))["messages"][0]["content"] == "answer"
    assert f.inlet({"messages": [{"role": "user", "content": "q"}]})["messages"][0]["content"] == "q"
    assert asyncio.run(f.outlet({"weird": 1})) == {"weird": 1}


def test_inlet_strips_old_receipts_from_history(rf, monkeypatch):
    f = rf.Filter()
    monkeypatch.setattr(f, "_post", lambda *a: {})
    hist = {"messages": [{"role": "user", "content": "q"},
                         {"role": "assistant", "content": "a\n\n---\n**Receipt** (x)\n- Model: y",
                          "output": [{"type": "message", "content": [{"type": "output_text", "text": "a\n\n---\n**Receipt** (x)\n- Model: y"}]}]}]}
    out = f.inlet(hist, __metadata__={"chat_id": "c"})
    assert out["messages"][1]["content"] == "a" and out["messages"][1]["output"][0]["content"][0]["text"] == "a"


def test_memory_filter_records_what_it_injected(monkeypatch):
    mf = load("memory_filter_t", os.path.join(HD, "memory_filter.py"))
    f = mf.Filter()
    digest = "MEMORY (local notes; newest first):\n- [a] 2026-10-07: n1\n- [b] 2026-10-07: n2\nLAST RUNS:\n- 2026-10-07 00:19 design=- | cmd=x\n"
    sent = []

    class R:
        def read(self):
            return b"{}"

    monkeypatch.setattr(f, "_digest", lambda: digest)
    monkeypatch.setattr(mf.urllib.request, "urlopen", lambda req, timeout=0: sent.append((req.full_url, json.loads(req.data))) or R())
    body = f.inlet({"messages": [{"role": "user", "content": "q"}]}, __metadata__={"chat_id": "c7"})
    assert body["messages"][0]["role"] == "system" and "[agent memory digest]" in body["messages"][0]["content"]
    url, rec = sent[0]
    assert url.endswith("/proof_record") and rec["chat_id"] == "c7" and rec["part"] == "memory"
    assert rec["data"]["notes"] == 2 and rec["data"]["runs"] == 1 and rec["data"]["sha256"] == hashlib.sha256(digest.encode()).hexdigest()


# ---------------------------------------------------------------- proof_local
LISTEN = """COMMAND     PID      USER   FD   TYPE             DEVICE SIZE/OFF NODE NAME
ollama     2411 raja-7384    3u  IPv4 0xa7c3a153baa54a09      0t0  TCP 127.0.0.1:11434 (LISTEN)
Python    34740 raja-7384    6u  IPv4 0x85fd69a7fb524035      0t0  TCP 127.0.0.1:8770 (LISTEN)
Python    34750 raja-7384   22u  IPv4 0xcc7ae103ec035005      0t0  TCP 127.0.0.1:8080 (LISTEN)
rapportd   1364 raja-7384   11u  IPv4 0xd929f9850b120686      0t0  TCP *:55833 (LISTEN)
"""
ESTAB = """COMMAND   PID      USER   FD   TYPE             DEVICE SIZE/OFF NODE NAME
Python  34750 raja-7384   30u  IPv4 0x1      0t0  TCP 127.0.0.1:8080->127.0.0.1:50000 (ESTABLISHED)
Safari    999 raja-7384   30u  IPv4 0x2      0t0  TCP 192.168.1.5:50001->17.253.1.1:443 (ESTABLISHED)
"""
OLLAMA_PS = """NAME          ID              SIZE      PROCESSOR    CONTEXT    UNTIL
hermes3:8b    4f6b83f30b62    6.0 GB    100% GPU     8192       3 minutes from now
"""
PS_ENV = ("34750 ttys000 0:09.1 /usr/bin/python open-webui serve OFFLINE_MODE=True HF_HUB_OFFLINE=1 ANONYMIZED_TELEMETRY=false "
          "DO_NOT_TRACK=true SCARF_NO_ANALYTICS=true ENABLE_VERSION_UPDATE_CHECK=False ENABLE_COMMUNITY_SHARING=False "
          "ENABLE_OPENAI_API=False OLLAMA_BASE_URL=http://127.0.0.1:11434 HOST=127.0.0.1")


def fx(**kw):
    d = {"lsof_listen": LISTEN, "lsof_established": ESTAB, "ollama_ps": OLLAMA_PS, "ps_env": PS_ENV, "audit": {"checks": 26, "drift": 0},
         "roles": {2411: "ollama", 34740: "tool server", 34750: "Open WebUI"}}
    d.update(kw)
    return d


def test_proof_local_parsers_on_fixtures(pt):
    ls = pt.parse_lsof_listen(LISTEN)
    assert [(l["pid"], l["bind"], l["port"]) for l in ls][:3] == [(2411, "127.0.0.1", 11434), (34740, "127.0.0.1", 8770), (34750, "127.0.0.1", 8080)]
    assert ls[3]["bind"] == "*"
    est = pt.parse_lsof_established(ESTAB)
    assert [pt.non_loopback_remote(c) for c in est] == [False, True]
    ps = pt.parse_ollama_ps(OLLAMA_PS)
    assert ps[0]["name"] == "hermes3:8b" and ps[0]["processor"] == "100% GPU" and ps[0]["size"] == "6.0 GB" and ps[0]["context"] == "8192"
    env = pt.parse_ps_env(PS_ENV)
    assert env["OFFLINE_MODE"] == "True" and env["OLLAMA_BASE_URL"] == "http://127.0.0.1:11434"
    assert all(c["ok"] for c in pt.env_checks(env) if c["ok"] is not None)


def test_proof_local_verdict_local_and_check(pt):
    rep = pt.local_report(fx())
    assert rep["verdict"].startswith("LOCAL: 3 stack listeners, all on loopback; 0 outbound non-loopback connections")
    assert "hermes3:8b on 100% GPU" in rep["verdict"] and rep["status_line"].startswith("Local - hermes3:8b - repo ")
    assert rep["status_line"].endswith("offline") and "Config audit: 26 checks, 0 drift" in rep["text"]
    bad = pt.local_report(fx(lsof_listen=LISTEN.replace("127.0.0.1:8080", "*:8080"),
                             lsof_established=ESTAB.replace("Safari    999", "Python  34750")))
    assert bad["verdict"].startswith("CHECK:") and "not on loopback" in bad["verdict"] and "outbound" in bad["verdict"]
    assert bad["status_line"].startswith("NOT VERIFIED")
    noenv = pt.local_report(fx(ps_env="x"))
    assert "offline env not confirmed" in noenv["verdict"]


def test_proof_local_endpoint_shape(pt, monkeypatch):
    monkeypatch.setattr(pt, "local_report", lambda fixtures=None: {
        "verdict": "LOCAL: x", "status_line": "Local - m - repo abc - offline", "text": "t", "listeners": [], "ollama_ps": [],
        "outbound_non_loopback": [], "audit": {}, "repo": {}})
    r = pt.proof_local()
    assert r["verdict"] == "LOCAL: x" and r["status_line"].startswith("Local") and "details" in r


# ---------------------------------------------------------------- show_context
def test_show_context_from_recorded_parts_and_calls(pt):
    pt.record_context("c5", "turn", {"user_head": "How many cells?", "since_ts": 1.0})
    pt.record_context("c5", "system", {"text": open(os.path.join(REPO, "tools", "prompts", "master_prompt.txt"), encoding="utf-8").read() + "\n\ntail"})
    pt.record_context("c5", "memory", pt.memory_summary("MEMORY:\n- [a] n\nLAST RUNS:\n- r1\n- r2\n"))
    pt.record_call("search_docs", "{}", 200, 10, 3, set(), {"hits": [{"file": "docs/RESULTS.md", "lines": "20-21"}]}, "c5", "m1", ts=pt.time.time())
    r = pt.show_context(pt.ShowReq(chat_id="c5"))
    c = r["context"]
    assert c["system"]["starts_with_master_prompt"] is True and len(c["system"]["sha256"]) == 64
    assert c["memory"]["notes"] == 1 and c["memory"]["runs"] == 2 and c["user_message"] == "How many cells?"
    assert c["retrieved"][0]["path"] == "docs/RESULTS.md" and c["retrieved"][0]["lines"] == "20-21"
    assert c["tools_prompt"]["sha256"] == hashlib.sha256(open(os.path.join(HD, "tools_prompt.txt"), "rb").read()).hexdigest()
    for needle in ("Preset system prompt", "Memory digest injected", "docs/RESULTS.md:20-21", "search_docs"):
        assert needle in r["text"]
    sources = pt.turn_data("c5", "m1", None, "hermes-chip-agent")["sources"]
    assert sources[0]["metadata"][0]["lines"] == "20-21" and "RESULTS.md" in sources[0]["source"]["name"]


# ---------------------------------------------------------------- demo 8, desktop app, hygiene
def test_demo8_defined_with_prompt_makefile_and_menu(pt):
    import demo_defs
    import install_prompts as IP
    d = demo_defs.find("proof")
    assert d["num"] == 8 and demo_defs.find("8") is d and demo_defs.find("/demo-proof") is d
    assert os.path.isfile(os.path.join(REPO, d["runner"])) and os.path.exists(os.path.join(REPO, d["docs"]))
    served = {r.name for r in pt.router.routes}
    assert {"proof_local", "show_context"} <= {s["tool"] for s in d["steps"]} <= served | {"read_metrics", "remember", "recall"}
    forms = {f["command"]: f["content"] for f in IP.demo_forms()}
    assert "demo_steps" in forms["/demo-proof"] and "proof" in forms["/demo-proof"]
    assert "8  proof" in demo_defs.menu()
    mk = open(os.path.join(REPO, "Makefile")).read()
    assert "demo-proof" in mk and mk.count("demo-proof") >= 3
    assert IP.receipt_form()["id"] == "chip_receipt" and "build_receipt" in IP.receipt_form()["content"]


def test_desktop_app_helpers():
    app = load("desktop_app_t", os.path.join(HD, "desktop", "app.py"))
    js = app.badge_js("Local - hermes3:8b - repo abc1234 - offline", True)
    assert "chip-proof" in js and "#1a7f37" in js and "Local - hermes3:8b" in js
    assert "#b42318" in app.badge_js("NOT VERIFIED", False)
    page = app.context_html("a <b> & c")
    assert "a &lt;b&gt; &amp; c" in page and "Context sent for the last turn" in page


def test_no_home_paths_in_new_files():
    for f in ("tool_server/proof_tools.py", "receipt_filter.py", "demos/proof_demo.py", "demos/PROOF_DEMO.md", "desktop/app.py"):
        t = open(os.path.join(HD, f), encoding="utf-8").read()
        assert "/Users/" not in t, f
