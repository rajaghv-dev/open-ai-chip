#!/usr/bin/env python3
"""Check designs/<d>/NOTES.md: required headings (same logic as tests/run_tests.sh "== notes") + mermaid lint.
Usage: python3 .claude/skills/write-design-notes/check_notes.py designs/<d>/NOTES.md [...]
Exit 1 on any problem."""
import re, sys

WANT = [("architecture",), ("data flow",), ("verification",), ("layout",), ("synthesis",), ("floorplan",), ("placement",),
        ("clock tree", "cts"), ("routing",), ("timing",), ("drc",), ("lvs",), ("power", "ir"), ("antenna",), ("run time",),
        ("reproduce",), ("intuitions and insights",)]          # keep in sync with tests/run_tests.sh

def headings(text):
    return [m.group(1).strip().lower() for m in re.finditer(r"^#{2,3}\s+(.+?)\s*$", text, re.M)]

def check_headings(text):
    heads = headings(text)
    miss = ["/".join(w) for w in WANT if not any(h.startswith(x) for h in heads for x in w)]
    return ["missing headings: " + ", ".join(miss)] if miss else []

# node: id followed by [..], (..), {..}, ((..)), [[..]], [(..)]; the label is unsafe when unquoted and holds special chars
NODE = re.compile(r'(?<![\w"])([A-Za-z_]\w*)\s*(\[\[|\[\(|\(\(|\[|\(|\{)("[^"]*"|[^"\]\)\}]*)(\]\]|\)\]|\)\)|\]|\)|\})')
SPECIAL = re.compile(r'[()\[\]{}/,:;<>&#%\'|+*=@\\]')
KW = {"subgraph", "end", "flowchart", "graph", "direction", "classDef", "class", "style", "linkStyle", "click"}

def lint_block(src, start_line):
    errs, seen = [], {}
    for i, line in enumerate(src.splitlines()):
        ln = start_line + i
        s = re.sub(r'\|[^|]*\|', '', line) if '-->' in line or '---' in line or '-.' in line else line   # drop |edge labels| (checked below)
        for m in re.finditer(r'\|([^|"]*)\|', line):
            if SPECIAL.search(m.group(1)):
                errs.append(f"line {ln}: unquoted edge label |{m.group(1)}| has special characters; write |\"...\"|")
        sg = re.match(r'\s*subgraph\s+(\w+)\s*\[(.*)\]\s*$', line)
        if sg:
            lab = sg.group(2).strip()
            if not (lab.startswith('"') and lab.endswith('"')) and SPECIAL.search(lab):
                errs.append(f"line {ln}: subgraph label unquoted: {lab}")
            if sg.group(1) in seen: errs.append(f"line {ln}: id {sg.group(1)} defined twice (first at line {seen[sg.group(1)][0]})")
            seen[sg.group(1)] = (ln, lab)
            continue
        for m in NODE.finditer(s):
            nid, lab = m.group(1), m.group(3).strip()
            if nid in KW: continue
            if not (lab.startswith('"') and lab.endswith('"')) and SPECIAL.search(lab):
                errs.append(f"line {ln}: node {nid} label unquoted with special characters: {lab[:40]}")
            if nid in seen and seen[nid][1] != lab:
                errs.append(f"line {ln}: id {nid} defined again with a different label (first at line {seen[nid][0]})")
            seen.setdefault(nid, (ln, lab))
        if line.count('"') % 2: errs.append(f"line {ln}: odd number of double quotes")
    return errs

def check_mermaid(text):
    errs = []
    for m in re.finditer(r"```mermaid\n(.*?)```", text, re.S):
        start = text[:m.start()].count("\n") + 2
        if not re.match(r"\s*(flowchart|graph)\b", m.group(1)): continue   # lint node labels of flowcharts only
        errs += lint_block(m.group(1), start)
    return errs

def main():
    bad = 0
    for p in sys.argv[1:]:
        t = open(p).read()
        errs = check_headings(t) + check_mermaid(t)
        n = len(re.findall(r"```mermaid", t))
        print(("FAIL " if errs else "ok   ") + f"{p} ({n} mermaid blocks)")
        for e in errs: print("   " + e)
        bad += bool(errs)
    sys.exit(1 if bad else 0)

if __name__ == "__main__":
    main()
