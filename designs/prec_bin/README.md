# prec_bin

**What:** the binary engine of the precision study (`model/precision_hw/spec.md`): one neuron classifying a 3 x 3 image of 4-bit pixels (vertical bar = 1, horizontal bar = 0), identical in structure to the other six formats; only the number format changes.

**Format:** 1-bit weights (sign of the fp32 weight), input bit = pixel[3], T = 3.

**Arithmetic:** XNOR of input bit and weight bit, popcount; `class = (m >= T)`. No multiplier, no adder beyond a 4-bit incrementer. Accumulator: 4-bit unsigned match count (0..9). One MAC unit reused serially over the 9 inputs (one pixel per clock, overlapping the arrival of the next beat). Cheapest format (13 parameter bits) but the only one that loses accuracy (88.95 % test).

**Ports (24 pins):** `clk`, `rst` (synchronous, active high), input stream `s_valid`, `s_data[7:0]` (pixel in `[3:0]`; `[7:4]` must be 0), `s_last`, `s_ready`; output stream `m_valid`, `m_data[7:0]`, `m_last`, `m_ready`. Output beat 0 = `{6'b0, error, class}`, beat 1 = `{4'b0, acc}` (the match count). Latency 2 cycles after the last input beat. `error`: an item with `s_data[7:4] != 0` (unused) or a frame that is not exactly 9 beats.

## Run

```bash
make simulate DESIGN=prec_bin    # iverilog; PASS prec_bin_tb: 931 cases
```

## Files

| Path | Contents |
|---|---|
| `config.json` | LibreLane configuration (80 x 80 um, met4 max, 25 ns clock) |
| `rtl/prec_bin.v` | the engine |
| `rtl/prec_bin_rom.v` | weights/constants, **generated** by `model/precision_hw/gen.py` (do not edit) |
| `tb/prec_bin_tb.v` | testbench; body shared in `shared/tb/stream_tb.vh` |
| `tb/vectors.hex` | **generated** vectors, 931 cases |

## Status

RTL verified; not hardened yet.
