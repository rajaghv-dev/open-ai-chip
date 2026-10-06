# harden-design reference: failure table with evidence

All evidence is a file in this repo. "cfg" = the design's `config.json` `"//..."` comment key.

## Failure table

### 1. Repair step runs out of memory (container capped at 8 GB, `PROFILE=tight`)
- Signature: `make gds` dies in the post-placement or post-GRT repair step (`32-openroad-repairdesignpostgpl`,
  `*-repairdesign*`); no `Flow complete.`; container exits.
- Cause: slew margins 70 or 40 with inputs whose transition is already over the limit (Caravel macro SDC: `wbs_dat_i`
  0.84 ns, `wbs_adr_i` 0.92 ns against the 0.75 ns limit): repair chases unfixable nets.
- Fix: `PL_RESIZER_MAX_SLEW_MARGIN` and `GRT_DESIGN_REPAIR_MAX_SLEW_PCT` = 20.
- Evidence: `designs/tiny_ai_core/config.json` `//SLEW` (70 % ran out of memory, 40 % gave 247 violations, 20 % gave
  195); `designs/user_project_wrapper/README.md` step 4; the `image_text_match` and `prec_int8` configs `//SLEW`
  (40 ran out of memory twice, at 80 and 120 um).

### 2. GPL-0301 / utilisation above 100 %, or 82 % then out of memory
- Fix: enlarge `DIE_AREA` (`FP_SIZING: absolute`) to about 36-40 % utilisation: 80 x 80 -> 120 x 120 um.
- Evidence: `//DIE_AREA` of `image_text_match` (82 %, repair out of memory, 120 um gives about 36 %) and `prec_int8`
  (115 % of the core, global placement refused); `designs/image_text_match/NOTES.md` line "(6400 um^2) ... 0.3628";
  `prec_fp8/prec_fp16/prec_bf16` configs size from expected cell area.
- Pin-limited dies: `tiny_ai_core` 250 x 250 um because 109 pins need about 2.3 um pitch on one edge
  (`//DIE_AREA`); die can be pin-limited, not area-limited.

### 3. pin_order.cfg comments
- Signature: pins placed by the wrong rule / wrong edge after adding a comment to `pin_order.cfg`.
- Cause: LibreLane reads every line starting with `#` as a direction marker (e.g. `#N`, `#S`).
- Fix: no comment lines; put the explanation in a `"//IO_PIN_ORDER_CFG"` key. One explicit regex per pin.
- Evidence: `designs/tiny_ai_core/config.json` and `designs/soc_image_text_match/config.json` `//IO_PIN_ORDER_CFG`.

### 4. Lint PINNOTFOUND vccd1 on a macro instance (wrapper)
- Cause: LibreLane lints the wrapper against the macro netlist with power pins defined; plain `nl` has none.
- Fix: list the macro's `pnl` (powered netlist) in the wrapper config `MACROS` entry (`nl` and `pnl`).
- Evidence: `designs/user_project_wrapper/README.md` "How it got clean" step 1.

### 5. GRT-0116 / global routing congestion in the wrapper
- Cause: the first macro had 361 pins on its bottom edge 16 um above the wrapper's own pin row.
- Fix: simpler macro (Wishbone + irq = 109 pins, all on one edge in the order of the wrapper's Wishbone pads,
  `pin_order.cfg`), placed at (189.06, 87.04) um so wires are short and one full PDN strap group crosses it.
- Evidence: `designs/user_project_wrapper/README.md` step 2; `designs/tiny_ai_core/NOTES.md` "The pin order is a
  layout decision"; build time 53 s, 0.62 GB (README status).

### 6. Hold violations inside a wrapper (-0.894 ns)
- Cause: macro hardened with default constraints and long input wires; Caravel's clock latency spread is
  4.65-5.57 ns (`designs/tiny_ai_core/base_tiny_ai_core.sdc`).
- Fix: harden the macro with the template macro SDC (`PNR_SDC_FILE`, `SIGNOFF_SDC_FILE` -> `base_<macro>.sdc`).
  Hold became +0.105 ns in macro and wrapper.
- Evidence: `designs/user_project_wrapper/README.md` step 3; `designs/tiny_ai_core/NOTES.md` "Why the Caravel macro SDC matters".

### 7. Unpowered antenna diodes in the wrapper (LVS 70 errors, nwell DRC)
- Cause: the wrapper's router added 40 diodes on long macro-to-pad wires; the wrapper is elaborate-only and the
  diodes are unpowered.
- Fix: same as 6 (short wires, macro SDC) and `DIODE_ON_PORTS: "in"` in the macro so its input ports carry diodes
  inside the macro (49 antenna diodes in `tiny_ai_core`, `NOTES.md` "Where the area goes").

### 8. Max-slew / max-fanout from heuristic diode insertion
- Fix used for the big macros: `RUN_HEURISTIC_DIODE_INSERTION: false`, `RUN_ANTENNA_REPAIR: true`,
  `DIODE_ON_PORTS: "in"`, `CTS_SINK_CLUSTERING_SIZE` 8, `CTS_SINK_CLUSTERING_MAX_DIAMETER` 20,
  `CTS_DISTANCE_BETWEEN_BUFFERS` 30, `MAX_FANOUT_CONSTRAINT` 8.
