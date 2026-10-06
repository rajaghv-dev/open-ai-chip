"""pytest tests/tools/test_agent_core.py
tools/hermes_agent.py (stub chat, no Ollama), tools/mcp_server.py, tools/eval/run_eval.py (scorer),
tools/eval/ground_truth.py (reproducibility), the 10-tool schemas. Deterministic: no network, no Docker."""
import asyncio
import json
import os
import sys

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
for sub in ("tools", os.path.join("tools", "eval")):
    sys.path.insert(0, os.path.join(REPO, sub))
import eda_tools  # noqa: E402
import hermes_agent as H  # noqa: E402
import run_eval as RE  # noqa: E402

TC = '<tool_call>\n{"name": "%s", "arguments": %s}\n</tool_call>'


# ------------------------------------------------------------------ stub chat for hermes_agent.ask
class StubOllama:
    """Replaces hermes_agent._post: replays scripted assistant replies, records every payload."""

    def __init__(self, replies):
        self.replies, self.payloads, self.i = replies, [], 0

    def __call__(self, path, payload):
        assert path == "/api/chat"
        self.payloads.append(json.loads(json.dumps(payload)))   # snapshot (msgs list is mutated later)
        r = self.replies[min(self.i, len(self.replies) - 1)]
        self.i += 1
        return {"message": r if isinstance(r, dict) else {"role": "assistant", "content": r},
                "eval_count": 10, "eval_duration": 1_000_000_000}


@pytest.fixture
def stub(monkeypatch):
    def make(replies):
        s = StubOllama(replies)
        monkeypatch.setattr(H, "_post", s)
        return s
    return make


# ------------------------------------------------------------------ prompt mode
def test_parse_tool_calls():
    p = H._parse_tool_calls
    assert p(TC % ("list_designs", "{}")) == [("list_designs", {})]
    two = TC % ("a", '{"x": 1}') + "\ntext\n" + TC % ("b", '{"y": [1, 2]}')
    assert p(two) == [("a", {"x": 1}), ("b", {"y": [1, 2]})]
    assert p('<tool_call>{"name": "a", "parameters": {"q": 1}}</tool_call>') == [("a", {"q": 1})]   # "parameters" alias
    assert p('<tool_call>{"name": "a"}</tool_call>') == [("a", {})]                                  # no args
    assert p("<tool_call>{not json}</tool_call>") == []                                              # malformed: skipped
    assert p("plain answer, no call") == []
    assert p('<tool_call>{"name": "a", "arguments": {}}') == []                                      # unclosed tag is not a call
    assert p('<tool_call>\n{"name": "a",\n "arguments": {"k": "v"}}\n</tool_call>') == [("a", {"k": "v"})]  # multi-line


def test_prompt_mode_round_trip(stub):
    s = stub([TC % ("read_metrics", '{"design": "vision_block", "pattern": "count__stdcell"}'),
              "297 standard cells. Source: read_metrics"])
    r = H.ask("How many std cells?", "prompt", verbose=False)
    assert r["answer"].startswith("297") and [c["name"] for c in r["tool_calls"]] == ["read_metrics"]
    assert r["tool_calls"][0]["error"] is False and r["tool_calls"][0]["result_chars"] > 10
    assert r["eval_tokens"] == 20 and r["eval_seconds"] == 2.0
    p0, p1 = s.payloads
    assert "tools" not in p0                                   # prompt mode: no Ollama tools field
    assert p0["stream"] is False and p0["options"]["temperature"] == 0 and p0["options"]["seed"] == 42
    sysmsg = p0["messages"][0]["content"]
    assert sysmsg.startswith(H.SYSTEM[:40]) and "<tools>" in sysmsg and "<tool_call>" in sysmsg
    for t in eda_tools.TOOLS:                                  # all 10 tools are advertised in the system prompt
        assert t["function"]["name"] in sysmsg
    last = p1["messages"][-1]
    assert last["role"] == "tool" and '"name": "read_metrics"' in last["content"] and "design__instance__count__stdcell" in last["content"]
    assert p1["messages"][-2]["role"] == "assistant"


def test_prompt_mode_multiple_calls_in_one_turn(stub):
    s = stub([TC % ("list_designs", "{}") + TC % ("precheck_summary", "{}"), "done"])
    r = H.ask("q", verbose=False)
    assert [c["name"] for c in r["tool_calls"]] == ["list_designs", "precheck_summary"]
    tool_msg = s.payloads[1]["messages"][-1]["content"]
    assert tool_msg.count("</tool_response>") == 1 and '"name": "list_designs"' in tool_msg and '"name": "precheck_summary"' in tool_msg


