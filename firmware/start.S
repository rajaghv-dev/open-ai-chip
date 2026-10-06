/* Purpose: Reset entry: set the stack pointer, clear .bss, call main(), then write the result to the EXIT register and halt.
 * Run: assembled by firmware/Makefile and firmware/kv/Makefile (make soc-sim, make soc-kv).
 * In: symbols from link.ld. Out: start.o.
 * Docs: firmware/README.md
 */
    .section .text.start
    .globl _start
_start:
    la sp, _stack_top        /* _stack_top comes from link.ld: top of the 64 KiB RAM */
    la t0, _bss_start
    la t1, _bss_end
1:  bgeu t0, t1, 2f
    sw zero, 0(t0)
    addi t0, t0, 4
    j 1b
2:  call main                /* main returns 1 = PASS or an error code */
    li t0, 0x20000004     # EXIT register: 1 = PASS, anything else = FAIL code
    sw a0, 0(t0)
3:  j 3b
