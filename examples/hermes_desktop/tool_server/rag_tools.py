#!/usr/bin/env python3
"""Hybrid mini RAG for the Hermes tool server (a FastAPI APIRouter, auto-mounted by tool_server.py): rag_search, rag_answer, rag_index.

Corpus (git-tracked files only; build/, runs/, generated ROM .v / vectors.hex / weights.json never enter): the repo's markdown (design
NOTES.md/README.md, docs/*.md, model specs, example READMEs, skill SKILL.md/reference.md), the purpose header of every script and
tool, and one rendered "key facts" chunk per designs/*/output/metrics.json. Markdown is chunked by heading with rag.chunk_file (the BM25
module's chunker); each chunk keeps file, heading path and 1-based line range.
Retrieval: BM25 (the v2 scoring of examples/hermes_rag/rag.py: same tokenizer, boosts and windows) + dense cosine over Ollama embeddings
(/api/embed on 127.0.0.1:11434, default qwen3-embedding:0.6b, env RAG_EMBED_MODEL), fused by reciprocal rank (k=60). Plain Python, no numpy.
Cache: build/agent/rag/chunks.json (file sha256 -> chunks) and emb_<model>.json (chunk text sha256 -> float32 vector, base64); a changed file
re-chunks only that file and re-embeds only its new chunks. Ollama down -> BM25 only, and the reply says so (`mode`, `note`).
rag_answer builds an extractive answer in code: the best-matching sentences quoted verbatim with file:line, so a small model can relay it.
Docs: docs/HERMES_AGENT_INTEGRATION.md, examples/hermes_rag/README.md
Tests: tests/tools/test_rag_tools.py
"""
import base64
import hashlib
import json
import math
import os
import re
import subprocess
import sys
import time
import urllib.request
from array import array
from typing import Any, Dict, List, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field

HERE = os.path.dirname(os.path.abspath(__file__))
_RAG_DIR = os.path.abspath(os.path.join(HERE, "..", "..", "hermes_rag"))
sys.path.insert(0, _RAG_DIR)
import rag  # noqa: E402  (examples/hermes_rag/rag.py: chunker, tokenizer, BM25 v2 constants)

router = APIRouter()

OLLAMA = os.environ.get("RAG_OLLAMA_URL", "http://127.0.0.1:11434")
EMBED_MODEL = os.environ.get("RAG_EMBED_MODEL", "qwen3-embedding:0.6b")      # also installed: bge-m3:latest
QUERY_PREFIX = os.environ.get("RAG_QUERY_PREFIX", "Instruct: Given a question about a chip design repository, retrieve the passages that answer it\nQuery: ")
EMBED_CHARS, EMBED_BATCH, RRF_K, POOL = 1500, 16, 60, 60
PER_FILE = 2
EXCLUDE_MD = (".claude/agents", ".claude/commands", "CLAUDE.md", "docs/HERMES_AGENT.md", "docs/HERMES_AGENT_INTEGRATION.md",
              "docs/HERMES_DEMOS.md", "hermes-agents.md", "examples/hermes_rag/README.md")   # agent config; docs that quote the eval questions
CODE_DIRS = ("scripts/", "tools/", "examples/")
CODE_EXT = (".py", ".sh")
SKIP_PARTS = ("/runs/", "/build/", "__pycache__")
HEADER_LINES = 25


def _root() -> str:
    return rag.ROOT


def _dir() -> str:
    return os.path.join(_root(), "build", "agent", "rag")


# ------------------------------------------------------------------ corpus
def _tracked() -> List[str]:
    r = subprocess.run(["git", "ls-files"], cwd=_root(), capture_output=True, text=True)
    files = r.stdout.split()
    if not files:                                       # not a git tree (tests): walk it
        files = []
        for d, ds, fs in os.walk(_root()):
            ds[:] = [x for x in ds if x not in ("build", "runs", ".git", "__pycache__")]
            files += [os.path.relpath(os.path.join(d, f), _root()) for f in fs]
    return sorted(f for f in files if os.path.isfile(os.path.join(_root(), f)) and not any(p in "/" + f for p in SKIP_PARTS))


