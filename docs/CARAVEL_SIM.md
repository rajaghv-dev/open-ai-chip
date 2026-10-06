# Full-Caravel simulation (ladder steps iv and v)

Scope: all runs below are for `designs/user_project_wrapper` (macro `tiny_ai_core`) only. `user_project_wrapper_soc_itm` and
`user_project_wrapper_soc_kv` were not run through caravel_sim (they are hardened with clean signoff; their firmware-level checks are the
PicoRV32 sims `make soc-sim` / `make soc-kv`, firmware/README.md, which are not Caravel sims). Make targets: `make caravel-rtl`,
`caravel-gl`, `caravel-fullgl`, `caravel-sdf-wrapper`.

Result: working natively on the Mac. A real VexRiscv management-core program, booted from a SPI flash model inside the
complete Caravel RTL, reads tiny_ai_core's ID through the real Wishbone path, runs one case per mode and checks RESULT and
CYCLES against model/tiny_ai/golden.py.

| Step | Command | Result | Wall time |
|---|---|---|---|
| (iv) RTL | `caravel_sim/run_rtl.sh` | `Monitor: tiny_ai_core Caravel firmware (RTL) PASS` (pass flag at 4102.5 us sim time) | 53 s |
| (v) gate-level, hybrid | `caravel_sim/run_gl.sh` | `Monitor: tiny_ai_core Caravel firmware (GL) PASS` | 59 s |
| negative check | `EXTRA_CFLAGS=-DNEG_TEST caravel_sim/run_rtl.sh` | `Monitor: tiny_ai_core Caravel firmware FAIL code 0xe032` | 55 s |

Step (v) is hybrid: user_project_wrapper (the newest run's final/pnl by mtime; RUN_2026-10-05_19-52-33 for the table's first runs, RUN_2026-10-06_03-29-55 since the GPIO fix) and tiny_ai_core (build/macros/tiny_ai_core/pnl)
are our routed power-aware netlists with sky130_fd_sc_hd functional models and unit delay; Caravel and the management core
stay RTL. Full-chip GL and SDF are in the next section. Not done: cocotb/Docker route.

Versions: caravel-lite CC2509 (58c8c77), caravel_mgmt_soc_litex CC2509 (503eda0), both from the template b510613 Makefile pins;
riscv64-elf-gcc 16.2.0, iverilog 13, sky130A at ~/.volare. Downloads: caravel 953 MB, mgmt core 4.1 GB (shallow). Work dir 182 MB.
Details, firmware, and exact commands: caravel_sim/README.md. Caveats: needs `-march=rv32i_zicsr`; the template's
riscv32-unknown-linux-gnu toolchain prefix is replaced; the wrapper pnl of the first runs came from an earlier wrapper run than the macro export
(identical interface). After the GPIO fix (project `user_defines.v`, GPIO 5..37 management-owned inputs) RTL and hybrid GL were re-run and PASS
(build/caravel_rtl_gpio.log, build/caravel_gl_gpio.log). No amd64 Docker image was needed.

## Full-chip gate-level and SDF (ladder step v, full)

### Full-chip gate-level, no SDF: PASS, native
`caravel_sim/run_fullgl.sh` (iverilog 13, native, no emulation). Everything is a gate-level netlist: caravel-lite CC2509 `gl/` (caravel,
chip_io, caravel_core - one flat netlist that already contains the management SoC, VexRiscv and the DFFRAM macros as cells - housekeeping,
gpio/logic-high/defaults blocks, mgmt_protect, clocking...), our newest `user_project_wrapper/runs/*/final/pnl` (RUN_2026-10-06_03-29-55) and
`build/macros/tiny_ai_core/pnl`, sky130 functional cell models with unit delay, the repo's `designs/user_project_wrapper/rtl/user_defines.v`.
Same firmware (ID + 4 cases) and the same exact check (pass code must be 0xAB61, 0xE0xx = fail; no X can pass).

