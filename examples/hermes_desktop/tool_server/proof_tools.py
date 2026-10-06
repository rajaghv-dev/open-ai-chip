#!/usr/bin/env python3
"""Proof tools for the Hermes tool server (a FastAPI APIRouter, auto-mounted by tool_server.py): evidence that the agent
runs locally, that its answers come from this repo, and exactly which context it was given.

Model-visible tools (POST /<name>): proof_local (listening sockets, ollama ps, offline env, outbound connections, verdict),
show_context (what the last turn sent: system prompt sha, tools prompt sha, memory digest, retrieved passages), call_log.
Hidden endpoints (not in the tool list): proof_turn (data for the receipt filter), proof_record (filters record what they injected).

CALL LOG: install(app) (called by tool_server.py) adds an ASGI middleware that records every tool call: time, chat id and
message id (X-OpenWebUI-Chat-Id / -Message-Id headers, sent because start.sh sets ENABLE_FORWARD_USER_INFO_HEADERS), tool, args,
result size, and every repo file the call opened for reading (builtins.open and io.open are wrapped, scoped by a contextvar
to the running call): repo-relative path, sha256, git blob id, tracked/clean against HEAD, plus the repo commit and dirty flag.
Kept in memory and appended to build/agent/proof/calls.jsonl (git-ignored; override with CHIP_PROOF_DIR). Nothing here
edits a repo file. Everything is read-only except that log directory.
A proof is a measurement of this moment on this machine (lsof, ps, git), not a guarantee about the future: docs/HERMES_DESKTOP.md.
Docs: examples/hermes_desktop/tool_server/README.md, docs/HERMES_DESKTOP.md (section "Proof: local, repo, context")
Tests: tests/tools/test_proof.py
"""
import builtins
import collections
import contextvars
import hashlib
import io
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.request
from typing import Any, Dict, List, Optional

from fastapi import APIRouter
from pydantic import BaseModel

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
HD = os.path.join(REPO, "examples", "hermes_desktop")
PROOF_DIR = os.environ.get("CHIP_PROOF_DIR") or os.path.join(REPO, "build", "agent", "proof")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
MASTER_PROMPT = os.path.join(REPO, "tools", "prompts", "master_prompt.txt")
TOOLS_PROMPT = os.path.join(HD, "tools_prompt.txt")
PRESET_JSON = os.path.join(HD, "preset.json")
MEM_MARK = "[agent memory digest]"
RECEIPT_MARK = "**Receipt**"

router = APIRouter()
_LOCK = threading.Lock()
CALLS: "collections.deque" = collections.deque(maxlen=1000)
CTX: Dict[str, dict] = {}                 # chat key -> context parts the filters recorded for the latest turn
LAST = {"key": None}
INTERNAL = {"memory_digest", "proof_turn", "proof_record", "call_log", "health", "job_status", "job_list", "record_run"}
SKIP_PATHS = {"proof_turn", "proof_record"}          # not logged at all (the receipt filter calls them on every turn)
MAX_HASH_BYTES = 5 * 1024 * 1024
MAX_FILES_PER_CALL = 60
NOISE_DIRS = ("build/webui/", "build/agent/proof/", "build/agent/venv/", "build/agent/traces/", ".git/", "node_modules/")
STACK_PORTS = {11434: "ollama", 8770: "tool server", 8080: "Open WebUI", 8766: "Magic bridge"}
STACK_CMD = re.compile(r"(?i)(open-webui|tool_server\.py|klayout|magic_bridge|Hermes Chip Agent)")
OFFLINE_ENV = ["OFFLINE_MODE", "HF_HUB_OFFLINE", "ANONYMIZED_TELEMETRY", "DO_NOT_TRACK", "SCARF_NO_ANALYTICS",
               "ENABLE_VERSION_UPDATE_CHECK", "ENABLE_COMMUNITY_SHARING", "ENABLE_OPENAI_API", "OLLAMA_BASE_URL", "HOST",
               "ENABLE_PERSISTENT_CONFIG", "ENABLE_WEB_SEARCH", "ENABLE_FORWARD_USER_INFO_HEADERS"]
OFFLINE_EXPECT = {"OFFLINE_MODE": "true", "HF_HUB_OFFLINE": "1", "ANONYMIZED_TELEMETRY": "false", "DO_NOT_TRACK": "true",
                  "SCARF_NO_ANALYTICS": "true", "ENABLE_VERSION_UPDATE_CHECK": "false", "ENABLE_COMMUNITY_SHARING": "false",
                  "ENABLE_OPENAI_API": "false"}
LOOPBACK = ("127.", "[::1]", "::1", "localhost")


# ---------------------------------------------------------------- git and hashing
_GIT = {"t": 0.0, "info": None, "tree": {}, "tree_for": None}


def _git(*args, timeout=8) -> str:
    try:
        r = subprocess.run(["git", "-C", REPO] + list(args), capture_output=True, text=True, timeout=timeout)
        return r.stdout if r.returncode == 0 else ""
    except Exception:  # noqa: BLE001
        return ""


