# Purpose: Build the KV-cache attention firmware (main.c) and run it in soc_sim/kv/.
# Run: make soc-kv (top-level) or make -C firmware/kv sim.
# In: main.c, ../start.S, ../link.ld, ../bin2hex.py, gen_expected.py. Out: build/firmware.hex, simulation console log.
# Docs: firmware/README.md, docs/LLM_INFERENCE.md
# KV-cache attention firmware (second program) for soc_sim/kv. `make -C firmware/kv sim` builds, simulates, exits non-zero unless PASS.
# Reuses ../start.S ../link.ld ../bin2hex.py unchanged.
CROSS  ?= riscv64-elf-
CFLAGS := -march=rv32i -mabi=ilp32 -Os -Wall -Wextra -ffreestanding -fno-builtin -nostdlib -fno-pic
LDFLAGS := -march=rv32i -mabi=ilp32 -nostdlib -static -Wl,-T,../link.ld -Wl,--no-warn-rwx-segments
B := build

.PHONY: all sim clean
all: $(B)/firmware.hex

$(B)/expected.h: gen_expected.py ../../model/kv_attention/golden.py
	@mkdir -p $(B)
	python3 gen_expected.py $@
$(B)/main.o: main.c $(B)/expected.h
	$(CROSS)gcc $(CFLAGS) -I$(B) -c main.c -o $@
$(B)/start.o: ../start.S
	@mkdir -p $(B)
	$(CROSS)gcc $(CFLAGS) -c ../start.S -o $@
$(B)/firmware.elf: $(B)/start.o $(B)/main.o ../link.ld
	$(CROSS)gcc $(LDFLAGS) $(B)/start.o $(B)/main.o -o $@
	$(CROSS)size $@
$(B)/firmware.hex: $(B)/firmware.elf ../bin2hex.py
	$(CROSS)objcopy -O binary $< $(B)/firmware.bin
	python3 ../bin2hex.py $(B)/firmware.bin $@

sim: $(B)/firmware.hex
	../../soc_sim/kv/run.sh $(abspath $(B)/firmware.hex)

clean:
	rm -rf $(B)