def corpus_files() -> List[Dict[str, str]]:
    """[{file, kind}] with kind md | code | metrics, sorted by file."""
    out = []
    for f in _tracked():
        if f.endswith(".md"):
            if not f.startswith(EXCLUDE_MD):
                out.append({"file": f, "kind": "md"})
        elif f.endswith(CODE_EXT) and f.startswith(CODE_DIRS):
            out.append({"file": f, "kind": "code"})
        elif re.fullmatch(r"designs/[^/]+/output/metrics\.json", f):
            out.append({"file": f, "kind": "metrics"})
    return out


def _sha(rel: str) -> str:
    return hashlib.sha256(open(os.path.join(_root(), rel), "rb").read()).hexdigest()


def chunk_code_header(rel: str) -> List[Dict[str, Any]]:
    """The purpose header: the leading comment / docstring block (shebang skipped), at most HEADER_LINES lines, as one chunk."""
    lines = open(os.path.join(_root(), rel), encoding="utf-8", errors="replace").read().split("\n")
    i = 1 if lines and lines[0].startswith("#!") else 0
    start, body, in_doc = i, [], False
    while i < len(lines) and len(body) < HEADER_LINES:
        s = lines[i].strip()
        if in_doc:
            body.append(lines[i])
            if '"""' in s:
                break
        elif s.startswith('"""') or s.startswith("'''"):
            body.append(lines[i])
            if s.count('"""') >= 2 or s.count("'''") >= 2:
                break
            in_doc = True
        elif s.startswith("#") or (not s and not body):
            if s:
                body.append(lines[i])
        else:
            break
        i += 1
    if not "".join(body).strip():
        return []
    return [{"file": rel, "heading": os.path.basename(rel) + " > header", "start": start + 1, "end": start + len(body),
             "text": "\n".join(body)}]


# metrics.json keys worth a sentence; value rendering in plain words so both BM25 and the embedder can use it
FACTS = [("design__instance__count__stdcell", "standard cells (instances)"), ("design__instance__area__stdcell", "standard-cell area (um^2)"),
         ("design__instance__count__class:sequential_cell", "sequential cells (flip-flops and latches)"),
         ("design__instance__utilization", "instance utilization"), ("design__die__area", "die area (um^2)"),
         ("design__die__bbox", "die bounding box (um)"), ("design__io", "I/O pins"),
         ("timing__setup__ws", "worst setup slack, all corners (ns)"), ("timing__hold__ws", "worst hold slack, all corners (ns)"),
         ("timing__setup_vio__count", "setup violations"), ("timing__hold_vio__count", "hold violations"),
         ("design__max_slew_violation__count", "max slew violations"), ("design__max_cap_violation__count", "max cap violations"),
         ("design__max_fanout_violation__count", "max fanout violations"), ("route__drc_errors", "route DRC errors"),
         ("magic__drc_error__count", "Magic DRC errors"), ("design__lvs_error__count", "LVS errors"),
         ("antenna__violating__nets", "antenna violating nets"), ("power__total", "total power (W)"),
         ("route__wirelength", "routed wirelength (um)"), ("design__instance__count__class:clock_buffer", "clock buffers"),
         ("design__instance__count__class:timing_repair_buffer", "timing repair buffers"), ("design__instance__count__class:hold_buffer", "hold buffers")]