def test_custom_system_prompt_is_used(stub):
    s = stub(["unknown"])
    H.ask("q", "prompt", system="MY SYSTEM", verbose=False)
    assert s.payloads[0]["messages"][0]["content"] == "MY SYSTEM"


def test_extra_messages_are_inserted_before_question(stub):
    s = stub(["ok"])
    H.ask("q?", extra_messages=[{"role": "user", "content": "earlier"}], verbose=False)
    roles = [m["content"] for m in s.payloads[0]["messages"][1:]]
    assert roles == ["earlier", "q?"]


# ------------------------------------------------------------------ native mode
def test_native_mode_payload_and_tool_message(stub):
    native = {"role": "assistant", "content": "", "tool_calls": [
        {"function": {"name": "read_metrics", "arguments": {"design": "vision_block", "pattern": "count__stdcell"}}}]}
    s = stub([native, "297 cells"])
    r = H.ask("q", "native", verbose=False)
    assert r["answer"] == "297 cells" and r["tool_calls"][0]["name"] == "read_metrics"
    p0, p1 = s.payloads
    assert p0["tools"] == json.loads(json.dumps(eda_tools.TOOLS))          # the Ollama tools field carries the exact schemas
    assert p0["messages"][0]["content"] == H.SYSTEM                         # plain system prompt, no <tools> block
    assert "<tools>" not in p0["messages"][0]["content"]
    assert p1["messages"][-1]["role"] == "tool" and p1["messages"][-1]["tool_name"] == "read_metrics"
    assert p1["messages"][-2] == native                                     # assistant message replayed verbatim


