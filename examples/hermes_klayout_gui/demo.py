#!/usr/bin/env python3
"""Educational demo: 5 scripted scenarios on real designs, offscreen KLayout view.

  build/agent/venv/bin/python examples/hermes_klayout_gui/demo.py --dry-run   # scripted calls, no Ollama
  build/agent/venv/bin/python examples/hermes_klayout_gui/demo.py --live      # local hermes3:8b via Ollama
Writes examples/hermes_klayout_gui/transcript_dry_run.txt or transcript_live.txt. PNGs go to build/agent/klayout_gui/
(git-ignored); three representative ones are copied to examples/hermes_klayout_gui/img/ by --save-img.
"""
import argparse
import os
import shutil
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import agent  # noqa: E402
import view_api as va  # noqa: E402

IMG_DIR = os.path.join(HERE, "img")
KEEP = {"wrapper_met45_mprj": "wrapper_met4_met5_mprj.png", "kv8_li1_met1_corner": "kv_attn_n8_li1_met1_corner.png",
        "tiny_demo_markers": "tiny_ai_core_demo_markers.png"}


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--dry-run", action="store_true")
    g.add_argument("--live", action="store_true")
    ap.add_argument("--backend", default="offscreen", choices=["offscreen", "live"])
    ap.add_argument("--save-img", action="store_true", help="copy 3 representative PNGs into examples/hermes_klayout_gui/img/")
    a = ap.parse_args()
    live = a.live
    name = "transcript_live.txt" if live else "transcript_dry_run.txt"
    path = os.path.join(HERE, name)
    backend = agent.make_backend(a.backend)
    rows = []
    with open(path, "w") as f:
        emit = agent.Transcript(f)
        emit("Hermes + KLayout view demo (%s model, %s backend)" % ("live hermes3:8b" if live else "scripted", a.backend))
        for i, sc in enumerate(agent.SCENARIOS):
            emit("")
            emit("=== scenario %d/%d: %s ===" % (i + 1, len(agent.SCENARIOS), sc["id"]))
            model = agent.live_model if live else agent.Scripted(sc["script"])
            try:
                r = agent.run_episode(sc["request"], backend, model, emit=emit)
            except OSError as e:
                emit("Ollama not reachable (%s); stopping" % e)
                break
            ok = agent.seq_ok(r["calls"], sc["expect"])
            rows.append((sc["id"], ok, r))
            emit("CHECK    expected %s -> %s" % (" > ".join(sc["expect"]), "PASS" if ok else "FAIL"))
            if a.save_img and sc["id"] in KEEP and r["pngs"]:
                os.makedirs(IMG_DIR, exist_ok=True)
                dst = os.path.join(IMG_DIR, KEEP[sc["id"]])
                shutil.copy(os.path.join(va.REPO, r["pngs"][-1]), dst)
                emit("IMG      examples/hermes_klayout_gui/img/%s (%d bytes)" % (KEEP[sc["id"]], os.path.getsize(dst)))
        emit("")
        emit("SUMMARY  %d/%d scenarios with the expected tool sequence" % (sum(1 for _, ok, _ in rows if ok), len(rows)))
        for sid, ok, r in rows:
            emit("  %-22s %s  %6.1fs  %d calls  router=%s rejected=%d" % (sid, "PASS" if ok else "FAIL", r["seconds"], len(r["calls"]),
                                                                       r["router_used"], r["rejected"]))
    backend.close()
    print("transcript:", va.rel(path))


if __name__ == "__main__":
    main()
