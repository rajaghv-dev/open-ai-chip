#!/usr/bin/env python3
"""Measure the Hermes fast paths and the mini RAG: wall time of each slash command and router sentence through the tool
server's POST /quick (in process, no model), and of rag_answer cold (fresh index load) and warm.
Read-only: report commands only; no window is opened and no job is started (window and run commands are timed by hand,
see the "observed" block, which records end-to-end Hermes times seen on this Mac with their source).
Run: build/agent/venv/bin/python scripts/hermes/measure_speed.py   (writes examples/hermes_desktop/eval_tools/speed_results.json)
Docs: hermes-agents.md, docs/HERMES_AGENT_INTEGRATION.md
"""
import datetime
import json
import os
import sys
import time

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "examples", "hermes_desktop", "tool_server"))
os.environ.setdefault("CHIP_RAG_WARM", "0")          # measure the cold path ourselves
OUT = os.path.join(REPO, "examples", "hermes_desktop", "eval_tools", "speed_results.json")

COMMANDS = [("timing", "kv_attn"), ("synth", "vision lit"), ("drc", "kv attention 16"), ("lvs", "prec bf16"),
            ("signoff", "caravel kv"), ("metrics", "kv8"), ("compare", "kv4 kv8 kv16"), ("designs", ""), ("chip", ""),
            ("run", "flow-all kv8"), ("loop", "signoff kv"), ("harness", "names"), ("harness", "facts kv")]
SENTENCES = ["what is the setup slack of kv attention 16?", "is caravel kv clean?",
             "why does kv_attn_n8_int4 have more flip-flops than kv_attn_n8?", "how was the wrapper hold violation fixed?",
             "hello there"]


def main():
    from fastapi.testclient import TestClient
    import tool_server as ts
    c = TestClient(ts.app)
    res = {"measured_on": datetime.date.today().isoformat(), "how": "TestClient POST /quick in one process, seconds of wall time",
           "commands": [], "sentences": [], "rag": {}}
    t = time.time()
    c.post("/rag_answer", json={"question": "why does kv_attn_n8_int4 have more flip-flops than kv_attn_n8?"})
    res["rag"]["first_question_seconds"] = round(time.time() - t, 2)
    t = time.time()
    c.post("/rag_answer", json={"question": "how was the wrapper hold violation fixed?"})
    res["rag"]["warm_question_seconds"] = round(time.time() - t, 2)
    for cmd, args in COMMANDS:
        t = time.time()
        r = c.post("/quick", json={"cmd": cmd, "args": args}).json()
        res["commands"].append({"command": "/%s %s" % (cmd, args), "seconds": round(time.time() - t, 3),
                                "first_line": r["reply"].splitlines()[0][:100]})
    for s in SENTENCES:
        t = time.time()
        r = c.post("/quick", json={"text": s}).json()
        res["sentences"].append({"text": s, "seconds": round(time.time() - t, 3), "kind": r.get("kind"), "handled": r.get("handled")})
    res["observed"] = {
        "note": "End-to-end times through the real hermes binary on 2026-10-07 (isolated home, qwen3.5-64k:9b, M-series Mac); "
                "one-shot `hermes -p chip -z` starts a new process, MCP bridge and tool server each time, so these include that start.",
        "before_fast_paths": {"default profile, 'open kv_attn design'": "about 390 s, interrupted (no repo tools: find over the home "
                              "directory); Hermes state.db session 20261007_052017_3ab1df",
                              "chip profile, 'open kv_attn design' (one gui_command call)": 70,
                              "chip profile, 'open the klayout with vision lit'": 24,
                              "chip profile, '/timing kv_attn' typed in -z mode (sent to the model)": 92},
        "with_fast_paths": {"plugin command handlers through Hermes's own dispatch (hermes_cli.plugins)": "0.1 s reports, 2.5 s /klayout vision lit show only met1",
                            "-z 'open kv_attn design' (window opened by pre_llm_call in 1.0 s, then the model's one-line reply)": 60,
                            "-z 'what is the setup slack of kv attention 16?' (facts injected)": 14,
                            "-z '/timing kv_attn' (answered by pre_llm_call)": 28},
        "e2e_real_binary_2026_10_07": {
            "note": "hermes -p chip -z on the owner's real chip profile after the fixes (router handled, transform_llm_output shows the exact reply); seconds",
            "search the docs for how the wrapper hold violation was fixed": 10, "run the soc-kv experiment (confirm id only)": 6,
            "list the experiments": 9, "close all windows": 6,
            "open kv attention 16 in klayout and show only met2 (window opened 3.2 s; before the exact-reply hook)": 64,
            "which kv design has the most flip-flops? (model + compare_designs, correct: kv_attn_n16 277)": 74,
            "before the fixes: the same search sentence (model paraphrased and invented a MAX_TRANSITION_CONSTRAINT change)": 68},
        "e2e_plugin_dispatch_2026_10_07": {
            "/search hold violation wrapper": 0.3, "/klayout kv_attn show only met1": 3.3, "/layout zoom to the lower-left 50 um": 0.7,
            "/layout show all": 0.5, "/drc kv8 live": 0.7, "/close all": 4.3, "/timing audio then 2": 0.0,
            "/experiment soc-kv (job done, exit 0)": 15, "/loopdemo sim kv8 (PASS verified)": 2.0,
            "/whatif vision lit PL_TARGET_DENSITY_PCT=60 (full flow on a copy, exit 0, compared by /result)": 51,
            "/whatif vision lit CLOCK_PERIOD=40": "blocked by the HARD RULES, nothing started"},
        "whatif_and_rebuild_2026_10_07": {
            "chip whatif vision lit CLOCK_PERIOD=5 (done, worst setup slack +0.069 ns at max_ss, shown orange)": 48,
            "chip whatif vision lit CLOCK_PERIOD=4 (LibreLane exit 2: hold violations at max_ff, min_ff, nom_ff; shown red)": 46,
            "chip rebuild kv8 (unchanged copy, identical cells, slack, DRC, LVS to the committed run)": 104},
        "loops_live": {"/loopdemo layers vision lit (open + 5 layer pictures in KLayout)": 7.2,
                       "/loopdemo sim vision lit (make simulate, PASS line verified)": 2.0}}
    with open(OUT, "w") as f:
        json.dump(res, f, indent=1)
        f.write("\n")
    print(json.dumps({k: res[k] for k in ("rag",)}, indent=1))
    for r in res["commands"] + res["sentences"]:
        print("%7.3f  %s" % (r["seconds"], r.get("command") or r.get("text")))
    print("wrote", os.path.relpath(OUT, REPO))


if __name__ == "__main__":
    main()
