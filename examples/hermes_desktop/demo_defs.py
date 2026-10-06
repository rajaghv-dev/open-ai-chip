#!/usr/bin/env python3
"""Demo definitions for the Hermes chip agent: plain data, standard library only, shared by demos.py (the runner) and
tool_server/experiments_tools.py (list_demos, demo_steps).
Each demo has a number, a one-word name, a title, a blurb, the time it takes, whether it runs a physical flow, and ordered
steps. A step is one act: `say` is the narration a presenter reads, `tool` + `args` is the tool call the agent makes,
`ask` is the chat message that makes the model call it, `look` is what to point at in the answer. A step with
`confirm: True` is a run: the tool answers with a confirm_id and nothing starts until the user writes "yes, run <id>".
Demo 7 (gui) is run by demos/gui_demo.py (owned by the GUI work); it is listed here so the menu is complete.
Demo 8 (proof) has steps (the chat version: /demo-proof) and a runner, demos/proof_demo.py, that also prints and runs the terminal
verification commands (shasum -a 256, git rev-parse HEAD); `make demo-proof` runs the runner.
Docs: docs/HERMES_DESKTOP.md (section "Experiments and demos")
Tests: tests/tools/test_experiments.py
"""

D_KV = "kv_attn_n8"

DEMOS = [
    {"num": 1, "name": "precision", "title": "Precision study tour", "minutes": 1.0, "physical": False,
     "blurb": "One neuron built in 7 number formats: why ternary and int4 win, and what fp16 looks like next to bin.",
     "docs": "docs/PRECISION_STUDY.md",
     "steps": [
         {"say": "Seven copies of the same 9-input neuron, one per number format, each taken to clean GDSII.",
          "tool": "experiment_result", "args": {"id": "precision"},
          "ask": "Call the experiment_result tool with id precision and show me the table.",
          "look": "accuracy column: 94.0 to 94.25 percent for tern, int4, int8, fp8, fp16, bf16; cells grow from 199 to 1932."},
         {"say": "Why do ternary and int4 win? Same accuracy, a fraction of the area. Here is the written reason.",
          "tool": "explain", "args": {"design": "prec_tern", "topic": "why ternary wins"},
          "ask": "Call the explain tool for design prec_tern and topic why ternary wins; quote what it returns.",
          "look": "the quoted NOTES.md paragraph and its file and line numbers."},
         {"say": "Now the pictures: the smallest layout (bin), then the biggest (fp16).",
          "tool": "klayout_view", "args": {"design": "prec_bin"},
          "ask": "Call the klayout_view tool to show prec_bin.", "look": "an 80 um die."},
         {"say": "fp16 on the same scale of ideas: a 220 um die and about ten times the cells.",
          "tool": "klayout_view", "args": {"design": "prec_fp16"},
          "ask": "Call the klayout_view tool to show prec_fp16.", "look": "the die is much larger and fuller."},
     ]},
    {"num": 2, "name": "kv", "title": "KV cache: prefill vs decode", "minutes": 0.5, "physical": False,
     "blurb": "Run RISC-V firmware against the KV-cache attention engine and see why decode is bus-bound.",
     "docs": "firmware/README.md",
     "steps": [
         {"say": "First a run: a PicoRV32 CPU talks to the KV-cache attention engine through a Wishbone adapter (about 15 s, no Docker).",
          "tool": "run_experiment", "args": {"id": "soc-kv"}, "confirm": True,
          "ask": "Call the run_experiment tool with id soc-kv.",
          "look": "the confirm step: nothing starts until you answer yes."},
         {"say": "The measured tables: prefill amortises the bus cost, decode pays a full round trip per token.",
          "tool": "experiment_result", "args": {"id": "soc-kv"},
          "ask": "Call the experiment_result tool with id soc-kv{job} and show the tables.",
          "look": "per-token prefill falls from 328 to about 91 cycles; decode stays at 670, of which 502 are reads."},
         {"say": "The same story at LLM scale: prefill is compute-bound, decode is memory-bound.",
          "tool": "search_docs", "args": {"query": "prefill compute-bound decode memory-bound", "k": 3},
          "ask": "Call the search_docs tool with query prefill compute-bound decode memory-bound.",
          "look": "docs/LLM_INFERENCE.md section 1."},
     ]},
    {"num": 3, "name": "rtl2gds", "title": "RTL to GDS live", "minutes": 3.0, "physical": True,
     "blurb": "A gated flow-all of vision_block (simulate, layout, signoff, gate level), then the summary and the picture.",
     "docs": "docs/RESULTS.md",
     "steps": [
         {"say": "The whole flow for one small design. This is a physical flow in Docker: about 1 to 3 minutes, one at a time.",
          "tool": "run_experiment", "args": {"id": "flow-all", "design": "vision_block"}, "confirm": True,
          "ask": "Call the run_experiment tool with id flow-all and design vision_block.",
          "look": "the confirm step and the exact make command."},
         {"say": "What the flow found: stages, key numbers, current or stale.",
          "tool": "run_summary", "args": {"design": "vision_block"},
          "ask": "Call the run_summary tool for design vision_block{job_ref} and summarize it.",
          "look": "DRC, LVS, antenna all clean; setup and hold slack."},
         {"say": "Rule-based suggestions: what a designer would look at next. It never advises loosening a constraint.",
          "tool": "suggest", "args": {"design": "vision_block"},
          "ask": "Call the suggest tool for design vision_block and list the suggestions.",
          "look": "utilisation, slack, what to read next."},
         {"say": "And the layout itself.",
          "tool": "klayout_view", "args": {"design": "vision_block"},
          "ask": "Call the klayout_view tool to show vision_block.", "look": "the placed and routed cells."},
     ]},
    {"num": 4, "name": "int4", "title": "Why int4 has more flip-flops", "minutes": 0.5, "physical": False,
     "blurb": "A surprise: halving the cache bits did not reduce flip-flops. The notes explain why.",
     "docs": "designs/kv_attn_n8_int4/NOTES.md",
     "steps": [
         {"say": "The family table: nominal cache bits against flip-flops actually built.",
          "tool": "experiment_result", "args": {"id": "kv-cache"},
          "ask": "Call the experiment_result tool with id kv-cache and show the table.",
          "look": "kv_attn_n8_int4: 256 nominal bits but 96 cache flops against 72 for kv_attn_n8."},
         {"say": "The explanation, quoted from the design notes.",
          "tool": "notes_section", "args": {"design": "kv_attn_n8_int4", "section": "Intuitions and insights", "item": 1},
          "ask": "Call the notes_section tool for design kv_attn_n8_int4, section Intuitions and insights, item 1; quote it.",
          "look": "the int8 baseline was already pruned: the ROM is constant, synthesis deleted the constant bits."},
     ]},
    {"num": 5, "name": "heatmaps", "title": "OpenROAD heat maps", "minutes": 0.5, "physical": False,
     "blurb": "What each OpenROAD engine saw on kv_attn_n8: placement density, routing congestion, IR drop.",
     "docs": "examples/openroad_gui/README.md",
     "steps": [
         {"say": "Placement (gpl): cells are charges, the engine spreads them until the density is even.",
          "tool": "engine_pictures", "args": {"design": D_KV, "view": "placement"},
          "ask": "Call the engine_pictures tool for design kv_attn_n8 and view placement.",
          "look": "a compact cloud, empty margin at the bottom."},
         {"say": "Global routing (grt): demand against capacity per tile; hot columns are the power stripes.",
          "tool": "engine_pictures", "args": {"design": D_KV, "view": "congestion"},
          "ask": "Call the engine_pictures tool for design kv_attn_n8 and view congestion.",
          "look": "no tile over 100 percent: the design routed clean."},
         {"say": "Power-grid analysis (psm): the voltage sag on vccd1. The numbers are microvolts.",
          "tool": "engine_pictures", "args": {"design": D_KV, "view": "ir"},
          "ask": "Call the engine_pictures tool for design kv_attn_n8 and view ir.",
          "look": "worst drop 2.16e-04 V; the colour scale is stretched."},
     ]},
    {"num": 6, "name": "soc", "title": "SoC: the bus dominates", "minutes": 0.7, "physical": False,
     "blurb": "RISC-V software against the accelerator on a PicoRV32 SoC: the accelerator computes in 6 to 15 clocks, the bus costs hundreds.",
     "docs": "firmware/README.md",
     "steps": [
         {"say": "A run: the CPU does the same tiny networks in software and through the accelerator (about 25 s, no Docker).",
          "tool": "run_experiment", "args": {"id": "soc-sim"}, "confirm": True,
          "ask": "Call the run_experiment tool with id soc-sim.", "look": "the confirm step."},
         {"say": "The cycle table: software against accelerator round trip.",
          "tool": "experiment_result", "args": {"id": "soc-sim"},
          "ask": "Call the experiment_result tool with id soc-sim{job} and show the table.",
          "look": "sw/accel is 0.3x, 1.8x, 0.7x: only the convolution wins; bus share around 89 percent."},
         {"say": "The lesson, from the project notes.",
          "tool": "search_docs", "args": {"query": "accelerator round trip dominated by bus writes", "k": 3},
          "ask": "Call the search_docs tool with query accelerator round trip dominated by bus writes.",
          "look": "moving data costs more than computing on it."},
     ]},
    {"num": 7, "name": "gui", "title": "Magic and KLayout side by side", "minutes": 2.0, "physical": False,
     "blurb": "A paced tour that opens the real KLayout and Magic windows (needs XQuartz on a Mac). Run by demos/gui_demo.py.",
     "docs": "docs/GUI_AND_LOGS.md", "runner": "examples/hermes_desktop/demos/gui_demo.py", "steps": []},
    {"num": 8, "name": "proof", "title": "Proof: local, repo, context", "minutes": 2.0, "physical": False,
     "blurb": "Show that it runs locally, that answers are tied to this repo (file, sha256, commit) and what context the model was given.",
     "docs": "docs/HERMES_DESKTOP.md", "runner": "examples/hermes_desktop/demos/proof_demo.py",
     "steps": [
         {"say": "First the claim: everything runs on this machine. The tool measures it now: sockets, ollama ps, offline settings, outbound connections.",
          "tool": "proof_local", "args": {},
          "ask": "Call the proof_local tool and quote the VERDICT line and the listening sockets.",
          "look": "VERDICT: LOCAL, every socket on 127.0.0.1, ollama ps shows the model on the GPU, outbound connections: none."},
         {"say": "Now a repo question. Under the answer is a receipt: model digest, repo commit, the file the tool read and its sha256.",
          "tool": "read_metrics", "args": {"design": D_KV, "keys": ["design__instance__count__class:sequential_cell"]},
          "ask": "Call the read_metrics tool for design kv_attn_n8 with keys design__instance__count__class:sequential_cell and tell me the flip-flop count.",
          "look": "the receipt lists designs/kv_attn_n8/output/metrics.json with a short sha256; shasum -a 256 on that file matches."},
         {"say": "Memory is read live: save a note, then ask what you remember.",
          "tool": "remember", "args": {"note": "proof demo marker", "topic": "proof"},
          "ask": "Call the remember tool with note proof demo marker and topic proof.",
          "look": "a note id; the next receipt counts one more memory entry."},
         {"say": "Ask again: the memory filter now injects the new note, and the receipt shows one more entry.",
          "tool": "recall", "args": {"query": "proof demo marker"},
          "ask": "Call the recall tool with query proof demo marker and tell me what it returns.",
          "look": "the note you just saved; memory digest entries in the receipt went up by one."},
         {"say": "Finally exactly what the last turn was sent.",
          "tool": "show_context", "args": {},
          "ask": "Call the show_context tool and show me its text.",
          "look": "system prompt sha and length, tools prompt sha, memory digest entries, retrieved passages and files with line ranges."},
     ]},
]


def find(key):
    """The demo for a number ("2", 2) or name ("kv", "/demo-kv", "demo-kv"); None when unknown."""
    k = str(key).strip().lower().lstrip("/")
    if k.startswith("demo-"):
        k = k[5:]
    for d in DEMOS:
        if k == str(d["num"]) or k == d["name"]:
            return d
    return None


def menu():
    """The numbered menu as text."""
    lines = ["Demos (python3 examples/hermes_desktop/demos.py <number or name>):"]
    for d in DEMOS:
        lines.append("  %d  %-9s %s (%s%s)" % (d["num"], d["name"], d["title"], "about %g min" % d["minutes"],
                                            ", runs a physical flow" if d["physical"] else ""))
    return "\n".join(lines)
