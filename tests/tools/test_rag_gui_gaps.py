"""pytest tests/tools/test_rag_gui_gaps.py
Gap-fillers for examples/hermes_rag and examples/hermes_klayout_gui (the main tests are test_rag.py,
test_klayout_gui.py, test_klayout_live.py). Stub model only: no Ollama, no display, no network."""
import glob
import json
import os
import sys

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
for sub in ("tools", os.path.join("tools", "eval"), os.path.join("examples", "hermes_rag"),
            os.path.join("examples", "hermes_harness"), os.path.join("examples", "hermes_klayout_gui")):
    sys.path.insert(0, os.path.join(REPO, sub))

TC = '<tool_call>\n{"name": "%s", "arguments": %s}\n</tool_call>'


def stub_chat(replies, seen=None):
    it = iter(replies)

    def chat(msgs):
        if seen is not None:
            seen.append(json.loads(json.dumps(msgs)))
        return {"message": {"content": next(it, "unknown")}}
    return chat


@pytest.fixture(autouse=True)
def trace_tmp(monkeypatch, tmp_path):
    import harness
    monkeypatch.setattr(harness, "TRACE_DIR", str(tmp_path / "traces"))


# ================================================================== hermes_rag
def test_rag_tools_only_config_hides_search_docs():
    import rag_agent as A
    assert [t["function"]["name"] for t in A.schemas(False)] == [t["function"]["name"] for t in A.eda_tools.TOOLS]
    assert "search_docs" in [t["function"]["name"] for t in A.schemas(True)] and len(A.schemas(True)) == 11
    assert "search_docs" not in A.system_prompt(False) and "search_docs" in A.system_prompt(True)
    for v in ("v1", "v2", "v3"):
        assert A.RAG_RULES[v] in A.system_prompt(True, v)
    assert A.system_prompt(True, "v1") != A.system_prompt(True, "v3")


def test_rag_search_docs_refused_when_rag_off():
    import rag_agent as A
    seen = []
    r = A.run_rag_episode("Why did x happen?", use_rag=False, grounding=False,
                          chat=stub_chat([TC % ("search_docs", '{"query": "x"}'), "unknown"], seen))
    assert r["searches"] == [] and r["tool_calls"][0]["error"] is True
    assert "not allowed" in seen[1][-1]["content"]


def test_rag_model_initiated_search_is_recorded_and_cited():
    import rag_agent as A
    q = "Why did simplifying the tiny_ai_core pins roughly halve its standard cell count?"
    seen = []
    ans = "Fewer tap cells.\nSources: designs/tiny_ai_core/NOTES.md > Intuitions and insights"
    r = A.run_rag_episode(q, grounding=False, chat=stub_chat([TC % ("search_docs", json.dumps({"query": "tiny_ai_core pins std cell count halve"})), ans], seen))
    assert r["searches"] and r["searches"][0]["auto"] is False and r["tool_calls"][0] == {
        "name": "search_docs", "args": {"query": "tiny_ai_core pins std cell count halve"}, "error": False, "auto": False}
    assert "file: " in seen[1][-1]["content"] and r["citations"] == ["designs/tiny_ai_core/NOTES.md"]


def test_rag_bad_search_args_become_errors_not_crashes():
    import rag_agent as A
    r = A.run_rag_episode("Why x?", grounding=False, chat=stub_chat([TC % ("search_docs", "{}"), "unknown"]))
    assert r["tool_calls"][0]["error"] is True and r["answer"] == "unknown"


def test_rag_loop_tool_budget_and_steps():
    import rag_agent as A
    r = A.run_rag_episode("How many standard cells does vision_block have?", grounding=False,
                          chat=stub_chat([TC % ("list_designs", "{}")] * 30))
    assert len(r["tool_calls"]) == 6 and r["steps"] == 10 and r["answer"].startswith("unknown")


