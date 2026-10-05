#!/usr/bin/env python3
"""bin2hex.py in.bin out.hex -- little-endian 32-bit words, one per line, for $readmemh."""
import sys
d = open(sys.argv[1], "rb").read()
d += b"\0" * (-len(d) % 4)
open(sys.argv[2], "w").write("".join("%08x\n" % int.from_bytes(d[i:i+4], "little") for i in range(0, len(d), 4)))
