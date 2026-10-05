# image_text_match

Status: **hardened** (`make flow-all DESIGN=image_text_match` passed all 5 stages; sky130A, 120 x 120 um die, 25 ns clock, 39 flip-flops, 551 standard cells, utilisation 0.363; setup WNS +13.42 ns, hold +0.107 ns; DRC 0, LVS match, antenna 0; RTL and both gate-level simulations PASS 2,079 cases; known: 61 max-slew violations in the ss corners and 6 max-fanout violations in `metrics.json`; details in [NOTES.md](NOTES.md)).

**Property:** Multimodal matching: an image encoder and a text encoder put a picture and a word into one shared vector space, and a similarity decides whether they agree (CLIP at toy scale).

**Task and model:** input = 3 x 3 one-bit image (9 beats, raster order) + one caption token (beat 10: 0 EMPTY, 1 VERT, 2 HORIZ, 3 DIAG). Output 1 when the caption describes the image:

| Caption | True when |
|---|---|
| EMPTY | no pixel is lit |
| VERT | some column is fully lit |
| HORIZ | some row is fully lit |
| DIAG | a diagonal (0,4,8 or 2,4,6) is fully lit |

All 512 x 4 = 2,048 pairs are enumerated (about 36 percent are true).

- **Image encoder:** two 3-tap binary neurons A and B (kernel 3 bits, threshold), each reused serially over the 8 lines (3 columns, 3 rows, 2 diagonals), one line per cycle. Fires are sum-pooled per line group: the image embedding is 6 small counts (A cols, A rows, A diags, B cols, B rows, B diags), each 0..3.
- **Text encoder:** a ROM table, caption -> 6 signed 3-bit integers in the same space.
- **Similarity:** dot product (7-bit signed); class = similarity >= threshold.

**Learned** (`model/image_text_match/train.py`, exhaustive deterministic search, **0 mismatches on all 2,048 pairs**):
kernel A = 000, B = 111 (both threshold 3: "dark line", "lit line"); VERT = 3 x B-cols, HORIZ = 3 x B-rows, DIAG = 3 x B-diags, EMPTY = 1 x A-rows (all three rows dark); similarity threshold 3.
Lessons: (1) the table is a design choice found by search, not a copy of the labels; (2) EMPTY cannot be expressed with only "lit line" features and non-negative counts, so the search picked a second neuron that detects dark lines (sum pooling turns "3 dark rows" into similarity 3); (3) A-cols, A-diags and the B weights not listed are 0: unused capacity of this structure.

**Why multimodal:** two different inputs (pixels, a word) are encoded separately into the same 6-dimensional space; the answer exists only in how well the two vectors agree.

**Hardware:** 9-bit frame register + 2-bit token register, 8 line cycles, 1 similarity cycle. Result **10 cycles** after the last beat (1 accept + 8 + 1). 41 flip-flops by inspection (state 3, count 4, frame 9, token 2, line 3, six 2-bit counts 12, score 7, error 1).

**Ports (24 pins):** `clk`, `rst` (synchronous, active high), input stream `s_valid`, `s_data[7:0]`, `s_last`, `s_ready`,
output stream `m_valid`, `m_data[7:0]`, `m_last`, `m_ready`. Output: beat 0 = `{6'b0, error, class}`, beat 1 = similarity score (signed 8-bit).
`error`: a pixel above 1, a token above 3, or a frame that is not exactly 10 beats. Full contract: `model/image_text_match/spec.json`.

## Worked example

Image (a lit middle column), caption VERT:

```
. # .        pixels 0..8 = 0 1 0  0 1 0  0 1 0       token = 1 (VERT)
. # .
. # .
```

Image encoder, 8 lines, tap i = pixel i of the line. Neuron B (kernel 111, threshold 3) fires only on a fully lit line: column 1 (pixels 1,4,7 = 1,1,1) fires; the other columns, all rows (each row has one lit pixel, match 1) and both diagonals (0,4,8 = 0,1,0 and 2,4,6 = 0,1,0) do not. Neuron A (kernel 000, threshold 3) fires on a fully dark line: columns 0 and 2 fire (all pixels 0); rows and diagonals contain the lit centre, so no. Pooled embedding (A cols, A rows, A diags, B cols, B rows, B diags) = **(2, 0, 0, 1, 0, 0)**.

Text encoder: VERT -> (0, 0, 0, 3, 0, 0).

Similarity: 2*0 + 0 + 0 + 1*3 + 0 + 0 = **3**; 3 >= threshold 3, so **class = 1** (the caption describes the image). Outputs: beat 0 = 0x01, beat 1 = 0x03.
Same image with caption HORIZ: text (0,0,0,0,3,0), similarity 0 < 3, class 0. With EMPTY: text (0,1,0,0,0,0), similarity 0 (A rows = 0), class 0.

## Run

```bash
python3 model/image_text_match/train.py     # fit, writes weights.json
python3 model/image_text_match/gen_rom.py   # ROM + vectors (writes only when content changes)
python3 model/image_text_match/golden.py --check
make simulate DESIGN=image_text_match
```

## Files

| Path | Contents |
|---|---|
| `config.json` | LibreLane configuration (copy of vision_block's; DESIGN_NAME and VERILOG_FILES changed) |
| `rtl/image_text_match.v` | the engine |
| `rtl/image_text_match_rom.v` | parameters, **generated** by `model/image_text_match/gen_rom.py` (do not edit) |
| `tb/image_text_match_tb.v` | testbench |
| `tb/stream_tb_big.vh` | copy of `shared/tb/stream_tb.vh` with only the case limit raised (1,024 to 4,096), since this design has 2,079 cases |
| `tb/vectors.hex` | **generated**: 2,048 pairs, 9 short frames, 2 long frames, 20 out-of-range items = 2,079 cases, random input gaps and output stalls |