- Small engines keep it true (13 diode cells): `designs/vision_all_lit/NOTES.md`, `text_sentiment/NOTES.md`
  ("tiny_ai_core sets it false"). `prec_fp16` inserted 242 diodes with it true (`NOTES.md` antenna paragraph).

### 9. Floating-point MAC setup misses (25 ns clock, `max_ss_100C_1v60`)
- Fix, in order: pipeline the product register (moved -2.1 -> -1.14 ns on `prec_fp8`), then tool-only repair:
  `RUN_POST_GRT_RESIZER_TIMING` true, `PL_RESIZER_SETUP_SLACK_MARGIN` / `GRT_RESIZER_SETUP_SLACK_MARGIN` 0.5,
  `PL_RESIZER_SETUP_BUFFERING` / `GRT_RESIZER_SETUP_BUFFERING`, `PL_RESIZER_SETUP_GATE_CLONING` /
  `GRT_RESIZER_SETUP_GATE_CLONING`, slew margins 40, `MAX_FANOUT_CONSTRAINT` 8. Clock stays 25 ns.
- Evidence: `designs/prec_fp8/NOTES.md` (table "Pipelined: product registered" then "Plus tool timing repair":
  +0.2591 ns), `prec_fp16/NOTES.md`, `prec_bf16/NOTES.md`; `model/precision_hw/spec.md` section 6 (a float MAC needs
  two pipeline stages at 40 MHz; hence the product register). Margins are thin (+0.04 ... +0.26 ns): recheck on
  another machine (`prec_fp16/NOTES.md` item 6).

### 10. "logic lost" in check_signoff
- Signature: `registers: RTL N (allowance A), surviving sequential cells M` with M < N - A.
- Steps: `python3 scripts/flow/check_signoff.py <d> --breakdown`; for each missing bit decide: provably dead
  (constant/unused) -> add `{"removed_registers": k, "reason": "..."}` to `scripts/flow/signoff_allowances.json`
  with a reason verifiable in the RTL; or a real bug -> fix the RTL. One-hot recoding adds flops (RTL 22 ->
  24 in `vision_block`), the check compares with the elaborated count.
- Evidence: `designs/image_text_match/NOTES.md` (4 bits, 6 pooled counters), `designs/prec_bf16/NOTES.md` (product
  register 16 bits, exponent bit 6 = NOT bit 7 since all weights are 0x74..0x7D; fixed by narrowing the register
  and a comment at `p_bits_r`).
- Not caught by this check: a multiply-driven register (caught by the driver-warning check, synth check errors with
  `ERROR_ON_SYNTH_CHECKS` true, and gate-level sim).

### 11. Undriven outputs of a wrapper
- Signature: Yosys "is used but has no driver" for `io_out`, `io_oeb`, `la_data_out` (204 bits).
- Fix: `undriven_outputs` + `undriven_reason` entry in `scripts/flow/signoff_allowances.json` (owner decision;
  tapeout caveat: floating `io_oeb`). Only those ports are accepted.

### 12. Timeouts
- `FLOW_TIMEOUT` (default 600 s) in `scripts/flow/run_capped.sh`; `docs/SOC_PLAN.md` says use 600 explicitly and
  give a smaller design when a step estimate approaches it. Biggest builds: `soc_image_text_match` 165 s,
  `prec_fp16` 104 s (README results table). A timeout well below those is a hang, not a size problem.

### 13. Max-slew counts that stay
- Slew violations are reported, not failed on. Classify before fixing: `classify_slew.py`. Port-driven nets (the
  Caravel input transition is above the limit at the pin) cannot be fixed by resizing. More repair margin made it
  worse (`tiny_ai_core` //SLEW: 70 % OOM, 40 % 247, 20 % 195). The wrapper's own 0 is not better: its limit is
  1.5 ns and it holds no cells (`designs/user_project_wrapper/NOTES.md`).

## Config keys that every hardened design carries (guard settings checked by `make test`)
`ERROR_ON_SYNTH_CHECKS` true, `RUN_POST_GRT_DESIGN_REPAIR` true, `MAX_FANOUT_CONSTRAINT` 8, slew margins,
`CLOCK_PERIOD` 25 ns (40 MHz) on `wb_clk_i`, `FP_SIZING` absolute with `DIE_AREA`, `RT_MAX_LAYER` met4,
`MAGIC_DRC_USE_GDS` true. See `tests/run_tests.sh` "config" check for the exact required set.

## What never to do (rule sources: SPEC.md "Agent operating rules" 7-12)
Loosen limits, `DISABLE_LVS`, suppress synthesis check errors, `SYNTH_STRATEGY` DELAY, accept deleted logic without
an allowance reason, edit fixed wrapper geometry, run flows in parallel, `cf` submit commands.
