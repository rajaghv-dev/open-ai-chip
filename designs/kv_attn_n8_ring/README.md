# kv_attn_n8_ring

Status: **hardened clean** (`make flow-all DESIGN=kv_attn_n8_ring`: all 5 stages PASS; 2621 std cells, 198 flip-flops, die 260 x 260 um, 25 ns clock; details in NOTES.md)

**What it shows:** a ring cache: never CACHE_FULL, the oldest entry is overwritten (sliding window), decode scans all 8 slots once wrapped (11 cycles). One attention head (model dimension 4) with a KV cache in registers and the two phases of LLM inference:
**prefill** streams prompt tokens into the cache (1 cycle per token, independent of the cache length) and **decode** scans the cache serially
with ONE dot-product unit (n + 3 cycles for n cached entries), picks the best-scoring entry (strictly greater replaces: the lowest slot wins a tie)
and returns its value vector (hard attention), then appends the new token.

| parameter | value |
|---|---|
| cache entries N | 8 |
| bits per K/V component | 8 |
| ring | 1 |
| cache bits N x 2 x 4 x bits | 512 |
| flip-flops: estimated by inspection / built | about 670 / 198 (`output/metrics.json`, `design__instance__count__class:sequential_cell`) |
| die (config estimate, flow clean) | 260 x 260 um, 25 ns clock, utilisation 0.283 (`design__instance__utilization`) |

**Ports (24 pins):** `clk`, `rst` (synchronous, active high), input stream `s_valid`, `s_data[7:0]`, `s_last`, `s_ready`, output stream `m_valid`, `m_data[7:0]`, `m_last`, `m_ready`.
Commands (first beat): `01` RESET_CACHE, `02` PREFILL + tokens, `03` DECODE + token. Responses: 2 beats `[status, count]`, or 8 beats
`[status, count, index, score, v0..v3]` for a successful DECODE. Full contract, token embedding, error codes and cycle schedule:
`model/kv_attention/spec.md`; reference: `model/kv_attention/golden.py`.

**Files:** `rtl/kv_attn_n8_ring.v` (thin top), `rtl/kv_attn_n8_ring_rom.v` (generated constants), `../../shared/rtl/kv_attn_core.v` (the engine, shared by all five variants),
`tb/kv_attn_n8_ring_tb.v` + `../../shared/tb/kv_attn_tb.vh` (self-checking testbench), `tb/vectors.hex` (generated).

Note: the `//DIE_AREA` comment in `config.json` ("estimate, not yet hardened") predates hardening; the die size was confirmed by a clean signoff run ([NOTES.md](NOTES.md)). It is not edited because that would mark the run stale.