def chunk_metrics(rel: str) -> List[Dict[str, Any]]:
    try:
        m = json.load(open(os.path.join(_root(), rel)))
    except (ValueError, OSError):
        return []
    design = rel.split("/")[1]
    lines = [f"Key facts of design {design} from its committed metrics.json (LibreLane sky130A flow result):"]
    for key, label in FACTS:
        if key in m:
            v = m[key]
            v = round(v, 4) if isinstance(v, float) and math.isfinite(v) else v
            lines.append(f"{design} {label}: {v} ({key})")
    if len(lines) == 1:
        return []
    return [{"file": rel, "heading": f"{design} > metrics.json key facts", "start": 1, "end": len(lines), "text": "\n".join(lines)}]


def chunk_one(f: Dict[str, str]) -> List[Dict[str, Any]]:
    k = f["kind"]
    return rag.chunk_file(f["file"]) if k == "md" else chunk_code_header(f["file"]) if k == "code" else chunk_metrics(f["file"])


def _chunk_key(c: Dict[str, Any]) -> str:
    return hashlib.sha256((c["file"] + "\n" + c["heading"] + "\n" + c["text"]).encode()).hexdigest()


def embed_text(c: Dict[str, Any]) -> str:
    return (c["file"] + " > " + c["heading"] + "\n" + c["text"])[:EMBED_CHARS]


# ------------------------------------------------------------------ embeddings (Ollama /api/embed)
def _ollama_embed(texts: List[str], model: str, timeout: float = 90.0) -> List[List[float]]:
    req = urllib.request.Request(OLLAMA + "/api/embed", data=json.dumps({"model": model, "input": texts, "truncate": True}).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)["embeddings"]


EMBED_FN = _ollama_embed          # tests replace this (a stub, or one that raises to simulate Ollama down)


def _unit(v: List[float]) -> array:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return array("f", [x / n for x in v])


def _enc(a: array) -> str:
    return base64.b64encode(a.tobytes()).decode()


def _dec(s: str) -> array:
    a = array("f")
    a.frombytes(base64.b64decode(s))
    return a


# ------------------------------------------------------------------ index
_MEM: Dict[str, Any] = {}
_QV: Dict[tuple, array] = {}      # last query vector (one search may rank twice)


def _emb_path(model: str) -> str:
    return os.path.join(_dir(), "emb_" + re.sub(r"[^A-Za-z0-9._-]", "_", model) + ".json")