def git_info(max_age: float = 20.0) -> dict:
    """{commit, short, dirty, dirty_count}; cached for max_age seconds. commit is None outside a git checkout."""
    now = time.time()
    if _GIT["info"] is not None and now - _GIT["t"] < max_age:
        return _GIT["info"]
    commit = _git("rev-parse", "HEAD").strip() or None
    st = _git("status", "--porcelain", timeout=15) if commit else ""
    n = len([l for l in st.splitlines() if l.strip()])
    info = {"commit": commit, "short": commit[:7] if commit else None, "dirty": n > 0, "dirty_count": n}
    _GIT.update(t=now, info=info)
    return info


def _head_tree() -> Dict[str, str]:
    """path -> blob id at HEAD (git ls-tree -r), cached per commit."""
    c = git_info()["commit"]
    if c and _GIT["tree_for"] != c:
        tree = {}
        for line in _git("ls-tree", "-r", c, timeout=20).splitlines():
            meta, _, path = line.partition("\t")
            parts = meta.split()
            if len(parts) == 3:
                tree[path] = parts[2]
        _GIT.update(tree=tree, tree_for=c)
    return _GIT["tree"] if c else {}


def git_blob_id(data: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(data) + data).hexdigest()


def file_provenance(path: str) -> Optional[dict]:
    """{path (repo-relative), sha256, bytes, kind, git:{tracked, blob, clean}} for a file under the repo, else None."""
    try:
        ap = os.path.realpath(path)
        rp = os.path.relpath(ap, os.path.realpath(REPO))
        if rp.startswith("..") or not os.path.isfile(ap):
            return None
        rp = rp.replace(os.sep, "/")
        if any(rp.startswith(d) or ("/" + d) in "/" + rp for d in NOISE_DIRS) or rp.endswith((".pyc", ".sock")) or "__pycache__" in rp:
            return None
        size = os.path.getsize(ap)
        if size > MAX_HASH_BYTES:
            return {"path": rp, "sha256": None, "bytes": size, "kind": "local", "git": {"tracked": False}, "note": "too large to hash"}
        data = open(ap, "rb").read()
        blob = git_blob_id(data)
        head = _head_tree().get(rp)
        tracked = head is not None
        kind = "repo" if tracked else ("memory" if rp.startswith("build/agent/memory/") else "local")
        return {"path": rp, "sha256": hashlib.sha256(data).hexdigest(), "bytes": size, "kind": kind,
                "git": {"tracked": tracked, "blob": blob, "clean": (blob == head) if tracked else None}}
    except Exception:  # noqa: BLE001
        return None


# ---------------------------------------------------------------- read tracking (open wrapper)
_CUR: "contextvars.ContextVar[Optional[dict]]" = contextvars.ContextVar("chip_proof_call", default=None)
_REAL_OPEN = builtins.open
_INSTALLED = {"open": False, "mw": False}


def _tracking_open(file, mode="r", *a, **kw):
    f = _REAL_OPEN(file, mode, *a, **kw)
    try:
        rec = _CUR.get()
        if rec is not None and isinstance(file, (str, os.PathLike)) and isinstance(mode, str) and not any(c in mode for c in "wax+"):
            rec["_paths"].add(os.fspath(file))
    except Exception:  # noqa: BLE001
        pass
    return f


def install_open_tracking() -> None:
    """Wrap builtins.open and io.open once; the wrapper only records while a call is being served."""
    if not _INSTALLED["open"]:
        builtins.open = _tracking_open
        io.open = _tracking_open
        _INSTALLED["open"] = True


def uninstall_open_tracking() -> None:
    if _INSTALLED["open"]:
        builtins.open = _REAL_OPEN
        io.open = _REAL_OPEN
        _INSTALLED["open"] = False


# ---------------------------------------------------------------- citations from results
def _lines_span(s) -> Optional[str]:
    """'98-112,113-116' -> '98-116'; '224' -> '224'."""
    nums = [int(x) for x in re.findall(r"\d+", str(s))]
    if not nums:
        return None
    lo, hi = min(nums), max(nums)
    return str(lo) if lo == hi else "%d-%d" % (lo, hi)


def extract_citations(obj, out: Optional[list] = None, depth: int = 0) -> list:
    """Walk a tool result for {file|source|path: <repo path>, lines: ...} objects -> [{path, lines}]."""
    out = [] if out is None else out
    if depth > 4 or len(out) >= 20:
        return out
    if isinstance(obj, dict):
        p = next((obj[k] for k in ("file", "source", "path") if isinstance(obj.get(k), str)), None)
        if p and "lines" in obj and ("/" in p or "." in p) and not p.startswith(("http", "tool")):
            span = _lines_span(obj["lines"])
            if span and {"path": p, "lines": span} not in out:
                out.append({"path": p, "lines": span})
        for v in obj.values():
            if isinstance(v, (dict, list)):
                extract_citations(v, out, depth + 1)
    elif isinstance(obj, list):
        for v in obj[:50]:
            extract_citations(v, out, depth + 1)
    return out


