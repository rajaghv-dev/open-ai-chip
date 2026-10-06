# soc_image_text_match

The first "one build per experiment" macro of `docs/SOC_PLAN.md` (option (a) delivered as option (c)): the generic
Wishbone-to-stream adapter `shared/rtl/wb_stream_adapter.v` plus ONE unchanged stream engine, `image_text_match`
(`designs/image_text_match`, 10 input beats in, 2 result beats out). Its port list is exactly `tiny_ai_core`'s
(`wb_clk_i`, `wb_rst_i`, `wbs_*`, `irq[2:0]`, optional `vccd1`/`vssd1`), so it drops into `user_project_wrapper` as `mprj`
without touching the wrapper except for the macro name.

**Status: hardened.** `make simulate DESIGN=soc_image_text_match` passes all 2,079 cases of `tb/vectors.hex` through the Wishbone bus, and `make flow-all` passed all 5 stages (185 s, my sum of `build/flow/soc_image_text_match/stages.txt`; physical flow 163 s, 0.98 GB peak, `output/resources.json`): 3201 std cells, 393 flip-flops, DRC/LVS/XOR/antenna 0, no setup or hold violations; 421 max-slew violations are reported, not failing. Details and evidence: [NOTES.md](NOTES.md), `output/`.

## Register map (base `0x3000_0000`, 256-byte window)

| Offset | Name | Access | Definition |
|---|---|---|---|
| 0x00 | ID | R | `0x5354_5201` |
| 0x04 | CTRL | RW | [0] irq enable (byte lane 0, resets to 0); W: bit 8 CLEAR (byte lane 1, self-clearing, reads 0) |
| 0x08 | STATUS | R | [0] TX_FULL [1] TX_EMPTY [2] RX_EMPTY [3] BUSY [4] DONE [5] ERR_OVF [6] ERR_UF, [15:8] TX level, [23:16] RX level |
| 0x0C | TXDATA | W | [7:0] pushed as `s_data`, `s_last` = 0 (byte lane 0) |
| 0x10 | TXLAST | W | [7:0] pushed as `s_data`, `s_last` = 1 (byte lane 0) |
| 0x14 | RXDATA | R | [7:0] `m_data`, [8] `m_last`, [9] valid; the read pops the RX FIFO; empty: returns 0 and sets ERR_UF |
| 0x18 | RXSTATUS | R | [0] EMPTY, [1] HEAD_LAST (next beat to pop has `m_last`), [2] DONE, [15:8] RX level (no side effect) |
| 0x1C | CYCLES | R | clocks from the edge that accepts a run's first TX beat to the edge that captures its `m_last` beat (16 bit, saturating) |
| 0x20 | CAPS | R | [7:0] RTL version 1, [15:8] TX depth (16), [23:16] RX depth (16) |

Bus rules as `tiny_ai_core`: one `wbs_ack_o` pulse per transaction; reads masked by `wbs_sel_i`; unmapped offsets read 0
and ignore writes; out-of-window reads return 0 and ack. BUSY = a run is in progress; DONE = its `m_last` result beat
has been captured (both clear on CLEAR and when the next run's first beat is written). ERR_OVF / ERR_UF are sticky until
CLEAR. CLEAR empties both FIFOs, resets the engine and all status; the irq enable is kept. A full RX FIFO stalls the
engine (`m_ready` low), so results are never lost. `irq[0]` pulses for one clock when a result beat with `m_last` is
captured (if CTRL[0] = 1); `irq[2:1]` = 0.

## How firmware drives it

```c
#define R(o) (*(volatile uint32_t *)(0x30000000 + (o)))
R(0x04) = 0x100;                        // CLEAR
if (R(0x00) != 0x53545201) fail();      // ID
R(0x04) = 0x001;                        // optional: enable irq[0]
for (i = 0; i < 9; i++) R(0x0C) = in[i];   // TXDATA
R(0x10) = in[9];                        // TXLAST ends the frame
while (!(R(0x18) & 4)) ;                // RXSTATUS.DONE (or wait for irq[0])
uint32_t b0 = R(0x14), b1 = R(0x14);    // two beats: {valid,last,data}; b1 has last = 1
uint32_t cycles = R(0x1C);
```

`b0 & 0xFF` = `{error, class}` beat, `b1 & 0xFF` = score (see `designs/image_text_match/README.md`). Do not write
more than 16 beats without letting the engine run (a write while TX is full is dropped and sets ERR_OVF).

## Files

- `rtl/soc_image_text_match.v`: wiring only. The adapter and the engine come from `config.json` `VERILOG_FILES`
  (`dir::../../shared/rtl/wb_stream_adapter.v`, `dir::../image_text_match/rtl/*.v`).
- `tb/soc_image_text_match_tb.v`: ports-only Wishbone testbench (runs on RTL now, on gate level later);
  `tb/vectors.hex` is a copy of `designs/image_text_match/tb/vectors.hex` (the Makefile looks for it in this directory).
- `base_soc.sdc`, `pin_order.cfg`, `config.json`: copied from `tiny_ai_core` and adapted (names only).
- Adapter regression (14 engines: the 13 stream engines plus `kv_attn_n8`): `tests/adapter/run.sh`.
- Sibling with the same adapter and a KV-cache attention engine instead: [../soc_kv_attn_n8/README.md](../soc_kv_attn_n8/README.md) (4514 cells, 570 flip-flops, 300 x 300 um; `soc_kv_attn_n8/output/metrics.json`). Its Caravel wrapper is `user_project_wrapper_soc_kv`.

## Measured size (was estimated before hardening)

393 flip-flops in silicon (`output/metrics.json`, `design__instance__count__class:sequential_cell`; RTL elaborates to the
same 393). The pre-hardening estimate here was about 420; synthesis removed constant and unused bits (for example bit 7
of the RX FIFO: `m_data[7]` is always 0 for this engine). About 90% of the flip-flops are the adapter (FIFO storage
alone is 69%) and 39 are the image_text_match engine: see NOTES.md, "Architecture".
