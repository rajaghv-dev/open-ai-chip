#!/usr/bin/env python3
"""Magic bridge client and launcher (read-only view of a real Magic window).

Magic runs in the LibreLane container and draws on XQuartz (same recipe as scripts/gui/open_gui.sh); bridge.tcl runs
inside Magic's Tcl interpreter and serves an allow-list of view commands over a TCP socket published only as
127.0.0.1:<port>. This module has: the wire protocol (encode_request / decode_reply), the layer-name map, the Docker
command, MagicClient (one method per bridge command), and window_png (xwd of the Magic window on the host -> PNG).
CLI: python3 client.py start <design> [port]   (foreground; Ctrl-C or closing stdin stops Magic)
Env: MAGIC_BRIDGE_PORT (8766), MAGIC_BRIDGE_TOKEN (optional), GUI_DISPLAY (container DISPLAY, default 192.168.5.2:0
     on macOS), DOCKER_HOST (default the osl Colima socket), MAGIC_BRIDGE_TIMEOUT (seconds per request, 60).
Docs: examples/hermes_desktop/magic_bridge/README.md, docs/HERMES_DESKTOP.md
"""
import json
import os
import re
import socket
import struct
import subprocess
import sys
import zlib

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
HOST = "127.0.0.1"
DEFAULT_PORT = 8766
ALLOWED = ("PING", "LOAD", "VIEW", "FULL", "SEE", "DRC", "FIND", "MEASURE", "PLOT", "STATE", "QUIT")
FORBIDDEN_WORDS = ("save", "writeall", "gds write", "cif write", "flush", "extract", "exec", "shell", "source", "eval")
WORD_RE = re.compile(r"^[A-Za-z0-9_./,*<>:+=@\[\]-]{1,250}$")

# Chat-level layer names -> Magic sky130A layer names (see `tech layers` in Magic).
LAYER_MAP = {"met1": "m1", "met2": "m2", "met3": "m3", "met4": "m4", "met5": "m5", "li1": "li", "li": "li",
             "mcon": "viali", "via": "via1", "via1": "via1", "via2": "via2", "via3": "via3", "via4": "via4",
             "poly": "poly", "diff": "ndiff,pdiff", "nwell": "nwell", "labels": "labels", "errors": "errors",
             "m1": "m1", "m2": "m2", "m3": "m3", "m4": "m4", "m5": "m5"}


class MagicError(Exception):
    pass


def magic_layers(layers):
    """list or 'met1, met2' -> 'm1,m2' (comma list for Magic's `see`). Unknown names raise MagicError."""
    if isinstance(layers, str):
        layers = [x for x in re.split(r"[,+;\s]+|\band\b", layers) if x]
    if not isinstance(layers, (list, tuple)) or not layers:
        raise MagicError("layers must be a non-empty list such as [\"met1\", \"met2\"]")
    out = []
    for x in layers:
        k = str(x).strip().lower().replace("/drawing", "").replace(" drawing", "")
        if k not in LAYER_MAP:
            raise MagicError("unknown layer %r; use one of: %s" % (x, ", ".join(sorted(k for k in LAYER_MAP if k[0] != "m" or k.startswith("met")))))
        for m in LAYER_MAP[k].split(","):
            if m not in out:
                out.append(m)
    return ",".join(out)


# ------------------------------------------------------------------ protocol (text, one line each way)
def encode_request(cmd, *args, token=None):
    """-> bytes. Raises MagicError for a command that is not on the allow-list or an argument with unsafe characters."""
    cmd = str(cmd).upper()
    if cmd not in ALLOWED:
        raise MagicError("command %r is not allowed; allowed: %s" % (cmd, ", ".join(ALLOWED)))
    words = [cmd] + [_fmt(a) for a in args]
    for w in words:
        if not WORD_RE.match(w):
            raise MagicError("unsafe or empty argument %r" % (w,))
    if token:
        words.insert(0, token)
    return (" ".join(words) + "\n").encode("ascii")


def _fmt(a):
    if isinstance(a, bool):
        raise MagicError("bad argument %r" % (a,))
    if isinstance(a, float):
        return repr(round(a, 4))
    return str(a)


def decode_reply(line):
    """'OK text' / 'ERR text' (bytes or str) -> (ok, text). The Tcl side escaped newlines as backslash-n."""
    if isinstance(line, bytes):
        line = line.decode("utf-8", "replace")
    line = line.rstrip("\r\n")
    if line.startswith("OK"):
        return True, line[2:].strip().replace("\\n", "\n")
    if line.startswith("ERR"):
        return False, line[3:].strip().replace("\\n", "\n")
    raise MagicError("bad reply from the Magic bridge: %r" % line[:120])


def parse_kv(text):
    """'a=1 b=2 3 4 5 6' -> {'a': '1', 'b': '2 3 4 5 6'} (a value runs until the next key=)."""
    d, key = {}, None
    for tok in text.split(" "):
        m = re.match(r"^([a-z_]+)=(.*)$", tok)
        if m:
            key = m.group(1)
            d[key] = m.group(2)
        elif key:
            d[key] = (d[key] + " " + tok).strip()
    return d


