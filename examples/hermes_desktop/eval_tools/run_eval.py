#!/usr/bin/env python3
"""Tool-calling eval for the Hermes chip agent (desktop app / Open WebUI, preset "Hermes chip agent", legacy function calling).
Cases: examples/hermes_desktop/eval_tools/cases.json (60 cases, 14 groups; fields documented in its "//" entry).

  python3 examples/hermes_desktop/eval_tools/run_eval.py                 # chat mode: Open WebUI /api/chat/completions with the preset
  python3 examples/hermes_desktop/eval_tools/run_eval.py --direct        # the same tool-selection prompt straight to Ollama (the model's choice only)
  python3 examples/hermes_desktop/eval_tools/run_eval.py --backend hermes --hermes-toolsets <mcp toolset> [--dry-run]
                                                                           # Nous Hermes Agent CLI one-shot: hermes -z "<prompt>" (needs the repo tools exposed over MCP)
Backends are small functions (run_webui, run_ollama, run_hermes) returning (called tools+args, answer, extra); add one to drive another agent.
  ... [--group run,gui] [--id m01,r01] [--allow-gui] [--allow-memory] [--tag after-router] [--no-summary]

Chat mode: each case is one fresh single-message chat (temperature 0, seed 42 come from the preset); the tools the tool server
ACTUALLY received are read from its call log (build/agent/proof/calls.jsonl: tool, args, time), not from what the model says.
Run cases never start a job: the runner compares job_list before and after, and confirm cases use bogus confirm ids.
Cases with side_effect gui (opens real windows) or memory (writes a note, deleted again) are skipped unless --allow-gui / --allow-memory.
Direct mode: rebuilds what Open WebUI 0.11.4 sends for the legacy tool-selection step (task model = the preset: its system prompt
is prepended, the tools prompt template with the live tool specs, History with the system message, Query) and posts it to Ollama
/api/chat with the preset options. Answers are not produced there, so answer checks are skipped. --lean drops the History block.
A case whose needs_tools are not served by the running tool server is "pending" (not scored, not counted as failure).
Score per case: right tool(s), right args (and no placeholder values such as "-"), no forbidden tool, answer check (chat mode),
no job started. Output: build/agent/tool_eval_<ts>.json (everything) and, for a full unfiltered run,
examples/hermes_desktop/eval_tools/results_summary.json (chat) or results_summary_direct.json (committed, repo-relative paths only).
Exit 0 when the run completed (this is a measurement, not a gate), 2 when a service is unreachable. Standard library only.
Docs: docs/HERMES_DESKTOP.md, build/agent/tool_eval_PLAN.md (phase 2 plan)
"""
import argparse
import copy
import hashlib
import json
import os
import re
import shutil
import statistics
import subprocess
import sys
import time
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
WEBUI = os.environ.get("WEBUI_URL", "http://127.0.0.1:8080")
TOOLS = os.environ.get("TOOLS_URL", "http://127.0.0.1:8770")
OLLAMA = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
PRESET = "hermes-chip-agent"
BASE_MODEL = "hermes3:8b"
CALLS_LOG = os.path.join(REPO, "build", "agent", "proof", "calls.jsonl")
HOOK_LOG = os.path.join(REPO, "build", "agent", "eval_hook.log")
ACCESS_LOG = os.path.join(REPO, "build", "webui", "logs", "tool_server.log")
NEUTRAL = {"context_brief", "memory_digest", "job_list", "job_status", "proof_record", "proof_turn"}
PLACEHOLDERS = {"-", "--", "none", "null", "n/a", "na", "unknown", "string", "", "?", "<design>", "design", "<target>", "..."}
RECEIPT_RE = re.compile(r"\n*---\n\*\*Receipt\*\*.*\Z", re.S)
DETAILS_RE = re.compile(r"<details.*?</details>", re.S)


# ----------------------------------------------------------------------------- http
def http(url, body=None, timeout=60, token=None):
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    if token:
        req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"null")


def tool(name, args=None, timeout=60):
    return http(TOOLS + "/" + name, args or {}, timeout)


# ----------------------------------------------------------------------------- tool specs (same conversion as open_webui.utils.tools)
def resolve_schema(schema, components, seen=None):
    if not schema:
        return {}
    seen = seen or set()
    if "$ref" in schema:
        name = schema["$ref"].split("/")[-1]
        if name in seen:
            return {}
        r = components
        for part in schema["$ref"].strip("#/").split("/")[1:]:
            r = r.get(part, {})
        return resolve_schema(r, components, seen | {name})
    s = copy.deepcopy(schema)
    for k, v in (s.get("properties") or {}).items():
        s["properties"][k] = resolve_schema(v, components, seen)
    if "items" in s:
        s["items"] = resolve_schema(s["items"], components, seen)
    for kw in ("oneOf", "anyOf", "allOf"):
        if isinstance(s.get(kw), list):
            s[kw] = [resolve_schema(i, components, seen) for i in s[kw]]
    return s


