# add-tiny-engine reference

Every statement names the file it comes from (paths from the repo root).

## Directory anatomy

```
model/<eng>/        spec.json  train.py  golden.py  gen_rom.py  weights.json   (tiny_ai also has common.py)
designs/<eng>/      config.json  README.md  NOTES.md  rtl/<eng>.v  rtl/<eng>_rom.v (generated)
                    tb/<eng>_tb.v  tb/vectors.hex (generated)  output/ runs/ (flow evidence, not hand-written)
shared/tb/stream_tb.vh       shared self-checking testbench body (frame engines)
```
model/tiny_ai/ holds three engines in one dir (DESIGNS tuple in common.py); the other model dirs hold one engine each (model/audio_pitch, model/audio_onset, model/image_text_match); model/precision_hw holds the seven prec_* engines; model/kv_attention holds the five kv_attn_* variants (`spec.md`, `golden.py`, `gen.py`, no `train.py`; ROM and vectors for all five come from one `gen.py`). Choose a new dir unless the engine really shares spec and fitter.

## The pipeline, per file

| Step | File | What it does (source) |
|---|---|---|
| spec | model/tiny_ai/spec.json | `stream` (input, output, error, latency wording) and `designs.<d>` (mode, inputs, input_max, label, model, widths, latency). `train.py`, `golden.py`, `gen_rom.py` and the RTL all derive from it. |
| labelled dataset | model/tiny_ai/common.py `truth_table(design)` | every valid input paired with the label; labels are ground truth, never copied into parameters. |
| fit | model/tiny_ai/train.py | exhaustive search over the whole parameter space (96 / 96 / 4096 settings); first exact fit in a fixed order (vision) or smallest-magnitude exact fit (text); exits non-zero if none. Writes weights.json. |
| golden | model/tiny_ai/golden.py | `run(design, beats, w) -> (beat0, beat1, latency)`; beat0 = `error<<1 \| class`; `error = len(beats) != n or any(b > vmax)`; only in-range beats of the first n reach the datapath. `--check` prints `golden: <d> <n> cases, <m> mismatches`. |
| generate | model/tiny_ai/gen_rom.py | `rom_verilog`, `cases`, `vectors`, `write_if_changed`; hash via `common.sha256_of(weights.json, golden.py, gen_rom.py, common.py, spec.json)` (paths hashed relative to REPO). |
| RTL | designs/vision_block/rtl/vision_block.v | big header (THE NETWORK, THE WEIGHTS ARE LEARNED, NEURAL NETWORK <-> HARDWARE, BLOCKS), `timescale`, `default_nettype none`, section comments `---- IO`, `---- MEMORY`, `---- COMPUTE`, `---- CONTROL`. FSM LOAD/COMP/OUT0/OUT1, saturating `count`, sticky `error` cleared after output. |
| testbench | designs/vision_block/tb/vision_block_tb.v | `define DUT vision_block`, `include "stream_tb.vh"`. |

## Vector record layout (frame engines)

From model/tiny_ai/gen_rom.py: 8-bit words, 16 per record. Record 0 = `A5, count_hi, count_lo, 0...`. Case record: `[0]` number of beats (1..12), `[1..12]` s_data (s_last on the last), `[13]` expected beat 0, `[14]` expected beat 1, `[15]` expected latency. `MAX_BEATS = 12`. Cases for vision_block (540): all 512 images, 8 short frames, 2 long frames, 18 out-of-range items (designs/vision_block/README.md).

Case limit: stream_tb.vh declares `MAXREC = 1024` (`reg [7:0] vec [0:16*MAXREC-1]`), so up to 1023 cases. model/precision_hw/gen.py asserts `MAX_CASES = 1023`. designs/image_text_match/tb/stream_tb_big.vh is the same body with `MAXREC = 4096` for its 2,079 cases, included as `include "designs/image_text_match/tb/stream_tb_big.vh"` (relative to the repo root, as make runs from there). Its own header says to delete it if the shared body ever grows.

