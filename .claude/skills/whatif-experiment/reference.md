# whatif-experiment reference

Facts from `examples/hermes_desktop/tool_server/whatif_tools.py`, `scripts/flow/whatif_flow.sh`, `tests/tools/test_whatif.py` and the real runs of 2026-10-07 on `vision_block`.

## Tools (all `POST /<name>` on the tool server, operationId == name)

| tool | arguments | returns | gate |
|---|---|---|---|
| `param_info` | `design`, optional `key`, optional `engine` | with key: current value and source, default, engine, meaning, safe range, symptom, verify-with, repo choice, the design's `//KEY` comment, rule, doc link; without: tunable keys by engine with current values | none (read-only) |
| `propose_change` | `design`, `changes` | per key `allowed`/`blocked` + rule + reason, `patch_text`, `patch_file` (build/whatif/patches/), `applied: false` | none; writes only the patch file |
| `whatif_run` | `design`, `changes`, `tag`; second call `confirm_id` | first: `needs_confirmation`, `confirm_id`, `plan`; second: `job_id`, `copy` | two-step "yes, run <id>" (valid 10 min, single use); `CHIP_TOOLS_NO_CONFIRM=1` skips it for tests |
| `whatif_result` | `tag` or `job_id` or `sweep_id` (+ optional `design`) | state, comparison table (markdown + rows), per-corner table, verdict, `signoff_check`, `committed_run_still_current` | none |
| `whatif_sweep` | `design`, `key`, `values` (2..6); second call `confirm_id` | first: plan + `confirm_id`; second: `job_id`, `sweep_id`, runs | two-step gate, one job, runs in sequence |
| `whatif_list` | none | copies with design, tag, changes, state; saved patches | none |
| `whatif_clean` | `tag` or `all: true` | removed paths (only under build/whatif/), running copies kept | none |

## Rule table (`check_key`; ids appear in `blocked[].rule`)

| id | blocked | text |
|---|---|---|
| R1-CLOCK | `CLOCK_PERIOD` above the current value | never loosen CLOCK_PERIOD (25 ns); shorter is allowed |
| R1-SLEW | `MAX_TRANSITION_CONSTRAINT` above the current (0.75 ns when unset) | never loosen; lower is allowed |
| R1-TIGHTEN | other constraints in the loosening direction: `MAX_FANOUT_CONSTRAINT`, `MAX_CAPACITANCE_CONSTRAINT` (lower only); `CLOCK_UNCERTAINTY_CONSTRAINT`, `CLOCK_TRANSITION_CONSTRAINT`, `TIME_DERATING_CONSTRAINT`, `IO_DELAY_CONSTRAINT`, `OUTPUT_CAP_LOAD` (higher only) | never weaken timing |
| R2-LVS | `DISABLE_LVS`, `RUN_LVS`, anything with LVS or NETGEN in the name | never DISABLE_LVS |
| R3-DELAY | `SYNTH_STRATEGY` `DELAY n` | only AREA 0..3 |
| R4-SYNTH-CHECKS | `ERROR_ON_SYNTH_CHECKS` false | stays true |
| R5-WEAKEN | `RUN_*` signoff/repair flags false when on, `ERROR_ON_*` false, `MAGIC_DRC_USE_GDS` false, `*_VIOLATION_CORNERS`, `STA_CORNERS`, `PNR_CORNERS`, `RSZ_CORNERS`, `MAGIC_*`, `KLAYOUT_*`, `RCX_*`, `STA_*`, `LINTER*` | never weaken DRC, LVS, timing or precheck |
| R6-GEOMETRY | on `user_project_wrapper*` / FP_DEF_TEMPLATE / MACROS designs: `DIE_AREA`, `CORE_AREA`, `FP_*`, `IO_PIN*`, `PDN_*`, `RT_*`, `MACRO_*`, margins | fixed wrapper geometry and pins |
| R7-ELABORATE | `SYNTH_ELABORATE_ONLY` true | wrappers only |
| R8-DESIGN-FILE | design identity, files, nets, macros (`DESIGN_NAME`, `VERILOG_FILES`, `*_SDC_FILE`, `IO_PIN_ORDER_CFG`, `MACROS`, `VDD_NETS`, `PDK*`, cell lists, ...) | owner-only |
| R9-UNKNOWN | not a LibreLane 3.0.2 variable (411 known, `whatif_keys.json`) | a typo would be a silent no-op |
| R10-TYPE | wrong type/enum/range | |
| R11-MARGIN | negative `*_SLACK_MARGIN`, `*_ALLOW_SETUP_VIOS` true | repair target loosened |

