# open-ai-chip

Twenty-five designs, each taken from RTL to GDSII on sky130A with LibreLane 3.0.2 in Docker, each hardened clean (DRC, LVS,
XOR, antenna all 0; setup and hold met at all 9 corners; gate-level simulation of the synthesised and routed netlists
passes). One page per design: `designs/<name>/NOTES.md` (architecture, data flow, layout, every step of the flow, what
was learned); the numbers are in the generated tables below.

**Baseline**
- [`user_proj_example`](designs/user_proj_example/NOTES.md): the 16-bit Wishbone / logic-analyser counter that ships in
  the ChipIgnite template (`chipfoundry/caravel_user_project` @ `b510613`).

**Tiny AI engines** (the `SPEC.md` MVP set, from the architecture study in `../open-ai-silicon`; model in `model/tiny_ai/`)
- [`vision_all_lit`](designs/vision_all_lit/NOTES.md): dense neuron, "are all pixels lit".
- [`vision_block`](designs/vision_block/NOTES.md): one convolution neuron reused at four positions, with max-pooling.
- [`text_sentiment`](designs/text_sentiment/NOTES.md): embedding lookup and accumulator.

**SoC macro and wrapper**
- [`tiny_ai_core`](designs/tiny_ai_core/NOTES.md): the three engines behind one Caravel Wishbone register interface
  (`SPEC.md` register map and interrupt); only the Wishbone bus and the interrupt leave the core.
- [`user_project_wrapper`](designs/user_project_wrapper/NOTES.md): Caravel's fixed user-area wrapper with `tiny_ai_core`
  as its single macro `mprj`; chip-level evidence in its README.
- [`soc_image_text_match`](designs/soc_image_text_match/NOTES.md): the generic Wishbone-to-stream adapter
  (`shared/rtl/wb_stream_adapter.v`) plus `image_text_match` as one 109-pin macro with `tiny_ai_core`'s port list; the
  first "one build per experiment" macro of `docs/SOC_PLAN.md`.
- [`user_project_wrapper_soc_itm`](designs/user_project_wrapper_soc_itm/NOTES.md): the same fixed Caravel wrapper with
  `soc_image_text_match` as `mprj` instead of `tiny_ai_core`: the second wrapper build, signoff-clean, its testbench
  passing through the wrapper's ports on both gate-level netlists (`make views DESIGN=soc_image_text_match`, then
  `make flow-all DESIGN=user_project_wrapper_soc_itm`).

**Audio** (streaming)
- [`audio_pitch`](designs/audio_pitch/NOTES.md): zero-crossing count over a window, high or low tone (`model/audio_pitch/`).
- [`audio_onset`](designs/audio_onset/NOTES.md): four-tap learned filter over an energy stream, onset or not (`model/audio_onset/`).

**Multimodal**
- [`image_text_match`](designs/image_text_match/NOTES.md): a 3 x 3 image and a one-word caption in one shared embedding
  space: does the caption describe the image (`model/image_text_match/`).

**LLM inference: KV cache** (one attention head with a KV cache; prefill vs decode; `model/kv_attention/`, engine
`shared/rtl/kv_attn_core.v`, background in [docs/LLM_INFERENCE.md](docs/LLM_INFERENCE.md))
- [`kv_attn_n4`](designs/kv_attn_n4/NOTES.md), [`kv_attn_n8`](designs/kv_attn_n8/NOTES.md),
  [`kv_attn_n16`](designs/kv_attn_n16/NOTES.md): 4, 8 and 16 cache entries, 8-bit keys and values.
- [`kv_attn_n8_int4`](designs/kv_attn_n8_int4/NOTES.md): 8 entries stored as 4-bit values.
- [`kv_attn_n8_ring`](designs/kv_attn_n8_ring/NOTES.md): 8 entries as a ring (sliding window: the oldest entry is overwritten).
- [`soc_kv_attn_n8`](designs/soc_kv_attn_n8/NOTES.md): `kv_attn_n8` behind the Wishbone-to-stream adapter as one 109-pin
  Caravel macro (300 x 300 um; the firmware in `firmware/kv/` drives this exact pair).
- [`user_project_wrapper_soc_kv`](designs/user_project_wrapper_soc_kv/NOTES.md): the fixed Caravel wrapper with
  `soc_kv_attn_n8` as `mprj`: KV-cache attention in the user area, signoff-clean, the testbench passing through the
  wrapper's ports on both gate-level netlists.

**Precision study**: the same 9-input neuron in seven number formats (`model/precision_hw/`), compared in
[docs/PRECISION_STUDY.md](docs/PRECISION_STUDY.md)
- [`prec_bin`](designs/prec_bin/NOTES.md) (1-bit), [`prec_tern`](designs/prec_tern/NOTES.md) (ternary),
  [`prec_int4`](designs/prec_int4/NOTES.md), [`prec_int8`](designs/prec_int8/NOTES.md),
  [`prec_fp8`](designs/prec_fp8/NOTES.md), [`prec_fp16`](designs/prec_fp16/NOTES.md), [`prec_bf16`](designs/prec_bf16/NOTES.md).

