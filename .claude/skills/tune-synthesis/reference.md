# tune-synthesis reference

Facts: the LibreLane 3.0.2 variable list (`examples/hermes_desktop/tool_server/whatif_keys.json`, generated from the pinned image by
`scripts/flow/gen_whatif_keys.py`), the ABC script builder of the image (`librelane/scripts/pyosys/construct_abc_script.py`), the committed
configs and `designs/vision_block/output/reports/`. "repo value" is what the 25 committed configs use.

## SYNTH_STRATEGY: what each value runs in ABC

`synthesize.py` calls `abc -D <CLOCK_PERIOD in ps>`, but none of the generated scripts below contains the `{D}` placeholder, so the period does not steer them in 3.0.2 (measured: vision_block has 297 cells at 25 ns and at 20 ns). The script is written to `06-yosys-synthesis/<STRATEGY_>.abc` of a run
(example: `designs/vision_block/runs/RUN_*/06-yosys-synthesis/AREA_0.abc`).

| value | script (from construct_abc_script.py) | use |
|---|---|---|
| `AREA 0` (default, all designs) | `fx; mfs; strash; drf -l`, resyn2, `retime -M 5`, `scleanup`, choice2, `amap -m -Q 0.1 -F 20 -A 20 -C 5000`, `retime`, `&get -n; &st; &dch; &nf; &put` | the committed baseline |
| `AREA 1` | as AREA 0 plus an area-recovery pass (choice2 + amap again) | small area gain attempt |
| `AREA 2` | choice2 used in the resynthesis step too, plus the area-recovery pass | strongest of the ABC area scripts |
| `AREA 3` | ORFS area script: `strash; dch; map -B 0.9; topo; stime -c; buffer -c -N <MAX_FANOUT>; upsize -c; dnsize -c` | different mapper; can be bigger (LibreLane sample table: 11620 cells vs about 6700) |
| `DELAY 0` .. `DELAY 4` | speed-oriented (`map` mapper, `&if -g -K 6` loops for DELAY 4) | FORBIDDEN here (CLAUDE.md); blocked by `propose_change` |

Only a measured comparison (cells, area, timing after the whole flow) decides; LibreLane itself says there is no way to know the best strategy beforehand.

## Hierarchy and what is kept

`SYNTH_HIERARCHY_MODE`: `flatten` (default, ABC sees the whole design), `deferred_flatten` (flatten after synthesis), `keep` (never). Partial keeping:
`SYNTH_KEEP_HIERARCHY_MODULES`, `SYNTH_KEEP_HIERARCHY_INSTANCES`, `SYNTH_KEEP_HIERARCHY_MIN_COST` (they only act when flattening is on). Keeping hierarchy
raises the cell count (no cross-module optimisation) but `synth_stat.rpt` then prints one table per module: use it to see which block (ROM, MAC, adapter) owns the area.

## Buffering, sizing, fanout

- `SYNTH_ABC_BUFFERING` (alias `SYNTH_BUFFERING`): ABC `buffer -N <MAX_FANOUT_CONSTRAINT> [-S <MAX_TRANSITION_CONSTRAINT in ps>]; upsize; dnsize`.
- `SYNTH_SIZING`: ABC `upsize; dnsize` only. If both are true buffering wins (the script uses `if buffering ... elif sizing`).
- `SYNTH_DIRECT_WIRE_BUFFERING`: buffer on input-to-output feed-throughs; keep true (else netlist `assign` errors, step 09).
- `MAX_FANOUT_CONSTRAINT`: used by ABC buffering and by `repair_design`; 8 in 21 designs, 16 in user_proj_example (template), PDK default 10. Tighten only.
- Later steps repair what synthesis leaves: `repair_design` after placement (rsz engine, skill `tune-openroad-engines`).

## How to read the synthesis reports

| file (committed in `designs/<d>/output/reports/`, run copy in `06-yosys-synthesis/reports/`) | what it shows |
|---|---|
| `synth_stat.rpt` (`stat.rpt`) | `Printing statistics`: wires, cells, area per cell type (`sky130_fd_sc_hd__dfxtp_2` = flip-flops), total area; per-module blocks when hierarchy is kept. vision_block: 168 cells, 1.73E+03 um^2, 24 `dfxtp_2` |
| `synth_checks.rpt` (`chk.rpt`) | Yosys `check` pass: "Found and reported 0 problems." Anything else fails step 08 while `ERROR_ON_SYNTH_CHECKS` is true |
| `pre_synth_chk.rpt`, `latch.rpt` | check before mapping, inferred latches (any latch is an error) |
| `stat.json` | machine-readable area and cell counts |
| `AREA_0.abc` | the ABC script that ran |

Steps 07 (`checker-yosysunmappedcells`) and 09 (`checker-netlistassignstatements`) fail on unmapped cells and `assign` statements; metrics
`design__instance_unmapped__count`, `design__inferred_latch__count`, `synthesis__check_error__count` must be 0 (`check_signoff.py`).

## Logic lost and allowances

See SKILL.md section "Logic lost". `scripts/flow/signoff_allowances.json` format: `designs.<name>.removed_registers` plus `reason`; wrappers also `undriven_outputs` plus
`undriven_reason`. An entry is accepted only when the RTL shows the bits are constant or unread. Examples to read before adding anything: `kv_attn_n8_int4`.


