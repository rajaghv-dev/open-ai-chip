#!/usr/bin/env python3
"""Argument normalisation and the one-step confirm tool for the Hermes tool server (auto-mounted by tool_server.py).

A small local model fills arguments badly: placeholder values ("-", "none", "string"), a design written "kv8" or "KV_ATTN_N8 ",
a confirm id echoed as ten characters, a whole sentence ("yes, run 50ad87") in confirm_id. This module repairs those in code,
before the request reaches the tool, and answers an unknown value with the list of valid values instead of a bare failure.
  install(app)    pure ASGI middleware on every POST /<tool> with a JSON object body:
                  - string values that are placeholders (PLACEHOLDERS) or blank are removed (the tool's own default applies);
                  - `design` / `designs`: exact, case, '-' for '_', alias (kv8, kv_n8, prec fp16 ...), unique prefix, unique
                    substring, then a clear difflib winner are repaired; no match -> HTTP 200 {"error", "valid_designs",
                    "closest"} and the tool is NOT called (an invented design name never reaches read_metrics and friends);
                  - `confirm_id`: the first 6 hex characters of the value ("yes, run 50ad87", "50ad87aa" -> 50ad87);
                  - `target`, `tool`, `viewer`: lower-cased and trimmed.
                  Every repair is appended to build/agent/normalize.jsonl (tool, field, before, after) for tuning.
  confirm_run     POST /confirm_run {confirm_id}: the single tool for the user's "yes, run <id>". It looks up what the id was
                  issued for (run_make, claude_task, what-if or an experiment render) and completes exactly that step, so the model
                  does not have to remember which gated tool to call again. An unknown, expired or used id starts nothing and the
                  error does not list other pending ids (the id is the user's consent token; the model must not be able to
                  discover one it was not given in this turn).
Never loosens a gate: confirm ids stay single use, 10 minutes, issued by the tool server only. CHIP_TOOLS_NORMALIZE=0 switches the
middleware off. Standard library plus FastAPI/pydantic.
Docs: docs/HERMES_AGENT_INTEGRATION.md, build/agent/tool_eval_PLAN.md (items C and E)
Tests: tests/tools/test_normalize_tools.py
"""
import difflib
import json
import os
import re
import sys
import time
from typing import List, Optional, Tuple

from fastapi import APIRouter
from pydantic import BaseModel, Field

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
LOG = os.path.join(REPO, "build", "agent", "normalize.jsonl")
router = APIRouter()

PLACEHOLDERS = {"-", "--", "---", "none", "null", "nil", "n/a", "na", "unknown", "string", "str", "", "?", "...", "<design>", "design",
                "<target>", "<none>", "undefined", "optional", "default", "empty", "tbd"}
SKIP = {"openapi.json", "docs", "redoc", "health"}
LOWER_KEYS = ("target", "tool", "viewer")
DESIGN_FIELDS = ("design",)
DESIGN_LIST_FIELDS = ("designs",)
ALIASES = {
    "kv8": "kv_attn_n8", "kv4": "kv_attn_n4", "kv16": "kv_attn_n16", "kv_n8": "kv_attn_n8", "kv_n4": "kv_attn_n4", "kv_n16": "kv_attn_n16",
    "kvn8": "kv_attn_n8", "kv_attn8": "kv_attn_n8", "kv8_int4": "kv_attn_n8_int4", "kv_int4": "kv_attn_n8_int4",
    "kv_ring": "kv_attn_n8_ring", "kv8_ring": "kv_attn_n8_ring", "kv_attn": "kv_attn_n8",
    "vision": "vision_block", "text": "text_sentiment", "sentiment": "text_sentiment", "pitch": "audio_pitch", "onset": "audio_onset",
    "itm": "image_text_match", "template": "user_proj_example", "baseline": "user_proj_example", "counter": "user_proj_example",
    "wrapper": "user_project_wrapper", "soc_kv": "soc_kv_attn_n8", "soc_itm": "soc_image_text_match", "tiny_ai": "tiny_ai_core",
    "kv": "kv_attn_n8", "kvattn": "kv_attn_n8", "kv_attention": "kv_attn_n8",
}
# Word-level matching for loose names ("vision lit", "kv attention 16", "the kv_attn design", "caravel kv"): words that never
# name a design are dropped, synonyms are mapped onto the tokens of the design names, and FAMILY_DEFAULT breaks a tie inside a family.
STOP_WORDS = {"the", "a", "an", "open", "opening", "load", "launch", "start", "show", "view", "display", "see", "in", "with", "on", "of",
              "for", "to", "and", "me", "please", "pls", "can", "you", "could", "would", "let", "lets", "use", "using", "it", "its",
              "klayout", "k", "magic", "layout", "layouts", "gds", "gdsii", "design", "designs", "chip", "app", "application", "window",
              "gui", "file", "engine", "macro", "block_design", "my", "this", "that", "one", "now", "just", "repo", "tool", "viewer"}