def tool_specs(openapi):
    out = []
    for _path, methods in openapi.get("paths", {}).items():
        for m, op in methods.items():
            if m not in ("get", "post", "put", "delete", "patch") or not isinstance(op, dict) or not op.get("operationId"):
                continue
            t = {"name": op["operationId"], "description": op.get("description", op.get("summary", "No description available.")),
                 "parameters": {"type": "object", "properties": {}, "required": []}}
            sch = ((op.get("requestBody") or {}).get("content", {}).get("application/json", {}) or {}).get("schema")
            if sch:
                r = resolve_schema(sch, openapi.get("components", {}))
                if r.get("properties"):
                    t["parameters"]["properties"].update(r["properties"])
                    t["parameters"]["required"] = list(set(t["parameters"]["required"] + r.get("required", [])))
            out.append(t)
    return out


# ----------------------------------------------------------------------------- the tool-selection prompt (direct mode)
def webui_token():
    return http(WEBUI + "/api/v1/auths/signin", {"email": "admin@localhost", "password": "x"})["token"]


def read_text(p):
    with open(os.path.join(REPO, p), encoding="utf-8") as f:
        return f.read()


def live_prompts(token):
    """(preset system prompt, tools prompt template, source note) as Open WebUI has them installed; files as fallback."""
    system = template = None
    try:
        req = urllib.request.Request(WEBUI + "/api/v1/models/model?id=" + PRESET, headers={"Authorization": "Bearer " + token})
        system = json.loads(urllib.request.urlopen(req, timeout=10).read())["params"]["system"]
        req = urllib.request.Request(WEBUI + "/api/v1/tasks/config", headers={"Authorization": "Bearer " + token})
        template = json.loads(urllib.request.urlopen(req, timeout=10).read())["TOOLS_FUNCTION_CALLING_PROMPT_TEMPLATE"]
        return system, template, "installed in Open WebUI"
    except Exception:  # noqa: BLE001
        sys_txt = read_text("tools/prompts/master_prompt.txt").rstrip() + "\n\n" + read_text("examples/hermes_desktop/system_prompt.txt")
        return sys_txt, read_text("examples/hermes_desktop/tools_prompt.txt"), "repo files (Open WebUI not readable)"


def build_direct_messages(system, template, specs, query, digest, lean):
    tools_prompt = template.replace("{{TOOLS}}", json.dumps(specs, ensure_ascii=False))
    sys_msg = system + "\n" + tools_prompt          # the preset system prompt is prepended to the task call's system message
    chat_system = system + ("\n\n[agent memory digest]\n" + digest if digest else "")
    user = "Query: " + query if lean else 'History:\nSYSTEM: """%s"""\nQuery: %s' % (chat_system, query)
    return [{"role": "system", "content": sys_msg}, {"role": "user", "content": user}]


def ollama_select(messages, opts):
    t0 = time.time()
    r = http(OLLAMA + "/api/chat", {"model": BASE_MODEL, "messages": messages, "stream": False, "options": opts}, timeout=300)
    txt = (r.get("message") or {}).get("content", "")
    calls, raw = [], txt
    try:
        js = json.loads(txt[txt.find("{"):txt.rfind("}") + 1])
        for tc in js.get("tool_calls") or []:
            if isinstance(tc, dict):
                calls.append((tc.get("name"), tc.get("parameters") or tc.get("arguments") or {}))
    except Exception:  # noqa: BLE001
        pass
    return calls, raw, time.time() - t0, r.get("prompt_eval_count"), r.get("eval_count")


# ----------------------------------------------------------------------------- chat mode plumbing
def file_size(p):
    try:
        return os.path.getsize(p)
    except OSError:
        return 0


def calls_since(pos):
    """[(tool, args dict)] the server received after byte offset pos of its call log (fallback: access log)."""
    out = []
    try:
        with open(CALLS_LOG, "rb") as f:
            f.seek(pos)
            for line in f.read().decode("utf-8", "replace").splitlines():
                try:
                    e = json.loads(line)
                except ValueError:
                    continue
                a = e.get("args")
                try:
                    a = json.loads(a) if isinstance(a, str) else (a or {})
                except ValueError:
                    a = {"_raw": a}
                out.append((e.get("tool"), a if isinstance(a, dict) else {"_raw": a}))
    except OSError:
        pass
    return out


