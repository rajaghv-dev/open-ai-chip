---
name: harden-design
description: Take a design in this repo from RTL to clean sky130 GDSII with `make flow-all DESIGN=<d>`, and diagnose a failing stage (out of memory, GPL-0301, GRT-0116 congestion, hold/setup/slew violations, "logic lost", lint PINNOTFOUND, timeouts). Use when hardening a new or changed design, re-running a flow, reading metrics.json / checks.rpt, or fixing a failed make gds / check / gl stage.
---

# Harden a design (RTL to clean GDSII)

Facts come from `Makefile` (`make help`), `scripts/flow/*`, and the notes of the 18 designs. Long tables are in
`reference.md` (same folder). Read the design's `NOTES.md` and `config.json` `"//..."` comment keys first: they
record the fixes that already worked for sibling designs.

## 0. Preconditions
1. `make doctor` (Docker daemon, LibreLane image, PDK) and `make test` (about 65 s, no Docker) pass.
2. One physical flow at a time on this machine. Check `docker ps` for a LibreLane container before starting.
   Each flow is capped by `FLOW_TIMEOUT` (default 600 s, `scripts/flow/run_capped.sh`); use `FLOW_TIMEOUT=600`.
3. Start from the nearest sibling: copy its `config.json` (tiny engines: `vision_block`; floats: `prec_fp8`;
   macros with the Wishbone port list: `tiny_ai_core`) and size `DIE_AREA` for about 40 % utilisation
   (`FP_SIZING: absolute`; 80 x 80 um at 82 % failed, see failure table).

## 1. The one command
`make flow-all DESIGN=<d>` runs, in order, each logged to `build/flow/stage_<name>.log`:
`simulate` (iverilog, self-checking tb) -> `gds` (LibreLane; reuses a complete, current run) -> `check`
(signoff) -> `gl_synth` (gate-level sim of the synthesised netlist) -> `gl_final` (routed netlist) -> `collect`
(GDS/LEF/netlists/layout.png to `build/results/<d>/`; metrics, resources, flow.log, LEF, reports to
`designs/<d>/output/`). It stops at the first failing stage, prints the last 15 log lines, then the
five-line summary (`scripts/flow/summary.py`, numbers only from `build/sim/<d>/sim.log` and `output/*.json`).
Per-design copies of the stage logs live in `build/flow/<d>/`.

Single stages: `make simulate|gds|check|gl|gl-final|collect|view DESIGN=<d>`; `gl NETLIST=final` = `gl-final`.
Macros: `make views DESIGN=<macro>` exports `final/{gds,lef,nl,pnl,spef,lib}` into `build/macros/<macro>/`
(needs a current run; else "run make gds DESIGN=<macro>"). `make wrapper` = views then
`flow-all DESIGN=user_project_wrapper` (see the wrapper-build skill).

## 2. Reuse and staleness (`scripts/flow/find_reusable_run.py <d>`)
- A run is reusable when complete (`final/metrics.json`, `final/gds/*.gds`, `flow.log` ends `Flow complete.`) and
  no input file changed after the run STARTED (timestamp in `runs/RUN_<date>_<time>`).
- Inputs: everything under paths in the run's `resolved.json`, the current `config.json` `dir::` paths, and
  `designs/<d>/` except `runs/ gds/ output/ model/ tb/`. `*.md` is excluded, so NOTES.md/README.md edits never
  make a run stale; any other edit in the design dir does (including `config.json` and `pin_order.cfg`).
- Wrappers also depend on the macro views in `build/macros/<macro>/`: a rebuilt macro makes the wrapper stale.
- `python3 scripts/flow/find_reusable_run.py <d>` prints the run or the reason on stderr (`build/flow/reuse.err`).
  Force a fresh run only when the inputs really are unchanged and you want a measurement (`flow` target: no reuse).

## 3. Reading results
- `designs/<d>/output/metrics.json` (committed evidence). Keys: `design__instance__count`, `design__instance__area`,
  `synthesis__check_error__count`, `design__lint_error__count`, `design__inferred_latch__count`,
  `timing__setup__ws__corner:<c>` / `timing__hold__ws__corner:<c>` (+ `__tns__`, `__wns__`, `_vio__count`),
  `design__max_slew_violation__count[__corner:<c>]`, `design__max_cap_violation__count...`,
  `design__max_fanout_violation__count...`, DRC/LVS/XOR/antenna counts (`magic__drc_error__count`,
  `klayout__drc_error__count`, `route__drc_errors`, `design__lvs_error__count`, `design__xor_difference__count`,
  `antenna__violating__nets`, `route__antenna_violation__count`), `design__die__bbox`, `power__total`.
  List them: `python3 -c "import json;print(*json.load(open('designs/<d>/output/metrics.json')),sep='\n')"`.
  Nine corners: `{min,nom,max}_{tt_025C_1v80, ss_100C_1v60, ff_n40C_1v95}`; the slow one is `max_ss_100C_1v60`
  (setup), the fast one `min_ff_n40C_1v95` (hold).
