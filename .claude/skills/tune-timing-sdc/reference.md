# tune-timing-sdc reference

Sources: `librelane/scripts/base.sdc` and `config/flow.py` of the pinned image (3.0.2), `designs/soc_kv_attn_n8/base_soc.sdc`,
`designs/tiny_ai_core/base_tiny_ai_core.sdc`, `designs/user_project_wrapper/signoff.sdc`, `designs/vision_block/output/reports/`,
`.claude/skills/harden-design/reference.md`, `.claude/skills/wrapper-build/reference.md`.

## Constraint variables (what the generic base.sdc does with them)

| variable (default) | SDC statement in base.sdc | rule | repo |
|---|---|---|---|
| `CLOCK_PERIOD` (10 ns library; **25 in every config**) | `create_clock -period $::env(CLOCK_PERIOD)`; input/output delay base; `abc -D` | tighten only | 25 (40 MHz) |
| `IO_DELAY_CONSTRAINT` (20 %) | `set_input_delay` / `set_output_delay` = period x percent / 100 on all inputs except the clock and all outputs | raise only | default |
| `CLOCK_UNCERTAINTY_CONSTRAINT` (0.25 ns) | `set_clock_uncertainty` | raise only | default |
| `CLOCK_TRANSITION_CONSTRAINT` (0.15 ns) | `set_clock_transition` | raise only | default |
| `TIME_DERATING_CONSTRAINT` (5 %) | `set_timing_derate -early 1-x`, `-late 1+x` | raise only | default |
| `OUTPUT_CAP_LOAD` (33.442 fF) | `set_load` on all outputs | raise only | default |
| `MAX_TRANSITION_CONSTRAINT` (PDK 0.75 ns) | `set_max_transition` (also ABC `buffer -S`, CTS slew) | tighten only | not set (0.75); wrappers 1.5 (fixed) |
| `MAX_FANOUT_CONSTRAINT` (PDK 10) | `set_max_fanout` | tighten only | 8 (21 designs), 16 (user_proj_example) |
| `MAX_CAPACITANCE_CONSTRAINT` (unset) | `set_max_capacitance` when provided | tighten only | unset |
| `SYNTH_DRIVING_CELL` (`inv_2/Y`) | `set_driving_cell` on inputs | not a knob | PDK |
| propagated clocks | `set_propagated_clock [all_clocks]` unless `OPENLANE_SDC_IDEAL_CLOCKS` | fixed | propagated |

`MAX_TRANSITION_CONSTRAINT` unset means: 10 percent of the clock period unless that exceeds the PDK `DEFAULT_MAX_TRAN`; with a 25 ns clock that is 2.5 ns, so the PDK value 0.75 ns applies.

## The Caravel macro SDC (`base_tiny_ai_core.sdc`, `base_soc.sdc`; template `base_user_proj_example.sdc`)

Same numbers in both macro files; `PNR_SDC_FILE` and `SIGNOFF_SDC_FILE` point to the same file so the macro is hardened in the context the wrapper checks it in
(the default SDC gave hold -0.894 ns in the wrapper; with the template SDC +0.105 ns, `designs/user_project_wrapper/README.md` item 3).

| item | value | meaning |
|---|---|---|
| clock | `create_clock -period CLOCK_PERIOD` on `wb_clk_i` | 25 ns |
| clock source latency | max 5.57, min 4.65 ns (`clk_max_latency`, `clk_min_latency`) | the wrapper's clock tree delay spread |
| clock input transition | 0.61 ns (`clk_tran`) | |
| uncertainty / transition / derate | `SYNTH_CLOCK_UNCERTAINTY`, `SYNTH_CLOCK_TRANSITION`, `SYNTH_TIMING_DERATE` | old names (deprecated aliases) that `librelane/scripts/openroad/common/io.tcl` sets from `CLOCK_UNCERTAINTY_CONSTRAINT`, `CLOCK_TRANSITION_CONSTRAINT`, `TIME_DERATING_CONSTRAINT` (percent / 100) before the SDC is read, so those variables do reach the Caravel SDC |
| reset | `set_input_delay` 0.5 x period on `wb_rst_i` | |
| input delays (max) | wbs_sel 3.17, wbs_we 3.74, wbs_adr 3.89, wbs_stb 4.13, wbs_dat 4.61, wbs_cyc 4.74 ns | min values 0.79 to 1.86 ns |
| input transitions (max) | wbs_dat_i 0.84 ns, wbs_adr_i 0.92 ns, others 0.14 to 0.18 ns | the two big ones exceed the 0.75 ns slew limit: environment-limited slew violations, see harden-design failure 1 |
| output delays (max) | irq 0.7, wbs_dat_o 3.62, wbs_ack_o 8.41 ns | |
| output load | 0.19 (`set_load`) | |
| multicycle paths | setup 2 / hold 1 through `wbs_ack_o`, `wbs_cyc_i`, `wbs_stb_i` | |
| max transition / fanout | `MAX_TRANSITION_CONSTRAINT`, `MAX_FANOUT_CONSTRAINT` | variables |

