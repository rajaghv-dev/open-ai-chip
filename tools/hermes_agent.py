#!/usr/bin/env python3
"""Local, offline Hermes tool-calling agent over the read-only EDA tool layer.
Docs: docs/HERMES_AGENT.md, docs/AGENT_MODELS.md

Model: Nous Research Hermes served by Ollama (http://localhost:11434). Tools: tools/eda_tools.py only
(TOOLS schemas + call(name, args)); the agent has no shell, no file write, no network except localhost.

  build/agent/venv/bin/python tools/hermes_agent.py "How many std cells does vision_block have?"
  build/agent/venv/bin/python tools/hermes_agent.py            # interactive
  --mode native   Ollama /api/chat `tools` field (its template drops the system prompt)
  --mode prompt   (default) Hermes function-calling prompt format (<tools> in system prompt, <tool_call> tags parsed)
  --context       (or env HERMES_CONTEXT=1) prepend the repo master prompt tools/prompts/master_prompt.txt to the system
                  prompt (make master-prompt). Default OFF: the recorded eval results and prompt_sha stay reproducible.
"""
import argparse, json, os, re, sys, time, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eda_tools  # noqa: E402  (the only tool source)

OLLAMA = os.environ.get("OLLAMA_URL", "http://localhost:11434")
MODEL = os.environ.get("HERMES_MODEL", "hermes3:8b")
# Models with a thinking mode get "think": false on /api/chat (tool use here wants a plain answer, not a reasoning trace).
# hermes3:8b and other models are untouched. Override with env HERMES_THINK=on to leave thinking at the model default.
NO_THINK_PREFIXES = ("qwen3", "gemma4")
MAX_TOOL_CALLS = 6
HTTP_TIMEOUT = 300
RESULT_CHARS = 3500   # tool result characters shown to the model
OPTS = {"temperature": 0, "seed": 42, "num_ctx": 8192, "num_predict": 700}   # deterministic decoding (also the harness and RAG agents)
BUDGET_MSG = "tool call budget exhausted; answer from what you have or say unknown"

SYSTEM_V1 = """You are an assistant for the open-ai-chip repository: tiny AI inference chips (vision, text, audio, number-format study prec_*, SoC and Caravel wrappers) hardened to GDSII on the open sky130A process.
Rules:
- Answer ONLY from tool results. Never guess or use memory for numbers.
- Call a tool whenever the question needs repository data. Use the exact design names (for example vision_block, prec_fp16, tiny_ai_core, soc_image_text_match). If unsure which designs exist, call list_designs.
- For questions about several designs, call the tool once per design (or use compare_designs) and then compute differences or ratios carefully.
- If no tool can answer the question (for example price, yield, schedule, or anything outside the repo data), answer exactly that it is unknown, and say why. Do not invent a value.
- Keep the final answer short: the value(s) with units, then 'Source: <tool names used>'."""

SYSTEM_V2 = SYSTEM_V1 + """

How to use the tools (recipes, follow them):
- Count of standard cells of one design: read_metrics(design=D, pattern="count__stdcell"). Flip-flops: pattern="class:sequential_cell". Area: pattern="area__stdcell". Die: pattern="die__bbox" (x0 y0 x1 y1, um).
- Worst setup slack and its corner: read_metrics(design=D, pattern="setup__ws__corner"); the smallest value is the worst, and the text after 'corner:' is its corner name. (Key timing__setup__ws is the overall worst.)
- Which design has the most/least of something: ONE call compare_designs(metric=EXACT_KEY), for example design__instance__count__stdcell, design__instance__count__class:sequential_cell, design__instance__area__stdcell. Optionally restrict with designs=[...] (for example only prec_* designs; ignore designs whose value is 0).
- Differences or ratios between designs: get both values (compare_designs with designs=[A,B], or read_metrics twice), then compute with care and show the two numbers.
- Die size / macro instances / top cell of a layout: layout_summary(design=D). Pin counts: find_pins(design=D, pattern="wbs_dat_i") and count the pins returned.
- DRC / LVS clean: signoff_summary(design=D). Max-slew violation total: read_metrics(design=D, pattern="max_slew_violation__count").
- Precheck: precheck_summary().
- Tool outputs may be long; read the exact fields. If a tool returns an error, fix the arguments and retry once.
- Questions about price, yield, schedule, people, tapeout status or measured silicon are not in any tool: say 'unknown' and do not call tools for them."""

