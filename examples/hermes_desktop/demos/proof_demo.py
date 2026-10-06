#!/usr/bin/env python3
"""Demo 8 (proof): narrated acts that show the Hermes chip agent runs locally, ties answers to this repo and shows its context.
  python3 examples/hermes_desktop/demos/proof_demo.py [--pace 6] [--timeout 300]       (or: make demo-proof)
Acts: 1 proof_local (sockets, ollama ps, offline env, outbound) and the same facts by hand with lsof; 2 a repo question in a real
Open WebUI chat, with the receipt the filter appends, then shasum -a 256 and git rev-parse HEAD run here and compared with the
receipt; 3 a memory note via remember and the same question again: the receipt's memory entry count goes up (context is read live);
4 show_context; 5 the airplane-mode check (suggested, never done by this script: it changes no network setting).
Each turn is one saved Open WebUI chat (title "Demo 8 proof: ...") so the answer, receipt and sources can be opened in the UI.
The transcript is written to examples/hermes_desktop/demos/proof.md. Needs Open WebUI and the tool server up (make hermes).
Exit: 0 all checks matched, 1 a check failed, 2 services unreachable. Standard library only.
Docs: docs/HERMES_DESKTOP.md (section "Proof: local, repo, context"), examples/hermes_desktop/demos/PROOF_DEMO.md
Tests: tests/tools/test_proof.py
"""
import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.request
import uuid

HERE = os.path.dirname(os.path.abspath(__file__))
HD = os.path.dirname(HERE)
REPO = os.path.abspath(os.path.join(HD, "..", ".."))
WEBUI = os.environ.get("WEBUI_URL", "http://127.0.0.1:8080")
TOOLS = os.environ.get("TOOLS_URL", "http://127.0.0.1:%s" % os.environ.get("CHIP_TOOLS_PORT", "8770"))
PRESET = "hermes-chip-agent"
QUESTION = ("Call the read_metrics tool for design kv_attn_n8 with keys design__instance__count__class:sequential_cell "
            "and tell me the flip-flop count.")
METRICS = "designs/kv_attn_n8/output/metrics.json"
RECEIPT_RE = re.compile(r"\n*---\n\*\*Receipt\*\*.*\Z", re.S)


def http(base, path, body=None, token=None, timeout=60):
    req = urllib.request.Request(base + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    if token:
        req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read() or b"null")


def narrate(i, n, text, pace):
    print("\n=== Act %d/%d: %s" % (i, n, text), flush=True)
    if pace > 0:
        time.sleep(pace)


def chat_turn(token, title, text, timeout):
    """One question in a new saved chat; waits for the answer (the outlet filter appends the receipt). Returns (answer, receipt, chat_id)."""
    uid, aid = str(uuid.uuid4()), str(uuid.uuid4())
    now = int(time.time())
    msgs = {uid: {"id": uid, "parentId": None, "childrenIds": [aid], "role": "user", "content": text, "timestamp": now, "models": [PRESET]},
            aid: {"id": aid, "parentId": uid, "childrenIds": [], "role": "assistant", "content": "", "model": PRESET, "timestamp": now}}
    chat = {"title": title, "models": [PRESET], "history": {"messages": msgs, "currentId": aid}, "messages": [msgs[uid], msgs[aid]],
            "tags": ["demo"], "timestamp": now * 1000}
    cid = http(WEBUI, "/api/v1/chats/new", {"chat": chat}, token)["id"]
    http(WEBUI, "/api/chat/completions", {"model": PRESET, "stream": False, "tool_ids": ["server:chip"], "chat_id": cid, "id": aid,
                                          "session_id": "demo-" + cid[:8], "parent_id": uid,
                                          "messages": [{"role": "user", "content": text}]}, token, timeout=60)
    t0, content = time.time(), ""
    while time.time() - t0 < timeout:
        time.sleep(2)
        m = http(WEBUI, "/api/v1/chats/" + cid, token=token)["chat"]["history"]["messages"].get(aid, {})
        content = m.get("content") or ""
        if m.get("done") and ("Receipt" in content or time.time() - t0 > 20):
            break
    mo = RECEIPT_RE.search(content)
    return (content[:mo.start()] if mo else content).strip(), (mo.group(0).strip() if mo else ""), cid


def run(cmd):
    """Run a shell command in the repo root; return (printed command line, output)."""
    p = subprocess.run(cmd, shell=True, cwd=REPO, capture_output=True, text=True, timeout=60)
    return "$ " + cmd, (p.stdout + p.stderr).strip()


