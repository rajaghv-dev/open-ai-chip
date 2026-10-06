# test_hermes_mcp_bridge.py -- tools/hermes_mcp_bridge.py: openapi -> MCP tool conversion, $ref resolution, curation file
# against the real tool server's operationIds, forwarding to a stub HTTP server, ask_claude policy text. No Docker, no model.
# Run: build/agent/venv/bin/python -m pytest -q tests/tools/test_hermes_mcp_bridge.py
# Docs: docs/HERMES_AGENT_INTEGRATION.md
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "tools"))
import hermes_mcp_bridge as hb  # noqa: E402

SPEC = {
    "paths": {
        "/read_metrics": {"post": {"operationId": "read_metrics", "summary": "s", "description": "orig",
                                   "requestBody": {"content": {"application/json": {"schema": {"$ref": "#/components/schemas/M"}}}}}},
        "/health": {"post": {"operationId": "health", "summary": "Health"}},
        "/claude_task": {"post": {"operationId": "claude_task", "description": "x"}},
        "/job_status": {"post": {"operationId": "job_status", "description": "j"}},
        "/extra": {"post": {"operationId": "extra", "description": "extra tool"}},
    },
    "components": {"schemas": {
        "M": {"title": "M", "type": "object", "required": ["design"],
              "properties": {"design": {"type": "string", "title": "Design"},
                             "keys": {"anyOf": [{"type": "array", "items": {"type": "string"}}, {"type": "null"}], "default": None},
                             "sub": {"$ref": "#/components/schemas/Sub"}}},
        "Sub": {"type": "object", "properties": {"n": {"type": "integer"}}},
    }},
}
CUR = {"include": {"read_metrics": "short", "health": "h"}, "exclude": ["claude_task", "extra"],
       "aliases": {"ask_claude": {"op": "claude_task", "description": "policy"},
                   "claude_status": {"op": "job_status", "description": "st"}}}


def test_resolve_refs_and_simplify():
    t = hb.openapi_to_tools(SPEC, CUR)["read_metrics"]
    s = t["schema"]
    assert s["required"] == ["design"] and "$ref" not in json.dumps(s) and "title" not in json.dumps(s)
    assert s["properties"]["keys"] == {"type": "array", "items": {"type": "string"}}     # Optional collapsed
    assert s["properties"]["sub"]["properties"]["n"]["type"] == "integer"
    assert hb.resolve_refs({"$ref": "#/components/schemas/Sub"}, SPEC)["type"] == "object"


def test_curated_vs_all_and_aliases():
    cur = hb.openapi_to_tools(SPEC, CUR)
    assert set(cur) == {"read_metrics", "health", "ask_claude", "claude_status"}
    assert cur["read_metrics"]["description"] == "short"
    assert cur["ask_claude"]["op"] == "claude_task" and cur["claude_status"]["path"] == "/job_status"
    assert hb.openapi_to_tools(SPEC, dict(CUR, exclude=["claude_task"]), all_tools=True).keys() >= {"extra", "job_status"}
    assert "claude_task" not in hb.openapi_to_tools(SPEC, CUR, all_tools=True)
    assert hb.openapi_to_tools(SPEC, CUR)["health"]["schema"] == {"type": "object", "properties": {}}


def test_curation_file_valid():
    cur = hb.load_curation()
    assert len(cur["include"]) >= 25
    for name, d in cur["include"].items():
        assert "USE WHEN" in d and "EXAMPLE" in d, name
        assert d.isascii() and len(d) < 1200, name
    assert not set(cur["include"]) & set(cur["exclude"])


def test_ask_claude_policy():
    d = hb.load_curation()["aliases"]["ask_claude"]
    assert d["op"] == "claude_task"
    t = d["description"]
    for word in ("ONLY", "asks for Claude", "inconclusive", "confirm", "edit tools denied", "claude_status"):
        assert word in t
    assert hb.load_curation()["aliases"]["claude_status"]["op"] == "job_status"


@pytest.fixture(scope="module")
def real_spec():
    """The real openapi.json, from the in-process FastAPI app (no server started)."""
    ts = os.path.join(REPO, "examples", "hermes_desktop", "tool_server")
    sys.path.insert(0, ts)
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location("chip_tool_server_for_bridge", os.path.join(ts, "tool_server.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except ImportError as e:
        pytest.skip("tool server deps missing: %s" % e)
    return mod.app.openapi()


def test_curation_references_existing_operations(real_spec):
    cur = hb.load_curation()
    ops = {o["operationId"] for m in real_spec["paths"].values() for o in m.values()}
    for name in list(cur["include"]) + cur["exclude"] + [a["op"] for a in cur["aliases"].values()]:
        assert name in ops, name
    tools = hb.openapi_to_tools(real_spec, cur)
    assert {"search_docs", "read_metrics", "run_make", "ask_claude", "claude_status"} <= set(tools)
    assert len(tools) >= 25
    assert "claude_task" not in hb.openapi_to_tools(real_spec, cur, all_tools=True)
    for n, t in hb.openapi_to_tools(real_spec, cur, all_tools=True).items():
        assert t["schema"]["type"] == "object" and "$ref" not in json.dumps(t["schema"]), n


class _Stub(BaseHTTPRequestHandler):
    seen = []

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        _Stub.seen.append((self.path, body))
        if self.path == "/boom":
            out, code = {"detail": "nope"}, 422
        elif self.path == "/err":
            out, code = {"error": "bad design"}, 200
        else:
            out, code = {"echo": body, "png_url": "http://127.0.0.1:1/img/a.png", "markdown": "![a](http://127.0.0.1:1/img/a.png)"}, 200
        data = json.dumps(out).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *a):
        pass


@pytest.fixture()
def stub(monkeypatch):
    srv = HTTPServer(("127.0.0.1", 0), _Stub)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv("CHIP_TOOLS_PORT", str(srv.server_port))
    _Stub.seen.clear()
    yield srv
    srv.shutdown()


def test_forward_markdown_and_errors(stub):
    ok = {"method": "POST", "path": "/klayout_view"}
    text, err = hb.forward(ok, {"design": "x"})
    assert not err and _Stub.seen == [("/klayout_view", {"design": "x"})]
    assert '"png_url"' in text and text.rstrip().endswith("![a](http://127.0.0.1:1/img/a.png)")
    assert hb.forward({"method": "POST", "path": "/err"}, {})[1] is True
    assert hb.forward({"method": "POST", "path": "/boom"}, {})[1] is True


def test_forward_alias_uses_target_path(stub):
    t = hb.openapi_to_tools(SPEC, CUR)["ask_claude"]
    hb.forward(t, {"instructions": "hi"})
    assert _Stub.seen == [("/claude_task", {"instructions": "hi"})]


def test_forward_server_down(monkeypatch):
    monkeypatch.setenv("CHIP_TOOLS_PORT", "1")
    text, err = hb.forward({"method": "POST", "path": "/x"}, {})
    assert err and "tool server call failed" in text
    assert hb.server_up() is False
