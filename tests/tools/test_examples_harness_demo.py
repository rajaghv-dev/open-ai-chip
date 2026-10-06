"""pytest tests/tools/test_examples_harness_demo.py
examples/hermes_harness (harness.py, eval_harness.py) and examples/hermes_klayout_demo (demo.py).
Stub model only: no Ollama, no network, no Docker."""
import glob
import json
import os
import re
import subprocess
import sys

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
for sub in ("tools", os.path.join("tools", "eval"), os.path.join("examples", "hermes_harness")):
    sys.path.insert(0, os.path.join(REPO, sub))
import eda_tools  # noqa: E402
import harness as HN  # noqa: E402
import eval_harness as EH  # noqa: E402

TC = '<tool_call>\n{"name": "%s", "arguments": %s}\n</tool_call>'


def metrics(d):
    return json.load(open(os.path.join(REPO, "designs", d, "output", "metrics.json")))


def hardened():
    return sorted(os.path.basename(os.path.dirname(os.path.dirname(p)))
                  for p in glob.glob(os.path.join(REPO, "designs", "*", "output", "metrics.json")))


# ------------------------------------------------------------------ pick_extreme vs metrics.json (independent computation)
def test_pick_extreme_max_stdcells_among_prec():
    precs = [d for d in hardened() if d.startswith("prec_")]
    want = max(precs, key=lambda d: metrics(d)["design__instance__count__stdcell"])
    r = HN.pick_extreme("design__instance__count__stdcell", "max", scope="prec_")
    assert r["winner"]["design"] == want and r["winner"]["value"] == metrics(want)["design__instance__count__stdcell"]
    assert r["winner"]["key"] == "design__instance__count__stdcell" and r["candidates"] == len(precs)
    assert len(r["next_best"]) == 3 and r["next_best"][0]["value"] <= r["winner"]["value"]


def test_pick_extreme_min_area_ignores_zero():
    vals = {d: metrics(d)["design__instance__area__stdcell"] for d in hardened()}
    nz = {d: v for d, v in vals.items() if v}
    r = HN.pick_extreme("design__instance__area__stdcell", "min")
    assert r["winner"]["value"] == min(nz.values()) and r["winner"]["value"] > 0
    assert vals[r["winner"]["design"]] == r["winner"]["value"]
    rmax = HN.pick_extreme("design__instance__area__stdcell", "max")
    assert rmax["winner"]["value"] == max(nz.values())


def test_pick_extreme_worst_corner_names_the_corner():
    m = metrics("prec_fp16")
    cs = {k: v for k, v in m.items() if k.startswith("timing__setup__ws__corner:")}
    k = min(cs, key=cs.get)
    r = HN.pick_extreme("timing__setup__ws__corner", "min", designs=["prec_fp16"])
    assert r["winner"] == {"design": "prec_fp16", "key": k, "value": cs[k]} and r["candidates"] == len(cs)
    # slack is not a count/area: a negative or zero value must not be filtered out
    r2 = HN.pick_extreme("timing__setup__ws", "max", designs=["prec_fp16"])
    assert r2["winner"]["design"] == "prec_fp16"


def test_pick_extreme_errors_are_informative():
    assert "scope" in HN.pick_extreme("design__instance__count__stdcell", "max", scope="zzz_")["error"]
    assert "no numeric values" in HN.pick_extreme("no_such_metric_xyz", "max", designs=["vision_block"])["error"]
    assert "error" in HN.pick_extreme("x", "max", designs=["nope"])
    assert "unknown design" in HN.execute("pick_extreme", {"metric": "x", "which": "max", "designs": ["nope"]}, HN.Config(pick_extreme=True))["error"]


def test_pick_extreme_explicit_designs_and_which():
    r = HN.pick_extreme("design__instance__count__stdcell", "min", designs=["vision_block", "prec_fp16"])
    a, b = (metrics(d)["design__instance__count__stdcell"] for d in ("vision_block", "prec_fp16"))
    assert r["winner"]["value"] == min(a, b) and r["candidates"] == 2


# ------------------------------------------------------------------ tool layer: schemas, validate_args, execute
def test_tool_schemas_swap_compare_for_pick_extreme():
    base = [t["function"]["name"] for t in HN.tool_schemas(HN.Config())]
    pe = [t["function"]["name"] for t in HN.tool_schemas(HN.Config(pick_extreme=True))]
    assert "compare_designs" in base and "pick_extreme" not in base and len(base) == 10
    assert "compare_designs" not in pe and "pick_extreme" in pe and len(pe) == 10