Deprecated spellings are mapped to the 3.0.2 name (`PL_RESIZER_MAX_SLEW_MARGIN` -> `DESIGN_REPAIR_MAX_SLEW_PCT`); a config that already uses the old spelling keeps it in the copy so the key is never duplicated.
Numeric and boolean strings from a model ("20", "true") are coerced.

## Files

| path | content |
|---|---|
| `build/whatif/<design>__<tag>/config.json` | the design config with changes and rewritten `dir::` paths (paths into the copied design dir stay; every other target becomes relative to the copy) |
| `.../whatif_meta.json` | design, tag, changes, job_id, created (sweep_id) |
| `.../runs/<tag>/` | the LibreLane run (`final/metrics.json`, `flow.log`, `57-openroad-stapostpnr/<corner>/`) |
| `.../whatif_resources.json` | exit code, wall seconds, container peak memory |
| `.../signoff.txt` | `check_signoff.py` output on the new metrics |
| `build/whatif/patches/*.patch` | PATCH TEXT from propose_change (never applied) |
| `build/whatif/sweeps/<id>.json` | sweep manifest |
| `build/agent/jobs/<job>.log` | job log |
Copy contents: `designs/<d>/` without `runs/`, `output/`, `tb/`, `*.md`. `build/` is git-ignored.

## Measured example (2026-10-07, vision_block, tight profile; committed: 297 cells, 24 flip-flops, 80 x 80 um, utilisation 60.27 %, setup +13.53 ns max_ss, hold +0.110 ns min_ff, 51 s)

| what-if | cells | util % | setup ws ns | hold ws ns | DRC/LVS/XOR/antenna | wall s | verdict |
|---|---|---|---|---|---|---|---|
| `PL_TARGET_DENSITY_PCT` 70 | 297 | 60.4 | 13.589 | 0.1135 | 0 | 86 | clean |
| `CLOCK_PERIOD` 20 | 297 | 60.27 | 9.53 | 0.1103 | 0 | 66 | clean (setup -4.0 ns, power +0.032 mW) |
| `CLOCK_PERIOD` 30 | refused: R1-CLOCK | | | | | | |
| `MAX_TRANSITION_CONSTRAINT` 1.0 | refused: R1-SLEW | | | | | | |
After both runs `find_reusable_run.py vision_block` still exited 0 (committed run current) and `designs/vision_block/` had no changed file. Source: `build/whatif/vision_block__dens70/`, `__clk20/` (regenerable, git-ignored).

## Failure table

| symptom | cause | action |
|---|---|---|
| `a flow container is already running (<name>)` | another session runs a flow (`docker ps`) | wait; never start a second physical flow |
| `a physical-flow job is already running` | tool server lock | wait or `job_cancel` |
| `tag ... already exists` | earlier copy | `whatif_clean {tag}` or pick another tag |
| `dir:: path ... does not resolve` | a config path points at a file that is not in the repo | fix the design (owner), not the copy |
| flow exit 124 or `TIMEOUT after 600 s` | repair thrash or slow design | keep the log, report; do not raise FLOW_TIMEOUT silently |
| `whatif_result` state `failed` | LibreLane stopped (see `log_tail`, `runs/<tag>/flow.log`) | read the first error; harden-design failure table |
| `signoff_check` FAIL | a check failed in the copy | that is the result; report it |
| `whatif_result` says committed run not current | someone edited `designs/<d>/` | find who; not caused by the tools |
