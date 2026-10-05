# open-ai-chip

Sixteen designs, each taken from RTL to GDSII on sky130A with LibreLane 3.0.2 in Docker, each hardened clean (DRC, LVS,
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

**Audio** (streaming)
- [`audio_pitch`](designs/audio_pitch/NOTES.md): zero-crossing count over a window, high or low tone (`model/audio_pitch/`).
- [`audio_onset`](designs/audio_onset/NOTES.md): four-tap learned filter over an energy stream, onset or not (`model/audio_onset/`).

**Multimodal**
- [`image_text_match`](designs/image_text_match/NOTES.md): a 3 x 3 image and a one-word caption in one shared embedding
  space: does the caption describe the image (`model/image_text_match/`).

**Precision study**: the same 9-input neuron in seven number formats (`model/precision_hw/`), compared in
[docs/PRECISION_STUDY.md](docs/PRECISION_STUDY.md)
- [`prec_bin`](designs/prec_bin/NOTES.md) (1-bit), [`prec_tern`](designs/prec_tern/NOTES.md) (ternary),
  [`prec_int4`](designs/prec_int4/NOTES.md), [`prec_int8`](designs/prec_int8/NOTES.md),
  [`prec_fp8`](designs/prec_fp8/NOTES.md), [`prec_fp16`](designs/prec_fp16/NOTES.md), [`prec_bf16`](designs/prec_bf16/NOTES.md).

Background and plans:
- [docs/WHY_AI.md](docs/WHY_AI.md): how and why these are AI rather than ordinary code or logic, with worked examples.
- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): architecture and block diagrams of every engine and of `tiny_ai_core`.
- [docs/SOC_PLAN.md](docs/SOC_PLAN.md): the plan from engines to a Caravel SoC.
- [docs/PRECISION_STUDY.md](docs/PRECISION_STUDY.md): number formats compared in hardware.

The flow and the checks are ported from `../open-ai-silicon` (its exercise 1); see `provenance/SOURCES.md`.
Next in `SPEC.md`: full-Caravel simulation with management firmware, and the ChipFoundry precheck.

## Run

```bash
make doctor      # tools, Docker daemon, LibreLane image, sky130A PDK at the pinned commit
make test        # fast checks, no Docker: structure, configs, lint, model, RTL sims, negative tests
make flow-all    # user_proj_example: simulate -> gds -> check -> gl (synthesised) -> gl-final (routed) -> collect
make tiny        # the same for the three tiny AI engines, then a comparison table
make all-designs # the same for all 16 designs in a fixed order (hours), then `make table`
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
| [user_project_wrapper](designs/user_project_wrapper/NOTES.md) | 0 | - | 2920 x 3520 | +1.46 (max_ss_100C_1v60) | +0.11 (min_ff_n40C_1v95) | 0/0/0/0 | 0/0/0 | 54 | 0.704 |
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
  with the reason in `scripts/flow/signoff_allowances.json`). Tapeout caveat: drive `io_oeb` high from the macro, or
  configure every user GPIO as an input in `user_defines.v`, before any submission.

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

- Full-Caravel simulation (the template's `io_ports`, `la_test1`, `la_test2`), and the ChipFoundry precheck.
- Max-slew / max-cap counts are reported by `make check`, not failed on: they come from the template's input-transition constraints on 541 unbuffered pins.
