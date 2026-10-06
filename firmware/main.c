/* Purpose: Self-test and CPU-vs-accelerator cycle comparison for tiny_ai_core, run by PicoRV32 in soc_sim/.
 * Run: make soc-sim (builds firmware/build/firmware.hex and simulates it).
 * In: build/expected.h (generated). Out: console text and PASS/FAIL via the EXIT register.
 * Docs: firmware/README.md, docs/SOC_PLAN.md
 */
/* Self-test + CPU-vs-accelerator comparison for tiny_ai_core, run by PicoRV32 in soc_sim/ (rv32i, no libc).
 * Register-level, this is what Caravel management-core firmware would do (docs/SOC_PLAN.md). */
#include <stdint.h>
#include "expected.h"

/* tiny_ai_core register map (offsets from the user Wishbone window; see docs/SOC_PLAN.md). Each is a 32-bit register. */
#define TAI      0x30000000u
#define R_ID     (TAI + 0x00)
#define R_CTRL   (TAI + 0x04)
#define R_STATUS (TAI + 0x08)
#define R_INPUT  (TAI + 0x0C)
#define R_RESULT (TAI + 0x10)
#define R_CYCLES (TAI + 0x14)
#define R_CAPS   (TAI + 0x18)
#define R_DEBUG  (TAI + 0x1C)
#define CTRL_START (1u << 8)
#define CTRL_CLEAR (1u << 9)
#define ST_BUSY 1u
#define ST_DONE 2u
#define ST_ERR  4u

/* Testbench-only peripherals provided by soc_sim/soc_tb.v (not part of the chip). */
#define CONSOLE  0x20000000u
#define EXITREG  0x20000004u
#define CYCLE    0x20000008u   /* free-running clock counter */
#define IRQST    0x2000000Cu   /* bit0: user_irq[0] seen since last write (sticky, any write clears) */

/* volatile accesses: every read/write must reach the bus exactly once, in program order */
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
/* a / b by shift-subtract (the core has no M extension, and linking libgcc is avoided); remainder in *rem */
static uint32_t udiv(uint32_t a, uint32_t b, uint32_t *rem) {
    uint32_t q = 0, r = 0;
    for (int i = 31; i >= 0; i--) { r = (r << 1) | ((a >> i) & 1); if (r >= b) { r -= b; q |= 1u << i; } }
    if (rem) *rem = r;
    return q;
}
/* total / n rounded to one decimal, printed as "x.y" */
static void putavg(uint32_t total, uint32_t n) {
    uint32_t r, q = udiv(total, n, &r);
    uint32_t t = udiv(r * 10 + n / 2, n, 0);          /* one decimal, rounded */
    if (t >= 10) { q++; t -= 10; }
    putdec(q); putc_('.'); putc_((char)('0' + t));
}
static void pad(uint32_t v, int w) {                    /* right-aligned decimal */
    int len = 1; for (uint32_t x = v; x >= 10; x = udiv(x, 10, 0)) len++;
    while (len++ < w) putc_(' ');
    putdec(v);
}

/* failure counter: checks do not abort, so one run reports every failing check; main() turns it into the exit code */
static int g_fail;
static void check(int ok, const char *what) {
    if (!ok) { g_fail++; puts_("  FAIL: "); puts_(what); putc_('\n'); }
}
static void check_eq(uint32_t got, uint32_t exp, const char *what) {
    if (got != exp) {
        g_fail++; puts_("  FAIL: "); puts_(what); puts_(" got "); puthex(got); puts_(" expected "); puthex(exp); putc_('\n');
    }
}

/* ------------------------------------------------------------------ accelerator driver */
static void tai_clear(uint32_t mode) { wr(R_CTRL, CTRL_CLEAR | mode); }
static void tai_push(uint32_t v) { wr(R_INPUT, v); }
static void tai_start(uint32_t mode) { wr(R_CTRL, CTRL_START | mode); }
static uint32_t tai_wait(void) {
    uint32_t s; do { s = rd(R_STATUS); } while (s & ST_BUSY);
    return s;
}

/* Timing method: the CYCLE register is read before and after each interval; t_ovh (one read) is subtracted. */
static uint32_t t_ovh;     /* cost of one timer read, measured at start */
static uint32_t tick(void) { return rd(CYCLE); }

