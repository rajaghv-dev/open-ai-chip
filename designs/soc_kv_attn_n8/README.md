# soc_kv_attn_n8

The KV-cache attention engine as a Caravel SoC macro: the generic Wishbone-to-stream adapter `shared/rtl/wb_stream_adapter.v`
plus ONE unchanged stream engine, `kv_attn_n8` (`designs/kv_attn_n8`: 8-slot KV cache, PREFILL and DECODE, engine
`shared/rtl/kv_attn_core.v`, contract `model/kv_attention/spec.md`). Its port list is exactly `tiny_ai_core`'s
(`wb_clk_i`, `wb_rst_i`, `wbs_*`, `irq[2:0]`, optional `vccd1`/`vssd1`), so it drops into `user_project_wrapper` as `mprj`
without touching the wrapper except for the macro name. Same adapter and register map as `designs/soc_image_text_match`.

**Status: hardened.** `make simulate DESIGN=soc_kv_attn_n8` passes all 1,521 records of `tb/vectors.hex` through the Wishbone bus
(31,647 checks), and `make flow-all` passed all 5 stages: 4514 std cells, 570 flip-flops, 300 x 300 um die, DRC/LVS/XOR/antenna 0,
no setup or hold violations; worst setup +1.44 ns (`max_ss_100C_1v60`), worst hold +0.105 ns; 836 max-slew violations are reported, not
failing (142 of them are the Caravel input ports' own transitions). Details and evidence: [NOTES.md](NOTES.md), `output/`.

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
| 0x20 | CAPS | R | [7:0] RTL version 1, [15:8] TX depth (16), [23:16] RX depth (16): `0x0010_1001` |

Bus rules as `tiny_ai_core`: one `wbs_ack_o` pulse per transaction; reads masked by `wbs_sel_i`; unmapped offsets read 0
and ignore writes; out-of-window reads return 0 and ack. BUSY = a run is in progress; DONE = its `m_last` response beat
has been captured (both clear on CLEAR and when the next run's first beat is written). ERR_OVF / ERR_UF are sticky until
CLEAR. CLEAR empties both FIFOs, resets the engine and all status; the irq enable is kept. A full RX FIFO stalls the
engine (`m_ready` low), so responses are never lost. `irq[0]` pulses for one clock when a response beat with `m_last` is
captured (if CTRL[0] = 1); `irq[2:1]` = 0.

## Command frames (engine, `model/kv_attention/spec.md` section 4)

| Opcode | Frame (bytes through `TXDATA`, the last through `TXLAST`) | Response beats |
|---|---|---|
| `01` RESET_CACHE | `[01]` | 2: `[status, count]` |
| `02` PREFILL | `[02, t0, ..., t(m-1)]`, tokens 0..15 (`t = 4*key + value`) | 2: `[status, count]` |
| `03` DECODE | `[03, t]` | 8: `[status, count, index, score, v0, v1, v2, v3]` (2 beats on an error) |

`status[3:0]`: 0 OK, 1 BAD_OPCODE, 2 BAD_TOKEN, 3 CACHE_FULL, 4 BAD_FRAME; `status[4]` = HIT. The engine accepts one frame at a time.

## How firmware drives it

```c
#define R(o) (*(volatile uint32_t *)(0x30000000 + (o)))
R(0x04) = 0x100;                           // CLEAR
if (R(0x00) != 0x53545201) fail();         // ID
R(0x10) = 0x01;                            // TXLAST: RESET_CACHE, a one-beat frame
// PREFILL A1 B3 A2 C0 (tokens 1, 7, 2, 8)
R(0x0C) = 0x02; R(0x0C) = 1; R(0x0C) = 7; R(0x0C) = 2;   // TXDATA: opcode, then tokens
R(0x10) = 8;                               // TXLAST ends the frame
while (!(R(0x18) & 4)) ;                   // RXSTATUS.DONE (or wait for irq[0])
uint32_t s = R(0x14), n = R(0x14);         // 2 beats: status 0, count 4
// DECODE B0 (token 4): "what was last stored under key B?"
R(0x0C) = 0x03; R(0x10) = 4;
while (!(R(0x18) & 4)) ;
for (i = 0; i < 8; i++) resp[i] = R(0x14) & 0xFF;   // 10 05 01 21 03 ff 01 01: HIT, slot 1, score 33, value 3
uint32_t cycles = R(0x1C);
```

The 8 response bytes of that DECODE are the output of `python3 model/kv_attention/golden.py --trace kv_attn_n8`
(see NOTES.md, "Data flow"). The real, self-checking program is `firmware/kv/main.c` (eight prefill/decode sessions
against `firmware/kv/gen_expected.py`), run on PicoRV32 by `make soc-kv` (`soc_sim/kv/`, the same adapter and engine pair). Do
not write more than 16 beats without letting the engine run (a write while TX is full is dropped and sets ERR_OVF).

Measured by that firmware (`firmware/README.md`, KV section): a one-token PREFILL round trip is 328 CPU clocks and a
seven-token one 634 (90.6 per token); every DECODE is 670 CPU clocks, 502 of them reading the 8 response bytes, while the engine
needs only n + 3 clocks (3 to 10) to score the cache.

## Files

- `rtl/soc_kv_attn_n8.v`: wiring only. The adapter and the engine come from `config.json` `VERILOG_FILES`
  (`dir::../../shared/rtl/wb_stream_adapter.v`, `dir::../../shared/rtl/kv_attn_core.v`, `dir::../kv_attn_n8/rtl/kv_attn_n8_rom.v`, `dir::../kv_attn_n8/rtl/kv_attn_n8.v`).
- `tb/soc_kv_attn_n8_tb.v`: ports-only Wishbone testbench (RTL and gate level); `tb/vectors.hex` is a copy of
  `designs/kv_attn_n8/tb/vectors.hex` (the Makefile looks for it in this directory).
- `base_soc.sdc`, `pin_order.cfg`, `config.json`: copied from `soc_image_text_match` and adapted (names, die size).
- `output/`: committed evidence (`metrics.json`, `resources.json`, `flow.log`, `layout.png`, `soc_kv_attn_n8.lef`, `reports/`).

## Measured size (estimated before hardening)

The die was sized before the first run (`config.json` `//DIE_AREA`): about 42,200 um^2 of standard cells, about 50 % of a 300 um die's core.
Measured (`output/metrics.json`): 42,420.7 um^2 (`design__instance__area__stdcell`), utilisation 0.529. 570 flip-flops
(`design__instance__count__class:sequential_cell`): 370 are the adapter (FIFO storage alone is 288) and 200 the attention engine, of which
only 72 hold the 8-slot KV cache, because key and value components are constants or copies. The worst setup path
starts at `wb_rst_i` (12.5 ns input delay in the Caravel SDC) and reaches all 570 flip-flops; it is the reset net, not the attention
math, that limits timing here (NOTES.md, "Timing").
