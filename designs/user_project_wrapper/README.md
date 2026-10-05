# user_project_wrapper

Caravel user project wrapper for tiny_ai_core: exactly one `tiny_ai_core` instance named `mprj`, no glue logic. Port list,
fixed DEF (pin geometry), signoff.sdc and the PDN ring/fixed config keys come unchanged from the pinned ChipFoundry
caravel_user_project template (see UPSTREAM.txt). The macro uses vccd1/vssd1; analog_io and user_clock2 are unused.

Run:

    make views DESIGN=tiny_ai_core            # export the hardened macro views into build/macros/tiny_ai_core/
    make flow-all DESIGN=user_project_wrapper # LibreLane 3.0.2 wrapper flow (elaborate-only, macro-first)

Status: integration in progress, not signoff-clean. RTL (one `tiny_ai_core mprj`, port list identical to the
template) and the Wishbone testbench pass in RTL simulation (784 cases, 65,642 checks). The first full wrapper runs
(2026-10-05): run 1 stopped in lint (fixed: the macro's powered netlist is now given as `pnl`); run 2 failed global
routing on congestion at the macro's bottom edge; with the macro moved to [299.92, 204] it routed (route DRC 0) but
failed signoff: hold -0.894 ns on Wishbone inputs (the macro was hardened without the Caravel timing context),
40 unpowered antenna diodes inserted by the wrapper router (LVS 70 errors, Magic DRC 25 nwell.4), and slew/cap on
long io_out wires. Next: re-harden the macro with the template's macro SDC, pins facing the wrapper pads and antenna
diodes inside the macro, then re-run. rtl/user_defines.v is still the template's (GPIO startup modes for pads 5..37 are not set).
lvs_config.json is for the later ChipFoundry precheck only; LibreLane does not read it.
