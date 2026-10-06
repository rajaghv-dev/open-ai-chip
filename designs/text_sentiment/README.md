# text_sentiment

**Property:** Embedding lookup: the model is mostly a small table.

**Task and model:** Four tokens from {PAD, GOOD, FINE, BAD}; output 1 when GOOD outnumbers BAD. Signed 3-bit embedding per token, four-term sum, positive when the sum is > 0 (0 is negative). Fitted (smallest exact): PAD 0, GOOD +1, FINE 0, BAD -1.

**Hardware:** 4 token beats, one ROM lookup and one add each into a 5-bit signed accumulator (no overflow by range). Score = signed sum. Result 1 cycle after the last beat.

**Ports (24 pins):** `clk`, `rst` (synchronous, active high), input stream `s_valid`, `s_data[7:0]`, `s_last`, `s_ready`,
output stream `m_valid`, `m_data[7:0]`, `m_last`, `m_ready`. Output: beat 0 = `{6'b0, error, class}`, beat 1 = score.
`error`: an item out of range or a frame that is not the exact length. Full contract: `model/tiny_ai/spec.json`.

## Run

```bash
make flow-all DESIGN=text_sentiment    # simulate, gds, check, gate-level (synthesised and routed), collect
```

## Files

| Path | Contents |
|---|---|
| `config.json` | LibreLane configuration (80 x 80 um, met4 max, 25 ns clock) |
| `rtl/text_sentiment.v` | the engine |
| `rtl/text_sentiment_rom.v` | parameters, **generated** by `model/tiny_ai/gen_rom.py` (do not edit; `make generate`) |
| `tb/text_sentiment_tb.v` | testbench; body shared in `shared/tb/stream_tb.vh` |
| `tb/vectors.hex` | **generated** vectors: all 256 sentences, 3 short frames, 2 long frames, 8 out-of-range tokens = 269 cases, with random input gaps and output stalls |
| `output/` | committed `metrics.json`, `resources.json`, `flow.log`, LEF from the last `make collect` |

The same testbench runs on the RTL, the synthesised netlist and the routed netlist.

## Status

Hardened: `make flow-all DESIGN=text_sentiment` passed all 5 stages (simulate, gds, check, gate-level of the synthesised and the routed netlist, collect). 200 std cells, 12 flip-flops, 80 x 80 um die, 25 ns clock, worst setup slack 14.75 ns, worst hold 0.108 ns, DRC/LVS/antenna clean, 0 max-slew violations, flow wall time 45 s at 0.554 GB peak (`output/metrics.json`, `output/resources.json`). The design page with every step and number is [NOTES.md](NOTES.md).
