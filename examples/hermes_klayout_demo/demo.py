#!/usr/bin/env python3
"""Hermes + KLayout in ~100 lines: a small local model answers from the real GDS.

Run:  build/agent/venv/bin/python examples/hermes_klayout_demo/demo.py [--dry-run] ["question"]
Minimal on purpose: read-only, 2 tools, 3 steps max. Not fully functional (see README).
Needs the GDS first:  make collect DESIGN=tiny_ai_core
"""
import argparse, glob, json, os, re, sys, urllib.request
import klayout.db as db                       # KLayout's Python API: GDS as data

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
LAYERS = {"met1": (68, 20), "met2": (69, 20), "met3": (70, 20), "met4": (71, 20)}  # sky130 GDS layer/datatype

# ---------------------------------------------------------------- TOOLS (the precise part)
def _top_cell(design):
    """Validate the name against designs/*/config.json, load the GDS, return (layout, top cell)."""
    known = [os.path.basename(os.path.dirname(p)) for p in glob.glob(f"{REPO}/designs/*/config.json")]
    if design not in known:
        raise ValueError(f"unknown design {design!r}; known: {sorted(known)[:5]}...")
    top = json.load(open(f"{REPO}/designs/{design}/config.json"))["DESIGN_NAME"]
    gds = f"{REPO}/build/results/{design}/{top}.gds"
    if not os.path.exists(gds):
        raise FileNotFoundError(f"{gds} missing; produce it with: make collect DESIGN={design}")
    ly = db.Layout(); ly.read(gds)
    return ly, ly.cell(top)

def die_size(design):
    """Width and height (um) of the top cell's bounding box."""
    ly, cell = _top_cell(design)
    box = cell.dbbox()                          # dbbox() is already in microns
    return {"width_um": round(box.width(), 3), "height_um": round(box.height(), 3)}

def count_shapes(design, layer):
    """Number of shapes on one metal layer, looking into sub-cells (hierarchy)."""
    if layer not in LAYERS:
        raise ValueError(f"layer must be one of {list(LAYERS)}")
    ly, cell = _top_cell(design)
    idx = ly.find_layer(*LAYERS[layer])
    # begin_shapes walks the hierarchy, so shapes inside sub-cells are counted too
    n = 0 if idx is None else sum(1 for _ in ly.begin_shapes(cell, idx))
    return {"layer": layer, "shapes": n}

TOOLS = {"die_size": die_size, "count_shapes": count_shapes}
SPECS = [  # what the model is told it can call (JSON schema, Hermes style)
    {"name": "die_size", "description": "Die width/height in um from a design's GDS.",
     "parameters": {"type": "object", "properties": {"design": {"type": "string"}}, "required": ["design"]}},
    {"name": "count_shapes", "description": "Count shapes on a metal layer (met1..met4) of a design's GDS.",
     "parameters": {"type": "object", "properties": {"design": {"type": "string"},
      "layer": {"type": "string", "enum": list(LAYERS)}}, "required": ["design", "layer"]}}]

# ---------------------------------------------------------------- AGENT LOOP (the thinking part)
SYSTEM = ("You are a function calling AI model. You may call functions to answer. Functions:\n"
          f"<tools>{json.dumps(SPECS)}</tools>\n"
          'To call one, reply ONLY with <tool_call>{"name": <name>, "arguments": <args>}</tool_call>.\n'
          "Answer ONLY from tool results, never from memory. When you have everything, reply in plain text.")

def ollama(messages):
    req = urllib.request.Request("http://localhost:11434/api/chat", json.dumps(
        {"model": "hermes3:8b", "messages": messages, "stream": False,
         "options": {"temperature": 0}}).encode(), {"Content-Type": "application/json"})
    return json.load(urllib.request.urlopen(req, timeout=120))["message"]["content"]

def scripted(step, msgs):  # --dry-run: a fake "model" that replays what a good model would say
    seen = " ; ".join(re.sub(r"</?tool_response>", "", m["content"]) for m in msgs if "<tool_response>" in m["content"])
    return ['<tool_call>{"name": "die_size", "arguments": {"design": "tiny_ai_core"}}</tool_call>',
            '<tool_call>{"name": "count_shapes", "arguments": {"design": "tiny_ai_core", "layer": "met4"}}</tool_call>',
            f"(scripted) Based on the tool results: {seen}"][step]

def run(question, dry):
    msgs = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": question}]
    print(f"QUESTION: {question}\n")
    for step in range(3):                                       # step cap: the model cannot loop forever
        reply = scripted(step, msgs) if dry else ollama(msgs)         # THINK: the model decides
        msgs.append({"role": "assistant", "content": reply})
        # The model may emit several calls; Ollama can eat the closing tag, so it is optional.
        calls = re.findall(r"<tool_call>\s*(\{.*?\})\s*(?:</tool_call>|(?=<tool_call>)|$)", reply, re.S)
        if not calls:                                           # no tool call = final answer
            print(f"[{step+1}] ANSWER  {reply.strip()}"); return
        for raw in calls:
            try:
                call = json.loads(raw)
                print(f"[{step+1}] ACT     {call['name']}({json.dumps(call['arguments'])})")
                result = TOOLS[call["name"]](**call["arguments"])   # the program executes, not the model
            except Exception as e:
                result = {"error": str(e)}                      # errors go back to the model too
            print(f"[{step+1}] OBSERVE {json.dumps(result)}")
            msgs.append({"role": "user", "content": f"<tool_response>{json.dumps(result)}</tool_response>"})
    print("STOPPED: step cap reached without a final answer")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("question", nargs="?", default="What is the die size of tiny_ai_core, and how many met4 shapes does it have?")
    ap.add_argument("--dry-run", action="store_true", help="skip Ollama; replay a scripted model reply")
    a = ap.parse_args()
    if a.dry_run: print("(dry run: scripted model replies, real KLayout tools; no Ollama needed)")
    try:
        run(a.question, a.dry_run)
    except OSError as e:
        sys.exit(f"Ollama not reachable ({e}). Start it: ollama serve ; ollama pull hermes3:8b ; or use --dry-run")