## What we built and what it taught us

The 25 hardened designs and the system checks around them, grouped into 11 families and seen from five angles:
[at a glance](#the-examples-at-a-glance), [chip design](#chip-design-perspective), [EDA](#eda-perspective-open-source-vs-proprietary),
[AI](#ai-perspective-which-problems), [business](#business-perspective-deployment-and-revenue) and [open source](#open-source-perspective). Every design here
is signoff-clean (DRC, LVS, XOR, antenna 0; setup and hold met at all 9 corners; gate-level simulation passes). The
per-design numbers are in [Measured results](#measured-results), and each lesson comes from the linked `NOTES.md`.
Areas are std-cell um2 from `metrics.json`; flow times are from `resources.json`.

### The examples at a glance

| # | Family | Designs | What it is | Size and timing |
|---|---|---|---|---|
| 1 | Baseline | [user_proj_example](designs/user_proj_example/NOTES.md) | The template's 16-bit counter: the non-AI control | 1,421 cells, 200 um die, 77 s |
| 2 | Tiny engines | [vision_all_lit](designs/vision_all_lit/NOTES.md), [vision_block](designs/vision_block/NOTES.md), [text_sentiment](designs/text_sentiment/NOTES.md) | Dense neuron, convolution neuron, word-embedding sentiment | 169-297 cells, 80 um dies, 45-51 s |
| 3 | Audio | [audio_pitch](designs/audio_pitch/NOTES.md), [audio_onset](designs/audio_onset/NOTES.md) | Zero-crossing pitch, learned 4-tap onset filter | 234 / 316 cells, 80 um, 47 / 49 s |
| 4 | Multimodal | [image_text_match](designs/image_text_match/NOTES.md) | 3 x 3 image vs one-word caption in one shared space | 551 cells, 120 um, 56 s |
| 5 | Precision | [bin](designs/prec_bin/NOTES.md), [tern](designs/prec_tern/NOTES.md), [int4](designs/prec_int4/NOTES.md), [int8](designs/prec_int8/NOTES.md), [fp8](designs/prec_fp8/NOTES.md), [fp16](designs/prec_fp16/NOTES.md), [bf16](designs/prec_bf16/NOTES.md) | One 9-input neuron in 7 number formats | 199-1,932 cells, 80-220 um, 45-104 s |
| 6 | First SoC | [tiny_ai_core](designs/tiny_ai_core/NOTES.md), [user_project_wrapper](designs/user_project_wrapper/NOTES.md) | Three engines behind Wishbone, inside Caravel's wrapper | 1,809 cells, +1.46 ns; wrapper 59 s |
| 7 | Adapter SoC | [soc_image_text_match](designs/soc_image_text_match/NOTES.md), [user_project_wrapper_soc_itm](designs/user_project_wrapper_soc_itm/NOTES.md) | Generic bus-to-stream adapter plus one engine | 3,201 cells, +2.96 ns; wrapper 60 s |
| 8 | KV cache | [kv_attn_n4](designs/kv_attn_n4/NOTES.md), [n8](designs/kv_attn_n8/NOTES.md), [n16](designs/kv_attn_n16/NOTES.md), [n8_int4](designs/kv_attn_n8_int4/NOTES.md), [n8_ring](designs/kv_attn_n8_ring/NOTES.md) | One attention head with a KV cache: prefill and decode | 1,679-4,169 cells, 200-340 um, 82-142 s |
| 9 | KV SoC | [soc_kv_attn_n8](designs/soc_kv_attn_n8/NOTES.md), [user_project_wrapper_soc_kv](designs/user_project_wrapper_soc_kv/NOTES.md) | The KV engine behind the adapter, inside Caravel's wrapper | 4,514 cells, +1.44 ns; wrapper 76 s |
| 10 | System checks | [firmware](firmware/README.md), [Caravel sims](docs/CARAVEL_SIM.md), [precheck](docs/PRECHECK.md) | RISC-V firmware, full-chip gate level, ChipFoundry precheck | full-chip GL 14 m 23 s; precheck 14/14 |
| 11 | Agents | [Hermes agent](docs/HERMES_AGENT.md), [KLayout demo](examples/hermes_klayout_demo/README.md), [harness](examples/hermes_harness/README.md) | A local LLM that reads chip results through read-only tools | 13/15, then 15/15 with one deterministic tool |

### Chip design perspective

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

### EDA perspective: open source vs proprietary

Each step of the flow, the open tool that ran it here, and the commercial tool that does the same job. The mapping is
approximate: no proprietary tool was run in this repository, so there is no head-to-head measurement.

| Flow step | Open tool used here | Proprietary counterpart (approx.) | What we saw here |
|---|---|---|---|
| RTL simulation | Icarus Verilog | Synopsys VCS, Cadence Xcelium, Siemens Questa | Every testbench, e.g. 31,647 KV checks; `make test` in about 65 s. |
| Gate-level simulation | Icarus Verilog | VCS, Xcelium, Questa | Synthesised and routed netlists of all 25 designs; full-chip GL 14 m 23 s. |
| SDF timing simulation | Open Verilog CVC | VCS, Xcelium | Iverilog mis-parses SDF; only CVC worked, and timing checks are not enforced. |
| Synthesis | Yosys + ABC | Synopsys Design Compiler / Fusion Compiler, Cadence Genus | One-hot FSM recoding and duplicate-flop merging; a no-logic-lost check guards it. |
| Floorplan, place, CTS, route | OpenROAD | Cadence Innovus, Synopsys IC Compiler II | Worked at these sizes; a 70 % slew margin ran out of memory, and 361 pins congested. |
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

### AI perspective: which problems

The kind of AI problem each example solves, its input and output, and where the same problem appears at full scale.

| Example | Problem type | Input -> output | Real-world counterpart |
|---|---|---|---|
| [vision_all_lit](designs/vision_all_lit/NOTES.md) | Binary image classification | 2 x 2 one-bit image -> "all four lit?" | Is the sensor fully covered or lit |
| [vision_block](designs/vision_block/NOTES.md) | Pattern detection, anywhere in the image | 3 x 3 image -> "a lit 2 x 2 block somewhere?" | Object presence in a camera frame |
| [text_sentiment](designs/text_sentiment/NOTES.md) | Text classification (sentiment) | 4 word tokens -> positive / negative | Review or command polarity |
| [audio_pitch](designs/audio_pitch/NOTES.md) | Audio classification over a window | 1-bit sample stream -> high / low tone | Tone, whistle or alarm detection |
| [audio_onset](designs/audio_onset/NOTES.md) | Event detection in a time series | 4-bit energy stream -> "getting louder?" each step | Clap, knock or wake-up onset |
| [image_text_match](designs/image_text_match/NOTES.md) | Cross-modal matching (CLIP-style) | 3 x 3 image + 1 word -> match? + similarity score | Image search, caption checking |
| [Precision study](docs/PRECISION_STUDY.md) | Classification under quantisation | 3 x 3 image -> vertical or horizontal bar, in 7 formats | Choosing int8 / int4 / fp formats for any model |
| [tiny_ai_core](designs/tiny_ai_core/NOTES.md), [adapter SoC](designs/soc_image_text_match/NOTES.md) | Serving several models behind one bus | CPU writes inputs, reads class and score | Sensor hub, accelerator offload |
| [KV cache](designs/kv_attn_n8/NOTES.md) family | Sequence modelling: attention as key-value recall (LLM decoding) | Prefill tokens; decode a key -> the value stored under it | LLM token generation, chat context |
| [n8_int4](designs/kv_attn_n8_int4/NOTES.md), [n8_ring](designs/kv_attn_n8_ring/NOTES.md) | Memory-bounded context: quantised cache, sliding window | Same, with a 4-bit cache or only the last 8 tokens | Long-context LLM serving |
| [KV SoC](designs/soc_kv_attn_n8/NOTES.md) | LLM decode driven by firmware | `PREFILL` / `DECODE` commands over Wishbone | On-device assistant |
| [Hermes agent](docs/HERMES_AGENT.md) | Question answering with tool use (LLM agent) | Question -> answer read from chip reports | Engineering copilot |

Measured where it applies:
- Audio: pitch 85.5 % at W = 8 and 100 % at W = 16; onset 95.63 % on noisy labels, 100 % on clean.
- Precision: six formats at 94.00-94.25 % and binary at 88.95 % (fp32 is 94.05 %).
- KV cache: int4 recall 81.65 % vs 100 %; ring 91.11 % against the unbounded cache.
- Hermes agent: 13/15 baseline, 15/15 with one deterministic tool.

`user_proj_example` is the non-AI control ([docs/WHY_AI.md](docs/WHY_AI.md)).

### Business perspective: deployment and revenue

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

### Open-source perspective

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
LICENSE file yet; third-party files and their licences are listed in [provenance/SOURCES.md](provenance/SOURCES.md).
Check the model's licence terms before redistributing it.

## Running on the SoC

All of this runs natively with iverilog (no Docker); `make test` includes the adapter tests and the firmware SoC sim.

- `make soc-sim` (about 25 s; needs `riscv64-elf-gcc`): a PicoRV32 runs RISC-V C firmware (`firmware/`, `soc_sim/`)
  against the real `user_project_wrapper` RTL. All 784 cases go through the accelerator and through pure-C software,
  plus 15 protocol negatives and the interrupt; it prints this cycle table and `PASS` (`firmware/README.md`):

      mode            cases  sw_cpu  accel_roundtrip  (write+wait+read)  accel_CYCLES_reg  sw/accel
      vision_all_lit     16  153.0  490.0  (327.0+79.0+84.0)  6.0  0.3x
      vision_block      512  1318.6  728.0  (565.0+79.0+84.0)  15.0  1.8x
      text_sentiment    256  351.0  490.0  (327.0+79.0+84.0)  6.0  0.7x

  Key insight: bus transactions dominate. The accelerator computes in 6 to 15 clocks, but each Wishbone write costs
  about 55 CPU clocks, so for the 4-input networks plain software is faster; only `vision_block` (the most work per
  input) wins, 1.8x. This is for tiny networks on a slow CPU with a one-input-per-write interface.
- `make soc-kv` (about 14 s; needs `riscv64-elf-gcc`): the KV-cache attention firmware (`firmware/kv/`, `soc_sim/kv/`) runs prefill and decode against `kv_attn_n8` behind the adapter and prints the cycle table (`firmware/README.md`, KV section).
- `make adapter-test` (about 9 s): `shared/rtl/wb_stream_adapter.v`, a generic Wishbone-to-stream adapter, verified
  with all 14 stream engines against their own vectors (`tests/adapter/`). `designs/soc_image_text_match` hardens it with
  `image_text_match` as a macro (`make flow-all DESIGN=soc_image_text_match`).
- `make caravel-rtl` (about 53 s) and `make caravel-gl` (about 58 s): the complete Caravel RTL with the real VexRiscv
  management core booting firmware from a flash model and talking to `tiny_ai_core` through the real Wishbone path; the
  GL run is hybrid (our routed wrapper and macro netlists inside RTL Caravel). They need about 5 GB of downloads in
  `build/caravel/` and are not part of `make test`; see [docs/CARAVEL_SIM.md](docs/CARAVEL_SIM.md) and
  `caravel_sim/README.md`.

Background and plans:
- [docs/WHY_AI.md](docs/WHY_AI.md): how and why these are AI rather than ordinary code or logic, with worked examples.
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): architecture and block diagrams of every engine and of `tiny_ai_core`.
- [docs/SOC_PLAN.md](docs/SOC_PLAN.md): the plan from engines to a Caravel SoC.
- [docs/PRECISION_STUDY.md](docs/PRECISION_STUDY.md): number formats compared in hardware.
- [docs/HERMES_AGENT.md](docs/HERMES_AGENT.md): a local, offline agent (Hermes 3 8B in Ollama) answering questions about the chips through read-only KLayout/EDA tools (`tools/`, also an MCP server); 13/15 on a ground-truth evaluation.
- [docs/LLM_INFERENCE.md](docs/LLM_INFERENCE.md): prefill vs decode, the KV cache and what they mean for hardware; the KV engines are the `kv_attn_*` designs.
- [examples/hermes_klayout_demo/README.md](examples/hermes_klayout_demo/README.md): a 99-line walkthrough of a local model with one KLayout tool.
- [examples/hermes_harness/README.md](examples/hermes_harness/README.md): loop and harness engineering: the same model goes 13/15 to 15/15 by adding a deterministic tool.
- [docs/SKILLS.md](docs/SKILLS.md): the project skills in `.claude/skills/`: what they encode and why, with the chip-design basics behind each.

The flow and the checks are ported from `../open-ai-silicon` (its exercise 1); see `provenance/SOURCES.md`.
Status (2026-10-06): the local ChipFoundry precheck passes 14 of 14 checks (61 s, `precheck/results/summary.tsv`, `docs/PRECHECK.md`; run in our own
container, not ChipFoundry's image; `make precheck`). Full-chip gate-level Caravel simulation with firmware (management SoC + our wrapper + macro,
functional cells) PASSES in 14 m 23 s (`make caravel-fullgl`, `docs/CARAVEL_SIM.md`). SDF back-annotation (CVC, x86-only, emulated amd64 container)
PASSES on wrapper + macro at three corners (`make caravel-sdf-wrapper`); full-chip GL+SDF with firmware was not completed (too slow emulated; needs an
x86 Linux host or a shorter flash boot). GPIO 5..37 are management-owned inputs (owner decision 2026-10-06, `user_defines.v`).
Open owner decisions: the slew-budget interpretation, and the host for full-chip SDF. Next in `SPEC.md`: the wrapper build with the adapter macro,
the release manifest, independent verification; `cf` account steps stay human-only.

## Run

```bash
make doctor      # tools, Docker daemon, LibreLane image, sky130A PDK at the pinned commit
make test        # fast checks, no Docker: structure, configs, lint, model, RTL sims, negative tests
make flow-all    # user_proj_example: simulate -> gds -> check -> gl (synthesised) -> gl-final (routed) -> collect
make tiny        # the same for the three tiny AI engines, then a comparison table
make all-designs # the same for all 17 designs in a fixed order (hours), then `make table`
make table       # regenerate the results tables below from designs/*/output (no Docker)
make flow-all DESIGN=tiny_ai_core   # the combined Wishbone macro
```

Any design: `make flow-all DESIGN=<name>`. Single stages: `make simulate | gds | check | gl | gl-final | collect | view`
(with `DESIGN=`). Models (all five dirs under `model/`): `make model-check` (`golden.py --check` of tiny_ai, image_text_match and
precision_hw; the two audio models have none, their testbenches check the RTL against `golden.py`), `make generate`
(re-fit, regenerate ROMs and vectors), `make check-generated` (regeneration reproduces every committed generated file). Defaults: `PROFILE=tight`
(container capped at 2 CPUs / 8 GB), `CPUSET=0-1`. If `DOCKER_HOST` is unset and `~/.colima/osl/docker.sock` exists,
the Makefile uses it.

| Stage | What passes means |
|---|---|
| simulate | the self-checking testbench on the RTL: the counter's checks; every input of each engine plus protocol cases, with random stalls |
| gds | LibreLane finished (`Flow complete.`); an unchanged, complete earlier run is reused |
| check | Magic/KLayout/route DRC, LVS, XOR, antenna all 0; setup and hold slack non-negative at every corner; zero synthesis check errors; surviving flip-flops = RTL registers |
| gl / gl-final | the same testbench passes on the synthesised and on the routed netlist |
| collect | GDS, LEF, netlists, reports, `layout.png` in `build/results/<design>/`; metrics, resources, flow.log, LEF in `designs/<design>/output/` |

## Measured results

Generated by `make table` (`scripts/docs/tables.py`) from `designs/<name>/output/metrics.json`, `resources.json` and
`config.json`; do not edit between the markers. All runs: PROFILE=tight (2 CPUs / 8 GB container), Colima `osl` VM, arm64.
Slack is the worst over all 9 corners (the corner is named). Max-slew / max-cap / max-fanout violations are reported by
`make check`, not failed on. Flow time and memory are one fresh LibreLane run (`resources.json`).

<!-- results:begin signoff -->
| design | std cells (incl. tap) | flip-flops | die um | worst setup ns (corner) | worst hold ns (corner) | DRC/LVS/XOR/antenna | slew/cap/fanout viol. | flow s | peak GB |
|---|---|---|---|---|---|---|---|---|---|
| [user_proj_example](designs/user_proj_example/NOTES.md) | 1,421 | 33 | 200 x 200 | +5.99 (max_ss_100C_1v60) | +0.40 (min_ff_n40C_1v95) | 0/0/0/0 | 482/1/3 | 77 | 0.764 |
| [vision_all_lit](designs/vision_all_lit/NOTES.md) | 169 | 10 | 80 x 80 | +16.07 (max_ss_100C_1v60) | +0.11 (min_ff_n40C_1v95) | 0/0/0/0 | 0/0/0 | 45 | 0.671 |
| [vision_block](designs/vision_block/NOTES.md) | 297 | 24 | 80 x 80 | +13.53 (max_ss_100C_1v60) | +0.11 (min_ff_n40C_1v95) | 0/0/0/0 | 0/0/0 | 51 | 0.691 |
| [text_sentiment](designs/text_sentiment/NOTES.md) | 200 | 12 | 80 x 80 | +14.75 (max_ss_100C_1v60) | +0.11 (min_ff_n40C_1v95) | 0/0/0/0 | 0/0/0 | 45 | 0.554 |
| [tiny_ai_core](designs/tiny_ai_core/NOTES.md) | 1,809 | 109 | 250 x 250 | +1.46 (max_ss_100C_1v60) | +0.11 (min_ff_n40C_1v95) | 0/0/0/0 | 195/0/0 | 99 | 0.642 |
| [user_project_wrapper](designs/user_project_wrapper/NOTES.md) | 0 | - | 2920 x 3520 | +1.46 (max_ss_100C_1v60) | +0.11 (min_ff_n40C_1v95) | 0/0/0/0 | 0/0/0 | 59 | 0.619 |
| [audio_pitch](designs/audio_pitch/NOTES.md) | 234 | 21 | 80 x 80 | +13.35 (max_ss_100C_1v60) | +0.10 (min_ff_n40C_1v95) | 0/0/0/0 | 18/0/1 | 47 | 0.624 |
| [audio_onset](designs/audio_onset/NOTES.md) | 316 | 25 | 80 x 80 | +13.21 (max_ss_100C_1v60) | +0.11 (min_ff_n40C_1v95) | 0/0/0/0 | 14/0/1 | 49 | 0.567 |
| [image_text_match](designs/image_text_match/NOTES.md) | 551 | 39 | 120 x 120 | +13.42 (max_ss_100C_1v60) | +0.11 (min_ff_n40C_1v95) | 0/0/0/0 | 61/0/6 | 56 | 0.708 |
| [prec_bin](designs/prec_bin/NOTES.md) | 199 | 19 | 80 x 80 | +16.40 (max_ss_100C_1v60) | +0.11 (min_ff_n40C_1v95) | 0/0/0/0 | 0/0/1 | 45 | 0.650 |
| [prec_tern](designs/prec_tern/NOTES.md) | 293 | 28 | 80 x 80 | +16.35 (max_ss_100C_1v60) | +0.11 (min_ff_n40C_1v95) | 0/0/0/0 | 9/0/0 | 47 | 0.559 |
| [prec_int4](designs/prec_int4/NOTES.md) | 377 | 30 | 80 x 80 | +13.46 (max_ss_100C_1v60) | +0.12 (min_ff_n40C_1v95) | 0/0/0/0 | 9/0/0 | 65 | 0.849 |
| [prec_int8](designs/prec_int8/NOTES.md) | 642 | 35 | 120 x 120 | +11.39 (max_ss_100C_1v60) | +0.11 (min_ff_n40C_1v95) | 0/0/0/0 | 27/0/4 | 59 | 0.598 |
| [prec_fp8](designs/prec_fp8/NOTES.md) | 1,404 | 49 | 170 x 170 | +0.26 (max_ss_100C_1v60) | +0.11 (min_ff_n40C_1v95) | 0/0/0/0 | 82/0/29 | 101 | 0.821 |
| [prec_fp16](designs/prec_fp16/NOTES.md) | 1,932 | 52 | 220 x 220 | +0.11 (max_ss_100C_1v60) | +0.11 (min_ff_n40C_1v95) | 0/0/0/0 | 36/0/26 | 104 | 0.707 |
| [prec_bf16](designs/prec_bf16/NOTES.md) | 1,754 | 51 | 220 x 220 | +0.04 (max_ss_100C_1v60) | +0.11 (min_ff_n40C_1v95) | 0/0/0/0 | 45/0/29 | 93 | 0.832 |
| [soc_image_text_match](designs/soc_image_text_match/NOTES.md) | 3,201 | 393 | 250 x 250 | +2.96 (max_ss_100C_1v60) | +0.11 (min_ff_n40C_1v95) | 0/0/0/0 | 421/0/0 | 163 | 0.980 |
| [user_project_wrapper_soc_itm](designs/user_project_wrapper_soc_itm/NOTES.md) | 0 | - | 2920 x 3520 | +2.96 (max_ss_100C_1v60) | +0.11 (min_ff_n40C_1v95) | 0/0/0/0 | 0/0/0 | 60 | 0.875 |
| [kv_attn_n4](designs/kv_attn_n4/NOTES.md) | 1,679 | 159 | 200 x 200 | +9.35 (max_ss_100C_1v60) | +0.10 (min_ff_n40C_1v95) | 0/0/0/0 | 415/0/29 | 82 | 0.655 |
| [kv_attn_n8](designs/kv_attn_n8/NOTES.md) | 2,566 | 200 | 260 x 260 | +10.74 (max_ss_100C_1v60) | +0.11 (min_ff_n40C_1v95) | 0/0/0/0 | 551/1/64 | 105 | 0.848 |
| [kv_attn_n16](designs/kv_attn_n16/NOTES.md) | 4,169 | 277 | 340 x 340 | +8.77 (max_ss_100C_1v60) | +0.10 (min_ff_n40C_1v95) | 0/0/0/0 | 1190/3/147 | 142 | 0.865 |
| [kv_attn_n8_int4](designs/kv_attn_n8_int4/NOTES.md) | 2,394 | 222 | 220 x 220 | +9.67 (max_ss_100C_1v60) | +0.11 (min_ff_n40C_1v95) | 0/0/0/0 | 554/0/83 | 93 | 0.722 |
| [kv_attn_n8_ring](designs/kv_attn_n8_ring/NOTES.md) | 2,621 | 198 | 260 x 260 | +10.70 (max_ss_100C_1v60) | +0.11 (min_ff_n40C_1v95) | 0/0/0/0 | 666/0/67 | 103 | 0.725 |
| [soc_kv_attn_n8](designs/soc_kv_attn_n8/NOTES.md) | 4,514 | 570 | 300 x 300 | +1.44 (max_ss_100C_1v60) | +0.10 (min_ff_n40C_1v95) | 0/0/0/0 | 836/0/0 | 181 | 1.099 |
| [user_project_wrapper_soc_kv](designs/user_project_wrapper_soc_kv/NOTES.md) | 0 | - | 2920 x 3520 | +1.45 (max_ss_100C_1v60) | +0.10 (min_ff_n40C_1v95) | 0/0/0/0 | 0/0/0 | 76 | 0.954 |
<!-- results:end signoff -->

Area, power and clock:

<!-- results:begin budget -->
| design | clock period ns | std-cell area um2 (excl. fill) | utilisation % | total power uW (nom_tt) | clock buffers | routed wire um |
|---|---|---|---|---|---|---|
| user_proj_example | 25 | 8046 | 24.1 | 387.4 | 7 | 23502 |
| vision_all_lit | 25 | 1085 | 27.7 | 63.7 | 3 | 1954 |
| vision_block | 25 | 2360 | 60.3 | 126.7 | 5 | 4306 |
| text_sentiment | 25 | 1368 | 34.9 | 72.4 | 3 | 2252 |
| tiny_ai_core | 25 | 11579 | 21.5 | 469.8 | 34 | 32323 |
| user_project_wrapper | 25 | 0 | 0.6 | 470.1 | - | 28365 |
| audio_pitch | 25 | 1744 | 44.6 | 112.1 | 8 | 2700 |
| audio_onset | 25 | 2794 | 71.4 | 806.6 | 8 | 4211 |
| image_text_match | 25 | 3856 | 36.3 | 208.4 | 10 | 7832 |
| prec_bin | 25 | 1474 | 37.6 | 105.5 | 6 | 2154 |
| prec_tern | 25 | 2485 | 63.5 | 143.9 | 5 | 3895 |
| prec_int4 | 25 | 3263 | 83.3 | 187.9 | 11 | 6171 |
| prec_int8 | 25 | 4860 | 45.7 | 240.4 | 14 | 8187 |
| prec_fp8 | 25 | 9225 | 39.6 | 1016.8 | 16 | 21106 |
| prec_fp16 | 25 | 11913 | 29.1 | 1928.9 | 13 | 28736 |
| prec_bf16 | 25 | 10170 | 24.9 | 1271.5 | 14 | 23952 |
| soc_image_text_match | 25 | 29686 | 55.1 | 2459.7 | 269 | 61165 |
| user_project_wrapper_soc_itm | 25 | 0 | 0.6 | 2460.0 | - | 28359 |
| kv_attn_n4 | 25 | 12708 | 38.1 | 1009.5 | 50 | 23906 |
| kv_attn_n8 | 25 | 16412 | 27.9 | 1136.1 | 57 | 34465 |
| kv_attn_n16 | 25 | 23406 | 22.4 | 1281.6 | 58 | 55238 |
| kv_attn_n8_int4 | 25 | 16680 | 40.8 | 1003.3 | 42 | 32319 |
| kv_attn_n8_ring | 25 | 16654 | 28.3 | 1146.8 | 55 | 35749 |
| soc_kv_attn_n8 | 25 | 42421 | 52.9 | 3638.7 | 387 | 82391 |
| user_project_wrapper_soc_kv | 25 | 0 | 0.9 | 3639.0 | - | 27069 |
<!-- results:end budget -->

Test coverage (what each testbench drives, on the RTL and again on both gate-level netlists) is in each design's NOTES.md,
section Verification. The Wishbone testbench of `tiny_ai_core` drives all 784 inputs of the three engines (16 + 512 + 256)
through the register interface, plus registers, byte lanes, protocol errors, back-to-back runs and reset, and again through
the wrapper's ports on the wrapper's netlists with the core's routed netlist inside.

How it got here, in short (details in `designs/user_project_wrapper/README.md`):
- The core is hardened with the template's macro constraints (`designs/tiny_ai_core/base_tiny_ai_core.sdc`: Caravel
  clock latencies, Wishbone input delays and transitions), so hold is checked against the real bus timing.
- Its 109 pins all sit on its bottom edge in the wrapper's Wishbone pad order, and it is placed just above those pads
  (`mprj` at 189.06, 87.04 um) with one full power-strap group crossing it: short wires, no congestion, no antenna diodes.
- The core's max-slew violations (count in the table above) are reported, not hidden: most are on nets driven directly by the
  `wbs_adr_i` / `wbs_dat_i` input ports, whose Caravel input transition (0.84-0.92 ns) already exceeds the 0.75 ns
  limit (environment-limited); a 70% repair margin made the repair step run out of memory chasing them, 40% gave 247,
  20% gives the count in the table. At wrapper level, max-slew, max-cap and max-fanout are 0.
- The wrapper's GPIO and logic-analyser outputs are left unconnected (owner decision for this learning build; listed
  with the reason in `scripts/flow/signoff_allowances.json`). Resolved 2026-10-06: every user GPIO 5..37 starts as a
  management-owned input (`GPIO_MODE_MGMT_STD_INPUT_NOPULL`, `designs/user_project_wrapper/rtl/user_defines.v`), so the floating `io_oeb`
  passes the precheck OEB check (14 of 14 PASS, `docs/PRECHECK.md`).

