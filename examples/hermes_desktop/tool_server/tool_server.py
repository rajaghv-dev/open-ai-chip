#!/usr/bin/env python3
"""Local tool server for the Hermes desktop/browser agent (FastAPI, 127.0.0.1 only).

Every operation is `POST /<tool_name>` with a JSON body and a JSON reply, operationId == tool name, so Open WebUI
(User Tool Server, http://127.0.0.1:8770/openapi.json) turns each one into a model tool.

Groups: the 10 read-only EDA tools (tools/eda_tools.py), search_docs (examples/hermes_rag), klayout_view (offscreen
render, PNG served at GET /img/<name>), list_skills/get_skill, run_make (allow-listed make targets as jobs),
job_status/job_list/job_cancel, claude_task (headless Claude Code CLI, edit tools denied), health.
See README.md in this directory for the safety model.
Run: build/agent/venv/bin/python examples/hermes_desktop/tool_server/tool_server.py
Docs: examples/hermes_desktop/tool_server/README.md, docs/HERMES_DESKTOP.md
"""
import json
import os
import re
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from typing import Any, Dict, List, Optional

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
for _p in (os.path.join(REPO, "tools"), os.path.join(REPO, "examples", "hermes_klayout_gui"),
           os.path.join(REPO, "examples", "hermes_rag"), os.path.join(REPO, "scripts", "lib")):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import eda_tools  # noqa: E402
import rag  # noqa: E402
import repo  # noqa: E402  (scripts/lib/repo.py: design list, DOCKER_HOST default)
from fastapi import Body, FastAPI  # noqa: E402
from fastapi.middleware.cors import CORSMiddleware  # noqa: E402
from fastapi.responses import FileResponse, JSONResponse  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402

HOST = "127.0.0.1"
PORT = int(os.environ.get("CHIP_TOOLS_PORT", "8770"))
PUBLIC_URL = os.environ.get("CHIP_TOOLS_PUBLIC_URL", "http://%s:%d" % (HOST, PORT)).rstrip("/")
CORS_ORIGINS = ["http://127.0.0.1:8080", "http://localhost:8080"]
IMG_DIR = os.path.join(REPO, "build", "agent", "klayout_gui")
JOB_DIR = os.path.join(REPO, "build", "agent", "jobs")
SKILL_DIR = os.path.join(REPO, ".claude", "skills")
# the platform's Docker socket for jobs: the Colima VM "osl" socket if it exists, else /var/run/docker.sock on Linux
DOCKER_SOCK = (repo.docker_env({}).get("DOCKER_HOST") or "unix://" + repo.COLIMA_SOCK)[len("unix://"):]
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
CLAUDE_BIN = shutil.which("claude") or os.path.expanduser("~/.local/bin/claude")
VERSION = "1.0"

# Safety model (see README.md): run_make only accepts these targets, never an arbitrary command string. Targets that
# write files outside build/ (clean, generate, table, precheck, push-like ones) are deliberately absent.
MAKE_ALLOW = ["doctor", "test", "simulate", "check", "gl", "gl-final", "gds", "flow-all", "views", "collect", "view",
              "soc-sim", "soc-kv", "adapter-test", "model-check", "check-generated", "test-full", "caravel-rtl",
              "caravel-gl"]
PHYSICAL = {"gds", "flow-all", "views", "collect", "test-full"}      # one at a time, globally
MAX_JOBS = 2
MAKE_TIMEOUT_S = 3 * 3600          # safety net; flows cap themselves (FLOW_TIMEOUT)
CLAUDE_TIMEOUT_S = 3600
LOG_TAIL = 60

# claude_task runs the headless Claude Code CLI. Three layers: the built-in tool set is cut to read/search/Bash (no
# Edit/Write), CLAUDE_ALLOWED lists the Bash patterns that need no prompt, CLAUDE_DISALLOWED blocks mutation, network,
# git, docker and cf (ChipFoundry CLI: never from an agent, see CLAUDE.md HARD RULES).
CLAUDE_TOOLS = ["Read", "Grep", "Glob", "Bash"]      # built-in set; no Edit/Write/Notebook/Web at all
CLAUDE_ALLOWED = ["Read", "Grep", "Glob", "Bash(make *)", "Bash(python3 scripts/flow/*)", "Bash(cat *)", "Bash(ls *)",
                  "Bash(head *)", "Bash(tail *)", "Bash(wc *)"]
CLAUDE_DISALLOWED = ["Edit", "Write", "NotebookEdit", "MultiEdit", "Bash(git *)", "Bash(rm *)", "Bash(mv *)",
                     "Bash(cp *)", "Bash(sed *)", "Bash(tee *)", "Bash(touch *)", "Bash(mkdir *)", "Bash(curl *)",
                     "Bash(wget *)", "Bash(cf *)", "Bash(docker *)", "Bash(*>*)",
                     "Bash(make clean*)", "Bash(make generate*)", "Bash(make table*)", "Bash(make precheck*)"]
