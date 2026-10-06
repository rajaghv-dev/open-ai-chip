#!/usr/bin/env python3
"""BM25 retrieval over the repo's committed markdown (standard library only, deterministic, no downloads).

  python3 examples/hermes_rag/rag.py "why does kv_attn_n8_int4 have more flip-flops" [-k 4]

Corpus: `git ls-files '*.md'` minus agent/skill config and the Hermes docs themselves (see EXCLUDE). Each file is
split by markdown heading (fenced code blocks are not scanned for headings); a chunk keeps file path, heading path
and 1-based line range. The index is cached in build/agent/rag_index.json, keyed by the sha256 of every file, and
is rebuilt only when a file is added, removed or changed.

API: search_docs(query, k=4) -> [{file, heading, lines, score, text}]   (text trimmed to about 800 characters)
"""
import hashlib, json, math, os, re, subprocess, sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
INDEX_PATH = os.path.join(ROOT, "build", "agent", "rag_index.json")
K1, B = 1.5, 0.75
TEXT_CHARS = 800
MAX_CHUNK_LINES = 30          # long sections are split so that one hit stays focused
EXCLUDE = (".claude/", "CLAUDE.md", "examples/", "docs/HERMES_AGENT.md")   # agent config and the agent's own docs (they quote the eval questions)
STOP = set("a an the of to in on for and or is are was were be by with as at it its this that these those from "
           "what why how which does do did has have had not no can".split())


def tokenize(text):
    """Lowercase; keep identifiers whole (kv_attn_n8_int4) AND add their parts (kv, attn, n8, int4)."""
    out = []
    for w in re.findall(r"[A-Za-z0-9_]+(?:[.\-][A-Za-z0-9_]+)*", text.lower()):
        parts = [p for p in re.split(r"[_.\-]", w) if p]
        if len(parts) > 1:
            out.append(w)
        out.extend(parts)
    return [t for t in out if t not in STOP and len(t) > 1 or t.isdigit()]


def corpus_files():
    r = subprocess.run(["git", "ls-files", "*.md"], cwd=ROOT, capture_output=True, text=True)
    files = sorted(f for f in r.stdout.split() if not f.startswith(EXCLUDE))
    return [f for f in files if os.path.isfile(os.path.join(ROOT, f))]


def chunk_file(rel):
    lines = open(os.path.join(ROOT, rel), encoding="utf-8", errors="replace").read().split("\n")
    chunks, path, start, fence = [], [], 1, False
    cur_heading = os.path.basename(rel)

    def flush(end):
        body = lines[start - 1:end]
        if not "".join(body).strip():
            return
        for s in range(0, len(body), MAX_CHUNK_LINES):
            part = body[s:s + MAX_CHUNK_LINES]
            if "".join(part).strip():
                chunks.append({"file": rel, "heading": cur_heading, "start": start + s, "end": start + s + len(part) - 1,
                               "text": "\n".join(part)})

    for i, ln in enumerate(lines, 1):
        if ln.lstrip().startswith("```"):
            fence = not fence
        m = None if fence else re.match(r"^(#{1,6})\s+(.*\S)\s*$", ln)
        if m:
            if i > start:
                flush(i - 1)
            level = len(m.group(1))
            path = path[:level - 1] + [m.group(2)] if len(path) >= level - 1 else path + [m.group(2)]
            cur_heading = " > ".join(path)
            start = i
    flush(len(lines))
    return chunks


def _sha(rel):
    return hashlib.sha256(open(os.path.join(ROOT, rel), "rb").read()).hexdigest()


def build_index(force=False):
    files = corpus_files()
    shas = {f: _sha(f) for f in files}
    if not force and os.path.isfile(INDEX_PATH):
        try:
            idx = json.load(open(INDEX_PATH))
            if idx.get("files") == shas and idx.get("params") == [K1, B, MAX_CHUNK_LINES]:
                idx["cached"] = True
                return idx
        except (ValueError, KeyError):
            pass
    chunks, df = [], {}
    for f in files:
        for c in chunk_file(f):
            # heading tokens are counted twice so a matching heading outranks a passing mention
            toks = tokenize(c["heading"]) * 2 + tokenize(c["text"])
            tf = {}
            for t in toks:
                tf[t] = tf.get(t, 0) + 1
            c["id"] = len(chunks)
            c["len"] = len(toks)
            c["tf"] = tf
            for t in tf:
                df[t] = df.get(t, 0) + 1
            chunks.append(c)
    idx = {"params": [K1, B, MAX_CHUNK_LINES], "files": shas, "chunks": chunks, "df": df,
           "avgdl": sum(c["len"] for c in chunks) / max(1, len(chunks)), "cached": False}
    os.makedirs(os.path.dirname(INDEX_PATH), exist_ok=True)
    tmp = INDEX_PATH + ".tmp"
    json.dump(idx, open(tmp, "w"), separators=(",", ":"), sort_keys=True)
    os.replace(tmp, INDEX_PATH)
    return idx


