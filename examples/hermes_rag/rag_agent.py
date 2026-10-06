#!/usr/bin/env python3
"""Hermes + KLayout/EDA tools, optionally plus RAG (a search_docs tool over the repo's markdown) and a RAG grounding check.
Docs: examples/hermes_rag/README.md, docs/HERMES_FROM_TERMINAL.md

  build/agent/venv/bin/python examples/hermes_rag/rag_agent.py "Why was the slew repair margin 20 and not 70?"
  build/agent/venv/bin/python examples/hermes_rag/rag_agent.py "..." --no-rag        # the 10 EDA tools only
  build/agent/venv/bin/python examples/hermes_rag/rag_agent.py "..." --no-grounding  # RAG without the answer check

The loop is the harness ReAct loop (examples/hermes_harness/harness.py: same model, options, prompt format, tool
budget, Tracer, grounding helpers); only the tool list, one prompt paragraph and the grounding rule differ. Prints a
trace on stderr (tool calls, retrieved chunk ids) and the answer plus the parsed citations on stdout.
"""
import argparse, json, os, re, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(ROOT, "examples", "hermes_harness"))
import rag                                  # noqa: E402
import router                               # noqa: E402
import harness                              # noqa: E402  (also imports eda_tools and hermes_agent)
from harness import eda_tools, hermes_agent  # noqa: E402

SEARCH_RESULT_CHARS_V2 = 5600               # v2 hits carry two ~600-character windows each
SEARCH_RESULT_CHARS = 4400                  # 4 chunks of up to 800 chars plus headers fit; hermes_agent.RESULT_CHARS (3500) would cut them

RAG_RULES_V1 = """

Documentation search (RAG):
- For WHY / HOW / WHAT-FIXED / explanation questions (causes, fixes, lessons, decisions) call search_docs(query) FIRST. The answer is in the written docs, not in metrics.json. Use distinctive keywords from the question (design names, error codes such as GRT-0116, metric names).
- If the first search does not contain the answer, search once more with different keywords. Use read_metrics etc. only for plain measured numbers.
- Answer in 1 to 3 sentences using ONLY the retrieved text. Copy numbers exactly as written there.
- End with 'Sources: <file> > <heading>' naming the file path and heading of each chunk you used (as printed in the search result). Cite only files that search_docs returned.
- If the retrieved text does not answer the question, say it is unknown; do not guess."""


# V2 (written after the first scored run, which showed the model using read_metrics/signoff_summary for why/what-caused questions)
RAG_RULES_V2 = RAG_RULES_V1 + """
- Routing: a question that asks why, how, what caused, what fixed, what limits, what was learned or which choice is best is a DOCUMENTATION question. Do NOT answer it from read_metrics, signoff_summary or precheck_summary (they only hold numbers and pass/fail lists; they cannot say why). Call search_docs first, with 4 to 8 keywords copied from the question, and leave k unset.
- Look at the lines of each chunk that match the question; if the chunks are about a different design or topic than the question, search again with the design name plus the key words."""
# V3 (router configs): short. The harness may already have run search_docs for a documentation question (the result is the first
# tool response in the conversation); the model only has to read it, answer and cite.
RAG_RULES_V3 = """

Documentation questions (why, how, what fixed, what caused, what limits): the answer is written text, not a number in metrics.json.
- Use the search_docs results already in this conversation, or call search_docs(query) with 4 to 8 keywords from the question if they do not contain the answer.
- Answer in 1 to 3 sentences from the retrieved text only: state the cause or reason, copy numbers exactly.
- End with 'Sources: <file> > <heading>' for each chunk you used. If the retrieved text does not answer it, say unknown."""
RAG_RULES = {"v1": RAG_RULES_V1, "v2": RAG_RULES_V2, "v3": RAG_RULES_V3}
PROMPT = {"version": "v2"}


def schemas(use_rag):
    return eda_tools.TOOLS + ([rag.TOOL] if use_rag else [])


def system_prompt(use_rag, version=None):
    base = hermes_agent.SYSTEM + (RAG_RULES[version or PROMPT["version"]] if use_rag else "")
    return base + hermes_agent.tools_suffix(schemas(use_rag))     # the Hermes tool list + <tool_call> format


def format_hits(res, limit=None):
    if "error" in res:
        return json.dumps(res)
    out = []
    for i, h in enumerate(res["hits"], 1):
        out.append(f"[{i}] file: {h['file']} | heading: {h['heading']} | lines {h['lines']}\n{h['text']}")
    s = "\n\n".join(out) or "no matching chunks"
    limit = limit or SEARCH_RESULT_CHARS
    return s if len(s) <= limit else s[:limit] + "...[truncated]"


# ---------------------------------------------------------------- citations and the RAG grounding check
CITE_RE = re.compile(r"[\w./\-]+\.md\b")
# Not tools/eval/run_eval.UNKNOWN_RE on purpose: this one gates citations/guardrail, that one scores answers.
UNKNOWN_RE = re.compile(r"\b(unknown|not (?:in|found|documented|available|mentioned)|no (?:information|data|record)|cannot (?:be )?(?:answer|determin|find))", re.I)