def test_rag_grounding_revision_once():
    import rag_agent as A
    q = "Why did simplifying the tiny_ai_core pins roughly halve its standard cell count?"
    seen = []
    r = A.run_rag_episode(q, grounding=True, search_mode="v2", auto_retrieve=True, prompt_version="v3",
                          chat=stub_chat(["It is 987654 percent.\nSources: docs/NOT_RETRIEVED.md > x", "unknown, the docs do not say"], seen))
    assert r["revisions"] == 1 and "Grounding check failed" in seen[1][-1]["content"] and r["answer"].startswith("unknown")


def test_rag_format_hits_truncates_and_errors():
    import rag_agent as A
    res = {"hits": [{"file": "a.md", "heading": "h", "lines": "1-2", "text": "x" * 9000}]}
    s = A.format_hits(res, 500)
    assert len(s) < 530 and s.endswith("[truncated]")
    assert A.format_hits({"hits": []}) == "no matching chunks"
    assert json.loads(A.format_hits({"error": "e"})) == {"error": "e"}


def test_rag_cli_flags_set_config(monkeypatch, capsys):
    import rag_agent as A
    got = {}

    def fake(q, use_rag, grounding, tr, **kw):
        got.update(q=q, use_rag=use_rag, grounding=grounding, **kw)
        return {"answer": "A", "searches": [], "tool_calls": [], "revisions": 0, "seconds": 0, "citations": [], "final_grounding_problems": []}
    monkeypatch.setattr(A, "run_rag_episode", fake)
    monkeypatch.setattr(sys, "argv", ["rag_agent.py", "--router", "--guardrail", "why", "x"])
    A.main()
    assert got["q"] == "why x" and got["auto_retrieve"] and got["search_mode"] == "v2" and got["guardrail"] and A.PROMPT["version"] == "v3"
    monkeypatch.setattr(sys, "argv", ["rag_agent.py", "--no-rag", "q"])
    A.main()
    assert got["use_rag"] is False and got["grounding"] is False and got["search_mode"] == "v1"
    A.PROMPT["version"] = "v2"
    assert capsys.readouterr().out.count("A") >= 2


def test_eval_rag_configs_are_valid_run_kwargs():
    import inspect
    import eval_rag as E
    import rag_agent as A
    params = set(inspect.signature(A.run_rag_episode).parameters)
    for name, cfg in E.CONFIGS.items():
        assert set(cfg) <= params, name
    assert set(E.DEFAULT_CONFIGS) <= set(E.CONFIGS)


def test_eval_rag_questions_well_formed():
    import eval_rag as E
    Q = E.load_questions()
    ids = [q["id"] for q in Q]
    assert len(ids) == len(set(ids)) and len(Q) == 17
    assert sum(bool(q.get("heldout")) for q in Q) == 5
    for q in Q:
        assert q["route"] in ("doc", "metric", "unknown") and q["check"]["type"] in ("words", "numbers", "unknown", "yesno")
        for s in q["sources"]:
            assert os.path.isfile(os.path.join(REPO, s)), (q["id"], s)       # every expected source file exists
        assert bool(q["sources"]) == (not q["id"].startswith("u")) or q["id"].startswith("r")


def test_eval_rag_committed_retrieval_numbers_reproduce():
    """results_summary.json['retrieval'] is a deterministic function of the index: recompute and compare."""
    import eval_rag as E
    s = json.load(open(os.path.join(REPO, "examples", "hermes_rag", "results_summary.json")))
    Q = E.load_questions()
    for key, (mode, held) in {"v1": ("v1", False), "v1_heldout": ("v1", True), "v2": ("v2", False), "v2_heldout": ("v2", True)}.items():
        got = E.retrieval_eval(Q, mode, held)
        for f in ("questions", "recall_at_k", "evidence_in_top4_text"):
            assert got[f] == s["retrieval"][key][f], (key, f)
    assert E.router_eval(Q)["rag_questions"] == s["router"]["rag_questions"]