def build_index(rebuild: bool = False, embed: bool = True, model: Optional[str] = None) -> Dict[str, Any]:
    """Incremental: unchanged files reuse cached chunks, unchanged chunks reuse cached vectors. Returns the status dict (also `timing`)."""
    model = model or EMBED_MODEL
    t0 = time.time()
    os.makedirs(_dir(), exist_ok=True)
    cpath = os.path.join(_dir(), "chunks.json")
    old = {"files": {}}
    if not rebuild and os.path.isfile(cpath):
        try:
            old = json.load(open(cpath))
        except ValueError:
            old = {"files": {}}
    files, shas, per_file, rechunked = corpus_files(), {}, {}, 0
    for f in files:
        sh = _sha(f["file"])
        shas[f["file"]] = sh
        o = old["files"].get(f["file"])
        if o and o["sha"] == sh and o["kind"] == f["kind"] and old.get("v") == 2:
            per_file[f["file"]] = o
        else:
            cs = chunk_one(f)
            for c in cs:
                c["key"] = _chunk_key(c)
            per_file[f["file"]] = {"sha": sh, "kind": f["kind"], "chunks": cs}
            rechunked += 1
    chunks = []
    for f in files:
        for c in per_file[f["file"]]["chunks"]:
            c["id"] = len(chunks)
            chunks.append(c)
    json.dump({"v": 2, "built": time.time(), "files": per_file}, open(cpath + ".tmp", "w"), separators=(",", ":"))
    os.replace(cpath + ".tmp", cpath)
    t_chunk = time.time() - t0
    emb, embedded, note = {}, 0, None
    epath = _emb_path(model)
    if embed:
        if not rebuild and os.path.isfile(epath):
            try:
                emb = {k: _dec(v) for k, v in json.load(open(epath)).items()}
            except ValueError:
                emb = {}
        live = {c["key"] for c in chunks}
        emb = {k: v for k, v in emb.items() if k in live}
        todo = [c for c in chunks if c["key"] not in emb]
        try:
            for i in range(0, len(todo), EMBED_BATCH):
                part = todo[i:i + EMBED_BATCH]
                for c, v in zip(part, EMBED_FN([embed_text(c) for c in part], model)):
                    emb[c["key"]] = _unit(v)
                    embedded += 1
                if (i // EMBED_BATCH) % 10 == 9:                      # checkpoint: a killed or failed build keeps its vectors
                    json.dump({k: _enc(v) for k, v in emb.items()}, open(epath + ".tmp", "w"), separators=(",", ":"))
                    os.replace(epath + ".tmp", epath)
                    if os.environ.get("RAG_VERBOSE"):
                        print(f"embedded {embedded}/{len(todo)}", file=sys.stderr, flush=True)
        except Exception as e:  # noqa: BLE001  Ollama down / model missing: keep what we have, BM25 still works
            note = f"embedding failed ({type(e).__name__}: {str(e)[:120]}); {len(todo) - embedded} chunks without vectors"
        json.dump({k: _enc(v) for k, v in emb.items()}, open(epath + ".tmp", "w"), separators=(",", ":"))
        os.replace(epath + ".tmp", epath)
    _MEM.clear()
    st = {"files": len(files), "chunks": len(chunks), "files_rechunked": rechunked, "model": model, "vectors": sum(c["key"] in emb for c in chunks),
          "vectors_new": embedded, "note": note, "chunk_seconds": round(t_chunk, 2), "build_seconds": round(time.time() - t0, 2)}
    _MEM.update({"chunks": chunks, "emb": emb, "model": model, "status": st, "built": time.time()})
    return st


def _ensure() -> Dict[str, Any]:
    if "chunks" not in _MEM:
        build_index()
    return _MEM


# ------------------------------------------------------------------ BM25 (rag.py v2 scoring) over the hybrid corpus
def _bm25_state(m: Dict[str, Any]) -> Dict[str, Any]:
    if "bm25" not in m:
        df, toks, head, tot = {}, [], [], 0
        for c in m["chunks"]:
            t = rag.tokenize2(c["text"])
            h = set(rag.tokenize2(c["heading"] + " " + c["file"].replace("/", " ")))
            toks.append(t)
            head.append(h)
            tot += len(t) + len(h)
            for w in set(t) | h:
                df[w] = df.get(w, 0) + 1
        m["bm25"] = {"df": df, "toks": toks, "head": head, "avgdl": tot / max(1, len(toks)), "lower": [c["text"].lower() for c in m["chunks"]]}
    return m["bm25"]


def _weights(m: Dict[str, Any], q: List[str]) -> Dict[str, float]:
    v, N = _bm25_state(m), len(m["chunks"])
    return {t: math.log(1 + (N - v["df"].get(t, 0) + 0.5) / (v["df"].get(t, 0) + 0.5)) for t in q}


def bm25_rank(query: str, pool: Optional[set] = None, limit: int = POOL) -> List[tuple]:
    """[(chunk_id, score)] best first; ties by file and line. `pool` restricts the candidate chunk ids."""
    m = _ensure()
    v = _bm25_state(m)
    q = list(dict.fromkeys(rag.tokenize2(query)))
    if not q:
        return []
    ids, why_q, w, out = rag.query_identifiers(query), bool(rag.WHY_Q.search(query)), _weights(m, q), []
    for c in m["chunks"]:
        i = c["id"]
        if pool is not None and i not in pool:
            continue
        toks, head, tf, s = v["toks"][i], v["head"][i], {}, 0.0
        for t in toks:
            if t in w:
                tf[t] = tf.get(t, 0) + 1
        dl = len(toks) / v["avgdl"]
        for t, f in tf.items():
            s += w[t] * f * (rag.K1 + 1) / (f + rag.K1 * (1 - rag.B + rag.B * dl))
        s += rag.HEAD_BOOST * sum(w[t] for t in q if t in head)
        if s <= 0:
            continue
        for ident in ids:
            if ident in v["lower"][i]:
                s += rag.ID_BONUS
            if f"/{ident}/" in "/" + c["file"]:
                s += rag.DESIGN_BONUS * (1.5 if c["file"].endswith("NOTES.md") else 1.0)
        if why_q and rag.WHY_HEAD.search(c["heading"].split(" > ")[-1]):
            s += rag.WHY_BONUS
        if len(q) >= 2:
            pairs = set(zip(q, q[1:]))
            s += rag.PHRASE_BONUS * sum(1 for p in zip(toks, toks[1:]) if p in pairs)
        out.append((-s, c["file"], c["start"], i))
    out.sort(key=lambda x: x[:3])
    return [(x[3], -x[0]) for x in out[:limit]]


def dense_rank(query: str, pool: Optional[set] = None, limit: int = POOL) -> List[tuple]:
    """[(chunk_id, cosine)] best first. Raises if the query cannot be embedded (caller falls back to BM25)."""
    m = _ensure()
    key = (m["model"], query)
    if key not in _QV:
        _QV.clear()
        _QV[key] = _unit(EMBED_FN([QUERY_PREFIX + query if QUERY_PREFIX else query], m["model"])[0])
    qv = _QV[key]
    out = []
    for c in m["chunks"]:
        v = m["emb"].get(c["key"])
        if v is None or (pool is not None and c["id"] not in pool):
            continue
        out.append((-sum(map(float.__mul__, qv, v)), c["file"], c["start"], c["id"]))
    out.sort(key=lambda x: x[:3])
    return [(x[3], -x[0]) for x in out[:limit]]


def fuse(rankings: List[List[tuple]], k: int = RRF_K) -> List[tuple]:
    """Reciprocal rank fusion: score(d) = sum over rankings of 1/(k + rank). [(chunk_id, score)], ties by id."""
    s: Dict[int, float] = {}
    for r in rankings:
        for pos, (cid, _) in enumerate(r, 1):
            s[cid] = s.get(cid, 0.0) + 1.0 / (k + pos)
    return sorted(s.items(), key=lambda x: (-x[1], x[0]))


def designs_named(query: str) -> List[str]:
    """Design directory names that occur as whole identifiers in the query (kv_attn_n8_int4 does not also select kv_attn_n8)."""
    base = os.path.join(_root(), "designs")
    names = sorted(os.listdir(base)) if os.path.isdir(base) else []
    ql = query.lower()
    return [d for d in names if re.search(r"(?<![a-z0-9_])" + re.escape(d.lower()) + r"(?![a-z0-9_])", ql)]


def _design_pool(m: Dict[str, Any], designs: List[str]) -> set:
    pre = tuple(f"designs/{d}/" for d in designs)
    return {c["id"] for c in m["chunks"] if c["file"].startswith(pre)}


def _explain_pool(m: Dict[str, Any]) -> set:
    """Chunk ids except the rendered metrics.json fact chunks (they match design names strongly but never explain a cause)."""
    return {c["id"] for c in m["chunks"] if not c["file"].endswith("/metrics.json")}


def search(query: str, k: int = 4, design: Optional[str] = None, mode: str = "hybrid", auto_design: bool = True, per_file: int = PER_FILE) -> Dict[str, Any]:
    """mode hybrid | bm25 | dense. design: explicit filter (hard). With no design and auto_design, designs named in the query pool the
    candidates first and unfiltered hits fill any remaining slots. Returns {hits, mode, note, filter, seconds}."""
    t0 = time.time()
    k = min(max(int(k or 4), 1), 10)
    m = _ensure()
    flt, hard = None, False
    if design:
        flt, hard = [design], True
    elif auto_design:
        flt = designs_named(query) or None
    pool = _design_pool(m, flt) if flt else None
    if pool is not None and not pool:
        pool, flt = None, None
    base = _explain_pool(m) if rag.WHY_Q.search(query) else None          # why / fix / limit questions: explanations, not metric rows
    if base is not None:
        pool = base if pool is None else (pool & base or pool)

    state = {"used": mode, "note": None}

    def rank(pl):
        if mode == "bm25":
            return bm25_rank(query, pl)
        if mode == "dense":
            return dense_rank(query, pl)
        bm = bm25_rank(query, pl)
        try:
            dn = dense_rank(query, pl)
        except Exception as e:  # noqa: BLE001  Ollama down or model missing: say so and use BM25 alone
            state["used"], state["note"] = "bm25-fallback", f"Ollama embeddings unavailable ({type(e).__name__}); BM25 only"
            return bm
        if not dn:
            state["used"], state["note"] = "bm25-fallback", "no vectors in the index; BM25 only"
            return bm
        return fuse([bm, dn])

    ranked = rank(pool)
    ids = [r[0] for r in ranked]
    if pool is not None and not hard and len(ids) < k:                      # soft filter: top up from the whole corpus
        ids += [r[0] for r in rank(base) if r[0] not in ids]
    used, note = state["used"], state["note"]
    score = dict(ranked)
    hits, per = [], {}
    qw = _weights(m, list(dict.fromkeys(rag.tokenize2(query))))
    for cid in ids:
        c = m["chunks"][cid]
        if per.get(c["file"], 0) >= per_file:
            continue
        per[c["file"]] = per.get(c["file"], 0) + 1
        text, lines = rag._windows(c, qw)
        hits.append({"id": cid, "file": c["file"], "heading": c["heading"], "lines": lines, "score": round(score.get(cid, 0.0), 4),
                     "text": text, "markdown": citation(c["file"], c["heading"], c["start"], c["end"])})
        if len(hits) >= k:
            break
    return {"query": query, "mode": used, "note": note, "filter": flt, "hits": hits, "seconds": round(time.time() - t0, 3)}


def citation(file: str, heading: str, a: int, b: int) -> str:
    return f"[{file} > {heading} (lines {a}-{b})]({file}#L{a}-L{b})"


# ------------------------------------------------------------------ extractive answer
COVER = float(os.environ.get("RAG_COVER", "0.4"))
MINE = 16
CAUSAL = re.compile(r"\b(because|since|so that|due to|therefore|prun\w*|merg\w*|removed|constant|caus\w*|fix\w*|limit\w*|instead|hides?|dominat\w*)\b", re.I)
SENT_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9`(*\[])")


def sentences(c: Dict[str, Any]) -> List[Dict[str, Any]]:
    """Verbatim sentences of a chunk with the absolute line each starts on. A paragraph wrapped over several lines is joined first (single
    spaces, so a quote is verbatim up to line breaks); a bullet, numbered item or table row starts its own unit. Fenced code, table separator
    and header rows and headings are skipped."""
    lines, out, fence = c["text"].split("\n"), [], False
    para: List[tuple] = []                                    # (line number, stripped text)

    def flush():
        if not para:
            return
        text, offs = "", []
        for ln, t in para:
            offs.append((len(text), ln))
            text += (" " if text else "") + t
        if text.startswith("|"):
            parts = [(0, text)]
        else:
            parts, pos = [], 0
            for piece in SENT_SPLIT.split(text):
                at = text.find(piece, pos)
                parts.append((at, piece))
                pos = at + len(piece)
        for at, piece in parts:
            if len(piece.strip()) >= 25:
                ln = [l for o, l in offs if o <= at][-1]
                out.append({"text": piece.strip(), "line": ln})
        para.clear()

    for off, ln in enumerate(lines):
        t = ln.strip()
        if t.startswith("```"):
            fence = not fence
            flush()
            continue
        sep = re.fullmatch(r"[|:\- ]+", t) is not None
        if fence or not t or sep or t.startswith("#"):
            flush()
            continue
        if t.startswith("|") and off + 1 < len(lines) and re.fullmatch(r"[|:\- ]+", lines[off + 1].strip()):
            flush()
            continue                                                            # a table header row says nothing by itself
        if t.startswith(("|", "- ", "* ")) or re.match(r"\d+\.\s", t):
            flush()
        para.append((c["start"] + off, t))
        if t.startswith("|"):
            flush()
    flush()
    return out


def answer(question: str, k: int = 4, design: Optional[str] = None, mode: str = "hybrid", max_quotes: int = 3) -> Dict[str, Any]:
    """Top passages + extractive answer: the max_quotes sentences with the most question-term weight, quoted verbatim with file:line."""
    k = min(max(int(k or 4), 1), 10)
    r = search(question, max(k, MINE), design, mode, per_file=6)         # sentences are mined from a few more passages than are returned
    m = _ensure()
    q = list(dict.fromkeys(rag.tokenize2(question)))
    w = _weights(m, q)
    ids = rag.query_identifiers(question)
    why_q = bool(rag.WHY_Q.search(question))
    pool = []
    for rank, h in enumerate(r["hits"]):
        c = m["chunks"][h["id"]]
        for s in sentences(c):
            pool.append((rank, c, s, set(rag.tokenize2(s["text"]))))
    # local idf over the mined sentences: a design name that is in every sentence carries no weight, "pruned" or "because" does
    n = max(1, len(pool))
    lw = {t: math.log(1 + (n - sum(t in x[3] for x in pool) + 0.5) / (sum(t in x[3] for x in pool) + 0.5)) for t in q}
    cands = []
    for rank, c, s, toks in pool:
        sc = sum(lw[t] for t in q if t in toks) / math.sqrt(1 + len(toks) / 12.0)
        if s["text"].startswith("|"):
            sc *= 0.5                                                           # a table row answers a number, rarely a why
        if why_q and CAUSAL.search(s["text"]):
            sc *= 1.5
        if why_q and rag.WHY_HEAD.search(c["heading"].split(" > ")[-1]):
            sc *= 1.5                                                           # the repo keeps its explanations under "Intuitions and insights"
        if sc > 0:
            cands.append((sc - 0.1 * rank, rank, c, s))
    total_w = sum(w.values()) or 1.0
    cands = [x for x in cands if sum(w[t] for t in q if t in set(rag.tokenize2(x[3]["text"]))) / total_w >= COVER]   # a quote must cover enough of the question
    cands.sort(key=lambda x: (-x[0], x[1], x[3]["line"]))
    quotes, seen = [], set()
    for sc, rank, c, s in cands:
        if s["text"] in seen:
            continue
        seen.add(s["text"])
        quotes.append({"quote": s["text"][:500], "file": c["file"], "heading": c["heading"], "line": s["line"], "score": round(sc, 3),
                       "citation": f"{c['file']}:{s['line']}"})
        if len(quotes) >= max_quotes:
            break
    if quotes:
        quotes = [x for x in quotes if x["score"] >= 0.4 * quotes[0]["score"]]    # best first; drop weak tail quotes
    if not quotes:
        text = "No passage in the repository documentation answers this. Say you do not know; do not guess."
    else:
        text = "\n".join(f"- \"{x['quote']}\" ({x['citation']}, under \"{x['heading']}\")" for x in quotes)
    return {"question": question, "mode": r["mode"], "note": r["note"], "found": bool(quotes), "answer": text, "quotes": quotes,
            "passages": r["hits"][:k], "instruction": "Relay `answer` as written: the quotes are verbatim, keep the file:line citations. "
            "Do not add facts that are not in the quotes or passages.", "seconds": r["seconds"]}


def status() -> Dict[str, Any]:
    m = _ensure()
    st = dict(m["status"])
    st["age_seconds"] = round(time.time() - m["built"], 1)
    st["ollama_embed_model"] = EMBED_MODEL
    st["index_dir"] = os.path.relpath(_dir(), _root())
    st["by_kind"] = {}
    for c in m["chunks"]:
        kd = "metrics" if c["file"].endswith("metrics.json") else "code" if not c["file"].endswith(".md") else "md"
        st["by_kind"][kd] = st["by_kind"].get(kd, 0) + 1
    return st


# ------------------------------------------------------------------ endpoints
class RagSearchReq(BaseModel):
    query: str = Field(..., description="the question or keywords, e.g. 'why does kv_attn_n8_int4 have more flip-flops than kv_attn_n8'")
    k: Optional[int] = Field(4, description="passages to return, 1 to 10 (default 4)")
    design: Optional[str] = Field(None, description="optional design name: only that design's files are searched")


class RagAnswerReq(BaseModel):
    question: str = Field(..., description="a why / how / what-fixed question about the repository")
    k: Optional[int] = Field(4, description="passages to retrieve, 1 to 10 (default 4)")


class RagIndexReq(BaseModel):
    rebuild: Optional[bool] = Field(False, description="true: discard the cache and re-chunk and re-embed everything (slow)")


def _safe(fn):
    try:
        return fn()
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}