def test_native_mode_missing_arguments_and_string_free_answer(stub):
    s = stub([{"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "list_designs"}}]}, "ok"])
    r = H.ask("q", "native", verbose=False)
    assert r["tool_calls"][0]["args"] == {} and r["answer"] == "ok" and len(s.payloads) == 2


# ------------------------------------------------------------------ limits and errors
def test_tool_call_budget_is_enforced(stub):
    s = stub([TC % ("list_designs", "{}")])           # model never stops calling tools
    r = H.ask("q", verbose=False)
    assert len(r["tool_calls"]) == H.MAX_TOOL_CALLS == 6        # the 7th+ calls are refused, not executed or recorded
    assert r["answer"] == "unknown (too many steps)"            # the loop is bounded by the message count
    assert "budget exhausted" in s.payloads[-1]["messages"][-1]["content"]
    assert len(s.payloads[-1]["messages"]) <= 41


def test_budget_error_does_not_count_as_a_call(stub):
    replies = [TC % ("list_designs", "{}")] * 6 + [TC % ("list_designs", "{}") * 2, "final"]
    stub(replies)
    r = H.ask("q", verbose=False)
    assert r["answer"] == "final" and len(r["tool_calls"]) == 6


def test_run_tool_errors_are_data():
    assert H.run_tool("rm_rf", {}) == {"error": "unknown tool 'rm_rf'"}
    assert "error" in H.run_tool("read_metrics", {})                         # missing design
    assert "error" in H.run_tool("read_metrics", {"design": "../etc"})
    assert "error" in H.run_tool("read_metrics", "not a dict")               # non-dict args become {} -> missing design
    assert H.run_tool("list_designs", ["x"])["designs"]                      # non-dict -> {} -> works
    assert "error" in H.run_tool("layout_summary", {"design": "nope"})


def test_run_tool_never_raises_on_tool_exception(monkeypatch):
    def boom(name, args):
        raise RuntimeError("kaput")
    monkeypatch.setattr(eda_tools, "call", boom)
    assert H.run_tool("list_designs", {}) == {"error": "RuntimeError: kaput"}


def test_run_tool_keys_substring_retry():
    r = H.run_tool("read_metrics", {"design": "vision_block", "keys": ["count__stdcell"]})
    assert r["matched"] >= 1 and "design__instance__count__stdcell" in r["metrics"] and "substrings" in r["note"]
    exact = H.run_tool("read_metrics", {"design": "vision_block", "keys": ["design__instance__count__stdcell"]})
    assert "note" not in exact and exact["matched"] == 1


def test_tool_error_reaches_the_model_and_is_flagged(stub):
    s = stub([TC % ("read_metrics", '{"design": "nope"}'), "unknown"])
    r = H.ask("q", verbose=False)
    assert r["tool_calls"][0]["error"] is True
    assert "error" in s.payloads[1]["messages"][-1]["content"] and "unknown design" in s.payloads[1]["messages"][-1]["content"]


def test_unknown_tool_call_is_reported(stub):
    s = stub([TC % ("shell", '{"cmd": "ls"}'), "unknown"])
    r = H.ask("q", verbose=False)
    assert r["tool_calls"][0]["error"] is True and "unknown tool" in s.payloads[1]["messages"][-1]["content"]


def test_result_truncation():
    big = {"x": "a" * 10000}
    out = H._fmt(big)
    assert len(out) < H.RESULT_CHARS + 60 and "[truncated" in out
    assert H._fmt({"a": 1}) == '{"a": 1}'


def test_post_failure_propagates(monkeypatch):
    def down(path, payload):
        raise OSError("connection refused")
    monkeypatch.setattr(H, "_post", down)
    with pytest.raises(OSError):
        H.ask("q", verbose=False)


# ------------------------------------------------------------------ eda_tools schemas (all 10)
def test_ten_tools_have_valid_schemas():
    names = {t["function"]["name"] for t in eda_tools.TOOLS}
    assert names == {"list_designs", "read_metrics", "compare_designs", "layout_summary", "layer_stats", "find_pins",
                     "render_png", "signoff_summary", "classify_slew", "precheck_summary"}
    import jsonschema
    for t in eda_tools.TOOLS:
        f = t["function"]
        assert f["description"] and f["parameters"]["type"] == "object"
        jsonschema.Draft202012Validator.check_schema(f["parameters"])
        for req in f["parameters"].get("required", []):
            assert req in f["parameters"]["properties"]


@pytest.mark.parametrize("name", [t["function"]["name"] for t in eda_tools.TOOLS])
def test_every_tool_survives_garbage_args(name):
    for args in ({}, {"design": None}, {"design": ["x"]}, {"nonsense": 1}, "str", None, 5):
        r = eda_tools.call(name, args)
        assert isinstance(r, dict)


# ------------------------------------------------------------------ MCP server
def _mcp():
    pytest.importorskip("mcp")
    import mcp.types as types
    import mcp_server
    return types, mcp_server


def test_mcp_list_tools_matches_eda_tools():
    types, M = _mcp()
    res = asyncio.run(M.on_list_tools(None, None))
    assert [t.name for t in res.tools] == [t["function"]["name"] for t in eda_tools.TOOLS]
    for t, e in zip(res.tools, eda_tools.TOOLS):
        assert t.description == e["function"]["description"] and t.input_schema == e["function"]["parameters"]


def test_mcp_call_tool_in_process():
    types, M = _mcp()
    ok = asyncio.run(M.on_call_tool(None, types.CallToolRequestParams(
        name="read_metrics", arguments={"design": "vision_block", "keys": ["design__instance__count__stdcell"]})))
    body = json.loads(ok.content[0].text)
    assert not ok.is_error and body["metrics"]["design__instance__count__stdcell"]["value"] > 0
    bad = asyncio.run(M.on_call_tool(None, types.CallToolRequestParams(name="read_metrics", arguments={"design": "nope"})))
    assert bad.is_error and "error" in json.loads(bad.content[0].text)
    nope = asyncio.run(M.on_call_tool(None, types.CallToolRequestParams(name="no_such_tool", arguments=None)))
    assert nope.is_error
    lst = asyncio.run(M.on_call_tool(None, types.CallToolRequestParams(name="list_designs", arguments=None)))
    assert not lst.is_error and json.loads(lst.content[0].text)["designs"]


def test_mcp_stdio_subprocess_round_trip():
    """Real MCP client over stdio against `python tools/mcp_server.py`."""
    _mcp()
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    async def go():
        params = StdioServerParameters(command=sys.executable, args=[os.path.join(REPO, "tools", "mcp_server.py")], cwd=REPO)
        async with stdio_client(params) as (r, w):
            async with ClientSession(r, w) as sess:
                await sess.initialize()
                tools = await sess.list_tools()
                res = await sess.call_tool("list_designs", {})
                bad = await sess.call_tool("read_metrics", {"design": "../etc"})
                return tools, res, bad
    tools, res, bad = asyncio.run(asyncio.wait_for(go(), 60))
    assert len(tools.tools) == 10
    assert not res.is_error and "vision_block" in res.content[0].text
    assert bad.is_error


# ------------------------------------------------------------------ run_eval scorer
N = lambda vals, **kw: {"type": "numbers", "values": vals, **kw}


def test_strip_source_edge_cases():
    s = RE.strip_source
    assert s("297 cells\nSource: read_metrics") == "297 cells"
    assert s("297 cells. Source: read_metrics(design='vision_block')") == "297 cells."
    assert s("Source: read_metrics\n297 cells") == "297 cells"                 # answer after a leading citation line survives
    assert s("297 cells (Source: read_metrics, 4 calls) done") == "297 cells  done"
    assert s("(Source: compare_designs)\n12 and 13") == "12 and 13"
    assert s("no citation here") == "no citation here"
    assert s("") == ""
    assert s("SOURCE: x\nvalue 5") == "value 5"                               # case-insensitive
    assert s("The resource: 5 is fine") == "The resource: 5 is fine"          # 'source' only as a whole word
    assert s("x 1\n  source - tools\ny 2") == "x 1\n\ny 2"


def test_numbers_check_tolerance_and_source_isolation():
    c = N([297], tol_rel=0)
    assert RE.score(c, "297 standard cells")
    assert RE.score(c, "There are 297.\nSource: read_metrics")
    assert not RE.score(c, "unsure\nSource: read_metrics(count=297)")          # a number only in the citation does not count
    assert not RE.score(c, "298")
    assert RE.score(N([1000], tol_rel=0.01), "about 1,005 cells")             # commas stripped, 1% tolerance
    assert not RE.score(N([1000], tol_rel=0.01), "1,020")
    assert RE.score(N([-0.1234], tol_rel=0.02, tol_abs=0.002), "slack is -0.1240 ns")
    assert not RE.score(N([5, 7], tol_rel=0), "5 only")                        # all values must be present
    assert RE.score(N([5, 7], tol_rel=0), "5 by 7")
    assert not RE.score(N([2], tol_rel=0), "area x2")                         # "x2" glued to a word is not a number
    assert RE.numbers_in("-3.5, 1,200 and a2") == [-3.5, 1200.0]


def test_words_checks():
    assert RE.score({"type": "words", "any_words": ["prec_fp16"]}, "It is PREC_FP16.")
    assert not RE.score({"type": "words", "any_words": ["prec_fp16"]}, "prec_bf16\nSource: prec_fp16")
    assert RE.score({"type": "numbers", "values": [4], "tol_rel": 0, "all_words": ["sky130_fd_sc_hd__a"]}, "4 x sky130_fd_sc_hd__a")
    assert not RE.score({"type": "numbers", "values": [4], "tol_rel": 0, "all_words": ["a", "zz"]}, "4 a")


def test_yesno():
    yes, no = {"type": "yesno", "expect": True}, {"type": "yesno", "expect": False}
    assert RE.score(yes, "Yes, both are clean.") and RE.score(yes, "DRC passes and LVS clean")
    assert not RE.score(yes, "No, it is not clean.") and not RE.score(yes, "There are violations found")
    assert RE.score(no, "No, DRC fails.") and not RE.score(no, "Yes, it is clean.")
    assert not RE.score(no, "")


def test_unknown_handling():
    u = {"type": "unknown"}
    for good in ("unknown", "I cannot determine that", "That is not available in the tools", "I don't know", "No tool provides price",
                 "Unable to answer", "The data does not provide it"):
        assert RE.score(u, good), good
    assert not RE.score(u, "The price is $5000")
    assert not RE.score(u, "")
    assert RE.score(u, "answer\nSource: unknown")                              # unknown is scored on the unstripped text


def test_questions_json_are_well_formed_and_expected_text_passes_scorer():
    Q = json.load(open(os.path.join(REPO, "tools", "eval", "questions.json")))
    assert [q["id"] for q in Q] == ["q%02d" % i for i in range(1, 16)]
    for q in Q:
        assert RE.score(q["check"], q["expected"]), q["id"]                   # the expected text satisfies its own check
        assert not RE.score(q["check"], "zzz qqq"), q["id"]                   # and a garbage answer never does
    assert sum(q["check"]["type"] == "unknown" for q in Q) == 2


# ------------------------------------------------------------------ ground_truth reproducibility
def test_ground_truth_reproduces_committed_questions():
    import ground_truth as G
    try:
        built = G.build()
    except (FileNotFoundError, TypeError, ValueError) as e:        # precheck results are local build/ files
        pytest.skip("ground truth inputs missing: %r" % (e,))
    path = os.path.join(REPO, "tools", "eval", "questions.json")
    assert json.loads(json.dumps(built)) == json.load(open(path)), "tools/eval/questions.json is stale: run python3 tools/eval/ground_truth.py"


def test_ground_truth_script_writes_identical_file(tmp_path, monkeypatch):
    """Run the script's own entry (via runpy) with __file__ redirected so the committed file is untouched."""
    import runpy
    import ground_truth as G
    try:
        G.build()
    except Exception:
        pytest.skip("ground truth inputs missing")
    src = open(os.path.join(REPO, "tools", "eval", "ground_truth.py")).read()
    fake = tmp_path / "tools" / "eval" / "ground_truth.py"
    fake.parent.mkdir(parents=True)
    fake.write_text(src.replace("ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), \"..\", \"..\"))",
                                "ROOT = %r" % REPO))
    runpy.run_path(str(fake), run_name="__main__")
    assert (fake.parent / "questions.json").read_text() == open(os.path.join(REPO, "tools", "eval", "questions.json")).read()