def clean_answer(text):
    return RECEIPT_RE.sub("", DETAILS_RE.sub("", text or "")).strip()


def chat_turn(token, content, timeout):
    d = http(WEBUI + "/api/chat/completions", {"model": PRESET, "stream": False, "tool_ids": ["server:chip"],
                                              "messages": [{"role": "user", "content": content}]}, timeout=timeout, token=token)
    return d["choices"][0]["message"]["content"]


# ----------------------------------------------------------------------------- backends
def canon(name, served):
    """Tool name as the tool server knows it (an MCP client may prefix it: mcp_chip_read_metrics, chip__read_metrics)."""
    if name in served:
        return name
    for t in sorted(served, key=len, reverse=True):
        if name and name.endswith(t) and name[-len(t) - 1:-len(t)] in ("_", "-", ":", "."):
            return t
    return name


def run_webui(ctx, case):
    before = len(tool("job_list").get("jobs", []))
    pos = file_size(CALLS_LOG)
    ans = chat_turn(ctx["token"], case["prompt"], ctx["timeout"])
    time.sleep(0.4)
    called = calls_since(pos)
    return called, ans, {}, max(0, len(tool("job_list").get("jobs", [])) - before)


def run_ollama(ctx, case):
    msgs = build_direct_messages(ctx["system"], ctx["template"], ctx["specs"], case["prompt"], ctx["digest"], ctx["lean"])
    sel, raw, _dt, pec, ec = ollama_select(msgs, ctx["opts"])
    allowed = {s["name"]: set(s["parameters"]["properties"]) for s in ctx["specs"]}
    called = [(n, {k: v for k, v in (p or {}).items() if k in allowed[n]}) for n, p in sel if n in allowed]
    return called, "", {"raw": raw[:400], "prompt_tokens": pec, "eval_tokens": ec}, 0


def hermes_cmd(ctx, prompt, usage_file=None):
    cmd = ["hermes"]
    if ctx.get("hermes_profile"):
        cmd += ["-p", ctx["hermes_profile"]]
    cmd += ["-z", prompt]
    if ctx.get("hermes_accept_hooks"):
        cmd.append("--accept-hooks")           # the isolated eval home may point the hook at a wrapper command (needs consent)
    if ctx.get("hermes_ignore_user_config"):
        cmd.append("--ignore-user-config")
    if ctx.get("hermes_toolsets"):
        cmd += ["-t", ctx["hermes_toolsets"]]
    if ctx.get("hermes_model"):
        cmd += ["-m", ctx["hermes_model"]]
    if ctx.get("hermes_skills"):
        cmd += ["--skills", ctx["hermes_skills"]]
    if usage_file:
        cmd += ["--usage-file", usage_file]
    cmd += ["--in", ctx.get("hermes_dir") or REPO]
    return cmd


def hermes_env(ctx):
    env = dict(os.environ)
    if ctx.get("hermes_home"):
        env["HERMES_HOME"] = ctx["hermes_home"]           # isolated home under build/: ~/.hermes is never used or read
    env.update(ctx.get("server_env") or {})
    env["CHIP_HOOK_LOG"] = HOOK_LOG                          # the repo's pre_tool_call hook logs every attempted tool (native ones too)
    return env


def hook_since(pos, served):
    """[(tool, {})] the agent ATTEMPTED (hook log), e.g. native skills_list or a call the hook blocked; mcp__chip__<t> -> <t>."""
    out = []
    try:
        with open(HOOK_LOG, "rb") as f:
            f.seek(pos)
            for line in f.read().decode("utf-8", "replace").splitlines():
                try:
                    t = json.loads(line).get("tool", "")
                except ValueError:
                    continue
                out.append((canon(re.sub(r"^mcp[_]+chip[_]+", "", t), served), {}))
    except OSError:
        pass
    return out


