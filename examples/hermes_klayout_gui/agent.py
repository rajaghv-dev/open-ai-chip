#!/usr/bin/env python3
"""Hermes (text-only, local) drives a KLayout VIEW with 7 coarse read-only commands.

  build/agent/venv/bin/python examples/hermes_klayout_gui/agent.py --dry-run          # scripted, no Ollama
  build/agent/venv/bin/python examples/hermes_klayout_gui/agent.py "open kv_attn_n8, show li1 and met1, zoom to the lower-left 50x50 um, snapshot"
  ... --backend live        # needs the KLayout desktop bridge (README_live.md)

The loop is the harness-style ReAct loop of examples/hermes_harness/harness.py (prompt-mode Hermes function calling,
same parser and HTTP helper from tools/hermes_agent.py) with three additions: the 7 view tools instead of the EDA tools,
argument validation in view_api.dispatch, and a deterministic ROUTER guardrail (see route()).
The model never sees pixels: it gets JSON (bbox, visible layers, PNG path) and decides the next command.
"""
import argparse
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import view_api as va  # noqa: E402
sys.path.insert(0, os.path.join(va.REPO, "tools"))
import eda_tools, hermes_agent  # noqa: E402

OPTS = {"temperature": 0, "seed": 42, "num_ctx": 8192, "num_predict": 400}
MAX_STEPS, MAX_CALLS, MAX_SECONDS = 10, 8, 240

SYSTEM = """You control a KLayout layout viewer for the open-ai-chip repository through function calls. You are a text-only model: you cannot see the picture. You only receive JSON results (bounding boxes in um, visible layers, PNG file paths), so never describe what the layout "looks like". Everything is read-only; no layout file can be changed.
Rules:
- Always call open_design first (exact design name, for example kv_attn_n8, tiny_ai_core, user_project_wrapper_soc_kv). Its result gives bbox_um = [x1, y1, x2, y2] in um.
- One command does one thing: open_design, then show_layers, zoom_to, highlight_drc, measure, snapshot. Do the steps the user asked for, in order, one call per step.
- Layers: show_layers(layers=["met4","met5"], only=true) shows only those; li1, met1..met5, poly, diff, via..via4 also work.
- zoom_to(target={"cell":"mprj"}) zooms to a macro by cell or instance name; zoom_to(target={"bbox":[x1,y1,x2,y2]}) uses um; zoom_to(target={"full":true}) shows everything. 'Lower-left 50x50 um' means bbox [x1, y1, x1+50, y1+50] using bbox_um from open_design.
- highlight_drc(design=D) reads the real DRC report: 0 markers means the design is DRC-clean; say so honestly. Only when the user asks for demo or example markers use demo_markers=true, and say they are demo boxes, not real errors.
- snapshot() renders the current view to a PNG; call it with no arguments when the user wants to see, render, snapshot or screenshot; report the png path from the result.
- Final answer: one or two short sentences with the facts from the tool results (design, layers, bbox, png path, n_markers). Never invent numbers."""


def system_prompt(with_metrics=False):
    tools = list(va.TOOLS)
    if with_metrics:
        tools += [t for t in eda_tools.TOOLS if t["function"]["name"] == "read_metrics"]
    blob = json.dumps([t["function"] for t in tools])
    return (SYSTEM + "\n\nYou are a function calling AI model. You may call one or more functions to assist with the user query. "
            f"Don't make assumptions about what values to plug into functions. Here are the available tools:\n<tools> {blob} </tools>\n"
            "For each function call return a json object with function name and arguments within <tool_call></tool_call> XML tags:\n"
            '<tool_call>\n{"name": <function-name>, "arguments": <args-dict>}\n</tool_call>')


# ============================================================ deterministic router (guardrail)
VIEW_WORDS = re.compile(r"\b(show|display|view|zoom|highlight|snapshot|screenshot|render|picture|image|png|open|measure|distance|drc|markers?|layers?)\b|\bmet[1-5]\b|\bli1\b", re.I)
RULES = [  # first matching rule wins when the design is already open
    ("snapshot", re.compile(r"snapshot|screenshot|render|png|picture|image", re.I)),
    ("highlight_drc", re.compile(r"highlight|drc|marker", re.I)),
    ("measure", re.compile(r"measure|distance", re.I)),
    ("zoom_to", re.compile(r"zoom|lower.left|macro|full view", re.I)),
    ("show_layers", re.compile(r"\bshow\b|\bonly\b|layers?|\bmet[1-5]\b|\bli1\b|poly", re.I)),
]