SYSTEM_V3 = SYSTEM_V2 + """
Common mistakes to avoid:
- `keys` takes EXACT full metric names; for a partial name always use `pattern` (a substring), never `keys`.
- Worst setup slack = the SMALLEST (most negative or closest to zero) value among the per-corner values, not the largest. Name that corner.
- For 'most', 'largest', 'smallest' across designs, call compare_designs with ONLY the metric (omit designs). Its list is sorted ascending: the first entry is the smallest, the last is the largest. Skip entries with value 0 when asked about designs that have standard cells.
- Worked examples: Q: 'std cells of vision_block?' -> read_metrics(design='vision_block', pattern='count__stdcell') -> answer the value of design__instance__count__stdcell. Q: 'which design has the smallest std-cell area?' -> compare_designs(metric='design__instance__area__stdcell') -> take the first non-zero entry."""

SYSTEM = SYSTEM_V3

MASTER_PROMPT_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "prompts", "master_prompt.txt")


def context_enabled():
    return os.environ.get("HERMES_CONTEXT", "") not in ("", "0", "false", "no")


def with_context(system):
    """The system prompt unchanged (default), or with the generated master prompt in front when HERMES_CONTEXT=1 / --context.
    Every agent loop of the repo passes its system text through here."""
    if not context_enabled():
        return system
    with open(MASTER_PROMPT_FILE, encoding="utf-8") as f:
        return f.read().strip() + "\n\n" + system


