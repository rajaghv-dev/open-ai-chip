# Sources

Every file below was copied from `../open-ai-silicon` at commit `78fa678829cdfca02a6fea747bf1f43b9eb1c743`
(private, same owner) unless another source is named. Nothing in this repository depends on that directory at run time.

| File here | Source | License | Local changes |
|---|---|---|---|
| `designs/user_proj_example/rtl/user_proj_example.v`, `rtl/defines.v`, `rtl/LICENSE` | `chipfoundry/caravel_user_project` @ `b510613cec367828966b37583f9090ac5ddb6491` (`verilog/rtl/`), via open-ai-silicon `designs/ci_user_proj_example/rtl/` | Apache-2.0 | none; byte-identity checked by `tests/upstream.sha256` |
| `designs/user_proj_example/{config.json,pin_order.cfg,base_user_proj_example.sdc}` | template `openlane/user_proj_example/`, as adapted in open-ai-silicon `designs/ci_user_proj_example/` | Apache-2.0 | none beyond the adaptation listed in `rtl/UPSTREAM.txt` (200 x 200 um die, LibreLane 3 keys, pins on four sides) |
| `designs/user_proj_example/rtl/UPSTREAM.txt` | open-ai-silicon `designs/ci_user_proj_example/rtl/UPSTREAM.txt` | - | paths renamed to `designs/user_proj_example/` |
| `designs/user_proj_example/tb/user_proj_example_tb.v` | open-ai-silicon `designs/ci_user_proj_example/tb/ci_user_proj_example_tb.v` | Apache-2.0 | module and file renamed `user_proj_example_tb` |
| `scripts/flow/run_capped.sh`, `find_reusable_run.py`, `gl_sim.sh`, `check_signoff.py`, `design_info.py`, `signoff_allowances.json` | open-ai-silicon `scripts/flow/` | - | design list reduced to `user_proj_example`; container prefix `oac_cap_`; hint texts |
| `scripts/flow/collect.sh` | open-ai-silicon `scripts/view/view_results.sh` | - | header, design order, hint texts; reports matched by step name instead of hard-coded step numbers (the reference copied none for this design) |
| `Makefile`, `scripts/flow/summary.py` | condensed from open-ai-silicon `Makefile`, `mk/flow.mk`, `mk/checks.mk`, `mk/exercise.mk` | - | one design, no registry |
| `scripts/doctor.sh`, `tests/run_tests.sh`, `versions.lock` | new, pins from open-ai-silicon `versions.lock` | - | - |

Not copied: the MNIST designs, `designs/user_proj_example` of open-ai-silicon (an MNIST network that reused the
template's module name), generated outputs, historical metrics. `designs/user_proj_example/output/` is produced here.