| Run | Result | Wall time |
|---|---|---|
| `caravel_sim/run_fullgl.sh` | `Monitor: tiny_ai_core Caravel firmware (GL) PASS` (checkbits 0xAB60 at 614.8 us, 0xAB61 at 4102.5 us) | 14 m 23 s = 604 s compile/elaborate + 260 s simulate (15.8 simulated us per wall second); first run 25 min on a loaded machine |

Caveat: the repo's `includes.gl.caravel` leaves `caravel_core.v` commented out, so the script enables it. No VCD is written (a full-chip
netlist VCD is multi-GB). mgmt_core_wrapper.v (the separately shipped SoC netlist) is not used by this flat configuration.

### SDF back-annotation
Which SDFs exist for CC2509:
- Ours: `designs/user_project_wrapper/runs/*/final/sdf/<corner>/user_project_wrapper__<corner>.sdf` (flat: contains every tiny_ai_core cell as
  `mprj.<cell>`, 984 cells, 2186 IOPATH, 2348 INTERCONNECT, 109 TIMINGCHECK blocks; 9 corners); the macro's own SDF
  (`designs/tiny_ai_core/runs/*/final/sdf`) is the same cells without wrapper parasitics and is not needed.
- mgmt_core_wrapper CC2509 ships SDFs (`signoff/mgmt_core_wrapper/{openlane,primetime}-signoff/sdf/{min,nom,max}/`, RAM128/RAM256 too), but they
  are for the hierarchical `gl/mgmt_core_wrapper.v`, not for the flat `caravel_core.v` that caravel-lite uses, so they do not apply to it.
- caravel-lite ships NO SDF (only SPEF in `signoff/*/openlane-signoff/spef`). `caravel_sim/gen_caravel_sdf.sh` makes one for the flat
  caravel_core with OpenSTA (the pinned librelane image, STA only, 20 s) from the shipped nom SPEF: 83.5k cells, 165k IOPATH, 1.09M INTERCONNECT,
  127 MB, corners tt/ss/ff of the liberty (SPEF is the nom one; housekeeping etc. internals have cell delays but no wire parasitics; tap/fill
  cells are black boxes; user_project_wrapper is a port stub there).

