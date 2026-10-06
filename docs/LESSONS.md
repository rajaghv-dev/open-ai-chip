# What we built and what it taught us

The 25 hardened designs and the system checks around them, grouped into 11 families and seen from five angles:
[at a glance](#the-examples-at-a-glance), [chip design](#chip-design-perspective), [EDA](#eda-perspective-open-source-vs-proprietary),
[AI](#ai-perspective-which-problems), [business](#business-perspective-deployment-and-revenue) and [open source](#open-source-perspective). Every design here
is signoff-clean (DRC, LVS, XOR, antenna 0; setup and hold met at all 9 corners; gate-level simulation passes). The
per-design numbers are in [RESULTS.md](RESULTS.md), and each lesson comes from the linked `NOTES.md`.
Areas are std-cell um2 from `metrics.json`; flow times are from `resources.json`.

## The examples at a glance

| # | Family | Designs | What it is | Size and timing |
|---|---|---|---|---|
| 1 | Baseline | [user_proj_example](../designs/user_proj_example/NOTES.md) | The template's 16-bit counter: the non-AI control | 1,421 cells, 200 um die, 77 s |
| 2 | Tiny engines | [vision_all_lit](../designs/vision_all_lit/NOTES.md), [vision_block](../designs/vision_block/NOTES.md), [text_sentiment](../designs/text_sentiment/NOTES.md) | Dense neuron, convolution neuron, word-embedding sentiment | 169-297 cells, 80 um dies, 45-51 s |
| 3 | Audio | [audio_pitch](../designs/audio_pitch/NOTES.md), [audio_onset](../designs/audio_onset/NOTES.md) | Zero-crossing pitch, learned 4-tap onset filter | 234 / 316 cells, 80 um, 47 / 49 s |
| 4 | Multimodal | [image_text_match](../designs/image_text_match/NOTES.md) | 3 x 3 image vs one-word caption in one shared space | 551 cells, 120 um, 56 s |
| 5 | Precision | [bin](../designs/prec_bin/NOTES.md), [tern](../designs/prec_tern/NOTES.md), [int4](../designs/prec_int4/NOTES.md), [int8](../designs/prec_int8/NOTES.md), [fp8](../designs/prec_fp8/NOTES.md), [fp16](../designs/prec_fp16/NOTES.md), [bf16](../designs/prec_bf16/NOTES.md) | One 9-input neuron in 7 number formats | 199-1,932 cells, 80-220 um, 45-104 s |
| 6 | First SoC | [tiny_ai_core](../designs/tiny_ai_core/NOTES.md), [user_project_wrapper](../designs/user_project_wrapper/NOTES.md) | Three engines behind Wishbone, inside Caravel's wrapper | 1,809 cells, +1.46 ns; wrapper 59 s |
| 7 | Adapter SoC | [soc_image_text_match](../designs/soc_image_text_match/NOTES.md), [user_project_wrapper_soc_itm](../designs/user_project_wrapper_soc_itm/NOTES.md) | Generic bus-to-stream adapter plus one engine | 3,201 cells, +2.96 ns; wrapper 60 s |
| 8 | KV cache | [kv_attn_n4](../designs/kv_attn_n4/NOTES.md), [n8](../designs/kv_attn_n8/NOTES.md), [n16](../designs/kv_attn_n16/NOTES.md), [n8_int4](../designs/kv_attn_n8_int4/NOTES.md), [n8_ring](../designs/kv_attn_n8_ring/NOTES.md) | One attention head with a KV cache: prefill and decode | 1,679-4,169 cells, 200-340 um, 82-142 s |
| 9 | KV SoC | [soc_kv_attn_n8](../designs/soc_kv_attn_n8/NOTES.md), [user_project_wrapper_soc_kv](../designs/user_project_wrapper_soc_kv/NOTES.md) | The KV engine behind the adapter, inside Caravel's wrapper | 4,514 cells, +1.44 ns; wrapper 76 s |
| 10 | System checks | [firmware](../firmware/README.md), [Caravel sims](CARAVEL_SIM.md), [precheck](PRECHECK.md) | RISC-V firmware, full-chip gate level, ChipFoundry precheck | full-chip GL 14 m 23 s; precheck 14/14 |
| 11 | Agents | [Hermes agent](HERMES_AGENT.md), [KLayout demo](../examples/hermes_klayout_demo/README.md), [harness](../examples/hermes_harness/README.md), [RAG](../examples/hermes_rag/README.md), [KLayout GUI](../examples/hermes_klayout_gui/README.md), [OpenROAD GUI views](../examples/openroad_gui/README.md) | A local LLM that reads chip results through read-only tools and searches the docs | 13/15, then 15/15 with one deterministic tool; RAG 2/10 -> 4/10 -> 6/10 (retrieve-first router) |

## Chip design perspective

| Family | Lesson | Evidence |
|---|---|---|
| Baseline | A known non-AI design is the control: if it breaks, the flow broke. | 33 flip-flops, clean in 77 s |
| Tiny engines | Learned weights are constants: they fold into logic and cost no flip-flops. | vision_all_lit: 10 flip-flops, 1,085 um2 |
| Audio | Streaming state is sized by the window, not by the stream. | audio_pitch: 21 flip-flops for any stream length |
| Multimodal | Unused learned capacity is still silicon: zero-weight features still cost gates. | 551 cells for a 6-dimension match |
| Precision | Zero weights remove multipliers; floats need an extra pipeline stage at 40 MHz. | ternary has no multiplier; single-cycle fp16 failed by -6.667 ns |
| First SoC | In a fixed frame, pin count and pin order decide routability. | 361 pins congested; 109 pins in pad order routed |
| Adapter SoC | Data movement, not the network, dominates the macro. | adapter = 354 of 393 flip-flops (90 %) |
| KV cache | Nominal cache bits are not silicon: synthesis removes constant and copied bits. | n8: 512-bit cache, 72 cache flip-flops built |
| KV SoC | The system interface sets the timing margin, not the attention math. | +10.74 ns alone, +1.44 ns as a macro (reset fan-out) |
| System checks | Bus round trips dwarf compute. | engine 6-15 clocks vs 490-728 for the round trip |

In short: plumbing (bus, adapter, reset, pins) costs more area and margin than the neural network itself.

## EDA perspective: open source vs proprietary

Each step of the flow, the open tool that ran it here, and the commercial tool that does the same job. The mapping is
approximate: no proprietary tool was run in this repository, so there is no head-to-head measurement.

| Flow step | Open tool used here | Proprietary counterpart (approx.) | What we saw here |
|---|---|---|---|
| RTL simulation | Icarus Verilog | Synopsys VCS, Cadence Xcelium, Siemens Questa | Every testbench, e.g. 31,647 KV checks; `make test` in about 65 s. |
| Gate-level simulation | Icarus Verilog | VCS, Xcelium, Questa | Synthesised and routed netlists of all 25 designs; full-chip GL 14 m 23 s. |
| SDF timing simulation | Open Verilog CVC | VCS, Xcelium | Iverilog mis-parses SDF; only CVC worked, and timing checks are not enforced. |
| Synthesis | Yosys + ABC | Synopsys Design Compiler / Fusion Compiler, Cadence Genus | One-hot FSM recoding and duplicate-flop merging; a no-logic-lost check guards it. |
| Floorplan, place, CTS, route | OpenROAD ([engines](OPENROAD_ENGINES.md)) | Cadence Innovus, Synopsys IC Compiler II | Worked at these sizes; a 70 % slew margin ran out of memory, and 361 pins congested. |
| Static timing | OpenSTA | Synopsys PrimeTime, Cadence Tempus | 9 corners per design; thinnest margin +0.04 ns (bf16). |
| Parasitic extraction | OpenRCX | Synopsys StarRC, Cadence Quantus | Min / nom / max SPEF exported for every macro view. |
| DRC | Magic + KLayout | Siemens Calibre, Synopsys IC Validator, Cadence Pegasus | 0 violations in both tools for all 25 designs. |
| LVS | Netgen | Calibre LVS, IC Validator | 0 errors; one early wrapper had 70 from unpowered router diodes. |
| IR drop | OpenROAD PDNSim | Cadence Voltus, Ansys RedHawk | Reported per design (`irdrop.rpt`). |
| Layout view, XOR | KLayout | Calibre DESIGNrev, Cadence Virtuoso | `layout.png` per design; XOR 0; the agent's GDS tools. |
| Flow scripts | LibreLane 3.0.2 | Vendor reference flows, in-house scripts | One `make flow-all` per design, at most 181 s on 2 CPUs / 8 GB. |
| Tapeout precheck | `cf-precheck` 1.3.7 | Foundry signoff decks (mostly Calibre) | 14 of 14 checks pass locally. |
| PDK | sky130A (open) | Commercial PDKs under NDA | Every file inspectable; commit pinned in `versions.lock`. |

In short: at this scale every step runs on open tools on a laptop. The gaps we hit are SDF simulation and the tuning
effort. For production nodes, foundries usually require certified commercial signoff decks.

## AI perspective: which problems

The kind of AI problem each example solves, its input and output, and where the same problem appears at full scale.

| Example | Problem type | Input -> output | Real-world counterpart |
|---|---|---|---|
| [vision_all_lit](../designs/vision_all_lit/NOTES.md) | Binary image classification | 2 x 2 one-bit image -> "all four lit?" | Is the sensor fully covered or lit |
| [vision_block](../designs/vision_block/NOTES.md) | Pattern detection, anywhere in the image | 3 x 3 image -> "a lit 2 x 2 block somewhere?" | Object presence in a camera frame |
| [text_sentiment](../designs/text_sentiment/NOTES.md) | Text classification (sentiment) | 4 word tokens -> positive / negative | Review or command polarity |
| [audio_pitch](../designs/audio_pitch/NOTES.md) | Audio classification over a window | 1-bit sample stream -> high / low tone | Tone, whistle or alarm detection |
| [audio_onset](../designs/audio_onset/NOTES.md) | Event detection in a time series | 4-bit energy stream -> "getting louder?" each step | Clap, knock or wake-up onset |
| [image_text_match](../designs/image_text_match/NOTES.md) | Cross-modal matching (CLIP-style) | 3 x 3 image + 1 word -> match? + similarity score | Image search, caption checking |
| [Precision study](PRECISION_STUDY.md) | Classification under quantisation | 3 x 3 image -> vertical or horizontal bar, in 7 formats | Choosing int8 / int4 / fp formats for any model |
| [tiny_ai_core](../designs/tiny_ai_core/NOTES.md), [adapter SoC](../designs/soc_image_text_match/NOTES.md) | Serving several models behind one bus | CPU writes inputs, reads class and score | Sensor hub, accelerator offload |
| [KV cache](../designs/kv_attn_n8/NOTES.md) family | Sequence modelling: attention as key-value recall (LLM decoding) | Prefill tokens; decode a key -> the value stored under it | LLM token generation, chat context |
| [n8_int4](../designs/kv_attn_n8_int4/NOTES.md), [n8_ring](../designs/kv_attn_n8_ring/NOTES.md) | Memory-bounded context: quantised cache, sliding window | Same, with a 4-bit cache or only the last 8 tokens | Long-context LLM serving |
| [KV SoC](../designs/soc_kv_attn_n8/NOTES.md) | LLM decode driven by firmware | `PREFILL` / `DECODE` commands over Wishbone | On-device assistant |
| [Hermes agent](HERMES_AGENT.md) | Question answering with tool use (LLM agent) | Question -> answer read from chip reports | Engineering copilot |
| [Hermes RAG](../examples/hermes_rag/README.md) | Retrieval-augmented QA over the design notes (BM25) | "Why / what fixed" question -> cited answer from NOTES.md | Design-knowledge search |

Measured where it applies:
- Audio: pitch 85.5 % at W = 8 and 100 % at W = 16; onset 95.63 % on noisy labels, 100 % on clean.
- Precision: six formats at 94.00-94.25 % and binary at 88.95 % (fp32 is 94.05 %).
- KV cache: int4 recall 81.65 % vs 100 %; ring 91.11 % against the unbounded cache.
- Hermes agent: 13/15 baseline, 15/15 with one deterministic tool.
- Hermes RAG: the 8B model alone calls `search_docs` on only 6 of 12 questions (answerable 2/10 without, 4/10 with the tool), so
  retrieval is now decided in code: a deterministic router retrieves before the first model turn for why/how questions. Answerable
  6/10 by keywords (5/10 hand-checked), held-out doc questions 4/5; recall@1 5/10 -> 8/10 with the new search
  (`examples/hermes_rag/results_summary.json`).

`user_proj_example` is the non-AI control ([docs/WHY_AI.md](WHY_AI.md)).

## Business perspective: deployment and revenue

Where each kind of block could be deployed and how it could earn money. These are plausible directions reasoned from
what each example does. None has been validated with customers, and no prices or market sizes are claimed. The toy
models here would need scaling and real data before any product.

| Family | Deployment use cases | Possible revenue opportunity |
|---|---|---|
| Baseline | Teaching the open tapeout flow | Training and workshops on open-source chip design |
| Tiny engines | Always-on sensors: presence, simple pattern or command detection, toys | Licensing fixed-function AI IP blocks; custom low-power ASICs |
| Audio | Clap, knock or alarm detection in appliances, wearables and machines | Audio-trigger IP for microcontroller and sensor vendors |
| Multimodal | On-device image tagging or caption checking in smart cameras, with private data kept local | Matching IP plus a software SDK |
| Precision | Choosing number formats before tapeout | Quantisation-to-silicon consulting; design-space exploration tools |
| First SoC | A RISC-V microcontroller with a small AI accelerator (sensor hub) | MCU product sales; NRE for custom variants |
| Adapter SoC | Plugging any engine into any bus-based SoC | Reusable integration IP; per-engine integration services |
| KV cache | On-device LLM decoding, edge assistants, memory-limited inference | Attention / KV-cache accelerator IP; cache-compression IP |
| KV SoC | Reference chip for evaluating LLM offload | Evaluation boards and reference designs; customer pilots |
| System checks | Signoff and precheck before a shuttle | Verification services that cut respin risk |
| Agents | An EDA assistant that reads reports on premises, so the design never leaves | On-prem copilot subscriptions; productivity tooling |

The measured base for these: each experiment costs minutes on a laptop (macro flows 45-181 s, wrapper flows 59-76 s)
on open tools. Silicon area is dominated by interfaces: the adapter turned a 551-cell engine into a 3,201-cell macro.
Integer formats match fp32 accuracy at 4.8x less area than fp16 (ternary).

## Open-source perspective

| Family | Open pieces it stands on | What it shows |
|---|---|---|
| Baseline | ChipFoundry `caravel_user_project` @ `b510613` (Apache-2.0), sky130A PDK, LibreLane 3.0.2 | Pinned versions (`versions.lock`) reproduce the template's build. |
| Tiny engines | Yosys, OpenROAD, Magic, KLayout, Netgen (inside LibreLane) | RTL to signoff-clean GDSII with no commercial licence. |
| Audio | Standard-library Python models, iverilog | Bit-exact golden models are the spec; `make check-generated` regenerates 41 files identically. |
| Multimodal | Generated test vectors in the repo (`tb/vectors.hex`) | Anyone can rerun the exact 2,079-case test. |
| Precision | One open flow for all 7 formats | Area and power compared like for like, with every report inspectable. |
| First SoC | Caravel wrapper, SDC and pin frame from the template | Chip-level integration is learnable from public files. |
| Adapter SoC | `shared/rtl/wb_stream_adapter.v` (this repo) | One reusable adapter serves 14 engines (`tests/adapter/run.sh`). |
| KV cache | Readable RTL plus a Python golden model | LLM inference ideas (prefill, decode, KV cache) at a size you can read. |
| KV SoC | PicoRV32 (ISC) and a RISC-V GCC toolchain | Firmware-to-silicon path with an open CPU and compiler (`make soc-kv`). |
| System checks | Caravel RTL, Open Verilog CVC, `cf-precheck` 1.3.7 | Full-chip gate level and precheck run locally; open SDF simulation is still thin. |
| Agents | Ollama, Hermes 3 8B (Llama 3.1 architecture), MCP | A local model and an open tool protocol, with no cloud service. |

In short: every step from model to precheck runs on open tools. Licence status: this repository is private and has no
LICENSE file yet; third-party files and their licences are listed in [provenance/SOURCES.md](../provenance/SOURCES.md).
Check the model's licence terms before redistributing it.