def _post(path, payload):
    if (path == "/api/chat" and os.environ.get("HERMES_THINK", "off") != "on" and "think" not in payload
            and str(payload.get("model", "")).startswith(NO_THINK_PREFIXES)):
        payload = dict(payload, think=False)
    req = urllib.request.Request(OLLAMA + path, json.dumps(payload).encode(),
                                 {"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as r:
        return json.load(r)


def log(*a):
    print(*a, file=sys.stderr, flush=True)


def run_tool(name, args):
    """Execute one tool through eda_tools.call only; never raises."""
    if name not in {t["function"]["name"] for t in eda_tools.TOOLS}:
        return {"error": f"unknown tool {name!r}"}
    if not isinstance(args, dict):
        args = {}
    try:
        res = eda_tools.call(name, args)
        # 8B models often put a substring into `keys`; retry those as `pattern` (still read-only, same tool)
        if name == "read_metrics" and isinstance(res, dict) and res.get("missing_keys"):
            merged = dict(res.get("metrics") or {})
            for k in res["missing_keys"]:
                r2 = eda_tools.call(name, {"design": args.get("design"), "pattern": k})
                merged.update((r2 or {}).get("metrics") or {})
            res = {"design": res.get("design"), "metrics": merged, "matched": len(merged),
                   "note": "some keys were not exact; they were matched as substrings"}
        return res
    except Exception as e:  # tool errors go back to the model as data
        return {"error": f"{type(e).__name__}: {e}"}


def _fmt(res):
    s = json.dumps(res, default=str)
    return s if len(s) <= RESULT_CHARS else s[:RESULT_CHARS] + f"...[truncated {len(s)-RESULT_CHARS} chars]"


# ---------------------------------------------------------------- Hermes prompt format (shared by every agent loop of the repo:
# examples/hermes_harness/harness.py, examples/hermes_rag/rag_agent.py, examples/hermes_klayout_gui/agent.py). Byte-stable:
# the recorded eval results and the harness trace prompt_sha depend on these exact strings.
def tools_suffix(schemas):
    """The Hermes function-calling paragraph appended to a system prompt: the tool list and the <tool_call> format."""
    tools = json.dumps([t["function"] for t in schemas])
    return ("\n\nYou are a function calling AI model. You may call one or more functions to assist with the user query. "
            "Don't make assumptions about what values to plug into functions. Here are the available tools:\n"
            f"<tools> {tools} </tools>\n"
            "For each function call return a json object with function name and arguments within <tool_call></tool_call> XML tags:\n"
            '<tool_call>\n{"name": <function-name>, "arguments": <args-dict>}\n</tool_call>')


def tool_call_text(name, args):
    """One tool call as the model writes it (used when code, not the model, makes a call: plan, auto-retrieve, router)."""
    return "<tool_call>\n%s\n</tool_call>" % json.dumps({"name": name, "arguments": args})


def tool_block(name, txt):
    """One tool result inside a prompt-mode tool message."""
    return json.dumps({"name": name, "content": txt})


def tool_message(blocks):
    """The prompt-mode tool message carrying the results of one model turn (tool_block strings)."""
    return {"role": "tool", "content": "\n</tool_response>\n<tool_response>\n".join(blocks)}


def _hermes_system():
    return with_context(SYSTEM) + tools_suffix(eda_tools.TOOLS)


def parse_tool_calls(text, lenient=False):
    """[(name, arguments)] of the <tool_call>{json}</tool_call> blocks in a reply; malformed JSON is skipped.
    lenient: when nothing parses but a <tool_call> tag is there, also accept a block whose closing tag the server ate."""
    out = []
    for m in re.finditer(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", text, re.S):
        try:
            d = json.loads(m.group(1))
            out.append((d.get("name"), d.get("arguments") or d.get("parameters") or {}))
        except json.JSONDecodeError:
            pass
    if lenient and not out and "<tool_call>" in text:                    # Ollama sometimes eats the closing tag
        for raw in re.findall(r"<tool_call>\s*(\{.*)", text, re.S):
            try:
                d = json.loads(raw.strip().rstrip("<>/tool_call").strip())
                out.append((d.get("name"), d.get("arguments") or {}))
            except Exception:
                pass
    return out


_parse_tool_calls = parse_tool_calls      # the old private name (examples and tests use it)


def ask(question, mode="prompt", model=MODEL, system=None, extra_messages=None, verbose=True):
    """Returns dict(answer, tool_calls=[{name,args,result_chars,error}], seconds, eval_tokens, eval_seconds)."""
    t0 = time.time()
    opts = dict(OPTS)
    sysmsg = system or (with_context(SYSTEM) if mode == "native" else _hermes_system())
    if mode == "prompt" and system is None:
        sysmsg = _hermes_system()
    msgs = [{"role": "system", "content": sysmsg}] + (extra_messages or []) + [{"role": "user", "content": question}]
    calls, toks, tsec = [], 0, 0.0
    answer = ""
    while True:
        payload = {"model": model, "messages": msgs, "stream": False, "options": opts}
        if mode == "native":
            payload["tools"] = eda_tools.TOOLS
        r = _post("/api/chat", payload)
        toks += r.get("eval_count", 0)
        tsec += r.get("eval_duration", 0) / 1e9
        m = r["message"]
        content = m.get("content") or ""
        if mode == "native":
            tcs = [(c["function"]["name"], c["function"].get("arguments") or {}) for c in m.get("tool_calls") or []]
        else:
            tcs = _parse_tool_calls(content)
        if not tcs:
            answer = content.strip()
            break
        msgs.append(m if mode == "native" else {"role": "assistant", "content": content})
        resp_blocks = []
        for name, args in tcs:
            if len(calls) >= MAX_TOOL_CALLS:
                res = {"error": BUDGET_MSG}
            else:
                res = run_tool(name, args)
                calls.append({"name": name, "args": args, "error": "error" in res if isinstance(res, dict) else False,
                              "result_chars": len(json.dumps(res, default=str))})
            txt = _fmt(res)
            if verbose:
                log(f"[tool {len(calls)}] {name}({json.dumps(args)}) -> {txt[:300]}")
            if mode == "native":
                msgs.append({"role": "tool", "content": txt, "tool_name": name})
            else:
                resp_blocks.append(tool_block(name, txt))
        if mode == "prompt":
            msgs.append(tool_message(resp_blocks))
        if len(msgs) > 40:
            answer = "unknown (too many steps)"
            break
    return {"answer": answer, "tool_calls": calls, "seconds": round(time.time() - t0, 2),
            "eval_tokens": toks, "eval_seconds": round(tsec, 2)}


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("question", nargs="*")
    ap.add_argument("--mode", choices=["native", "prompt"], default="prompt")
    ap.add_argument("--model", default=MODEL)
    ap.add_argument("--context", action="store_true", help="prepend the repo master prompt (same as env HERMES_CONTEXT=1)")
    a = ap.parse_args()
    if a.context:
        os.environ["HERMES_CONTEXT"] = "1"
    if a.question:
        r = ask(" ".join(a.question), a.mode, a.model)
        print(r["answer"])
        log(f"[{r['seconds']}s, {len(r['tool_calls'])} tool calls, {r['eval_tokens']} tokens]")
        return
    log(f"Hermes agent ({a.model}, {a.mode}); empty line or Ctrl-D to quit")
    while True:
        try:
            q = input("? ").strip()
        except EOFError:
            break
        if not q:
            break
        r = ask(q, a.mode, a.model)
        print(r["answer"], flush=True)
        log(f"[{r['seconds']}s, {len(r['tool_calls'])} tool calls]")


if __name__ == "__main__":
    main()