def run_hermes(ctx, case):
    """Nous Hermes Agent, non-interactive: `hermes -z "<prompt>"` prints only the final answer; approvals are auto-bypassed, so the
    run is limited to the profile's toolsets (chip profile: repo MCP tools + read-only file/skills) with the repo's pre_tool_call hook.
    Tool calls are read from the tool server call log (the MCP bridge forwards to it)."""
    before = len(tool("job_list").get("jobs", []))
    pos, hpos = file_size(CALLS_LOG), file_size(HOOK_LOG)
    uf = os.path.join(REPO, "build", "agent", "eval_usage_%s.json" % case["id"])
    r = subprocess.run(hermes_cmd(ctx, case["prompt"], uf), capture_output=True, text=True, timeout=ctx["timeout"], cwd=REPO, env=hermes_env(ctx))
    time.sleep(0.4)
    called = [(canon(n, ctx["served"]), a) for n, a in calls_since(pos)]
    seen = {n for n, _ in called}
    called += [(n, a) for n, a in hook_since(hpos, ctx["served"]) if n not in seen and n not in NEUTRAL]
    extra = {"rc": r.returncode, "stderr": r.stderr[-300:]}
    try:
        u = json.load(open(uf))
        extra.update(api_calls=u.get("api_calls"), input_tokens=u.get("input_tokens"), output_tokens=u.get("output_tokens"))
        os.remove(uf)
    except (OSError, ValueError):
        pass
    return called, r.stdout, extra, max(0, len(tool("job_list").get("jobs", [])) - before)


BACKENDS = {"webui": run_webui, "ollama": run_ollama, "hermes": run_hermes}


# ----------------------------------------------------------------------------- scoring
def match(spec, value):
    """One arg constraint against a value (see cases.json '//')."""
    if spec == "absent":
        return value is None or str(value).strip().lower() in PLACEHOLDERS
    if value is None:
        return False
    sv = value if isinstance(value, str) else json.dumps(value)
    if isinstance(spec, str) and spec.startswith("re:"):
        return re.search(spec[3:], sv) is not None
    if isinstance(spec, dict) and "contains" in spec:
        return all(c.lower() in sv.lower() for c in spec["contains"])
    if isinstance(spec, list):
        return str(value).strip().lower() in [str(s).lower() for s in spec]
    return str(value).strip().lower() == str(spec).lower()


def placeholders(args):
    bad = []
    for k, v in (args or {}).items():
        if isinstance(v, str) and v.strip().lower() in PLACEHOLDERS:
            bad.append(k)
    return bad


def metric_numbers(m):
    try:                                            # ground truth straight from the committed file (no server needed)
        with open(os.path.join(REPO, "designs", m["design"], "output", "metrics.json"), encoding="utf-8") as f:
            v = json.load(f).get(m["key"])
        if isinstance(v, (int, float)):
            return v
    except (OSError, ValueError):
        pass
    r = tool("read_metrics", {"design": m["design"], "keys": [m["key"]]})
    v = ((r.get("metrics") or {}).get(m["key"]) or {}).get("value")
    return v


def check_answer(spec, text):
    """(ok, why). Only meaningful in chat mode."""
    if not spec:
        return None, ""
    t = clean_answer(text)
    if "must_contain_regex" in spec and not re.search(spec["must_contain_regex"], t):
        return False, "answer lacks /%s/" % spec["must_contain_regex"]
    if "not_regex" in spec and re.search(spec["not_regex"], t):
        return False, "answer matches forbidden /%s/" % spec["not_regex"]
    if "any_words" in spec and not any(w.lower() in t.lower() for w in spec["any_words"]):
        return False, "answer has none of %s" % spec["any_words"]
    if "metric" in spec:
        v = metric_numbers(spec["metric"])
        if v is None:
            return None, "metric unavailable"
        dig = spec["metric"].get("digits")
        tol = max(0.005 * abs(v), 0.5 * 10 ** -dig if dig is not None else 0)
        nums = []
        for s in re.findall(r"-?\d[\d,]*\.?\d*", t):
            try:
                nums.append(float(s.replace(",", "").rstrip(".")))
            except ValueError:
                pass
        if not any(abs(n - v) <= tol for n in nums):
            return False, "answer has no number close to %s (%s)" % (v, spec["metric"]["key"])
    return True, ""


