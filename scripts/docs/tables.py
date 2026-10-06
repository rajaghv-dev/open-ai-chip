#!/usr/bin/env python3
"""tables.py -- regenerate the results tables of README.md between marker comments from the committed evidence
(designs/<d>/output/metrics.json, resources.json, designs/<d>/config.json). Standard library only; no Docker, no build/.

README.md holds, for each table NAME:   <!-- results:begin NAME -->  ...  <!-- results:end NAME -->
Only the text between the markers is replaced; running it twice gives no diff.   Usage: python3 scripts/docs/tables.py [--check]
  --check  exit 1 (and change nothing) when README.md is out of date."""
import json, os, re, sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
README = os.path.join(REPO, "README.md")
# the order of every table: the order make all-designs hardens the designs
ORDER = ["user_proj_example", "vision_all_lit", "vision_block", "text_sentiment", "tiny_ai_core", "user_project_wrapper",
         "audio_pitch", "audio_onset", "image_text_match",
         "prec_bin", "prec_tern", "prec_int4", "prec_int8", "prec_fp8", "prec_fp16", "prec_bf16",
         "soc_image_text_match", "user_project_wrapper_soc_itm",
         "kv_attn_n4", "kv_attn_n8", "kv_attn_n16", "kv_attn_n8_int4", "kv_attn_n8_ring",
         "soc_kv_attn_n8", "user_project_wrapper_soc_kv"]


def load(path):
    try:
        with open(os.path.join(REPO, path)) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def worst(m, kind):
    """(slack, corner) of the smallest timing__<kind>__ws__corner:<c> value."""
    best = None
    for k, v in m.items():
        if k.startswith("timing__%s__ws__corner:" % kind) and isinstance(v, (int, float)):
            if best is None or v < best[0]:
                best = (v, k.split(":", 1)[1])
    return best


def slack(m, kind):
    w = worst(m, kind)
    return "%+.2f (%s)" % w if w else "-"


def num(v, fmt="%d"):
    if not isinstance(v, (int, float)):
        return "-"
    return fmt.format(int(v)) if fmt == "{:,}" else fmt % v


def die(m):
    bb = str(m.get("design__die__bbox", "")).split()
    return "%g x %g" % (float(bb[2]) - float(bb[0]), float(bb[3]) - float(bb[1])) if len(bb) == 4 else "-"


def rows():
    for d in ORDER:
        m = load("designs/%s/output/metrics.json" % d)
        if m:
            yield d, m, load("designs/%s/output/resources.json" % d), load("designs/%s/config.json" % d)


def table(cols, body):
    out = ["| " + " | ".join(cols) + " |", "|" + "---|" * len(cols)]
    out += ["| " + " | ".join(str(x) for x in r) + " |" for r in body]
    return "\n".join(out)


def signoff():
    body = []
    for d, m, r, _ in rows():
        g = lambda k: m.get(k, "-")
        body.append(["[%s](designs/%s/NOTES.md)" % (d, d), num(g("design__instance__count__stdcell"), "{:,}"),
                     num(g("design__instance__count__class:sequential_cell")), die(m),
                     slack(m, "setup"), slack(m, "hold"),
                     "%s/%s/%s/%s" % (g("magic__drc_error__count"), g("design__lvs_error__count"), g("design__xor_difference__count"),
                                      g("route__antenna_violation__count")),
                     "%s/%s/%s" % (g("design__max_slew_violation__count"), g("design__max_cap_violation__count"),
                                   g("design__max_fanout_violation__count")),
                     num(r.get("wall_s_total")), num(r.get("container_peak_mem_gb"), "%.3f")])
    return table(["design", "std cells (incl. tap)", "flip-flops", "die um", "worst setup ns (corner)", "worst hold ns (corner)",
                  "DRC/LVS/XOR/antenna", "slew/cap/fanout viol.", "flow s", "peak GB"], body)


def budget():
    body = []
    for d, m, r, c in rows():
        g = lambda k: m.get(k)
        pw = g("power__total")
        body.append(["%s" % d, num(c.get("CLOCK_PERIOD"), "%g"), num(g("design__instance__area__stdcell"), "%.0f"),
                     num(100 * g("design__instance__utilization"), "%.1f") if isinstance(g("design__instance__utilization"), (int, float)) else "-",
                     num(pw * 1e6, "%.1f") if isinstance(pw, (int, float)) else "-",
                     num(g("design__instance__count__class:clock_buffer")), num(g("route__wirelength"), "%d")])
    return table(["design", "clock period ns", "std-cell area um2 (excl. fill)", "utilisation %", "total power uW (nom_tt)", "clock buffers", "routed wire um"], body)


TABLES = {"signoff": signoff, "budget": budget}


def main():
    text = open(README).read()
    new = text
    for name, fn in TABLES.items():
        pat = re.compile(r"(<!-- results:begin %s -->\n).*?(<!-- results:end %s -->)" % (name, name), re.S)
        if not pat.search(new):
            sys.exit("tables.py: README.md has no <!-- results:begin %s --> / <!-- results:end %s --> block" % (name, name))
        new = pat.sub(lambda mo: mo.group(1) + fn() + "\n" + mo.group(2), new)
    if "--check" in sys.argv:
        if new != text:
            print("tables.py: README.md results tables are out of date (run make table)")
            sys.exit(1)
        print("tables.py: README.md results tables are up to date")
        return
    if new != text:
        open(README, "w").write(new)
    print("tables.py: %d tables, %d designs, README.md %s" % (len(TABLES), len(list(rows())), "updated" if new != text else "unchanged"))


if __name__ == "__main__":
    main()