def design_in(text):
    """Longest design name mentioned in text (exact, word-bounded), else None."""
    best = None
    for d in eda_tools._designs():
        if re.search(r"(?<![A-Za-z0-9_])%s(?![A-Za-z0-9_])" % re.escape(d), text) and (best is None or len(d) > len(best)):
            best = d
    return best


def is_view_request(text):
    return bool(VIEW_WORDS.search(text))


def route(text, opened=None):
    """Request text -> the right FIRST tool call as {"tool", "args"}, or None if it is not a view request.
    open_design if a design is named and not yet open; otherwise the first rule (snapshot > highlight > measure > zoom > show) that matches."""
    if not is_view_request(text):
        return None
    d = design_in(text)
    if d and d != opened:
        return {"tool": "open_design", "args": {"design": d}}
    for tool, rx in RULES:
        if rx.search(text):
            if tool == "snapshot":
                return {"tool": tool, "args": {}}
            if tool == "highlight_drc" and (d or opened):
                return {"tool": tool, "args": {"design": d or opened}}
            if tool == "zoom_to":
                return {"tool": tool, "args": {"target": {"full": True}}}
            if tool == "show_layers":
                ls = re.findall(r"\bmet[1-5]\b|\bli1\b|\bpoly\b|\bdiff\b", text, re.I)
                if ls:
                    return {"tool": tool, "args": {"layers": [x.lower() for x in dict.fromkeys(ls)], "only": True}}
                continue
    return {"tool": "state", "args": {}}


# ============================================================ scripted scenarios (shared with demo.py)
SCENARIOS = [
    {"id": "wrapper_met45_mprj",
     "request": "Open user_project_wrapper_soc_kv, show only met4 and met5, zoom to the macro mprj and take a snapshot.",
     "script": [("open_design", {"design": "user_project_wrapper_soc_kv"}),
                ("show_layers", {"layers": ["met4", "met5"], "only": True}),
                ("zoom_to", {"target": {"cell": "mprj"}}),
                ("snapshot", {"path": "build/agent/klayout_gui/demo_wrapper_met45_mprj.png"})]},
    {"id": "kv8_li1_met1_corner",
     "request": "Open kv_attn_n8, show li1 and met1, zoom to the lower-left 50x50 um and take a snapshot.",
     "script": [("open_design", {"design": "kv_attn_n8"}),
                ("show_layers", {"layers": ["li1", "met1"], "only": True}),
                ("zoom_to", {"target": {"bbox": "LOWER_LEFT_50"}}),
                ("snapshot", {"path": "build/agent/klayout_gui/demo_kv8_li1_met1.png"})]},
    {"id": "tiny_drc_honest",
     "request": "Open tiny_ai_core and highlight the DRC markers.",
     "expect": ["open_design", "highlight_drc"],      # the request does not ask for a picture
     "script": [("open_design", {"design": "tiny_ai_core"}),
                ("highlight_drc", {"design": "tiny_ai_core"})]},
    {"id": "tiny_demo_markers",
     "request": "Open tiny_ai_core and highlight demo markers so I can see the feature, then take a snapshot.",
     "script": [("open_design", {"design": "tiny_ai_core"}),
                ("highlight_drc", {"design": "tiny_ai_core", "demo_markers": True}),
                ("snapshot", {"path": "build/agent/klayout_gui/demo_tiny_markers.png"})]},
    {"id": "vision_measure",
     "request": "Open vision_block, zoom to the full design and measure the distance between (10, 10) and (70, 50) um.",
     "script": [("open_design", {"design": "vision_block"}),
                ("zoom_to", {"target": {"full": True}}),
                ("measure", {"a": [10, 10], "b": [70, 50]})]},
]
for _s in SCENARIOS:
    _s.setdefault("expect", [t for t, _ in _s["script"]])