@router.post("/rag_search", operation_id="rag_search", summary="Hybrid (BM25 + embeddings) search of the repo docs", response_model=None)
def rag_search(req: RagSearchReq) -> dict:
    """Search the repository's notes, docs, specs, script headers and result summaries with BM25 fused with local dense embeddings.
    Returns ranked passages with file, heading, line range, score, text and a `markdown` citation. `mode` says whether embeddings were used
    (hybrid) or Ollama was down (bm25-fallback). Cite file and heading in the answer."""
    def go():
        r = search(req.query, req.k or 4, req.design)
        r["markdown"] = "\n".join(f"{i}. {h['markdown']}" for i, h in enumerate(r["hits"], 1))
        return r
    return _safe(go)


@router.post("/rag_answer", operation_id="rag_answer", summary="Answer a repo question with verbatim quoted sources", response_model=None)
def rag_answer(req: RagAnswerReq) -> dict:
    """Retrieve with rag_search and build an extractive answer in code: the sentences that best match the question, quoted verbatim with
    file:line. Relay the `answer` field as is (keep the quotes and citations); `found` false means the docs do not cover it."""
    return _safe(lambda: answer(req.question, req.k or 4))


@router.post("/rag_index", operation_id="rag_index", summary="Status (or rebuild) of the docs search index", response_model=None)
def rag_index(req: RagIndexReq = RagIndexReq()) -> dict:
    """Index status: files, chunks, embedding model, vectors, build time and age. rebuild=true re-chunks and re-embeds everything."""
    def go():
        if req.rebuild:
            build_index(rebuild=True)
        return status()
    return _safe(go)


def main():
    import argparse
    ap = argparse.ArgumentParser(description="hybrid mini RAG: python3 rag_tools.py 'question' [-k 4] [--answer] [--mode hybrid|bm25|dense]")
    ap.add_argument("query", nargs="*")
    ap.add_argument("-k", type=int, default=4)
    ap.add_argument("--answer", action="store_true")
    ap.add_argument("--mode", default="hybrid")
    ap.add_argument("--rebuild", action="store_true")
    a = ap.parse_args()
    if a.rebuild:
        print(json.dumps(build_index(rebuild=True)))
    if a.query:
        q = " ".join(a.query)
        print(json.dumps(answer(q, a.k, mode=a.mode) if a.answer else search(q, a.k, mode=a.mode), indent=1))
    else:
        print(json.dumps(status(), indent=1))


if __name__ == "__main__":
    main()
