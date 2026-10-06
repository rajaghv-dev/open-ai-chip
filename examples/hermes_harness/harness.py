#!/usr/bin/env python3
"""A tiny, educational HARNESS around a local Hermes model + the read-only KLayout/EDA tools.
Docs: examples/hermes_harness/README.md, docs/HERMES_FROM_TERMINAL.md

The model (hermes3:8b via Ollama) is fixed. Everything else is the harness, and each feature is a few lines:

  --guardrails   validate tool arguments against the JSON schema before calling; bounded retry with the error
  --grounding    check that numbers/design names in the final answer appear in tool output; one revision
  --pick-extreme add a deterministic tool pick_extreme(metric, which, designs, scope) (code does min/max)
  --plan         plan-then-execute instead of pure ReAct
  (tracing is always on: build/agent/traces/<timestamp>.jsonl)

  build/agent/venv/bin/python examples/hermes_harness/harness.py --all "Which prec_* design has the most std cells?"
"""
import argparse, hashlib, json, os, re, sys, time
from dataclasses import dataclass, asdict

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(ROOT, "tools"))
import eda_tools, hermes_agent  # noqa: E402  (reused: tools + prompt/HTTP helpers)

OPTS = dict(hermes_agent.OPTS)   # deterministic decoding: temperature 0, seed 42, num_ctx 8192, num_predict 700
TRACE_DIR = os.path.join(ROOT, "build", "agent", "traces")


# ============================================================ 0. CONFIG: one switch per idea
@dataclass
class Config:
    guardrails: bool = False
    grounding: bool = False
    pick_extreme: bool = False
    plan: bool = False
    max_calls: int = 6          # LOOP budget: tool calls per episode (same as tools/hermes_agent.py)
    max_steps: int = 10         # LOOP budget: model turns per episode
    max_seconds: float = 120.0  # LOOP budget: wall clock
    max_arg_retries: int = 3    # GUARDRAILS: bounded retries on invalid arguments
    max_revisions: int = 1      # GROUNDING: allowed answer revisions


# ============================================================ 1. TRACING: JSONL, one line per event
class Tracer:
    def __init__(self, tag=""):
        os.makedirs(TRACE_DIR, exist_ok=True)
        self.path = os.path.join(TRACE_DIR, time.strftime("%Y%m%d_%H%M%S") + (f"_{tag}" if tag else "") + ".jsonl")
        self.episode = 0

    def event(self, kind, **kw):
        with open(self.path, "a") as f:
            f.write(json.dumps({"t": round(time.time(), 3), "episode": self.episode, "event": kind, **kw}, default=str) + "\n")


# ============================================================ 2. DETERMINISTIC HELPER TOOL
PICK_SCHEMA = {"type": "function", "function": {
    "name": "pick_extreme",
    "description": "Deterministically find the design with the smallest or largest value of a metric (computed by code, not by you). "
                   "metric = exact metrics key or a substring (a substring is searched in every key, e.g. 'setup__ws__corner' = all corners; "
                   "the winning key names the corner). Zero counts/areas are ignored. scope = design-name prefix such as 'prec_'.",
    "parameters": {"type": "object", "properties": {
        "metric": {"type": "string"}, "which": {"type": "string", "enum": ["min", "max"]},
        "designs": {"type": ["array", "null"], "items": {"type": "string"}},
        "scope": {"type": ["string", "null"]}}, "required": ["metric", "which"]}}}


def pick_extreme(metric, which="min", designs=None, scope=None):
    names = designs or [d["design"] for d in eda_tools.list_designs()["designs"] if d["hardened"]]
    if scope:
        names = [d for d in names if d.startswith(scope)]
        if not names:                            # an informative error lets the model fix a bad scope
            return {"error": f"scope {scope!r} matches no design; scope is a design-name PREFIX such as 'prec_' (or omit it)"}
    cands = []                                   # (value, design, key)
    for d in names:
        r = eda_tools.call("read_metrics", {"design": d, "pattern": metric})
        if "error" in r:
            return r
        for k, v in (r.get("metrics") or {}).items():
            val = v["value"]
            if isinstance(val, (int, float)) and not isinstance(val, bool):
                cands.append((val, d, k))
    if re.search(r"count|area", metric):         # a 0 there means "design has none of these", not a real minimum
        cands = [c for c in cands if c[0] != 0]
    if not cands:
        return {"error": f"no numeric values for metric {metric!r}; use read_metrics to find the key"}
    cands.sort(key=lambda c: c[0], reverse=(which == "max"))
    row = lambda c: {"design": c[1], "key": c[2], "value": c[0]}
    return {"metric": metric, "which": which, "winner": row(cands[0]),
            "next_best": [row(c) for c in cands[1:4]], "candidates": len(cands)}