# ============================================================ model back ends: live Hermes or scripted
class Scripted:
    """--dry-run 'model': emits the scripted calls one per turn through the same loop, then a templated answer."""

    def __init__(self, script):
        self.script, self.i = list(script), 0
        self.last = {}

    def __call__(self, msgs):
        for m in msgs[-1:]:
            if m["role"] == "tool":
                try:
                    self.last = json.loads(re.findall(r"\{.*\}", m["content"], re.S)[0]).get("content") or {}
                    if isinstance(self.last, str):
                        self.last = json.loads(self.last.split("...[truncated")[0])
                except Exception:
                    pass
        if self.i < len(self.script):
            name, args = self.script[self.i]
            self.i += 1
            if isinstance(args.get("target", {}).get("bbox"), str):          # LOWER_LEFT_50 -> from open_design's bbox
                bb = self._bbox(msgs)
                args = {"target": {"bbox": [bb[0], bb[1], bb[0] + 50, bb[1] + 50]}}
            return '<tool_call>\n%s\n</tool_call>' % json.dumps({"name": name, "arguments": args})
        return "(scripted answer) Done. Last tool result: %s" % json.dumps(self.last)[:400]

    @staticmethod
    def _bbox(msgs):
        for m in msgs:
            if m["role"] == "tool" and "bbox_um" in m["content"]:
                s = re.search(r'bbox_um\\?": \[([^\]]+)\]', m["content"])
                return [float(x) for x in s.group(1).split(",")]
        return [0, 0, 100, 100]


def live_model(msgs):
    r = hermes_agent._post("/api/chat", {"model": hermes_agent.MODEL, "messages": msgs, "stream": False, "options": OPTS})
    return r["message"].get("content") or "", r.get("eval_count", 0)


# ============================================================ the loop
class Transcript:
    def __init__(self, sink=None):
        self.lines, self.sink = [], sink

    def __call__(self, line=""):
        self.lines.append(line)
        print(line, flush=True)
        if self.sink:
            self.sink.write(line + "\n")
            self.sink.flush()


