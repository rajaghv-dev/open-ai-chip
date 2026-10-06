# open-ai-chip

Twenty-three designs, each taken from RTL to GDSII on sky130A with LibreLane 3.0.2 in Docker, each hardened clean (DRC, LVS,
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

**Precision study**: the same 9-input neuron in seven number formats (`model/precision_hw/`), compared in
[docs/PRECISION_STUDY.md](docs/PRECISION_STUDY.md)
- [`prec_bin`](designs/prec_bin/NOTES.md) (1-bit), [`prec_tern`](designs/prec_tern/NOTES.md) (ternary),
  [`prec_int4`](designs/prec_int4/NOTES.md), [`prec_int8`](designs/prec_int8/NOTES.md),
  [`prec_fp8`](designs/prec_fp8/NOTES.md), [`prec_fp16`](designs/prec_fp16/NOTES.md), [`prec_bf16`](designs/prec_bf16/NOTES.md).

## What we built and what it taught us

Every row below was hardened clean with `make flow-all` (`designs/<d>/output/metrics.json` exists; the signoff table under
"Measured results" shows 0/0/0/0 DRC/LVS/XOR/antenna and met setup/hold for each), plus the system-level milestones that
were verified. Key numbers are std cells (incl. tap) / flip-flops / die / worst setup slack / flow time, from that table
unless another file is named. Each lesson cell is one sentence taken from the linked page.

| Example | What it is | Key numbers | Chip design lesson | EDA lesson | AI lesson | Business perspective |
|---|---|---|---|---|---|---|
| [user_proj_example](designs/user_proj_example/NOTES.md) | Template 16-bit Wishbone/logic-analyser counter (baseline) | 1,421 cells, 33 FF, 200 x 200 um, +5.99 ns, 77 s | A boring non-AI design is the control experiment; the 200 x 200 um slot is far smaller than the template's full wrapper die. | A known-good design proves the toolchain, so a later failure points at the engine, not the flow. | None: deliberately not a neural network (see `docs/WHY_AI.md`). | Start with a known-good baseline: 77 s on a laptop makes toolchain cost a rounding error. |
| [vision_all_lit](designs/vision_all_lit/NOTES.md) | Dense neuron: are all four pixels lit | 169 cells, 10 FF, 80 x 80 um, +16.07 ns, 45 s | A dense neuron needs one weight per input, so weight storage grows with input count. | Trained weights are ROM constants that fold into logic, costing no flip-flops. | Same circuit computes another rule by changing weights; one neuron cannot learn XOR-like "exactly two lit". | The smallest AI block here is 1,085 um2 of std cells: fixed-function inference can be tiny. |
| [vision_block](designs/vision_block/NOTES.md) | One convolution neuron reused over four windows, max-pooled | 297 cells, 24 FF, 80 x 80 um, +13.53 ns, 51 s | One neuron reused over 4 windows trades latency for area; the kernel is a constant with no ROM address port. | Yosys recoded the FSM to one-hot, so RTL state bits become more flip-flops (`yosys-synthesis.log`). | Weight sharing is convolution; the 2x2 kernel slides over the image. | Reuse beats replication for small edge parts: 2,360 um2 buys four-window vision. |
| [text_sentiment](designs/text_sentiment/NOTES.md) | Token embedding lookup and accumulator: positive or negative | 200 cells, 12 FF, 80 x 80 um, +14.75 ns, 45 s | The whole learned content is a 4 x 3 = 12-bit table; the decision `acc > 0` is hand-written RTL. | A lookup table synthesises to logic, not memory: no SRAM macro needed at this scale. | Embedding lookup is blind to word order; the model is a bag of signed words. | Tiny lookup models cost 1,368 um2: language features need not imply large silicon. |
| [tiny_ai_core](designs/tiny_ai_core/NOTES.md) | Three engines behind one Caravel Wishbone register interface | 1,809 cells, 109 FF, 250 x 250 um, +1.46 ns, 99 s | 63 of 109 flip-flops are Wishbone glue, only 46 are engines; fewer pins (609 to 109) halved cells by shrinking taps. | Caravel input transitions (0.84-0.92 ns) exceed the 0.75 ns limit: 195 slew violations are environment-limited, not fixable. | Engines are cheap; shared register access is the larger part of the macro. | Interface and glue, not the network, dominate small macros: budget for plumbing. |
| [user_project_wrapper](designs/user_project_wrapper/NOTES.md) | Caravel's fixed 2920 x 3520 um wrapper holding `tiny_ai_core` | 0 std cells (1 macro), 2920 x 3520 um, +1.46 ns, 59 s | Macro pin order and placement decide routability: 361 pins failed congestion, 109 pins in pad order routed. | Wrapper-level lint needs the powered netlist; unpowered router diodes gave 70 LVS errors; macro SDC fixed a -0.894 ns hold. | The wrapper adds no AI; the AI is a macro behind a bus. | A 59 s wrapper build means a design change reaches signoff fast; the fixed frame removes die-level decisions. |
| [audio_pitch](designs/audio_pitch/NOTES.md) | Zero-crossing count over a window: high or low tone | 234 cells, 21 FF, 80 x 80 um, +13.35 ns, 47 s | Streaming state is a delay line sized by the window: 21 FF whether the stream is 16 samples or a year. | The running count is updated (add and subtract), avoiding a population-count tree. | Window W = 8 gives only 85.5 % because counts overlap; W = 16 reaches 100 % at more flip-flops. | A longer window costs flip-flops, not logic: accuracy is bought with state bits. |
| [audio_onset](designs/audio_onset/NOTES.md) | Four-tap learned filter over energy stream: onset or not | 316 cells, 25 FF, 80 x 80 um, +13.21 ns, 49 s | 4-bit samples cost 2.4x the combinational area of 1-bit (1,549 vs 654 um2): arithmetic grows more than storage. | Weights [-1,-1,+1,+1] let a 6-bit modular sum stay exact; the ROM generator refuses ones that overflow. | The trainer rediscovered "recent minus older" from noisy labels (95.63 % noisy, 100 % clean). | Sample width multiplies silicon (2,794 vs 1,744 um2 of cells): choose the narrowest sample that works. |
| [image_text_match](designs/image_text_match/NOTES.md) | CLIP-style match of 3 x 3 image and one-word caption | 551 cells, 39 FF, 120 x 120 um, +13.42 ns, 56 s | Unused learned capacity is still real silicon: zero-weight features still cost gates. | The testbench has 2,079 cases (2,048 exhaustive pairs) and passes on RTL and both gate netlists. | Two modalities meet in one 6-dimensional space; neither vector means anything alone. | A multimodal model fits 3,856 um2 of cells and 56 s of flow: cheap to iterate. |
| [prec_bin](designs/prec_bin/NOTES.md) | 1-bit-weight neuron, XNOR + popcount | 199 cells, 19 FF, 80 x 80 um, +16.40 ns, 45 s | No multiplier: 15 of 19 flip-flops are control and input stage, so plumbing is the design. | Constant weight bits fold XNOR into plain decode logic: 0 xor cells in `synth_stat.rpt`. | Only format that loses accuracy: 88.95 % vs 94.05 % fp32; sign-only weights cannot say "pixel hardly matters". | 1,474 um2 is the floor, but 5 points lower accuracy is the price (`docs/PRECISION_STUDY.md`). |
| [prec_tern](designs/prec_tern/NOTES.md) | Ternary {-1,0,+1}-weight neuron | 293 cells, 28 FF, 80 x 80 um, +16.35 ns, 47 s | Zero weights remove hardware: no multiplier at all, at 1.7x binary area. | Constant weights prune whole terms before place and route. | 94.15 % accuracy vs 94.05 % fp32 with no multiplier: the study's sweet spot. | Ternary reaches fp32-level accuracy at 2,485 um2, 4.8x less than fp16. |
| [prec_int4](designs/prec_int4/NOTES.md) | 4-bit signed integer neuron | 377 cells, 30 FF, 80 x 80 um, +13.46 ns, 65 s | 12-bit accumulator; 2.2x binary area still fits the 80 x 80 um die (83.3 % utilisation). | High utilisation (83.3 %) still routed clean; hold needed buffers, not area. | 94.25 % accuracy, the best of the seven; differences among six formats are noise (5 images). | Integer 4-bit is the cost/accuracy knee: 3,263 um2 vs 11,913 um2 for fp16. |
| [prec_int8](designs/prec_int8/NOTES.md) | 8-bit signed integer neuron | 642 cells, 35 FF, 120 x 120 um, +11.39 ns, 59 s | 8 x 5 multiplier plus 17-bit add: 5.5x the binary logic area, one pipeline stage. | Integer MAC closes timing in one stage at 40 MHz; no pipelining needed. | Matches every fp32 decision (100.00 %): the accuracy ceiling reference. | Shipping int8 is safe but costs 3.3x binary area for no accuracy gain over int4. |
| [prec_fp8](designs/prec_fp8/NOTES.md) | E4M3 float neuron, fp16 products | 1,404 cells, 49 FF, 170 x 170 um, +0.26 ns, 101 s | Float alignment and rounding need a pipeline register: latency 3 instead of 2. | Setup slack drops to +0.26 ns; 82 slew and 29 fanout violations appear. | 94.15 % accuracy, no better than int4 (94.25 %). | fp8 is 6.3x binary area for no accuracy gain here (`docs/PRECISION_STUDY.md`). |
| [prec_fp16](designs/prec_fp16/NOTES.md) | IEEE binary16 neuron, two-stage MAC | 1,932 cells, 52 FF, 220 x 220 um, +0.11 ns, 104 s | The accumulate loop is the speed limit: single-cycle MAC was -6.667 ns; product register plus repair gave +0.111 ns. | Setup margin is 0.44 % of the clock and moved 1.4 ns with repair settings alone: results are partly the tool run. | 94.05 % and 100 % same-decision as fp32: precision beyond int8 buys nothing here. | Largest of the study: 8.1x binary area and 1.929 mW (8.0x int8): fixed-function edge inference should use integer formats. |
| [prec_bf16](designs/prec_bf16/NOTES.md) | bfloat16 neuron | 1,754 cells, 51 FF, 220 x 220 um, +0.04 ns, 93 s | Same 16 bits as fp16 but 14.6 % smaller (10,170 vs 11,913 um2): fewer mantissa bits shrink the multiplier. | Slack of +0.04 ns is the thinnest margin of all 18 builds. | 94.00 % accuracy: range matters more than mantissa for this neuron. | Format choice moves area by 15 % at equal storage: cheapest to decide before layout. |
| [soc_image_text_match](designs/soc_image_text_match/NOTES.md) | Generic Wishbone-stream adapter plus `image_text_match`, 109-pin macro | 3,201 cells, 393 FF, 250 x 250 um, +2.96 ns, 165 s | The adapter is 354 of 393 flip-flops (90 %): data movement, not the engine, is the cost. | 421 slew violations are input-port limited; the build is the longest macro flow here. | The network is 10 % of the macro; a 10-clock engine needs about 550 CPU clocks to feed (estimate). | Interfaces cost 7.7x the engine's cell area: budget integration, not just the model. |
| [user_project_wrapper_soc_itm](designs/user_project_wrapper_soc_itm/NOTES.md) | Same Caravel wrapper with `soc_image_text_match` as macro | 0 std cells (1 macro), 2920 x 3520 um, +2.96 ns, 66 s | A same-footprint macro makes the wrapper a copy with one instance name changed. | Macro views (liberty, SPEF) are the interface; editing a non-.md file after a run makes it "not current". | Not an AI lesson: a swap of the macro leaves the system's behaviour the same. | Second wrapper in 66 s vs 59 s for the first: per-experiment cost is nearly constant. |
| [kv_attn_n4](designs/kv_attn_n4/NOTES.md), [n8](designs/kv_attn_n8/NOTES.md), [n16](designs/kv_attn_n16/NOTES.md) | One-head KV-cache attention, 4 / 8 / 16 entries, 8-bit; prefill appends tokens, decode scans the cache serially (latency n + 3) | n4: 1,679 cells, 159 FF, 200 x 200 um, +9.35 ns, 82 s; n8: 2,566, 200, 260 x 260, +10.74 ns, 105 s; n16: 4,169, 277, 340 x 340, +8.77 ns, 142 s | The nominal cache is 256 / 512 / 1,024 bits (`golden.py --check`) but only 36 / 72 / 144 cache flip-flops were built (159 / 200 / 277 in total, `metrics.json`): the ROM weights are constants, so most stored bits are constant or copies and synthesis removes them (`designs/kv_attn_n8/NOTES.md`). A reset bug on the scan counter `jc` was caught only in gate-level simulation (X-pessimism, comment in `shared/rtl/kv_attn_core.v`). | The RTL simulated clean while the gate-level netlist did not: an uninitialised scan counter stays X in gates, so gate-level runs are a separate test, not a repeat. | Prefill vs decode on the SoC (`firmware/README.md`, KV section): cost per prompt token 328 -> 90.6 cycles as prompt length P goes 1 -> 7; a decode round trip is 670 cycles at every cache fill while the engine needs n + 3. | Context length is paid for in silicon and in time: 4x the entries cost 2.5x the cells (4,169 vs 1,679) and every decode takes n + 3 cycles. |
| [kv_attn_n8_int4](designs/kv_attn_n8_int4/NOTES.md) | n8 with 4-bit cache values | 2,394 cells, 222 FF, 220 x 220 um, +9.67 ns, 93 s; cache 256 bits vs 512 | Halving the nominal cache bits (512 -> 256) did not reduce flip-flops: 222 vs 200, because the int8 baseline was already pruned to 72 cache flip-flops (96 here); synthesised area is +7.3 % (`designs/kv_attn_n8_int4/NOTES.md`). | Nominal bits are not silicon: check what survived synthesis (`check_signoff.py --breakdown`) before claiming a saving; the die was set smaller by hand (220 vs 260 um). | Recall of the value last stored under a key: 81.65 % vs 100.00 % for int8 (`model/kv_attention/golden.py --check`); the cost lands on recency-sensitive recall. | Quantising the cache saves memory at a measured accuracy price: decide on the task, not on bits alone. |
| [kv_attn_n8_ring](designs/kv_attn_n8_ring/NOTES.md) | n8 with a ring buffer: overwrite the oldest entry | 2,621 cells, 198 FF, 260 x 260 um, +10.70 ns, 103 s | A ring costs about the same as the linear cache (2,621 vs 2,566 cells): wrap logic is small. | Latency stays n + 3 with n up to 8 (`golden.py --check`). | Ring = sliding window: 100.00 % against the window oracle but 91.11 % against the unbounded one; older context is gone by design. | A fixed memory budget forces forgetting: a sliding window is the cheapest policy, and its accuracy depends on how far back the task looks. |
| [PicoRV32 SoC firmware run](firmware/README.md) | RISC-V firmware drives the real wrapper in an iverilog SoC | PASS; accel CYCLES 6-15 vs 490-728 round trip; about 55 CPU clocks per bus write | Bus transactions cost tens of clocks: the accelerator computes in 6-15 while the round trip is 490-728. | The testbench forces `sel = 1111` on reads as Caravel does: protocol details break integration, not logic. | Software won for 4-input nets (0.3x, 0.7x); only the 9-pixel convolution is 1.8x faster on the accelerator. | A tiny accelerator behind a slow bus can lose to software: measure end-to-end before committing silicon. |
| [Wishbone-stream adapter, 13 engines](tests/adapter/run.sh) | One adapter run with every stream engine, only via Wishbone | `adapter tests: 13 engines PASS` (`tests/adapter/run.sh`) | One 24-pin stream contract lets 13 engines share the same adapter unchanged. | Each engine's own `vectors.hex` is reused as the adapter test: verification effort is amortised. | Engines of different formats (FP, int, audio, image) behave identically behind the bus. | A uniform interface cuts per-engine integration cost: add an engine, rerun the same test. |
| [Full-Caravel RTL / full-chip GL / SDF](docs/CARAVEL_SIM.md) | `tiny_ai_core` firmware in Caravel RTL, full-chip gate-level, and SDF on wrapper+macro | RTL PASS 53 s; full-chip GL PASS 14 m 23 s; SDF PASS at tt, nom_ss, max_ss (5 s) | Hybrid GL keeps Caravel at RTL; full-chip GL makes everything a netlist and still passes with exact pass code 0xAB61. | iverilog cannot do SDF properly; Open Verilog CVC was the only open SDF simulator; timing checks are not enforced. | Firmware ID plus 4 cases match golden results at gate level. | Gate-level evidence needs a long run (14 m 23 s) but only open tools and a laptop. |
| [Local ChipFoundry precheck](docs/PRECHECK.md) | Local run of the 14 precheck checks on the wrapper | 14 of 14 PASS (baseline run: 12 of 14) | Floating `io_oeb` failed GPIO and OEB checks; starting user GPIOs as management-owned inputs fixed both. | The checks run in one container each; LVS 14 s, OEB 14 s, Magic DRC 6 s. | Not applicable: checks are about the chip frame, not the model. | Precheck is local and cheap, so shuttle rejection risk is found before submission (`docs/PRECHECK.md`). |
| [Local Hermes + KLayout agent](docs/HERMES_AGENT.md) | Local Hermes model answering questions about the repo's chips via tools; minimal walkthrough in [examples/hermes_klayout_demo](examples/hermes_klayout_demo/README.md) | Prompt mode 13/15; native tool mode 6/15 (`build/agent/eval_20261006_121338.json`) | Questions use the same `metrics.json` data as the flow, so the agent reads real results. | The agent reads reports through tools (`read_metrics`, `compare_designs`, `layout_summary`). | Failures were wrong corner and sign (q02) and ignoring a condition (q04): evaluate before trusting. | A local 5.8 GB model on a laptop costs no per-query fee, but 13/15 is not release quality. |
| [Hermes harness](examples/hermes_harness/README.md) | Same model, same 15 questions; only the loop, checks and one deterministic tool change | baseline 13/15 -> 15/15 with `pick_extreme`; guardrails 13/15, grounding 13/15, all+plan 14/15 (`build/agent/harness_eval_20261006_121338.json`) | Not a chip lesson: the harness is plain code around a model. | Evaluation as a gate: `eval_harness.py --gate N` exits 1 when the score regresses. | Prompt-side features (guardrails, grounding) gave nothing; moving "find the minimum" out of the model into a deterministic tool fixed both comparison failures. | Improve the tool before buying a bigger model: it is cheaper, testable and measured. |

Sources: `designs/<d>/output/metrics.json` and `resources.json` for every design (via the "Measured results" tables above),
`designs/<d>/NOTES.md` "Intuitions and insights" for the lessons, `docs/PRECISION_STUDY.md` (accuracy, area ratios),
`firmware/README.md` (cycle table), `tests/adapter/run.sh` (13 engines), `docs/CARAVEL_SIM.md`, `docs/PRECHECK.md`,
`docs/HERMES_AGENT.md`. "Estimate" marks a value derived by arithmetic in a NOTES page, not measured.

### Big picture: chip design
- Data movement dominates: the adapter is 354 of 393 flip-flops (90 %) of `soc_image_text_match`, and a PicoRV32 bus write costs about 55 clocks against a 6-15 clock computation (`firmware/README.md`).
- ROM constants fold into logic: learned weights cost no flip-flops in `tiny_ai_core` (63 glue + 46 engine = 109 FF), and constant weight bits remove the XNOR in `prec_bin`.
- Zero weights prune hardware: ternary reaches fp32 accuracy with no multiplier at 2,485 um2 (`docs/PRECISION_STUDY.md`).
- Taps scale with die area, not logic: shrinking `tiny_ai_core` from 160,000 to 62,500 um2 cut taps from 2,115 to 765.
- Streaming state is sized by the window: `audio_pitch` stays at 21 FF for any stream length.
- Floats need pipelining at 40 MHz: fp16's single-cycle MAC was -6.667 ns; integers closed in one stage.
- A fixed frame (Caravel wrapper) makes pin order and macro placement the design decision: 361 pins congested, 109 routed.

### Big picture: EDA
- Environment-limited slew: Caravel input transitions of 0.84-0.92 ns exceed the 0.75 ns limit, so `tiny_ai_core` reports 195 slew violations that no resizing can remove.
- Signoff-clean is not functionally correct: every design is also simulated on the synthesised and routed gate-level netlists, with a no-logic-lost check.
- One-hot recoding changes flip-flop counts (9, 22, 11 RTL state bits became 10, 24, 12 in `tiny_ai_core`).
- Tool results are partly the run: fp16 setup slack moved 1.4 ns from repair settings alone, so margins of 0.04-0.11 ns are fragile.
- Open SDF simulation is a gap: iverilog mis-parses SDF, so only CVC could back-annotate, and timing checks are not enforced.
- Wrapper lint needs the powered netlist and unpowered router diodes broke LVS (70 errors): frame-level flows have their own failure modes.
- Editing any non-.md file after a run makes it "not current" for later steps, so reproducibility is enforced by the flow.

### Big picture: AI
- A neural network is constants plus a fixed structure: the same RTL computes a different rule when only ROM weights change, but one neuron cannot learn XOR-like rules.
- Weight sharing is convolution: `vision_block` reuses one neuron over four windows.
- More bits did not buy accuracy: tern, int4, int8, fp8, fp16, bf16 span 94.00-94.25 % (five images, noise); only binary loses (88.95 %).
- Window length trades accuracy for state: `audio_pitch` 85.5 % at W = 8, 100 % at W = 16.
- Trainers can rediscover structure: `audio_onset` found [-1,-1,+1,+1] from noisy labels.
- Small accelerators can lose to software when the bus is slow: 0.3x and 0.7x on 4-input networks.
- Evaluate before trusting a local model: Hermes scored 12/15 in prompt mode and 6/15 in native mode.

### Big picture: Business
Implications reasoned from measured facts in this repository; no prices, market sizes or customers are stated.
- A laptop takes an AI accelerator from RTL to a precheck-clean Caravel wrapper: macro flows take 45-165 s and wrapper flows 59-66 s on 2 CPUs / 8 GB, peak memory under 0.9 GB.
- Open-source tools (LibreLane, Yosys, OpenROAD, Magic, KLayout, iverilog) carry the whole flow including the 14-check local precheck, so tool licence cost is not a barrier to a first shuttle submission.
- Reuse lowers cost per experiment: the second wrapper (66 s) and the 13-engine adapter test reuse one frame and one test.
- Verification effort, not layout, is the long pole: full-chip gate-level took 14 m 23 s against a 165 s macro flow.
- Integer formats lower silicon cost: fp formats cost 6-8x the binary area for no accuracy gain here (`docs/PRECISION_STUDY.md`); for fixed-function edge inference, choose int4 or ternary.
- Integration costs more than the model: the adapter took the macro from 551 to 3,201 cells, so budget interface area and CPU-bus time.
- Risk is found early and locally: the precheck went from 12/14 to 14/14 with one GPIO-default fix before any submission.

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
  with all 13 stream engines against their own vectors (`tests/adapter/`). `designs/soc_image_text_match` hardens it with
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
| [soc_image_text_match](designs/soc_image_text_match/NOTES.md) | 3,201 | 393 | 250 x 250 | +2.96 (max_ss_100C_1v60) | +0.11 (min_ff_n40C_1v95) | 0/0/0/0 | 421/0/0 | 165 | 0.802 |
| [user_project_wrapper_soc_itm](designs/user_project_wrapper_soc_itm/NOTES.md) | 0 | - | 2920 x 3520 | +2.96 (max_ss_100C_1v60) | +0.11 (min_ff_n40C_1v95) | 0/0/0/0 | 0/0/0 | 66 | 0.790 |
| [kv_attn_n4](designs/kv_attn_n4/NOTES.md) | 1,679 | 159 | 200 x 200 | +9.35 (max_ss_100C_1v60) | +0.10 (min_ff_n40C_1v95) | 0/0/0/0 | 415/0/29 | 82 | 0.655 |
| [kv_attn_n8](designs/kv_attn_n8/NOTES.md) | 2,566 | 200 | 260 x 260 | +10.74 (max_ss_100C_1v60) | +0.11 (min_ff_n40C_1v95) | 0/0/0/0 | 551/1/64 | 105 | 0.848 |
| [kv_attn_n16](designs/kv_attn_n16/NOTES.md) | 4,169 | 277 | 340 x 340 | +8.77 (max_ss_100C_1v60) | +0.10 (min_ff_n40C_1v95) | 0/0/0/0 | 1190/3/147 | 142 | 0.865 |
| [kv_attn_n8_int4](designs/kv_attn_n8_int4/NOTES.md) | 2,394 | 222 | 220 x 220 | +9.67 (max_ss_100C_1v60) | +0.11 (min_ff_n40C_1v95) | 0/0/0/0 | 554/0/83 | 93 | 0.722 |
| [kv_attn_n8_ring](designs/kv_attn_n8_ring/NOTES.md) | 2,621 | 198 | 260 x 260 | +10.70 (max_ss_100C_1v60) | +0.11 (min_ff_n40C_1v95) | 0/0/0/0 | 666/0/67 | 103 | 0.725 |
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