def score(case, called, answer, jobs_started, chat):
    """called: [(tool, args)] the server received (or the model chose, in direct mode)."""
    expect_names = set(case.get("expect_tools") or [])
    names = [n for n, _ in called if n in expect_names or n not in NEUTRAL]
    sel = [(n, a) for n, a in called if n in expect_names or n not in NEUTRAL]
    if any(n == "run_experiment" for n, _ in sel) and "run_make" not in expect_names:     # run_experiment itself POSTs to /run_make (gated)
        sel = [(n, a) for n, a in sel if n != "run_make"]
        names = [n for n in names if n != "run_make"]
    s = {"tool": True, "args": True, "forbid": True, "answer": None, "nostart": True, "why": []}
    if case.get("tools_optional"):
        pass
    elif case.get("no_tools"):
        if names:
            s["tool"] = False
            s["why"].append("called %s, expected no tool" % ",".join(dict.fromkeys(names)))
    else:
        miss = [t for t in case.get("expect_tools", []) if t not in names]
        if miss:
            s["tool"] = False
            s["why"].append("missing tool %s (called: %s)" % (",".join(miss), ",".join(dict.fromkeys(names)) or "none"))
        if case.get("expect_any") and not any(t in names for t in case["expect_any"]) and not case.get("expect_tools"):
            s["tool"] = False
            s["why"].append("none of %s called (called: %s)" % (",".join(case["expect_any"]), ",".join(dict.fromkeys(names)) or "none"))
    fb = [n for n in dict.fromkeys(names) if n in case.get("forbid_tools", [])]
    if fb:
        s["forbid"] = False
        s["why"].append("forbidden tool " + ",".join(fb))
    for tname, cons in (case.get("args") or {}).items():
        mine = [a for n, a in sel if n == tname]
        if not mine:
            continue
        if not any(all(match(sp, a.get(k)) for k, sp in cons.items()) for a in mine):
            s["args"] = False
            bad = {k: mine[-1].get(k) for k in cons}
            s["why"].append("args of %s %s do not satisfy %s" % (tname, json.dumps(bad)[:120], json.dumps(cons)[:120]))
    for n, a in sel:
        ph = placeholders(a)
        if ph:
            s["args"] = False
            s["why"].append("placeholder value in %s: %s" % (n, ",".join("%s=%r" % (k, a[k]) for k in ph)))
    if chat:
        ok, why = check_answer(case.get("answer"), answer)
        s["answer"] = ok
        if ok is False:
            s["why"].append(why)
    if jobs_started:
        s["nostart"] = False
        s["why"].append("a job was started (%d new)" % jobs_started)
    s["pass"] = all(v is not False for k, v in s.items() if k in ("tool", "args", "forbid", "answer", "nostart"))
    return s, [n for n in dict.fromkeys(names)]


