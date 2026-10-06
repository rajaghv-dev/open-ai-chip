#!/usr/bin/env python3
"""Experiment and demo tools for the Hermes desktop/browser agent: list_experiments, run_experiment, experiment_result,
engine_pictures, list_demos, demo_steps.

Mounted by tool_server.py (_mount_extensions) as `router`; every endpoint is `POST /<tool_name>` (operationId == name).
The catalog is data built from the repo (Makefile design list, resources.json, doc files). run_experiment never starts a
make job by itself: it calls the tool server's own run_make over HTTP (127.0.0.1:${CHIP_TOOLS_PORT:-8770}), so the same
allow-list, one-physical-flow rule and "yes, run <id>" confirmation gate apply. The one experiment that is not a make
target (OpenROAD offscreen engine views, a ~30 s container) has its own gate of the same shape. experiment_result parses
the logs and committed evidence by code; the small model only relays the table.
Does NOT import tool_server.py (it imports this file). Read-only apart from PNG copies under build/agent/klayout_gui/.
Docs: docs/HERMES_DESKTOP.md (section "Experiments and demos"), examples/hermes_desktop/tool_server/README.md
Tests: tests/tools/test_experiments.py
"""
import glob
import importlib.util
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from typing import Any, Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
for _p in (os.path.join(REPO, "scripts", "lib"),):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import repo  # noqa: E402
from fastapi import APIRouter  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

router = APIRouter()

HOST = "127.0.0.1"
PORT = int(os.environ.get("CHIP_TOOLS_PORT", "8770"))
PUBLIC_URL = os.environ.get("CHIP_TOOLS_PUBLIC_URL", "http://%s:%d" % (HOST, PORT)).rstrip("/")
IMG_DIR = os.path.join(REPO, "build", "agent", "klayout_gui")
JOB_DIR = os.path.join(REPO, "build", "agent", "jobs")
OR_DIR = os.path.join(REPO, "build", "agent", "openroad_gui")
OR_COMMITTED = os.path.join(REPO, "examples", "openroad_gui", "img")
PRECISION = ["prec_bin", "prec_tern", "prec_int4", "prec_int8", "prec_fp8", "prec_fp16", "prec_bf16"]
KV = ["kv_attn_n4", "kv_attn_n8", "kv_attn_n16", "kv_attn_n8_int4", "kv_attn_n8_ring"]

# Test hook: replaces the HTTP call to this server (tests set it to a function that uses a TestClient).
CALL_HOOK = None