def citations(answer):
    """File paths ending .md named in the answer (de-duplicated, in order)."""
    return list(dict.fromkeys(CITE_RE.findall(answer)))


def _file_ok(cited, retrieved_files):
    return any(f == cited or f.endswith("/" + cited) for f in retrieved_files)


def rag_grounding(question, answer, retrieved, results):
    """List of problems: (1) a cited file that no search returned, (2) no citation although chunks were retrieved,
    (3) a number that is in neither a retrieved chunk nor a tool result (harness.grounding_check, a sum/difference/ratio
    of two source numbers also counts). `retrieved` is the list of hit dicts shown to the model."""
    bad = []
    files = {h["file"] for h in retrieved}
    body = harness.hermes_agent_strip(answer)
    for c in citations(answer):
        if not _file_ok(c, files):
            bad.append(f"cited file {c} was not retrieved")
    if retrieved and not citations(answer) and not UNKNOWN_RE.search(answer):
        bad.append("no citation (file + heading)")
    blobs = [h["text"] for h in retrieved] + list(results)
    for b in harness.grounding_check(question, body, blobs):
        if b not in bad:
            bad.append(f"number or name {b} not in retrieved text or tool results")
    return bad


GUARD_MSG = ("This is a documentation question and your answer cites no retrieved file. Call search_docs with 4 to 8 keywords from the "
             "question, or answer only from the retrieved text and end with 'Sources: <file> > <heading>' naming a retrieved file; "
             "if the documents do not say, answer unknown.")


def guardrail_problem(route, answer, retrieved):
    """Retrieval guardrail: for a doc-type question an answer must cite a retrieved file (or say it is unknown).
    Returns a problem string or None. Pure function, no model."""
    if route != "doc" or UNKNOWN_RE.search(answer):
        return None
    files = {h["file"] for h in retrieved}
    if any(_file_ok(c, files) for c in citations(answer)):
        return None
    return "doc-type question answered without citing a retrieved file"


