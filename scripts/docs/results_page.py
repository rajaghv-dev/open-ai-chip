#!/usr/bin/env python3
# Docs: docs/RESULTS.md
"""results_page.py -- data and markdown blocks for docs/RESULTS.md (gallery, engine views, agent results) and for the local site
(scripts/docs/results_site.py). Standard library only; reads only committed files. Imported by tables.py; not run on its own.
A missing optional file (examples/models_results.json) just omits its table."""
import json, os, re

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

VIEWS = [  # (directory, caption by file-name fragment)
    ("examples/openroad_gui/img", {
        "02_placement_density": "Placement density: where the standard cells sit (OpenROAD GUI)",
        "03_routing_congestion": "Routing congestion: global-route usage per tile",
        "05_ir_drop": "IR drop on the power grid",
        "06_clock_tree_viewer": "Clock tree: root, buffers and sinks",
        "07_worst_setup_path": "The worst setup path highlighted on the layout"}),
    ("examples/hermes_klayout_gui/img", {
        "live_kv_attn_n8_li1_met1_corner": "KLayout window driven by the agent: li1 + met1, lower-left corner of kv_attn_n8",
        "live_wrapper_met4_met5_mprj": "KLayout window driven by the agent: met4 + met5, macro mprj in the wrapper",
        "kv_attn_n8_li1_met1_corner": "Offscreen render: li1 + met1, lower-left 50 x 50 um of kv_attn_n8",
        "wrapper_met4_met5_mprj": "Offscreen render: met4 + met5 of user_project_wrapper_soc_kv, zoom to mprj",
        "tiny_ai_core_demo_markers": "tiny_ai_core with demo markers (red boxes are demo markers, not errors)"})]


def load(path):
    try:
        with open(os.path.join(REPO, path)) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def designs(order):
    """designs of ORDER that have a committed layout.png."""
    return [d for d in order if os.path.exists(os.path.join(REPO, "designs", d, "output", "layout.png"))]


def views():
    """[(repo-relative path, caption)] of the committed engine-view images, in VIEWS order."""
    out = []
    for dirn, caps in VIEWS:
        full = os.path.join(REPO, dirn)
        if not os.path.isdir(full):
            continue
        names = sorted(os.listdir(full))
        used = set()
        for key, cap in caps.items():
            for n in names:
                if n.endswith(".png") and n not in used and (n[:-4] == key or n.endswith(key + ".png") and "live_" not in n.replace(key, "")):
                    if key == "kv_attn_n8_li1_met1_corner" and n.startswith("live_"):
                        continue
                    if key == "wrapper_met4_met5_mprj" and n.startswith("live_"):
                        continue
                    used.add(n)
                    out.append(("%s/%s" % (dirn, n), cap))
        for n in names:
            if n.endswith(".png") and n not in used:
                out.append(("%s/%s" % (dirn, n), n[:-4].replace("_", " ")))
    return out


def klayout_rows():
    """rows of the measured-results table in examples/hermes_klayout_gui/README.md (scenario, tool sequence, right, seconds)."""
    try:
        text = open(os.path.join(REPO, "examples/hermes_klayout_gui/README.md")).read()
    except OSError:
        return []
    m = re.search(r"## Results \(measured\)(.*?)\n## ", text, re.S)
    rows = []
    for line in (m.group(1) if m else "").split("\n"):
        if line.startswith("|") and not line.startswith("|---") and not line.startswith("| Scenario"):
            rows.append([c.strip().replace("`", "") for c in line.strip().strip("|").split("|")])
    return rows


def harness():
    d = load("examples/hermes_harness/results_summary.json")
    if not d:
        return None
    head = ["configuration", "passed", "median latency s", "tool calls / question", "retries", "failed"]
    body = [[k, "%d/%d" % (v["passed"], v["total"]), v["median_latency_s"], v["tool_calls_per_q"], v["retries_total"],
             ", ".join(v.get("failed") or []) or "-"] for k, v in d["configs"].items()]
    return d, head, body


def rag():
    d = load("examples/hermes_rag/results_summary.json")
    if not d:
        return None
    rh = ["retrieval", "questions", "recall@1", "recall@4", "recall@8", "evidence in top 4"]
    rb = [[k, v["questions"], v["recall_at_k"]["@1"], v["recall_at_k"]["@4"], v["recall_at_k"]["@8"], v["evidence_in_top4_text"]]
          for k, v in d.get("retrieval", {}).items()]
    eh = ["configuration", "main passed", "held-out passed", "main hand-checked", "main median s"]
    eb = []
    for k, v in d.get("end_to_end", {}).items():
        m, h = v.get("main", {}), v.get("heldout", {})
        eb.append([k, "%s/%s" % (m.get("passed"), m.get("total")), "%s/%s" % (h.get("passed"), h.get("total")),
                   "%s/%s" % (m.get("hand_checked_passed"), m.get("total")), m.get("median_latency_s")])
    return d, rh, rb, eh, eb