def _demo_defs():
    spec = importlib.util.spec_from_file_location("chip_demo_defs", os.path.join(HERE, "..", "demo_defs.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


# ---------------------------------------------------------------- helpers
def _rel(p: str) -> str:
    return os.path.relpath(p, REPO)


def _read(path: str) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as f:
            return f.read()
    except OSError:
        return ""


def _json(path: str, default=None):
    return repo.load_json(path, default)


def _server(name: str, body: dict) -> dict:
    """POST /<name> on this tool server (the gated run_make, run_summary, ...)."""
    if CALL_HOOK:
        return CALL_HOOK(name, body)
    port = os.environ.get("CHIP_TOOLS_PORT", str(PORT))
    req = urllib.request.Request("http://%s:%s/%s" % (HOST, port, name), data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return json.loads(r.read())
    except Exception as e:  # noqa: BLE001
        return {"error": "tool server call %s failed: %s" % (name, e)}


def _designs() -> List[str]:
    return repo.all_designs()


def _flow_seconds(d: str) -> Optional[int]:
    r = _json("designs/%s/output/resources.json" % d, {})
    v = r.get("wall_s_total") if isinstance(r, dict) else None
    return int(v) if isinstance(v, (int, float)) else None


def _metrics(d: str) -> dict:
    return _json("designs/%s/output/metrics.json" % d, {}) or {}


def _md_table(head: List[str], rows: List[List[Any]]) -> str:
    out = ["| " + " | ".join(head) + " |", "|" + "|".join("---" for _ in head) + "|"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def _num(x, nd=2):
    return ("%.*f" % (nd, x)) if isinstance(x, (int, float)) else "n/a"


# ---------------------------------------------------------------- the catalog
def _exp(id, group, title, shows, command, minutes, physical, docs, kind="make", target=None, designs=None,
         needs_design=False, fixed_design=None, extra=None):
    e = {"id": id, "group": group, "title": title, "shows": shows, "command": command, "expected_time": minutes,
         "physical_flow": physical, "docs": docs, "kind": kind, "target": target}
    if needs_design:
        e["needs_design"] = True
        e["designs"] = designs if designs is not None else _designs()
    elif designs:
        e["designs"] = designs
    if fixed_design:
        e["fixed_design"] = fixed_design
    if extra:
        e.update(extra)
    return e


def build_catalog() -> List[dict]:
    """Every experiment the repo can run, as data. Designs come from the Makefile ALL_DESIGNS list."""
    alld = _designs()
    cat: List[dict] = []
    flow_t = "about 1 to 2.5 minutes per design (flow wall time %s s in resources.json) plus simulate and gate-level checks" % \
             ("%d to %d" % (min(v for v in map(_flow_seconds, alld) if v), max(v for v in map(_flow_seconds, alld) if v))
              if any(map(_flow_seconds, alld)) else "n/a")
    cat += [
        _exp("flow-all", "design", "Full flow for one design", "RTL to clean GDSII: simulate, layout, signoff, gate level, collect.",
             "make flow-all DESIGN=<design>", flow_t, True, "docs/RESULTS.md", target="flow-all", needs_design=True),
        _exp("simulate", "design", "RTL simulation of one design", "The self-checking testbench passes (PASS line with case and check counts).",
             "make simulate DESIGN=<design>", "1 to 3 seconds", False, "docs/VALIDATION.md", target="simulate", needs_design=True),
        _exp("check", "design", "Signoff checks of one design", "DRC, LVS, XOR, antenna, slack at all corners, no logic lost, on the current run.",
             "make check DESIGN=<design>", "about 1 second to a few seconds (reuses the finished run)", False, "docs/VALIDATION.md",
             target="check", needs_design=True),
        _exp("gl", "design", "Gate-level simulation (synthesised netlist)", "The synthesised netlist passes the same testbench.",
             "make gl DESIGN=<design>", "2 to 10 seconds", False, "docs/VALIDATION.md", target="gl", needs_design=True),
        _exp("gl-final", "design", "Gate-level simulation (routed netlist)", "The final routed netlist passes the same testbench.",
             "make gl-final DESIGN=<design>", "2 to 10 seconds", False, "docs/VALIDATION.md", target="gl-final", needs_design=True),
    ]
    fam = [
        ("baseline", "Baseline counter", "The template's 16-bit counter: the non-AI control.", ["user_proj_example"], "docs/LESSONS.md"),
        ("tiny-engines", "Tiny engines", "Dense neuron, convolution neuron, word-embedding sentiment: 169 to 297 cells.",
         ["vision_all_lit", "vision_block", "text_sentiment"], "docs/LESSONS.md"),
        ("audio", "Audio engines", "Zero-crossing pitch and a learned 4-tap onset filter.", ["audio_pitch", "audio_onset"], "docs/LESSONS.md"),
        ("multimodal", "Multimodal engine", "A 3x3 image against a one-word caption in one shared space.", ["image_text_match"], "docs/LESSONS.md"),
        ("precision", "Precision study (7 formats)", "One 9-input neuron in bin, tern, int4, int8, fp8, fp16, bf16: accuracy, area, slack.",
         PRECISION, "docs/PRECISION_STUDY.md"),
        ("kv-cache", "KV-cache attention family (5 variants)", "Cache bits against flip-flops built: n4, n8, n16, int4, ring.",
         KV, "docs/LLM_INFERENCE.md"),
        ("soc-macros", "SoC macros", "Engines behind a Wishbone adapter, hardened as macros.",
         ["tiny_ai_core", "soc_image_text_match", "soc_kv_attn_n8"], "docs/SOC_PLAN.md"),
        ("wrappers", "Caravel wrappers", "The fixed Caravel user_project_wrapper around a macro.",
         ["user_project_wrapper", "user_project_wrapper_soc_itm", "user_project_wrapper_soc_kv"], "docs/SOC_PLAN.md"),
    ]
    for fid, title, shows, members, doc in fam:
        cat.append(_exp(fid, "family", title, shows + " Result: a comparison table from the committed metrics.json files. "
                        "Run: a quick RTL simulation of one member (pass design).",
                        "make simulate DESIGN=<member>", "1 to 3 seconds per member (result table: instant)", False, doc,
                        target="simulate", needs_design=True, designs=members, extra={"family": True}))
    sysx = [
        ("soc-sim", "SoC cycle table: software vs accelerator", "A PicoRV32 runs tiny networks in software and through the accelerator: the bus dominates.",
         "make soc-sim", "about 25 to 30 seconds", False, "firmware/README.md", "soc-sim"),
        ("soc-kv", "KV cache prefill vs decode", "Cycles per token for prefill and decode on a PicoRV32 SoC with the KV engine; decode is bus-bound.",
         "make soc-kv", "about 15 seconds", False, "firmware/README.md", "soc-kv"),
        ("adapter-test", "Wishbone adapter with all 14 engines", "The generic bus-to-stream adapter passes with every stream engine.",
         "make adapter-test", "about 10 seconds", False, "docs/SOC_PLAN.md", "adapter-test"),
        ("caravel-rtl", "Full-Caravel RTL simulation", "VexRiscv firmware reads and writes the macro through the real Caravel RTL.",
         "make caravel-rtl", "about 1 minute (needs the build/caravel downloads)", False, "docs/CARAVEL_SIM.md", "caravel-rtl"),
        ("caravel-gl", "Hybrid gate-level Caravel simulation", "The same test with the wrapper as a gate-level netlist.",
         "make caravel-gl", "about 1 minute (needs the build/caravel downloads)", False, "docs/CARAVEL_SIM.md", "caravel-gl"),
        ("test-full", "Heavy local checks of all designs", "Simulate, signoff, run state and gate level for all 25 designs, SoC and Caravel; no physical flow is started.",
         "make test-full", "several minutes", True, "docs/VALIDATION.md", "test-full"),
        ("model-check", "Python golden models", "Each golden.py --check passes (tiny_ai, image_text_match, precision_hw, kv_attention).",
         "make model-check", "a few seconds", False, "docs/VALIDATION.md", "model-check"),
        ("check-generated", "Generated files are reproducible", "Regenerating ROMs, vectors and weights changes nothing.",
         "make check-generated", "a few seconds", False, "docs/VALIDATION.md", "check-generated"),
    ]
    for id_, title, shows, cmd, mins, phys, doc, tgt in sysx:
        cat.append(_exp(id_, "system", title, shows, cmd, mins, phys, doc, target=tgt))
    cat.append(_exp("precheck", "system", "ChipFoundry precheck (14 checks)", "All 14 precheck checks pass on user_project_wrapper. Result: the committed summary.",
                    "make precheck", "about 1 minute", False, "docs/PRECHECK.md", kind="terminal", target="precheck",
                    extra={"why_not_here": "make precheck writes under precheck/results, outside build/, so the agent's allow-list refuses it. "
                           "Run it in a terminal; experiment_result shows the committed 14-check summary."}))
    cat += [
        _exp("openroad-views", "pictures", "OpenROAD engine pictures (render)", "Placement density, routing congestion, IR drop, clock tree and worst path of a finished run, rendered off-screen in the LibreLane container.",
             "examples/openroad_gui/render_views.sh <design>", "about 30 seconds (Docker)", False, "examples/openroad_gui/README.md",
             kind="script", target="openroad-views", needs_design=True, designs=_designs()),
        _exp("heatmaps-live", "pictures", "OpenROAD live heat maps (window)", "The real OpenROAD GUI cycling through the heat maps on XQuartz.",
             "bash scripts/gui/open_gui.sh heatmaps <design>", "about 1 minute; needs XQuartz", False, "docs/GUI_AND_LOGS.md", kind="terminal",
             target="heatmaps", extra={"why_not_here": "It opens a window on your display through XQuartz; start it from a terminal. "
                                       "engine_pictures shows the same maps as pictures."}),
        _exp("klayout-live", "pictures", "KLayout window driven from the chat", "The real KLayout window: zoom, layers, markers, measure.",
             "tools gui_start {tool: klayout, design}, then klayout_live", "5 to 30 seconds to open", False, "examples/hermes_klayout_gui/README.md",
             kind="tools", target="klayout", extra={"tool_steps": [
                 {"tool": "gui_start", "args": {"tool": "klayout", "design": "kv_attn_n8"}},
                 {"tool": "klayout_live", "args": {"action": "zoom", "bbox": [0, 0, 50, 50]}},
                 {"tool": "gui_stop", "args": {"tool": "klayout"}}]}),
        _exp("magic-live", "pictures", "Magic window driven from the chat", "The real Magic window (in the LibreLane container on XQuartz): layers, DRC, find, snapshot.",
             "tools gui_start {tool: magic, design}, then magic_live", "10 to 30 seconds to open", False, "docs/GUI_AND_LOGS.md",
             kind="tools", target="magic", extra={"tool_steps": [
                 {"tool": "gui_start", "args": {"tool": "magic", "design": "kv_attn_n8"}},
                 {"tool": "magic_live", "args": {"action": "snapshot"}},
                 {"tool": "gui_stop", "args": {"tool": "magic"}}]}),
    ]
    return cat


def _find(id: str) -> Optional[dict]:
    k = (id or "").strip().lower()
    alias = {"flow": "flow-all", "soc_kv": "soc-kv", "soc_sim": "soc-sim", "kv": "kv-cache", "kv_cache": "kv-cache",
             "adapter": "adapter-test", "tiny": "tiny-engines", "prec": "precision", "precision-study": "precision",
             "gl_final": "gl-final", "openroad": "openroad-views", "engine-pictures": "openroad-views"}
    k = alias.get(k, k)
    for e in build_catalog():
        if e["id"] == k:
            return e
    return None


# ---------------------------------------------------------------- request models
class ListReq(BaseModel):
    group: Optional[str] = Field(None, description="optional: design, family, system or pictures. Omit for all.", examples=["system"])


class RunExpReq(BaseModel):
    id: str = Field(..., description="experiment id from list_experiments, e.g. soc-kv, soc-sim, flow-all, simulate", examples=["soc-kv"])
    design: Optional[str] = Field(None, description="design name, only for experiments that need one (flow-all, simulate, check, gl, gl-final, a family, openroad-views)",
                                  examples=["vision_block"])
    confirm_id: Optional[str] = Field(None, description="SECOND call only, after the user wrote 'yes, run <confirm_id>': the 6-character id "
                                      "from the first reply (for make experiments run_make with the same confirm_id does the same).")


class ResultReq(BaseModel):
    id: str = Field(..., description="experiment id, e.g. soc-kv, soc-sim, precision, kv-cache, adapter-test, precheck, flow-all", examples=["soc-kv"])
    design: Optional[str] = Field(None, description="design name for per-design experiments (flow-all, simulate, check, ...)")
    job_id: Optional[str] = Field(None, description="job_id of the finished run_make job; omit to use the committed results")


class PicReq(BaseModel):
    design: Optional[str] = Field("kv_attn_n8", description="design with OpenROAD pictures, default kv_attn_n8", examples=["kv_attn_n8"])
    view: Optional[str] = Field(None, description="one of: placement, congestion, power, ir, clock, path, layout. Omit to list the available views.",
                                examples=["congestion"])


class DemoReq(BaseModel):
    name: str = Field(..., description="demo number or name from list_demos: 1 precision, 2 kv, 3 rtl2gds, 4 int4, 5 heatmaps, 6 soc, 7 gui", examples=["kv"])


class Empty(BaseModel):
    pass


def post(name: str, summary: str):
    return router.post("/" + name, operation_id=name, summary=summary, response_model=None)


# ---------------------------------------------------------------- list_experiments
@post("list_experiments", "List every experiment the repo can run")
def list_experiments(req: ListReq = ListReq()) -> dict:
    """The catalog of experiments (id, title, what it shows, command, expected time, physical flow yes/no, docs link),
    grouped: design (flow-all, simulate, check, gl, gl-final: each takes a design), family (tiny-engines, audio, multimodal,
    precision, kv-cache, soc-macros, wrappers, baseline), system (soc-sim, soc-kv, adapter-test, caravel-rtl, caravel-gl,
    precheck, test-full, model-check, check-generated), pictures (openroad-views, heatmaps-live, klayout-live, magic-live).
    Show the list to the user and ask which to run. Use run_experiment to start one, experiment_result for its results."""
    g = (req.group or "").strip().lower()
    cat = build_catalog()
    if g:
        cat = [e for e in cat if e["group"] == g]
        if not cat:
            return {"error": "unknown group %r; groups: design, family, system, pictures" % g}
    rows = []
    for e in cat:
        r = {k: e[k] for k in ("id", "group", "title", "shows", "command", "expected_time", "physical_flow", "docs")}
        if e.get("needs_design"):
            r["designs"] = e["designs"]
        rows.append(r)
    return {"count": len(rows), "groups": sorted({e["group"] for e in build_catalog()}), "experiments": rows,
            "say": "Show id, title and expected_time as a list. Running anything needs the user's 'yes, run <id>'."}


# ---------------------------------------------------------------- run_experiment
_CONFIRMS: Dict[str, dict] = {}
_RENDER: Dict[str, dict] = {}       # design -> {state, started, rc, log}
_LOCK = threading.Lock()
CONFIRM_TTL_S = 600


def _no_confirm() -> bool:
    return os.environ.get("CHIP_TOOLS_NO_CONFIRM") == "1"


def _docker_env() -> dict:
    env = dict(os.environ)
    env.update(repo.docker_env(os.environ))
    return env


def _start_render(design: str) -> dict:
    out_dir = os.path.join(OR_DIR, design)
    os.makedirs(out_dir, exist_ok=True)
    log = os.path.join(out_dir, "agent_render.log")
    st = {"state": "running", "started": time.time(), "rc": None, "log": _rel(log)}

    def work():
        try:
            with open(log, "w") as f:
                p = subprocess.run(["bash", os.path.join(REPO, "examples", "openroad_gui", "render_views.sh"), design],
                                   cwd=REPO, env=_docker_env(), stdout=f, stderr=subprocess.STDOUT, timeout=300)
            st["rc"] = p.returncode
            st["state"] = "done" if p.returncode == 0 else "failed"
        except Exception as e:  # noqa: BLE001
            st["state"], st["rc"] = "failed", str(e)

    with _LOCK:
        cur = _RENDER.get(design)
        if any(v["state"] == "running" for v in _RENDER.values()):
            return {"error": "an OpenROAD render is already running; wait for it (experiment_result id openroad-views)"}
        _RENDER[design] = st
    threading.Thread(target=work, daemon=True).start()
    return {"state": "running", "log": st["log"]}


@post("run_experiment", "Start an experiment (asks the user to confirm first)")
def run_experiment(req: RunExpReq) -> dict:
    """Start one experiment from list_experiments. It NEVER starts on the first call: the reply has needs_confirmation, a
    confirm_id and the exact command; show `say` to the user and STOP. Only after the user writes 'yes, run <confirm_id>'
    call run_make with {confirm_id} (run_experiment with the same confirm_id also works). Then poll job_status and
    call experiment_result with the job_id. Experiments that cannot run from the chat (precheck, live windows) say so and
    give the terminal command. Call it only when the user ORDERS a run, never for a 'how do I' question."""
    e = _find(req.id)
    if not e:
        return {"error": "unknown experiment %r; call list_experiments" % req.id}
    base = {"experiment": e["id"], "title": e["title"], "expected_time": e["expected_time"],
            "physical_flow": e["physical_flow"], "docs": e["docs"]}
    if req.confirm_id:
        return _confirm_script(base, e, req.confirm_id.strip())
    if e["kind"] == "terminal":
        return dict(base, runnable_here=False, run_in_terminal=e["command"], why=e.get("why_not_here", ""),
                    say="This one cannot be started from the chat. Run it in a terminal: %s. Then experiment_result id %s shows what is committed." % (e["command"], e["id"]))
    if e["kind"] == "tools":
        return dict(base, runnable_here=True, tool_steps=e["tool_steps"],
                    say="Call these tools in order (a window opens on your screen): %s." % ", ".join(s["tool"] for s in e["tool_steps"]))
    design = (req.design or "").strip() or None
    if design and design.lower() in ("-", "none", "null", "n/a", "na", "default", "all"):
        design = None                                  # the 8B model fills optional fields with placeholders
    if not e.get("needs_design"):
        design = None                                  # a fixed experiment has no design; ignore a stray one
    if e.get("needs_design"):
        members = e["designs"]
        if design is None and len(members) == 1:
            design = members[0]
        if design is None:
            return dict(base, error="this experiment needs a design", designs=members, say="Ask the user which design, then call run_experiment again.")
        if design not in members:
            return dict(base, error="design %r is not valid for %s" % (design, e["id"]), designs=members)
    if e["kind"] == "script":
        if not os.path.isdir(os.path.join(REPO, "designs", design)):
            return dict(base, error="unknown design")
        cid = secrets.token_hex(3)
        now = time.time()
        with _LOCK:
            for k in [k for k, v in _CONFIRMS.items() if v["expires"] < now]:
                del _CONFIRMS[k]
            _CONFIRMS[cid] = {"design": design, "expires": now + CONFIRM_TTL_S}
        return dict(base, needs_confirmation=True, confirm_id=cid, started=False, design=design,
                    will_run="examples/openroad_gui/render_views.sh %s" % design,
                    say="Reply 'yes, run %s' to start. Nothing has been started yet." % cid)
    if e["target"] in ("caravel-rtl", "caravel-gl") and not os.path.isdir(os.path.join(REPO, "build", "caravel", "caravel")):
        return dict(base, runnable_here=False, error="build/caravel is missing (a 953 MB + 4.1 GB download, see caravel_sim/README.md); "
                    "this experiment cannot run on this machine yet.")
    body = {"target": e["target"]}
    if design:
        body["design"] = design
    r = _server("run_make", body)
    if r.get("needs_confirmation"):
        # compact on purpose: the 8B model quotes a short result, and paraphrases (wrongly) a long one
        out = {"experiment": e["id"], "needs_confirmation": True, "started": False, "confirm_id": r["confirm_id"],
               "will_run": r["will_run"], "expected_time": e["expected_time"], "physical_flow": e["physical_flow"],
               "say": "Tell the user: Nothing has started. Reply 'yes, run %s' to run `%s` (%s)." % (r["confirm_id"], r["will_run"], e["expected_time"])}
        out["next"] = ("After the user writes 'yes, run %s', call run_make with confirm_id %s, then job_status, "
                       "then experiment_result id %s with the job_id." % (r["confirm_id"], r["confirm_id"], e["id"]))
        return out
    out = dict(base)
    out.update(r)
    return out


def _confirm_script(base: dict, e: dict, cid: str) -> dict:
    if e["kind"] != "script":
        # tolerated: the user's "yes, run <id>" may be routed here; the id belongs to the gated run_make, so hand it over
        r = _server("run_make", {"confirm_id": cid})
        if r.get("job_id"):
            r["next"] = "Poll job_status with this job_id, then call experiment_result with id %s and job_id." % e["id"]
        return dict(base, **r)
    now = time.time()
    with _LOCK:
        c = _CONFIRMS.get(cid)
        if not c or c["expires"] < now:
            return dict(base, error="unknown, expired or already used confirm_id %r; call run_experiment without confirm_id" % cid)
        del _CONFIRMS[cid]
    r = _start_render(c["design"])
    return dict(base, design=c["design"], **r, next="Wait about 30 s, then call experiment_result id openroad-views design %s." % c["design"])


# ---------------------------------------------------------------- parsers (pure functions, tested on fixtures)
def parse_soc_sim(text: str) -> dict:
    """The [5] cycle table of the PicoRV32 SoC firmware: mode, cases, sw cycles, accelerator round trip and its parts."""
    rows = []
    for ln in text.splitlines():
        m = re.match(r"^\s*(\w+)\s+(\d+)\s+([\d.]+)\s+([\d.]+)\s+\(([\d.]+)\+([\d.]+)\+([\d.]+)\)\s+([\d.]+)\s+([\d.]+)x\s*$", ln)
        if m:
            g = m.groups()
            rt, wr, wt, rd = float(g[3]), float(g[4]), float(g[5]), float(g[6])
            rows.append({"mode": g[0], "cases": int(g[1]), "sw_cpu": float(g[2]), "accel_roundtrip": rt, "write": wr, "wait": wt,
                         "read": rd, "accel_cycles_reg": float(g[7]), "sw_over_accel": float(g[8]),
                         "bus_share_pct": round(100.0 * (wr + rd) / rt, 1)})
    return {"rows": rows, "pass": bool(re.search(r"^PASS\b", text, re.M)),
            "total_cycles": int(m2.group(1)) if (m2 := re.search(r"firmware exit PASS after (\d+) cycles", text)) else None}


def parse_soc_kv(text: str) -> dict:
    """Prefill and decode tables of the KV firmware console (also the indented copy in firmware/README.md)."""
    pre, dec = [], []
    for ln in text.splitlines():
        m = re.match(r"^\s+(\d)\s+(\d+)\s+(\d+\.\d)\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)\s*$", ln)
        if m:
            g = m.groups()
            pre.append({"P": int(g[0]), "roundtrip": int(g[1]), "per_token": float(g[2]), "write": int(g[3]), "wait": int(g[4]),
                        "read": int(g[5]), "cycles_reg": int(g[6])})
            continue
        m = re.match(r"^\s+(\d)\s+(?:(\d+)\s+)?(\d+\.\d)\s+(\d+\.\d)\s+(\d+\.\d)\s+(\d+\.\d)\s+\((\d+)/(\d+)/(\d+)\)\s+(\d+\.\d)\s+(\d+\.\d)\s+(\d+)\s*$", ln)
        if m:
            g = m.groups()
            dec.append({"n": int(g[0]), "roundtrip": float(g[2]), "write": float(g[3]), "wait": float(g[4]), "read": float(g[5]),
                        "read_pct": int(g[8]), "cycles_reg": float(g[9]), "minus_base": float(g[10]), "engine_L": int(g[11])})
    base = re.search(r"baseline.*roundtrip (\d+), CYCLES_reg (\d+)", text)
    return {"prefill": pre, "decode": dec, "baseline_roundtrip": int(base.group(1)) if base else None,
            "pass": bool(re.search(r"^PASS\b|firmware exit PASS", text, re.M))}


def parse_adapter(text: str) -> dict:
    rows = []
    for ln in text.splitlines():
        m = re.match(r"^PASS (\w+): (\d+) input beats, (\d+) result beats checked.*?, (\d+) checks\s*$", ln)
        if m:
            rows.append({"engine": m.group(1), "input_beats": int(m.group(2)), "result_beats": int(m.group(3)), "checks": int(m.group(4))})
    fail = [ln for ln in text.splitlines() if ln.startswith("FAIL")]
    summ = re.search(r"adapter tests: (\d+) engines PASS", text)
    return {"rows": rows, "fail": fail, "engines_pass": int(summ.group(1)) if summ else None}


def parse_precheck(tsv: str) -> dict:
    rows = []
    for ln in tsv.splitlines():
        p = ln.split("\t")
        if len(p) >= 2 and p[1] in ("PASS", "FAIL", "SKIP"):
            rows.append({"check": p[0], "status": p[1], "seconds": p[2] if len(p) > 2 else ""})
    return {"rows": rows, "passed": sum(r["status"] == "PASS" for r in rows), "total": len(rows)}


def parse_precision_doc(text: str) -> Dict[str, dict]:
    """Study table A of docs/PRECISION_STUDY.md: accuracy, memory, accumulator, latency per format."""
    out = {}
    for ln in text.splitlines():
        m = re.match(r"^(bin|tern|int4|int8|fp8|fp16|bf16)\s+([\d.]+)\s+([\d.]+)\s+(\d+)\s+(\d+)\s+(.+?)\s+(\d)\s+(\d+)\s*$", ln)
        if m and m.group(1) not in out:
            g = m.groups()
            out[g[0]] = {"acc": float(g[1]), "eq_fp32": float(g[2]), "param_bits": int(g[3]), "accum": g[5], "latency": int(g[6])}
    return out


def parse_kv_doc(text: str) -> Dict[str, dict]:
    """The KV family table of docs/LLM_INFERENCE.md section 5.1: nominal cache bits and cache flip-flops built."""
    out = {}
    for ln in text.splitlines():
        m = re.match(r"^\|\s*`(kv_attn_\w+|soc_kv_attn_n8)`\s*\|\s*([^|]+?)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|", ln)
        if m:
            out[m.group(1)] = {"entries": m.group(2), "nominal_bits": int(m.group(3)), "cache_flops": int(m.group(4)),
                               "all_flops_doc": int(m.group(5))}
    return out


def _key_numbers(d: str) -> dict:
    m = _metrics(d)
    bb = m.get("design__die__bbox", "")
    die = "%gx%g" % (float(bb.split()[2]), float(bb.split()[3])) if isinstance(bb, str) and len(bb.split()) == 4 else "n/a"
    return {"cells": m.get("design__instance__count__stdcell"), "cell_um2": m.get("design__instance__area__stdcell"),
            "flops": m.get("design__instance__count__class:sequential_cell"), "die_um": die,
            "util": m.get("design__instance__utilization"), "setup_ss_ns": m.get("timing__setup__ws__corner:max_ss_100C_1v60"),
            "hold_ns": m.get("timing__hold__ws"), "wall_s": _flow_seconds(d), "have_metrics": bool(m)}


# ---------------------------------------------------------------- experiment_result
def _job_log(job_id: str) -> Optional[str]:
    if not re.match(r"^[A-Za-z0-9_-]+$", job_id or ""):
        return None
    p = os.path.join(JOB_DIR, job_id + ".log")
    return _read(p) if os.path.isfile(p) else None


def _latest_log(needle: str) -> Optional[str]:
    for p in sorted(glob.glob(os.path.join(JOB_DIR, "*.log")), reverse=True)[:200]:
        t = _read(p)
        if t.startswith("$ make " + needle):
            return t
    return None


def _text_for(e: dict, req: ResultReq, readme_fallback: str):
    """(text, source). A finished job's log when job_id is given or one exists, else the committed README copy."""
    if req.job_id:
        t = _job_log(req.job_id)
        if t is None:
            return None, "unknown job_id %r (no log in build/agent/jobs)" % req.job_id
        return t, "job %s (build/agent/jobs/%s.log)" % (req.job_id, req.job_id)
    t = _latest_log(e["target"])
    if t and "PASS" in t:
        return t, "latest `make %s` job log in build/agent/jobs" % e["target"]
    return _read(os.path.join(REPO, readme_fallback)), "committed results in %s (latest recorded run; run the experiment for a fresh one)" % readme_fallback


def _result_soc_sim(e, req):
    t, src = _text_for(e, req, "firmware/README.md")
    if t is None:
        return {"error": src}
    p = parse_soc_sim(t)
    if not p["rows"]:
        return {"error": "no cycle table found in %s (job still running or failed?)" % src}
    tbl = _md_table(["mode", "cases", "sw cycles", "accel round trip", "write+wait+read", "accel CYCLES", "sw/accel", "bus share"],
                    [[r["mode"], r["cases"], r["sw_cpu"], r["accel_roundtrip"], "%g+%g+%g" % (r["write"], r["wait"], r["read"]),
                      r["accel_cycles_reg"], "%.1fx" % r["sw_over_accel"], "%s%%" % r["bus_share_pct"]] for r in p["rows"]])
    best = max(p["rows"], key=lambda r: r["sw_over_accel"])
    lesson = ("The accelerator computes in %g to %g clocks, but its round trip is %g to %g clocks: %s to %s%% of it is bus writes and reads. "
              "Software wins for %d of %d networks; only %s (the most work per input) is faster on the accelerator (%.1fx)." %
              (min(r["accel_cycles_reg"] for r in p["rows"]), max(r["accel_cycles_reg"] for r in p["rows"]),
               min(r["accel_roundtrip"] for r in p["rows"]), max(r["accel_roundtrip"] for r in p["rows"]),
               min(r["bus_share_pct"] for r in p["rows"]), max(r["bus_share_pct"] for r in p["rows"]),
               sum(r["sw_over_accel"] < 1 for r in p["rows"]), len(p["rows"]), best["mode"], best["sw_over_accel"]))
    return {"id": "soc-sim", "source": src, "table": tbl, "rows": p["rows"], "lesson": lesson, "docs": "firmware/README.md",
            "say": "Show `table` and `lesson`. CPU clock cycles on a PicoRV32; not Caravel numbers."}


def _result_soc_kv(e, req):
    t, src = _text_for(e, req, "firmware/README.md")
    if t is None:
        return {"error": src}
    p = parse_soc_kv(t)
    if not p["prefill"] or not p["decode"]:
        return {"error": "no prefill/decode tables found in %s (job still running or failed?)" % src}
    t1 = _md_table(["P prompt tokens", "round trip", "per token", "write", "wait", "read"],
                   [[r["P"], r["roundtrip"], r["per_token"], r["write"], r["wait"], r["read"]] for r in p["prefill"]])
    d0 = p["decode"][0]
    t2 = _md_table(["cache fill n", "round trip", "write", "wait", "read", "read share", "engine extra cycles"],
                   [[r["n"], r["roundtrip"], r["write"], r["wait"], r["read"], "%d%%" % r["read_pct"], r["minus_base"]] for r in p["decode"]])
    first, last = p["prefill"][0], p["prefill"][-1]
    ratio = d0["roundtrip"] / first["roundtrip"]
    lesson = ("Prefill: one frame pays the bus setup once, so the cost per prompt token falls from %.1f (P=%d) to %.1f (P=%d) clocks. "
              "Decode: every token is its own frame, a flat %.0f clocks round trip (%.1fx a one-token prefill), of which %.0f (%d%%) are bus reads of the "
              "8-beat answer. The engine's own growth with the cache is %.0f to %.0f extra clocks, hidden behind the bus: decode is bus-bound." %
              (first["per_token"], first["P"], last["per_token"], last["P"], d0["roundtrip"], ratio, d0["read"], d0["read_pct"],
               min(r["minus_base"] for r in p["decode"]), max(r["minus_base"] for r in p["decode"])))
    return {"id": "soc-kv", "source": src, "prefill_table": t1, "decode_table": t2, "prefill": p["prefill"], "decode": p["decode"],
            "lesson": lesson, "docs": "docs/LLM_INFERENCE.md",
            "say": "Show both tables, then `lesson`. PicoRV32 clock cycles, 8-bit tokens, 8-entry cache; says nothing about real LLM sizes."}


def _result_adapter(e, req):
    t, src = None, ""
    if req.job_id:
        t = _job_log(req.job_id)
        src = "job %s" % req.job_id
        if t is None:
            return {"error": "unknown job_id %r" % req.job_id}
    else:
        t = _latest_log("adapter-test")
        src = "latest `make adapter-test` job log"
        if not t:
            lines = []
            for p in sorted(glob.glob(os.path.join(REPO, "build", "adapter_tests", "*.log"))):
                lines += [ln for ln in _read(p).splitlines() if ln.startswith("PASS ")][:1]
            t, src = "\n".join(lines), "build/adapter_tests/*.log from the last run"
    p = parse_adapter(t)
    if not p["rows"]:
        return {"error": "no adapter-test results found; run_experiment id adapter-test first (about 10 s)"}
    tbl = _md_table(["engine", "input beats", "result beats", "checks"], [[r["engine"], r["input_beats"], r["result_beats"], r["checks"]] for r in p["rows"]])
    return {"id": "adapter-test", "source": src, "table": tbl, "engines_pass": len(p["rows"]), "fail": p["fail"],
            "summary": "%d engines PASS%s" % (len(p["rows"]), "" if not p["fail"] else ", FAILURES: %s" % "; ".join(p["fail"])),
            "docs": "docs/SOC_PLAN.md", "say": "Show `summary` and `table`."}


def _result_precheck(e, req):
    p = parse_precheck(_read(os.path.join(REPO, "precheck", "results", "summary.tsv")))
    if not p["rows"]:
        return {"error": "precheck/results/summary.tsv not found; run `make precheck` in a terminal"}
    tbl = _md_table(["check", "status", "seconds"], [[r["check"], r["status"], r["seconds"]] for r in p["rows"]])
    return {"id": "precheck", "source": "precheck/results/summary.tsv (committed; the check is run in a terminal)", "table": tbl,
            "summary": "%d/%d checks PASS" % (p["passed"], p["total"]), "docs": "docs/PRECHECK.md",
            "say": "Show `summary` and `table`. It ran on user_project_wrapper only."}


def _result_checks(e, req):
    t = _job_log(req.job_id) if req.job_id else _latest_log(e["target"])
    if not t:
        return {"error": "no finished %s job found; run_experiment id %s first, then pass its job_id" % (e["id"], e["id"]), "docs": e["docs"]}
    lines = [ln for ln in t.splitlines() if re.search(r"PASS|FAIL|OK\b|ok\b", ln)][:40]
    tail = t.strip().splitlines()[-5:]
    return {"id": e["id"], "source": "job log", "pass_lines": lines, "tail": tail, "docs": e["docs"],
            "say": "Quote the PASS/FAIL lines; do not add claims."}


def _result_precision(e, req):
    acc = parse_precision_doc(_read(os.path.join(REPO, "docs", "PRECISION_STUDY.md")))
    rows, base = [], None
    for f in ("bin", "tern", "int4", "int8", "fp8", "fp16", "bf16"):
        k = _key_numbers("prec_" + f)
        a = acc.get(f, {})
        if f == "bin":
            base = k["cell_um2"]
        rows.append([f, a.get("acc", "n/a"), a.get("param_bits", "n/a"), a.get("accum", "n/a"), k["cells"], k["flops"], k["cell_um2"],
                     "%.1fx" % (k["cell_um2"] / base) if base and k["cell_um2"] else "n/a", k["die_um"], _num(k["setup_ss_ns"], 3), _num(k["hold_ns"], 3)])
    tbl = _md_table(["format", "accuracy %", "param bits", "accumulator", "std cells", "flops", "cell um2", "area vs bin", "die um", "setup@ss ns", "hold ns"], rows)
    ok = [r for r in rows if isinstance(r[1], float)]
    lesson = ("Accuracy sits between %.2f and %.2f percent for tern, int4, int8, fp8, fp16 and bf16 (bin: %.2f), but the cell area grows from %s to %s um2: "
              "ternary costs %s and int4 %s of bin, fp16 %s. Same accuracy, a fraction of the silicon: that is why ternary and int4 win for this neuron. "
              "The floats also leave only %s (fp16) and %s (bf16) ns of setup slack on the 25 ns clock." %
              (min(r[1] for r in ok if r[0] != "bin"), max(r[1] for r in ok if r[0] != "bin"), ok[0][1], rows[0][6], rows[5][6],
               rows[1][7], rows[2][7], rows[5][7], rows[5][9], rows[6][9])) if len(ok) == 7 else "Accuracy source docs/PRECISION_STUDY.md not parsed."
    return {"id": "precision", "source": "designs/prec_*/output/metrics.json (cells, flops, area, die, slack) and docs/PRECISION_STUDY.md Study table A (accuracy, bits)",
            "table": tbl, "lesson": lesson, "docs": "docs/PRECISION_STUDY.md",
            "say": "Show `table` then `lesson`. Weights are baked-in constants, so area excludes weight memory (see the doc)."}


def _result_kv(e, req):
    doc = parse_kv_doc(_read(os.path.join(REPO, "docs", "LLM_INFERENCE.md")))
    rows = []
    for d in KV:
        k, x = _key_numbers(d), doc.get(d, {})
        rows.append([d, x.get("entries", "n/a"), x.get("nominal_bits", "n/a"), x.get("cache_flops", "n/a"), k["flops"], k["cells"], k["die_um"], _num(k["setup_ss_ns"], 3)])
    tbl = _md_table(["design", "entries x bits", "nominal cache bits", "cache flip-flops built", "all flip-flops", "std cells", "die um", "setup@ss ns"], rows)
    n8, i4 = doc.get("kv_attn_n8", {}), doc.get("kv_attn_n8_int4", {})
    lesson = ("Nominal cache bits are not silicon: kv_attn_n8 declares %s cache bits and builds %s cache flip-flops, because the weights are constants and "
              "synthesis removes constant and copied bits. int4 halves the nominal bits (%s) yet builds %s cache flip-flops (all flip-flops %s against %s): "
              "the int8 baseline was already pruned. See designs/kv_attn_n8_int4/NOTES.md, Intuitions and insights." %
              (n8.get("nominal_bits"), n8.get("cache_flops"), i4.get("nominal_bits"), i4.get("cache_flops"), rows[3][4], rows[1][4]))
    return {"id": "kv-cache", "source": "designs/kv_attn_*/output/metrics.json (flip-flops, cells, die, slack) and the table in docs/LLM_INFERENCE.md section 5.1 (nominal bits, cache flip-flops from check_signoff.py --breakdown)",
            "table": tbl, "lesson": lesson, "docs": "docs/LLM_INFERENCE.md", "say": "Show `table` then `lesson`."}


def _result_family(e, req):
    rows = []
    for d in e["designs"]:
        k = _key_numbers(d)
        rows.append([d, k["cells"], k["flops"], k["cell_um2"], k["die_um"], _num(k["setup_ss_ns"], 3), _num(k["hold_ns"], 3), k["wall_s"] or "n/a"])
    return {"id": e["id"], "source": "designs/<d>/output/metrics.json and resources.json (committed)", "docs": e["docs"],
            "table": _md_table(["design", "std cells", "flip-flops", "cell um2", "die um", "setup@ss ns", "hold ns", "flow s"], rows),
            "say": "Show `table`. Ask which member to simulate (run_experiment id %s design <member>)." % e["id"]}


def _result_design(e, req):
    d = (req.design or "").strip()
    if not d or d not in _designs():
        return {"error": "this experiment needs a design", "designs": _designs()}
    if e["id"] in ("simulate", "gl", "gl-final", "check") and req.job_id:
        t = _job_log(req.job_id)
        if t is None:
            return {"error": "unknown job_id %r" % req.job_id}
        lines = [ln for ln in t.splitlines() if re.search(r"PASS|FAIL|mismatch|ERROR|error", ln)][:20]
        return {"id": e["id"], "design": d, "source": "job %s" % req.job_id, "result_lines": lines, "tail": t.strip().splitlines()[-4:],
                "say": "Quote result_lines; PASS means the testbench passed."}
    body = {"design": d}
    if req.job_id:
        body["job_id"] = req.job_id
    r = _server("run_summary", body)
    if "error" in r and "summary" not in r:
        k = _key_numbers(d)
        return {"id": e["id"], "design": d, "run_summary_error": r["error"], "committed_key_numbers": k,
                "say": "run_summary was not available; quote committed_key_numbers (designs/%s/output/metrics.json)." % d}
    return {"id": e["id"], "design": d, "source": "run_summary tool (designs/%s output and runs)" % d, "run_summary": r,
            "say": "Show run_summary.summary verbatim."}


def _result_openroad(e, req):
    d = (req.design or "kv_attn_n8").strip()
    st = _RENDER.get(d)
    if st and st["state"] == "running":
        return {"id": e["id"], "design": d, "state": "running", "seconds": round(time.time() - st["started"], 1),
                "say": "Still rendering (about 30 s); ask again shortly."}
    if st and st["state"] == "failed":
        return {"id": e["id"], "design": d, "state": "failed", "rc": st["rc"], "log": st["log"], "say": "The render failed; show the last lines of the log."}
    return engine_pictures(PicReq(design=d))


# ---------------------------------------------------------------- engine_pictures
VIEWS = {
    "layout": ("01_layout.png", "The finished layout (full die).", "Layout", None),
    "placement": ("02_placement_density.png", "gpl (RePlAce) spreads cells like charges until the density is even; hot bins would be overfilled.",
                  "Placement density (gpl)", ["design__instance__utilization"]),
    "congestion": ("03_routing_congestion.png", "grt (FastRoute) lays nets on a coarse grid; colour is demand against track capacity per tile. "
                   "Hot vertical columns are power stripes using tracks. 100 percent or more would be overflow (GRT-0116).",
                   "Routing congestion (grt)", ["global_route__wirelength", "global_route__vias", "route__drc_errors"]),
    "power": ("04_power_density.png", "Static power density from liberty data and activity defaults.", "Power density", ["power__total"]),
    "ir": ("05_ir_drop.png", "psm (PDNSim) builds a resistor network of the power grid and injects each cell's current; the map is the voltage sag on vccd1 layer met1. "
           "The colour scale is stretched, so a hot cell is still microvolts.", "IR drop (psm)", ["ir__drop__worst", "ir__drop__avg"]),
    "clock": ("06_clock_tree_viewer.png", "cts (TritonCTS) clock tree viewer: time against tree depth. Balanced leaves mean small skew.",
              "Clock tree (cts)", ["clock__skew__worst_setup"]),
    "path": ("07_worst_setup_path.png", "sta (OpenSTA) worst setup path highlighted on the layout.", "Worst setup path (sta)", ["timing__setup__ws"]),
}
_COMMITTED_NAME = {k: "kv_attn_n8_" + v[0] for k, v in VIEWS.items()}


def _view_file(d: str, view: str) -> Optional[str]:
    fn = VIEWS[view][0]
    p = os.path.join(OR_DIR, d, fn)
    if os.path.isfile(p):
        return p
    if d == "kv_attn_n8":
        p = os.path.join(OR_COMMITTED, _COMMITTED_NAME[view])
        if os.path.isfile(p):
            return p
    return None


@post("engine_pictures", "OpenROAD engine pictures of a design with a plain explanation")
def engine_pictures(req: PicReq = PicReq()) -> dict:
    """Show an OpenROAD engine view of a finished design as a picture with an explanation and the real numbers from metrics.json.
    view is one of: placement (gpl), congestion (grt), power, ir (psm), clock (cts), path (sta), layout. Without view it lists
    the views that exist. Every reply has png_url and markdown: paste the markdown line verbatim so the user sees the picture.
    Pictures exist for kv_attn_n8 (committed) and any design rendered with run_experiment id openroad-views."""
    d = (req.design or "kv_attn_n8").strip()
    if d not in _designs():
        return {"error": "unknown design %r" % d, "designs": _designs()}
    avail = [v for v in VIEWS if _view_file(d, v)]
    if not avail:
        return {"error": "no OpenROAD pictures for %s yet; run_experiment id openroad-views design %s (about 30 s, needs Docker)" % (d, d)}
    if not req.view:
        return {"design": d, "views": avail, "say": "Ask which view, then call engine_pictures with design and view."}
    v = req.view.strip().lower()
    v = {"placement_density": "placement", "gpl": "placement", "grt": "congestion", "routing": "congestion", "psm": "ir", "irdrop": "ir",
         "ir_drop": "ir", "cts": "clock", "sta": "path", "timing": "path"}.get(v, v)
    if v not in VIEWS:
        return {"error": "unknown view %r; views: %s" % (req.view, ", ".join(VIEWS))}
    src = _view_file(d, v)
    if not src:
        return {"error": "view %s not rendered for %s; available: %s" % (v, d, ", ".join(avail))}
    os.makedirs(IMG_DIR, exist_ok=True)
    name = "engine_%s_%s.png" % (d, v)
    shutil.copyfile(src, os.path.join(IMG_DIR, name))
    url = "%s/img/%s" % (PUBLIC_URL, name)
    m = _metrics(d)
    nums = {k: m[k] for k in (VIEWS[v][3] or []) if k in m}
    return {"design": d, "view": v, "title": VIEWS[v][2], "png_url": url, "markdown": "![%s %s](%s)" % (d, VIEWS[v][2], url),
            "explanation": VIEWS[v][1], "numbers_from_metrics_json": nums, "source_image": _rel(src),
            "docs": "docs/OPENROAD_ENGINES.md", "say": "Paste `markdown` verbatim, then explain with `explanation` and quote `numbers_from_metrics_json`."}


# ---------------------------------------------------------------- experiment_result dispatch
RESULTS = {"soc-sim": _result_soc_sim, "soc-kv": _result_soc_kv, "adapter-test": _result_adapter, "precheck": _result_precheck,
           "precision": _result_precision, "kv-cache": _result_kv, "openroad-views": _result_openroad,
           "model-check": _result_checks, "check-generated": _result_checks, "test-full": _result_checks,
           "caravel-rtl": _result_checks, "caravel-gl": _result_checks}


@post("experiment_result", "Parsed results of an experiment")
def experiment_result(req: ResultReq) -> dict:
    """The results of an experiment, parsed by code into a markdown table plus a short lesson: soc-kv (prefill cycles per token and
    decode round trip), soc-sim (software against accelerator cycles), precision (7-format table: cells, area, accuracy, slack),
    kv-cache (cache bits against flip-flops), adapter-test (14 engines PASS), precheck (14 checks), per-design flow-all/check
    (run summary), families (committed metrics table). Pass job_id of a finished run_make job for fresh results; without it the
    committed results are used and the reply says so. Show `table` and `lesson` verbatim; unsupported ids get an honest error."""
    e = _find(req.id)
    if not e:
        return {"error": "unknown experiment %r; call list_experiments" % req.id}
    if e["id"] in RESULTS:
        return RESULTS[e["id"]](e, req)
    if e.get("family"):
        return _result_family(e, req)
    if e["group"] == "design":
        return _result_design(e, req)
    return {"error": "no result parser for %s" % e["id"], "docs": e["docs"]}


# ---------------------------------------------------------------- demos
@post("list_demos", "List the demos (numbered menu)")
def list_demos(req: Empty = Empty()) -> dict:
    """The numbered demo menu: 1 precision, 2 kv, 3 rtl2gds, 4 int4, 5 heatmaps, 6 soc, 7 gui, each with a title, time and whether
    it runs a physical flow. Show it as a numbered list and ask which one the user wants; the answer is a number or a name:
    then call demo_steps with it."""
    dm = _demo_defs()
    return {"demos": [{"number": d["num"], "name": d["name"], "title": d["title"], "what": d["blurb"], "minutes": d["minutes"],
                       "physical_flow": d["physical"], "docs": d["docs"]} for d in dm.DEMOS],
            "say": "Show the numbered list. Ask: which demo (number or name)?"}


@post("demo_steps", "The ordered steps of one demo")
def demo_steps(req: DemoReq) -> dict:
    """The ordered steps of a demo (number or name from list_demos): for each step what to say, which tool to call with which
    arguments, and what to look at. Follow them ONE AT A TIME: call the tool, show its result, say the narration, then go on.
    A step marked confirm is a run: show the confirm text and wait for the user's 'yes, run <id>' before calling run_make."""
    dm = _demo_defs()
    d = dm.find(req.name)
    if not d:
        return {"error": "unknown demo %r" % req.name, "menu": dm.menu()}
    steps = [{"step": i + 1, "say": s["say"], "tool": s["tool"], "args": s["args"], "look_for": s["look"],
              "needs_confirmation": bool(s.get("confirm"))} for i, s in enumerate(d["steps"])]
    out = {"number": d["num"], "name": d["name"], "title": d["title"], "minutes": d["minutes"], "physical_flow": d["physical"],
           "docs": d["docs"], "steps": steps}
    if d.get("runner") and steps:       # chat steps and a terminal runner (demo 8, proof)
        out["run_in_terminal"] = "python3 " + d["runner"]
        out["say"] = ("Do step 1 now: say its narration, call its tool, show the result. Then continue with the next step. "
                      "The terminal version, which also prints and runs the shasum and git commands that verify the receipt, is run_in_terminal.")
    elif d.get("runner"):
        out["run_in_terminal"] = "python3 " + d["runner"]
        out["say"] = "This demo opens real windows: it is run from a terminal with the command in run_in_terminal."
    else:
        out["say"] = "Do step 1 now: say its narration, call its tool, show the result. Then continue with the next step."
    return out
