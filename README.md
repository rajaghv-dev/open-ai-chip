# open-ai-chip

Four designs, each taken from RTL to GDSII on sky130A with LibreLane 3.0.2 in Docker:

- `user_proj_example`: the 16-bit Wishbone / logic-analyser counter that ships in the ChipIgnite template
  (`chipfoundry/caravel_user_project` @ `b510613`). The baseline.
- Three tiny AI engines, the `SPEC.md` MVP set, built from the architecture study in `../open-ai-silicon`
  (`docs/ARCH_STUDY_PLAN.md`):
  - `vision_all_lit`: dense neuron.
  - `vision_block`: one convolution neuron reused at four positions, with max-pooling.
  - `text_sentiment`: embedding lookup and accumulator.

  Each has its own `designs/<name>/README.md`.

How and why these are AI rather than ordinary code or logic, with worked examples: [docs/WHY_AI.md](docs/WHY_AI.md).
Architecture and block diagrams of every engine and of `tiny_ai_core`: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).

The flow and the checks are ported from `../open-ai-silicon` (its exercise 1); see `provenance/SOURCES.md`.
- `tiny_ai_core`: the three engines, unchanged, behind one Caravel Wishbone register interface (`SPEC.md` register
  map, GPIO and logic-analyser mirrors, interrupt). Hardened as a 400 x 400 um macro, clean.

The next step in `SPEC.md` is the `user_project_wrapper` around `tiny_ai_core`, full-Caravel simulation and the precheck.

## Run

```bash
make doctor      # tools, Docker daemon, LibreLane image, sky130A PDK at the pinned commit
make test        # fast checks, no Docker: structure, configs, lint, model, RTL sims, negative tests
make flow-all    # user_proj_example: simulate -> gds -> check -> gl (synthesised) -> gl-final (routed) -> collect
make tiny        # the same for the three tiny AI engines, then a comparison table
make flow-all DESIGN=tiny_ai_core   # the combined Wishbone macro
```

Any design: `make flow-all DESIGN=<name>`. Single stages: `make simulate | gds | check | gl | gl-final | collect | view`
(with `DESIGN=`). Tiny AI model: `make model-check` (fitted model matches every label), `make generate` (re-fit,
regenerate ROMs and vectors), `make check-generated` (regeneration reproduces every committed file). Defaults: `PROFILE=tight`
(container capped at 2 CPUs / 8 GB), `CPUSET=0-1`. If `DOCKER_HOST` is unset and `~/.colima/osl/docker.sock` exists,
the Makefile uses it.

| Stage | What passes means |
|---|---|
| simulate | the self-checking testbench on the RTL: 28 checks (counter); every input of each tiny engine plus protocol cases, with random stalls |
| gds | LibreLane finished (`Flow complete.`); an unchanged, complete earlier run is reused |
| check | Magic/KLayout/route DRC, LVS, XOR, antenna all 0; setup and hold slack non-negative at every corner; zero synthesis check errors; surviving flip-flops = RTL registers |
| gl / gl-final | the same testbench passes on the synthesised and on the routed netlist |
| collect | GDS, LEF, netlists, reports, `layout.png` in `build/results/user_proj_example/`; metrics, resources, flow.log, LEF in `designs/user_proj_example/output/` |

## Measured results

### user_proj_example

From `designs/user_proj_example/output/metrics.json` and `resources.json` (run `RUN_2026-10-05_16-01-41`, PROFILE=tight,
Colima `osl` VM, arm64). They match the reference repository's exercise 1 on every number below.

| Measure | Value |
|---|---|
| Standard cells (incl. tap, excl. fill) | 1,421 |
| Flip-flops (RTL / surviving) | 33 / 33 |
| Die | 200 x 200 um |
| Worst setup / hold slack, all 9 corners | +5.99 ns / +0.40 ns |
| Magic DRC / KLayout DRC / LVS / XOR / antenna | 0 / 0 / 0 / 0 / 0 |
| Max-slew / max-cap violations (reported, not failed) | 482 / 1 |
| Flow wall time, peak container memory | 74 s, 0.763 GB |
| `make flow-all`: first run / rerun (run reused) | 83 s / 9 s |