def run_episode(request, backend, model, emit=None, with_metrics=False, use_router=True):
    """ReAct loop. Returns {answer, calls:[{name,args,ok,src}], pngs, seconds, steps, router_used, rejected}."""
    emit = emit or Transcript()
    t0 = time.time()
    msgs = [{"role": "system", "content": system_prompt(with_metrics)}, {"role": "user", "content": request}]
    calls, pngs, steps, rejected, router_used, tokens = [], [], 0, 0, False, 0
    opened = None
    emit("REQUEST  %s" % request)

    def do(name, args, src):
        nonlocal opened
        t1 = time.time()
        if name == "read_metrics" and with_metrics:
            res = hermes_agent.run_tool(name, args)
        else:
            res = va.dispatch(backend, name, args)
        ok = isinstance(res, dict) and res.get("ok", "error" not in res)
        if name == "open_design" and ok:
            opened = args.get("design")
        if name == "snapshot" and ok:
            pngs.append(res["png"])
        calls.append({"name": name, "args": args, "ok": bool(ok), "src": src})
        short = json.dumps(res, default=str)
        emit("  [%s] %s(%s)  (%.2fs)" % (src, name, json.dumps(args), time.time() - t1))
        emit("        -> %s" % (short if len(short) < 420 else short[:420] + "..."))
        return res

    answer = ""
    while steps < MAX_STEPS and time.time() - t0 < MAX_SECONDS:
        steps += 1
        out = model(msgs)
        content, tok = out if isinstance(out, tuple) else (out, 0)
        tokens += tok
        tcs = hermes_agent._parse_tool_calls(content)
        if not tcs and "<tool_call>" in content:                      # Ollama sometimes eats the closing tag
            for raw in re.findall(r"<tool_call>\s*(\{.*)", content, re.S):
                try:
                    d = json.loads(raw.strip().rstrip("<>/tool_call").strip())
                    tcs.append((d.get("name"), d.get("arguments") or {}))
                except Exception:
                    pass
        if tcs:
            msgs.append({"role": "assistant", "content": content})
            blocks = []
            for name, args in tcs:
                if len(calls) >= MAX_CALLS:
                    res = {"ok": False, "error": "tool call budget exhausted; answer from what you have"}
                else:
                    res = do(name, args, "model")
                blocks.append(json.dumps({"name": name, "content": hermes_agent._fmt(res)}))
            msgs.append({"role": "tool", "content": "\n</tool_response>\n<tool_response>\n".join(blocks)})
            continue
        # no tool call: GUARDRAIL for view requests
        view_calls = [c for c in calls if c["name"] in va.TOOL_NAMES]
        suggestion = route(request, opened) if use_router else None
        if suggestion and not view_calls:
            if rejected == 0:
                rejected = 1
                emit("  [guardrail] rejected: view request answered without a view tool; router suggests %s" % suggestion["tool"])
                msgs += [{"role": "assistant", "content": content},
                         {"role": "user", "content": "This is a viewer request. Do not answer yet: call the tool %s with %s first."
                          % (suggestion["tool"], json.dumps(suggestion["args"]))}]
                continue
            router_used = True                                         # second miss: the code makes the call itself
            res = do(suggestion["tool"], suggestion["args"], "router")
            msgs += [{"role": "assistant", "content": '<tool_call>\n%s\n</tool_call>' % json.dumps(
                {"name": suggestion["tool"], "arguments": suggestion["args"]})},
                {"role": "tool", "content": json.dumps({"name": suggestion["tool"], "content": hermes_agent._fmt(res)})}]
            continue
        answer = content.strip()
        break
    else:
        answer = "unknown (step or time budget exhausted)"
    secs = round(time.time() - t0, 2)
    emit("ANSWER   %s" % answer)
    emit("PNG      %s" % (", ".join(pngs) if pngs else "(none)"))
    emit("STATS    %.2fs, %d steps, %d tool calls (%s)%s" % (secs, steps, len(calls), " > ".join(c["name"] for c in calls),
                                                             ", router intervened" if router_used else ""))
    return {"answer": answer, "calls": calls, "pngs": pngs, "seconds": secs, "steps": steps, "tokens": tokens,
            "router_used": router_used, "rejected": rejected}


def seq_ok(calls, expect):
    """True if expect is an in-order subsequence of the successful model/router calls."""
    it = iter(c["name"] for c in calls if c["ok"])
    return all(any(x == e for x in it) for e in expect)


def make_backend(kind):
    if kind == "offscreen":
        from offscreen_backend import OffscreenBackend
        return OffscreenBackend()
    try:
        from live_backend import LiveBackend
        return LiveBackend()
    except ImportError as e:
        sys.exit("live backend not available (%s): the option-B bridge (live_backend.py, README_live.md) is missing. "
                 "Use --backend offscreen." % e)
    except Exception as e:  # noqa: BLE001
        sys.exit("live backend is not running (%s). Start the KLayout bridge as described in README_live.md, "
                 "or use --backend offscreen." % e)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("request", nargs="*")
    ap.add_argument("--backend", choices=["offscreen", "live"], default="offscreen")
    ap.add_argument("--dry-run", action="store_true", help="scripted tool calls, no Ollama (uses --scenario)")
    ap.add_argument("--scenario", type=int, default=0, help="index into SCENARIOS for --dry-run without a request")
    ap.add_argument("--with-metrics", action="store_true", help="also give the model eda_tools.read_metrics")
    a = ap.parse_args()
    backend = make_backend(a.backend)
    if a.dry_run:
        sc = SCENARIOS[a.scenario]
        req = " ".join(a.request) or sc["request"]
        print("(dry run: scripted model, real %s backend, no Ollama)" % a.backend)
        run_episode(req, backend, Scripted(sc["script"]), with_metrics=a.with_metrics)
    else:
        if not a.request:
            ap.error("give a request or use --dry-run")
        try:
            run_episode(" ".join(a.request), backend, live_model, with_metrics=a.with_metrics)
        except OSError as e:
            sys.exit("Ollama not reachable (%s). Start it: ollama serve ; ollama pull hermes3:8b ; or use --dry-run" % e)
    backend.close()


if __name__ == "__main__":
    main()