def test_eval_rag_e2e_with_stub_chat_and_make_summary(monkeypatch, tmp_path):
    import eval_rag as E
    import harness
    Q = [q for q in E.load_questions() if q["id"] in ("r01", "u01")]
    assert len(Q) == 2
    monkeypatch.setattr(harness, "chat", stub_chat(["unknown, not in the docs"] * 8))
    out = E.e2e_eval(Q, ["rag+router+guardrail"])
    c = out["rag+router+guardrail"]
    assert c["main"]["total"] + c["heldout"]["total"] == 2 and len(c["rows"]) == 2
    rows = {r["id"]: r for r in c["rows"]}
    assert rows["u01"]["pass"] is True                           # an honest 'unknown' passes the unanswerable control
    assert rows["r01"]["auto_searched"] and rows["r01"]["context_present"]
    # make_summary: repo-relative output only, hand-check merging
    rep = {"model": "m", "options": {}, "index": {"chunks": 1, "files": 1}, "retrieval": {}, "router": {}, "end_to_end": out}
    rp = tmp_path / "rep.json"
    rp.write_text(json.dumps(rep))
    hc = tmp_path / "hc.json"
    hc.write_text(json.dumps({"rag+router+guardrail": {"u01": {"ok": True, "note": "n"}}}))
    monkeypatch.setattr(E, "HERE", str(tmp_path))
    E.make_summary(str(rp), str(hc))
    s = json.load(open(tmp_path / "results_summary.json"))
    e = s["end_to_end"]["rag+router+guardrail"]
    assert e["hand_check_notes"] == {"u01": "n"} and "failed_answers" in e


# ================================================================== hermes_klayout_gui
GDS = os.path.join(REPO, "build", "results", "tiny_ai_core", "tiny_ai_core.gds")


@pytest.fixture(scope="module")
def be():
    pytest.importorskip("klayout.lay")
    from offscreen_backend import OffscreenBackend
    return OffscreenBackend()


def _scenario_gds(sc):
    import view_api as va
    d = sc["script"][0][1]["design"]
    top = json.load(open(os.path.join(REPO, "designs", d, "config.json")))["DESIGN_NAME"]
    return bool(glob.glob(os.path.join(REPO, "build", "results", d, "*.gds")))


def test_gui_scenarios_are_well_formed():
    import agent
    import view_api as va
    assert len(agent.SCENARIOS) == 5 and len({s["id"] for s in agent.SCENARIOS}) == 5
    for sc in agent.SCENARIOS:
        assert sc["script"][0][0] == "open_design" and sc["expect"] and all(n in va.TOOL_NAMES for n, _ in sc["script"])
        assert agent.is_view_request(sc["request"]) and agent.route(sc["request"], None)["tool"] == "open_design"
        if sc["id"] == "tiny_drc_honest":
            assert "snapshot" not in sc["expect"]


@pytest.mark.parametrize("idx", range(5))
def test_gui_every_scenario_dry_run(be, idx):
    """Each scripted scenario through the real loop + real offscreen KLayout: every call ok, expected sequence, PNG for snapshot ones."""
    import agent
    sc = agent.SCENARIOS[idx]
    if not _scenario_gds(sc):
        pytest.skip("GDS for %s not collected (git-ignored)" % sc["script"][0][1]["design"])
    lines = []
    r = agent.run_episode(sc["request"], be, agent.Scripted(sc["script"]), emit=lines.append)
    assert agent.seq_ok(r["calls"], sc["expect"]), [c["name"] for c in r["calls"]]
    assert all(c["ok"] for c in r["calls"]) and not r["router_used"] and r["rejected"] == 0
    assert r["answer"].startswith("(scripted answer)")
    assert len(r["pngs"]) == sum(1 for n, _ in sc["script"] if n == "snapshot")
    for p in r["pngs"]:
        assert os.path.getsize(os.path.join(REPO, p)) > 1000
    assert any(l.startswith("STATS") for l in lines)


def test_gui_seq_ok():
    import agent
    c = lambda *names: [{"name": n, "ok": True} for n in names]   # noqa: E731
    assert agent.seq_ok(c("open_design", "zoom_to", "snapshot"), ["open_design", "snapshot"])
    assert not agent.seq_ok(c("snapshot", "open_design"), ["open_design", "snapshot"])      # order matters
    assert not agent.seq_ok([{"name": "open_design", "ok": False}], ["open_design"])        # failed calls do not count
    assert agent.seq_ok([], [])