def excerpt(path: str, lines: Optional[str], max_chars: int = 700) -> str:
    """The cited lines of a repo file (at most max_chars), '' when unreadable."""
    try:
        ap = os.path.join(REPO, path)
        if not lines or not os.path.realpath(ap).startswith(os.path.realpath(REPO)):
            return ""
        lo, _, hi = lines.partition("-")
        lo = int(lo)
        hi = int(hi or lo)
        with _REAL_OPEN(ap, encoding="utf-8", errors="replace") as f:
            txt = "".join(l for i, l in enumerate(f, 1) if lo <= i <= min(hi, lo + 40))
        return txt[:max_chars]
    except Exception:  # noqa: BLE001
        return ""


# ---------------------------------------------------------------- the call log
def _cap(s: str, n: int) -> str:
    return s if len(s) <= n else s[:n] + "..."


def record_call(tool: str, args: str, status: int, size: int, ms: int, paths: set, result: Any, chat_id: Optional[str],
                message_id: Optional[str], ts: Optional[float] = None) -> dict:
    """Append one call to the log (memory + calls.jsonl). Returns the record. Never raises."""
    gi = git_info()
    files, seen, hashed = [], set(), 0
    for p in sorted(paths):
        if len(files) >= MAX_FILES_PER_CALL or hashed > 20 * MAX_HASH_BYTES:
            break
        fp = file_provenance(p)
        if fp and fp["path"] not in seen:
            seen.add(fp["path"])
            files.append(fp)
            hashed += fp["bytes"]
    cites = extract_citations(result) if result is not None else []
    for c in cites:
        c["commit"] = gi["short"]
        if c["path"] not in seen and len(files) < MAX_FILES_PER_CALL:        # a retrieval index served it: hash the cited file now
            fp = file_provenance(os.path.join(REPO, c["path"]))
            if fp:
                seen.add(fp["path"])
                files.append(dict(fp, via="cited"))
    t = time.time() if ts is None else ts
    rec = {"ts": round(t, 3), "time": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(t)), "chat_id": chat_id,
           "message_id": message_id, "tool": tool, "args": _cap(args, 400), "status": status, "result_bytes": size,
           "ms": ms, "internal": tool in INTERNAL, "files": files, "citations": cites,
           "commit": gi["commit"], "dirty": gi["dirty"]}
    with _LOCK:
        CALLS.append(rec)
        if not rec["internal"]:
            LAST["key"] = chat_id or LAST["key"] or "session"
        try:
            os.makedirs(PROOF_DIR, exist_ok=True)
            with _REAL_OPEN(os.path.join(PROOF_DIR, "calls.jsonl"), "a", encoding="utf-8") as f:
                f.write(json.dumps(rec) + "\n")
        except OSError:
            pass
    return rec