Max-slew reached 0 by tightening design repair, not by loosening the limit: `MAX_FANOUT_CONSTRAINT` 8,
`PL_RESIZER_MAX_SLEW_MARGIN` 40, `GRT_DESIGN_REPAIR_MAX_SLEW_PCT` 40, `RUN_POST_GRT_DESIGN_REPAIR`.

## Design notes and reports

One page per design: architecture (block diagram, every register), data flow cycle by cycle, verification, the GDSII layout
picture, and what each step from RTL to GDSII did, read from that design's own reports.

| Design | Notes | Layout | Reports |
|---|---|---|---|
| user_proj_example | [NOTES.md](designs/user_proj_example/NOTES.md) | [layout.png](designs/user_proj_example/output/layout.png) | [output/reports/](designs/user_proj_example/output/reports/) |
| vision_all_lit | [NOTES.md](designs/vision_all_lit/NOTES.md) | [layout.png](designs/vision_all_lit/output/layout.png) | [output/reports/](designs/vision_all_lit/output/reports/) |
| vision_block | [NOTES.md](designs/vision_block/NOTES.md) | [layout.png](designs/vision_block/output/layout.png) | [output/reports/](designs/vision_block/output/reports/) |
| text_sentiment | [NOTES.md](designs/text_sentiment/NOTES.md) | [layout.png](designs/text_sentiment/output/layout.png) | [output/reports/](designs/text_sentiment/output/reports/) |
| tiny_ai_core | [NOTES.md](designs/tiny_ai_core/NOTES.md) | [layout.png](designs/tiny_ai_core/output/layout.png) | [output/reports/](designs/tiny_ai_core/output/reports/) |
| user_project_wrapper | [NOTES.md](designs/user_project_wrapper/NOTES.md) | [layout.png](designs/user_project_wrapper/output/layout.png) | [output/reports/](designs/user_project_wrapper/output/reports/) |
| audio_pitch | [NOTES.md](designs/audio_pitch/NOTES.md) | [layout.png](designs/audio_pitch/output/layout.png) | [output/reports/](designs/audio_pitch/output/reports/) |
| audio_onset | [NOTES.md](designs/audio_onset/NOTES.md) | [layout.png](designs/audio_onset/output/layout.png) | [output/reports/](designs/audio_onset/output/reports/) |
| image_text_match | [NOTES.md](designs/image_text_match/NOTES.md) | [layout.png](designs/image_text_match/output/layout.png) | [output/reports/](designs/image_text_match/output/reports/) |
| prec_bin | [NOTES.md](designs/prec_bin/NOTES.md) | [layout.png](designs/prec_bin/output/layout.png) | [output/reports/](designs/prec_bin/output/reports/) |
| prec_tern | [NOTES.md](designs/prec_tern/NOTES.md) | [layout.png](designs/prec_tern/output/layout.png) | [output/reports/](designs/prec_tern/output/reports/) |
| prec_int4 | [NOTES.md](designs/prec_int4/NOTES.md) | [layout.png](designs/prec_int4/output/layout.png) | [output/reports/](designs/prec_int4/output/reports/) |
| prec_int8 | [NOTES.md](designs/prec_int8/NOTES.md) | [layout.png](designs/prec_int8/output/layout.png) | [output/reports/](designs/prec_int8/output/reports/) |
| prec_fp8 | [NOTES.md](designs/prec_fp8/NOTES.md) | [layout.png](designs/prec_fp8/output/layout.png) | [output/reports/](designs/prec_fp8/output/reports/) |
| prec_fp16 | [NOTES.md](designs/prec_fp16/NOTES.md) | [layout.png](designs/prec_fp16/output/layout.png) | [output/reports/](designs/prec_fp16/output/reports/) |
| prec_bf16 | [NOTES.md](designs/prec_bf16/NOTES.md) | [layout.png](designs/prec_bf16/output/layout.png) | [output/reports/](designs/prec_bf16/output/reports/) |

