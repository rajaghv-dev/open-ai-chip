# vision_block

**Property:** Convolution: one small kernel reused at every position.

**Task and model:** 3 x 3 one-bit image; output 1 when some 2 x 2 window is fully lit. One 2 x 2 kernel neuron evaluated serially at the four positions (one per cycle), OR max-pooling. Fitted: kernel 1,1,1,1, threshold 4.

**Hardware:** 9 pixel beats into a 9-bit frame register (the SPEC.md MVP choice; a line buffer is later work). Score = largest window match count 0..4. Result 5 cycles after the last beat.

**Ports (24 pins):** `clk`, `rst` (synchronous, active high), input stream `s_valid`, `s_data[7:0]`, `s_last`, `s_ready`,
output stream `m_valid`, `m_data[7:0]`, `m_last`, `m_ready`. Output: beat 0 = `{6'b0, error, class}`, beat 1 = score.
`error`: an item out of range or a frame that is not the exact length. Full contract: `model/tiny_ai/spec.json`.

## Run

```bash
make flow-all DESIGN=vision_block    # simulate, gds, check, gate-level (synthesised and routed), collect
```

## Files

| Path | Contents |
|---|---|
| `config.json` | LibreLane configuration (80 x 80 um, met4 max, 25 ns clock) |
| `rtl/vision_block.v` | the engine |
| `rtl/vision_block_rom.v` | parameters, **generated** by `model/tiny_ai/gen_rom.py` (do not edit; `make generate`) |
| `tb/vision_block_tb.v` | testbench; body shared in `shared/tb/stream_tb.vh` |
| `tb/vectors.hex` | **generated** vectors: all 512 images, 8 short frames, 2 long frames, 18 out-of-range items = 540 cases, with random input gaps and output stalls |
| `output/` | committed `metrics.json`, `resources.json`, `flow.log`, LEF from the last `make collect` |

The same testbench runs on the RTL, the synthesised netlist and the routed netlist.