Audio engines: model/audio_pitch/gen_rom.py uses 32-bit words: word 0 `{A5, 24-bit beat count}`, word 1 recordings, words 2-3 reset-test span, then one word per input beat (`[7:0]` s_data, `[8]` s_last, `[9]` result expected, `[10]` its m_last, `[18:11]` its m_data). The testbench designs/audio_pitch/tb/audio_pitch_tb.v sends the file three times (random gaps, full rate with m_ready high so s_ready must never drop, other gaps) and drives garbage on s_data while s_valid is low.

## What stream_tb.vh checks (shared/tb/stream_tb.vh header)

Random input gaps (`$random(seed) & 3`), random m_ready stalls, beat 0, beat 1, m_last on beat 1 only, latency; output stable while stalled; `s_ready` low from the last input beat until the result is taken; reset mid-frame and with a result waiting. First failure `$fatal(1, "FAIL ...")`; success prints `PASS <dut>_tb: <n> cases, <k> checks`. Runs unchanged on RTL and on synthesised/routed gate-level netlists (Makefile `gl` target passes `-I shared/tb`).

## config.json fields (designs/vision_block/config.json, designs/image_text_match/config.json)

Keys: PDK sky130A, STD_CELL_LIBRARY sky130_fd_sc_hd, DESIGN_NAME, VERILOG_FILES (`dir::rtl/...`, ROM first), CLOCK_PERIOD 25, CLOCK_PORT clk, FP_SIZING absolute, DIE_AREA, VDD_NETS vccd1, GND_NETS vssd1, RT_MAX_LAYER met4, PDN_MULTILAYER false, MAGIC_DRC_USE_GDS true, RUN_HEURISTIC_DIODE_INSERTION true, RUN_ANTENNA_REPAIR true, RUN_POST_GRT_DESIGN_REPAIR true, ERROR_ON_SYNTH_CHECKS true, MAX_FANOUT_CONSTRAINT 8, PL_RESIZER_MAX_SLEW_MARGIN, GRT_DESIGN_REPAIR_MAX_SLEW_PCT. Keys starting `//` are comments (JSON has none) and tests/run_tests.sh's config check accepts them.

Die and slew history: vision_block uses an 80 x 80 die and slew 40; image_text_match copied that, reached 82 % utilisation and its repair step ran out of memory, then 120 x 120 (about 36 %) with slew 20 (its `//DIE_AREA` and `//SLEW` keys). Prec dies: 80, 80, 80, 120, 170, 220, 220 um (bin..bf16) from `config.json`s; they carry `//DIE_AREA` notes ("~40% utilisation").

## Wiring map (exact current contents)