CLAUDE_SYSTEM_PROMPT = ("You are running headless as a read-and-run helper for the open-ai-chip repo. You must NOT "
                        "create, edit, delete or move any file (no Edit, Write, shell redirection, sed -i, git). You may "
                        "read files and run make targets. If a step needs a file change, do not attempt it: describe "
                        "the change you would make and stop. Never run cf login/init/push, never publish anything. "
                        "Report status, commands run, evidence paths.")
DEFAULT_MAX_TURNS = 30


# ---------------------------------------------------------------- helpers
def valid_designs() -> List[str]:
    """DESIGN names accepted by run_make: the Makefile ALL_DESIGNS list."""
    return repo.all_designs()


def _clean(d: dict) -> dict:
    return {k: v for k, v in d.items() if v is not None}


def _eda(name: str, **args) -> dict:
    return eda_tools.call(name, _clean(args))


def _skills() -> Dict[str, dict]:
    out = {}
    if not os.path.isdir(SKILL_DIR):
        return out
    for n in sorted(os.listdir(SKILL_DIR)):
        f = os.path.join(SKILL_DIR, n, "SKILL.md")
        if not os.path.isfile(f):
            continue
        txt = open(f, encoding="utf-8").read()
        fm, body = {}, txt
        m = re.match(r"---\n(.*?)\n---\n?(.*)", txt, re.S)
        if m:
            for line in m.group(1).splitlines():
                if ":" in line:
                    k, v = line.split(":", 1)
                    fm[k.strip()] = v.strip()
            body = m.group(2)
        out[fm.get("name", n)] = {"name": fm.get("name", n), "description": fm.get("description", ""),
                                  "body": body.lstrip("\n"), "dir": n}
    return out


def _tail(path: str, n: int = LOG_TAIL) -> List[str]:
    try:
        with open(path, "r", errors="replace") as f:
            return [l.rstrip("\n") for l in f.readlines()[-n:]]
    except OSError:
        return []


# ---------------------------------------------------------------- jobs
class Job:
    def __init__(self, jid, kind, cmd, label, physical=False, env=None, cwd=REPO):
        self.id, self.kind, self.cmd, self.label, self.physical = jid, kind, cmd, label, physical
        self.env, self.cwd = env, cwd
        self.log = os.path.join(JOB_DIR, jid + ".log")
        self.t0 = time.time()
        self.t1 = None
        self.rc = None
        self.proc = None
        self.cancelled = False
        self.extra: Dict[str, Any] = {}

    @property
    def state(self):
        if self.rc is None:
            return "running"
        return "done" if self.rc == 0 and not self.cancelled else "failed"

    def seconds(self):
        return round((self.t1 or time.time()) - self.t0, 1)

    def summary(self):
        return {"job_id": self.id, "kind": self.kind, "label": self.label, "state": self.state, "rc": self.rc,
                "seconds": self.seconds(), "command": " ".join(shlex.quote(c) for c in self.cmd)}


