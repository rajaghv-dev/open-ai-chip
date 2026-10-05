# prec_int4

**Property:** Number-format study: one neuron with 4-bit signed integer weights (post-training quantised, symmetric per tensor).

**Task and model:** 3 x 3 image of 4-bit pixels; class 1 = vertical bar, 0 = horizontal bar. `sum = bias + sum(w[i]*x[i])`, `class = (sum >= 0)`.
Contract: `model/precision_hw/spec.md`; bit-exact reference `model/precision_hw/golden.py`.

**Hardware:** ONE signed 4 x 5 bit multiplier (weight x zero-extended pixel) and one 12-bit accumulator, used serially over the 9 inputs
(bias pre-loaded, then raster order). Weights from the generated `rtl/prec_int4_rom.v`; synthesis folds these constants into logic.
Flip-flops: 28 (state 2, count 4, error 1, x_vld 1, x_pix 4, x_idx 4, acc 12). Latency 2 cycles after the last beat.
Output: beat 0 = `{6'b0, error, class}`, beat 1 = XOR-fold of the raw 12-bit accumulator bytes.

**Ports (24 pins):** `clk`, `rst` (synchronous, active high), `s_valid`, `s_data[7:0]`, `s_last`, `s_ready`, `m_valid`, `m_data[7:0]`, `m_last`, `m_ready`.

**Status:** Hardened: `make flow-all` passed all 5 stages (simulate, gds, check, gate-level, collect). 377 std cells, 80 x 80 um die, 25 ns clock, worst setup slack 13.46 ns, hold 0.115 ns, DRC/LVS/antenna clean, 931-case RTL and gate-level simulation PASS (`output/metrics.json`, `NOTES.md`).

## Run

```bash
make simulate DESIGN=prec_int4    # 931 cases, iverilog
```

## Files

| Path | Contents |
|---|---|
| `config.json` | LibreLane configuration (80 x 80 um, 25 ns clock) |
| `rtl/prec_int4.v` | the engine |
| `rtl/prec_int4_rom.v` | **generated** by `model/precision_hw/gen.py` (do not edit) |
| `tb/prec_int4_tb.v` | testbench; body shared in `shared/tb/stream_tb.vh` |
| `tb/vectors.hex` | **generated** vectors (931 cases) |
