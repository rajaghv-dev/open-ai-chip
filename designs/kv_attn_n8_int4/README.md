# kv_attn_n8_int4

Status: **hardened clean** (`make flow-all DESIGN=kv_attn_n8_int4`: all 5 stages PASS; 2394 std cells, 222 flip-flops, die 220 x 220 um, 25 ns clock; details in NOTES.md)

**What it shows:** an int4 KV cache: half the nominal cache bits (256 against 512), but not fewer flip-flops (222 in total against 200 for `kv_attn_n8`; 96 cache flip-flops built against 72, `NOTES.md`), and position resolution drops to buckets of 4 (ties go to the older token). One attention head (model dimension 4) with a KV cache in registers and the two phases of LLM inference:
**prefill** streams prompt tokens into the cache (1 cycle per token, independent of the cache length) and **decode** scans the cache serially
with ONE dot-product unit (n + 3 cycles for n cached entries), picks the best-scoring entry (strictly greater replaces: the lowest slot wins a tie)
and returns its value vector (hard attention), then appends the new token.

| parameter | value |
|---|---|
| cache entries N | 8 |
| bits per K/V component | 4 |
| ring | 0 |
| cache bits N x 2 x 4 x bits | 256 |
| flip-flops: estimated by inspection / built | about 420 / 222 (`output/metrics.json`, `design__instance__count__class:sequential_cell`) |
| die (config estimate, flow clean) | 220 x 220 um, 25 ns clock, utilisation 0.408 (`design__instance__utilization`) |

**Ports (24 pins):** `clk`, `rst` (synchronous, active high), input stream `s_valid`, `s_data[7:0]`, `s_last`, `s_ready`, output stream `m_valid`, `m_data[7:0]`, `m_last`, `m_ready`.
Commands (first beat): `01` RESET_CACHE, `02` PREFILL + tokens, `03` DECODE + token. Responses: 2 beats `[status, count]`, or 8 beats
`[status, count, index, score, v0..v3]` for a successful DECODE. Full contract, token embedding, error codes and cycle schedule:
`model/kv_attention/spec.md`; reference: `model/kv_attention/golden.py`.

**Files:** `rtl/kv_attn_n8_int4.v` (thin top), `rtl/kv_attn_n8_int4_rom.v` (generated constants), `../../shared/rtl/kv_attn_core.v` (the engine, shared by all five variants),
`tb/kv_attn_n8_int4_tb.v` + `../../shared/tb/kv_attn_tb.vh` (self-checking testbench), `tb/vectors.hex` (generated).

## Why 24 registers are pruned

`make check` compares the flip-flops in the elaborated RTL (246, `synth -flatten -noabc`) with the sequential cells that survive the
flow (222). The 24 that disappear are exact duplicates, not lost state; `scripts/flow/signoff_allowances.json` records
`"kv_attn_n8_int4": {"removed_registers": 24}`.

What each stored int4 component can ever hold (golden.py, tokens 0..15 x pos 0..31, `store_k` / `store_v` with 4 bits):

| field | values | 4-bit patterns |
|---|---|---|
| K0 = 4 e0 -> `(k+2)>>2` | -1, +1 | `1111`, `0001` |
| K1 = 4 e1 | -1, +1 | `1111`, `0001` |
| K2 (Wk column 3 is 0) | 0 | `0000` |
| K3 = `min(7, (pos+2)>>2)` | 0..7 | `0xxx` |
| V0 = e2 (value level) | -3, -1, 1, 3 | `1101`, `1111`, `0001`, `0011` |
| V1 = e0, V2 = e1 | -1, +1 | `1111`, `0001` |
| V3 = `min(7, pos)` | 0..7 | `0xxx` |

Per cache slot (x 8 slots = 24 flip-flops), the bits that vanish are:

* `kc` bit 3 and bit 7 (sign bit of K0 and of K1): K0, K1 are +-1, so bits 1, 2, 3 are all equal to the sign; the sign copy equals bit 1 (resp. bit 5). 2 x 8 = 16.
* `vc` bit 3 (sign bit of V0): e2 in {-3,-1,1,3} gives bit 3 == bit 2 in all four patterns. 1 x 8 = 8.

Each pair has the same data input and the same write enable, so it is the same flop; abc merges them. Already gone in the 246 count
(constants and identical-expression merges): K0/K1 bit 0 (always 1), V0/V1/V2 bit 0 (always 1), all of K2 (always 0), and V1/V2 (= e0/e1 =
K0/K1, same stored value) merged into kc. The 8-bit variants have no such pruning because their sign copies are literally the same
expression, which `-noabc` already merges (kv_attn_n4/n8/n8_ring/n16 report equal counts with and without abc: 159/200/198/277, `build/flow/<d>/stage_check.log`).

Not pruned although constant: the top bit of K3 and V3 (`0xxx`, saturated at 7) stays as a flip-flop in all 8 slots (16 flip-flops) because the
clamp is not provably constant to yosys without reachable-state analysis. They are dead by construction too, and an RTL change could
remove them; the allowance does not cover them (they survive).

Note on `config.json`: its `//DIE_AREA` comment still says "estimate, not yet hardened". The estimate has since been
confirmed by hardening (all signoff checks clean, see [NOTES.md](NOTES.md)). The comment is left as is because editing
`config.json` would mark the finished run stale. The same applies to the other `kv_attn_*` configs.
