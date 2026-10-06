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


# ------------------------------------------------------------ v2 retrieval (new; v1 above is unchanged and stays the default)
# Added after the first eval (recall@1 6/10; right file but answer outside the 800-char window in r03, r09, r10):
# query normalisation (question words and filler dropped, light stemming), a boost for a query identifier that is a design
# directory name or occurs verbatim in the chunk (kv_attn_n8_int4, wb_rst_i, GRT-0116), file-path and heading tokens as a
# separate boosted field, a phrase bonus, near-duplicate removal, and two ~600-character windows per hit instead of one 800.
STOP2 = STOP | set("why how what which when where who whom whose does do did done is are was were be been being has have had "
                   "can could would should will shall may might must than then so such even though although while only also "
                   "both each any all some more most many much very just about into over under up down out off again "
                   "instead rather there their they them we our us you your i me my he she his her".split())
WIN_CHARS, WIN_N = 600, 2
PER_FILE_V2 = 2
HEAD_BOOST, ID_BONUS, DESIGN_BONUS, PHRASE_BONUS, WHY_BONUS = 1.0, 3.0, 2.0, 0.6, 4.0
WHY_Q = re.compile(r"\b(why|caus\w*|limit\w*|fix\w*|reason|lesson\w*|explain\w*|insight\w*|knee|sweet spot)\b", re.I)
WHY_HEAD = re.compile(r"intuition|insight|lesson|takeaway|why|reason|limit", re.I)
ID_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:[_\-][A-Za-z0-9]+)+")


def _stem(t):
    if t.isdigit() or len(t) <= 4 or "_" in t or "-" in t:
        return t
    for suf, keep in (("ies", "y"), ("ing", ""), ("ed", ""), ("es", ""), ("s", "")):
        if t.endswith(suf) and len(t) - len(suf) >= 3:
            return t[:-len(suf)] + keep
    return t


def tokenize2(text, stop=STOP2):
    out = []
    for w in re.findall(r"[A-Za-z0-9_]+(?:[.\-][A-Za-z0-9_]+)*", text.lower()):
        parts = [p for p in re.split(r"[_.\-]", w) if p]
        if len(parts) > 1:
            out.append(w)
        out.extend(parts)
    return [_stem(t) for t in out if (t not in stop and len(t) > 1) or t.isdigit()]


def query_identifiers(query):
    """Identifiers in a query: snake_case or dash-code words such as kv_attn_n8_int4, wb_rst_i, GRT-0116 (lower-cased)."""
    return list(dict.fromkeys(m.lower() for m in ID_RE.findall(query)))


def _idx2():
    if "v2" not in _MEM:
        idx = _index()
        df, toks, hfield, tot = {}, [], [], 0
        for c in idx["chunks"]:
            t = tokenize2(c["text"])
            h = tokenize2(c["heading"] + " " + c["file"].replace("/", " "))
            toks.append(t)
            hfield.append(set(h))
            tot += len(t) + len(h)
            for w in set(t) | set(h):
                df[w] = df.get(w, 0) + 1
        _MEM["v2"] = {"df": df, "toks": toks, "head": hfield, "avgdl": tot / max(1, len(toks)),
                      "lower": [c["text"].lower() for c in idx["chunks"]]}
    return _MEM["v2"]