# ----------------------------------------------------------------------------- main
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--direct", action="store_true", help="tool-selection prompt straight to Ollama (no Open WebUI chat)")
    ap.add_argument("--backend", choices=sorted(BACKENDS), default="", help="webui (default), ollama (= --direct) or hermes (Nous Hermes Agent CLI)")
    ap.add_argument("--hermes-toolsets", default="", help="hermes backend: value of hermes -t (the MCP toolset holding the repo tools)")
    ap.add_argument("--hermes-profile", default="", help="hermes backend: profile (hermes -p), e.g. chip")
    ap.add_argument("--hermes-home", default="", help="hermes backend: isolated HERMES_HOME (e.g. build/hermes_eval_home); never ~/.hermes")
    ap.add_argument("--private-server", action="store_true", help="start a private tool server (port 8830, own call log and memory dir) so other sessions cannot pollute the scoring")
    ap.add_argument("--rescore", default="", help="re-score a saved build/agent/tool_eval_*.json with the current cases.json (no model, no tools run)")
    ap.add_argument("--hermes-accept-hooks", action="store_true", help="hermes backend: pass --accept-hooks (isolated home only)")
    ap.add_argument("--hermes-model", default="", help="hermes backend: value of hermes -m")
    ap.add_argument("--hermes-skills", default="", help="hermes backend: value of hermes --skills")
    ap.add_argument("--hermes-isolated", action="store_true", help="hermes backend: add --ignore-user-config (built-in defaults, .env still loaded)")
    ap.add_argument("--dry-run", action="store_true", help="hermes backend: print the command for the first case and stop")
    ap.add_argument("--lean", action="store_true", help="direct mode: omit the History block (what-if: no duplicated system prompt)")
    ap.add_argument("--group", default="", help="comma separated group names")
    ap.add_argument("--id", default="", help="comma separated case ids")
    ap.add_argument("--allow-gui", action="store_true", help="chat mode: also run cases that open real KLayout/Magic windows")
    ap.add_argument("--allow-memory", action="store_true", help="chat mode: also run the remember case (the note is deleted afterwards)")
    ap.add_argument("--timeout", type=int, default=420)
    ap.add_argument("--tag", default="", help="label stored in the output (e.g. baseline, after-router)")
    ap.add_argument("--no-summary", action="store_true", help="do not write results_summary.json even for a full run")
    ap.add_argument("--cases", default=os.path.join(HERE, "cases.json"))
    a = ap.parse_args(argv)
    backend = a.backend or ("ollama" if a.direct else "webui")
    a.direct = backend == "ollama"
    chat = backend != "ollama"
    doc = json.load(open(a.cases, encoding="utf-8"))
    cases = doc["cases"]
    full = not (a.group or a.id)
    if a.group:
        cases = [c for c in cases if c["group"] in a.group.split(",")]
    if a.id:
        cases = [c for c in cases if c["id"] in a.id.split(",")]
    global TOOLS, CALLS_LOG
    if a.rescore:
        return rescore(a)
    server_env, srv_proc = {}, None
    if a.private_server:
        port = os.environ.get("EVAL_TOOLS_PORT", "8830")
        pd, md = os.path.join(REPO, "build", "agent", "eval_proof"), os.path.join(REPO, "build", "agent", "eval_memory")
        os.makedirs(pd, exist_ok=True)
        os.makedirs(md, exist_ok=True)
        server_env = {"CHIP_TOOLS_PORT": port, "CHIP_PROOF_DIR": pd, "CHIP_MEMORY_DIR": md}
        py = os.path.join(REPO, "build", "agent", "venv", "bin", "python")
        lg = open(os.path.join(REPO, "build", "agent", "eval_toolserver.log"), "ab")
        srv_proc = subprocess.Popen([py if os.path.exists(py) else sys.executable, os.path.join(REPO, "examples", "hermes_desktop", "tool_server", "tool_server.py")],
                                    cwd=REPO, env=dict(os.environ, **server_env), stdout=lg, stderr=lg, start_new_session=True)
        TOOLS = "http://127.0.0.1:" + port
        CALLS_LOG = os.path.join(pd, "calls.jsonl")
        for _ in range(60):
            try:
                http(TOOLS + "/openapi.json", timeout=2)
                break
            except Exception:  # noqa: BLE001
                time.sleep(0.5)
        import atexit
        import signal
        atexit.register(lambda: os.kill(srv_proc.pid, signal.SIGTERM) if srv_proc.poll() is None else None)
    try:
        openapi = http(TOOLS + "/openapi.json", timeout=10)
        served = {op["operationId"] for p in openapi["paths"].values() for op in p.values() if isinstance(op, dict) and op.get("operationId")}
        try:
            token = webui_token()
        except Exception:  # noqa: BLE001
            token = None            # Open WebUI is only needed for the webui backend and for the installed prompts (direct mode)
        http(OLLAMA + "/api/tags", timeout=5)
    except Exception as e:  # noqa: BLE001
        print("services unreachable (%s): start them with bash examples/hermes_desktop/start.sh" % e, file=sys.stderr)
        return 2
    specs = tool_specs(openapi)
    system, template, src = live_prompts(token)
    try:
        digest = tool("memory_digest").get("digest", "")
    except Exception:  # noqa: BLE001
        digest = ""
    opts = {"temperature": 0, "seed": 42, "num_ctx": 8192, "num_predict": 1000}
    print("mode: %s | tools served: %d | prompts: %s | cases: %d" % ({"ollama": "direct (Ollama)", "hermes": "hermes agent CLI (-z)"}.get(backend, "chat (Open WebUI preset)"), len(served), src, len(cases)))
    ctx = {"token": token, "timeout": a.timeout, "system": system, "template": template, "specs": specs, "digest": digest, "lean": a.lean,
           "opts": opts, "served": served, "hermes_toolsets": a.hermes_toolsets, "hermes_model": a.hermes_model,
           "hermes_skills": a.hermes_skills, "hermes_ignore_user_config": a.hermes_isolated,
           "hermes_profile": a.hermes_profile, "hermes_accept_hooks": a.hermes_accept_hooks, "hermes_home": os.path.abspath(a.hermes_home) if a.hermes_home else "", "server_env": server_env}
    if backend == "hermes":
        if not shutil.which("hermes"):
            print("hermes CLI not found on PATH", file=sys.stderr)
            return 2
        if not (a.hermes_toolsets or a.hermes_profile) or not a.hermes_home:
            print("refusing: hermes -z auto-bypasses approvals: pass --hermes-home <isolated dir under build/> and --hermes-profile chip "
                  "(or --hermes-toolsets <repo MCP toolset>); never the real ~/.hermes", file=sys.stderr)
            return 2
        if os.path.abspath(a.hermes_home) == os.path.expanduser("~/.hermes"):
            print("refusing: ~/.hermes is not to be used by the eval", file=sys.stderr)
            return 2
        if a.dry_run:
            print(" ".join(hermes_cmd(ctx, cases[0]["prompt"])))
            return 0
    results, memory_made = [], False
    t_all = time.time()
    for c in cases:
        r = {"id": c["id"], "group": c["group"], "prompt": c["prompt"], "status": "scored"}
        missing = [t for t in c.get("needs_tools", []) if t not in served]
        if missing:
            r.update(status="pending", why="tool not served yet: " + ",".join(missing))
        elif chat and c.get("side_effect") == "gui" and not a.allow_gui:
            r.update(status="skipped", why="opens real windows (pass --allow-gui)")
        elif chat and c.get("side_effect") == "memory" and not a.allow_memory:
            r.update(status="skipped", why="writes a memory note (pass --allow-memory)")
        if r["status"] != "scored":
            results.append(r)
            print("%-4s %-13s %-8s %s" % (c["id"], c["group"], r["status"].upper(), r["why"]))
            continue
        t0 = time.time()
        answer, extra = "", {}
        try:
            called, answer, extra, jobs_started = BACKENDS[backend](ctx, c)
            dt = time.time() - t0
        except Exception as e:  # noqa: BLE001
            r.update(status="error", why=str(e)[:200], seconds=round(time.time() - t0, 1))
            results.append(r)
            print("%-4s %-13s ERROR    %s" % (c["id"], c["group"], e))
            continue
        s, names = score(c, called, answer, jobs_started, chat)
        if c.get("side_effect") == "memory":
            memory_made = True
        r.update(called=[{"tool": n, "args": ar} for n, ar in called], tools=names, score=s, passed=s["pass"], seconds=round(dt, 1),
                 answer=clean_answer(answer)[:700] if chat else None, **extra)
        results.append(r)
        print("%-4s %-13s %-4s %5.1fs  %-34s %s" % (c["id"], c["group"], "PASS" if s["pass"] else "FAIL", dt, ",".join(names) or "none",
                                                   "; ".join(s["why"])[:160]))
    if memory_made:                                         # delete the eval note(s)
        try:
            for n in tool("recall", {"query": "eval-case", "limit": 20}).get("notes", []):
                if "eval-case" in n.get("text", ""):
                    tool("forget", {"id": n["id"]})
        except Exception:  # noqa: BLE001
            pass
    return report(a, results, specs, system, template, src, full, time.time() - t_all, served)


