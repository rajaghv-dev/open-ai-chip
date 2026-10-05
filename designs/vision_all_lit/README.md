# vision_all_lit

**Property:** Dense layer: every input has its own weight.

**Task and model:** 2 x 2 one-bit image; output 1 when all four pixels are lit. One binary neuron: count the pixels that equal their 1-bit weight (XNOR + count), fire when the count reaches the threshold. Fitted: weights 1,1,1,1, threshold 4.

**Hardware:** 4 pixel beats in, consumed as they arrive (no pixel storage). Score = match count 0..4. Result 1 cycle after the last beat.

**Ports (24 pins):** `clk`, `rst` (synchronous, active high), input stream `s_valid`, `s_data[7:0]`, `s_last`, `s_ready`,
output stream `m_valid`, `m_data[7:0]`, `m_last`, `m_ready`. Output: beat 0 = `{6'b0, error, class}`, beat 1 = score.
`error`: an item out of range or a frame that is not the exact length. Full contract: `model/tiny_ai/spec.json`.

## Run

```bash
make flow-all DESIGN=vision_all_lit    # simulate, gds, check, gate-level (synthesised and routed), collect
```

## Files

| Path | Contents |
|---|---|
| `config.json` | LibreLane configuration (80 x 80 um, met4 max, 25 ns clock) |
| `rtl/vision_all_lit.v` | the engine |
| `rtl/vision_all_lit_rom.v` | parameters, **generated** by `model/tiny_ai/gen_rom.py` (do not edit; `make generate`) |
| `tb/vision_all_lit_tb.v` | testbench; body shared in `shared/tb/stream_tb.vh` |
| `tb/vectors.hex` | **generated** vectors: all 16 images, 3 short frames, 2 long frames, 8 out-of-range items = 29 cases, with random input gaps and output stalls |
| `output/` | committed `metrics.json`, `resources.json`, `flow.log`, LEF from the last `make collect` |

The same testbench runs on the RTL, the synthesised netlist and the routed netlist.
