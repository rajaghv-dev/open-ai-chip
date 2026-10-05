# prec_tern

**What:** the ternary engine of the precision study (`model/precision_hw/spec.md`): one neuron classifying a 3 x 3 image of 4-bit pixels (vertical bar = 1, horizontal bar = 0), identical in structure to the other six formats; only the number format changes.

**Format:** weights {-1, 0, +1} as 2-bit two's complement, 9-bit bias (-1).

**Arithmetic:** Per step: add the unsigned 4-bit pixel, subtract it, or skip it (no multiplier); `class = (acc >= 0)`. The accumulator starts at the bias. Accumulator: 10-bit signed. One MAC unit reused serially over the 9 inputs (one pixel per clock, overlapping the arrival of the next beat). Test accuracy 94.15 %, 98.8 % same decision as fp32.

**Ports (24 pins):** `clk`, `rst` (synchronous, active high), input stream `s_valid`, `s_data[7:0]` (pixel in `[3:0]`; `[7:4]` must be 0), `s_last`, `s_ready`; output stream `m_valid`, `m_data[7:0]`, `m_last`, `m_ready`. Output beat 0 = `{6'b0, error, class}`, beat 1 = `acc[7:0] ^ {6'b0, acc[9:8]}`. Latency 2 cycles after the last input beat. `error`: an item with `s_data[7:4] != 0` (unused) or a frame that is not exactly 9 beats.

## Run

```bash
make simulate DESIGN=prec_tern    # iverilog; PASS prec_tern_tb: 931 cases
```

## Files

| Path | Contents |
|---|---|
| `config.json` | LibreLane configuration (80 x 80 um, met4 max, 25 ns clock) |
| `rtl/prec_tern.v` | the engine |
| `rtl/prec_tern_rom.v` | weights/constants, **generated** by `model/precision_hw/gen.py` (do not edit) |
| `tb/prec_tern_tb.v` | testbench; body shared in `shared/tb/stream_tb.vh` |
| `tb/vectors.hex` | **generated** vectors, 931 cases |

## Status

Hardened: `make flow-all` passed all 5 stages (simulate, gds, check, gate-level, collect). 293 std cells, 80 x 80 um die, 25 ns clock, worst setup slack 16.35 ns, hold 0.112 ns, DRC/LVS/antenna clean, 931-case RTL and gate-level simulation PASS (`output/metrics.json`, `NOTES.md`).