def test_validate_args():
    sch = HN.tool_schemas(HN.Config(pick_extreme=True))
    v = lambda n, a: HN.validate_args(n, a, sch)
    assert v("read_metrics", {"design": "vision_block", "pattern": "x"}) is None
    assert v("pick_extreme", {"metric": "m", "which": "max", "designs": None, "scope": "prec_"}) is None
    assert "not allowed" in v("compare_designs", {"metric": "m"})            # swapped out
    assert "not allowed" in v("rm", {})
    assert "JSON object" in v("read_metrics", ["design"])
    assert "invalid arguments" in v("read_metrics", {})                      # required design missing
    assert "invalid arguments" in v("read_metrics", {"design": "x", "bogus": 1})   # additionalProperties False
    assert "invalid arguments" in v("pick_extreme", {"metric": "m", "which": "median"})   # enum
    assert "invalid arguments" in v("read_metrics", {"design": 5})           # wrong type
    assert "invalid arguments" in v("layer_stats", {"design": "x"})          # layer required


def test_execute_allow_list():
    off, on = HN.Config(), HN.Config(pick_extreme=True)
    assert "unknown tool" in HN.execute("pick_extreme", {"metric": "m", "which": "max"}, off)["error"]   # not offered -> not runnable
    assert HN.execute("pick_extreme", {"metric": "design__instance__count__stdcell", "which": "max"}, on)["winner"]
    assert "error" in HN.execute("pick_extreme", {"wrong": 1}, on)           # TypeError turned into data
    assert "unknown tool" in HN.execute("os.system", {}, on)["error"]


# ------------------------------------------------------------------ grounding_check
def test_nums_strip_units_and_commas():
    assert HN._nums("area 1,234.5 um^2 and 3") == [1234.5, 3.0]             # the ^2 exponent is not a number
    assert HN._nums("no digits") == []


def test_grounding_check_numbers_and_names():
    gc = HN.grounding_check
    res = [{"design": "vision_block", "metrics": {"k": {"value": 297}, "j": {"value": 100}}}]
    assert gc("q", "297 cells. Source: read_metrics", res) == []
    assert gc("q", "350 cells", res) == ["350.0"]
    assert gc("q", "298 cells", res) == []                                   # 298 is within the 0.5% tolerance of 297
    assert gc("q", "The sum is 397", res) == []                              # sum of two tool numbers
    assert gc("q", "difference 197", res) == []
    assert gc("q", "ratio 2.97", res) == []
    assert gc("q", "ratio 2.971", res) == []                                 # within 0.5%
    assert gc("q", "297.5", res) == []                                       # within 0.5% of 297
    assert gc("q", "Number 5000", res) == ["5000.0"]
    assert gc("how about 5000?", "5000", res) == []                          # number given in the question
    assert gc("q", "42\nSource: read_metrics(297)", res) == ["42.0"]
    assert gc("q", "vision_block has 297", res) == []                        # design name present in tool output
    bad = gc("q", "prec_fp16 has 297", res)
    assert bad == ["prec_fp16"]                                              # name neither in tool output nor question
    assert gc("tell me about prec_fp16", "prec_fp16 has 297", res) == []
    assert gc("q", "unknown", []) == []


def test_grounding_check_ignores_source_tail():
    res = [{"v": 10}]
    assert HN.grounding_check("q", "10\nSource: read_metrics 999", res) == []
    assert HN.grounding_check("q", "10 (Source: x 999)", res) == []
    assert HN.hermes_agent_strip("a\nSource: b") == "a"


# ------------------------------------------------------------------ the loop with a stub chat
@pytest.fixture
def episode(monkeypatch, tmp_path):
    monkeypatch.setattr(HN, "TRACE_DIR", str(tmp_path / "traces"))

    def run(replies, cfg, question="q"):
        seen, it = [], iter(replies)

        def chat(msgs):
            seen.append(json.loads(json.dumps(msgs)))
            try:
                return {"message": {"content": next(it)}}
            except StopIteration:
                return {"message": {"content": "unknown"}}
        monkeypatch.setattr(HN, "chat", chat)
        tr = HN.Tracer("t")
        out = HN.run_episode(question, cfg, tr)
        events = [json.loads(l) for l in open(tr.path)]
        return out, seen, events
    return run


def test_loop_plain_react(episode):
    out, seen, ev = episode([TC % ("read_metrics", '{"design": "vision_block", "pattern": "count__stdcell"}'), "297. Source: read_metrics"], HN.Config())
    assert out["answer"].startswith("297") and [c["name"] for c in out["tool_calls"]] == ["read_metrics"] and out["steps"] == 2
    assert [e["event"] for e in ev] == ["start", "model", "tool", "model", "end"]
    assert ev[0]["config"]["guardrails"] is False and len(ev[0]["prompt_sha"]) == 12
    assert out["grounding"] is None and out["retries"] == 0


