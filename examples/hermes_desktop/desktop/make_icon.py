"""Draw the app icon (a stylised chip: rounded dark square, light die, pins) with the Python stdlib only.
Usage: python3 make_icon.py [out.png] [size]   (default icon.png, 1024; make_app.sh turns it into icon.icns with sips/iconutil)
Docs: examples/hermes_desktop/README.md, docs/HERMES_DESKTOP.md"""
import struct
import sys
import zlib


def render(n=1024):
    bg, die, pin, core = (24, 32, 56), (226, 232, 240), (250, 190, 60), (56, 189, 148)
    r = n * 0.22                       # corner radius of the rounded square
    lo, hi = n * 0.27, n * 0.73        # die
    cl, ch = n * 0.40, n * 0.60        # core
    pw = n * 0.045                     # pin half-width
    rows = []
    for y in range(n):
        row = bytearray([0])
        for x in range(n):
            # rounded-square mask (anti-aliasing not needed at icon scale)
            dx = max(r - x, 0, x - (n - 1 - r)); dy = max(r - y, 0, y - (n - 1 - r))
            if dx * dx + dy * dy > r * r:
                row += bytes((0, 0, 0, 0)); continue
            c = bg
            if lo <= x <= hi and lo <= y <= hi:
                c = die
                if cl <= x <= ch and cl <= y <= ch:
                    c = core
            else:
                for k in (0.36, 0.5, 0.64):
                    p = n * k
                    near_x, near_y = abs(x - p) <= pw, abs(y - p) <= pw
                    if (near_x and (n * 0.17 <= y < lo or hi < y <= n * 0.83)) or \
                       (near_y and (n * 0.17 <= x < lo or hi < x <= n * 0.83)):
                        c = pin
            row += bytes(c) + b"\xff"
        rows.append(bytes(row))
    raw = b"".join(rows)

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", n, n, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b""))


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "icon.png"
    open(out, "wb").write(render(int(sys.argv[2]) if len(sys.argv) > 2 else 1024))
    print("wrote", out)
