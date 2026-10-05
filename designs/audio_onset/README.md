# audio_onset

**Property:** Streaming with multi-bit samples: a sliding window of 4-bit energies, one result per input beat.

**Task and model:** A stream of 4-bit window energies (one per beat, `s_data[3:0]`; `s_data[7:4]` must be 0). After each energy, once four have been seen, the engine says whether the sound just got louder. One neuron over the last four energies: `sum = w0*e[n-3] + w1*e[n-2] + w2*e[n-1] + w3*e[n]`, `class = (sum >= threshold)`. Trained by `model/audio_onset/train.py` (exhaustive search of the 625 integer weight vectors in -2..2 on labelled windows with 5% label noise, label rule "newest two minus oldest two >= 4"). It **rediscovers [-1, -1, +1, +1] with threshold 4**: recent energy minus older energy (train accuracy 95.63% on the noisy labels, 100% on clean test windows and on all 65,536 windows).

**Hardware:** a delay line of three 4-bit registers (12 flops), a 2-bit warm-up counter, a sticky error flag, and one output register (valid, last, `{error, class, sum[5:0]}` = 10 flops): 25 flops in all. The weighted sum is shifts and adds of constant weights (no multiplier); the sum is 6 bits signed (exact range -30..30, checked by `gen_rom.py`). Compared with `audio_pitch` (1-bit samples) the same four-sample window needs 4x the history and a wider adder: the bit-width lesson. Result 1 cycle after the input beat, full throughput.

**Ports (24 pins):** `clk`, `rst` (synchronous, active high), input stream `s_valid`, `s_data[7:0]`, `s_last`, `s_ready`,
output stream `m_valid`, `m_data[7:0]`, `m_last`, `m_ready`. Output beat: `m_data = {error, class, sum[5:0]}`; the first 3 samples of a stream (warm-up) produce no output, then one beat per input beat; `s_last` ends the stream, clears the history and the error flag, and is copied to `m_last`. `error`: `s_data[7:4] != 0` in this or an earlier beat of the stream. A stream shorter than 4 samples produces no output. Full contract: `model/audio_onset/spec.json`.

**Status:** Hardened: `make flow-all` passed all 5 stages (simulate, gds, check, gate-level, collect); 316 std cells, 25 flip-flops, 0 DRC/LVS/antenna violations, worst setup slack 13.206 ns at a 25 ns clock (`output/metrics.json`).

## Run

```bash
python3 model/audio_onset/train.py     # fit the weights -> weights.json
python3 model/audio_onset/gen_rom.py   # rtl/audio_onset_rom.v and tb/vectors.hex
make simulate DESIGN=audio_onset       # self-checking testbench (iverilog)
```

## Files

| Path | Contents |
|---|---|
| `config.json` | LibreLane configuration (80 x 80 um, met4 max, 25 ns clock) |
| `rtl/audio_onset.v` | the engine |
| `rtl/audio_onset_rom.v` | weights and threshold, **generated** by `model/audio_onset/gen_rom.py` (do not edit) |
| `tb/audio_onset_tb.v` | self-checking, ports-only testbench (also runs on gate-level netlists), `+VEC=<vectors.hex>` |
| `tb/vectors.hex` | **generated**: 68,829 beats = one de Bruijn stream containing each of the 65,536 four-sample windows exactly once, short streams, `s_last` everywhere, error samples, generated onset sequences, resets with and without a result waiting. Played three times (full rate; random gaps and stalls; heavy back-pressure). Expected outputs from `model/audio_onset/golden.py` |
