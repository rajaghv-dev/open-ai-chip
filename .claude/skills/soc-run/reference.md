# soc-run reference

## Files (all paths from repo root)

| Path | Role |
|---|---|
| firmware/{main.c,start.S,link.ld,Makefile,bin2hex.py,gen_expected.py} | PicoRV32 firmware; `make -C firmware sim`; `build/expected.h` generated from model/tiny_ai/golden.py |
| soc_sim/{soc_tb.v,run.sh} | iverilog SoC: picorv32_wb + 64 KiB RAM + misc block + real user_project_wrapper; outputs soc_sim/build/{soc.vvp,sim.log} |
| soc_sim/third_party/picorv32/ | picorv32.v, YosysHQ main @ ef203c2b, ISC, unmodified (SOURCE.txt) |
| tests/adapter/{run.sh,adapter_tb.v} | adapter + engine tests; logs in build/adapter_tests/<engine>.log |
| shared/rtl/wb_stream_adapter.v | generic Wishbone-to-stream slave, TX/RX FIFOs (parameters TX_DEPTH/RX_DEPTH powers of 2, 2..64; tb uses 16) |
| designs/soc_image_text_match/ | first hardened adapter+engine macro (3201 cells, all 2,079 vectors pass) |
| caravel_sim/{README.md,VERSIONS.txt,run_rtl.sh,run_gl.sh,tiny_ai_wb.c,tiny_ai_wb_tb.v,includes.rtl.user} | full-Caravel RTL / hybrid GL |
| docs/{CARAVEL_SIM.md,SOC_PLAN.md} | results table; plan, ladder (section 4), firmware design (section 3) |

## soc_sim memory map (soc_sim/soc_tb.v header)

RAM 0x0000_0000..0x0000_FFFF; 0x2000_0000 CONSOLE W; 0x2000_0004 EXIT W (1 PASS); 0x2000_0008 CYCLE R; 0x2000_000C IRQST R
(bit0 sticky user_irq[0], any write clears); 0x3000_0000 tiny_ai_core / adapter window (256 B). Other addresses print
`access to unmapped address`. Watchdog 40 M cycles. Trap prints `FAIL: cpu trap`.

## wb_stream_adapter register map (shared/rtl/wb_stream_adapter.v header)

| Off | Name | Acc | Bits |
|---|---|---|---|
| 0x00 | ID | R | 0x5354_5201 |
| 0x04 | CTRL | RW | [0] irq enable (lane 0, reset 0); W bit 8 CLEAR (lane 1, self-clearing) |
| 0x08 | STATUS | R | [0] TX_FULL [1] TX_EMPTY [2] RX_EMPTY [3] BUSY [4] DONE [5] ERR_OVF [6] ERR_UF [15:8] TX level [23:16] RX level |
| 0x0C | TXDATA | W | [7:0] s_data, s_last=0 |
| 0x10 | TXLAST | W | [7:0] s_data, s_last=1 |
| 0x14 | RXDATA | R | [7:0] m_data [8] m_last [9] valid; read pops; empty returns 0 and sets ERR_UF |
| 0x18 | RXSTATUS | R | [0] EMPTY [1] HEAD_LAST [2] DONE [15:8] RX level; no side effect |
| 0x1C | CYCLES | R | first accepted TX beat to captured m_last beat, 16 bit saturating |
| 0x20 | CAPS | R | [7:0] version 1, [15:8] TX depth, [23:16] RX depth |

BUSY/DONE clear on CLEAR and on the next run's first TX beat. ERR_* sticky until CLEAR. CLEAR resets FIFOs, engine (eng_rst, two
clocks), status, CYCLES; keeps irq enable. RX full stalls the engine (no lost beats). irq[0] = one-clock pulse on captured m_last
if CTRL[0]. Bus: one ack per transaction; read data masked by sel; unmapped reads 0.

## tiny_ai_core protocol errors (sticky ERROR, otherwise no effect; tiny_ai_core.v header)

START or INPUT while busy; START mode 3; START with wrong input count; input out of range (>1 modes 0/1, >3 mode 2, any in mode 3);
10th input; CLEAR while busy. CLEAR(idle) resets DONE, ERROR, count, buffer, RESULT, CYCLES. DONE stays until CLEAR / next START.

## Caravel flow details (caravel_sim/README.md, run_rtl.sh, run_gl.sh)

- Setup:
  `git clone -b CC2509 --depth=1 https://github.com/chipfoundry/caravel-lite build/caravel/caravel`
  `git clone -b CC2509 --depth=1 https://github.com/chipfoundry/caravel_mgmt_soc_litex build/caravel/mgmt_core_wrapper`
  `brew install riscv64-elf-gcc iverilog`. Template pin: caravel_user_project b510613 (CARAVEL_LITE=1, MPW_TAG=CC2509).
- Compile defs RTL: `-DFUNCTIONAL -DSIM -DUSE_POWER_PINS -DUNIT_DELAY=#1`, `-Ttyp`; GL adds `-DGL`.
- Hex: `objcopy -O verilog`, then `sed -i.bak -e 's/@10/@00/g'` to rebase flash image at 0.
- GL inputs: newest `designs/user_project_wrapper/runs/*/final/pnl/user_project_wrapper.pnl.v` (override `WRAP_PNL`), macro
  `build/macros/tiny_ai_core/pnl/tiny_ai_core.pnl.v` (override `MACRO_PNL`); needs `make wrapper` if no powered netlist; exit 2 otherwise.
- Debug: add `-DTRACE_WB` to SIMDEF to print Wishbone acks. Pass/fail is read from mprj_io[31:16] by tiny_ai_wb_tb.v.
- Firmware runs from a SPI flash model: slow (~4.1 ms simulated). Work dir ~182 MB (docs) to 648 MB (observed).
- Expected cases: vision_all_lit 1 1 1 1 -> RESULT 0x0401 CYCLES 6; text_sentiment 1 1 3 0 -> 0x0101, 6; vision_block all ones ->
  0x0401, 15; diagonal -> 0x0200, 15.
- Not covered by the hybrid run: Caravel/mgmt core as GL, SDF back-annotation (needs cvc64), cocotb/Docker route.

## Adapter tb phases (tests/adapter/adapter_tb.v header)

0 register checks; 1 phase 0 with irq disabled; 2 burst until TX full (ERR_OVF on one more write); 3 interleaved push/pop with irq
pulse count == m_last beats while enabled; 4 ERR_UF, CLEAR, replay. `NLIM_AUDIO_ONSET=<n>` caps audio_onset beats (default 68,829).
Engines in run.sh: vision_all_lit, vision_block, text_sentiment, image_text_match (FRAME); audio_pitch (PITCH); audio_onset (ONSET);
prec_{bin,tern,int4,int8,fp8,fp16,bf16} (FRAME).

## Open items

Full-chip GL+SDF with firmware (needs an x86 Linux host or a shorter flash boot); precheck confirmation with ChipFoundry's own image (human). Commands and lessons: SKILL.md section 8, docs/CARAVEL_SIM.md, docs/PRECHECK.md.
