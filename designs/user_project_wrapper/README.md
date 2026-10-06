# user_project_wrapper

Caravel user project wrapper for tiny_ai_core: exactly one `tiny_ai_core` instance named `mprj`, no glue logic. Port list,
fixed DEF (pin geometry), signoff.sdc and the PDN ring/fixed config keys come unchanged from the pinned ChipFoundry
caravel_user_project template (see UPSTREAM.txt). The macro uses vccd1/vssd1; analog_io and user_clock2 are unused.

Run:

    make views DESIGN=tiny_ai_core            # export the hardened macro views into build/macros/tiny_ai_core/
    make flow-all DESIGN=user_project_wrapper # LibreLane 3.0.2 wrapper flow (elaborate-only, macro-first)

Status: hardened, signoff-clean (`make wrapper`, 2026-10-06). `designs/user_project_wrapper/output/metrics.json`:
Magic and KLayout DRC 0, LVS 0, XOR 0, antenna 0, route DRC 0, max-slew / max-cap / max-fanout 0, worst setup
+1.46 ns and hold +0.105 ns over all corners; layout run 59 s, 0.62 GB (`output/resources.json`). The Wishbone testbench (784 cases, 39,956
checks) passes through the wrapper's ports on the RTL, the synthesised netlist and the routed netlist, with the
macro's routed gate-level netlist inside (`build/macros/tiny_ai_core/nl/`).

How it got clean (each attempt is a lesson):
1. Lint failed: LibreLane lints the wrapper against the macro's netlist with power pins defined, so the config needs
   the macro's powered netlist (`pnl`), not only `nl`.
2. Global routing failed on congestion: the macro had 361 pins on its bottom edge, 16 um above the wrapper's own
   pin row. Fix: a simpler macro (only Wishbone + interrupt, 109 pins) with all pins on its bottom edge in the order of
   the wrapper's Wishbone pads, placed at (189.06, 87.04) um so the wires are short and one full power-strap group
   (all 8 nets, including vccd1 and vssd1) crosses it.
3. Hold failed (-0.894 ns) and the router added 40 unpowered antenna diodes (LVS 70 errors, nwell DRC): the macro had
   been hardened with default constraints and long input wires. Fix: harden the macro with the template's macro SDC
   (Caravel clock latencies, Wishbone input delays) and keep the Wishbone wires short.
4. The macro build ran out of memory: a 70% slew-repair margin made OpenROAD chase input nets whose Caravel input
   transition is already above the limit. Fix: 20% margin; those violations are reported as environment-limited.
5. Signoff flagged 204 undriven wrapper outputs (io_out, io_oeb, la_data_out): accepted explicitly, with the reason,
   in `scripts/flow/signoff_allowances.json` (learning build). Tapeout caveat: a floating io_oeb leaves the pad output
   enable undefined; drive it high from the macro or configure every user GPIO as an input in `user_defines.v`.
   Resolved the second way on 2026-10-06 (owner decision, see "Status after the precheck" below).

Status after the precheck (2026-10-06): `rtl/user_defines.v` now sets GPIO 5..37 to `GPIO_MODE_MGMT_STD_INPUT_NOPULL`
(header comment "LOCAL CHANGE (owner decision 2026-10-06)"); it is no longer the template's file. With that change the local ChipFoundry
precheck passes 14 of 14 checks (it was 12 of 14 before: `gpio_defines` and `oeb` failed on the template's `GPIO_MODE_INVALID`;
`docs/PRECHECK.md`, `precheck/results/summary.tsv`). The full-Caravel simulations were run for this wrapper with the real management core
and firmware: `make caravel-rtl` PASS, `make caravel-gl` (hybrid) PASS, `make caravel-fullgl` (full-chip gate level) PASS, and
`make caravel-sdf-wrapper` (wrapper + macro with SDF, three corners) PASS (`docs/CARAVEL_SIM.md`). Not done: full-chip GL with SDF and
firmware (too slow in the emulated amd64 container), ChipFoundry's own `mpw_precheck` image, and every `cf` account step (human-only).
`lvs_config.json` is used by the precheck (with the macro netlist path changed in the staged copy, `docs/PRECHECK.md`); LibreLane does not read it.