class ProofMiddleware:
    """Pure ASGI middleware: records POST /<tool> calls with the files they read. Sync endpoints run in a thread that
    inherits the contextvar, so the open() wrapper adds paths to this call's record."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") != "POST":
            return await self.app(scope, receive, send)
        tool = scope["path"].strip("/")
        if tool in SKIP_PATHS or "/" in tool:
            return await self.app(scope, receive, send)
        hdr = {k.decode("latin1").lower(): v.decode("latin1") for k, v in scope.get("headers", [])}
        state = {"_paths": set(), "body": b"", "out": b"", "status": 0, "t0": time.time()}
        tok = _CUR.set(state)

        async def _recv():
            m = await receive()
            if m.get("type") == "http.request" and len(state["body"]) < 4000:
                state["body"] += m.get("body", b"")
            return m

        async def _send(m):
            if m["type"] == "http.response.start":
                state["status"] = m["status"]
            elif m["type"] == "http.response.body":
                state["n"] = state.get("n", 0) + len(m.get("body", b""))
                if len(state["out"]) < 1_000_000:
                    state["out"] += m.get("body", b"")
            await send(m)

        try:
            await self.app(scope, _recv, _send)
        finally:
            _CUR.reset(tok)
            try:
                result = None
                if state["out"] and state.get("n", 0) <= 1_000_000:
                    try:
                        result = json.loads(state["out"])
                    except ValueError:
                        result = None
                record_call(tool, state["body"].decode("utf-8", "replace"), state["status"], state.get("n", 0),
                            int((time.time() - state["t0"]) * 1000), state["_paths"], result,
                            hdr.get("x-openwebui-chat-id"), hdr.get("x-openwebui-message-id"))
            except Exception:  # noqa: BLE001
                pass


def install(app) -> None:
    """Called by tool_server._mount_extensions: open tracking plus the middleware (must run before the app starts)."""
    install_open_tracking()
    if not _INSTALLED["mw"]:
        app.add_middleware(ProofMiddleware)
        _INSTALLED["mw"] = True


def calls(chat_id: Optional[str] = None, since_ts: Optional[float] = None, message_id: Optional[str] = None,
          include_internal: bool = False, limit: int = 50) -> List[dict]:
    """Logged calls, oldest first, filtered. message_id wins when any call carries it; else chat id and time window."""
    with _LOCK:
        rows = list(CALLS)
    if not include_internal:
        rows = [r for r in rows if not r["internal"]]
    if message_id and any(r.get("message_id") == message_id for r in rows):
        return [r for r in rows if r.get("message_id") == message_id][-limit:]
    if chat_id and any(r.get("chat_id") == chat_id for r in rows):
        rows = [r for r in rows if r.get("chat_id") == chat_id]
    if since_ts is not None:
        rows = [r for r in rows if r["ts"] >= since_ts - 2]
    return rows[-limit:]


# ---------------------------------------------------------------- context records (filters call proof_record)
def record_context(chat_id: Optional[str], part: str, data: dict) -> None:
    key = chat_id or "session"
    if part == "system" and "text" in data:     # the filter sends the preset system text; keep only its measurements
        t = data["text"]
        master = _read(MASTER_PROMPT).strip()
        data = {"sha256": _sha(t), "chars": len(t), "tokens_est": _tokens(t), "head": t.splitlines()[:6],
                "starts_with_master_prompt": bool(master) and t.strip().startswith(master)}
    with _LOCK:
        c = CTX.setdefault(key, {})
        c[part] = dict(data, ts=round(time.time(), 3))
        LAST["key"] = key
        while len(CTX) > 50:
            CTX.pop(next(iter(CTX)))


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _file_sha(path: str) -> Optional[str]:
    try:
        return hashlib.sha256(_REAL_OPEN(path, "rb").read()).hexdigest()
    except OSError:
        return None


def _tokens(text: str) -> int:
    return (len(text) + 3) // 4              # estimate: 4 chars per token (same rule as make_master_prompt.py)


def context_for(chat_id: Optional[str]) -> dict:
    """What the last turn was sent: preset system prompt, tools prompt, memory digest, retrieved passages."""
    key = chat_id or LAST["key"] or "session"
    with _LOCK:
        c = dict(CTX.get(key) or {})
    t_ts = (c.get("turn") or {}).get("ts")
    if t_ts:                                   # parts recorded long before this turn belong to an older turn
        c = {k: v for k, v in c.items() if v.get("ts", 0) >= t_ts - 60}
    master = _read(MASTER_PROMPT)
    out = {"chat_id": key if key != "session" else None, "recorded": bool(c)}
    sysm = c.get("system")
    if sysm:
        out["system"] = {k: sysm.get(k) for k in ("sha256", "chars", "tokens_est", "head", "starts_with_master_prompt")}
    else:
        out["system"] = {"sha256": _sha(master) if master else None, "chars": len(master), "tokens_est": _tokens(master),
                         "head": master.splitlines()[:6], "starts_with_master_prompt": True,
                         "note": "no turn recorded yet: this is the master prompt file the preset starts with"}
    out["master_prompt"] = {"file": "tools/prompts/master_prompt.txt", "sha256": _sha(master) if master else None,
                            "chars": len(master), "tokens_est": _tokens(master)}
    tp = _read(TOOLS_PROMPT)
    out["tools_prompt"] = {"file": "examples/hermes_desktop/tools_prompt.txt", "sha256": _sha(tp) if tp else None, "chars": len(tp)}
    mem = c.get("memory")
    out["memory"] = mem if mem else _memory_fallback()
    t = c.get("turn") or {}
    out["user_message"] = t.get("user_head")
    cs = calls(chat_id=None if key == "session" else key, since_ts=t.get("since_ts"))
    out["tools_called"] = [{"tool": r["tool"], "files": [f["path"] for f in used_files(r)], "opened": len(r["files"]), "ms": r["ms"]} for r in cs]
    out["retrieved"] = [dict(x, tool=r["tool"]) for r in cs for x in r["citations"]]
    return out


def _read(path: str) -> str:
    try:
        return _REAL_OPEN(path, encoding="utf-8").read()
    except OSError:
        return ""


def _memory_fallback() -> dict:
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("chip_proof_sm", os.path.join(HERE, "skills_memory_tools.py"))
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        return memory_summary(m.digest(), recorded=False)
    except Exception:  # noqa: BLE001
        return {"recorded": False, "entries": 0}


def memory_summary(digest: str, recorded: bool = True) -> dict:
    """Counts and a hash of a memory digest text: notes before 'LAST RUNS:', runs after."""
    head, _, runs = digest.partition("LAST RUNS:")
    notes = [l for l in head.splitlines() if l.startswith("- ")]
    run_l = [l for l in runs.splitlines() if l.startswith("- ")]
    return {"recorded": recorded, "notes": len(notes), "runs": len(run_l), "entries": len(notes) + len(run_l),
            "sha256": _sha(digest) if digest else None, "chars": len(digest), "head": digest.splitlines()[:8]}


# ---------------------------------------------------------------- model and endpoint
def _http_json(url: str, body: Optional[dict] = None, timeout: float = 3.0):
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"null")


def model_info(model_id: Optional[str] = None) -> dict:
    """{id, base, digest, size, endpoint}: the preset id resolves to its base model (preset.json); digest from Ollama /api/tags."""
    base = model_id or "hermes3:8b"
    try:
        p = json.loads(_read(PRESET_JSON) or "{}")
        if base in (p.get("id"), None):
            base = p.get("base_model_id", base)
    except ValueError:
        pass
    out = {"id": model_id, "base": base, "endpoint": OLLAMA_URL.replace("http://", ""), "digest": None, "size": None}
    try:
        for m in _http_json(OLLAMA_URL + "/api/tags").get("models", []):
            if m.get("name") == base or m.get("model") == base:
                out.update(digest=(m.get("digest") or "")[:12], size=m.get("size"))
                break
    except Exception:  # noqa: BLE001
        out["error"] = "ollama not reachable"
    return out


# ---------------------------------------------------------------- proof_local parsers and probes
def parse_lsof_listen(text: str) -> List[dict]:
    """lsof -nP -iTCP -sTCP:LISTEN -> [{command, pid, bind, port}] (bind is the address part of NAME)."""
    out = []
    for line in text.splitlines()[1:]:
        m = re.match(r"^(\S+)\s+(\d+)\s+\S+\s+.*?\sTCP\s+(\S+):(\d+)\s+\(LISTEN\)", line)
        if m:
            out.append({"command": m.group(1).replace("\\x20", " "), "pid": int(m.group(2)), "bind": m.group(3), "port": int(m.group(4))})
    return out


def parse_lsof_established(text: str) -> List[dict]:
    """lsof -nP -iTCP -sTCP:ESTABLISHED -> [{command, pid, local, remote}]."""
    out = []
    for line in text.splitlines()[1:]:
        m = re.match(r"^(\S+)\s+(\d+)\s+\S+\s+.*?\sTCP\s+(\S+)->(\S+)\s+\(ESTABLISHED\)", line)
        if m:
            out.append({"command": m.group(1).replace("\\x20", " "), "pid": int(m.group(2)), "local": m.group(3), "remote": m.group(4)})
    return out


def is_loopback(addr: str) -> bool:
    a = addr.strip("[]")
    return addr.startswith(LOOPBACK) or a.startswith("127.") or a in ("::1", "localhost")


def non_loopback_remote(conn: dict) -> bool:
    host = conn["remote"].rsplit(":", 1)[0]
    return not is_loopback(host)


def parse_ollama_ps(text: str) -> List[dict]:
    """`ollama ps` table -> [{name, id, size, processor, context, until}]."""
    lines = [l for l in text.splitlines() if l.strip()]
    rows = []
    for l in lines[1:]:
        parts = re.split(r"\s{2,}", l.strip())
        if len(parts) >= 4:
            rows.append({"name": parts[0], "id": parts[1], "size": parts[2], "processor": parts[3],
                         "context": parts[4] if len(parts) > 4 else None, "until": parts[5] if len(parts) > 5 else None})
    return rows


def parse_ps_env(text: str, keys=None) -> Dict[str, str]:
    """`ps eww -p <pid>` output -> {KEY: value} for the wanted keys (values with no spaces)."""
    out = {}
    for k in (keys or OFFLINE_ENV):
        m = re.search(r"(?:^|\s)%s=(\S*)" % re.escape(k), text)
        if m:
            out[k] = m.group(1)
    return out


def env_checks(env: Dict[str, str]) -> List[dict]:
    rows = []
    for k in OFFLINE_ENV:
        v = env.get(k)
        exp = OFFLINE_EXPECT.get(k)
        ok = None if exp is None else (v is not None and v.lower() == exp)
        rows.append({"key": k, "value": v, "expected": exp, "ok": ok})
    return rows


def _run(cmd: List[str], timeout: int = 10) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except Exception:  # noqa: BLE001
        return ""


def stack_pids(listeners: List[dict]) -> Dict[int, str]:
    """pid -> role for the stack: listeners on the known ports, ollama's runner children, klayout/magic processes."""
    roles = {}
    for l in listeners:
        if l["port"] in STACK_PORTS:
            roles[l["pid"]] = STACK_PORTS[l["port"]]
    ps = _run(["ps", "-axo", "pid=,ppid=,command="])
    parents = {}
    for line in ps.splitlines():
        m = re.match(r"\s*(\d+)\s+(\d+)\s+(.*)", line)
        if not m:
            continue
        pid, ppid, cmd = int(m.group(1)), int(m.group(2)), m.group(3)
        parents[pid] = ppid
        if STACK_CMD.search(cmd) and "grep" not in cmd and pid != os.getpid() or (pid == os.getpid()):
            roles.setdefault(pid, "tool server" if pid == os.getpid() else re.sub(r".*/", "", cmd.split()[0])[:24])
    for pid, ppid in parents.items():            # the llama runner is a child of the ollama server
        if ppid in roles and roles[ppid] == "ollama":
            roles.setdefault(pid, "ollama runner")
    return roles