Simulator: iverilog is unusable: `$sdf_annotate` uses only its first two arguments, mis-parses `cell.pin` INTERCONNECT paths ("Submodule
ANTENNA_... not found"), asserts in `vpi_scan`, and timing checks are "not supported". The only open SDF simulator is Open Verilog CVC, which
is x86_64/Linux only (compiled-mode x86 assembly): it does not build on macOS arm64. It therefore runs in an amd64 container (colima profile
`osl`, vz, Rosetta/emulation) - the one place a non-native step is strictly required.
Setup used (build/caravel/cvc_src = github.com/cambridgehackers/open-src-cvc, OSS CVC 7.00b):
    git clone --depth=1 https://github.com/cambridgehackers/open-src-cvc build/caravel/cvc_src
    sed -i '' -E 's/::: "%([a-z0-9]+)"/::: "\1"/' build/caravel/cvc_src/src/v_sim.c        # modern gcc rejects "%rsp" clobbers
    docker run --name cvcb --platform linux/amd64 -v $PWD/build/caravel/cvc_src:/opt/cvc ubuntu:22.04 bash -c \
      'apt-get update -qq; apt-get install -y build-essential zlib1g-dev; cd /opt/cvc/src && make cvc64'   # ~3 min
    docker commit cvcb openchip-cvc64-base                                                              # image used by the scripts
CVC needs fixed copies of the cvc-pdk models (`@(*)` and UDP edges like `(0x)` are mis-lexed); the scripts make them under build/caravel/work/cvc-pdk
and drop the 36 INTERCONNECT entries that end at a top-level output port (CVC 7.00b aborts with ARG INTERNAL on them; all are cell-output to
wrapper-pin entries of 0 or tie-cell value).

#### Result 1: our blocks, gate-level + SDF, Wishbone testbench: PASS (`caravel_sim/run_sdf_wrapper.sh`, 5 s)
`tiny_ai_wrapper_sdf_tb.v` drives the gate-level user_project_wrapper+tiny_ai_core over Wishbone with the firmware's exact accesses: ID 0x54414901,
vision_all_lit 1 1 1 1, status DONE and not ERROR, RESULT 0x0401, CYCLES 6 (exact compare with `!==`, X fails).
- nom_tt_025C_1v80: `Monitor: tiny_ai_core wrapper GL+SDF PASS`; nom_ss_100C_1v60 PASS; max_ss_100C_1v60 PASS (40 MHz clock).
- SDF is live (not just loaded): tt fails at clock periods <= 4 ns (half period 2 ns: 3 mismatches) and passes from 6 ns; max_ss fails at 6 ns,
  passes from 12 ns. Zero-delay would pass at any period. (Timing checks are not enforced: cell models are the functional ones, `+notimingchecks`,
  as in the template's GL_SDF flow; setup/hold closure is STA's job and is in the flow reports.)
- Annotation coverage of ours: all 2186 IOPATH entries (4018 log lines for rise/fall) and 2312 of 2348 INTERCONNECT entries (36 dropped, see above)
  were annotated; CVC's SDF log has 0 "not found/ignored" entries. 109 TIMINGCHECK blocks are in the file but not enforced.
  CVC warnings in the main log (count by CVC id, 3486 lines): 531 x 780 (tap-cell port lists), 653 x 809 ($recrem parentheses in the models),
  597 x 362, 679 x 347 (specify in top-level cell modules), 3153/3154/3155 x ~1060 (unsupported $setuphold optional args), 3106 x 98
  (INTERCONNECT from input port, handled as PORT), 654 x 28, 662 x 2.

#### Result 2: full-chip GL + SDF with firmware: NOT completed (blocked by simulation speed, documented evidence)
`SCOPE=full caravel_sim/run_sdf.sh` (flat gate-level Caravel + mgmt SoC + our wrapper, minimal firmware = ID read + one case, CVC `+interp`
in the amd64 container): the design elaborates in about a minute, but no simulated time was reached in the time-boxed attempts: 45 min with
no result (killed by the cap), and a 15 min probe with a 50 us heartbeat printed nothing (so under 50 simulated us in 15 min, below
0.06 us per wall second; native iverilog does 15.8). Extrapolating from native iverilog the run needs about 4000 us of simulated boot+test, so at
that speed it cannot finish in the 45 min cap. Not covered therefore: Caravel/management-core timing with SDF in a firmware run
(`CORE_SDF=1` generates and annotates the OpenSTA caravel_core SDF; untested because the base run does not finish), and the hierarchical variant
with the shipped mgmt_core_wrapper SDF. The RTL Caravel padframe also cannot be used with CVC (`chip_io.v` has an instance named like a port;
CVC rejects it), so the hybrid "RTL Caravel + SDF wrapper" under CVC is not possible either; the Wishbone testbench above is the largest
SDF scope that fits.
Unblock paths: (a) run `SCOPE=full ... SIM_CAP=14400` on an x86 Linux host (native CVC compiled mode, no `+interp`, is typically 10-100x faster
than this emulated interpreter); (b) shorten the boot (the SPI flash bit-bang dominates: 615 us of the 4100 us is before the firmware starts);
(c) a native arm64 CVC build is not available.

Commands: `caravel_sim/run_fullgl.sh`, `caravel_sim/run_sdf_wrapper.sh` (CORNER=..., CLK_HALF=... for the too-fast-clock check),
`caravel_sim/gen_caravel_sdf.sh` (CORNER=...), `SCOPE=full caravel_sim/run_sdf.sh` (MINIMAL=1 builds the firmware with `-DMINIMAL`).
Versions: iverilog 13.0, OSS CVC 7.00b-x86_64-rhel6x (open-src-cvc HEAD), OpenSTA 2.7.0 (librelane 3.0.2 image), caravel-lite CC2509 58c8c77,
mgmt core CC2509 503eda0, sky130A at ~/.volare.