def _windows(c, weights, n=WIN_CHARS, count=WIN_N):
    """Up to `count` non-overlapping runs of whole lines (<= n chars each) with the largest sum of query-term weights.
    A single line longer than n is cut to n characters around its densest part. Returns (text, 'a-b,c-d')."""
    lines = c["text"].split("\n")
    if len(c["text"].strip()) <= n * count:
        return c["text"].strip(), f"{c['start']}-{c['end']}"
    val = [sum(weights.get(t, 0.0) for t in set(tokenize2(l))) for l in lines]
    taken, spans = [False] * len(lines), []
    for _ in range(count):
        best = (0.0, -1, -1)
        for i in range(len(lines)):
            if taken[i]:
                continue
            size, j, tot = 0, i, 0.0
            while j < len(lines) and not taken[j] and (size + len(lines[j]) + 1 <= n or j == i):
                size += len(lines[j]) + 1
                tot += val[j]
                j += 1
            if tot > best[0]:
                best = (tot, i, j)
        if best[1] < 0:
            break
        _, i, j = best
        for x in range(i, j):
            taken[x] = True
        spans.append((i, j))
    if not spans:
        spans = [(0, 1)]
    spans.sort()
    parts, labels = [], []
    for i, j in spans:
        txt = "\n".join(lines[i:j]).strip()
        if len(txt) > n:                                   # one long line (a table row): centre on the query terms
            pos = [m.start() for t in weights for m in re.finditer(re.escape(t), txt.lower())]
            mid = sorted(pos)[len(pos) // 2] if pos else 0
            a = max(0, min(mid - n // 2, len(txt) - n))
            txt = ("..." if a else "") + txt[a:a + n] + ("..." if a + n < len(txt) else "")
        parts.append(txt)
        labels.append(f"{c['start'] + i}-{c['start'] + j - 1}")
    return "\n[...]\n".join(parts), ",".join(labels)


def search_docs_v2(query, k=4):
    idx, v = _index(), _idx2()
    N = len(idx["chunks"])
    q = list(dict.fromkeys(tokenize2(query)))
    ids = query_identifiers(query)
    why_q = bool(WHY_Q.search(query))
    if not q:
        return []
    w = {t: math.log(1 + (N - v["df"].get(t, 0) + 0.5) / (v["df"].get(t, 0) + 0.5)) for t in q}
    scored = []
    for c in idx["chunks"]:
        i = c["id"]
        toks, head = v["toks"][i], v["head"][i]
        tf = {}
        for t in toks:
            if t in w:
                tf[t] = tf.get(t, 0) + 1
        s = 0.0
        dl = len(toks) / v["avgdl"]
        for t, f in tf.items():
            s += w[t] * f * (K1 + 1) / (f + K1 * (1 - B + B * dl))
        s += HEAD_BOOST * sum(w[t] for t in q if t in head)
        if s <= 0:
            continue
        low = v["lower"][i]
        for ident in ids:
            if ident in low:
                s += ID_BONUS
            if f"/{ident}/" in "/" + c["file"]:
                s += DESIGN_BONUS * (1.5 if c["file"].endswith("NOTES.md") else 1.0)
        if why_q and WHY_HEAD.search(c["heading"].split(" > ")[-1]):   # repo convention: the explanations sit under "Intuitions and insights"
            s += WHY_BONUS
        if len(q) >= 2:                                    # phrase bonus: adjacent query terms adjacent in the chunk
            pairs = set(zip(q, q[1:]))
            s += PHRASE_BONUS * sum(1 for p in zip(toks, toks[1:]) if p in pairs)
        scored.append((-s, c["file"], c["start"], c))
    scored.sort(key=lambda x: x[:3])
    picked, per, seen = [], {}, []
    for item in scored:
        f, c = item[1], item[3]
        if per.get(f, 0) >= PER_FILE_V2:
            continue
        ts = set(v["toks"][c["id"]])
        if any(len(ts & o) / max(1, len(ts | o)) > 0.8 for o in seen):      # near-duplicate of a chunk already chosen
            continue
        per[f] = per.get(f, 0) + 1
        seen.append(ts)
        picked.append(item)
        if len(picked) >= max(1, int(k)):
            break
    out = []
    for ns, _, _, c in picked:
        text, lines = _windows(c, w)
        out.append({"id": c["id"], "file": c["file"], "heading": c["heading"], "lines": lines, "score": round(-ns, 3), "text": text})
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


def call(args, mode="v1"):
    """Tool dispatch for the agent: never raises; {"error": ...} on bad input. mode "v2" uses search_docs_v2."""
    try:
        if not isinstance(args, dict) or not isinstance(args.get("query"), str) or not args["query"].strip():
            return {"error": "search_docs needs a non-empty string argument 'query'"}
        k = args.get("k") or 4
        if not isinstance(k, int) or isinstance(k, bool):
            return {"error": "k must be an integer 1..8"}
        fn = search_docs_v2 if mode == "v2" else search_docs
        return {"query": args["query"], "hits": fn(args["query"], min(max(k, 1), 8))}
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
