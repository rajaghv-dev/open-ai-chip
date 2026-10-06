#!/usr/bin/env python3
"""make_master_prompt.py -- generate tools/prompts/master_prompt.txt (the repo "master prompt" for Hermes / Open WebUI).
Docs: docs/HERMES_AGENT.md, docs/HERMES_DESKTOP.md

Template: tools/prompts/master_prompt.template.txt (hand-written; '#' lines are dropped; {{N_DESIGNS}}, {{LIBRELANE}},
{{CLOCK_NS}}, {{CLOCK_MHZ}}, {{FAMILIES}}, {{CONTEXT}} are filled). Facts come from the repo, so the prompt does not go stale:
design list and count = Makefile ALL_DESIGNS (scripts/lib/repo.py), families = the README "The designs" table,
LibreLane version = versions.lock, clock = designs/kv_attn_n8/config.json CLOCK_PERIOD. Standard library only.

  python3 scripts/docs/make_master_prompt.py           write the file, print the size estimate (4 chars/token)
  python3 scripts/docs/make_master_prompt.py --check   exit 1 if the file is not what the generator produces
  python3 scripts/docs/make_master_prompt.py --print   write to stdout
{{CONTEXT}} = the block between master:begin/master:end in docs/AGENT_CONTEXT.md (curated facts, rules, skills).
Budget: <= 1400 tokens (8B model, num_ctx 8192; tool schemas and the user's turn need the rest); the script fails above it.
"""
import os, re, sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "lib"))
import repo  # noqa: E402

TEMPLATE = os.path.join(repo.REPO, "tools", "prompts", "master_prompt.template.txt")
OUT = os.path.join(repo.REPO, "tools", "prompts", "master_prompt.txt")
BUDGET_TOKENS = 1400
CONTEXT_DOC = os.path.join(repo.REPO, "docs", "AGENT_CONTEXT.md")
# Short role per design where the name is not self-explanatory (hand-written; a missing entry just prints the bare name).
ROLE = {
    "user_proj_example": "Caravel template counter", "vision_all_lit": "vision engine", "vision_block": "vision engine",
    "text_sentiment": "text engine", "audio_pitch": "pitch", "audio_onset": "onset", "image_text_match": "image vs text",
    "prec_bin": "1-bit", "prec_tern": "ternary", "tiny_ai_core": "Wishbone engine core",
    "soc_image_text_match": "PicoRV32 SoC", "soc_kv_attn_n8": "SoC around kv_attn_n8",
}


def tokens(text):
    return (len(text) + 3) // 4


def families():
    """[(family, [design, ...])] from the README design table, checked against Makefile ALL_DESIGNS."""
    text = open(os.path.join(repo.REPO, "README.md"), encoding="utf-8").read()
    sec = text.split("## The designs", 1)[1].split("\n## ", 1)[0]
    fams = []
    for m in re.finditer(r"^\|\s*([^|]+?)\s*\|\s*(\[.*?)\s*\|\s*$", sec, re.M):
        names = re.findall(r"\[([A-Za-z0-9_]+)\]\(designs/", m.group(2))
        if names:
            fams.append((m.group(1), names))
    listed = sorted(n for _, ns in fams for n in ns)
    if listed != sorted(repo.all_designs()):
        raise SystemExit("README design table and Makefile ALL_DESIGNS differ: %s" %
                         sorted(set(listed) ^ set(repo.all_designs())))
    return fams


def context_block():
    m = re.search(r"<!-- master:begin -->\n(.*?)<!-- master:end -->", open(CONTEXT_DOC, encoding="utf-8").read(), re.S)
    if not m:
        raise SystemExit("docs/AGENT_CONTEXT.md: master:begin/master:end block missing")
    return m.group(1).strip("\n")


def build():
    tpl = open(TEMPLATE, encoding="utf-8").read()
    body = "\n".join(l for l in tpl.split("\n") if not l.startswith("#")).strip("\n") + "\n"
    lib = re.search(r"^LIBRELANE_VERSION=(\S+)", open(os.path.join(repo.REPO, "versions.lock")).read(), re.M).group(1)
    ns = float(repo.config("kv_attn_n8")["CLOCK_PERIOD"])
    fam = "\n".join("- %s: %s" % (f, ", ".join(n + (" (%s)" % ROLE[n] if n in ROLE else "") for n in ns_))
                    for f, ns_ in families())
    vals = {"N_DESIGNS": str(len(repo.all_designs())), "LIBRELANE": lib, "CLOCK_NS": "%g" % ns,
            "CLOCK_MHZ": "%g" % (1000.0 / ns), "FAMILIES": fam,
            "CONTEXT": context_block()}
    for k, v in vals.items():
        body = body.replace("{{%s}}" % k, v)
    left = re.findall(r"\{\{\w+\}\}", body)
    if left:
        raise SystemExit("unfilled placeholders: %s" % left)
    return body


def main(argv):
    out = build()
    if "--print" in argv:
        sys.stdout.write(out)
        return 0
    if tokens(out) > BUDGET_TOKENS:
        print("master prompt too long: ~%d tokens > %d" % (tokens(out), BUDGET_TOKENS))
        return 1
    if "--check" in argv:
        have = open(OUT, encoding="utf-8").read() if os.path.exists(OUT) else None
        if have != out:
            print("tools/prompts/master_prompt.txt is stale: run `make master-prompt`")
            return 1
        print("master prompt up to date (~%d tokens)" % tokens(out))
        return 0
    with open(OUT, "w", encoding="utf-8") as f:
        f.write(out)
    print("wrote tools/prompts/master_prompt.txt: %d chars, ~%d tokens (4 chars/token, budget %d)" % (len(out), tokens(out), BUDGET_TOKENS))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
