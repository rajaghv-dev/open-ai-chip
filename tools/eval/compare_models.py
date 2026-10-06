#!/usr/bin/env python3
"""Compare local Ollama models on this repo's agent workflow, one model and one job at a time (see docs/AGENT_MODELS.md).
Docs: docs/AGENT_MODELS.md
  build/agent/venv/bin/python tools/eval/compare_models.py gemma4:e2b granite4.1:3b qwen3.5:4b [--skip rag,klayout]
Per model: (a) tools/eval/run_eval.py prompt mode, (b) examples/hermes_harness/eval_harness.py --only baseline,all,
(c) examples/hermes_rag/eval_rag.py --only rag+router, (d) examples/hermes_klayout_gui/demo.py --live (offscreen).
Writes build/agent/models_<ts>.json. Settings are the repo's own (temperature 0, seed 42, num_ctx 8192)."""
import argparse, glob, json, os, re, shutil, subprocess, sys, time

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PY = sys.executable
BUSY = r"run_eval.py|eval_harness.py|eval_rag.py|hermes_klayout_gui/agent.py|hermes_klayout_gui/demo.py"


def others_running():
    me = str(os.getpid())
    out = subprocess.run(["pgrep", "-f", BUSY], capture_output=True, text=True).stdout.split()
    return [p for p in out if p != me and p != str(os.getppid())]


def run(cmd, log):
    t0 = time.time()
    with open(log, "w") as f:
        rc = subprocess.run(cmd, cwd=ROOT, stdout=f, stderr=subprocess.STDOUT).returncode
    return rc, round(time.time() - t0)


def newest(pattern, after):
    fs = [p for p in glob.glob(os.path.join(ROOT, pattern)) if os.path.getmtime(p) >= after]
    return max(fs, key=os.path.getmtime) if fs else None


def ps_row(model):
    out = subprocess.run(["ollama", "ps"], capture_output=True, text=True).stdout.splitlines()
    for l in out[1:]:
        if l.split() and l.split()[0] == model:
            return " ".join(l.split()[2:5])          # SIZE unit, PROCESSOR e.g. "6.9 GB 100%/GPU"
    return None


def one(model, skip, stamp):
    safe = model.replace(":", "_")
    logd = os.path.join(ROOT, "build", "agent", "models_logs_" + stamp)
    os.makedirs(logd, exist_ok=True)
    res = {"model": model}
    if others_running():
        sys.exit("another eval job is running: %s" % others_running())
    t = time.time()
    rc, secs = run([PY, "tools/eval/run_eval.py", "--mode", "prompt", "--model", model, "--tag", "models"], os.path.join(logd, safe + "_a.log"))
    p = newest("build/agent/eval_*.json", t)
    if p:
        d = json.load(open(p))
        s = d["summary"]
        res["chip_eval"] = {"passed": s["passed"], "total": s["total"], "median_latency_s": s["median_latency_s"], "tokens_per_s": s["tokens_per_s"],
                            "failed": [r["id"] for r in d["results"] if not r["pass"]], "wall_s": secs, "report": os.path.relpath(p, ROOT)}
        res["tool_call_rate"] = "%d/%d answers used >=1 tool" % (sum(1 for r in d["results"] if r["tool_calls"]), len(d["results"]))
    else:
        res["chip_eval"] = {"error": "no report, rc=%s" % rc}
    res["memory_ollama_ps"] = ps_row(model)
    if "chip_eval" in res and res["chip_eval"].get("passed", 0) < 5 and model.endswith(":0.5b"):
        res["note"] = "stopped after the 15-question eval (< 5/15)"
        return res
    if "harness" not in skip:
        t = time.time()
        rc, secs = run([PY, "examples/hermes_harness/eval_harness.py", "--model", model, "--only", "baseline,all"], os.path.join(logd, safe + "_b.log"))
        p = newest("build/agent/harness_eval_*.json", t)
        res["harness"] = ({n: {k: c[k] for k in ("passed", "total", "median_latency_s", "failed")} for n, c in json.load(open(p))["configs"].items()}
                          if p else {"error": "no report, rc=%s" % rc})
        res["harness_wall_s"] = secs
    if "rag" not in skip:
        t = time.time()
        rc, secs = run([PY, "examples/hermes_rag/eval_rag.py", "--model", model, "--only", "rag+router"], os.path.join(logd, safe + "_c.log"))
        p = newest("build/agent/rag_eval_*.json", t)
        if p:
            c = json.load(open(p))["end_to_end"]["rag+router"]
            res["rag_router"] = {"main": "%d/%d" % (c["main"]["passed"], c["main"]["total"]), "heldout": "%d/%d" % (c["heldout"]["passed"], c["heldout"]["total"]),
                                 "median_latency_s": c["main"]["median_latency_s"], "failed": c["main"]["failed"] + c["heldout"]["failed"]}
        else:
            res["rag_router"] = {"error": "no report, rc=%s" % rc}
        res["rag_wall_s"] = secs
    if "klayout" not in skip:
        committed = os.path.join(ROOT, "examples", "hermes_klayout_gui", "transcript_live.txt")
        bak = committed + ".bak_models"
        default = model == "hermes3:8b"
        if default and os.path.exists(committed):
            shutil.copy(committed, bak)
        rc, secs = run([PY, "examples/hermes_klayout_gui/demo.py", "--live", "--model", model, "--backend", "offscreen"], os.path.join(logd, safe + "_d.log"))
        tp = committed if default else os.path.join(ROOT, "build", "agent", "klayout_gui", "transcript_live_%s.txt" % safe)
        txt = open(tp).read() if os.path.exists(tp) else ""
        if default:
            shutil.copy(tp, os.path.join(logd, "transcript_live_hermes3_8b.txt"))
            if os.path.exists(bak):
                shutil.move(bak, committed)         # keep the committed transcript untouched
        m = re.search(r"SUMMARY\s+(\d+)/(\d+)", txt)
        secs_l = [float(x) for x in re.findall(r"(?m)^\s+\S+\s+(?:PASS|FAIL)\s+([\d.]+)s", txt)]
        fails = re.findall(r"(?m)^\s+(\S+)\s+FAIL", txt)
        res["klayout"] = ({"passed": int(m.group(1)), "total": int(m.group(2)), "median_latency_s": sorted(secs_l)[len(secs_l) // 2] if secs_l else None,
                           "failed": fails, "wall_s": secs} if m else {"error": "no summary, rc=%s" % rc})
    subprocess.run(["ollama", "stop", model], capture_output=True)
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("models", nargs="+")
    ap.add_argument("--skip", default="", help="comma list of: harness,rag,klayout")
    a = ap.parse_args()
    stamp = time.strftime("%Y%m%d_%H%M%S")
    out = os.path.join(ROOT, "build", "agent", "models_%s.json" % stamp)
    rep = {"settings": {"temperature": 0, "seed": 42, "num_ctx": 8192, "mode": "prompt"}, "models": []}
    for m in a.models:
        print("==", m, flush=True)
        rep["models"].append(one(m, a.skip.split(","), stamp))
        json.dump(rep, open(out, "w"), indent=1)
        print(json.dumps(rep["models"][-1]), flush=True)
    print("->", os.path.relpath(out, ROOT))


if __name__ == "__main__":
    main()