# ============================================================ 3. TOOL LAYER: allow-list, validation, execution
def tool_schemas(cfg):
    if cfg.pick_extreme:   # the helper REPLACES compare_designs: v1 left both and the model never chose the new one
        return [t for t in eda_tools.TOOLS if t["function"]["name"] != "compare_designs"] + [PICK_SCHEMA]
    return eda_tools.TOOLS


def validate_args(name, args, schemas):
    """GUARDRAIL: return an error string or None. Allow-list + JSON-schema check of the arguments."""
    import jsonschema
    sch = {s["function"]["name"]: s["function"]["parameters"] for s in schemas}
    if name not in sch:
        return f"tool {name!r} is not allowed; allowed tools: {', '.join(sch)}"
    if not isinstance(args, dict):
        return "arguments must be a JSON object"
    try:
        jsonschema.validate(args, {**sch[name], "additionalProperties": False})
    except jsonschema.ValidationError as e:
        return f"invalid arguments for {name}: {e.message}"
    return None


def execute(name, args, cfg):
    """Read-only by construction: only eda_tools.call (read-only) or pick_extreme (built on it) can run."""
    if name == "pick_extreme" and cfg.pick_extreme:
        try:
            return pick_extreme(**args)
        except Exception as e:
            return {"error": f"{type(e).__name__}: {e}"}
    return hermes_agent.run_tool(name, args)


# ============================================================ 4. GROUNDING CHECK
# Not merged with tools/eval/run_eval.numbers_in on purpose: that one scores the recorded evals (it keeps the "2" of um^2).
NUM = re.compile(r"(?<![\w.])-?\d+(?:\.\d+)?")


def _nums(text):
    text = re.sub(r"\^\d", "", text.replace(",", ""))     # "um^2" is a unit, not the number 2 (v1 bug: caused a false alarm)
    return [float(x) for x in NUM.findall(text)]


def _close(a, b):
    return abs(a - b) <= max(abs(b) * 0.005, 0.006)      # 0.5% or rounding to 2 decimals


def grounding_check(question, answer, results):
    """Return list of ungrounded claims. Numbers must appear in a tool result (or be a sum/difference/ratio of
    two tool numbers); design names must appear in a tool result or the question."""
    body = hermes_agent_strip(answer)
    blob = " ".join(json.dumps(r, default=str) for r in results)
    have = _nums(blob)
    asked = set(_nums(question))
    bad = []
    for x in dict.fromkeys(_nums(body)):
        if x in asked or any(_close(x, h) for h in have):
            continue
        derived = {a + b for a in have[:80] for b in have[:80]} | {abs(a - b) for a in have[:80] for b in have[:80]} | \
                  {a / b for a in have[:80] for b in have[:80] if b}
        if not any(_close(x, d) for d in derived):
            bad.append(str(x))
    designs = [d["design"] for d in eda_tools.list_designs()["designs"]]
    for d in designs:
        if d in body and d not in blob and d not in question:
            bad.append(d)
    return bad


# Differs from tools/eval/run_eval.strip_source on purpose (that is the scorer; changing either moves recorded scores).
def hermes_agent_strip(ans):                              # drop the "Source: ..." tail
    return re.split(r"\n?\s*\(?Source", ans, maxsplit=1, flags=re.I)[0]


# ============================================================ 5. THE LOOP (ReAct, optional plan seed)
def chat(msgs):
    return hermes_agent._post("/api/chat", {"model": hermes_agent.MODEL, "messages": msgs, "stream": False, "options": OPTS})


def system_prompt(cfg):
    base = hermes_agent.with_context(hermes_agent.SYSTEM)
    if cfg.pick_extreme:
        base = base.replace("compare_designs", "pick_extreme")   # the recipes in the prompt must name the tool that exists
        base += ("\n- For 'which design has the most/least/smallest/largest X' and for the WORST (smallest) value across timing corners, "
                 "ALWAYS call pick_extreme instead of read_metrics or comparing numbers yourself. Omit designs and scope unless the question "
                 "names them. Examples: 'which design has the smallest std-cell area' -> pick_extreme(metric='design__instance__area__stdcell', "
                 "which='min'); 'most std cells among prec_ variants' -> pick_extreme(metric='design__instance__count__stdcell', which='max', "
                 "scope='prec_'); 'worst setup slack of D and its corner' -> pick_extreme(metric='timing__setup__ws__corner', which='min', "
                 "designs=['D']) and the winner's key names the corner. Use the 'winner' field as the answer.")
    return base + hermes_agent.tools_suffix(tool_schemas(cfg))     # the Hermes tool list + <tool_call> format


PLAN_PROMPT = ("Before answering, write a short plan: the list of tool calls you need, in order, as JSON inside "
               "<plan></plan>, e.g. <plan>[{\"name\": \"read_metrics\", \"arguments\": {\"design\": \"X\", \"pattern\": \"count__stdcell\"}}]</plan>. "
               "If no tool can answer (price, yield, schedule), write <plan>[]</plan>. Output only the plan.\nQuestion: ")


