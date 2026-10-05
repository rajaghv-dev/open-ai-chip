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