SYNONYMS = {"attention": "attn", "atten": "attn", "attn": "attn", "lite": "lit", "light": "lit", "lighted": "lit", "caravel": "wrapper",
            "precision": "prec", "bfloat16": "bf16", "binary": "bin", "ternary": "tern", "sentiments": "sentiment", "matching": "match",
            "img": "image", "txt": "text", "soc": "soc", "upw": "wrapper", "proj": "project", "core": "core"}
FAMILY_DEFAULT = {"kv": "kv_attn_n8", "attn": "kv_attn_n8", "kv_attn": "kv_attn_n8", "prec": None, "audio": None, "vision": "vision_block",
                  "wrapper": "user_project_wrapper", "soc": None}
for _f in ("bin", "tern", "int4", "int8", "fp8", "fp16", "bf16"):
    ALIASES["prec_" + _f] = "prec_" + _f
    ALIASES[_f] = "prec_" + _f
    ALIASES["prec" + _f] = "prec_" + _f
_DESIGNS: List[str] = []


def designs() -> List[str]:
    if not _DESIGNS:
        d = os.path.join(REPO, "designs")
        try:
            _DESIGNS.extend(sorted(n for n in os.listdir(d) if os.path.exists(os.path.join(d, n, "config.json"))))
        except OSError:
            pass
    return _DESIGNS


def is_placeholder(v) -> bool:
    return isinstance(v, str) and v.strip().strip("\"'`").lower() in PLACEHOLDERS


def resolve_design(raw: str, valid: Optional[List[str]] = None) -> Tuple[Optional[str], List[str]]:
    """(name, closest). name is the valid design the text means, or None (then `closest` has up to 5 suggestions)."""
    valid = valid if valid is not None else designs()
    if not valid:
        return raw, []
    s = raw.strip().strip("\"'`").lower().replace("-", "_").replace(" ", "_")
    s = re.sub(r"^designs/", "", s).rstrip("/")
    if s in valid:
        return s, []
    if s in ALIASES and ALIASES[s] in valid:
        return ALIASES[s], []
    pre = [v for v in valid if v.startswith(s)] if len(s) >= 3 else []
    if len(pre) == 1:
        return pre[0], []
    sub = [v for v in valid if s in v] if len(s) >= 4 else []
    if len(sub) == 1:
        return sub[0], []
    by_words, tied = _match_words(s, valid)
    if by_words:
        return by_words, []
    close = difflib.get_close_matches(s, valid, n=5, cutoff=0.6)
    if tied:
        return None, tied[:5]
    if close:
        best = difflib.SequenceMatcher(None, s, close[0]).ratio()
        second = difflib.SequenceMatcher(None, s, close[1]).ratio() if len(close) > 1 else 0.0
        if best >= 0.86 and best - second >= 0.06:
            return close[0], []
    return None, (close or pre or sub)[:5]


def _query_words(s: str) -> List[str]:
    out = []
    for w in re.findall(r"[a-z0-9]+", s.lower().replace("_", " ")):
        if w in STOP_WORDS:
            continue
        out.append(SYNONYMS.get(w, w))
    return out


def _word_hits(q: str, toks: List[str]) -> bool:
    return any(t == q or (len(q) >= 2 and t.startswith(q)) or t == "n" + q for t in toks)


def _match_words(s: str, valid: List[str]) -> Tuple[Optional[str], List[str]]:
    """(design, tied). Every remaining query word must hit a token of the design name (equal, prefix, or 16 -> n16). One hit
    wins; several: the fewest extra tokens, then FAMILY_DEFAULT. (None, tied) if still ambiguous."""
    q = _query_words(s)
    if not q:
        return None, []
    hits = [v for v in valid if all(_word_hits(w, v.split("_")) for w in q)]
    if not hits:
        return None, []
    if len(hits) == 1:
        return hits[0], []
    least = min(len(v.split("_")) for v in hits)
    best = [v for v in hits if len(v.split("_")) == least]
    if len(best) == 1:
        return best[0], []
    for key in ("_".join(q), q[0]):
        d = FAMILY_DEFAULT.get(key) or ALIASES.get(key)
        if d in best:
            return d, []
    return None, best