def run_episode(question, cfg, tracer=None):
    tracer = tracer or Tracer()
    tracer.episode += 1
    t0 = time.time()
    sysmsg = system_prompt(cfg)
    schemas = tool_schemas(cfg)
    tracer.event("start", question=question, config=asdict(cfg), prompt_sha=hashlib.sha256(sysmsg.encode()).hexdigest()[:12])
    msgs = [{"role": "system", "content": sysmsg}, {"role": "user", "content": question}]
    calls, results, retries, revisions, arg_retries = [], [], 0, 0, 0
    verdict, answer, steps = None, "", 0

    def do_calls(tcs):                                   # --- validate -> execute -> observe
        nonlocal retries, arg_retries
        blocks = []
        for name, args in tcs:
            err = validate_args(name, args, schemas) if cfg.guardrails else None
            if err and arg_retries < cfg.max_arg_retries:
                arg_retries += 1; retries += 1
                res = {"error": err + ". Fix the arguments and call again."}
                tracer.event("validation_error", tool=name, args=args, error=err)
            elif len(calls) >= cfg.max_calls:
                res = {"error": hermes_agent.BUDGET_MSG}
            else:
                t1 = time.time()
                res = execute(name, args, cfg)
                results.append(res)
                calls.append({"name": name, "args": args, "error": isinstance(res, dict) and "error" in res})
                tracer.event("tool", tool=name, args=args, result_chars=len(json.dumps(res, default=str)), secs=round(time.time() - t1, 3))
            blocks.append(hermes_agent.tool_block(name, hermes_agent._fmt(res)))
        msgs.append(hermes_agent.tool_message(blocks))

    if cfg.plan:                                          # PLAN-THEN-EXECUTE: the model writes calls, code runs them
        r = chat([msgs[0], {"role": "user", "content": PLAN_PROMPT + question}])["message"]["content"]
        tracer.event("plan", output=r)
        m = re.search(r"<plan>(.*?)</plan>", r, re.S)
        try:
            plan = [(p["name"], p.get("arguments") or {}) for p in json.loads(m.group(1))][:cfg.max_calls]
        except Exception:
            plan = []
        if plan:
            msgs.append({"role": "assistant", "content": "".join(hermes_agent.tool_call_text(n, a) + "\n" for n, a in plan)})
            do_calls(plan)                                # then fall into the normal loop to write the answer

    while steps < cfg.max_steps and time.time() - t0 < cfg.max_seconds:   # REACT LOOP: act -> observe -> repeat
        steps += 1
        t1 = time.time()
        content = chat(msgs)["message"].get("content") or ""
        tracer.event("model", step=steps, output=content[:1500], secs=round(time.time() - t1, 2))
        tcs = hermes_agent._parse_tool_calls(content)
        if tcs:
            msgs.append({"role": "assistant", "content": content})
            do_calls(tcs)
            continue
        answer = content.strip()
        if cfg.grounding and revisions < cfg.max_revisions and answer:
            bad = grounding_check(question, answer, results)
            verdict = {"ok": not bad, "ungrounded": bad}
            tracer.event("grounding", **verdict)
            if bad:                                       # one corrective turn, then accept whatever comes back
                revisions += 1; retries += 1
                msgs += [{"role": "assistant", "content": answer},
                         {"role": "user", "content": f"These numbers/names do not appear in any tool output: {', '.join(bad)}. "
                          "Answer only from tool results (call a tool if you need data), or say unknown."}]
                continue
        break
    else:
        answer = answer or "unknown (step or time budget exhausted)"
    out = {"answer": answer, "tool_calls": calls, "seconds": round(time.time() - t0, 2), "retries": retries,
           "steps": steps, "grounding": verdict}
    tracer.event("end", **{k: out[k] for k in ("answer", "seconds", "retries", "steps")}, n_calls=len(calls))
    return out


# ============================================================ 6. CLI
def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("question", nargs="+")
    for f in ("guardrails", "grounding", "pick-extreme", "plan", "all"):
        ap.add_argument("--" + f, action="store_true")
    ap.add_argument("--model", default=None, help="Ollama model tag (default: env HERMES_MODEL, else hermes3:8b)")
    ap.add_argument("--context", action="store_true", help="prepend the repo master prompt (env HERMES_CONTEXT=1)")
    a = ap.parse_args()
    if a.context:
        os.environ["HERMES_CONTEXT"] = "1"
    if a.model:
        hermes_agent.MODEL = a.model
    cfg = Config(a.guardrails or a.all, a.grounding or a.all, a.pick_extreme or a.all, a.plan)
    tr = Tracer()
    r = run_episode(" ".join(a.question), cfg, tr)
    print(r["answer"])
    print(f"[{r['seconds']}s, {len(r['tool_calls'])} tool calls, {r['retries']} retries, grounding={r['grounding']}] trace: {tr.path}", file=sys.stderr)


if __name__ == "__main__":
    main()