### Tiny AI engines

From `designs/<name>/output/` (`make tiny`, PROFILE=tight, same VM). Cases = every valid input plus protocol cases
(short and long frames, out-of-range items), each also run on both gate-level netlists. Slack is the worst over all
9 corners.

| design | cases | std cells | flip-flops | die um | setup ns | hold ns | DRC/LVS/XOR/antenna | max-slew/max-cap | flow s | peak GB |
|---|---|---|---|---|---|---|---|---|---|---|
| vision_all_lit | 29 | 169 | 10 | 80 x 80 | +16.07 | +0.11 | 0/0/0/0 | 0/0 | 42 | 0.549 |
| vision_block | 540 | 297 | 24 | 80 x 80 | +13.53 | +0.11 | 0/0/0/0 | 0/0 | 49 | 0.557 |
| text_sentiment | 269 | 200 | 12 | 80 x 80 | +14.75 | +0.11 | 0/0/0/0 | 0/0 | 45 | 0.56 |

### tiny_ai_core

From `designs/tiny_ai_core/output/` (`make flow-all DESIGN=tiny_ai_core`). The Wishbone testbench drives all 784 inputs
of the three engines (16 + 512 + 256) through the register interface, plus registers, byte lanes, protocol errors,
back-to-back runs and reset: 65,642 checks, passing on the RTL, the synthesised netlist and the routed netlist.

| std cells (tap / logic) | flip-flops | die um | setup ns | hold ns | DRC/LVS/XOR/antenna | slew/cap/fanout | longest run | flow s | peak GB |
|---|---|---|---|---|---|---|---|---|---|
| 3,521 (2,115 / 1,406) | 109 | 400 x 400 | +6.99 | +0.108 | 0/0/0/0 | 0/0/0 | 15 cycles (vision_block) | 168 | 1.016 |

`SPEC.md` budgets 2,500 standard cells excluding fill: exceeded as written (3,521) because the 400 x 400 um die needs
2,115 tap cells; the logic itself is 1,406 cells. The slew, cap and fanout zeros came from tightening repair, not from
loosening limits: heuristic diode insertion off (it added a diode to buffer outputs; antenna repair stays on and
antenna is 0), slew margins 70, a deeper clock tree (`CTS_DISTANCE_BETWEEN_BUFFERS` 30).

Max-slew reached 0 by tightening design repair, not by loosening the limit: `MAX_FANOUT_CONSTRAINT` 8,
`PL_RESIZER_MAX_SLEW_MARGIN` 40, `GRT_DESIGN_REPAIR_MAX_SLEW_PCT` 40, `RUN_POST_GRT_DESIGN_REPAIR`.

## Design notes and reports

One page per design: architecture (block diagram, every register), data flow cycle by cycle, the GDSII layout
picture, and what each step from RTL to GDSII did, read from that design's own reports.

| Design | Notes | Layout | Reports |
|---|---|---|---|
| user_proj_example | [NOTES.md](designs/user_proj_example/NOTES.md) | [layout.png](designs/user_proj_example/output/layout.png) | [output/reports/](designs/user_proj_example/output/reports/) |
| vision_all_lit | [NOTES.md](designs/vision_all_lit/NOTES.md) | [layout.png](designs/vision_all_lit/output/layout.png) | [output/reports/](designs/vision_all_lit/output/reports/) |
| vision_block | [NOTES.md](designs/vision_block/NOTES.md) | [layout.png](designs/vision_block/output/layout.png) | [output/reports/](designs/vision_block/output/reports/) |
| text_sentiment | [NOTES.md](designs/text_sentiment/NOTES.md) | [layout.png](designs/text_sentiment/output/layout.png) | [output/reports/](designs/text_sentiment/output/reports/) |

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

- `tiny_ai_core` is hardened standalone; it is not yet placed in `user_project_wrapper`.
- `user_project_wrapper`, full-Caravel simulation (the template's `io_ports`, `la_test1`, `la_test2`), and the ChipFoundry precheck.
- Max-slew / max-cap counts are reported by `make check`, not failed on: they come from the template's input-transition constraints on 541 unbuffered pins.
