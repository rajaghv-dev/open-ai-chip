# open-ai-chip

Twenty-five small digital designs (tiny AI engines, a precision study, KV-cache attention, SoC macros and Caravel
wrappers), each taken from RTL to signoff-clean GDSII on sky130A with LibreLane 3.0.2 in Docker, on a laptop. Local
Hermes agents read the results, drive KLayout and OpenROAD, and can hand flows to Claude.

## Open and run

```bash
cd open-ai-chip
make doctor                                  # tools, Docker, LibreLane image, PDK: what is missing and how to get it
bash scripts/run_all_mac.sh                  # macOS: verify everything (tests, sims, signoff, gate level), ~10 min
bash scripts/run_all_mac.sh --all            # + flows, precheck, Hermes agents, GUI windows
bash scripts/setup_linux.sh && bash scripts/run_all.sh   # Linux: set up, then the same run
```

One design end to end: `make flow-all DESIGN=kv_attn_n8`. Fast gate: `make test`. All targets: `make help`.

## Hermes agent and demos

```bash
make hermes          # set up what is missing, start, open the Mac app (or http://127.0.0.1:8080)
make demo            # numbered demo menu
make demo-kv         # run a demo by name (or: bash scripts/hermes.sh demo 2)
make hermes-stop     # stop everything
```

