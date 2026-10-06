#!/usr/bin/env python3
"""LiveBackend: ViewBackend client for the KLayout desktop window (option B).

Talks JSON lines over a localhost TCP socket to klayout_macro/agent_bridge.py, which runs inside the
KLayout GUI and does all window work on the GUI thread. Start the window with start_live.sh.

Env: KLAYOUT_AGENT_PORT (default 8765), KLAYOUT_AGENT_TOKEN (optional shared secret),
     KLAYOUT_AGENT_TIMEOUT (seconds per request, default 60).
Docs: examples/hermes_klayout_gui/README_live.md, docs/HERMES_AGENT.md
"""
import json
import os
import socket

from view_api import ViewBackend, ViewError

HOST = "127.0.0.1"
START_HINT = "start it with: bash examples/hermes_klayout_gui/start_live.sh [design]"


class LiveError(ConnectionError):
    pass


# Wire format shared with klayout_macro/agent_bridge.py: one compact ASCII JSON object per line (newline = frame end).
def encode_request(rid, method, params, token=None):
    req = {"id": rid, "method": method, "params": params}
    if token:
        req["token"] = token
    return (json.dumps(req, separators=(",", ":")) + "\n").encode("ascii")


# A bridge reply is either {"id", "error"} (request rejected: bad token, disallowed method) or {"id", "result"}.
# Rejections are mapped to the same {ok: False} shape as backend errors; only a malformed frame raises LiveError.
def decode_response(line):
    """-> (id, result_dict). Raises LiveError for protocol-level errors."""
    try:
        r = json.loads(line)
    except ValueError as e:
        raise LiveError("bad reply from KLayout bridge: %s" % e)
    if not isinstance(r, dict):
        raise LiveError("bad reply from KLayout bridge")
    if "error" in r:
        return r.get("id"), {"ok": False, "error": str(r["error"])}
    res = r.get("result")
    if not isinstance(res, dict):
        raise LiveError("bridge reply has no result")
    return r.get("id"), res


class LiveBackend(ViewBackend):
    name = "live"

    def __init__(self, port=None, host=HOST, token=None, timeout=None, connect=True):
        self.host = host
        self.port = int(port or os.environ.get("KLAYOUT_AGENT_PORT", "8765"))
        self.token = token if token is not None else os.environ.get("KLAYOUT_AGENT_TOKEN") or None
        self.timeout = float(timeout or os.environ.get("KLAYOUT_AGENT_TIMEOUT", "60"))
        self._sock = None
        self._file = None
        self._id = 0
        if connect:
            self._connect()
            r = self._call("ping", {})
            if not r.get("ok"):
                raise LiveError("KLayout bridge refused ping: %s" % r.get("error"))

    # ---- transport
    def _connect(self):
        try:
            s = socket.create_connection((self.host, self.port), timeout=3)
        except OSError as e:
            raise LiveError("KLayout is not running or the bridge is not listening on %s:%d (%s); %s"
                            % (self.host, self.port, e, START_HINT))
        s.settimeout(self.timeout)
        self._sock, self._file = s, s.makefile("rb")

    def close(self):
        for o in (self._file, self._sock):
            try:
                if o:
                    o.close()
            except OSError:
                pass
        self._sock = self._file = None

    def _call(self, method, params):
        for attempt in (0, 1):             # one transparent reconnect if the window was restarted
            if self._sock is None:
                self._connect()
            self._id += 1
            try:
                self._sock.sendall(encode_request(self._id, method, params, self.token))
                line = self._file.readline()
                if not line:
                    raise OSError("connection closed")
            except socket.timeout:
                self.close()
                raise LiveError("KLayout did not answer within %.0f s (modal dialog open in the window?)" % self.timeout)
            except OSError as e:
                self.close()
                if attempt == 0:
                    continue
                raise LiveError("lost the KLayout bridge (%s); %s" % (e, START_HINT))
            rid, res = decode_response(line)
            if rid != self._id:
                raise LiveError("reply id %r does not match request %r" % (rid, self._id))
            return res

    def _rpc(self, method, **params):
        r = self._call(method, params)
        if r.get("ok") is False:
            raise ViewError(r.get("error", "unknown bridge error"))
        return r

    # ---- ViewBackend implementation (each returns the bridge dict)
    def _open_design(self, design):
        return self._rpc("open_design", design=design)

    def _zoom_to(self, target):
        return self._rpc("zoom_to", target=target)

    def _show_layers(self, layers, only):
        return self._rpc("show_layers", layers=layers, only=only)

    def _highlight_drc(self, design, max_items, demo_markers):
        return self._rpc("highlight_drc", design=design, max_items=max_items, demo_markers=demo_markers)

    def _snapshot(self, path, width, height):
        return self._rpc("snapshot", path=path, width=width, height=height)

    def _state(self):
        return self._rpc("state")

    def _measure(self, a, b):
        return ViewBackend._measure(self, a, b)      # pure geometry, no round trip needed

    def quit_window(self):
        """Admin only (needs KLAYOUT_AGENT_ALLOW_QUIT=1 in the window's environment). Not an agent tool."""
        return self._call("quit", {})