def test_loop_budget_and_step_limits(episode):
    cfg = HN.Config(max_calls=2, max_steps=5)
    out, seen, ev = episode([TC % ("list_designs", "{}")] * 10, cfg)
    assert len(out["tool_calls"]) == 2 and out["steps"] == 5          # step cap stops the loop
    assert out["answer"] == "unknown (step or time budget exhausted)"
    assert "budget exhausted" in seen[-1][-1]["content"]


def test_loop_time_budget(episode):
    out, _, _ = episode([TC % ("list_designs", "{}")] * 3, HN.Config(max_seconds=0))
    assert out["steps"] == 0 and out["answer"].startswith("unknown")


def test_loop_guardrails_bounded_retries(episode):
    bad = TC % ("read_metrics", '{"nodesign": 1}')
    out, seen, ev = episode([bad] * 6 + ["final"], HN.Config(guardrails=True, max_arg_retries=3))
    assert out["retries"] == 3 and [e["event"] for e in ev].count("validation_error") == 3
    assert "Fix the arguments" in seen[1][-1]["content"]
    assert len(out["tool_calls"]) >= 1                                 # after 3 retries the call is let through (and errors in the tool)
    assert out["answer"] == "final"


def test_loop_guardrail_off_lets_bad_args_through(episode):
    out, _, ev = episode([TC % ("read_metrics", '{"nodesign": 1}'), "x"], HN.Config())
    assert out["retries"] == 0 and out["tool_calls"][0]["error"] is True


def test_loop_grounding_one_revision(episode):
    read = TC % ("read_metrics", '{"design": "vision_block", "pattern": "count__stdcell"}')
    out, seen, ev = episode([read, "It has 99999 cells.", "It has 297 cells."], HN.Config(grounding=True))
    assert out["answer"] == "It has 297 cells." and out["retries"] == 1
    assert "do not appear in any tool output" in seen[-1][-1]["content"] and "99999" in seen[-1][-1]["content"]
    g = [e for e in ev if e["event"] == "grounding"]
    assert g[0]["ok"] is False
    # revised once only: a second ungrounded answer is accepted as is
    out2, _, _ = episode([read, "99999", "88888"], HN.Config(grounding=True))
    assert out2["answer"] == "88888" and out2["retries"] == 1


def test_loop_grounded_answer_not_revised(episode):
    read = TC % ("read_metrics", '{"design": "vision_block", "pattern": "count__stdcell"}')
    out, seen, _ = episode([read, "297"], HN.Config(grounding=True))
    assert out["retries"] == 0 and out["grounding"] == {"ok": True, "ungrounded": []} and len(seen) == 2


def test_loop_pick_extreme_prompt_and_execution(episode):
    pe = TC % ("pick_extreme", '{"metric": "design__instance__count__stdcell", "which": "max", "scope": "prec_"}')
    out, seen, _ = episode([pe, "done"], HN.Config(pick_extreme=True))
    sysmsg = seen[0][0]["content"]
    assert '"name": "pick_extreme"' in sysmsg and '"name": "compare_designs"' not in sysmsg
    assert "pick_extreme(metric=" in sysmsg
    assert "winner" in seen[1][-1]["content"] and out["tool_calls"][0]["error"] is False


def test_loop_plan_then_execute(episode):
    plan = '<plan>[{"name": "list_designs", "arguments": {}}]</plan>'
    out, seen, ev = episode([plan, "answer"], HN.Config(plan=True))
    assert [c["name"] for c in out["tool_calls"]] == ["list_designs"] and out["answer"] == "answer"
    assert "plan" in [e["event"] for e in ev]
    out, _, _ = episode(["<plan>garbage", "just an answer"], HN.Config(plan=True))      # unparsable plan: falls back to the loop
    assert out["tool_calls"] == [] and out["answer"] == "just an answer"
    out, _, _ = episode(["<plan>[]</plan>", "unknown"], HN.Config(plan=True))
    assert out["tool_calls"] == []


def test_trace_file_is_jsonl_under_trace_dir(episode, tmp_path):
    _, _, ev = episode(["hi"], HN.Config())
    assert all({"t", "episode", "event"} <= set(e) for e in ev) and ev[-1]["event"] == "end"
    assert os.listdir(tmp_path / "traces")


