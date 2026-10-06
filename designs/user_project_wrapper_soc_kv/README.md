# user_project_wrapper_soc_kv

Caravel user project wrapper for soc_kv_attn_n8 (Wishbone adapter + kv_attn_n8, the 8-slot KV-cache attention engine):
exactly one `soc_kv_attn_n8` instance named `mprj`, no glue logic. Module name stays `user_project_wrapper` (Caravel needs it).
Same wrapper, fixed DEF, signoff.sdc and 109-pin interface as designs/user_project_wrapper (tiny_ai_core) and
designs/user_project_wrapper_soc_itm; two things differ: the macro, and its footprint. soc_kv_attn_n8 is 300 x 300 um
(designs/soc_kv_attn_n8/config.json, `//DIE_AREA`), not 250 x 250 um, so it spans x 189.06..489.06, y 87.04..387.04 at the
same origin [189.06, 87.04] N. Register map: shared/rtl/wb_stream_adapter.v header (ID 0x5354_5201, base 0x3000_0000);
protocol: model/kv_attention/spec.md. See UPSTREAM.txt for what was copied and changed.

Run:

    make views DESIGN=soc_kv_attn_n8
    make flow-all DESIGN=user_project_wrapper_soc_kv

The 204 undriven wrapper outputs (io_out, io_oeb, la_data_out) are accepted in scripts/flow/signoff_allowances.json,
as for user_project_wrapper (same tapeout caveat). Results: output/metrics.json, NOTES.md (setup +1.448 ns, hold +0.105 ns,
all other signoff counts 0). The testbench tb/user_project_wrapper_soc_kv_tb.v passes on RTL and on both gate-level netlists.
This wrapper is not wired into the full-Caravel sims (caravel_sim/ is hard-wired to tiny_ai_core); NOTES.md says what that takes.
