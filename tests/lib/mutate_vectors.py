#!/usr/bin/env python3
"""mutate_vectors.py <design> <src vectors.hex> <dst> -- write a copy of a design's vectors with ONE expected value flipped.

Used by tests/run_tests.sh (== negative-all): the self-checking testbench must FAIL on the copy. The record layout depends on
the vector family (see the generators under model/); exit 1 when the mutation could not be applied (a layout change)."""
import sys

design, src, dst = sys.argv[1:4]
lines = open(src).read().split("\n")
out, n, hit = [], 0, False
KV = ("kv_attn_", "soc_kv_attn_", "user_project_wrapper_soc_kv")
first = {"audio_pitch": 4, "audio_onset": 3}.get(design)      # 32-bit word files: index of the first input-beat word
flag = {"audio_pitch": 9, "audio_onset": 10}.get(design)      # "a result is expected" bit
for ln in lines:
    body = ln.split("//")[0].strip()
    if body and not hit:
        w = body.split()
        if design.startswith(KV):
            # 40-byte records, header first; a CMD record (word 0 == 01) with word 2 = number of expected output beats (> 2)
            if n >= 1 and int(w[0], 16) == 1 and int(w[2], 16) > 2:
                w[4] = "%02x" % (int(w[4], 16) ^ 1); ln = " ".join(w); hit = True
        elif first is not None:
            if n >= first and int(w[0], 16) >> flag & 1:
                ln = "%08x" % (int(w[0], 16) ^ (1 << 11)); hit = True
        elif design in ("tiny_ai_core", "user_project_wrapper"):
            if n == 1:                                    # record 0 header, record 1 first case: byte 11 = expected class
                w[11] = "%02x" % (int(w[11], 16) ^ 1); ln = " ".join(w); hit = True
        else:
            if n == 1:                                    # 16-byte records: word 13 = expected beat 0
                w[13] = "%02x" % (int(w[13], 16) ^ 1); ln = " ".join(w); hit = True
        n += 1
    out.append(ln)
open(dst, "w").write("\n".join(out))
sys.exit(0 if hit else 1)