def models():
    """(head, body) from examples/models_results.json, whatever its shape: the first list of dicts, or a dict of dicts."""
    d = load("examples/models_results.json")
    if not d:
        return None
    rows = None
    if isinstance(d, list):
        rows = d
    elif isinstance(d, dict):
        for v in d.values():
            if isinstance(v, list) and v and all(isinstance(x, dict) for x in v):
                rows = v
                break
        if rows is None:
            vals = [(k, v) for k, v in d.items() if isinstance(v, dict)]
            if vals:
                rows = [dict(name=k, **{a: b for a, b in v.items() if not isinstance(b, (dict, list))}) for k, v in vals]
    if not rows:
        return None
    cols = []
    for r in rows:
        for k, v in r.items():
            if isinstance(r, dict) and not isinstance(v, (dict, list)) and k not in cols:
                cols.append(k)
    return cols, [[r.get(c, "-") for c in cols] for r in rows]


def md_table(cols, body):
    return "\n".join(["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)] +
                     ["| " + " | ".join(str(x) for x in r) + " |" for r in body])


def gallery_md(order):
    ds = designs(order)
    per, rows = 5, []
    for i in range(0, len(ds), per):
        cells = ""
        for d in ds[i:i + per]:
            # small thumbnail (docs/img/thumbs/<d>.jpg, made by scripts/docs/make_thumbs.sh) when present, else the full render;
            # thumbnails live under docs/ because any new file in designs/<d>/ would make that design's run stale
            thumb = "img/thumbs/%s.jpg" % d
            src = thumb if os.path.exists(os.path.join(REPO, "docs", thumb)) else "../designs/%s/output/layout.png" % d
            cells += ('<td align="center"><a href="../designs/%s/NOTES.md"><img src="%s" width="160" alt="%s layout"></a><br>'
                      '<a href="../designs/%s/NOTES.md">%s</a> (<a href="../designs/%s/output/layout.png">full</a>)</td>'
                      % (d, src, d, d, d, d))
        rows.append("<tr>%s</tr>" % cells)
    return ("<table>\n%s\n</table>\n\nEach picture is the KLayout render of the design's GDSII (`output/layout.png`, thumbnails in "
            "`docs/img/thumbs/`); click it for the design page, \"full\" for the full-size render." % "\n".join(rows))


def views_md():
    out = ["Committed screenshots of the OpenROAD GUI ([how](../examples/openroad_gui/README.md)) and of KLayout driven by a Hermes agent ([how](../examples/hermes_klayout_gui/README.md)).", ""]
    for p, cap in views():
        out.append("**%s**  \n<img src=\"../%s\" width=\"420\" alt=\"%s\">\n" % (cap, p, cap))
    return "\n".join(out).rstrip()


def agents_md():
    o = []
    h = harness()
    if h:
        d, head, body = h
        o += ["### Tool-use harness (Hermes %s, %s questions)" % (d["model"], d["configs"]["baseline"]["total"]), "",
              md_table(head, body), "",
              "Source: `examples/hermes_harness/results_summary.json`; method in [the harness README](../examples/hermes_harness/README.md).", ""]
    r = rag()
    if r:
        d, rh, rb, eh, eb = r
        o += ["### Retrieval-augmented answers (Hermes %s, %s chunks of %s files)" % (d["model"], d["index"]["chunks"], d["index"]["files"]), "",
              md_table(rh, rb), "", md_table(eh, eb), "",
              "Source: `examples/hermes_rag/results_summary.json`; method in [the RAG README](../examples/hermes_rag/README.md).", ""]
    k = klayout_rows()
    if k:
        o += ["### KLayout driven by the agent (live Hermes, 5 scenarios)", "",
              md_table(["scenario", "tool sequence", "right sequence", "seconds"], k), "",
              "Source: results table of [the KLayout GUI README](../examples/hermes_klayout_gui/README.md).", ""]
    m = models()
    if m:
        o += ["### Other models", "", md_table(*m), "", "Source: `examples/models_results.json`.", ""]
    return "\n".join(o).rstrip() or "No agent summaries are committed yet."