/* one inference through the accelerator; returns RESULT, fills *cyc (CYCLES reg); accumulates phase times */
static uint32_t g_ph_wr, g_ph_wait, g_ph_rd;
static uint32_t tai_infer(uint32_t mode, uint32_t in, uint32_t n, uint32_t *cyc, uint32_t *status) {
    uint32_t t0 = tick();
    tai_clear(mode);
    for (uint32_t k = 0; k < n; k++) tai_push((in >> (2 * k)) & 3);
    tai_start(mode);
    uint32_t t1 = tick();
    uint32_t s = tai_wait();
    uint32_t t2 = tick();
    uint32_t res = rd(R_RESULT);
    *cyc = rd(R_CYCLES);
    uint32_t t3 = tick();
    g_ph_wr += t1 - t0 - t_ovh; g_ph_wait += t2 - t1 - t_ovh; g_ph_rd += t3 - t2 - t_ovh;
    *status = s;
    return res;
}

/* ------------------------------------------------------------------ pure-C software networks (bit-exact with golden.py) */
static uint32_t popc4(uint32_t x) { return (x & 1) + ((x >> 1) & 1) + ((x >> 2) & 1) + ((x >> 3) & 1); }
/* returns {class | score << 8} like RESULT */
static uint32_t sw_all_lit(uint32_t in) {
    uint32_t score = 0;
    for (int i = 0; i < 4; i++) score += (((in >> (2 * i)) & 3) == W_ALL_LIT[i]);   /* XNOR-count neuron */
    return (score >= TH_ALL_LIT) | score << 8;
}
static uint32_t sw_block(uint32_t in) {
    uint32_t f[9], k = 0, best = 0;
    for (int i = 0; i < 9; i++) f[i] = (in >> (2 * i)) & 3;
    for (int i = 0; i < 4; i++) k |= (uint32_t)K_BLOCK[i] << i;
    for (int r = 0; r < 2; r++)
        for (int c = 0; c < 2; c++) {                      /* 2x2 kernel over the four windows, bit order TL,TR,BL,BR */
            int b = r * 3 + c;
            uint32_t win = f[b] | f[b + 1] << 1 | f[b + 3] << 2 | f[b + 4] << 3;
            uint32_t cnt = popc4(~(win ^ k) & 0xF);
            if (cnt > best) best = cnt;                    /* max pooling; class = (max >= th) is the OR of the windows */
        }
    return (best >= TH_BLOCK) | best << 8;
}
static uint32_t sw_text(uint32_t in) {
    int32_t sum = 0;
    for (int i = 0; i < 4; i++) sum += EMB_TEXT[(in >> (2 * i)) & 3];                /* embedding lookup + sum */
    return (sum > TH_TEXT) | ((uint32_t)sum & 0xFF) << 8;
}
static uint32_t sw_run(uint32_t mode, uint32_t in) {
    return mode == 0 ? sw_all_lit(in) : mode == 1 ? sw_block(in) : sw_text(in);
}

/* ------------------------------------------------------------------ tests */
static void test_regs(void) {
    puts_("[1] register block\n");
    check_eq(rd(R_ID), 0x54414901u, "ID");
    uint32_t caps = rd(R_CAPS);
    check_eq(caps & 7, 7, "CAPS modes");
    check_eq((caps >> 8) & 15, 9, "CAPS max inputs");
    check_eq((caps >> 16) & 255, 1, "CAPS version");
    check_eq(rd(R_STATUS), 0, "STATUS after reset");
    check_eq(rd(TAI + 0x20), 0, "unmapped read 0x20");
    check_eq(rd(TAI + 0xFC), 0, "unmapped read 0xFC");
    check_eq(rd(R_INPUT), 0, "INPUT is write-only (reads 0)");
    wr(TAI + 0x20, 0xFFFFFFFFu);                           /* unmapped write: no effect */
    check_eq(rd(R_STATUS), 0, "STATUS after unmapped write");
    /* byte lanes: sb to CTRL lane 0 sets the mode; sb to lane 1 with bit 9 is CLEAR */
    *(volatile uint8_t *)R_CTRL = 2;
    check_eq(rd(R_CTRL), 2, "CTRL mode via byte write");
    tai_push(1); tai_push(3);
    check_eq((rd(R_STATUS) >> 8) & 15, 2, "count 2");
    check_eq(rd(R_DEBUG), 0x0000000Du, "DEBUG buffer {3,1}");
    *(volatile uint8_t *)(R_CTRL + 1) = 2;               /* CLEAR through byte lane 1 */
    check_eq((rd(R_STATUS) >> 8) & 15, 0, "count 0 after CLEAR");
    check_eq(rd(R_DEBUG), 0, "DEBUG cleared");
    tai_clear(0);
}