def nums(s):
    return [float(x) for x in re.findall(r"-?\d+(?:\.\d+)?(?:e-?\d+)?", s or "")]


# ------------------------------------------------------------------ launcher
def docker_env():
    env = dict(os.environ)
    if sys.platform == "darwin":
        env.setdefault("DOCKER_HOST", "unix://" + os.path.join(os.path.expanduser("~"), ".colima", "osl", "docker.sock"))
    return env


def container_name(port):
    return "chip_magic_%d" % int(port)


def docker_command(port, token=None, interactive=True):
    """argv of `docker run ... magic` exactly like scripts/gui/open_gui.sh magic, plus the published bridge port."""
    image = None
    with open(os.path.join(REPO, "versions.lock")) as f:
        for l in f:
            if l.startswith("LIBRELANE_IMAGE="):
                image = l.split("=", 1)[1].split("#")[0].strip()
    if not image:
        raise MagicError("LIBRELANE_IMAGE missing in versions.lock")
    home = os.path.expanduser("~")
    pdk_root = os.environ.get("PDK_ROOT", os.path.join(home, ".volare"))
    tech = os.path.join(pdk_root, "sky130A", "libs.tech", "magic")
    port = int(port)
    cmd = ["docker", "run", "--rm", "-i", "--name", container_name(port), "-p", "127.0.0.1:%d:%d" % (port, port),
           "-v", "%s:%s" % (home, home), "-v", "%s:%s" % (REPO, REPO), "-w", REPO,
           "-e", "PDK_ROOT=" + pdk_root, "-e", "PDK=sky130A", "-e", "MAGIC_BRIDGE_PORT=%d" % port,
           "-e", "MAGIC_BRIDGE_REPO=" + REPO]
    if token:
        cmd += ["-e", "MAGIC_BRIDGE_TOKEN=" + token]
    if sys.platform == "darwin":
        cmd += ["-e", "DISPLAY=" + os.environ.get("GUI_DISPLAY", "192.168.5.2:0")]
    else:
        cmd += ["-e", "DISPLAY=" + os.environ.get("GUI_DISPLAY", os.environ.get("DISPLAY", ":0")),
                "-v", "/tmp/.X11-unix:/tmp/.X11-unix"]
    cmd += [image, "magic", "-d", "XR", "-noconsole", "-T", os.path.join(tech, "sky130A.tech"),
            "-rcfile", os.path.join(tech, "sky130A.magicrc")]
    return cmd


def bridge_source_line():
    return "source %s\n" % os.path.join(HERE, "bridge.tcl")


def host_display():
    return ":0" if sys.platform == "darwin" else os.environ.get("DISPLAY", ":0")


# ------------------------------------------------------------------ client
class MagicClient:
    def __init__(self, port=None, token=None, timeout=None):
        self.port = int(port or os.environ.get("MAGIC_BRIDGE_PORT", DEFAULT_PORT))
        self.token = token if token is not None else os.environ.get("MAGIC_BRIDGE_TOKEN") or None
        self.timeout = float(timeout or os.environ.get("MAGIC_BRIDGE_TIMEOUT", "60"))

    def call(self, cmd, *args, timeout=None):
        """One request on a fresh connection -> (ok, text). Raises MagicError when nothing listens or on timeout."""
        data = encode_request(cmd, *args, token=self.token)
        try:
            s = socket.create_connection((HOST, self.port), timeout=3)
        except OSError as e:
            raise MagicError("Magic bridge is not listening on %s:%d (%s); start it with gui_start {tool: magic}"
                             % (HOST, self.port, e))
        try:
            s.settimeout(timeout or self.timeout)
            s.sendall(data)
            buf = b""
            while not buf.endswith(b"\n"):
                chunk = s.recv(65536)
                if not chunk:
                    break
                buf += chunk
                if len(buf) > 1 << 20:
                    raise MagicError("reply too large")
            if not buf:
                raise MagicError("Magic closed the connection without a reply")
            return decode_reply(buf)
        except socket.timeout:
            raise MagicError("Magic did not answer within %.0f s" % (timeout or self.timeout))
        except OSError as e:
            raise MagicError("lost the Magic bridge: %s" % e)
        finally:
            s.close()

    def ping(self):
        return self.call("PING", timeout=5)

    def request(self, cmd, *args, timeout=None):
        """-> dict {ok, ...parsed key=value fields, text}; never raises."""
        try:
            ok, text = self.call(cmd, *args, timeout=timeout)
        except MagicError as e:
            return {"ok": False, "error": str(e)}
        if not ok:
            return {"ok": False, "error": text}
        d = {"ok": True, "text": text}
        d.update(parse_kv(text))
        return d


