# audio_pitch

**Property:** Data never stops arriving; the answer depends on recent history (streaming, no frames).

**Task and model:** a stream of one-bit audio samples (the sign of a waveform). After every sample, output whether the last W = 8 samples look like a high tone. A `[+1, -1]` kernel slid over time marks each sign change (XOR of consecutive samples), the changes in the window are counted, and a threshold neuron compares the count with a **trained** threshold. Fitted (`model/audio_pitch/train.py`, generated square waves of long and short period at every phase plus single-bit noise): threshold 4, **training accuracy 85.5 %** (171 of 200). W = 8 is not enough to separate everything: a high tone of half-period 3 shows only 2 or 3 changes in 8 samples, which a noisy low tone can match. That is a finding, not a bug; a longer window helps (`model/examples/audio.py`).

**Hardware:** no frame buffer. The state is the last sample, a 7-bit delay line of the last W-1 change bits and a 3-bit running count updated by "change entering minus change leaving", plus a warm-up counter: 21 flip-flops, independent of the stream length. One result beat per input beat after the first 7 samples; the result is registered, so m_valid rises on the edge that accepts the sample. `s_last` ends a recording and clears the history.

**Ports (24 pins):** `clk`, `rst` (synchronous, active high), input stream `s_valid`, `s_data[7:0]`, `s_last`, `s_ready`, output stream `m_valid`, `m_data[7:0]`, `m_last`, `m_ready`. Input: `s_data[0]` = sign bit, `s_data[7:1]` must be 0. Output: `m_data = {error, class, count[5:0]}`, `class = count >= threshold` (1 = high tone), `count` = sign changes among the last 8 samples (0..7); `m_last` = `s_last` of that input beat. `error`: a sample with `s_data[7:1] != 0` (its bit 0 is still used); the flag rides on the next result beat. Full contract: `model/audio_pitch/spec.json`.

## Run

```bash
make simulate DESIGN=audio_pitch     # RTL simulation, self-checking testbench
make flow-all DESIGN=audio_pitch     # simulate, gds, check, gate-level (synthesised and routed), collect
python3 model/audio_pitch/train.py   # re-fit the threshold -> weights.json
python3 model/audio_pitch/gen_rom.py # regenerate the ROM and the vectors (writes only when content changes)
```

## Files

| Path | Contents |
|---|---|
| `config.json` | LibreLane configuration (80 x 80 um, met4 max, 25 ns clock) |
| `rtl/audio_pitch.v` | the engine |
| `rtl/audio_pitch_rom.v` | the learned threshold, **generated** by `model/audio_pitch/gen_rom.py` (do not edit) |
| `tb/audio_pitch_tb.v` | self-checking testbench, ports only (also runs on gate-level netlists), `+VEC=<vectors.hex>` |
| `tb/vectors.hex` | **generated**: all 256 window contents, a de Bruijn stream (every window in a continuing stream), generated tones and tone changes with noise, short recordings, error samples, reset-test recording: 370 recordings, 4564 input beats, 2020 expected result beats; sent three times (random gaps/stalls, full rate, random again) plus reset tests |

Reference: `model/audio_pitch/golden.py` (bit-exact), checked against an independent recount of the window while generating the vectors.

## Status

Hardened: `make flow-all` passed all 5 stages (simulate, gds, check, gate-level, collect); 234 std cells, 21 flip-flops, 0 DRC/LVS/antenna violations, worst setup slack 13.354 ns at a 25 ns clock (`output/metrics.json`).
