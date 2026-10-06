#!/usr/bin/env python3
"""classify_slew.py <design> [corner] [run_dir] -- split a run's max-slew violations into port-driven and internal.

For each violating pin in the post-route <corner>/checks.rpt ("max slew" table of
NN-openroad-stapostpnr), find the net the pin sits on in final/nl/*.nl.v and its driver:
  port-driven  the net is a top-level input port (environment-limited: the Caravel SDC sets the
               input transition, resizing cells behind the port cannot fix it)
  internal     the net is driven by a cell output (repair could in principle fix it)
Defaults: corner max_ss_100C_1v60, newest complete run of the design under designs/<design>/runs/.
Prints the two counts, the driver cell types of the internal ones, and the checks.rpt total.
"""
import glob, os, re, sys, collections

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))


def newest_run(design):
    runs = sorted(glob.glob(os.path.join(REPO, "designs", design, "runs", "RUN_*")))
    for r in reversed(runs):
        if glob.glob(r + "/final/nl/*.nl.v") and glob.glob(r + "/*-openroad-stapostpnr"):
            return r
    sys.exit("no complete run under designs/%s/runs/" % design)


def parse_netlist(path):
    txt = open(path).read()
    inputs = set()
    for m in re.finditer(r"^\s*input\s+(?:\[[^\]]*\]\s*)?(\w+)\s*;", txt, re.M):
        w = re.search(r"^\s*input\s+\[(\d+):(\d+)\]\s*" + m.group(1) + r"\s*;", txt, re.M)
        if w:
            hi, lo = int(w.group(1)), int(w.group(2))
            inputs.update("%s[%d]" % (m.group(1), i) for i in range(min(hi, lo), max(hi, lo) + 1))
        else:
            inputs.add(m.group(1))
    pins = {}      # (inst, pin) -> net
    cells = {}     # inst -> cell type
    drivers = {}   # net -> (inst, cell)
    outpins = {"X", "Y", "Q", "Q_N", "COUT", "SUM", "HI", "LO", "GCLK", "Z"}
    for m in re.finditer(r"^\s*(\w+)\s+(\S+)\s*\((.*?)\);", txt, re.M | re.S):
        cell, inst, body = m.groups()
        if cell in ("module", "input", "output", "wire", "assign"):
            continue
        cells[inst] = cell
        for p, n in re.findall(r"\.(\w+)\(\s*([^)]*?)\s*\)", body):
            pins[(inst, p)] = n
            if p in outpins:
                drivers[n] = (inst, cell)
    return inputs, pins, cells, drivers


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    design = sys.argv[1]
    corner = sys.argv[2] if len(sys.argv) > 2 else "max_ss_100C_1v60"
    run = sys.argv[3] if len(sys.argv) > 3 else newest_run(design)
    rpt = glob.glob("%s/*-openroad-stapostpnr/%s/checks.rpt" % (run, corner))
    nl = glob.glob(run + "/final/nl/*.nl.v")
    if not rpt or not nl:
        sys.exit("missing checks.rpt or netlist in " + run)
    sec = re.search(r"^max slew\n(.*?)(?=^max (?:fanout|cap)|^=====|\Z)", open(rpt[0]).read(), re.M | re.S)
    viol = re.findall(r"^(\S+)/(\w+)\s+[\d.]+\s+[\d.]+\s+-[\d.]+ \(VIOLATED\)", sec.group(1) if sec else "", re.M)
    inputs, pins, cells, drivers = parse_netlist(nl[0])
    port, internal, unknown = 0, collections.Counter(), 0
    for inst, p in viol:
        net = pins.get((inst, p))
        if net is None:
            unknown += 1
        elif net in inputs:
            port += 1
        elif net in drivers:
            internal[drivers[net][1].replace("sky130_fd_sc_hd__", "")] += 1
        else:
            unknown += 1
    total = re.search(r"max slew violation count (\d+)", open(rpt[0]).read())
    print("run %s corner %s" % (os.path.relpath(run, REPO), corner))
    print("listed violating pins: %d (checks.rpt total count: %s)" % (len(viol), total.group(1) if total else "?"))
    print("port-driven (environment-limited): %d" % port)
    print("internal (cell-driven): %d  %s" % (sum(internal.values()), dict(internal)))
    if unknown:
        print("unclassified (pin or driver not found in netlist): %d" % unknown)


main()
