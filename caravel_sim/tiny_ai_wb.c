/* Caravel management-core (VexRiscv) firmware: drives tiny_ai_core (mprj) over the user Wishbone window 0x3000_0000.
 * Result signalling (read by tiny_ai_wb_tb.v from mprj_io[31:16]): 0xAB60 started, 0xAB61 all checks passed,
 * 0xE0nn..: failure, nn = number of the failing check. Expected values from model/tiny_ai/golden.py core_run():
 *   vision_all_lit 1 1 1 1 -> class 1 score 4 CYCLES 6; text_sentiment 1 1 3 0 -> class 1 score 1 CYCLES 6;
 *   vision_block all ones  -> class 1 score 4 CYCLES 15; vision_block diagonal -> class 0 score 2 CYCLES 15. */
#include <defs.h>
#include <stub.c>

#define TAI(off) (*(volatile uint32_t *)(0x30000000u + (off)))
#define R_ID 0x00
#define R_CTRL 0x04
#define R_STATUS 0x08
#define R_INPUT 0x0C
#define R_RESULT 0x10
#define R_CYCLES 0x14
#define START (1u << 8)
#define CLEAR (1u << 9)

static void flag(uint32_t v) { reg_mprj_datal = v << 16; }
static void fail(uint32_t n) { flag(0xE000u | n); while (1) ; }

static void run(uint32_t mode, const uint8_t *in, uint32_t n, uint32_t exp_res, uint32_t exp_cyc, uint32_t id) {
    TAI(R_CTRL) = CLEAR | mode;
    for (uint32_t i = 0; i < n; i++) TAI(R_INPUT) = in[i];
    TAI(R_CTRL) = START | mode;
    uint32_t guard = 0, s;
    do { s = TAI(R_STATUS); if (++guard > 10000) fail(id | 0x40); } while (s & 1);
    if (!(s & 2) || (s & 4)) fail(id | 0x10);                 /* DONE and not ERROR */
    if (TAI(R_RESULT) != exp_res) fail(id | 0x20);
    if (TAI(R_CYCLES) != exp_cyc) fail(id | 0x30);
}

void main(void) {
    reg_spi_enable = 1;
    reg_wb_enable = 1;                                        /* connect user Wishbone */
    /* the 16 upper mprj_io pins carry the result code */
    reg_mprj_io_31 = GPIO_MODE_MGMT_STD_OUTPUT; reg_mprj_io_30 = GPIO_MODE_MGMT_STD_OUTPUT;
    reg_mprj_io_29 = GPIO_MODE_MGMT_STD_OUTPUT; reg_mprj_io_28 = GPIO_MODE_MGMT_STD_OUTPUT;
    reg_mprj_io_27 = GPIO_MODE_MGMT_STD_OUTPUT; reg_mprj_io_26 = GPIO_MODE_MGMT_STD_OUTPUT;
    reg_mprj_io_25 = GPIO_MODE_MGMT_STD_OUTPUT; reg_mprj_io_24 = GPIO_MODE_MGMT_STD_OUTPUT;
    reg_mprj_io_23 = GPIO_MODE_MGMT_STD_OUTPUT; reg_mprj_io_22 = GPIO_MODE_MGMT_STD_OUTPUT;
    reg_mprj_io_21 = GPIO_MODE_MGMT_STD_OUTPUT; reg_mprj_io_20 = GPIO_MODE_MGMT_STD_OUTPUT;
    reg_mprj_io_19 = GPIO_MODE_MGMT_STD_OUTPUT; reg_mprj_io_18 = GPIO_MODE_MGMT_STD_OUTPUT;
    reg_mprj_io_17 = GPIO_MODE_MGMT_STD_OUTPUT; reg_mprj_io_16 = GPIO_MODE_MGMT_STD_OUTPUT;
    reg_mprj_xfer = 1;
    while (reg_mprj_xfer == 1) ;
    reg_la2_oenb = reg_la2_iena = 0x00000000;
    flag(0xAB60);

    if (TAI(R_ID) != 0x54414901u) fail(1);

    static const uint8_t lit[4] = {1, 1, 1, 1};
    static const uint8_t txt[4] = {1, 1, 3, 0};
    static const uint8_t blk1[9] = {1, 1, 1, 1, 1, 1, 1, 1, 1};
    static const uint8_t blk0[9] = {1, 0, 0, 0, 1, 0, 0, 0, 1};
#ifdef NEG_TEST
    run(0, lit, 4, 0x0401, 7, 2);   /* deliberately wrong CYCLES: must report FAIL code 0xE032 */
#else
    run(0, lit, 4, 0x0401, 6, 2);
#endif
    run(2, txt, 4, 0x0101, 6, 3);
    run(1, blk1, 9, 0x0401, 15, 4);
    run(1, blk0, 9, 0x0200, 15, 5);

    flag(0xAB61);
}