def test_cli_config_flags(monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(HN, "TRACE_DIR", str(tmp_path))
    got = {}
    monkeypatch.setattr(HN, "run_episode", lambda q, cfg, tr: got.update(q=q, cfg=cfg) or
                        {"answer": "A", "tool_calls": [], "seconds": 0, "retries": 0, "steps": 1, "grounding": None})
    monkeypatch.setattr(sys, "argv", ["harness.py", "--all", "how", "many"])
    HN.main()
    assert got["q"] == "how many" and (got["cfg"].guardrails, got["cfg"].grounding, got["cfg"].pick_extreme, got["cfg"].plan) == (True, True, True, False)
    assert capsys.readouterr().out.strip() == "A"
    monkeypatch.setattr(sys, "argv", ["harness.py", "--plan", "q"])
    HN.main()
    assert (got["cfg"].guardrails, got["cfg"].plan) == (False, True)


# ------------------------------------------------------------------ eval_harness: configs and --gate exit codes
def test_eval_harness_configs():
    assert list(EH.CONFIGS) == ["baseline", "+guardrails", "+grounding", "+pick_extreme", "all", "all+plan"]
    a = EH.CONFIGS["all"]
    assert (a.guardrails, a.grounding, a.pick_extreme, a.plan) == (True, True, True, False)
    assert EH.CONFIGS["all+plan"].plan and EH.score.__module__ == "run_eval"


@pytest.fixture
def gate_env(monkeypatch, tmp_path):
    monkeypatch.setattr(HN, "TRACE_DIR", str(tmp_path / "tr"))
    Q = {q["question"]: q for q in json.load(open(os.path.join(REPO, "tools", "eval", "questions.json")))}
    before = set(glob.glob(os.path.join(REPO, "build", "agent", "harness_eval_*.json")))

    def stub(answer_fn):
        def run_episode(question, cfg, tracer):
            return {"answer": answer_fn(Q[question]), "tool_calls": [], "seconds": 0.1, "retries": 0, "steps": 1, "grounding": None}
        monkeypatch.setattr(HN, "run_episode", run_episode)
    yield stub
    for f in set(glob.glob(os.path.join(REPO, "build", "agent", "harness_eval_*.json"))) - before:
        os.remove(f)                                                     # leave no stub report behind


def run_gate(monkeypatch, *args):
    monkeypatch.setattr(sys, "argv", ["eval_harness.py", "--only", "all", *args])
    try:
        EH.main()
    except SystemExit as e:
        return e.code
    return 0


def test_gate_passes_with_perfect_stub(gate_env, monkeypatch, capsys):
    gate_env(lambda q: q["expected"])
    assert run_gate(monkeypatch, "--gate", "15") == 0
    out = capsys.readouterr().out
    assert "15/15" in out and "GATE FAILED" not in out


def test_gate_fails_below_threshold(gate_env, monkeypatch, capsys):
    gate_env(lambda q: "" if q["id"] in ("q01", "q02", "q03") else q["expected"])
    assert run_gate(monkeypatch, "--gate", "13") == 1                    # 12 < 13
    out = capsys.readouterr().out
    assert "GATE FAILED: all-features score 12 < 13" in out and "q01,q02,q03" in out
    assert run_gate(monkeypatch, "--gate", "12") == 0                    # exactly at the threshold passes


def test_gate_absent_or_other_config_never_exits_nonzero(gate_env, monkeypatch):
    gate_env(lambda q: "")
    assert run_gate(monkeypatch) == 0                                    # no --gate: report only
    monkeypatch.setattr(sys, "argv", ["eval_harness.py", "--only", "baseline", "--gate", "15"])
    EH.main()                                                            # 'all' not run: gate is skipped (documented behaviour)


def test_gate_counts_episode_exceptions_as_failures(gate_env, monkeypatch, capsys):
    def boom(q, cfg, tr):
        raise RuntimeError("ollama down")
    monkeypatch.setattr(HN, "run_episode", boom)
    assert run_gate(monkeypatch, "--gate", "1") == 1
    assert "0/15" in capsys.readouterr().out


def test_gate_report_json_shape(gate_env, monkeypatch, capsys):
    gate_env(lambda q: q["expected"])
    run_gate(monkeypatch)
    path = re.search(r"-> (\S+harness_eval_\S+\.json)", capsys.readouterr().out).group(1)
    rep = json.load(open(path))
    c = rep["configs"]["all"]
    assert c["passed"] == c["total"] == 15 and c["failed"] == [] and len(c["rows"]) == 15 and rep["model"]


# ------------------------------------------------------------------ examples/hermes_klayout_demo
GDS = os.path.join(REPO, "build", "results", "tiny_ai_core", "tiny_ai_core.gds")
needs_gds = pytest.mark.skipif(not os.path.isfile(GDS), reason="build/results/tiny_ai_core GDS missing (git-ignored)")


@pytest.fixture(scope="module")
def demo():
    pytest.importorskip("klayout.db")
    import importlib.util       # by path: examples/hermes_klayout_gui/demo.py has the same module name
    spec = importlib.util.spec_from_file_location("klayout_demo_example", os.path.join(REPO, "examples", "hermes_klayout_demo", "demo.py"))
    D = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(D)
    return D


@needs_gds
def test_demo_tools_match_eda_tools(demo):
    d = demo.die_size("tiny_ai_core")
    assert d == {"width_um": 250.0, "height_um": 250.0}
    assert eda_tools.call("layout_summary", {"design": "tiny_ai_core"})["die_size_um"] == [d["width_um"], d["height_um"]]
    r = demo.count_shapes("tiny_ai_core", "met4")
    assert r["layer"] == "met4" and r["shapes"] == eda_tools.call("layer_stats", {"design": "tiny_ai_core", "layer": "met4"})["shapes"]


def test_demo_tool_errors(demo):
    with pytest.raises(ValueError):
        demo.die_size("../etc")
    with pytest.raises(ValueError):
        demo.die_size("nope")
    with pytest.raises(ValueError):
        demo.count_shapes("tiny_ai_core", "met9")
    assert set(demo.TOOLS) == {s["name"] for s in demo.SPECS} == {"die_size", "count_shapes"}


def test_demo_missing_gds_message(demo, monkeypatch, tmp_path):
    monkeypatch.setattr(demo, "REPO", str(tmp_path))
    (tmp_path / "designs" / "x").mkdir(parents=True)
    (tmp_path / "designs" / "x" / "config.json").write_text('{"DESIGN_NAME": "x"}')
    with pytest.raises(FileNotFoundError, match="make collect"):
        demo.die_size("x")


@needs_gds
def test_demo_dry_run_in_process_matches_committed_transcript(demo, capsys):
    demo.run("What is the die size of tiny_ai_core, and how many met4 shapes does it have?", True)
    out = capsys.readouterr().out
    want = open(os.path.join(REPO, "examples", "hermes_klayout_demo", "transcript_dry_run.txt")).read().splitlines()
    got = ("(dry run: scripted model replies, real KLayout tools; no Ollama needed)\n" + out).splitlines()
    assert [l.rstrip() for l in got] == [l.rstrip() for l in want]


@needs_gds
def test_demo_dry_run_cli_exit_code():
    p = subprocess.run([sys.executable, os.path.join(REPO, "examples", "hermes_klayout_demo", "demo.py"), "--dry-run"],
                       capture_output=True, text=True, timeout=120, cwd=REPO)
    assert p.returncode == 0 and "[3] ANSWER" in p.stdout and "250.0" in p.stdout


def test_demo_loop_error_paths(demo, monkeypatch, capsys):
    """Stub model: unknown tool, bad JSON args, then never a final answer -> step cap."""
    replies = iter(['<tool_call>{"name": "rm", "arguments": {}}</tool_call>',
                    '<tool_call>{"name": "die_size", "arguments": {"design": "../x"}}',      # closing tag eaten (Ollama quirk)
                    '<tool_call>{"name": "die_size", "arguments": {"design": "nope"}}</tool_call>'])
    monkeypatch.setattr(demo, "ollama", lambda msgs: next(replies))
    demo.run("q", False)
    out = capsys.readouterr().out
    assert out.count("OBSERVE") == 3 and out.count('"error"') == 3
    assert "STOPPED: step cap reached" in out


def test_demo_loop_two_calls_one_turn_and_final(demo, monkeypatch, capsys):
    replies = iter(['<tool_call>{"name": "die_size", "arguments": {"design": "nope"}}<tool_call>{"name": "count_shapes", "arguments": {"design": "nope", "layer": "met1"}}</tool_call>',
                    "final text"])
    monkeypatch.setattr(demo, "ollama", lambda msgs: next(replies))
    demo.run("q", False)
    out = capsys.readouterr().out
    assert out.count("ACT ") == 2 and "[2] ANSWER  final text" in out


def test_demo_ollama_unreachable_exits_cleanly():
    p = subprocess.run([sys.executable, "-c",
                        "import sys; sys.argv=['demo.py','q']; import urllib.request as u;"
                        "u.urlopen=lambda *a,**k: (_ for _ in ()).throw(OSError('refused'));"
                        "import runpy; runpy.run_path('examples/hermes_klayout_demo/demo.py', run_name='__main__')"],
                       capture_output=True, text=True, timeout=60, cwd=REPO)
    assert p.returncode != 0 and "Ollama not reachable" in p.stderr
