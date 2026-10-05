# user_project_wrapper_soc_itm

Caravel user project wrapper for soc_image_text_match (Wishbone adapter + image_text_match engine): exactly one
`soc_image_text_match` instance named `mprj`, no glue logic. Module name stays `user_project_wrapper` (Caravel needs it).
Same wrapper, fixed DEF, signoff.sdc and macro placement as designs/user_project_wrapper (tiny_ai_core); only the macro
differs (same 109-pin interface and 250 x 250 um footprint). Register map: designs/soc_image_text_match/README.md.
See UPSTREAM.txt for what was copied and changed.

Run:

    make views DESIGN=soc_image_text_match
    make flow-all DESIGN=user_project_wrapper_soc_itm

The 204 undriven wrapper outputs (io_out, io_oeb, la_data_out) are accepted in scripts/flow/signoff_allowances.json,
as for user_project_wrapper (same tapeout caveat). Results: output/metrics.json, NOTES.md.