_MEM = {}


def _index():
    if "idx" not in _MEM:
        _MEM["idx"] = build_index()
    return _MEM["idx"]


def _trim(text, n=TEXT_CHARS):
    text = text.strip()
    return text if len(text) <= n else text[:n].rsplit(" ", 1)[0] + " ..."


PER_FILE = 2   # at most this many chunks from one file in a result list, so one big file cannot fill all k slots


def _window(c, qtoks, n=TEXT_CHARS):
    """The best <= n character run of whole lines of the chunk (most query-term hits), so the answer part of a long
    chunk is not cut off. Returns (text, first_line, last_line)."""
    lines = c["text"].split("\n")
    if len(c["text"].strip()) <= n:
        return c["text"].strip(), c["start"], c["end"]
    qs = set(qtoks)
    val = [sum(t in qs for t in tokenize(l)) for l in lines]
    best = (-1, 0, 0)
    for i in range(len(lines)):
        size, j, tot = 0, i, 0
        while j < len(lines) and size + len(lines[j]) + 1 <= n:
            size += len(lines[j]) + 1
            tot += val[j]
            j += 1
        if j == i:                    # one line longer than n: take it and trim
            j, tot = i + 1, val[i]
        if tot > best[0]:
            best = (tot, i, j)
    _, i, j = best
    return _trim("\n".join(lines[i:j])), c["start"] + i, c["start"] + j - 1


def search_docs(query, k=4):
    idx = _index()
    N = len(idx["chunks"])
    q = list(dict.fromkeys(tokenize(query)))
    scored = []
    for c in idx["chunks"]:
        s = 0.0
        for t in q:
            f = c["tf"].get(t)
            if not f:
                continue
            n = idx["df"][t]
            idf = math.log(1 + (N - n + 0.5) / (n + 0.5))
            s += idf * f * (K1 + 1) / (f + K1 * (1 - B + B * c["len"] / idx["avgdl"]))
        if s > 0:
            scored.append((-s, c["file"], c["start"], c))
    scored.sort(key=lambda x: x[:3])          # ties broken by file and line: fully deterministic
    picked, per = [], {}
    for item in scored:
        f = item[1]
        if per.get(f, 0) < PER_FILE:
            per[f] = per.get(f, 0) + 1
            picked.append(item)
        if len(picked) >= max(1, int(k)):
            break
    out = []
    for ns, _, _, c in picked:
        text, a, b = _window(c, q)
        out.append({"id": c["id"], "file": c["file"], "heading": c["heading"], "lines": f"{a}-{b}",
                    "score": round(-ns, 3), "text": text})
    return out


# ------------------------------------------------------------ agent tool (same schema style as tools/eda_tools.py TOOLS)
TOOL = {"type": "function", "function": {
    "name": "search_docs",
    "description": "Search the repository's written documentation (design NOTES.md/README.md, docs/*.md, model specs, firmware README) "
                   "with BM25 keyword ranking. Use it for why / how / what-fixed questions (causes, fixes, explanations, lessons) that "
                   "are not plain numbers in metrics.json. Put distinctive words in the query (design names, error codes, metric names). "
                   "Returns the top chunks with file, heading, line range and text; cite file and heading in your answer.",
    "parameters": {"type": "object", "properties": {
        "query": {"type": "string", "description": "keywords, e.g. 'kv_attn_n8_int4 flip-flops more than kv_attn_n8'"},
        "k": {"type": ["integer", "null"], "description": "number of chunks to return, 1 to 8 (default 4)"}},
        "required": ["query"]}}}


def call(args):
    """Tool dispatch for the agent: never raises; {"error": ...} on bad input."""
    try:
        if not isinstance(args, dict) or not isinstance(args.get("query"), str) or not args["query"].strip():
            return {"error": "search_docs needs a non-empty string argument 'query'"}
        k = args.get("k") or 4
        if not isinstance(k, int) or isinstance(k, bool):
            return {"error": "k must be an integer 1..8"}
        return {"query": args["query"], "hits": search_docs(args["query"], min(max(k, 1), 8))}
    except Exception as e:  # noqa: BLE001
        return {"error": f"{type(e).__name__}: {e}"}


def main():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("query", nargs="+")
    ap.add_argument("-k", type=int, default=4)
    ap.add_argument("--rebuild", action="store_true")
    a = ap.parse_args()
    if a.rebuild:
        _MEM["idx"] = build_index(force=True)
    idx = _index()
    print(f"[index: {len(idx['chunks'])} chunks from {len(idx['files'])} files, {'cached' if idx['cached'] else 'rebuilt'}]", file=sys.stderr)
    for i, h in enumerate(search_docs(" ".join(a.query), a.k), 1):
        print(f"{i}. {h['file']} :: {h['heading']} (lines {h['lines']}) score {h['score']}")
        print("   " + h["text"][:300].replace("\n", "\n   ") + "\n")


if __name__ == "__main__":
    main()