static void test_protocol(void) {
    puts_("[2] protocol negative cases (each must set sticky ERROR; CLEAR removes it)\n");
    tai_clear(0); check_eq(rd(R_STATUS) & ST_ERR, 0, "ERROR clear at start");
    tai_start(0);                                          /* START with 0 inputs */
    check(rd(R_STATUS) & ST_ERR, "START with no inputs sets ERROR");
    check_eq(rd(R_STATUS) & ST_BUSY, 0, "...and does not start");
    tai_clear(0); check_eq(rd(R_STATUS) & 7, 0, "CLEAR clears ERROR");
    tai_push(1); tai_push(1); tai_push(1); tai_start(0);   /* wrong count (3 of 4) */
    check(rd(R_STATUS) & ST_ERR, "START with 3 of 4 inputs sets ERROR");
    tai_clear(0);
    tai_push(2);                                           /* out of range for mode 0 */
    check(rd(R_STATUS) & ST_ERR, "input 2 in mode 0 sets ERROR");
    check_eq((rd(R_STATUS) >> 8) & 15, 0, "...and is not stored");
    tai_clear(2); tai_push(4);
    check(rd(R_STATUS) & ST_ERR, "input 4 in mode 2 sets ERROR");
    tai_clear(3); tai_push(0);
    check(rd(R_STATUS) & ST_ERR, "any input in mode 3 sets ERROR");
    tai_clear(3); tai_start(3);
    check(rd(R_STATUS) & ST_ERR, "START in mode 3 sets ERROR");
    tai_clear(1);
    for (int i = 0; i < 9; i++) tai_push(1);
    check_eq(rd(R_STATUS) & ST_ERR, 0, "9 inputs accepted in mode 1");
    tai_push(1);
    check(rd(R_STATUS) & ST_ERR, "10th input sets ERROR");
    check_eq((rd(R_STATUS) >> 8) & 15, 9, "...count stays 9");
    /* START + CLEAR in one write: CLEAR wins */
    tai_clear(0); for (int i = 0; i < 4; i++) tai_push(1);
    wr(R_CTRL, CTRL_START | CTRL_CLEAR);
    check_eq(rd(R_STATUS), 0, "START+CLEAR: clear wins, no run");
    /* INPUT / START / CLEAR while busy (mode 1 runs 15 clocks; the next bus write comes sooner) */
    tai_clear(1); for (int i = 0; i < 9; i++) tai_push(i & 1);
    { volatile uint32_t *p = (volatile uint32_t *)R_CTRL, *q = (volatile uint32_t *)R_INPUT; uint32_t st = CTRL_START | 1;
      uint32_t one = 1; __asm__ volatile("sw %2,0(%0)\n\tsw %3,0(%1)" :: "r"(p), "r"(q), "r"(st), "r"(one) : "memory"); }                                   /* INPUT lands while busy */
    uint32_t s = tai_wait();
    check(s & ST_ERR, "INPUT while busy sets ERROR");
    check(s & ST_DONE, "...but the run still completes (DONE)");
    check_eq(rd(R_RESULT) & 1, 0, "...with the right class (no lit 2x2 window)");
    tai_clear(1); for (int i = 0; i < 9; i++) tai_push(1);
    tai_start(1);
    tai_start(1);
    check(rd(R_STATUS) & ST_ERR, "START while busy sets ERROR");
    s = tai_wait();
    check(s & ST_DONE, "...and the first run completes");
    check_eq(rd(R_RESULT) & 1, 1, "...class 1 (all lit)");
    tai_clear(1); for (int i = 0; i < 9; i++) tai_push(1);
    { volatile uint32_t *p = (volatile uint32_t *)R_CTRL; uint32_t st = CTRL_START | 1, cl = CTRL_CLEAR | 1;
      __asm__ volatile("sw %1,0(%0)\n\tsw %2,0(%0)" :: "r"(p), "r"(st), "r"(cl) : "memory"); }                                  /* two back-to-back stores: the second lands while busy */
    check(rd(R_STATUS) & ST_ERR, "CLEAR while busy sets ERROR");
    s = tai_wait();
    check(s & ST_DONE, "...and the run still completes");
    tai_clear(0);
    check_eq(rd(R_STATUS) & 0xF0F, 0, "clean after CLEAR (active-mode field 5:4 is not cleared)");
}

static void test_irq(void) {
    puts_("[3] irq[0] completion pulse\n");
    tai_clear(0);
    for (int i = 0; i < 4; i++) tai_push(1);
    wr(IRQST, 0);
    check_eq(rd(IRQST), 0, "irq flag cleared");
    tai_start(0);
    tai_wait();
    check_eq(rd(IRQST), 1, "irq[0] seen on completion");
    tai_clear(0);
}

