#!/usr/bin/env python3
# Purpose: Convert a raw RISC-V binary into 32-bit little-endian hex words (one per line) for $readmemh.
# Run: python3 bin2hex.py in.bin out.hex (called by firmware/Makefile and firmware/kv/Makefile).
# In: .bin from objcopy. Out: firmware.hex loaded by soc_sim/*.v.
# Docs: firmware/README.md
"""bin2hex.py in.bin out.hex -- little-endian 32-bit words, one per line, for $readmemh."""
import sys
d = open(sys.argv[1], "rb").read()
d += b"\0" * (-len(d) % 4)   # pad to a whole number of 32-bit words
# one word per line, lowest address first: matches $readmemh into a 32-bit RAM array
open(sys.argv[2], "w").write("".join("%08x\n" % int.from_bytes(d[i:i+4], "little") for i in range(0, len(d), 4)))