def local_report(fixtures: Optional[dict] = None) -> dict:
    """Measure the local-only claims now. `fixtures` (tests) replaces the system probes with text."""
    fx = fixtures or {}
    gi = git_info()
    lis_txt = fx.get("lsof_listen") if "lsof_listen" in fx else _run(["lsof", "-nP", "-iTCP", "-sTCP:LISTEN"], 15)
    est_txt = fx.get("lsof_established") if "lsof_established" in fx else _run(["lsof", "-nP", "-iTCP", "-sTCP:ESTABLISHED"], 15)
    ps_txt = fx.get("ollama_ps") if "ollama_ps" in fx else _run(["ollama", "ps"])
    listeners = parse_lsof_listen(lis_txt)
    roles = fx.get("roles") if "roles" in fx else stack_pids(listeners)
    mine = [dict(l, role=roles.get(l["pid"]) or STACK_PORTS.get(l["port"])) for l in listeners
            if l["pid"] in roles or l["port"] in STACK_PORTS]
    for l in mine:
        l["loopback"] = is_loopback(l["bind"])
    out_conns = [c for c in parse_lsof_established(est_txt) if c["pid"] in roles and non_loopback_remote(c)]
    webui_pid = next((l["pid"] for l in mine if l["port"] == 8080), None)
    if "ps_env" in fx:
        env = parse_ps_env(fx["ps_env"])
    else:
        env = parse_ps_env(_run(["ps", "eww", "-p", str(webui_pid)]) if webui_pid else "")
    checks = env_checks(env)
    env_ok = bool(env) and all(c["ok"] for c in checks if c["ok"] is not None)
    audit = fx.get("audit") if "audit" in fx else audit_summary()
    models = parse_ollama_ps(ps_txt)
    bad_bind = [l for l in mine if not l["loopback"]]
    missing = [p for p in (11434, 8770, 8080) if not any(l["port"] == p for l in mine)]
    problems = []
    if bad_bind:
        problems.append("%d listener(s) not on loopback" % len(bad_bind))
    if out_conns:
        problems.append("%d outbound connection(s) to non-loopback hosts" % len(out_conns))
    if not env_ok:
        problems.append("offline env not confirmed in the running Open WebUI")
    if missing:
        problems.append("not listening: %s" % ",".join(str(p) for p in missing))
    verdict = ("LOCAL: %d stack listeners, all on loopback; %d outbound non-loopback connections; offline env confirmed; %s"
               % (len(mine), len(out_conns), ("model %s on %s" % (models[0]["name"], models[0]["processor"])) if models else "no model loaded now")
               if not problems else "CHECK: " + "; ".join(problems))
    rep = {"verdict": verdict, "local": not problems, "listeners": mine, "ollama_ps": models, "env": checks, "audit": audit,
           "outbound_non_loopback": out_conns, "stack_pids": {str(k): v for k, v in roles.items()},
           "repo": {"commit": gi["commit"], "short": gi["short"], "dirty": gi["dirty"]},
           "checked_at": time.strftime("%Y-%m-%d %H:%M:%S"),
           "caveat": "A measurement of this moment (lsof, ps, ollama ps), TCP only. Turn Wi-Fi off and rerun to see it work with no network."}
    rep["status_line"] = status_line(rep)
    rep["text"] = render_local(rep)
    return rep