def test_gui_route_edge_cases():
    import agent
    assert agent.route("", None) is None and agent.route("hello", None) is None
    assert agent.route("snapshot", None) == {"tool": "snapshot", "args": {}}                 # no design: snapshot still first rule
    assert agent.route("highlight drc", None)["tool"] in ("state", "highlight_drc")
    assert agent.route("highlight drc", "tiny_ai_core") == {"tool": "highlight_drc", "args": {"design": "tiny_ai_core"}}
    assert agent.route("show layers", "tiny_ai_core") == {"tool": "state", "args": {}}       # layers not named: falls through to state
    assert agent.route("zoom to full view", "x")["args"] == {"target": {"full": True}}
    assert agent.route("show only MET1 and li1", "x")["args"]["layers"] == ["met1", "li1"]
    assert agent.design_in("vision_block and vision_all_lit") in ("vision_block", "vision_all_lit")
    assert agent.design_in("xvision_block") is None                                          # word-bounded
    assert agent.route("open tiny_ai_core", "tiny_ai_core")["tool"] != "open_design"         # already open


def test_gui_loop_budget_closing_tag_eaten_and_bad_args():
    import agent

    class Fake:
        def __getattr__(self, n):
            raise AssertionError("backend must not be touched for refused calls: " + n)
    be_ = Fake()
    lines = []
    replies = iter(['<tool_call>\n{"name": "state", "arguments": {}}'] + ["x"])               # no closing tag
    calls_seen = []

    class B:
        def state(self):
            calls_seen.append("state")
            return {"ok": True, "design": None}
    r = agent.run_episode("what is open", B(), lambda m: next(replies), emit=lambda l: None)
    assert calls_seen == ["state"] and r["calls"][0]["name"] == "state"
    r = agent.run_episode("what is open", B(), lambda m: '<tool_call>{"name": "state", "arguments": {}}</tool_call>', emit=lambda l: None)
    assert len(r["calls"]) == agent.MAX_CALLS and r["steps"] == agent.MAX_STEPS and r["answer"].startswith("unknown")
    r = agent.run_episode("show x", B(), lambda m: '<tool_call>{"name": "rm", "arguments": {"a": 1}}</tool_call>', emit=lambda l: None)
    assert all(not c["ok"] for c in r["calls"])                                              # tool outside the allow-list is refused


def test_gui_with_metrics_adds_read_metrics(be):
    import agent
    base, withm = agent.system_prompt(False), agent.system_prompt(True)
    assert '"name": "read_metrics"' not in base and '"name": "read_metrics"' in withm
    r = agent.run_episode("q?", be, lambda m: TC % ("read_metrics", '{"design": "vision_block", "keys": ["design__instance__count__stdcell"]}') if len(m) < 3 else "297",
                          emit=lambda l: None, with_metrics=True)
    assert r["calls"][0]["name"] == "read_metrics" and r["calls"][0]["ok"] and r["answer"] == "297"
    r = agent.run_episode("q?", be, lambda m: TC % ("read_metrics", '{"design": "vision_block"}') if len(m) < 3 else "done",
                          emit=lambda l: None, with_metrics=False)
    assert r["calls"][0]["ok"] is False                                                      # not offered -> refused


def test_gui_main_dry_run_cli(monkeypatch, capsys):
    import agent
    pytest.importorskip("klayout.lay")
    sc = agent.SCENARIOS[2]
    if not _scenario_gds(sc):
        pytest.skip("GDS missing")
    monkeypatch.setattr(sys, "argv", ["agent.py", "--dry-run", "--scenario", "2"])
    agent.main()
    out = capsys.readouterr().out
    assert "dry run" in out and "ANSWER" in out and "open_design" in out


def test_gui_main_requires_request_without_dry_run(monkeypatch):
    import agent
    pytest.importorskip("klayout.lay")
    monkeypatch.setattr(sys, "argv", ["agent.py"])
    with pytest.raises(SystemExit) as e:
        agent.main()
    assert e.value.code == 2