def rescore(a):
    old = json.load(open(a.rescore, encoding="utf-8"))
    cases = {c["id"]: c for c in json.load(open(a.cases, encoding="utf-8"))["cases"]}
    results = []
    for r in old["results"]:
        c = cases.get(r["id"])
        if c is None:
            continue
        r = dict(r, group=c["group"], prompt=c["prompt"])
        if r["status"] == "scored":
            called = [(x["tool"], x["args"]) for x in r["called"]]
            s, names = score(c, called, r.get("answer") or "", 0 if r["score"]["nostart"] else 1, a.backend != "ollama" and a.backend != "" or not old["summary"]["backend"] == "ollama")
            r.update(score=s, passed=s["pass"], tools=names)
        results.append(r)
    class _A:
        pass
    a2 = _A()
    a2.__dict__.update(vars(a))
    a2.tag = (old["summary"].get("tag") or "") + "+rescored"
    a2.direct = old["summary"].get("backend") == "ollama"
    a2.backend = old["summary"].get("backend")
    a2.lean = False
    a2.no_summary = True
    served = set()
    return report(a2, results, [], "", "", "rescored from " + os.path.basename(a.rescore), False, 0, served)


def pct(n, d):
    return round(100.0 * n / d, 1) if d else None


def report(a, results, specs, system, template, src, full, total_s, served):
    scored = [r for r in results if r["status"] == "scored"]
    groups = {}
    for r in scored:
        g = groups.setdefault(r["group"], {"cases": 0, "passed": 0, "tool_ok": 0, "args_ok": 0, "forbid_ok": 0, "answer_ok": 0, "answer_n": 0})
        g["cases"] += 1
        g["passed"] += r["passed"]
        g["tool_ok"] += bool(r["score"]["tool"])
        g["args_ok"] += bool(r["score"]["args"])
        g["forbid_ok"] += bool(r["score"]["forbid"])
        if r["score"]["answer"] is not None:
            g["answer_n"] += 1
            g["answer_ok"] += bool(r["score"]["answer"])
    for g in groups.values():
        g["accuracy_pct"] = pct(g["passed"], g["cases"])
    n = len(scored)
    lat = sorted(r["seconds"] for r in scored)
    conf = {}
    cases = {c["id"]: c for c in json.load(open(a.cases, encoding="utf-8"))["cases"]}
    for r in scored:
        c = cases[r["id"]]
        exp = ("none" if c.get("no_tools") else (c.get("expect_tools") or c.get("expect_any") or ["?"])[0])
        got = r["tools"][0] if r["tools"] else "none"
        if exp != got and not r["score"]["tool"]:
            k = "%s -> %s" % (exp, ",".join(r["tools"]) or "none")
            conf[k] = conf.get(k, 0) + 1
    summary = {
        "tag": a.tag or ("direct" if a.direct else "chat"),
        "backend": a.backend or ("ollama" if a.direct else "webui"),
        "mode": ("direct (Ollama, tool-selection prompt only)" + (", lean" if a.lean else "")) if a.direct else ("hermes agent CLI one-shot (-z)" if a.backend == "hermes" else "chat (Open WebUI preset, legacy function calling)"),
        "model": BASE_MODEL, "options": {"temperature": 0, "seed": 42, "num_ctx": 8192},
        "date": time.strftime("%Y-%m-%d %H:%M"), "tools_served": len(served),
        "cases_total": len(results), "scored": n, "pending": sum(r["status"] == "pending" for r in results),
        "skipped": sum(r["status"] == "skipped" for r in results), "errors": sum(r["status"] == "error" for r in results),
        "passed": sum(r["passed"] for r in scored), "accuracy_pct": pct(sum(r["passed"] for r in scored), n),
        "tool_selection_pct": pct(sum(bool(r["score"]["tool"]) for r in scored), n),
        "args_pct": pct(sum(bool(r["score"]["args"]) for r in scored), n),
        "no_forbidden_pct": pct(sum(bool(r["score"]["forbid"]) for r in scored), n),
        "latency_s": {"median": round(statistics.median(lat), 1) if lat else None, "p90": lat[int(0.9 * (len(lat) - 1))] if lat else None, "total": round(total_s)},
        "groups": groups,
        "confusion_expected_to_got": dict(sorted(conf.items(), key=lambda kv: -kv[1])),
        "failed_ids": [r["id"] for r in scored if not r["passed"]],
        "pending_ids": [r["id"] for r in results if r["status"] == "pending"],
        "skipped_ids": [r["id"] for r in results if r["status"] == "skipped"],
        "inputs": {"tools_prompt_sha256": hashlib.sha256(template.encode()).hexdigest()[:12],
                   "system_prompt_sha256": hashlib.sha256(system.encode()).hexdigest()[:12], "prompts_from": src,
                   "tool_spec_chars": len(json.dumps(specs, ensure_ascii=False)), "tool_count": len(specs),
                   "tools_prompt_chars": len(template), "system_prompt_chars": len(system)},
    }
    ts = time.strftime("%Y%m%d_%H%M%S")
    out_dir = os.path.join(REPO, "build", "agent")
    os.makedirs(out_dir, exist_ok=True)
    out = os.path.join(out_dir, "tool_eval_%s.json" % ts)
    with open(out, "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "results": results}, f, indent=1)
    print("\n%-14s %5s %6s %6s %6s %6s" % ("group", "cases", "pass", "tool", "args", "answer"))
    for gname, g in groups.items():
        print("%-14s %5d %5.0f%% %5.0f%% %5.0f%% %6s" % (gname, g["cases"], g["accuracy_pct"], pct(g["tool_ok"], g["cases"]), pct(g["args_ok"], g["cases"]),
                                                       ("%.0f%%" % pct(g["answer_ok"], g["answer_n"])) if g["answer_n"] else "-"))
    print("OVERALL %d/%d = %s%% (tool %s%%, args %s%%, no-forbidden %s%%) pending %d skipped %d errors %d, median %ss, total %ss" % (
        summary["passed"], n, summary["accuracy_pct"], summary["tool_selection_pct"], summary["args_pct"], summary["no_forbidden_pct"],
        summary["pending"], summary["skipped"], summary["errors"], summary["latency_s"]["median"], summary["latency_s"]["total"]))
    if conf:
        print("confusion (expected -> got): " + "; ".join("%s x%d" % kv for kv in list(summary["confusion_expected_to_got"].items())[:12]))
    print("full results: " + os.path.relpath(out, REPO))
    if full and not a.no_summary and summary["errors"] == 0:
        sp = os.path.join(HERE, "results_summary_direct.json" if a.direct else ("results_summary_hermes.json" if a.backend == "hermes" else "results_summary.json"))
        slim = dict(summary)
        slim["per_case"] = {r["id"]: ("pass" if r["passed"] else "fail: " + "; ".join(r["score"]["why"])[:200]) for r in scored}
        with open(sp, "w", encoding="utf-8") as f:
            json.dump(slim, f, indent=1)
        print("summary: " + os.path.relpath(sp, REPO))
    return 0


if __name__ == "__main__":
    sys.exit(main())