# ---------------------------------------------------------------- the loop
def run_rag_episode(question, use_rag=True, grounding=True, tracer=None, verbose=False, max_revisions=1,
                    search_mode="v1", auto_retrieve=False, guardrail=False, chat=None, prompt_version=None):
    """search_mode "v2": improved retrieval for every search_docs call. auto_retrieve: the router classifies the question and
    for doc-type the harness calls search_docs itself before the first model turn. guardrail: one rejection of a doc-type
    answer that cites no retrieved file. chat: the model call (default harness.chat; a stub in the tests)."""
    chat = chat or harness.chat
    cfg = harness.Config(grounding=grounding)
    tracer = tracer or harness.Tracer("rag" if use_rag else "norag")
    tracer.episode += 1
    t0 = time.time()
    sysmsg = system_prompt(use_rag, prompt_version)
    allowed = schemas(use_rag)
    route = router.classify(question)
    tracer.event("start", question=question, rag=use_rag, grounding=grounding, route=route, search_mode=search_mode,
                 auto_retrieve=auto_retrieve, guardrail=guardrail)
    msgs = [{"role": "system", "content": sysmsg}, {"role": "user", "content": question}]
    calls, results, retrieved, revisions, steps, verdict, answer = [], [], [], 0, 0, None, ""
    searches, guard_used = [], 0
    limit = SEARCH_RESULT_CHARS_V2 if search_mode == "v2" else SEARCH_RESULT_CHARS

    def record_search(args, res, auto):
        retrieved.extend(res["hits"])
        searches.append({"query": res["query"], "chunk_ids": [h["id"] for h in res["hits"]],
                         "files": [h["file"] for h in res["hits"]], "auto": auto})

    if use_rag and auto_retrieve and route == "doc":      # retrieve-first: the harness, not the model, decides to search
        args = {"query": question}
        res = rag.call(args, search_mode)
        calls.append({"name": "search_docs", "args": args, "error": "error" in res, "auto": True})
        if "hits" in res:
            record_search(args, res, True)
        txt = format_hits(res, limit)
        tracer.event("auto_retrieve", route=route, args=args, chunk_ids=[h["id"] for h in res.get("hits", [])])
        msgs.append({"role": "assistant", "content": hermes_agent.tool_call_text("search_docs", args)})
        msgs.append(hermes_agent.tool_message([hermes_agent.tool_block("search_docs", txt)]))
        if verbose:
            print(f"[auto search_docs] {question!r} -> {[h['file'] for h in res.get('hits', [])]}", file=sys.stderr, flush=True)

    def do_calls(tcs):
        blocks = []
        for name, args in tcs:
            if len(calls) >= cfg.max_calls:
                txt = json.dumps({"error": hermes_agent.BUDGET_MSG})
            elif name == "search_docs" and use_rag:
                res = rag.call(args, search_mode)
                calls.append({"name": name, "args": args, "error": "error" in res, "auto": False})
                if "hits" in res:
                    record_search(args, res, False)
                txt = format_hits(res, limit)
                tracer.event("tool", tool=name, args=args, chunk_ids=[h["id"] for h in res.get("hits", [])])
            else:
                err = harness.validate_args(name, args, allowed) if name not in {s["function"]["name"] for s in allowed} else None
                res = {"error": err} if err else harness.execute(name, args, cfg)
                if not err:
                    results.append(json.dumps(res, default=str))
                calls.append({"name": name, "args": args, "error": isinstance(res, dict) and "error" in res})
                txt = hermes_agent._fmt(res)
                tracer.event("tool", tool=name, args=args, result_chars=len(txt))
            if verbose:
                print(f"[tool {len(calls)}] {name}({json.dumps(args)}) -> {txt[:200]!r}", file=sys.stderr, flush=True)
            blocks.append(hermes_agent.tool_block(name, txt))
        msgs.append(hermes_agent.tool_message(blocks))

    while steps < cfg.max_steps and time.time() - t0 < cfg.max_seconds:
        steps += 1
        content = chat(msgs)["message"].get("content") or ""
        tracer.event("model", step=steps, output=content[:1500])
        tcs = hermes_agent._parse_tool_calls(content)
        if tcs:
            msgs.append({"role": "assistant", "content": content})
            do_calls(tcs)
            continue
        answer = content.strip()
        if guardrail and not guard_used and answer:
            gp = guardrail_problem(route, answer, retrieved)
            tracer.event("guardrail", route=route, problem=gp)
            if gp:
                guard_used += 1
                msgs += [{"role": "assistant", "content": answer}, {"role": "user", "content": GUARD_MSG}]
                continue
        if grounding and revisions < max_revisions and answer:
            bad = rag_grounding(question, answer, retrieved, results)
            verdict = {"ok": not bad, "problems": bad}
            tracer.event("grounding", **verdict)
            if bad:
                revisions += 1
                msgs += [{"role": "assistant", "content": answer},
                         {"role": "user", "content": "Grounding check failed: " + "; ".join(bad) + ". Answer again using only retrieved text "
                          "or tool results and cite only retrieved files as 'Sources: <file> > <heading>', or say unknown."}]
                continue
        break
    else:
        answer = answer or "unknown (step or time budget exhausted)"
    final_bad = rag_grounding(question, answer, retrieved, results) if (use_rag or grounding) else None
    out = {"answer": answer, "route": route, "guardrail_rejections": guard_used, "tool_calls": calls, "searches": searches, "citations": citations(answer),
           "seconds": round(time.time() - t0, 2), "revisions": revisions, "steps": steps,
           "grounding_verdict": verdict, "final_grounding_problems": final_bad}
    tracer.event("end", answer=answer, seconds=out["seconds"], revisions=revisions, citations=out["citations"])
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("question", nargs="+")
    ap.add_argument("--no-rag", action="store_true", help="do not offer search_docs (the 10 EDA tools only)")
    ap.add_argument("--prompt", choices=["v1", "v2", "v3"], default="v2", help="RAG prompt paragraph (v2 adds routing rules, v3 is the short router-config prompt)")
    ap.add_argument("--router", action="store_true", help="v2 retrieval + retrieve-first for doc-type questions (implies --prompt v3)")
    ap.add_argument("--guardrail", action="store_true", help="reject once a doc-type answer that cites no retrieved file")
    ap.add_argument("--no-grounding", action="store_true", help="skip the grounding check and its one revision")
    ap.add_argument("--model", default=None, help="Ollama model tag (default: env HERMES_MODEL, else hermes3:8b)")
    a = ap.parse_args()
    if a.model:
        hermes_agent.MODEL = a.model
    use_rag = not a.no_rag
    PROMPT["version"] = "v3" if a.router else a.prompt
    tr = harness.Tracer("rag" if use_rag else "norag")
    idx = rag.build_index() if use_rag else None
    if idx:
        print(f"[index: {len(idx['chunks'])} chunks, {'cached' if idx['cached'] else 'rebuilt'}]", file=sys.stderr)
    r = run_rag_episode(" ".join(a.question), use_rag, use_rag and not a.no_grounding, tr, verbose=True,
                        search_mode="v2" if a.router else "v1", auto_retrieve=a.router, guardrail=a.guardrail)
    print(r["answer"])
    print("\n--- trace ---", file=sys.stderr)
    for s in r["searches"]:
        print(f"search_docs({s['query']!r}) -> chunk ids {s['chunk_ids']}\n    files {s['files']}", file=sys.stderr)
    print(f"tool calls: {[c['name'] for c in r['tool_calls']]}; revisions: {r['revisions']}; {r['seconds']}s", file=sys.stderr)
    print(f"citations: {r['citations']}; grounding problems: {r['final_grounding_problems']}", file=sys.stderr)
    print(f"trace file: {os.path.relpath(tr.path, ROOT)}", file=sys.stderr)


if __name__ == "__main__":
    main()
