#!/usr/bin/env python3
# Docs: docs/RESULTS.md
"""tiny_table.py <design> ... -- one comparison table of the tiny AI engines, from each design's committed evidence
(designs/<d>/output/metrics.json, resources.json) and its RTL simulation log (build/sim/<d>/sim.log)."""
import json, re, sys


def load(p):
    try:
        return json.load(open(p))
    except (OSError, ValueError):
        return {}


cols = ("design", "cases", "std cells", "flip-flops", "die um", "setup ns", "hold ns", "DRC/LVS/XOR/ant", "slew/cap", "flow s", "mem GB")
rows = []
for d in sys.argv[1:]:
    m = load("designs/%s/output/metrics.json" % d)
    r = load("designs/%s/output/resources.json" % d)
    try:
        cases = re.search(r"PASS \S+: (\d+) cases", open("build/sim/%s/sim.log" % d).read()).group(1)
    except (OSError, AttributeError):
        cases = "?"
    g = lambda k: m.get(k, "?")
    bb = str(g("design__die__bbox")).split()
    die = "%.0f x %.0f" % (float(bb[2]), float(bb[3])) if len(bb) == 4 else "?"
    f = lambda v: "%+.2f" % v if isinstance(v, (int, float)) else "?"
    rows.append((d, cases, g("design__instance__count__stdcell"), g("design__instance__count__class:sequential_cell"), die,
                 f(g("timing__setup__ws")), f(g("timing__hold__ws")),
                 "%s/%s/%s/%s" % (g("magic__drc_error__count"), g("design__lvs_error__count"), g("design__xor_difference__count"),
                                  g("route__antenna_violation__count")),
                 "%s/%s" % (g("design__max_slew_violation__count"), g("design__max_cap_violation__count")),
                 r.get("wall_s_total", "?"), r.get("container_peak_mem_gb", "?")))
print("| " + " | ".join(cols) + " |")
print("|" + "---|" * len(cols))
for row in rows:
    print("| " + " | ".join(str(x) for x in row) + " |")