def audit_summary() -> dict:
    """Run audit_config's checks against the running Open WebUI: {checks, drift} or {error}."""
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("chip_proof_audit", os.path.join(HD, "audit_config.py"))
        a = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(a)
        tok = a.api("/api/v1/auths/signin", body={"email": "admin@localhost", "password": "x"})["token"]
        flat = a.flatten(a.api("/api/v1/configs/export", tok))
        models = a.api("/api/models", tok)["data"]
        prompts = [p["command"] for p in a.api("/api/v1/prompts/", tok) or []]
        fl = {}
        for fid in (a.FILTER_ID, a.RECEIPT_ID):
            try:
                fl[fid] = a.api("/api/v1/functions/id/" + fid, tok) or {}
            except OSError:
                fl[fid] = {}
        rows = a.compare(flat, models, flat.get("tool_server.connections", []), prompts, fl[a.FILTER_ID], fl[a.RECEIPT_ID])
        return {"checks": len(rows), "drift": len([r for r in rows if not r[3]])}
    except Exception as e:  # noqa: BLE001
        return {"error": type(e).__name__}


def status_line(rep: dict) -> str:
    """'Local - hermes3:8b - repo 811c87d (dirty) - offline' for the desktop window."""
    m = rep["ollama_ps"][0]["name"] if rep.get("ollama_ps") else "hermes3:8b"
    r = rep["repo"]["short"] or "no-git"
    if rep["repo"]["dirty"]:
        r += "+dirty"
    net = "offline" if rep["local"] and not rep["outbound_non_loopback"] else "CHECK network"
    return "%s - %s - repo %s - %s" % ("Local" if rep["local"] else "NOT VERIFIED", m, r, net)