## Every SYNTH_* key that is a what-if knob

Doc: LibreLane step -> engine table (Yosys + ABC are outside OpenROAD); .claude/skills/tune-synthesis/reference.md

| key | default | repo value (why) | safe range | symptom it fixes | verify with |
|---|---|---|---|---|---|
| `SYNTH_ABC_AREA_USE_NF` | False | default | true or false | Area recovery experiments. | synth_stat.rpt design area |
| `SYNTH_ABC_BUFFERING` (alias `SYNTH_BUFFERING`) | False | false for user_proj_example (explicit), default elsewhere | true or false | Fanout/slew violations already present at synthesis. | metrics.json max_fanout / max_slew counts |
| `SYNTH_ABC_DFF` | False | default | false for committed designs: merged flops change the register count and trip the 'logic lost' check unless allowed with a verifiable reason. | Never use to hide a failing check. | check_signoff.py register count |
| `SYNTH_ABC_LEGACY_REFACTOR` | False | default | true or false | Reproducing older results. | synth_stat.rpt |
| `SYNTH_ABC_LEGACY_REWRITE` | False | default | true or false | Reproducing older results. | synth_stat.rpt |
| `SYNTH_ABC_USE_MFS3` | False | default | true or false | Small QoR change; slower synthesis. | synth_stat.rpt |
| `SYNTH_ADDER_TYPE` | YOSYS | default | YOSYS (default), RCA, CSA, FA | Long adder carry chains in float and accumulator designs (prec_*). | timing path through the adder in timing_paths_max_ss.rpt |
| `SYNTH_AUTONAME` | False | default | false (default) | Debugging only. | - |
| `SYNTH_CHECKS_ALLOW_TRISTATE` | True | default | true (default) | Tri-state designs only. | synth_checks.rpt |
| `SYNTH_CLOCKGATE_MIN_WIDTH` (alias `USE_LIGHTER`) | unset | default | Needs the ICG cell variables; leave unset | Power experiments only. | metrics.json power__total |
| `SYNTH_CORNER` | unset | default | Default corner | Leave unset. | - |
| `SYNTH_DIRECT_WIRE_BUFFERING` (alias `SYNTH_BUFFER_DIRECT_WIRES`) | True | default | Leave true; false can give assign statements, which the netlist check rejects. | Netlist 'assign' errors from checker-netlistassignstatements. | 09-checker-netlistassignstatements result |
| `SYNTH_ELABORATE_ONLY` | False | default | Never set true on a macro or engine | BLOCKED by the what-if tools: a netlist that is not mapped is no sign-off. | - |
| `SYNTH_HIERARCHY_MODE` (alias `SYNTH_NO_FLAT`, `SYNTH_ELABORATE_FLATTEN`, `SYNTH_FLAT_TOP`) | flatten | default | flatten or deferred_flatten for the committed style; keep only to study a block. | Cannot see which block dominates area: keep (with SYNTH_KEEP_HIERARCHY_*) shows per-module cells in synth_stat.rpt. | synth_stat.rpt per-module sections |
| `SYNTH_KEEP_HIERARCHY_INSTANCES` | unset | default | Instance names that exist | Per-instance area report. | synth_stat.rpt |
| `SYNTH_KEEP_HIERARCHY_MIN_COST` | unset | default | Positive integer | Very large flattened netlists that ABC handles slowly. | synth_stat.rpt |
| `SYNTH_KEEP_HIERARCHY_MODULES` | unset | default | Module names that exist in the RTL | Per-module area report. | synth_stat.rpt |
| `SYNTH_MUL_BOOTH` | False | default | true or false | Multiplier area/delay trade. | synth_stat.rpt |
| `SYNTH_NORMALIZE_SINGLE_BIT_VECTORS` | True | default | true (default) | Pin-name mismatches at LEF/pin level. | - |
| `SYNTH_PARAMETERS` | unset | default | Only parameters that exist in the RTL top | Parameter sweeps on the elaborated design (not a way to change committed RTL). | synth_stat.rpt |
| `SYNTH_SHARE_RESOURCES` | True | default | true (default) | Area; rarely changed. | synth_stat.rpt |
| `SYNTH_SIZING` | False | default | true or false | Weak drivers on slow paths; costs area. | synth_stat.rpt cell mix; timing_summary.rpt |
| `SYNTH_SPLITNETS` | True | default | true (default) | Readability of netlist only. | - |
| `SYNTH_STRATEGY` | AREA 0 | AREA 0 everywhere (the default) | AREA 0, AREA 1, AREA 2, AREA 3. Never any DELAY value. | Cell count / area too high, or a path too long: try the next AREA value and compare cells, area and slack. AREA 3 often bigger (LibreLane's own explore table: 11620 vs about 6700 cells on its sample). | synth_stat.rpt in the run dir (06-yosys-synthesis/reports/stat.rpt) and metrics.json design__instance__count__stdcell |
| `SYNTH_TIE_UNDEFINED` | low | default | low (default) or high | Undriven-net synthesis checks. | 08-checker-yosyssynthchecks; synth_checks.rpt |