Never relax any of these numbers to pass a check. They are measured Caravel behaviour, not tuning parameters. `signoff.sdc` of the wrappers is the fixed Caravel version.

## PNR versus SIGNOFF SDC

LibreLane has `PNR_SDC_FILE` (floorplan, placement, CTS, repair) and `SIGNOFF_SDC_FILE` (post-route STA, `stapostpnr`); if only `FALLBACK_SDC_FILE` or none is given, the base SDC is used for both.
Giving different files lets a PnR step be tighter than sign-off; this repo uses the same file for both. `check-sdc-files` (step 10) fails when a named file is missing. Both are design files: not what-if knobs.

## Slack margins (resizer)

| key | step | default | repo | effect |
|---|---|---|---|---|
| `PL_RESIZER_SETUP_SLACK_MARGIN` | 37 `resizertimingpostcts` | 0.05 ns | 0.5 prec_fp8/fp16/bf16, 0.4 user_proj_example | repair until setup slack >= margin |
| `PL_RESIZER_HOLD_SLACK_MARGIN` | 37 | 0.1 ns | 0.4 user_proj_example | repair until hold slack >= margin (overfix) |
| `GRT_RESIZER_SETUP_SLACK_MARGIN` | `resizertimingpostgrt` (only if `RUN_POST_GRT_RESIZER_TIMING`) | 0.025 ns | 0.5 prec_*, 0.2 user_proj_example | after global routing |
| `GRT_RESIZER_HOLD_SLACK_MARGIN` | same | 0.05 ns | 0.2 user_proj_example | |
| `*_MAX_BUFFER_PCT` (50), `*_REPAIR_TNS_PCT`, `*_MAX_UTIL_PCT` | | | | limits of the repair |
| `*_ALLOW_SETUP_VIOS` (false) | | | | blocked to true |

More keys with symptoms and verification: `.claude/skills/tune-openroad-engines/reference.md` (rsz tables).

## The nine corners

`STA_CORNERS` = {nom, min, max} x {tt_025C_1v80, ss_100C_1v60, ff_n40C_1v95} (interconnect corner x process/voltage/temperature). `DEFAULT_CORNER` nom_tt_025C_1v80 is used by synthesis and
placement. Post-route sign-off reports all nine (`57-openroad-stapostpnr/<corner>/`). Setup is limited at `ss` (slow), hold at `ff` (fast): vision_block committed worst
setup +13.53 ns at max_ss, hold +0.110 ns at min_ff (`output/reports/timing_summary.rpt`). `check_signoff.py` fails on any negative worst slack in any corner.

## Reading the reports

| file | content |
|---|---|
| `output/reports/timing_summary.rpt` | table per corner: hold worst slack, hold TNS, violation count, setup worst slack, TNS, count, max cap and max slew violation counts; row "Overall" |
| `output/reports/timing_paths_max_ss.rpt` | the worst setup path at max_ss: startpoint, endpoint, per-stage fanout, cap, slew, delay, arrival, required time, slack |
| `output/reports/timing_paths_min_ff.rpt` | the worst hold path at min_ff |
| run `57-openroad-stapostpnr/<corner>/{max,min,checks,clock,skew.max,skew.min,tns.*,power}.rpt` | full per-corner reports |
| `metrics.json` | `timing__setup__ws`, `timing__hold__ws` (overall) and `...__corner:<c>`; `timing__setup_vio__count`, `timing__hold_vio__count`; `design__max_slew_violation__count` |

A positive slack passes. In `max.rpt`: slack = required - arrival; setup required = period + clock arrival - uncertainty - setup time; input paths start at the input external delay.
Example in the committed vision_block report: `s_data[2]` input with 5.0 ns external delay (20 percent of 25 ns) into a flip-flop.

## Tool and file map

`param_info {design, key}` (value, rule), `propose_change`, `whatif_run`, `whatif_result`, `whatif_sweep` (e.g. CLOCK_PERIOD over 25, 22, 20, 18 to find where setup fails);
`scripts/flow/check_signoff.py <d> --metrics <file>` for the sign-off verdict of any metrics.json.