- Stage logs: `build/flow/stage_check.log` shows `registers: RTL N (allowance A), surviving sequential cells M`,
  slew/cap counts and `=> PASS|FAIL` with one line per reason.
- Signoff alone: `python3 scripts/flow/check_signoff.py <d>` (newest of run and `output/`), `--all`,
  `--breakdown` (per-register accounting, use for "logic lost"), `--metrics FILE`. Max-slew/cap/fanout are
  reported, not failed on; DRC/LVS/XOR/antenna, any corner's setup/hold slack, synth check errors, unmapped
  cells, latches, lost registers and driver warnings are failed on.
- Timing paths: `designs/<d>/runs/RUN_*/NN-openroad-stapostpnr/<corner>/{checks.rpt,max.rpt,min.rpt,
  violator_list.rpt,ws.max.rpt}`. `checks.rpt` ends with "max slew violation count N" and lists violating
  pins; `max.rpt` has the worst setup path. Step logs: `runs/RUN_*/NN-<tool>-<step>/` (the step name tells the
  stage: `06-yosys-synthesis`, `28-openroad-globalplacement`, `39-openroad-globalrouting`, ...), plus
  `runs/RUN_*/{flow.log,error.log,warning.log}`.
- Slew triage: `python3 .claude/skills/harden-design/classify_slew.py <d> [corner] [run_dir]` splits violating
  pins into port-driven (environment-limited) and internal (cell-driven). On `tiny_ai_core`'s run at
  `max_ss_100C_1v60`: 80 port-driven, 51 internal of 131 listed (195 counted).

## 4. When a stage fails
Stop at the first decisive failure, keep the run dir and logs, find the signature in the table below
(details and evidence in `reference.md`), change ONE thing, re-run. Never change something unrelated at the
same time: the repo's notes record which change did what.

| Signature | Fix that worked here |
|---|---|
| repair step killed / out of memory (peak 8 GB), after 70/40 slew margins | `PL_RESIZER_MAX_SLEW_MARGIN` and `GRT_DESIGN_REPAIR_MAX_SLEW_PCT` = 20 |
| `GPL-0301` utilisation > 100 % (or 82 % then OOM) | grow `DIE_AREA`, target about 40 % (80 -> 120 um) |
| `IO_PIN_ORDER_CFG` pins land on wrong side / "pin not found" | no lines starting with `# ` in `pin_order.cfg` (direction markers); notes go in a `"//IO_PIN_ORDER_CFG"` config key |
| lint `PINNOTFOUND vccd1` on a macro | add the macro's powered netlist (`pnl`) to the wrapper config `MACROS` |
| `GRT-0116` / global routing congestion in a wrapper | fewer macro pins, order them like the wrapper pads, place the macro facing the pads |
| hold violations only inside the wrapper | harden the macro with the Caravel macro SDC (`PNR_SDC_FILE` and `SIGNOFF_SDC_FILE`) and short Wishbone wires |
| unpowered antenna diodes in the wrapper (LVS errors, nwell DRC) | macro hardened with Caravel SDC; keep wires short; `DIODE_ON_PORTS: "in"` in the macro |
| max-slew/fanout piles from heuristic diode insertion | `RUN_HEURISTIC_DIODE_INSERTION: false` plus CTS settings (sink clustering 8, diameter 20, buffer distance 30) |
| float MAC setup misses at `max_ss_100C_1v60` | pipeline the product register, `RUN_POST_GRT_RESIZER_TIMING` true, `PL/GRT_RESIZER_SETUP_SLACK_MARGIN` 0.5 |
| check: "logic lost" (surviving flops < RTL registers) | `check_signoff.py <d> --breakdown`; prove the bit dead and add an allowance with a reason, or fix the RTL |
| undriven outputs in a wrapper ("is used but has no driver") | explicit `undriven_outputs` + reason in `scripts/flow/signoff_allowances.json` |
| flow killed at 600 s | `FLOW_TIMEOUT`; first shrink the problem (die, margins), raise only if justified and recorded |

## 5. What never to do
- Never loosen `MAX_TRANSITION_CONSTRAINT` / `CLOCK_PERIOD`, never `DISABLE_LVS`, never turn
  `ERROR_ON_SYNTH_CHECKS` off in a design config (only the elaborate-only wrappers have it false, `tests/run_tests.sh`), never `SYNTH_STRATEGY` DELAY, never edit the fixed wrapper
  geometry or pin locations (`designs/user_project_wrapper*/fixed_dont_change`).
- Never accept deleted logic by lowering the check; an allowance needs a reason a reviewer can verify in the RTL.
- Never hand-edit generated files (ROM `.v`, `vectors.hex`, `weights.json`): change the model, `make generate`.
- Never run flows concurrently, and never run `cf login/init/push/submit/confirm` or publish.
- Never put absolute home paths in committed files; every number in docs names its repo file.

## 6. Finish
`make flow-all DESIGN=<d>` PASS on all 5 stages, then `make table` (README tables), write NOTES.md (write-design-notes
skill), `make test` passes, only then commit. Report: stage results, `output/metrics.json` figures, first failure.
