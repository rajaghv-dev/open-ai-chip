#!/usr/bin/env python3
# Docs: .claude/skills/harden-design/SKILL.md
"""summary.py <design> <stages.txt> <total seconds> -- the five-line summary printed by `make flow-all`.
Numbers come only from build/sim/<design>/sim.log and designs/<design>/output/{metrics,resources}.json."""
import json, re, sys

d, st, total = sys.argv[1], sys.argv[2], int(sys.argv[3])
S = {}
for l in open(st):
    n, r, t = l.split()
    S[n] = (r, int(t))


def t(n):
    return "%s %ss" % S[n] if n in S else "not run"


def load(p):
    try:
        return json.load(open(p))
    except (OSError, ValueError):
        return {}


try:
    sim = re.findall(r"^PASS.*", open("build/sim/%s/sim.log" % d).read(), re.M)
except OSError:
    sim = []
m = load("designs/%s/output/metrics.json" % d)
res = load("designs/%s/output/resources.json" % d)
g = lambda k: m.get(k, "?")
print("1 simulate : %s -- %s" % (t("simulate"), sim[0] if sim else "no PASS line"))
print("2 gds      : %s -- %s std cells, die %s um2, wall %ss, peak mem %s GB" % (
    t("gds"), g("design__instance__count__stdcell"), g("design__die__area"),
    res.get("wall_s_total", "?"), res.get("container_peak_mem_gb", "?")))
print("3 check    : %s -- DRC/LVS/XOR/antenna, slack at all corners, no logic lost (scripts/flow/check_signoff.py)" % t("check"))
print("4 gate-lvl : synthesised %s, routed %s" % (t("gl_synth"), t("gl_final")))
print("5 collect  : %s -- designs/%s/output/, build/results/%s/ ; total %ss" % (t("collect"), d, d, total))
sys.exit(0 if S and all(r == "PASS" for r, _ in S.values()) and len(S) == 6 else 1)