# ------------------------------------------------------------------ window snapshot (xwd -> PNG, no dependencies)
def find_window_id(name="layout1"):
    out = subprocess.run(["/opt/X11/bin/xwininfo", "-root", "-tree"] if os.path.exists("/opt/X11/bin/xwininfo")
                         else ["xwininfo", "-root", "-tree"], capture_output=True, text=True, timeout=10,
                         env=dict(os.environ, DISPLAY=host_display())).stdout
    for l in out.splitlines():
        m = re.match(r'\s*(0x[0-9a-f]+) "%s"' % re.escape(name), l)
        if m:
            return m.group(1)
    return None


def xwd_to_png(xwd_bytes):
    """Parse a 24/32 bpp ZPixmap xwd dump and return (png_bytes, width, height)."""
    h = struct.unpack(">25I", xwd_bytes[:100])
    hdr, fmt, depth, w, ht, bo, bpp, bpl, ncol = h[0], h[2], h[3], h[4], h[5], h[7], h[11], h[12], h[19]
    rm, gm, bm = h[14], h[15], h[16]
    if fmt != 2 or bpp not in (24, 32):
        raise MagicError("unsupported xwd format (need ZPixmap 24/32 bpp)")
    px = xwd_bytes[hdr + ncol * 12:]
    bpx = bpp // 8
    raw = bytearray()
    shifts = [(m & -m).bit_length() - 1 for m in (rm, gm, bm)]
    for y in range(ht):
        row = px[y * bpl:y * bpl + w * bpx]
        out = bytearray(w * 3)
        for ci, sh in enumerate(shifts):
            # little-endian byte order: channel byte index = shift/8; big-endian: bpx-1-shift/8
            idx = sh // 8 if bo == 0 else bpx - 1 - sh // 8
            out[ci::3] = row[idx::bpx][:w]
        raw += b"\x00" + out

    def chunk(t, d):
        c = struct.pack(">I", len(d)) + t + d
        return c + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, ht, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(bytes(raw), 6)) + chunk(b"IEND", b""))
    return png, w, ht


def window_png(out_path, name="layout1"):
    """xwd of the Magic window on the host display -> PNG at out_path. Returns (w, h)."""
    wid = find_window_id(name)
    if not wid:
        raise MagicError("no Magic window named %r on the X display %s (is XQuartz running and the window open?)"
                         % (name, host_display()))
    xwd = "/opt/X11/bin/xwd" if os.path.exists("/opt/X11/bin/xwd") else "xwd"
    r = subprocess.run([xwd, "-id", wid, "-silent"], capture_output=True, timeout=20,
                       env=dict(os.environ, DISPLAY=host_display()))
    if r.returncode != 0 or not r.stdout:
        raise MagicError("xwd failed: %s" % r.stderr.decode("utf-8", "replace")[:200])
    png, w, h = xwd_to_png(r.stdout)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(png)
    return w, h


def pnm_to_png(pnm_path, out_path):
    """Binary PPM (P6, 8 bit) from Magic's `plot pnm` -> PNG at out_path. Returns (w, h)."""
    with open(pnm_path, "rb") as f:
        data = f.read()
    m = re.match(rb"P6\s+(?:#[^\n]*\n\s*)*(\d+)\s+(\d+)\s+(\d+)\s", data)
    if not m or int(m.group(3)) != 255:
        raise MagicError("unsupported pnm from Magic")
    w, h = int(m.group(1)), int(m.group(2))
    px = data[m.end():m.end() + w * h * 3]
    raw = b"".join(b"\x00" + px[y * w * 3:(y + 1) * w * 3] for y in range(h))

    def chunk(t, d):
        return struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)
    png = (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
           + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "wb") as f:
        f.write(png)
    return w, h


def main(argv):
    if len(argv) >= 2 and argv[0] == "start":
        from_design = argv[1]
        port = int(argv[2]) if len(argv) > 2 else int(os.environ.get("MAGIC_BRIDGE_PORT", DEFAULT_PORT))
        sys.path.insert(0, os.path.join(REPO, "examples", "hermes_klayout_gui"))
        sys.path.insert(0, os.path.join(REPO, "tools"))
        import view_api  # noqa: E402
        gds = view_api.find_gds(from_design)
        top = view_api.top_cell_name(from_design)
        p = subprocess.Popen(docker_command(port, os.environ.get("MAGIC_BRIDGE_TOKEN") or None), stdin=subprocess.PIPE,
                             env=docker_env())
        p.stdin.write((bridge_source_line()).encode())
        p.stdin.flush()
        import time
        c = MagicClient(port)
        for _ in range(60):
            if c.request("PING", timeout=2).get("ok"):
                break
            time.sleep(0.5)
        print(json.dumps(c.request("LOAD", gds, top, timeout=120)))
        print("Magic bridge on 127.0.0.1:%d; Ctrl-C or close the window (QUIT) to stop." % port)
        try:
            p.wait()
        except KeyboardInterrupt:
            c.request("QUIT")
            subprocess.run(["docker", "kill", container_name(port)], env=docker_env(), capture_output=True)
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