- Makefile: `TINY := vision_all_lit vision_block text_sentiment` (only the three originals; new engines do not go here). `ALL_DESIGNS` (25) = user_proj_example vision_all_lit vision_block text_sentiment tiny_ai_core user_project_wrapper audio_pitch audio_onset image_text_match prec_bin ... prec_bf16 soc_image_text_match user_project_wrapper_soc_itm kv_attn_n4 kv_attn_n8 kv_attn_n16 kv_attn_n8_int4 kv_attn_n8_ring soc_kv_attn_n8 user_project_wrapper_soc_kv. `MODELS := tiny_ai audio_pitch audio_onset image_text_match precision_hw kv_attention`. Targets `generate` (train + gen_rom per dir; `cd model/precision_hw && python3 gen.py`; `cd model/kv_attention && python3 gen.py`) and `model-check` (golden --check for tiny_ai, image_text_match, precision_hw, kv_attention; audio_* have none, their testbenches compare against golden.py). `DESIGNS` is auto-discovered from `designs/*/config.json`.
- tests/run_tests.sh: `TINY`, `CORE=tiny_ai_core`, `NEWENG="audio_pitch audio_onset image_text_match prec_bin ... prec_bf16"`, `STREAM="$TINY $NEWENG"`, `SOCM="soc_image_text_match soc_kv_attn_n8"`, `KV="kv_attn_n4 kv_attn_n8 kv_attn_n16 kv_attn_n8_int4 kv_attn_n8_ring"` (own testbench body `shared/tb/kv_attn_tb.vh`), `ALL` (plus every `designs/user_project_wrapper*/`). Sections: structure (required files per design, model files list), upstream, config, rtl (iverilog -Wall), model (golden --check; loop `for m in image_text_match precision_hw kv_attention`; then check_generated.sh), sim (PASS line per design), negative, notes (NOTES.md required headings once `output/metrics.json` exists), adapter, soc, tables. Negative section: tiny engines get a vector corruption and an RTL mutation (`case $d in` table of from/to strings); `for d in audio_pitch audio_onset image_text_match prec_int8` corrupts one expected value (record 1, word 13 for frame layouts). Mutations are applied in a temp dir and asserted to have changed the file.
- scripts/check_generated.sh: `FIT="tiny_ai audio_pitch audio_onset image_text_match"` (train.py + gen_rom.py; weights.json deleted in the scratch copy first), `precision_hw` and `kv_attention` copied separately with gen.py (the five kv_attn_* ROMs and vectors are compared; 41 files in total today), a design list for ROM + vectors, plus tiny_ai_core vectors.
- scripts/docs/tables.py: `ORDER` list; README.md holds `<!-- results:begin NAME -->` blocks that `make table` rewrites and `--check` verifies.
- tests/adapter/run.sh: `ENGINES="vision_all_lit:FRAME ... audio_pitch:PITCH audio_onset:ONSET prec_bin:FRAME ... prec_bf16:FRAME kv_attn_n8:KV"` (14 engines); compiles `shared/rtl/wb_stream_adapter.v designs/$d/rtl/*.v tests/adapter/adapter_tb.v` with `-DENG=$d -D$FMT`; runs the engine's own vectors through Wishbone only. Burst, interleaved, overflow, underflow and CLEAR phases are in adapter_tb.v. Beat limits come from the adapter FIFOs (TX_DEPTH 16, RX_DEPTH 16).
- README.md: engine list, engine-count wording (also Makefile `help`: "14 stream engines (incl. kv_attn_n8)"), per-design results rows (regenerated by tables.py).

## Verification gates and expected output

| Gate | Command | Pass looks like |
|---|---|---|
| model | `python3 model/<eng>/golden.py --check` | `0 mismatches` on every line |
| sim | `make simulate DESIGN=<eng>` | `PASS <eng>_tb: <n> cases, <k> checks`, exit 0 (e.g. `PASS vision_block_tb: 540 cases, 2713 checks`, designs/vision_block/NOTES.md) |
| negative | run_tests.sh negative section | `testbench rejects a corrupted expected value`, `testbench rejects broken RTL` |
| adapter | `make adapter-test` | `PASS <eng>` and `adapter tests: N engines PASS` |
| flow | `make flow-all DESIGN=<eng>` | stages simulate, gds, check, gl_synth, gl_final, collect all PASS; evidence lands in designs/<eng>/output/ |
| repo | `make test` | `test: ALL PASSED` |
| generated | `make check-generated` | `check-generated: PASS (regeneration reproduces N files)` |

Signoff catches lost logic: scripts/flow/check_signoff.py requires surviving sequential cells >= RTL registers - allowance; allowances live in scripts/flow/signoff_allowances.json and default to 0.

## NOTES.md

Point to the write-design-notes skill. tests/run_tests.sh requires these headings (## or ###, prefix match) for any design with output/metrics.json: Architecture, Data flow, Verification, Layout, Synthesis, Floorplan, Placement, Clock tree (or CTS), Routing, Timing, DRC, LVS, Power (or IR), Antenna, Run time, Reproduce, Intuitions and insights. Every number must cite a file under output/, config.json, rtl/, tb/ or model/.
