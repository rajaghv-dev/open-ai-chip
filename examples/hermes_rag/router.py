#!/usr/bin/env python3
"""Deterministic question router (no LLM, standard library): doc | metric | unknown.

  python3 examples/hermes_rag/router.py "Why does kv_attn_n8_int4 have more flip-flops?"   -> doc

doc     why / explain / what fixed, caused, limits / reason / lesson / knee ... : the answer is written text in NOTES.md,
        README.md or docs/, so the harness retrieves BEFORE the first model turn (rag_agent.py --router).
metric  a number or pass/fail that read_metrics, compare_designs, pick_extreme, signoff_summary ... answer.
unknown anything else (including questions that name neither): left to the model, which may still call search_docs.
Doc patterns are checked first: "Why did the std cell count halve?" mentions a metric but asks for a reason.
"""
import re, sys

DOC_RE = re.compile(
    r"\bwhy\b|\bexplain\b|\bexplanation\b|\breasons?\b|\brationale\b|\broot cause\b|\blessons?\b|\binsights?\b|\btrade-?offs?\b"
    r"|\bwhat\s+(?:fixed|caused|causes|limits|limited|limit|solved|resolved|went wrong|changed)\b"
    r"|\bwhat\s+(?:is|was|were|are)\s+(?:the\s+)?(?:cause|fix|limit|limiting|reason|problem|lesson)"
    r"|\bhow\s+(?:did|does|do|was|were|is|are)\b.*\b(?:fix|solve|resolve|avoid|work|handle|differ)\w*"
    r"|\bwhich\b.*\b(?:knee|sweet spot|best trade|trade-?off)\b|\bwhat\s+did\b.*\blearn", re.I)

METRIC_NOUN = (r"(?:std\.?\s*cells?|standard[- ]cells?|cells?|flip-?flops?|sequential|pins?|ports?|instances?|violations?|checks?|errors?|macros?|"
               r"nets?|wires?|wirelength|vias|area|slack|power|utili[sz]ation|die|core|size|drc|lvs|antenna|clean|timing|corner|"
               r"count|ratio|difference|latency|frequency|price|yield)")
METRIC_RE = re.compile(
    r"\bhow\s+(?:many|much)\b.*\b" + METRIC_NOUN + r"\b|\bhow\s+(?:many|much)\s+(?:more|fewer|less)\b"
    r"|\b(?:worst|best|smallest|largest|biggest|most|least|fewest|highest|lowest)\b.*\b" + METRIC_NOUN + r"\b"
    r"|\bwhich\b.*\b(?:design|variant|format|macro)\b.*\b(?:has|have|is|are)\b"
    r"|\bwhat\s+is\s+the\s+" + METRIC_NOUN + r"\b|\bwhat\s+is\s+the\s+(?:worst|total|ratio)\b"
    r"|\bis\s+\S+\s+(?:drc|lvs)\b|\bratio\b|\bcompare\b|\bdie size\b", re.I)


def classify(question):
    q = question.strip()
    if DOC_RE.search(q):
        return "doc"
    if METRIC_RE.search(q):
        return "metric"
    return "unknown"


if __name__ == "__main__":
    print(classify(" ".join(sys.argv[1:])))
