# open-ai-chip

One design for now: `user_proj_example`, the 16-bit Wishbone / logic-analyser counter that ships in the ChipIgnite
template (`chipfoundry/caravel_user_project` @ `b510613`), taken from RTL to GDSII on sky130A with LibreLane 3.0.2 in
Docker. The flow and the checks are ported from `../open-ai-silicon` (its exercise 1); see `provenance/SOURCES.md`.
The tiny-AI project this repository is meant to grow into is specified in `SPEC.md`.

## Run

```bash
make doctor      # tools, Docker daemon, LibreLane image, sky130A PDK at the pinned commit
make test        # fast checks, no Docker
make flow-all    # simulate -> gds -> check -> gl (synthesised) -> gl-final (routed) -> collect
```

Single stages: `make simulate | gds | check | gl | gl-final | collect | view`. Defaults: `PROFILE=tight`
(container capped at 2 CPUs / 8 GB), `CPUSET=0-1`. If `DOCKER_HOST` is unset and `~/.colima/osl/docker.sock` exists,
the Makefile uses it.

| Stage | What passes means |
|---|---|
| simulate | 28 self-checking checks on the RTL (`designs/user_proj_example/tb/`) |
| gds | LibreLane finished (`Flow complete.`); an unchanged, complete earlier run is reused |
| check | Magic/KLayout/route DRC, LVS, XOR, antenna all 0; setup and hold slack non-negative at every corner; zero synthesis check errors; surviving flip-flops = RTL registers |
| gl / gl-final | the same testbench passes on the synthesised and on the routed netlist |
| collect | GDS, LEF, netlists, reports, `layout.png` in `build/results/user_proj_example/`; metrics, resources, flow.log, LEF in `designs/user_proj_example/output/` |

## Measured results

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

## Not covered

- `user_project_wrapper`, full-Caravel simulation (the template's `io_ports`, `la_test1`, `la_test2`), and the ChipFoundry precheck.
- Max-slew / max-cap counts are reported by `make check`, not failed on: they come from the template's input-transition constraints on 541 unbuffered pins.