def render_local(rep: dict) -> str:
    L = ["VERDICT: " + rep["verdict"], "", "Listening sockets of the stack (expect 127.0.0.1):"]
    for l in rep["listeners"]:
        L.append("  %-14s pid %-6s %s:%s  %s" % (l.get("role") or l["command"], l["pid"], l["bind"], l["port"], "ok" if l["loopback"] else "NOT LOOPBACK"))
    L.append("Ollama (ollama ps):")
    for m in rep["ollama_ps"] or [{"name": "(no model loaded right now)", "size": "", "processor": "", "context": ""}]:
        L.append("  %s  %s  %s  ctx %s" % (m["name"], m["size"], m["processor"], m.get("context")))
    L.append("Open WebUI environment (from the running process):")
    for c in rep["env"]:
        if c["value"] is not None:
            L.append("  %s=%s%s" % (c["key"], c["value"], "" if c["ok"] in (None, True) else "  (expected %s)" % c["expected"]))
    a = rep.get("audit") or {}
    L.append("Config audit: %s" % ("%d checks, %d drift" % (a["checks"], a["drift"]) if "checks" in a else "unavailable (%s)" % a.get("error")))
    L.append("Outbound connections now (TCP, non-loopback, stack processes): %s" %
             ("none" if not rep["outbound_non_loopback"] else ", ".join("%s pid %s -> %s" % (c["command"], c["pid"], c["remote"]) for c in rep["outbound_non_loopback"])))
    L.append("Repo: %s%s" % (rep["repo"]["commit"], " (dirty)" if rep["repo"]["dirty"] else ""))
    L.append("Checked " + rep["checked_at"] + ". " + rep["caveat"])
    return "\n".join(L)


def render_context(c: dict) -> str:
    s, mem, tp, mp = c["system"], c["memory"], c["tools_prompt"], c["master_prompt"]
    L = ["Context of the last turn" + ("" if c.get("recorded") else " (nothing recorded yet: showing the standing context)"),
         "User message: %s" % (c.get("user_message") or "-"),
         "1. Preset system prompt: %s chars, ~%s tokens (estimate), sha256 %s%s" % (
             s.get("chars"), s.get("tokens_est"), (s.get("sha256") or "-")[:16],
             ", starts with tools/prompts/master_prompt.txt" if s.get("starts_with_master_prompt") else ""),
         "   first lines: " + " | ".join(l[:90] for l in (s.get("head") or [])[:3]),
         "   master prompt file sha256 %s" % (mp.get("sha256") or "-")[:16],
         "2. Tools / function-calling prompt: examples/hermes_desktop/tools_prompt.txt sha256 %s, %s chars" % ((tp.get("sha256") or "-")[:16], tp.get("chars")),
         "3. Memory digest injected by the memory filter: %s notes + %s runs, sha256 %s%s" % (
             mem.get("notes"), mem.get("runs"), (mem.get("sha256") or "-")[:16], "" if mem.get("recorded") else " (not yet recorded by the filter)")]
    L += ["   " + l[:120] for l in (mem.get("head") or [])[:5]]
    L.append("4. Retrieved passages (file:lines @ repo commit): %s" % ("none" if not c["retrieved"] else ""))
    for r in c["retrieved"]:
        L.append("   %s:%s  (%s)" % (r["path"], r["lines"], r["tool"]))
    L.append("5. Tools called this turn: %s" % ("none" if not c["tools_called"] else ", ".join(t["tool"] for t in c["tools_called"])))
    for t in c["tools_called"]:
        for f in t["files"][:5]:
            L.append("   %s read %s" % (t["tool"], f))
    return "\n".join(L)


# ---------------------------------------------------------------- turn data for the receipt filter
def used_files(r: dict) -> List[dict]:
    """The files a call's answer rests on: the cited ones when the result carries file:line citations (a retrieval tool opens
    the whole corpus, but only the cited passages were returned), otherwise every repo or memory file it opened."""
    cited = {c["path"] for c in r["citations"]}
    fs = [f for f in r["files"] if f["path"] in cited] if cited else r["files"]
    return [f for f in fs if f["kind"] in ("repo", "memory")] if not cited else fs