`make collect DESIGN=<name>` refreshes `output/`: `metrics.json`, `resources.json`, `flow.log`, LEF, `layout.png`
(KLayout render of the GDS) and `reports/`. The reports are synthesis (`synth_stat.rpt`, `synth_checks.rpt`), floorplan, placement (global, detailed),
clock tree (`cts.rpt`), routing (global, detailed), cell usage, timing (summary and the worst paths at the slow and
fast corners), DRC (Magic, KLayout), LVS (Netgen), IR drop and manufacturability. Home paths are written as `~`. The
GDS itself stays out of Git (`build/results/<name>/<name>.gds`).

## Worked AI examples beyond the chips

[docs/WHY_AI.md](docs/WHY_AI.md) sections 6 to 8 add computed examples that are not built as chips yet. Each comes
from a standard-library script whose output contains every number it quotes:
- audio: pitch and onset detection on a stream (`model/examples/audio.py`);
- a tiny transformer: bigram generation, attention, and a word-order task that bag-of-words cannot learn
  (`model/examples/transformer.py`);
- number precision: fp32, bf16, fp16, fp8 E4M3 and E5M2, int8, int4 and 1-bit on one trained model
  (`model/examples/precision.py`).

## Slides

`docs/slides/` is the workshop deck of `../open-ai-silicon`, copied unchanged as reference material. It describes that
repository's MNIST designs and is not regenerated here (see `docs/slides/README.md` and `provenance/SOURCES.md`).

## Not covered

- The template's cocotb tests (`io_ports`, `la_test1`, `la_test2`; replaced by `caravel_sim/` iverilog runs), full-chip SDF with firmware, and ChipFoundry's own `mpw_precheck` image (our precheck ran in our own container).
- Max-slew / max-cap counts are reported by `make check`, not failed on: they come from the template's input-transition constraints on 541 unbuffered pins.
