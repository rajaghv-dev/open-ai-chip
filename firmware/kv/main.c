/* KV-cache attention session for the local PicoRV32 SoC (soc_sim/kv/): PicoRV32 -> Wishbone -> wb_stream_adapter ->
 * kv_attn_n8. A scripted LLM-style session (RESET_CACHE, PREFILL of P tokens as ONE frame, DECODE until the cache is
 * full, one more DECODE = CACHE_FULL), every response beat checked against firmware/kv/gen_expected.py (golden.py),
 * with the cycle counter measuring prefill and decode round trips. rv32i, no libc, no multiply/divide at run time. */
#include <stdint.h>
#include "expected.h"

#define KV       0x30000000u
#define R_ID     (KV + 0x00)
#define R_CTRL   (KV + 0x04)
#define R_TXDATA (KV + 0x0C)
#define R_TXLAST (KV + 0x10)
#define R_RXDATA (KV + 0x14)
#define R_RXSTAT (KV + 0x18)
#define R_CYCLES (KV + 0x1C)
#define R_CAPS   (KV + 0x20)
#define CTRL_CLEAR (1u << 8)
#define RXS_DONE (1u << 2)

#define CONSOLE  0x20000000u
#define EXITREG  0x20000004u
#define CYCLE    0x20000008u

static inline void wr(uint32_t a, uint32_t v) { *(volatile uint32_t *)a = v; }
static inline uint32_t rd(uint32_t a) { return *(volatile uint32_t *)a; }

static void putc_(char c) { wr(CONSOLE, (uint32_t)c); }
static void puts_(const char *s) { while (*s) putc_(*s++); }
static void puthex(uint32_t v) {
    puts_("0x");
    for (int i = 28; i >= 0; i -= 4) putc_("0123456789abcdef"[(v >> i) & 15]);
}
static void putdec(uint32_t v) {
    static const uint32_t p10[] = {1000000000u,100000000u,10000000u,1000000u,100000u,10000u,1000u,100u,10u,1u};
    int started = 0;
    for (int i = 0; i < 10; i++) {
        uint32_t d = 0;
        while (v >= p10[i]) { v -= p10[i]; d++; }
        if (d || started || i == 9) { putc_((char)('0' + d)); started = 1; }
    }
}
static uint32_t udiv(uint32_t a, uint32_t b, uint32_t *rem) {
    uint32_t q = 0, r = 0;
    for (int i = 31; i >= 0; i--) { r = (r << 1) | ((a >> i) & 1); if (r >= b) { r -= b; q |= 1u << i; } }
    if (rem) *rem = r;
    return q;
}
static void putavg(uint32_t total, uint32_t n) {            /* total / n with one decimal */
    uint32_t r, q = udiv(total, n, &r);
    uint32_t t = udiv((r << 3) + (r << 1) + (n >> 1), n, 0);
    if (t >= 10) { q++; t -= 10; }
    putdec(q); putc_('.'); putc_((char)('0' + t));
}
static uint32_t x100(uint32_t v) { return (v << 6) + (v << 5) + (v << 2); }
static int dlen(uint32_t v) { int len = 1; for (uint32_t x = v; x >= 10; x = udiv(x, 10, 0)) len++; return len; }
static void padn(uint32_t v, int w) { for (int l = dlen(v); l < w; l++) putc_(' '); putdec(v); }
static void pada(uint32_t tot, uint32_t n, int w) {        /* average, right-aligned in w columns */
    uint32_t q = udiv(tot, n, 0);
    for (int l = dlen(q) + 2; l < w; l++) putc_(' ');
    putavg(tot, n);
}

static int g_fail;
static void check_eq(uint32_t got, uint32_t exp, const char *what) {
    if (got != exp) {
        g_fail++; puts_("  FAIL: "); puts_(what); puts_(" got "); puthex(got); puts_(" expected "); puthex(exp); putc_('\n');
    }
}

static uint32_t t_ovh;
static inline uint32_t tick(void) { return rd(CYCLE); }

typedef struct { uint32_t wr, wait, rd, total, reg; uint32_t n; uint8_t resp[16]; } frame_t;

/* one command frame: push the beats (the last one with TXLAST), poll RXSTATUS.DONE, pop the response up to m_last, read
 * CYCLES. Phases: write = pushes, wait = polling, read = RXDATA pops + CYCLES read (timer-read overhead subtracted). */
static void run_frame(const uint8_t *beats, int nb, frame_t *f) {
    uint32_t t0 = tick();
    for (int i = 0; i < nb - 1; i++) wr(R_TXDATA, beats[i]);
    wr(R_TXLAST, beats[nb - 1]);
    uint32_t t1 = tick();
    while (!(rd(R_RXSTAT) & RXS_DONE)) { }
    uint32_t t2 = tick();
    uint32_t n = 0, v;
    do { v = rd(R_RXDATA); if (n < 16) f->resp[n] = (uint8_t)v; n++; } while (!(v & 0x100) && n < 16);
    f->reg = rd(R_CYCLES);
    uint32_t t3 = tick();
    f->n = n;
    f->wr = t1 - t0 - t_ovh; f->wait = t2 - t1 - t_ovh; f->rd = t3 - t2 - t_ovh; f->total = f->wr + f->wait + f->rd;
}
static void check_resp(const frame_t *f, const uint8_t *exp, uint32_t ne, const char *what) {
    check_eq(f->n, ne, what);
    for (uint32_t i = 0; i < ne && i < f->n; i++)
        if (f->resp[i] != exp[i]) {
            g_fail++; puts_("  FAIL: "); puts_(what); puts_(" beat "); putdec(i);
            puts_(" got "); puthex(f->resp[i]); puts_(" expected "); puthex(exp[i]); putc_('\n');
        }
}

