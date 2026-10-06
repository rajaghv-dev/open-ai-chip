#!/usr/bin/env python3
"""Skills, memory and context tools for the Hermes tool server (a FastAPI APIRouter, auto-mounted by tool_server.py).

Tools (POST /<name>, operationId == name): skill_plan, remember, recall, forget, memory_digest, context_brief; plus the
hidden (not in the model's tool list) record_run endpoint used by the job system.
Plain-function API for other modules (no HTTP): record_run(), remember_note(), recall_notes(), forget_note(), digest(),
context_brief(), skill_plan_for().

Memory = markdown files in build/agent/memory/ (git-ignored, local only; override the directory with CHIP_MEMORY_DIR
for tests): MEMORY.md (index), <topic>.md (notes), runs.md (automatic run history). This is the ONLY place this module
writes. It never edits repo files; skill_plan only returns plans.
Do not import tool_server.py (it imports this file).
Docs: examples/hermes_desktop/tool_server/README.md, docs/HERMES_DESKTOP.md, docs/AGENT_CONTEXT.md
"""
import datetime
import os
import re
import sys
import threading
from typing import Dict, List, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
sys.path.insert(0, os.path.join(REPO, "scripts", "lib"))
import repo  # noqa: E402

router = APIRouter()
_LOCK = threading.Lock()

MEM_DIR = os.environ.get("CHIP_MEMORY_DIR") or os.path.join(REPO, "build", "agent", "memory")
CONTEXT_DOC = os.path.join(REPO, "docs", "AGENT_CONTEXT.md")
MAX_NOTE_CHARS = 400
MAX_TOPIC_ENTRIES = 100        # oldest entries of a topic file are dropped above this
MAX_RUN_ENTRIES = 200          # oldest runs.md entries are dropped above this
MAX_TOPICS = 40
MAX_FIELD = 200
DIGEST_TOKENS = 400            # 4 chars per token
BRIEF_CONTEXT_CHARS = 5000     # cap of the AGENT_CONTEXT.md text inside context_brief
TOPIC_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
RESERVED = {"memory", "runs", "index"}
SECRET_RE = re.compile(r"(sk-[A-Za-z0-9]{16,}|ghp_[A-Za-z0-9]{16,}|xox[bp]-[A-Za-z0-9-]{10,}|AKIA[0-9A-Z]{12,}|"
                       r"(password|passwd|secret|api[_-]?key|token|bearer)\s*[:=]\s*\S{6,}|"
                       r"[A-Fa-f0-9]{40,}|[A-Za-z0-9+/]{48,}={0,2}|-----BEGIN [A-Z ]*PRIVATE KEY)", re.I)
ENTRY_RE = re.compile(r"^- \[(?P<id>[0-9a-z]+)\] (?P<ts>\d{4}-\d\d-\d\d \d\d:\d\d) (?P<text>.*)$")


def _tokens(text: str) -> int:
    return (len(text) + 3) // 4


def _now() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M")


def _one_line(s, n=MAX_FIELD) -> str:
    return re.sub(r"\s+", " ", str(s if s is not None else "")).strip()[:n]


def _path(name: str) -> str:
    return os.path.join(MEM_DIR, name + ".md")


def _read_entries(name: str) -> List[dict]:
    try:
        lines = open(_path(name), encoding="utf-8").read().splitlines()
    except OSError:
        return []
    out = []
    for l in lines:
        m = ENTRY_RE.match(l)
        if m:
            out.append(m.groupdict())
    return out


def _write_entries(name: str, title: str, entries: List[dict], cap: int) -> None:
    os.makedirs(MEM_DIR, exist_ok=True)
    entries = entries[-cap:]
    body = "# %s\n\n" % title + "".join("- [%s] %s %s\n" % (e["id"], e["ts"], e["text"]) for e in entries)
    tmp = _path(name) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(body)
    os.replace(tmp, _path(name))


def _topics() -> List[str]:
    if not os.path.isdir(MEM_DIR):
        return []
    return sorted(f[:-3] for f in os.listdir(MEM_DIR)
                  if f.endswith(".md") and f[:-3].lower() not in RESERVED and TOPIC_RE.match(f[:-3]))


def _new_id(used: set) -> str:
    n = int(datetime.datetime.now().strftime("%y%m%d%H%M%S"))
    while True:
        s = format(n, "x")
        if s not in used:
            return s
        n += 1


