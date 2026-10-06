# kv_attn_n8

Status: **Hardened**: `make flow-all` passed all 5 stages (simulate, gds, check, gate-level, collect). 2566 std cells, 260 x 260 um die (`config.json`), 25 ns clock, worst setup slack 10.74 ns, hold 0.105 ns, DRC/LVS/antenna clean, 1521-record RTL and gate-level simulation PASS; max-slew violations 551 are reported, not failing (`output/metrics.json`, `NOTES.md`).

**What it shows:** the base variant. One attention head (model dimension 4) with a KV cache in registers and the two phases of LLM inference:
**prefill** streams prompt tokens into the cache (1 cycle per token, independent of the cache length) and **decode** scans the cache serially
with ONE dot-product unit (n + 3 cycles for n cached entries), picks the best-scoring entry (strictly greater replaces: the lowest slot wins a tie)
and returns its value vector (hard attention), then appends the new token.

| parameter | value |
|---|---|
| cache entries N | 8 |
| bits per K/V component | 8 |
| ring | 0 |
| cache bits N x 2 x 4 x bits | 512 |
| estimated flip-flops (by inspection) | about 670 |
| die (estimate) | 260 x 260 um, 25 ns clock |

**Ports (24 pins):** `clk`, `rst` (synchronous, active high), input stream `s_valid`, `s_data[7:0]`, `s_last`, `s_ready`, output stream `m_valid`, `m_data[7:0]`, `m_last`, `m_ready`.
Commands (first beat): `01` RESET_CACHE, `02` PREFILL + tokens, `03` DECODE + token. Responses: 2 beats `[status, count]`, or 8 beats
`[status, count, index, score, v0..v3]` for a successful DECODE. Full contract, token embedding, error codes and cycle schedule:
`model/kv_attention/spec.md`; reference: `model/kv_attention/golden.py`.

**Files:** `rtl/kv_attn_n8.v` (thin top), `rtl/kv_attn_n8_rom.v` (generated constants), `../../shared/rtl/kv_attn_core.v` (the engine, shared by all five variants),
`tb/kv_attn_n8_tb.v` + `../../shared/tb/kv_attn_tb.vh` (self-checking testbench), `tb/vectors.hex` (generated).

Note: the `//DIE_AREA` comment in `config.json` ("estimate, not yet hardened") predates hardening; the die size was confirmed by a clean signoff run ([NOTES.md](NOTES.md)). It is not edited because that would mark the run stale.