static const char *names[3] = {"vision_all_lit", "vision_block", "text_sentiment"};
static const tcase_t *tabs[3] = {cases_vision_all_lit, cases_vision_block, cases_text_sentiment};
static const uint32_t ns[3] = {N_VISION_ALL_LIT, N_VISION_BLOCK, N_TEXT_SENTIMENT};
static const uint32_t lens[3] = {LEN_VISION_ALL_LIT, LEN_VISION_BLOCK, LEN_TEXT_SENTIMENT};

static uint32_t sum_sw[3], sum_acc[3], sum_reg[3], sum_ph[3][3];

static void test_modes(void) {
    puts_("[4] exhaustive: every input of every mode, accelerator and software vs golden.py tables\n");
    for (uint32_t m = 0; m < 3; m++) {
        uint32_t bad_acc = 0, bad_sw = 0, bad_cyc = 0;
        g_ph_wr = g_ph_wait = g_ph_rd = 0;
        for (uint32_t i = 0; i < ns[m]; i++) {
            uint32_t in = tabs[m][i].in, ex = tabs[m][i].exp, cyc, st;
            uint32_t res = tai_infer(m, in, lens[m], &cyc, &st);
            if ((res & 0xFFFF) != (ex & 0xFFFF) || (st & ST_ERR)) bad_acc++;
            if (cyc != ((ex >> 16) & 255)) bad_cyc++;
            if ((res & 1) != ((ex >> 24) & 1)) bad_acc++;                       /* labelled truth */
            sum_reg[m] += cyc;
        }
        sum_ph[m][0] = g_ph_wr; sum_ph[m][1] = g_ph_wait; sum_ph[m][2] = g_ph_rd;
        sum_acc[m] = g_ph_wr + g_ph_wait + g_ph_rd;
        for (uint32_t i = 0; i < ns[m]; i++) {                                   /* software, timed separately */
            uint32_t in = tabs[m][i].in, ex = tabs[m][i].exp;
            uint32_t t0 = tick();
            uint32_t r = sw_run(m, in);
            uint32_t t1 = tick();
            sum_sw[m] += t1 - t0 - t_ovh;
            if (r != (ex & 0xFFFF)) bad_sw++;
        }
        puts_("  "); puts_(names[m]); puts_(": "); putdec(ns[m]); puts_(" cases, accelerator mismatches ");
        putdec(bad_acc); puts_(", CYCLES mismatches "); putdec(bad_cyc); puts_(", software mismatches "); putdec(bad_sw); putc_('\n');
        check_eq(bad_acc, 0, "accelerator vs golden"); check_eq(bad_cyc, 0, "CYCLES vs golden"); check_eq(bad_sw, 0, "software vs golden");
    }
}

static void print_table(void) {
    puts_("[5] cycles per inference (CPU clock cycles, averages over all cases of the mode; timer-read overhead "); putdec(t_ovh); puts_(" subtracted)\n");
    puts_("mode            cases  sw_cpu  accel_roundtrip  (write+wait+read)  accel_CYCLES_reg  sw/accel\n");
    for (uint32_t m = 0; m < 3; m++) {
        puts_(names[m]);
        { int l = 0; while (names[m][l]) l++; while (l++ < 16) putc_(' '); }
        pad(ns[m], 5); puts_("  ");
        putavg(sum_sw[m], ns[m]); puts_("  ");
        putavg(sum_acc[m], ns[m]); puts_("  (");
        putavg(sum_ph[m][0], ns[m]); putc_('+'); putavg(sum_ph[m][1], ns[m]); putc_('+'); putavg(sum_ph[m][2], ns[m]); puts_(")  ");
        putavg(sum_reg[m], ns[m]); puts_("  ");
        putavg(sum_sw[m], sum_acc[m]); puts_("x\n");
    }
    puts_("(sw/accel = software cycles divided by accelerator round-trip cycles)\n");
}

int main(void) {
    puts_("tiny_ai_core firmware self-test (PicoRV32 rv32i)\n");
    uint32_t a = tick(), b = tick(); t_ovh = b - a;
    puts_("timer read overhead (cycles): "); putdec(t_ovh); putc_('\n');
    test_regs();
    test_protocol();
    test_irq();
    test_modes();
    print_table();
    puts_("total cycles: "); putdec(tick()); putc_('\n');
    /* EXIT register protocol (start.S): return 1 = PASS, any other value = FAIL code */
    if (g_fail) { puts_("FAIL ("); putdec((uint32_t)g_fail); puts_(" checks failed)\n"); return 2; }
    puts_("PASS\n");
    return 1;
}