def sources_for(cs: List[dict], short: Optional[str]) -> List[dict]:
    """Open WebUI 'source' dicts (source/document/metadata) for the files and line ranges the turn's calls used."""
    out, seen = [], set()
    for r in cs:
        for c in r["citations"]:
            k = (c["path"], c["lines"])
            if k in seen:
                continue
            seen.add(k)
            fp = next((f for f in r["files"] if f["path"] == c["path"]), None) or file_provenance(os.path.join(REPO, c["path"])) or {}
            out.append({"source": {"name": "%s:%s @ %s" % (c["path"], c["lines"], short or "no-git")},
                        "document": [excerpt(c["path"], c["lines"]) or "(excerpt unavailable)"],
                        "metadata": [{"source": c["path"], "name": "%s:%s" % (c["path"], c["lines"]), "lines": c["lines"],
                                      "commit": short, "sha256": fp.get("sha256"), "tool": r["tool"]}]})
        for f in used_files(r):
            if f["kind"] != "repo" or (f["path"], None) in seen or any(f["path"] == p for p, _ in seen):
                continue
            seen.add((f["path"], None))
            out.append({"source": {"name": "%s @ %s" % (f["path"], short or "no-git")},
                        "document": ["sha256 %s, git blob %s%s" % (f["sha256"], (f["git"].get("blob") or "")[:12],
                                                                   "" if f["git"].get("clean") else " (modified since HEAD)")],
                        "metadata": [{"source": f["path"], "name": f["path"], "commit": short, "sha256": f["sha256"], "tool": r["tool"]}]})
    return out[:12]


def turn_data(chat_id=None, message_id=None, since_ts=None, model=None) -> dict:
    gi = git_info()
    ctx = context_for(chat_id)
    with _LOCK:
        t0 = ((CTX.get(chat_id or LAST["key"] or "session") or {}).get("turn") or {}).get("since_ts")
    cs = calls(chat_id=chat_id, since_ts=since_ts if since_ts is not None else t0, message_id=message_id)
    return {"model": model_info(model), "repo": {"commit": gi["commit"], "short": gi["short"], "dirty": gi["dirty"], "dirty_count": gi["dirty_count"]},
            "calls": [{"tool": r["tool"], "ms": r["ms"], "result_bytes": r["result_bytes"],
                       "opened": len(r["files"]),
                       "files": [{"path": f["path"], "sha256": f["sha256"], "kind": f["kind"], "clean": f["git"].get("clean")} for f in used_files(r)],
                       "citations": r["citations"]} for r in cs],
            "context": ctx, "sources": sources_for(cs, gi["short"])}


# ---------------------------------------------------------------- endpoints
class Nothing(BaseModel):
    pass


class ShowReq(BaseModel):
    chat_id: Optional[str] = None


class LogReq(BaseModel):
    limit: int = 10


class TurnReq(BaseModel):
    chat_id: Optional[str] = None
    message_id: Optional[str] = None
    since_ts: Optional[float] = None
    model: Optional[str] = None


class RecordReq(BaseModel):
    chat_id: Optional[str] = None
    part: str
    data: dict = {}


def post(name: str, summary: str, show: bool = True):
    return router.post("/" + name, operation_id=name, summary=summary, response_model=None, include_in_schema=show)


@post("proof_local", "Prove the stack runs locally: sockets, ollama ps, offline env, outbound connections, verdict")
def proof_local(req: Nothing = Nothing()) -> dict:
    """Measure now: listening sockets of Ollama, the tool server, Open WebUI and the GUI bridges with their bind addresses
    (expect 127.0.0.1), `ollama ps` (model, size, GPU or CPU), the offline and telemetry environment of the running Open WebUI,
    its config audit, outbound non-loopback TCP connections of these processes (expect none), the repo commit, and a verdict line.
    Use when the user asks whether this runs locally, offline, or sends data anywhere. Quote the VERDICT line."""
    r = local_report()
    return {"verdict": r["verdict"], "status_line": r["status_line"], "text": r["text"], "details": {k: r[k] for k in ("listeners", "ollama_ps", "outbound_non_loopback", "audit", "repo")}}


@post("show_context", "Show exactly what the last turn sent: system prompt, tools prompt, memory digest, retrieved passages")
def show_context(req: ShowReq = ShowReq()) -> dict:
    """What the model was given for the last conversation turn: the preset system prompt (first lines, length, sha256), the
    tools prompt sha256, the memory digest the memory filter injected (counts, sha256), and the retrieved passages (repo
    file:lines) and files read by the turn's tool calls. Use when the user asks what context or sources the answer used."""
    c = context_for(req.chat_id)
    return {"text": render_context(c), "context": c}


@post("call_log", "The last tool calls with the repo files they read (path, sha256, git blob)")
def call_log(req: LogReq = LogReq()) -> dict:
    """Recent tool calls: time, tool, args, result size, and for each repo file read its path, sha256 and git status."""
    rows = calls(include_internal=False, limit=max(1, min(req.limit, 50)))
    return {"commit": git_info()["commit"], "calls": rows, "log_file": "build/agent/proof/calls.jsonl"}


@post("proof_turn", "Internal: receipt data for one turn (used by the receipt filter)", show=False)
def proof_turn(req: TurnReq) -> dict:
    return turn_data(req.chat_id, req.message_id, req.since_ts, req.model)


@post("proof_record", "Internal: a filter records what it injected into a turn", show=False)
def proof_record(req: RecordReq) -> dict:
    record_context(req.chat_id, req.part, req.data)
    return {"ok": True}
