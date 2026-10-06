#!/usr/bin/env python3
# Docs: docs/RESULTS.md
"""results_site.py -- build build/site/index.html, a local results page from the same committed data as docs/RESULTS.md
(signoff and budget tables, layout gallery, engine views, agent results). Standard library only, no network.
Images and design pages are referenced by relative path from build/site/ into the repository (nothing is copied, so the page
is only valid inside this checkout). Usage: python3 scripts/docs/results_site.py [--open]"""
import html, os, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import tables, results_page as rp   # noqa: E402

REPO = tables.REPO
OUT = os.path.join(REPO, "build", "site")
UP = "../../"   # build/site -> repo root

CSS = """body{font:15px/1.5 system-ui,sans-serif;margin:0 auto;max-width:1200px;padding:16px;color:#222;background:#fff}
h1,h2,h3{line-height:1.2}table{border-collapse:collapse;margin:8px 0;font-size:13px}th,td{border:1px solid #ccc;padding:3px 7px;text-align:left}
th{background:#f0f0f0}.grid{display:flex;flex-wrap:wrap;gap:10px}.grid figure{margin:0;width:170px;text-align:center;font-size:12px}
.grid img{width:160px;height:160px;object-fit:contain;background:#eee;border:1px solid #ccc}.views figure{display:inline-block;margin:6px;vertical-align:top;max-width:430px}
.views img{width:420px;max-width:100%}.wide{overflow-x:auto}code{background:#f3f3f3;padding:0 3px}
@media (prefers-color-scheme:dark){body{background:#181818;color:#ddd}th{background:#2a2a2a}th,td{border-color:#444}code{background:#2a2a2a}a{color:#8ab4f8}.grid img{background:#333}}"""


def esc(x):
    return html.escape(str(x))


def table(cols, body):
    return '<div class="wide"><table><tr>%s</tr>%s</table></div>' % (
        "".join("<th>%s</th>" % esc(c) for c in cols),
        "".join("<tr>%s</tr>" % "".join("<td>%s</td>" % esc(c) for c in r) for r in body))


def link_table(md):
    """a generated markdown table whose first column may be [name](../designs/x/NOTES.md): render as an HTML table with links."""
    lines = [l for l in md.split("\n") if l.startswith("|")]
    import re
    cells = lambda l: [c.strip() for c in l.strip().strip("|").split("|")]
    head, body = cells(lines[0]), [cells(l) for l in lines[2:]]
    def cell(c):
        m = re.match(r"\[(.*)\]\(\.\./(.*)\)$", c)
        return '<a href="%s%s">%s</a>' % (UP, esc(m.group(2)), esc(m.group(1))) if m else esc(c)
    return '<div class="wide"><table><tr>%s</tr>%s</table></div>' % (
        "".join("<th>%s</th>" % esc(c) for c in head), "".join("<tr>%s</tr>" % "".join("<td>%s</td>" % cell(c) for c in r) for r in body))


def build():
    p = ["<!doctype html><html lang=en><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
         "<title>open-ai-chip results</title><style>%s</style><body>" % CSS,
         "<h1>open-ai-chip: measured results</h1>",
         "<p>Built by <code>make results</code> from committed files (<code>designs/*/output/metrics.json</code>, layout pictures, "
         "<code>examples/*</code> summaries); the same content as <a href='%sdocs/RESULTS.md'>docs/RESULTS.md</a>, which GitHub shows "
         "directly. Validation: <a href='%sdocs/VALIDATION.md'>VALIDATION.md</a>. Lessons: <a href='%sdocs/LESSONS.md'>LESSONS.md</a>.</p>"
         % (UP, UP, UP), "<h2>Layout gallery</h2><div class=grid>"]
    for d in rp.designs(tables.ORDER):
        p.append("<figure><a href='%sdesigns/%s/NOTES.md'><img loading=lazy src='%sdesigns/%s/output/layout.png' alt='%s'><br>%s</a></figure>"
                 % (UP, d, UP, d, d, d))
    p.append("</div><h2>Signoff</h2>" + link_table(tables.signoff()))
    p.append("<h2>Budget</h2>" + link_table(tables.budget()))
    p.append("<h2>Engine views</h2><div class=views>")
    for path, cap in rp.views():
        p.append("<figure><img loading=lazy src='%s%s' alt='%s'><figcaption>%s</figcaption></figure>" % (UP, esc(path), esc(cap), esc(cap)))
    p.append("</div><h2>Agent results</h2>")
    h = rp.harness()
    if h:
        p.append("<h3>Tool-use harness (%s)</h3>%s<p>Source: examples/hermes_harness/results_summary.json</p>" % (esc(h[0]["model"]), table(h[1], h[2])))
    r = rp.rag()
    if r:
        p.append("<h3>Retrieval-augmented answers (%s)</h3>%s%s<p>Source: examples/hermes_rag/results_summary.json</p>"
                 % (esc(r[0]["model"]), table(r[1], r[2]), table(r[3], r[4])))
    k = rp.klayout_rows()
    if k:
        p.append("<h3>KLayout driven by the agent</h3>%s<p>Source: examples/hermes_klayout_gui/README.md</p>"
                 % table(["scenario", "tool sequence", "right sequence", "seconds"], k))
    m = rp.models()
    if m:
        p.append("<h3>Other models</h3>%s<p>Source: examples/models_results.json</p>" % table(*m))
    p.append("<h2>Design pages</h2><p>%s</p></body></html>" % " | ".join(
        "<a href='%sdesigns/%s/NOTES.md'>%s</a>" % (UP, d, d) for d in tables.ORDER))
    os.makedirs(OUT, exist_ok=True)
    path = os.path.join(OUT, "index.html")
    with open(path, "w") as f:
        f.write("\n".join(p))
    return path


if __name__ == "__main__":
    path = build()
    print("results_site: wrote build/site/index.html")
    if "--open" in sys.argv:
        opener = "open" if sys.platform == "darwin" else "xdg-open"
        try:
            subprocess.run([opener, path], check=False)
        except OSError:
            print("results_site: no '%s' here; open build/site/index.html in a browser" % opener)