static uint32_t pf_tot[NSESS], pf_wr[NSESS], pf_wait[NSESS], pf_rd[NSESS], pf_reg[NSESS];
static uint32_t dc_n[8], dc_tot[8], dc_wr[8], dc_wait[8], dc_rd[8], dc_reg[8], dc_diff[8];
static uint32_t full_tot, full_reg;

static void session(int P) {
    frame_t f; uint8_t b[10];
    static const uint8_t rst_exp[2] = {0, 0};
    b[0] = 1; run_frame(b, 1, &f);
    check_resp(&f, rst_exp, 2, "RESET_CACHE response");
    b[0] = 2; for (int i = 0; i < P; i++) b[1 + i] = S_PROMPT[P][i];
    run_frame(b, P + 1, &f);
    check_resp(&f, S_PRE[P], 2, "PREFILL response");
    pf_tot[P] = f.total; pf_wr[P] = f.wr; pf_wait[P] = f.wait; pf_rd[P] = f.rd; pf_reg[P] = f.reg;
    uint32_t cyc[8];
    for (int i = 0; i < 8 - P; i++) {
        int n = P + i;
        b[0] = 3; b[1] = S_Q[P][i];
        run_frame(b, 2, &f);
        check_resp(&f, S_DEC[P][i], 8, "DECODE response");
        dc_n[n]++; dc_tot[n] += f.total; dc_wr[n] += f.wr; dc_wait[n] += f.wait; dc_rd[n] += f.rd; dc_reg[n] += f.reg;
        cyc[i] = f.reg;
    }
    b[0] = 3; b[1] = 0; run_frame(b, 2, &f);                 /* cache full: CACHE_FULL, 2 beats, engine L = 2 */
    check_resp(&f, S_FULL[P], 2, "DECODE on a full cache response");
    full_tot = f.total; full_reg = f.reg;
    /* Same 2 input beats as the baseline, so the CPU write gap cancels in the difference: the engine's latency differs
     * by (n+3)-2 and the response by 8-2 beats, so CYCLES(decode at n) - CYCLES(baseline) must be n + 7. */
    for (int i = 0; i < 8 - P; i++) {
        int n = P + i;
        dc_diff[n] += cyc[i] - f.reg;
        check_eq(cyc[i] - f.reg, (uint32_t)(n + 7), "decode CYCLES minus baseline (expected n+7)");
    }
}

static void print_tables(void) {
    puts_("\nPREFILL: one frame [02, t0..t(P-1)], CPU clock cycles, timer overhead removed\n");
    puts_("  P  roundtrip  per_token  write  wait  read  CYCLES_reg\n");
    for (int P = 1; P < NSESS; P++) {
        puts_("  "); putdec((uint32_t)P); puts_("  "); padn(pf_tot[P], 9); puts_("  "); pada(pf_tot[P], (uint32_t)P, 9);
        puts_("  "); padn(pf_wr[P], 5); puts_("  "); padn(pf_wait[P], 4); puts_("  "); padn(pf_rd[P], 4);
        puts_("  "); padn(pf_reg[P], 10); putc_('\n');
    }
    puts_("\nDECODE by cache fill n (average over the sessions that decode at that n)\n");
    puts_("  n  cases  roundtrip   write    wait    read  (wr%/wait%/rd%)  CYCLES_reg  minus_base  engine_L(n+3)\n");
    for (int n = 0; n < 8; n++) {
        uint32_t c = dc_n[n];
        puts_("  "); putdec((uint32_t)n); puts_("  "); padn(c, 5); puts_("  ");
        pada(dc_tot[n], c, 9); puts_("  "); pada(dc_wr[n], c, 6); puts_("  "); pada(dc_wait[n], c, 6);
        puts_("  "); pada(dc_rd[n], c, 6); puts_("  (");
        putdec(udiv(x100(dc_wr[n]), dc_tot[n], 0)); puts_("/");
        putdec(udiv(x100(dc_wait[n]), dc_tot[n], 0)); puts_("/");
        putdec(udiv(x100(dc_rd[n]), dc_tot[n], 0)); puts_(")  ");
        pada(dc_reg[n], c, 10); puts_("  "); pada(dc_diff[n], c, 10); puts_("  "); padn((uint32_t)(n + 3), 13); putc_('\n');
    }
    puts_("baseline (DECODE on a full cache, 2 response beats): roundtrip "); putdec(full_tot);
    puts_(", CYCLES_reg "); putdec(full_reg); putc_('\n');
    puts_("CYCLES_reg = first TX beat accepted to m_last captured (includes the CPU write gap and the response beats);\n");
    puts_("minus_base = same frame shape on a full cache subtracted, expected n+7 = (n+3-2) + (8-2).\n");
}

int main(void) {
    puts_("kv_attn_n8 firmware session (PicoRV32 rv32i, wb_stream_adapter)\n");
    uint32_t a = tick(), b = tick(); t_ovh = b - a;
    puts_("timer read overhead (cycles): "); putdec(t_ovh); putc_('\n');
    check_eq(rd(R_ID), 0x53545201u, "adapter ID");
    puts_("CAPS "); puthex(rd(R_CAPS)); putc_('\n');
    wr(R_CTRL, CTRL_CLEAR);
    for (int P = 0; P < NSESS; P++) session(P);
    print_tables();
    puts_("total cycles: "); putdec(tick()); putc_('\n');
    if (g_fail) { puts_("FAIL ("); putdec((uint32_t)g_fail); puts_(" checks failed)\n"); return 2; }
    puts_("PASS\n");
    return 1;
}
