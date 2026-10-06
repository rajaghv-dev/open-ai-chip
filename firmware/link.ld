/* Purpose: Linker script: 64 KiB RAM at address 0, code/data/bss in order, stack at the top (matches soc_sim/soc_tb.v).
 * Run: used by the gcc link step of firmware/Makefile and firmware/kv/Makefile (-Wl,-T,link.ld).
 * In: object files. Out: firmware.elf.
 * Docs: firmware/README.md
 */
/* 64 KiB on-chip RAM at 0 (soc_sim/soc_tb.v); stack grows down from the top */
MEMORY { RAM (rwx) : ORIGIN = 0x00000000, LENGTH = 64K }
ENTRY(_start)
SECTIONS {
  .text : { *(.text.start) *(.text*) } > RAM
  .rodata : { *(.rodata*) *(.srodata*) } > RAM
  .data : { *(.data*) *(.sdata*) } > RAM
  .bss : { . = ALIGN(4); _bss_start = .; *(.bss*) *(.sbss*) *(COMMON) . = ALIGN(4); _bss_end = .; } > RAM
  _stack_top = ORIGIN(RAM) + LENGTH(RAM);
}