def receipt_values(receipt):
    """(repo commit short, {path: sha8}, memory entries) parsed from a receipt block."""
    c = re.search(r"commit ([0-9a-f]{7})", receipt)
    files = dict(re.findall(r"read (\S+?) sha256 ([0-9a-f]{8})", receipt))
    files.update({p: s for p, s in re.findall(r"(\S+?) sha256 ([0-9a-f]{8})", receipt) if "/" in p or "." in p})
    m = re.search(r"memory digest (\d+) entries", receipt)
    return (c.group(1) if c else None), files, (int(m.group(1)) if m else None)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--pace", type=float, default=6.0, help="seconds to pause after each narration line (default 6; 0 = none)")
    ap.add_argument("--timeout", type=int, default=300, help="seconds per chat turn")
    a = ap.parse_args(argv)
    try:
        http(TOOLS, "/health", {}, timeout=5)
        http(WEBUI, "/health", timeout=5)
        token = http(WEBUI, "/api/v1/auths/signin", {"email": "admin@localhost", "password": "x"})["token"]
    except Exception as e:  # noqa: BLE001
        print("services not reachable (%s): run `make hermes` first" % e, file=sys.stderr)
        return 2
    t0, out, fails = time.time(), [], []
    N = 5

    def say(text=""):
        print(text, flush=True)
        out.append(text)

    # Act 1: local
    narrate(1, N, "Claim 1: everything runs on this machine. The proof_local tool measures it right now.", a.pace)
    r = http(TOOLS, "/proof_local", {}, timeout=60)
    say(r["text"])
    say("\nThe same by hand (any terminal):")
    for cmd in ("ollama ps", "lsof -nP -iTCP -sTCP:LISTEN | grep -E ':(11434|8770|8080) '"):
        c, o = run(cmd)
        say(c + "\n" + o)
    if not r["verdict"].startswith("LOCAL"):
        fails.append("proof_local verdict: " + r["verdict"])

    # Act 2: repo question + receipt + verification
    narrate(2, N, "Claim 2: answers are tied to this repo. Ask a repo question in a real chat; the receipt under the answer names the file and its sha256.", a.pace)
    say("you: " + QUESTION)
    ans1, rec1, cid1 = chat_turn(token, "Demo 8 proof: repo question", QUESTION, a.timeout)
    say("hermes: " + ans1 + "\n\n" + rec1)
    commit, files, mem1 = receipt_values(rec1)
    say("\nNow verify the receipt yourself, with plain terminal commands:")
    c1, o1 = run("shasum -a 256 " + METRICS)
    c2, o2 = run("git rev-parse HEAD")
    say(c1 + "\n" + o1)
    say(c2 + "\n" + o2)
    real_sha = hashlib.sha256(open(os.path.join(REPO, METRICS), "rb").read()).hexdigest()
    ok_file = files.get(METRICS) == real_sha[:8]
    ok_commit = bool(commit) and o2.startswith(commit)
    say("receipt file sha256 %s vs shasum %s: %s" % (files.get(METRICS), real_sha[:8], "MATCH" if ok_file else "NO MATCH"))
    say("receipt commit %s vs git rev-parse HEAD %s: %s" % (commit, o2[:7], "MATCH" if ok_commit else "NO MATCH"))
    if not (ok_file and ok_commit):
        fails.append("receipt does not match shasum/git")

    # Act 3: memory is read live
    narrate(3, N, "Claim 3: the context is read live. Save a note with remember, then ask again: the memory digest gains an entry.", a.pace)
    marker = "proof demo marker %s" % time.strftime("%H%M%S")
    q3 = "Call the remember tool with note %s and topic proof." % marker
    say("you: " + q3)
    ans3, rec3, _ = chat_turn(token, "Demo 8 proof: remember", q3, a.timeout)
    say("hermes: " + ans3)
    q4 = "Call the recall tool with query proof demo marker and tell me what it returns."
    say("\nyou (again): " + q4)
    ans4, rec4, cid4 = chat_turn(token, "Demo 8 proof: ask again", q4, a.timeout)
    say("hermes: " + ans4 + "\n\n" + rec4)
    _, _, mem4 = receipt_values(rec4)
    say("\nmemory digest entries in the receipts: before the note %s, after %s" % (mem1, mem4))
    if mem1 is None or mem4 is None or mem4 <= mem1:
        fails.append("memory entry count did not go up (%s -> %s)" % (mem1, mem4))
    note_id = None
    try:
        notes = http(TOOLS, "/recall", {"query": marker, "limit": 3}).get("notes", [])
        note_id = next((n["id"] for n in notes if marker in n.get("text", "")), None)
    except Exception:  # noqa: BLE001
        pass
    if note_id:
        http(TOOLS, "/forget", {"id": note_id})
        say("(demo note %s removed again with forget, so your memory stays as it was)" % note_id)

    # Act 4: show_context
    narrate(4, N, "Claim 3, in detail: exactly what the model was sent for the last turn.", a.pace)
    sc = http(TOOLS, "/show_context", {}, timeout=30)
    say(sc["text"])

    # Act 5: airplane mode
    narrate(5, N, "The strongest check is yours: turn Wi-Fi off (airplane mode) and run demo 2 (make demo-kv). It still works, "
                  "because nothing here needs the network. This script never changes network settings.", a.pace)
    say("Result: %s in %.0f s" % ("ALL CHECKS MATCHED" if not fails else "FAILED: " + "; ".join(fails), time.time() - t0))
    path = os.path.join(HERE, "proof.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("# Demo 8: Proof: local, repo, context\n\nGenerated by `python3 examples/hermes_desktop/demos/proof_demo.py` on %s. Total %.0f s. "
                "Chats: Demo 8 proof (saved in Open WebUI, tag demo).\n\n```\n%s\n```\n" % (time.strftime("%Y-%m-%d"), time.time() - t0, "\n".join(out)))
    print("transcript %s; chat %s/c/%s" % (os.path.relpath(path, REPO), WEBUI, cid4))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