In the app or browser: ask about any design ("How many cells does kv_attn_n8 have?"), run things ("Run flow-all for
vision_block": it asks you to reply `yes, run <id>` first), "Summarize the last run of kv_attn_n8", "What should I
improve in prec_bf16?", `/harden kv_attn_n8`, `/demo`, or "Open kv_attn_n8 in Magic". Only the local `hermes3:8b`
model is used; Claude is called only when you ask for `claude_task`.

| # | Demo | What you see | Time |
|---|---|---|---|
| 1 | `precision` | the 7 number formats compared: why ternary and int4 win | ~1.5 min |
| 2 | `kv` | KV cache on the RISC-V SoC: prefill cost per token falls, decode pays a full bus round trip | ~1.5 min |
| 3 | `rtl2gds` | `vision_block` hardened from the chat, then run summary, suggestions, layout picture | ~1-3 min (runs a flow) |
| 4 | `int4` | why the int4 cache has more flip-flops than int8, quoted from the design notes | ~40 s |
| 5 | `heatmaps` | OpenROAD placement, congestion and IR-drop pictures of `kv_attn_n8`, each explained | ~35 s |
| 6 | `soc` | firmware cycle table: the bus, not the accelerator, dominates | ~1 min |
| 7 | `gui` | a narrated tour in the live KLayout and Magic windows (layers, rows, power straps, DRC) | ~2 min |

Every demo prints a numbered narration line and pauses before each step (`--pace` seconds) so you can explain it; each
is saved as an Open WebUI chat and a transcript. Details: [Hermes desktop](docs/HERMES_DESKTOP.md),
[GUI demo walkthrough](examples/hermes_desktop/demos/GUI_DEMO.md).

## Open by hand

| Open | Command |
|---|---|
| Hermes agent in the browser / Mac app | `make hermes` (or `bash scripts/hermes.sh`) |
| Hermes in the terminal | `build/agent/venv/bin/python tools/hermes_agent.py "How many standard cells does vision_block have?"` |
| KLayout driven by Hermes | `bash examples/hermes_klayout_gui/start_live.sh`, then `build/agent/venv/bin/python examples/hermes_klayout_gui/agent.py --backend live "open kv_attn_n8, show met1"` |
| OpenROAD GUI, live heat maps | `bash scripts/gui/open_gui.sh heatmaps kv_attn_n8` |
| Magic | `bash scripts/gui/open_gui.sh magic kv_attn_n8` |
| Layout picture | `open designs/kv_attn_n8/output/layout.png` |

Step-by-step guides: [macOS](docs/RUN_ON_MAC.md), [Linux](docs/RUN_ON_LINUX.md),
[Hermes from the terminal](docs/HERMES_FROM_TERMINAL.md), [Hermes desktop and browser UI](docs/HERMES_DESKTOP.md),
[every GUI and log](docs/GUI_AND_LOGS.md).

## The designs

Each name links to its design page (architecture, data flow, every flow step, what was learned).

| Family | Designs |
|---|---|
| Baseline | [user_proj_example](designs/user_proj_example/NOTES.md) |
| Tiny AI engines | [vision_all_lit](designs/vision_all_lit/NOTES.md), [vision_block](designs/vision_block/NOTES.md), [text_sentiment](designs/text_sentiment/NOTES.md) |
| Audio | [audio_pitch](designs/audio_pitch/NOTES.md), [audio_onset](designs/audio_onset/NOTES.md) |
| Multimodal | [image_text_match](designs/image_text_match/NOTES.md) |
| Precision study | [prec_bin](designs/prec_bin/NOTES.md), [prec_tern](designs/prec_tern/NOTES.md), [prec_int4](designs/prec_int4/NOTES.md), [prec_int8](designs/prec_int8/NOTES.md), [prec_fp8](designs/prec_fp8/NOTES.md), [prec_fp16](designs/prec_fp16/NOTES.md), [prec_bf16](designs/prec_bf16/NOTES.md) |
| KV-cache attention | [kv_attn_n4](designs/kv_attn_n4/NOTES.md), [kv_attn_n8](designs/kv_attn_n8/NOTES.md), [kv_attn_n16](designs/kv_attn_n16/NOTES.md), [kv_attn_n8_int4](designs/kv_attn_n8_int4/NOTES.md), [kv_attn_n8_ring](designs/kv_attn_n8_ring/NOTES.md) |
| SoC macros | [tiny_ai_core](designs/tiny_ai_core/NOTES.md), [soc_image_text_match](designs/soc_image_text_match/NOTES.md), [soc_kv_attn_n8](designs/soc_kv_attn_n8/NOTES.md) |
| Caravel wrappers | [user_project_wrapper](designs/user_project_wrapper/NOTES.md), [user_project_wrapper_soc_itm](designs/user_project_wrapper_soc_itm/NOTES.md), [user_project_wrapper_soc_kv](designs/user_project_wrapper_soc_kv/NOTES.md) |

## More

- Results and lessons: [measured results](docs/RESULTS.md) (layout gallery, tables, agent results; locally `make results`), [what it taught us](docs/LESSONS.md) (chip design, EDA, AI, business, open source), [validation of every run](docs/VALIDATION.md), [tests](tests/TEST_MATRIX.md).
- Background: [why this is AI](docs/WHY_AI.md), [architecture](docs/ARCHITECTURE.md), [precision study](docs/PRECISION_STUDY.md), [LLM inference and the KV cache](docs/LLM_INFERENCE.md), [OpenROAD engines](docs/OPENROAD_ENGINES.md).
- SoC and tapeout: [SoC plan](docs/SOC_PLAN.md), [firmware](firmware/README.md), [Caravel simulation](docs/CARAVEL_SIM.md), [precheck](docs/PRECHECK.md).
- Agents: [Hermes agent](docs/HERMES_AGENT.md), [tools](tools/README.md), [KLayout demo](examples/hermes_klayout_demo/README.md), [harness](examples/hermes_harness/README.md), [RAG](examples/hermes_rag/README.md), [KLayout GUI](examples/hermes_klayout_gui/README.md), [OpenROAD GUI](examples/openroad_gui/README.md), [desktop and tool server](examples/hermes_desktop/README.md), [skills](docs/SKILLS.md).
- Project: [plan and rules](SPEC.md), [sources and licences](provenance/SOURCES.md), [slides](docs/slides/README.md) (from the sibling repository).

Not covered: no tapeout or `cf` account step is automated (owner only); the full-Caravel sims and the precheck ran for
`user_project_wrapper` only; full-chip gate level with SDF needs an x86 host.
