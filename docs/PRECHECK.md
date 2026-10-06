# Local ChipFoundry precheck (SPEC phase 9)

Date: 2026-10-06. Tool: `cf-precheck 1.3.7` (PyPI), run locally on this MacBook (aarch64 Colima VM `osl`, native, no x86
emulation, no cloud). No `cf login/init/push/submit`, no account, no upload: precheck needs none of them (it writes
only `<project>/.cf/project.json` locally; nothing from `cf init` was required or created).

## Command

    precheck/run_precheck.sh              # stage + all 14 checks, one container per check, 45 min cap per check
    precheck/run_precheck.sh lvs oeb      # subset
## Final result: 14 of 14 PASS (after the GPIO fix)

Final run: `build/precheck_final.log`, reports `build/precheck/results_20261006_090206/` (git-ignored), 61 s wall time (slowest:
LVS 14 s, OEB 14 s, KLayout BEOL 8 s, Magic DRC 6 s; `precheck/results/summary.tsv` is a copy of that run's `summary.tsv`).
Checks: topcell, gpio_defines, xor, magic_drc, klayout_feol, klayout_beol, klayout_offgrid, klayout_met_min_ca_density,
klayout_pin_label_purposes_overlapping_drawing, klayout_zeroarea, spike_check, illegal_cellname_check, lvs, oeb: all PASS.
It checks the final wrapper run `designs/user_project_wrapper/runs/RUN_2026-10-06_03-29-55` (owner decision 2026-10-06: GPIO 5..37 =
`GPIO_MODE_MGMT_STD_INPUT_NOPULL` in `designs/user_project_wrapper/rtl/user_defines.v`, see the fix section below).
Run natively on this Mac (aarch64 Colima container; cvc_rv built from source; KLayout 0.30.7 in the table, the pinned 0.29.12 also passes).
LVS still ends with the CVC ERC "warning" (`ERC check failed (stat=4)` in `build/precheck_final.log`; "Unexpected voltage: 28" in
`build/precheck/results_20261006_090206/lvs/logs/cvc.log`): the same 28 `io_out[0..6]` / `io_oeb[0..6]` entries as the baseline are still reported (nets listed in
`lvs/tmp/cvc.error.gz` of that run), and cf-precheck treats it as a warning, LVS is PASS.

**Caveat (honest scope):** this is our own container (magic/netgen from the LibreLane image, self-built cvc_rv and KLayout), not
ChipFoundry's `mpw_precheck` image. Confirmation with ChipFoundry's tooling, and every `cf` account step, remain human-only.

Earlier (baseline) run, before the fix: 55 s, 12 of 14 PASS; its small summaries are `precheck/results/evidence.txt` and
the baseline table below. Nothing came near the 45 min cap; no check is "NOT RUN".

## Inputs (staged into `build/precheck/project/`, never restructuring the repo)

| precheck expects | taken from |
|---|---|
| `gds/user_project_wrapper.gds` (the only wrapper-type GDS) | `build/results/user_project_wrapper/user_project_wrapper.gds` (macro GDS also copied as `gds/tiny_ai_core.gds`) |
| `verilog/gl/user_project_wrapper.v`, `verilog/gl/tiny_ai_core.v` | powered netlists (`final/pnl` of the wrapper run, `build/macros/tiny_ai_core/pnl`) |
| `verilog/rtl/*` incl. `user_defines.v` | `designs/user_project_wrapper/rtl/*` + tiny_ai_core and the engine RTL it instantiates |
| `lef/`, `def/` | `designs/*/output/*.lef`, `fixed_dont_change/user_project_wrapper.def` |
| `lvs/user_project_wrapper/lvs_config.json` | `designs/user_project_wrapper/lvs_config.json` with ONE change: macro netlist path -> staged powered `verilog/gl/tiny_ai_core.v` (the prepared file pointed at the unpowered `nl` under `build/macros`, which is outside a template-shaped project) |
| golden Caravel root (`-c`) | `build/caravel/caravel` = chipfoundry/caravel tag CC2509 (the template's `MPW_TAG`), gds.gz files gunzipped in a copy (XOR needs `gds/user_project_wrapper_empty.gds`, OEB `verilog/gl/caravel.v`) |
| PDK | sky130A open_pdks `3e0e31dc` (the template's `OPEN_PDKS_COMMIT`), fetched with ciel from ChipFoundry's public static mirror (`precheck/fetch_pdk.sh`, no login) |

## Tool versions actually used

cf-precheck 1.3.7; Magic 8.3.623 and Netgen 1.5.316 (from the LibreLane 3.0.2 image); cvc_rv 1.1.7 (built from
github d-m-bailey/cvc master 23c3867); KLayout binary **0.30.7** (LibreLane image) and pya 0.30.7. Mismatches:
- `versions.lock` in this repo has NO `CF_PRECHECK_VERSION / PRECHECK_KLAYOUT / CF_CLI_VERSION / PRECHECK_MAGIC / PRECHECK_NETGEN /
  LVS_PDK_COMMIT / OPEN_PDKS_COMMIT_SKY130A` lines (only LibreLane, PDK 8afc8346 and the template pins); I used the values in the task text
  and the template Makefile. The lock file should get these lines (not edited here).
- cf-precheck pins `klayout==0.29.12`, but PyPI has no aarch64 wheel older than 0.30.2, so the venv uses 0.30.7 (installed with
  `--no-deps`). A source build of 0.29.12 was also run, see below.
- PDK: LibreLane pinned 8afc8346; the precheck template expects 3e0e31dc. Both were run; results identical (the table is from 3e0e31dc;
  the 8afc8346 run, `PDK_ROOT=$HOME/.volare`, also gave GPIO FAIL, OEB FAIL, everything else PASS).
  The template's `OPEN_PDKS_COMMIT_LVS=6d4d1178` is not on the ciel mirror (manifest not found); cf-precheck 1.3.7 uses the one PDK passed with `-p`.

## Results of the baseline run (before the fix, history)

| Check | Result | Evidence |
|---|---|---|
| Top Cell | PASS | single top cell `user_project_wrapper` |
| GPIO Defines | **FAIL** | 33 invalid directives: `USER_CONFIG_GPIO_5..37_INIT = 13'hXXXX` (`GPIO_MODE_INVALID`) |
| XOR vs golden wrapper | PASS | total XOR differences 0 |
| Magic DRC | PASS | 0 violations |
| KLayout FEOL / BEOL / Offgrid | PASS / PASS / PASS | no violations |
| KLayout metal min clear-area density | PASS | no violations |
| KLayout pin/label purposes overlapping drawing | PASS | no violations |
| KLayout zero area | PASS | no violations |
| Spike check | PASS | no spikes |
| Illegal cellname | PASS | no `#` or `/` in cell names |
| LVS (hier check + Netgen full LVS) | PASS (with 28 CVC warnings) | "Circuits match uniquely." CVC (ERC, treated by cf-precheck as a warning, exit 4) reports 28 "Unexpected voltage": `io_out[0..6]` and `io_oeb[0..6]`, min and max, "expected 0/1.8 but found unknown" |
| OEB | **FAIL** | GPIO 5..37: "ERROR: missing gpio configuration" (33 errors) |

(`pdnmulti` and `metalcheck` do not apply: gf180-only / mini-only.) Overall: 12 of 14 PASS, 2 FAIL, both caused by `user_defines.v`. Both PASS after the fix (final run above).

## Why the two checks failed, and the fix (applied 2026-10-06)

1. GPIO Defines and OEB: `designs/user_project_wrapper/rtl/user_defines.v` is byte-identical to the template, whose pads 5..37 are
   `GPIO_MODE_INVALID`. Fix: set every `USER_CONFIG_GPIO_n_INIT`. Experiment on the staged copy only (not release evidence):

   | mode for pads 5..37 | gpio_defines | lvs | oeb |
   |---|---|---|---|
   | `GPIO_MODE_MGMT_STD_INPUT_NOPULL` | PASS | PASS | **PASS** ("No warnings or errors detected") |
   | `GPIO_MODE_USER_STD_INPUT_NOPULL` | PASS | PASS | FAIL (user-owned pad needs output and oeb connection) |
   | `GPIO_MODE_USER_STD_OUTPUT` | PASS | PASS | FAIL (missing user output/oeb) |

   Applied: `GPIO_MODE_MGMT_STD_INPUT_NOPULL` for 5..37 in both `designs/user_project_wrapper/rtl/user_defines.v` and
   `designs/user_project_wrapper_soc_itm/rtl/user_defines.v` (header comment "LOCAL CHANGE (owner decision 2026-10-06)"); `caravel_sim/run_rtl.sh`
   and `run_gl.sh` now use the project's `user_defines.v` instead of Caravel's default. Caravel sims after the fix
   (`build/gpio_fix_chain.log`): caravel-rtl PASS 54 s, caravel-gl (hybrid) PASS 59 s; full-chip GL also PASS (`docs/CARAVEL_SIM.md`).

   So the intentionally unconnected `io_out / io_oeb / la_data_out` are NOT a problem if the pads are management-owned inputs
   (the macro uses only Wishbone + irq, so this is consistent), but they do fail if any pad is user-owned. The README's tapeout caveat
   ("configure every user GPIO as an input") is only enough with the management-owned input mode. Resolved: the owner chose
   `GPIO_MODE_MGMT_STD_INPUT_NOPULL` for 5..37.
2. LVS CVC warnings (28): the same floating `io_out[0..6]` / `io_oeb[0..6]` (the 7 pads the user area drives on the CVC model; still 28 in the final run). Not a failure
   in cf-precheck 1.3.7 (LVS passes), but real: driving `io_oeb` high and `io_out` low from the macro (or a tie cell) would clear them; that
   needs a wrapper/macro change, i.e. contradicts the "no glue logic" owner decision, so it is left as an owner decision.
3. Not hidden: `la_data_out` floating is not flagged by any check in this version.

## Other caveats

- Design built with LibreLane 3.0.2; ChipFoundry's flow pins openlane CI2511 (LibreLane 2.4.6). The precheck only sees the GDS/netlists, so it
  does not care, but a ChipFoundry-side rebuild could differ.
- The table is from KLayout 0.30.7; the pinned 0.29.12 gives the same results (section below).
- Checks are not vacuous: Magic wrote a valid report (`COUNT: 0`), LVS log shows the full match, the DRC decks wrote their XML reports.

## Human-only steps that remain (not done, not allowed here)

`cf` CLI install and `cf login`/`cf init` (account), project registration/reservation, `cf precheck` via the CLI against the account's
project record, `cf push`/submit, `cf confirm`, payment. Also: confirming with ChipFoundry's own precheck tooling, and updating
`versions.lock` with the precheck pins. (The GPIO-mode decision is done.)

## KLayout 0.29.12 (pinned version) run

KLayout 0.29.12 was built from source for aarch64 with Qt (16 min, `precheck/docker/Dockerfile`, image `osl-precheck:klayout-0.29.12`;
pya inside the venv stays 0.30.7 because no 0.29.12 aarch64 wheel exists). Run with `PRECHECK_IMAGE=osl-precheck:klayout-0.29.12 precheck/run_precheck.sh`
(`precheck/results/summary_klayout_0.29.12.tsv`): XOR PASS, KLayout BEOL / Offgrid / Density / Pin label / Zero area PASS, Magic DRC, LVS,
spike, cellname unchanged. KLayout FEOL failed once with a SIGSEGV (exit 11, multi-threaded `CompoundRegionEdgeToPolygon`, no DRC finding) and
PASSED on two reruns: a flaky crash of this source build, not a design problem. Net: all KLayout checks pass on the pinned 0.29.12 as well
(FEOL needs a retry); a ChipFoundry-supplied KLayout binary was not available to test.

## Open items

- Add the precheck pins to `versions.lock` (owner).
- The tools/Docker pieces (magic/netgen from the LibreLane image, self-built cvc_rv and klayout) are not ChipFoundry's own `mpw_precheck` image;
  a final confirmation with ChipFoundry's own tooling remains a human step.
