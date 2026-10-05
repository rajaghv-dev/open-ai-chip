# firmware for the local PicoRV32 SoC (soc_sim/). `make sim` builds, simulates, exits non-zero unless PASS.
# rv32i/ilp32: no M extension, so no libgcc is needed (the code never multiplies or divides at run time).
CROSS  ?= riscv64-elf-
CFLAGS := -march=rv32i -mabi=ilp32 -Os -Wall -Wextra -ffreestanding -fno-builtin -nostdlib -fno-pic
LDFLAGS := -march=rv32i -mabi=ilp32 -nostdlib -static -Wl,-T,link.ld -Wl,--no-warn-rwx-segments
B := build

.PHONY: all sim clean
all: $(B)/firmware.hex

$(B)/expected.h: gen_expected.py ../model/tiny_ai/golden.py ../model/tiny_ai/weights.json ../model/tiny_ai/common.py
	@mkdir -p $(B)
	python3 gen_expected.py $@

$(B)/main.o: main.c $(B)/expected.h
	$(CROSS)gcc $(CFLAGS) -I$(B) -c main.c -o $@
$(B)/start.o: start.S
	@mkdir -p $(B)
	$(CROSS)gcc $(CFLAGS) -c start.S -o $@
$(B)/firmware.elf: $(B)/start.o $(B)/main.o link.ld
	$(CROSS)gcc $(LDFLAGS) $(B)/start.o $(B)/main.o -o $@
	$(CROSS)size $@
$(B)/firmware.hex: $(B)/firmware.elf bin2hex.py
	$(CROSS)objcopy -O binary $< $(B)/firmware.bin
	python3 bin2hex.py $(B)/firmware.bin $@

sim: $(B)/firmware.hex
	../soc_sim/run.sh $(abspath $(B)/firmware.hex)

clean:
	rm -rf $(B)