class JobManager:
    def __init__(self):
        self.jobs: Dict[str, Job] = {}
        self.lock = threading.Lock()
        self.n = 0

    def _running(self):
        return [j for j in self.jobs.values() if j.state == "running"]

    def start(self, kind, cmd, label, physical=False, env=None, claude=False, timeout=MAKE_TIMEOUT_S, cwd=REPO):
        """Returns (job, error). Enforces the caps under one lock."""
        # All cap checks and the job insert happen under one lock so two concurrent POSTs cannot both pass.
        with self.lock:
            run = self._running()
            if len(run) >= MAX_JOBS:
                return None, "job cap reached (%d running: %s); wait or job_cancel one" % (len(run), ", ".join(j.id for j in run))
            if physical and any(j.physical for j in run):
                return None, "a physical-flow job is already running (%s); only one at a time" % [j.id for j in run if j.physical][0]
            if claude and any(j.kind == "claude" for j in run):
                return None, "a claude_task is already running (%s)" % [j.id for j in run if j.kind == "claude"][0]
            self.n += 1
            jid = "%s-%02d" % (time.strftime("%Y%m%d_%H%M%S"), self.n)
            job = Job(jid, kind, cmd, label, physical, env, cwd)
            os.makedirs(JOB_DIR, exist_ok=True)
            self.jobs[jid] = job
            threading.Thread(target=self._run, args=(job, claude, timeout), daemon=True).start()
            return job, None

    def _run(self, job, claude, timeout):
        try:
            with open(job.log, "w", buffering=1) as lf:
                lf.write("$ %s\n" % " ".join(shlex.quote(c) for c in job.cmd))
                env = dict(os.environ)
                if job.env:
                    env.update(job.env)
                # start_new_session=True (below) makes the child its own process group, so _kill (killpg) also stops
                # make's children (docker, openroad) instead of orphaning them.
                job.proc = subprocess.Popen(job.cmd, cwd=job.cwd, env=env, stdin=subprocess.DEVNULL,
                                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                                            errors="replace", start_new_session=True)
                if job.cancelled:          # cancel arrived before the process existed
                    self._kill(job)
                timer = threading.Timer(timeout, lambda: self._kill(job))
                timer.daemon = True
                timer.start()
                try:
                    if claude:
                        render_claude_stream(job.proc.stdout, lf, job.extra)
                    else:
                        for line in job.proc.stdout:
                            lf.write(line)
                    job.rc = job.proc.wait()
                finally:
                    timer.cancel()
                if claude and job.extra.get("is_error") and job.rc == 0:
                    job.rc = 1
                lf.write("[exit %s after %.1fs]\n" % (job.rc, time.time() - job.t0))
        except Exception as e:  # noqa: BLE001
            job.rc = job.rc if job.rc is not None else 127
            try:
                with open(job.log, "a") as lf:
                    lf.write("[job error] %s: %s\n" % (type(e).__name__, e))
            except OSError:
                pass
        finally:
            job.t1 = time.time()
            _record_history(job)

    @staticmethod
    def _kill(job):
        if job.proc and job.proc.poll() is None:
            try:
                os.killpg(job.proc.pid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass

    def cancel(self, jid):
        job = self.jobs.get(jid)
        if not job:
            return None
        if job.state == "running":
            job.cancelled = True
            self._kill(job)
        return job


def _ext(fn: str):
    """Load an extension module (examples/hermes_desktop/tool_server/<fn>.py) once, for in-process calls."""
    import importlib.util
    if fn not in _EXT_CACHE:
        spec = importlib.util.spec_from_file_location("chip_hook_" + fn, os.path.join(HERE, fn + ".py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _EXT_CACHE[fn] = mod
    return _EXT_CACHE[fn]


_EXT_CACHE: Dict[str, Any] = {}


def _record_history(job: "Job") -> None:
    """Run-history hook: when a make/claude job ends, append it to the memory run history
    (skills_memory_tools.record_run). Never raises; a missing module or any error is ignored."""
    try:
        if job.cancelled:
            result = "CANCELLED"
        else:
            result = "PASS" if job.rc == 0 else "FAIL rc=%s" % job.rc
        design = next((c[7:] for c in job.cmd if c.startswith("DESIGN=")), None)
        numbers = None
        if design and job.kind == "make":
            try:
                k = _ext("analysis_tools").key_numbers(eda_tools._metrics(design))
                numbers = "cells=%s ff=%s setup=%s hold=%s" % (k["stdcells"], k["flip_flops"], (k["setup_worst"] or {}).get("slack_ns"),
                                                               (k["hold_worst"] or {}).get("slack_ns"))
            except Exception:  # noqa: BLE001
                numbers = None
        cmd = " ".join(job.cmd) if job.kind == "make" else "claude_task: " + job.label
        _ext("skills_memory_tools").record_run(design, cmd, result, numbers, job.log)
    except Exception:  # noqa: BLE001
        pass


JOBS = JobManager()


def render_claude_stream(stream, out, info: dict):
    """Turn Claude Code stream-json lines into readable log lines; fills info with result/cost."""
    for raw in stream:
        raw = raw.strip()
        if not raw:
            continue
        try:
            ev = json.loads(raw)
        except ValueError:
            out.write(raw + "\n")
            continue
        t = ev.get("type")
        if t == "system" and ev.get("subtype") == "init":
            out.write("[claude] session start, model=%s, tools=%d\n" % (ev.get("model"), len(ev.get("tools") or [])))
        elif t == "assistant":
            for b in (ev.get("message") or {}).get("content") or []:
                if b.get("type") == "text" and b.get("text", "").strip():
                    out.write("[assistant] %s\n" % b["text"].strip())
                elif b.get("type") == "tool_use":
                    out.write("[tool_use] %s %s\n" % (b.get("name"), json.dumps(b.get("input"), sort_keys=True)[:400]))
        elif t == "user":
            c = (ev.get("message") or {}).get("content")
            for b in c if isinstance(c, list) else []:
                if b.get("type") == "tool_result":
                    body = b.get("content")
                    if isinstance(body, list):
                        body = " ".join(x.get("text", "") for x in body if isinstance(x, dict))
                    tag = "tool_error" if b.get("is_error") else "tool_result"
                    out.write("[%s] %s\n" % (tag, " ".join(str(body).split())[:300]))
        elif t == "result":
            info["is_error"] = bool(ev.get("is_error"))
            info["result"] = ev.get("result")
            denials = ev.get("permission_denials") or []
            out.write("[result] %s turns=%s duration_ms=%s cost_usd=%s\n" % (
                "ERROR" if ev.get("is_error") else "ok", ev.get("num_turns"), ev.get("duration_ms"), ev.get("total_cost_usd")))
            if ev.get("usage"):
                u = ev["usage"]
                out.write("[usage] input=%s output=%s cache_read=%s\n" % (
                    u.get("input_tokens"), u.get("output_tokens"), u.get("cache_read_input_tokens")))
            for d in denials:
                out.write("[permission_denied] %s %s\n" % (d.get("tool_name"), json.dumps(d.get("tool_input"))[:200]))
            if ev.get("result"):
                out.write("[final] %s\n" % str(ev["result"]).strip())


def build_claude_command(prompt: str, max_turns: int = DEFAULT_MAX_TURNS) -> List[str]:
    return [CLAUDE_BIN, "-p", prompt, "--output-format", "stream-json", "--verbose",
            "--permission-mode", "dontAsk", "--max-turns", str(max_turns),
            "--tools", ",".join(CLAUDE_TOOLS), "--strict-mcp-config",
            "--disallowedTools", ",".join(CLAUDE_DISALLOWED),
            "--allowedTools", ",".join(CLAUDE_ALLOWED),
            "--append-system-prompt", CLAUDE_SYSTEM_PROMPT]


def build_claude_prompt(skill, design, instructions) -> str:
    parts = []
    if skill:
        parts.append("Use the %s skill." % skill)
    if design:
        parts.append("Design: %s." % design)
    parts.append(instructions.strip())
    parts.append("You may run make targets and read files; you must not edit, create or delete files. "
                 "Report status, commands run (with exit codes), and evidence paths.")
    return " ".join(parts)


# ---------------------------------------------------------------- models
D = Field(..., description="design directory name under designs/, e.g. kv_attn_n8 (see list_designs)",
          examples=["kv_attn_n8"])


class Empty(BaseModel):
    pass


class DesignReq(BaseModel):
    design: str = D


class ReadMetricsReq(BaseModel):
    design: str = D
    keys: Optional[List[str]] = Field(None, description="exact metric keys, e.g. [\"design__instance__count__stdcell\"]")
    pattern: Optional[str] = Field(None, description="case-insensitive substring of metric keys, e.g. setup__ws",
                                   examples=["drc"])


class CompareReq(BaseModel):
    metric: str = Field(..., description="exact metrics.json key, e.g. design__instance__count__stdcell",
                        examples=["design__instance__count__stdcell"])
    designs: Optional[List[str]] = Field(None, description="designs to compare; omit for all hardened designs")


class LayerStatsReq(BaseModel):
    design: str = D
    layer: str = Field(..., description="met1..met5, li1, poly, diff, mcon, via..via4 or 'L/D' like 68/20", examples=["met4"])


class FindPinsReq(BaseModel):
    design: str = D
    pattern: str = Field(..., description="regular expression over pin/label names", examples=["wbs_dat_i"])


class RenderReq(BaseModel):
    design: str = D
    out: Optional[str] = Field(None, description="optional .png path under build/agent/")
    width_px: Optional[int] = Field(None, description="image size in pixels (default 1200)")


class ClassifySlewReq(BaseModel):
    design: str = D
    corner: Optional[str] = Field(None, description="timing corner, default max_ss_100C_1v60")


class SearchReq(BaseModel):
    query: str = Field(..., description="natural-language question or identifiers to search the repo docs for",
                       examples=["why does kv_attn_n8_int4 have more flip-flops"])
    k: Optional[int] = Field(4, description="number of passages, 1..8", ge=1, le=8)


class Zoom(BaseModel):
    bbox: Optional[List[float]] = Field(None, description="[x1,y1,x2,y2] in um", min_length=4, max_length=4)
    cell: Optional[str] = Field(None, description="cell or instance name, e.g. mprj")
    full: Optional[bool] = Field(None, description="true = whole design")


class KlayoutViewReq(BaseModel):
    design: str = D
    layers: Optional[List[str]] = Field(None, description="layers to show (others hidden), e.g. [\"met4\",\"met5\"]; omit for all",
                                        examples=[["met4", "met5"]])
    zoom: Optional[Zoom] = Field(None, description="one of bbox / cell / full; default whole design")
    demo_markers: Optional[bool] = Field(None, description="draw 5 labelled DEMO boxes (not real errors)")
    width: Optional[int] = Field(None, description="PNG width px, 200..2400 (default 1200)", ge=200, le=2400)
    height: Optional[int] = Field(None, description="PNG height px, 200..2400 (default 900)", ge=200, le=2400)


class SkillReq(BaseModel):
    name: str = Field(..., description="skill name from list_skills, e.g. harden-design", examples=["harden-design"])


class RunMakeReq(BaseModel):
    target: Optional[str] = Field(None, description="one of: " + " ".join(MAKE_ALLOW) + ". Required on the first call.",
                                  examples=["simulate"])
    design: Optional[str] = Field(None, description="DESIGN=<name> (must be in the Makefile design list); omit for default",
                                  examples=["vision_block"])
    confirm_id: Optional[str] = Field(None, description="SECOND call only: the confirm_id from the first reply, after the "
                                      "USER wrote 'yes, run <confirm_id>'. Never invent or reuse one; target/design may be omitted.")


class JobReq(BaseModel):
    job_id: str = Field(..., description="id returned by run_make or claude_task")


class ClaudeTaskReq(BaseModel):
    instructions: Optional[str] = Field(None, description="what Claude should do (it can read files and run make; it cannot edit files); required on the first call",
                                        max_length=4000)
    confirm_id: Optional[str] = Field(None, description="SECOND call only: the confirm_id from the first reply, after the "
                                      "USER wrote 'yes, run <confirm_id>'. Never invent or reuse one.")
    skill: Optional[str] = Field(None, description="optional skill name from list_skills for Claude to follow")
    design: Optional[str] = Field(None, description="optional design name")
    max_turns: Optional[int] = Field(DEFAULT_MAX_TURNS, ge=1, le=60, description="turn cap (default 30)")


# ---------------------------------------------------------------- safety gate (two-step confirmation)
# Measured problem: the question "How do I run the full flow for kv_attn_n8?" made the 8B model start `make flow-all`
# three times. run_make and claude_task therefore never start anything on the first call: they return a confirm_id and
# the exact command; the job starts only on a second call that carries that id (valid 10 min, single use).
# CHIP_TOOLS_NO_CONFIRM=1 (read per call) skips the gate for tests and terminal use.
CONFIRM_TTL_S = 600
_CONFIRMS: Dict[str, dict] = {}
_CONFIRM_LOCK = threading.Lock()


def _no_confirm() -> bool:
    return os.environ.get("CHIP_TOOLS_NO_CONFIRM") == "1"


def _issue_confirm(kind: str, payload: dict, will_run: str, what: str) -> dict:
    import secrets
    now = time.time()
    with _CONFIRM_LOCK:
        for k in [k for k, v in _CONFIRMS.items() if v["expires"] < now]:
            del _CONFIRMS[k]
        cid = secrets.token_hex(3)
        _CONFIRMS[cid] = {"kind": kind, "payload": payload, "expires": now + CONFIRM_TTL_S}
    return {"needs_confirmation": True, "confirm_id": cid, "started": False, "will_run": will_run, "what": what,
            "valid_minutes": CONFIRM_TTL_S // 60,
            "say": "Reply 'yes, run %s' to start. Nothing has been started yet." % cid}


def _take_confirm(kind: str, cid: str):
    """(payload, None) when cid is valid for this kind; the id is consumed by _consume_confirm after a successful start."""
    now = time.time()
    with _CONFIRM_LOCK:
        c = _CONFIRMS.get(cid)
        if not c or c["expires"] < now:
            _CONFIRMS.pop(cid, None)
            return None, "unknown, expired or already used confirm_id %r; call again without confirm_id to get a new one" % cid
        if c["kind"] != kind:
            return None, "confirm_id %r belongs to %s, not %s" % (cid, c["kind"], kind)
        return c["payload"], None


def _consume_confirm(cid: str):
    with _CONFIRM_LOCK:
        _CONFIRMS.pop(cid, None)


# ---------------------------------------------------------------- app
app = FastAPI(title="open-ai-chip tool server",
              description="Local tools for the Hermes agent: read-only EDA/layout tools, doc search, KLayout renders, "
                          "skills, allow-listed make jobs and a no-edit Claude Code bridge. Localhost only.",
              version=VERSION)
app.add_middleware(CORSMiddleware, allow_origins=CORS_ORIGINS, allow_methods=["GET", "POST", "OPTIONS"],
                   allow_headers=["*"])


def post(name: str, summary: str):
    return app.post("/" + name, operation_id=name, summary=summary, response_model=None)


@post("list_designs", "List all designs")
def list_designs(req: Empty = Empty()) -> dict:
    """List all designs with a one-line description, whether hardened (has metrics.json) and DESIGN_NAME."""
    return _eda("list_designs")


@post("read_metrics", "Read metrics.json values")
def read_metrics(req: ReadMetricsReq) -> dict:
    """Read values from a design's metrics.json by exact keys and/or substring pattern (e.g. 'setup__ws', 'drc', 'stdcell'), with meanings."""
    return _eda("read_metrics", design=req.design, keys=req.keys, pattern=req.pattern)


@post("compare_designs", "Compare one metric across designs")
def compare_designs(req: CompareReq) -> dict:
    """Compare one exact metrics.json key across designs; sorted table with min/max."""
    return _eda("compare_designs", metric=req.metric, designs=req.designs)


@post("layout_summary", "GDS layout summary")
def layout_summary(req: DesignReq) -> dict:
    """From the GDS: top cell, die bbox (um), cell count, shape count, busiest layers, macro instances."""
    return _eda("layout_summary", design=req.design)


@post("layer_stats", "Stats of one GDS layer")
def layer_stats(req: LayerStatsReq) -> dict:
    """Shape count, merged area (um^2) and bbox of one GDS layer."""
    return _eda("layer_stats", design=req.design, layer=req.layer)


@post("find_pins", "Find pins by regex")
def find_pins(req: FindPinsReq) -> dict:
    """Find pins/labels by regex in the GDS text labels (LEF fallback); max 50."""
    return _eda("find_pins", design=req.design, pattern=req.pattern)


@post("render_png", "Quick PNG render of a design")
def render_png(req: RenderReq) -> dict:
    """Render the GDS to a PNG and return its path plus a `markdown` field ![...](png_url): paste that field verbatim
    into your answer so the image is shown in the chat. For layer/zoom control use klayout_view."""
    r = _eda("render_png", design=req.design, out=req.out, width_px=req.width_px)
    src = os.path.join(REPO, r["path"]) if isinstance(r, dict) and r.get("path") else None
    if src and os.path.isfile(src):
        try:
            os.makedirs(IMG_DIR, exist_ok=True)
            name = "render_%s_%s.png" % (req.design, time.strftime("%Y%m%d_%H%M%S"))
            shutil.copyfile(src, os.path.join(IMG_DIR, name))
            r["png_url"] = "%s/img/%s" % (PUBLIC_URL, name)
            r["markdown"] = "![%s](%s)" % (req.design, r["png_url"])
        except OSError:
            pass
    return r


@post("signoff_summary", "Signoff summary")
def signoff_summary(req: DesignReq) -> dict:
    """DRC, LVS, XOR, antenna, worst setup/hold slack with corner, slew/cap/fanout and the check_signoff.py verdict."""
    return _eda("signoff_summary", design=req.design)


@post("classify_slew", "Classify slew violations")
def classify_slew(req: ClassifySlewReq) -> dict:
    """Split a run's max-slew violations into port-driven vs internal (needs a local flow run dir)."""
    return _eda("classify_slew", design=req.design, corner=req.corner)


@post("precheck_summary", "ChipFoundry precheck result")
def precheck_summary(req: Empty = Empty()) -> dict:
    """Newest ChipFoundry precheck result: check name -> PASS/FAIL."""
    return _eda("precheck_summary")


@post("search_docs", "Search the repo documentation")
def search_docs(req: SearchReq) -> dict:
    """BM25 search over the repo's markdown. Returns passages with file, heading, line range and text. Use it to answer questions about designs, flow, results."""
    return rag.call({"query": req.query, "k": req.k}, mode="v2")


# KLayout's offscreen LayoutView is not thread-safe and FastAPI runs sync endpoints in a thread pool: serialise renders.
_VIEW_LOCK = threading.Lock()


@post("klayout_view", "Render a layout view to a PNG")
def klayout_view(req: KlayoutViewReq) -> dict:
    """Render a design's layout with KLayout (offscreen) and return png_url and a `markdown` field ![...](png_url).
    Paste the `markdown` field verbatim into your answer so the picture appears in the chat. Optionally restrict layers and zoom (bbox in um, a cell/instance name, or full)."""
    try:
        eda_tools._check_design(req.design)
        from offscreen_backend import OffscreenBackend
        import view_api as va
        with _VIEW_LOCK:
            be = OffscreenBackend()
            try:
                r = be.open_design(req.design)
                if not r.get("ok"):
                    return {"error": r.get("error")}
                if req.layers:
                    r = be.show_layers(req.layers, True)
                    if not r.get("ok"):
                        return {"error": r.get("error")}
                z = req.zoom
                target = {"full": True}
                if z:
                    given = {k: v for k, v in z.model_dump().items() if v not in (None, False)}
                    if len(given) > 1:
                        return {"error": "zoom: give exactly one of bbox, cell, full"}
                    if given:
                        target = given
                r = be.zoom_to(target)
                if not r.get("ok"):
                    return {"error": r.get("error")}
                if req.demo_markers:
                    r = be.highlight_drc(req.design, 200, True)
                    if not r.get("ok"):
                        return {"error": r.get("error")}
                os.makedirs(IMG_DIR, exist_ok=True)
                name = "%s_%s.png" % (req.design, time.strftime("%Y%m%d_%H%M%S_") + "%03d" % (int(time.time() * 1000) % 1000))
                r = be.snapshot(os.path.join(IMG_DIR, name), req.width or 1200, req.height or 900)
                if not r.get("ok"):
                    return {"error": r.get("error")}
                return {"png_url": "%s/img/%s" % (PUBLIC_URL, name), "view_bbox_um": r["view_bbox_um"],
                        "visible_layers": r["visible_layers"], "markdown": "![%s](%s/img/%s)" % (req.design, PUBLIC_URL, name)}
            finally:
                be.close()
    except Exception as e:  # noqa: BLE001
        return {"error": "%s: %s" % (type(e).__name__, e)}


_IMG_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,100}\.png$")


@app.get("/img/{name}", include_in_schema=False)
def get_img(name: str):
    if not _IMG_RE.match(name) or ".." in name:
        return JSONResponse({"error": "bad image name"}, status_code=400)
    p = os.path.realpath(os.path.join(IMG_DIR, name))
    if not p.startswith(os.path.realpath(IMG_DIR) + os.sep) or not os.path.isfile(p):
        return JSONResponse({"error": "not found"}, status_code=404)
    return FileResponse(p, media_type="image/png")


@post("list_skills", "List the repo skills")
def list_skills(req: Empty = Empty()) -> dict:
    """List the project skills (name and description). Then call get_skill to read one before doing that kind of task."""
    return {"skills": [{"name": s["name"], "description": s["description"]} for s in _skills().values()]}


@post("get_skill", "Read one skill")
def get_skill(req: SkillReq) -> dict:
    """Return the full SKILL.md body of one skill (workflow instructions)."""
    s = _skills().get(req.name)
    if not s:
        return {"error": "unknown skill %r; valid: %s" % (req.name, ", ".join(_skills()))}
    return {"name": s["name"], "description": s["description"], "body": s["body"]}


@post("run_make", "Run an allow-listed make target as a background job (needs user confirmation)")
def run_make(req: RunMakeReq) -> dict:
    """Start `make <target> [DESIGN=<design>]` as a background job. TWO STEPS: the first call starts NOTHING and returns
    {needs_confirmation, confirm_id, will_run, say}: show `say` to the user and STOP. Only after the user replies
    'yes, run <confirm_id>' call again with that confirm_id; then it returns {job_id}, poll with job_status.
    Call it only when the user ORDERS a run; a question like 'how do I run...' is never an order.
    gds/flow-all/views/collect/test-full are physical flows: one at a time, can take minutes."""
    confirmed = None
    if not req.confirm_id and req.target and re.fullmatch(r"[0-9a-f]{6}", req.target.strip()) and req.target.strip() in _CONFIRMS:
        # the 8B model sometimes puts the id of "yes, run <id>" in `target`; a pending issued id can only mean the confirmation
        req.confirm_id = req.target.strip()
    if req.confirm_id:
        payload, err = _take_confirm("make", req.confirm_id.strip())
        if err:
            return {"error": err}
        confirmed = req.confirm_id.strip()
        target, design = payload["target"], payload["design"]
    else:
        target, design = req.target, req.design
    if not target or target not in MAKE_ALLOW:
        return {"error": "target %r not allowed; allowed: %s" % (target or "", " ".join(MAKE_ALLOW))}
    cmd = ["make", target]
    if design is not None:
        if design not in valid_designs():
            return {"error": "unknown design %r; valid: %s" % (design, " ".join(valid_designs()))}
        cmd.append("DESIGN=" + design)
    if not confirmed and not _no_confirm():
        return _issue_confirm("make", {"target": target, "design": design}, " ".join(cmd),
                              "make %s%s (%s)" % (target, " for " + design if design else "",
                                                  "physical flow, can take minutes" if target in PHYSICAL else "background job"))
    # DOCKER_HOST points the flow at the platform socket (flows run LibreLane in Docker)
    env = {"DOCKER_HOST": "unix://" + DOCKER_SOCK}
    job, err = JOBS.start("make", cmd, " ".join(cmd[1:]), physical=target in PHYSICAL, env=env)
    if err:
        return {"error": err}
    if confirmed:
        _consume_confirm(confirmed)
    return {"job_id": job.id, "state": job.state, "command": " ".join(cmd), "log": os.path.relpath(job.log, REPO)}


def _status(job: Job) -> dict:
    r = job.summary()
    r["log_tail"] = _tail(job.log)
    r["log"] = os.path.relpath(job.log, REPO)
    if job.kind == "claude" and job.extra.get("result") is not None:
        r["result"] = job.extra["result"]
    return r


@post("job_status", "Status and log tail of a job")
def job_status(req: JobReq) -> dict:
    """State (running|done|failed), return code, seconds and the last 60 log lines of a job."""
    job = JOBS.jobs.get(req.job_id)
    return _status(job) if job else {"error": "unknown job_id %r" % req.job_id}


@post("job_list", "List jobs")
def job_list(req: Empty = Empty()) -> dict:
    """All jobs started by this server (newest last) with state and seconds."""
    return {"jobs": [j.summary() for j in JOBS.jobs.values()]}


@post("job_cancel", "Cancel a running job")
def job_cancel(req: JobReq) -> dict:
    """Terminate a running job (SIGTERM to its process group)."""
    job = JOBS.cancel(req.job_id)
    if not job:
        return {"error": "unknown job_id %r" % req.job_id}
    time.sleep(0.3)
    return {"job_id": job.id, "state": job.state, "cancel_requested": job.cancelled}


@post("claude_task", "Hand a task to Claude Code (read and run only, cannot edit files; needs user confirmation)")
def claude_task(req: ClaudeTaskReq) -> dict:
    """Run the Claude Code CLI headless as a background job with edit tools denied: it can read files and run make
    targets (including flows) and reports back; it cannot create or edit files. TWO STEPS: the first call starts NOTHING
    and returns {needs_confirmation, confirm_id, will_run, say}: show `say` to the user and STOP; call again with the
    confirm_id only after the user replies 'yes, run <confirm_id>'. Then it returns {job_id}; poll job_status.
    Uses the owner's Claude plan: use for work that needs a stronger model, not for simple lookups."""
    if not os.path.exists(CLAUDE_BIN):
        return {"error": "claude CLI not found at %s" % CLAUDE_BIN}
    confirmed = None
    if req.confirm_id:
        payload, err = _take_confirm("claude", req.confirm_id.strip())
        if err:
            return {"error": err}
        confirmed = req.confirm_id.strip()
        instructions, skill, design, max_turns = payload["instructions"], payload["skill"], payload["design"], payload["max_turns"]
    else:
        instructions, skill, design, max_turns = req.instructions, req.skill, req.design, req.max_turns or DEFAULT_MAX_TURNS
    if not instructions or not instructions.strip():
        return {"error": "instructions are required"}
    if skill and skill not in _skills():
        return {"error": "unknown skill %r; valid: %s" % (skill, ", ".join(_skills()))}
    if design and design not in valid_designs():
        return {"error": "unknown design %r" % design}
    prompt = build_claude_prompt(skill, design, instructions)
    cmd = build_claude_command(prompt, max_turns)
    if not confirmed and not _no_confirm():
        return _issue_confirm("claude", {"instructions": instructions, "skill": skill, "design": design,
                                         "max_turns": max_turns},
                              " ".join(shlex.quote(c) for c in cmd),
                              "Claude Code task (read and run only, edit tools denied): " + instructions.strip()[:200])
    env = {"DOCKER_HOST": "unix://" + DOCKER_SOCK}
    job, err = JOBS.start("claude", cmd, "claude_task" + (" " + skill if skill else ""), claude=True,
                          env=env, timeout=CLAUDE_TIMEOUT_S)
    if err:
        return {"error": err}
    if confirmed:
        _consume_confirm(confirmed)
    return {"job_id": job.id, "state": job.state, "command": job.summary()["command"], "log": os.path.relpath(job.log, REPO)}


def _ollama():
    try:
        with urllib.request.urlopen(OLLAMA_URL + "/api/version", timeout=1.5) as r:
            return {"reachable": True, "version": json.loads(r.read()).get("version")}
    except Exception as e:  # noqa: BLE001
        return {"reachable": False, "error": type(e).__name__}


def _docker():
    if not os.path.exists(DOCKER_SOCK):
        return {"reachable": False, "error": "socket missing"}
    try:
        s = socket.socket(socket.AF_UNIX)
        s.settimeout(2)
        s.connect(DOCKER_SOCK)
        s.sendall(b"GET /_ping HTTP/1.0\r\n\r\n")
        ok = b"OK" in s.recv(512)
        s.close()
        return {"reachable": ok, "socket": DOCKER_SOCK.replace(os.path.expanduser("~"), "~", 1)}
    except OSError as e:
        return {"reachable": False, "error": type(e).__name__}


@post("health", "Server health and dependencies")
def health(req: Empty = Empty()) -> dict:
    """Versions and reachability: this server, python, klayout, Ollama, Docker (colima), Claude CLI."""
    try:
        import klayout.db as kdb
        kv = kdb.__version__ if hasattr(kdb, "__version__") else "present"
    except Exception:  # noqa: BLE001
        kv = None
    claude_ok = os.path.exists(CLAUDE_BIN)
    return {"ok": True, "server_version": VERSION, "python": sys.version.split()[0], "klayout": kv,
            "ollama": _ollama(), "docker": _docker(),
            "claude": {"path": CLAUDE_BIN if claude_ok else None, "found": claude_ok},
            "running_jobs": len(JOBS._running()), "designs": len(valid_designs()), "skills": len(_skills())}


@app.get("/health", include_in_schema=False)
def health_get():
    return health()


# ---- extension modules: every examples/hermes_desktop/tool_server/*_tools.py that defines `router` (a FastAPI APIRouter)
# is mounted here, in file-name order, so feature modules (run analysis, GUI operation, skills/memory) are added without
# editing this file. Each module owns its endpoints; operationIds must be unique across modules (Open WebUI tool names).
def _mount_extensions() -> List[str]:
    import importlib.util
    mounted = []
    here = os.path.dirname(os.path.abspath(__file__))
    for fn in sorted(os.listdir(here)):
        if not fn.endswith("_tools.py"):
            continue
        spec = importlib.util.spec_from_file_location("chip_ext_" + fn[:-3], os.path.join(here, fn))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        if hasattr(mod, "router"):
            app.include_router(mod.router)
            if hasattr(mod, "install"):         # optional hook (proof_tools: call-log middleware); needs the app before it starts
                mod.install(app)
            mounted.append(fn)
    return mounted


EXTENSIONS = _mount_extensions()


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=HOST, port=PORT, log_level="info")
