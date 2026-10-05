# user_project_wrapper

Caravel user project wrapper for tiny_ai_core: exactly one `tiny_ai_core` instance named `mprj`, no glue logic. Port list,
fixed DEF (pin geometry), signoff.sdc and the PDN ring/fixed config keys come unchanged from the pinned ChipFoundry
caravel_user_project template (see UPSTREAM.txt). The macro uses vccd1/vssd1; analog_io and user_clock2 are unused.

Run:

    make views DESIGN=tiny_ai_core            # export the hardened macro views into build/macros/tiny_ai_core/
    make flow-all DESIGN=user_project_wrapper # LibreLane 3.0.2 wrapper flow (elaborate-only, macro-first)

Status: hardened, signoff-clean (`make wrapper`, 2026-10-06). `designs/user_project_wrapper/output/metrics.json`:
Magic and KLayout DRC 0, LVS 0, XOR 0, antenna 0, route DRC 0, max-slew / max-cap / max-fanout 0, worst setup
+1.46 ns and hold +0.105 ns over all corners; layout run 53 s, 0.62 GB. The Wishbone testbench (784 cases, 39,956
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

Not done: GPIO startup modes for pads 5..37 (`rtl/user_defines.v` is still the template's), full-Caravel simulation
with management firmware, and the ChipFoundry precheck (`lvs_config.json` is prepared for it). rtl/user_defines.v is still the template's (GPIO startup modes for pads 5..37 are not set).
lvs_config.json is for the later ChipFoundry precheck only; LibreLane does not read it.