def design_from_text(text: str, valid: Optional[List[str]] = None) -> Optional[str]:
    """The design a free sentence talks about ("open the klayout with vision lit" -> vision_all_lit), or None."""
    valid = valid if valid is not None else designs()
    t = (text or "").lower()
    for d in sorted(valid, key=len, reverse=True):   # an exact full name wins
        if re.search(r"(?<![a-z0-9_])%s(?![a-z0-9_])" % re.escape(d), t):
            return d
    words = [w for w in re.findall(r"[a-z0-9_]+", t) if w not in STOP_WORDS]
    if not words:
        return None
    name, _ = resolve_design(" ".join(words), valid)
    if name:
        return name
    # a question has other words too ("what is the setup slack of kv attention 16"): the longest run of words that names a design
    heads = {v.split("_")[0] for v in valid} | {w for v in valid for w in v.split("_")[:2]} | set(ALIASES)
    for n in range(min(4, len(words)), 0, -1):
        for i in range(len(words) - n + 1):
            span = words[i:i + n]
            if not any(w in heads or SYNONYMS.get(w) in heads or "_" in w or re.search(r"\d", w) for w in span):
                continue
            if n == 1 and not (span[0] in ALIASES or "_" in span[0] or span[0] in valid or re.search(r"[a-z]\d|\d[a-z]", span[0])):
                continue
            got, _ = _match_words(" ".join(span), valid) if n > 1 or "_" in span[0] else (None, [])
            got = got or (ALIASES.get(span[0]) if n == 1 else None) or (span[0] if span[0] in valid else None)
            if not got and "_" in span[0]:
                got = resolve_design(span[0], valid)[0]
            if got:
                return got
    return None


def clean_confirm(v: str) -> str:
    m = re.search(r"(?<![0-9a-f])[0-9a-f]{6}", str(v).strip().lower())
    return m.group(0) if m else str(v).strip()


def normalize(tool: str, body: dict, valid: Optional[List[str]] = None):
    """(new_body, changes, error). error is a dict to return instead of calling the tool. Pure function."""
    out, changes = {}, []
    for k, v in body.items():
        if is_placeholder(v):
            changes.append((k, v, "(removed)"))
            continue
        if isinstance(v, str) and v != v.strip():
            changes.append((k, v, v.strip()))
            v = v.strip()
        out[k] = v
    if "confirm_id" in out and isinstance(out["confirm_id"], str):
        c = clean_confirm(out["confirm_id"])
        if c != out["confirm_id"]:
            changes.append(("confirm_id", out["confirm_id"], c))
            out["confirm_id"] = c
    for k in LOWER_KEYS:
        if isinstance(out.get(k), str) and out[k] != out[k].lower():
            changes.append((k, out[k], out[k].lower()))
            out[k] = out[k].lower()
    for k in DESIGN_FIELDS:
        if isinstance(out.get(k), str):
            name, close = resolve_design(out[k], valid)
            if name is None:
                return out, changes, {"error": "unknown design %r" % out[k], "closest": close,
                                      "valid_designs": valid if valid is not None else designs(),
                                      "hint": "call again with one of valid_designs (list_designs shows them with a description)"}
            if name != out[k]:
                changes.append((k, out[k], name))
                out[k] = name
    for k in DESIGN_LIST_FIELDS:
        v = out.get(k)
        if isinstance(v, str):
            v = [x for x in re.split(r"[,\s;]+", v) if x]
        if isinstance(v, list):
            fixed = []
            for x in v:
                if not isinstance(x, str) or is_placeholder(x):
                    continue
                name, close = resolve_design(x, valid)
                if name is None:
                    return out, changes, {"error": "unknown design %r in %s" % (x, k), "closest": close,
                                          "valid_designs": valid if valid is not None else designs(),
                                          "hint": "call again with names from valid_designs, or omit %s for all designs" % k}
                fixed.append(name)
            if fixed != out[k]:
                changes.append((k, out[k], fixed))
            if fixed:
                out[k] = fixed
            else:
                out.pop(k)
    return out, changes, None


def _log(tool: str, changes) -> None:
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            for k, a, b in changes:
                f.write(json.dumps({"t": time.strftime("%Y-%m-%dT%H:%M:%S"), "tool": tool, "field": k, "before": a, "after": b}) + "\n")
    except OSError:
        pass


class NormalizeMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or scope.get("method") != "POST" or os.environ.get("CHIP_TOOLS_NORMALIZE") == "0":
            return await self.app(scope, receive, send)
        tool = scope["path"].strip("/")
        if "/" in tool or tool in SKIP:
            return await self.app(scope, receive, send)
        chunks, more = [], True
        while more:
            m = await receive()
            if m["type"] != "http.request":
                break
            chunks.append(m.get("body", b""))
            more = m.get("more_body", False)
        raw = b"".join(chunks)
        try:
            body = json.loads(raw) if raw.strip() else None
        except ValueError:
            body = None
        if isinstance(body, dict):
            new, changes, err = normalize(tool, body)
            if changes:
                _log(tool, changes)
            if err is not None:
                payload = json.dumps(err).encode()
                await send({"type": "http.response.start", "status": 200,
                            "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(payload)).encode())]})
                await send({"type": "http.response.body", "body": payload})
                return
            if changes:
                raw = json.dumps(new).encode()
                scope = dict(scope)
                scope["headers"] = [(k, v) for k, v in scope["headers"] if k.lower() != b"content-length"] + \
                                   [(b"content-length", str(len(raw)).encode())]
        sent = {"done": False}

        async def replay():
            if not sent["done"]:
                sent["done"] = True
                return {"type": "http.request", "body": raw, "more_body": False}
            return await receive()

        await self.app(scope, replay, send)


def install(app) -> None:
    if not getattr(app.state, "chip_normalize_installed", False):
        app.add_middleware(NormalizeMiddleware)
        app.state.chip_normalize_installed = True


# ---------------------------------------------------------------- confirm_run
class ConfirmRunReq(BaseModel):
    confirm_id: str = Field(..., description="the 6-character id from the first reply of a gated tool (run_make, run_experiment, "
                            "claude_task, whatif_run), copied from the user's 'yes, run <id>'. Never invent one.", examples=["50ad87"])


def _server():
    for name in ("tool_server", "__main__"):
        m = sys.modules.get(name)
        if m is not None and hasattr(m, "_CONFIRMS") and hasattr(m, "run_make"):
            return m
    return None


def _self(name: str, body: dict) -> dict:
    """Call another mounted tool in this process (route endpoint + its request model); no HTTP, works under TestClient too."""
    import inspect
    srv = _server()
    flat = []
    for r in getattr(getattr(srv, "app", None), "routes", []):          # newer FastAPI wraps include_router() results
        flat.extend(r.original_router.routes if hasattr(r, "original_router") else [r])
    for r in flat:
        if getattr(r, "path", "") == "/" + name and getattr(r, "endpoint", None):
            try:
                param = next(iter(inspect.signature(r.endpoint).parameters.values()))
                return r.endpoint(param.annotation(**body))
            except Exception as e:  # noqa: BLE001
                return {"error": "call %s failed: %s" % (name, e)}
    return {"error": "tool %s is not mounted" % name}


@router.post("/confirm_run", operation_id="confirm_run", summary="Start what the user confirmed with 'yes, run <id>'", response_model=None)
def confirm_run(req: ConfirmRunReq) -> dict:
    """USE WHEN the user wrote 'yes, run <id>' (6 hex characters): completes the gated step that id was issued for (make job, Claude task,
    what-if run or experiment render) and returns its job_id. Same as repeating the gated tool with confirm_id, without choosing which.
    An unknown, expired or used id starts nothing. Never call it without the user's own 'yes, run <id>'."""
    cid = clean_confirm(req.confirm_id)
    srv = _server()
    if srv is None:
        return {"error": "tool server internals not reachable; repeat the gated tool with confirm_id"}
    c = srv._CONFIRMS.get(cid)
    if c and c.get("expires", 0) >= time.time():
        kind = c["kind"]
        if kind == "make":
            return srv.run_make(srv.RunMakeReq(confirm_id=cid))
        if kind == "claude":
            return srv.claude_task(srv.ClaudeTaskReq(confirm_id=cid))
        if kind == "whatif":
            pl = c.get("payload") or {}
            return _self("whatif_sweep" if "values" in pl or "sweep" in json.dumps(pl)[:200] else "whatif_run", {"confirm_id": cid})
    r = _self("run_experiment", {"id": "openroad-views", "confirm_id": cid})      # experiment render ids live in experiments_tools
    if r.get("job_id") or r.get("state") or not str(r.get("error", "")).startswith("unknown"):
        return r
    return {"error": "unknown, expired or already used confirm_id %r; nothing was started. Call the gated tool again without confirm_id to get a new one" % cid,
            "started": False}