def _update_index() -> None:
    lines = ["# Agent memory index", "",
             "Local to this machine, never committed. Files: <topic>.md (notes, via remember/recall/forget), "
             "runs.md (automatic run history).", ""]
    for t in _topics():
        lines.append("- [%s](%s.md): %d notes" % (t, t, len(_read_entries(t))))
    lines.append("- [runs](runs.md): %d runs" % len(_read_entries("runs")))
    with open(os.path.join(MEM_DIR, "MEMORY.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")


# ---------------------------------------------------------------- memory API (plain functions)
def remember_note(note: str, topic: Optional[str] = None) -> dict:
    topic = (topic or "general").strip().lower()
    if not TOPIC_RE.match(topic) or topic in RESERVED:
        return {"error": "invalid topic %r: use 1-32 chars a-z 0-9 _ - (not %s)" % (topic, "/".join(sorted(RESERVED)))}
    text = _one_line(note, MAX_NOTE_CHARS + 1)
    if not text:
        return {"error": "empty note"}
    if len(text) > MAX_NOTE_CHARS:
        return {"error": "note too long (max %d chars): shorten it" % MAX_NOTE_CHARS}
    if SECRET_RE.search(text):
        return {"error": "note looks like it contains a secret (key, token, password); not stored"}
    with _LOCK:
        if topic not in _topics() and len(_topics()) >= MAX_TOPICS:
            return {"error": "too many topics (max %d): reuse one" % MAX_TOPICS}
        ents = _read_entries(topic)
        e = {"id": _new_id({x["id"] for t in _topics() + ["runs"] for x in _read_entries(t)}), "ts": _now(), "text": text}
        ents.append(e)
        _write_entries(topic, "Notes: " + topic, ents, MAX_TOPIC_ENTRIES)
        _update_index()
    return {"id": e["id"], "topic": topic, "stored": text}


def _all_entries(include_runs: bool = False) -> List[dict]:
    out = []
    for t in _topics() + (["runs"] if include_runs else []):
        for e in _read_entries(t):
            out.append(dict(e, topic=t))
    out.sort(key=lambda e: (e["ts"], e["id"]), reverse=True)     # newest first
    return out


def recall_notes(query: Optional[str] = None, limit: int = 10) -> dict:
    words = [w for w in re.findall(r"[a-z0-9_]+", (query or "").lower()) if len(w) > 1]
    hits = []
    for e in _all_entries(include_runs=True):
        hay = (e["topic"] + " " + e["text"]).lower()
        if not words or all(w in hay for w in words) or (len(words) > 1 and sum(w in hay for w in words) * 2 >= len(words)):
            hits.append(e)
    hits = hits[:max(1, min(int(limit), 30))]
    return {"count": len(hits), "notes": [{"id": e["id"], "topic": e["topic"], "date": e["ts"], "text": e["text"]}
                                          for e in hits]}


def forget_note(note_id: str) -> dict:
    nid = _one_line(note_id, 40).lower()
    if not re.match(r"^[0-9a-z]+$", nid):
        return {"error": "invalid id"}
    with _LOCK:
        for t in _topics() + ["runs"]:
            ents = _read_entries(t)
            keep = [e for e in ents if e["id"] != nid]
            if len(keep) != len(ents):
                _write_entries(t, "Run history" if t == "runs" else "Notes: " + t, keep,
                               MAX_RUN_ENTRIES if t == "runs" else MAX_TOPIC_ENTRIES)
                _update_index()
                return {"forgotten": nid, "topic": t}
    return {"error": "no note with id %s (use recall to list ids)" % nid}


def record_run(design: Optional[str], command: str, result: str, key_numbers: Optional[str] = None,
               log_path: Optional[str] = None) -> dict:
    """Append one dated run entry to runs.md. Never raises (the job system calls it)."""
    try:
        parts = ["design=%s" % (_one_line(design, 60) or "-"), "cmd=%s" % _one_line(command, 120),
                 "result=%s" % _one_line(result, 40)]
        if key_numbers:
            parts.append("numbers=%s" % _one_line(key_numbers))
        if log_path:
            parts.append("log=%s" % _one_line(os.path.relpath(log_path, REPO) if os.path.isabs(log_path) and
                                              log_path.startswith(REPO) else log_path, 120))
        text = " | ".join(parts)
        if SECRET_RE.search(text):
            text = SECRET_RE.sub("[redacted]", text)
        with _LOCK:
            ents = _read_entries("runs")
            e = {"id": _new_id({x["id"] for t in _topics() + ["runs"] for x in _read_entries(t)}), "ts": _now(),
                 "text": text}
            ents.append(e)
            _write_entries("runs", "Run history", ents, MAX_RUN_ENTRIES)
            _update_index()
        return {"id": e["id"], "stored": text}
    except Exception as exc:    # history must never break a job
        return {"error": str(exc)}


def _fit(lines: List[str], cap: int) -> List[str]:
    """Keep whole lines (newest first) while they fit in cap chars."""
    out, used = [], 0
    for l in lines:
        if used + len(l) + 1 > cap:
            break
        out.append(l)
        used += len(l) + 1
    return out


def digest(max_tokens: int = DIGEST_TOKENS) -> str:
    """Latest notes + last 5 runs, newest first, hard-capped at max_tokens (4 chars/token): notes get 45 percent of the
    room, runs the rest; lines are shortened, whole lines dropped, so the cap always holds."""
    cap = max_tokens * 4
    head, mid = "MEMORY (local notes; newest first):", "LAST RUNS:"
    room = cap - len(head) - len(mid) - 4
    notes = ["- [%s] %s: %s" % (e["topic"], e["ts"][:10], e["text"][:140]) for e in _all_entries()[:8]]
    runs = ["- %s %s" % (e["ts"], e["text"][:170]) for e in list(reversed(_read_entries("runs")))[:5]]
    n = _fit(notes, int(room * 0.45)) or ["- (no notes yet)"]
    r = _fit(runs, room - sum(len(x) + 1 for x in n)) or ["- (no runs recorded yet)"]
    return "\n".join([head] + n + [mid] + r)[:cap]


def context_brief() -> dict:
    try:
        ctx = open(CONTEXT_DOC, encoding="utf-8").read()
    except OSError:
        ctx = "(docs/AGENT_CONTEXT.md missing)"
    ctx = re.sub(r"<!--.*?-->\n?", "", ctx, flags=re.S)
    if len(ctx) > BRIEF_CONTEXT_CHARS:
        ctx = ctx[:BRIEF_CONTEXT_CHARS].rsplit("\n", 1)[0] + "\n..."
    return {"context": ctx, "memory": digest(), "note": "Facts above are curated; numbers still come from tools."}


# ---------------------------------------------------------------- skills data
GATE = "needs the confirm step: first call returns confirm_id; ask the user to reply 'yes, run <id>', then repeat with it"
HANDOFF = ("hand to Claude (claude_task) or do it in Claude Code; this agent does not edit files. "
           "claude_task cannot edit either: it can only read, run make and describe the change")


def _s(tool, args, why):
    return {"tool": tool, "args": args, "why": why}


SKILLS: Dict[str, dict] = {
    "harden-design": {
        "kind": "run",
        "summary": "RTL to clean GDSII with make flow-all, then read the result and diagnose a failure.",
        "doc": ".claude/skills/harden-design/SKILL.md (+ reference.md); docs/VALIDATION.md, docs/LESSONS.md",
        "needs_design": True,
        "can_do": ["run make doctor, make flow-all DESIGN=<d>, then look at results (read only)",
                   "summarise a run, diagnose a failed stage, suggest the fix to apply"],
        "needs_edits": ["applying a fix (config.json, rtl/, pin_order.cfg, *.sdc), changing a die size, new design files"],
        "steps": lambda d: [
            _s("run_make", {"target": "doctor"}, "Docker, LibreLane image, PDK ready (%s)" % GATE),
            _s("job_status", {"job_id": "<id from run_make>"}, "poll until state is done"),
            _s("job_list", {}, "check no other physical flow is running: only one at a time"),
            _s("run_make", {"target": "flow-all", "design": d},
               "simulate, gds, check, gl, gl-final, collect (up to 10 min per flow; %s)" % GATE),
            _s("job_status", {"job_id": "<id>"}, "poll until done; report the exit status"),
            _s("run_summary", {"design": d}, "one-screen result of the run (stages, key numbers)"),
            _s("diagnose", {"design": d}, "only if a stage failed: matches the failure table in the skill"),
            _s("suggest", {"design": d}, "only if a stage failed: the fix to apply (a human or Claude applies it)"),
            _s("signoff_summary", {"design": d}, "DRC, LVS, timing numbers; quote the values"),
        ],
    },
    "soc-run": {
        "kind": "run",
        "summary": "Run the PicoRV32 SoC sim, KV firmware, adapter test and Caravel RTL/GL sims.",
        "doc": ".claude/skills/soc-run/SKILL.md; docs/SOC_PLAN.md, docs/CARAVEL_SIM.md",
        "needs_design": False,
        "can_do": ["make soc-sim, soc-kv, adapter-test, caravel-rtl, caravel-gl (cheapest first) and read the log"],
        "needs_edits": ["adding a firmware test case, putting a new engine behind wb_stream_adapter.v, register map changes"],
        "steps": lambda d: [
            _s("run_make", {"target": "soc-sim"}, "PicoRV32 + tiny_ai_core firmware sim, cheapest (%s)" % GATE),
            _s("job_status", {"job_id": "<id>"}, "poll; the log tail shows PASS or the first failure"),
            _s("run_make", {"target": "soc-kv"}, "KV-attention firmware behind the adapter"),
            _s("run_make", {"target": "adapter-test"}, "all stream engines behind wb_stream_adapter"),
            _s("run_make", {"target": "caravel-rtl"}, "full Caravel RTL sim (needs build/caravel downloads)"),
            _s("run_make", {"target": "caravel-gl"}, "Caravel hybrid gate-level sim"),
            _s("get_skill", {"name": "soc-run"}, "read section 7 / 8 for how to read the results"),
        ],
    },
    "wrapper-build": {
        "kind": "run",
        "summary": "Export macro views, then harden a user_project_wrapper_<tag> around a macro.",
        "doc": ".claude/skills/wrapper-build/SKILL.md (+ reference.md); docs/ARCHITECTURE.md",
        "needs_design": True,
        "can_do": ["make views DESIGN=<macro>, make flow-all DESIGN=<existing wrapper>, read the result"],
        "needs_edits": ["creating designs/user_project_wrapper_<tag>/ (config.json, rtl, tb), signoff allowances, MACROS config"],
        "steps": lambda d: [
            _s("run_make", {"target": "views", "design": d},
               "export LEF/GDS/netlist views of the macro (design = the macro, e.g. soc_kv_attn_n8) (%s)" % GATE),
            _s("job_status", {"job_id": "<id>"}, "poll until done"),
            _s("job_list", {}, "no other physical flow running"),
            _s("run_make", {"target": "flow-all", "design": "user_project_wrapper_<tag>"},
               "harden the wrapper; the wrapper dir must already exist (existing: user_project_wrapper_soc_itm, "
               "user_project_wrapper_soc_kv)"),
            _s("run_summary", {"design": "user_project_wrapper_<tag>"}, "result"),
            _s("diagnose", {"design": "user_project_wrapper_<tag>"}, "if a stage failed"),
        ],
    },
    "write-design-notes": {
        "kind": "edit",
        "summary": "Write or refresh designs/<d>/NOTES.md with required headings and cited numbers.",
        "doc": ".claude/skills/write-design-notes/SKILL.md; reference examples designs/prec_int8/NOTES.md",
        "needs_design": True,
        "can_do": ["read the skill, metrics (read_metrics) and sections of existing notes (notes_section, explain)"],
        "needs_edits": ["the whole job: writing designs/<d>/NOTES.md"],
        "steps": lambda d: [
            _s("get_skill", {"name": "write-design-notes"}, "read the rules and the section-to-source table"),
            _s("read_metrics", {"design": d}, "confirm designs/%s/output/metrics.json exists (else the flow is not done)" % d),
            "1. Read designs/%s/README.md, rtl/*.v, tb/*_tb.v and a reference NOTES.md (prec_int8 or tiny_ai_core)." % d,
            "2. Fill each section from output/metrics.json, output/reports/*, resources.json; every number names its file.",
            "3. Required headings: architecture, data flow, verification, layout, synthesis, floorplan, placement, "
            "clock tree, routing, timing, drc, lvs, power, antenna, run time, reproduce, intuitions and insights.",
            "4. Check: python3 .claude/skills/write-design-notes/check_notes.py designs/%s/NOTES.md, then bash tests/run_tests.sh (== notes)." % d,
            "Only designs/%s/NOTES.md changes. " % d + HANDOFF,
        ],
    },
    "precision-variant": {
        "kind": "edit",
        "summary": "Add or change a number-format variant of the 9-input neuron (prec_<fmt>).",
        "doc": ".claude/skills/precision-variant/SKILL.md (+ reference.md); docs/PRECISION_STUDY.md; model/precision_hw/spec.md",
        "needs_design": False,
        "can_do": ["read the skill and the study; read_metrics of existing prec_* designs to compare formats"],
        "needs_edits": ["model/precision_hw/{spec.md,golden.py,gen.py}, designs/prec_<fmt>/*, wiring lists, docs"],
        "steps": lambda d: [
            _s("get_skill", {"name": "precision-variant"}, "read it first"),
            "1. Spec first: add the format to model/precision_hw/spec.md (weights, bias, accumulator, rounding, latency).",
            "2. golden.py: add to FMTS/WBITS/BIAS_BITS/ACC_BITS/LATENCY, quantise() and infer() with exact Fractions; "
            "python3 model/precision_hw/golden.py --check.",
            "3. gen.py: ROM branch (continuous assign only), <= 1023 cases; python3 model/precision_hw/gen.py (second run writes nothing).",
            "4. RTL: copy prec_int8 (integer) or prec_fp16 (float) to designs/prec_<fmt>/rtl/; tb with `define DUT; config.json; "
            "README.md with a 'Latency <n>' line.",
            "5. Wire it in: Makefile ALL_DESIGNS, tests/run_tests.sh NEWENG, scripts/check_generated.sh, scripts/docs/tables.py ORDER, tests/adapter/run.sh.",
            "6. Gates: make simulate DESIGN=prec_<fmt> (run_make, allowed), make flow-all DESIGN=prec_<fmt> (run_make), "
            "python3 model/precision_hw/report.py, make table, make test, make check-generated.",
            "Lesson: a float MAC misses 25 ns single-stage; never raise CLOCK_PERIOD (docs/PRECISION_STUDY.md section 6). " + HANDOFF,
        ],
    },
    "add-tiny-engine": {
        "kind": "edit",
        "summary": "Add a new tiny stream engine (model, generated ROM + vectors, RTL, tb, config, wiring).",
        "doc": ".claude/skills/add-tiny-engine/SKILL.md (+ templates/); docs/ARCHITECTURE.md; model/tiny_ai/",
        "needs_design": False,
        "can_do": ["read the skill and sibling designs (read_metrics, list_designs) to pick the closest one to copy"],
        "needs_edits": ["model/<eng>/, designs/<eng>/, Makefile, tests/run_tests.sh, scripts/*, docs"],
        "steps": lambda d: [
            _s("get_skill", {"name": "add-tiny-engine"}, "read it first"),
            "1. model/<eng>/spec.json (stream contract, latency, label rule). 2. train.py exhaustive fit. 3. golden.py with --check.",
            "4. gen_rom.py writes designs/<eng>/rtl/<eng>_rom.v and tb/vectors.hex (continuous assign ROM, write-if-changed, <= 1023 cases). "
            "Never hand-edit generated files.",
            "5. RTL designs/<eng>/rtl/<eng>.v (24-pin stream; sections IO/MEMORY/COMPUTE/CONTROL); tb/<eng>_tb.v; config.json "
            "(copy a neighbour: CLOCK_PERIOD 25, slew margin 20, die about 40 percent utilisation); README.md.",
            "6. Wire it in: Makefile ALL_DESIGNS/MODELS/generate/model-check, tests/run_tests.sh, scripts/check_generated.sh, "
            "scripts/docs/tables.py, tests/adapter/run.sh, README counts.",
            "7. Gates in order: golden.py --check; make simulate DESIGN=<eng> (run_make); negative test; adapter test (run_make adapter-test); "
            "make flow-all DESIGN=<eng> (run_make); make table; make test; make check-generated.",
            "8. Then NOTES.md with the write-design-notes skill. " + HANDOFF,
        ],
    },
}
ALIASES = {"harden": "harden-design", "soc": "soc-run", "wrapper": "wrapper-build", "notes": "write-design-notes",
           "precision": "precision-variant", "add-engine": "add-tiny-engine", "engine": "add-tiny-engine"}


def skill_plan_for(skill: str, design: Optional[str] = None) -> dict:
    key = ALIASES.get((skill or "").strip().lower().lstrip("/"), (skill or "").strip().lower())
    if key not in SKILLS:
        return {"error": "unknown skill %r" % skill, "skills": sorted(SKILLS)}
    s = SKILLS[key]
    if design is not None and design not in repo.all_designs() and not design.startswith("user_project_wrapper"):
        return {"error": "unknown design %r" % design, "valid": repo.all_designs()}
    d = design or "<design>"
    raw = s["steps"](d)
    steps = []
    for i, st in enumerate(raw, 1):
        steps.append(dict(st, n=i) if isinstance(st, dict) else {"n": i, "do": st})
    out = {"skill": key, "mode": s["kind"], "summary": s["summary"], "docs": s["doc"],
           "agent_can_do": s["can_do"], "needs_edits": s["needs_edits"], "steps": steps}
    if s["needs_design"] and not design:
        out["note"] = "no design given: replace <design> in the steps; ask the user which design"
    if s["kind"] == "edit":
        out["handoff"] = HANDOFF
    else:
        out["note_gate"] = "run_make and claude_task " + GATE
        out["handoff"] = "edits are needed only to apply a fix: " + HANDOFF
    return out


# ---------------------------------------------------------------- HTTP models and routes
class SkillPlanReq(BaseModel):
    skill: str = Field(..., description="one of: harden-design, soc-run, wrapper-build, write-design-notes, "
                                        "precision-variant, add-tiny-engine", examples=["harden-design"])
    design: Optional[str] = Field(None, description="design name, e.g. kv_attn_n8 (needed for harden-design, "
                                                   "wrapper-build, write-design-notes)", examples=["kv_attn_n8"])


class RememberReq(BaseModel):
    note: str = Field(..., description="one short fact to keep (max 400 chars, no secrets)", min_length=1, max_length=2000)
    topic: Optional[str] = Field(None, description="optional topic, lowercase a-z 0-9 _ -, default general",
                                 examples=["preferences"])


class RecallReq(BaseModel):
    query: Optional[str] = Field(None, description="keywords; omit to list the newest notes")
    limit: Optional[int] = Field(10, ge=1, le=30)


class ForgetReq(BaseModel):
    id: str = Field(..., description="note id from recall or remember")


class RecordRunReq(BaseModel):
    design: Optional[str] = None
    command: str = ""
    result: str = ""
    key_numbers: Optional[str] = None
    log_path: Optional[str] = None


class Nothing(BaseModel):
    pass


def post(name: str, summary: str, show: bool = True):
    return router.post("/" + name, operation_id=name, summary=summary, response_model=None, include_in_schema=show)


@post("skill_plan", "Ordered plan for a project skill, with the tool to call at each step")
def skill_plan(req: SkillPlanReq) -> dict:
    """Return the ordered steps of one repo skill (harden-design, soc-run, wrapper-build, write-design-notes,
    precision-variant, add-tiny-engine): which steps this agent can do itself (run make, read results) and which need
    file edits (this agent never edits: hand to Claude). Each step names the tool to call. Use when the user asks to
    harden/run/add/write something."""
    return skill_plan_for(req.skill, req.design)


@post("remember", "Save one short note to local memory")
def remember(req: RememberReq) -> dict:
    """Store one short fact or preference (max 400 chars, never a secret) in local agent memory (build/agent/memory).
    Use when the user says 'remember ...'. Returns the note id."""
    return remember_note(req.note, req.topic)


@post("recall", "Search local memory notes and past runs, newest first")
def recall(req: RecallReq = RecallReq()) -> dict:
    """Keyword search over saved notes and the run history (newest first). Omit query to list the newest. Use when the
    user asks what was noted or run before."""
    return recall_notes(req.query, req.limit or 10)


@post("forget", "Delete one memory note by id")
def forget(req: ForgetReq) -> dict:
    """Delete one note (or run-history entry) by its id (see recall). Only when the user asks to forget it."""
    return forget_note(req.id)


@post("memory_digest", "Short digest of the latest notes and the last 5 runs")
def memory_digest(req: Nothing = Nothing()) -> dict:
    """At most about 400 tokens: the latest notes and the last 5 recorded runs (design, command, result, log)."""
    d = digest()
    return {"digest": d, "approx_tokens": _tokens(d)}


@post("context_brief", "Call first in a new conversation: repo facts and memory digest")
def context_brief_tool(req: Nothing = Nothing()) -> dict:
    """Call ONCE at the start of a conversation. Returns the curated repo facts (what the repo is, design families,
    flow stages, where evidence lives, the hard rules, the skills) plus the memory digest (latest notes, last 5 runs)."""
    return context_brief()


@post("record_run", "Internal: append a run to the history (used by the job system)", show=False)
def record_run_tool(req: RecordRunReq) -> dict:
    return record_run(req.design, req.command, req.result, req.key_numbers, req.log_path)
