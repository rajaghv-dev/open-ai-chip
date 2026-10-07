#!/usr/bin/env python3
"""MCP stdio bridge: exposes the chip tool server (examples/hermes_desktop/tool_server, 127.0.0.1:8770) as MCP tools.

Run with the venv python:  build/agent/venv/bin/python tools/hermes_mcp_bridge.py [--all]
Tools are generated from the tool server's /openapi.json (name = operationId, description, input schema with $refs
resolved and Optional anyOf collapsed). By default only the curated set in tools/hermes_tools.json is listed (short
USE WHEN / DO NOT USE WHEN / EXAMPLE descriptions for a small local model); --all (or CHIP_BRIDGE_ALL=1) lists every
operation except the file's "exclude" list. `ask_claude` forwards to claude_task and `claude_status` to job_status.
Calls are POSTed to http://127.0.0.1:${CHIP_TOOLS_PORT:-8770}. If the server is down the bridge starts it (background,
loopback only, pid file build/agent/hermes_bridge_toolserver.pid) and stops only a server it started itself.
The server's confirm gate (run_make, run_experiment, whatif_run, claude_task) is untouched: nothing starts without it.
Results are text: JSON, then the `markdown` field on its own when present (image URLs are kept as given).
Docs: docs/HERMES_AGENT_INTEGRATION.md
"""
import atexit
import json
import os
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request

REPO = os.path.abspath(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
CURATION = os.path.join(REPO, "tools", "hermes_tools.json")
PID_FILE = os.path.join(REPO, "build", "agent", "hermes_bridge_toolserver.pid")
SERVER_PY = os.path.join(REPO, "examples", "hermes_desktop", "tool_server", "tool_server.py")
SERVER_LOG = os.path.join(REPO, "build", "agent", "hermes_bridge_toolserver.log")
HOST = "127.0.0.1"
CALL_TIMEOUT_S = 300


def port():
    return int(os.environ.get("CHIP_TOOLS_PORT", "8770"))


def base_url():
    return "http://%s:%d" % (HOST, port())


# ---------------------------------------------------------------- openapi -> MCP tool specs
def resolve_refs(node, root, _seen=()):
    """Inline every {"$ref": "#/components/schemas/X"}; a cycle is cut to a plain object."""
    if isinstance(node, list):
        return [resolve_refs(n, root, _seen) for n in node]
    if not isinstance(node, dict):
        return node
    if "$ref" in node:
        ref = node["$ref"]
        if ref in _seen:
            return {"type": "object"}
        target = root
        for part in ref.lstrip("#/").split("/"):
            target = target[part]
        extra = {k: v for k, v in node.items() if k != "$ref"}
        out = resolve_refs(target, root, _seen + (ref,))
        out = dict(out) if isinstance(out, dict) else out
        if isinstance(out, dict):
            out.update(resolve_refs(extra, root, _seen))
        return out
    return {k: resolve_refs(v, root, _seen) for k, v in node.items()}


def simplify(schema):
    """Small local models handle plain types better: anyOf [X, null] -> X; drop titles and $defs."""
    if isinstance(schema, list):
        return [simplify(s) for s in schema]
    if not isinstance(schema, dict):
        return schema
    s = {k: simplify(v) for k, v in schema.items() if k not in ("title", "$defs")}
    if "default" in s and s["default"] is None:
        del s["default"]
    for key in ("anyOf", "oneOf"):
        if key in s:
            alts = [a for a in s[key] if a.get("type") != "null"]
            if len(alts) == 1:
                rest = {k: v for k, v in s.items() if k != key}
                alts[0].update({k: v for k, v in rest.items() if k not in alts[0]})
                return alts[0]
            s[key] = alts
    return s


def input_schema(op, root):
    schema = {"type": "object", "properties": {}}
    body = ((op.get("requestBody") or {}).get("content") or {}).get("application/json", {}).get("schema")
    if body:
        schema = simplify(resolve_refs(body, root))
    for p in op.get("parameters", []):          # query/path parameters, if an endpoint ever has them
        p = resolve_refs(p, root)
        props = schema.setdefault("properties", {})
        props[p["name"]] = simplify(p.get("schema", {"type": "string"}))
        if p.get("required"):
            schema.setdefault("required", []).append(p["name"])
    schema.setdefault("type", "object")
    schema.setdefault("properties", {})
    return schema


def load_curation(path=CURATION):
    with open(path) as f:
        return json.load(f)


def openapi_to_tools(spec, curation, all_tools=False):
    """-> {tool_name: {"op": operationId, "path": str, "method": str, "description": str, "schema": dict}}."""
    ops = {}
    for path, methods in spec.get("paths", {}).items():
        for method, op in methods.items():
            if method.lower() not in ("post", "get") or "operationId" not in op:
                continue
            desc = (op.get("description") or op.get("summary") or op["operationId"]).strip()
            ops[op["operationId"]] = {"op": op["operationId"], "path": path, "method": method.upper(),
                                      "description": desc, "schema": input_schema(op, spec)}
    include = curation.get("include", {})
    exclude = set(curation.get("exclude", []))
    tools = {}
    for name, t in ops.items():
        if name in exclude:
            continue
        if name in include:
            tools[name] = dict(t, description=include[name])
        elif all_tools:
            tools[name] = t
    for alias, a in curation.get("aliases", {}).items():
        if a["op"] in ops:
            tools[alias] = dict(ops[a["op"]], description=a["description"])
    return tools


# ---------------------------------------------------------------- HTTP to the tool server
def http_json(method, path, payload=None, timeout=CALL_TIMEOUT_S):
    data = None if payload is None else json.dumps(payload).encode()
    req = urllib.request.Request(base_url() + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"null")
    except urllib.error.HTTPError as e:
        body = e.read()
        try:
            return e.code, json.loads(body)
        except ValueError:
            return e.code, {"error": "HTTP %d: %s" % (e.code, body[:300].decode("utf-8", "replace"))}


def forward(tool, arguments, timeout=CALL_TIMEOUT_S):
    """-> (text, is_error). Text is the JSON result, then the markdown field alone when present."""
    body = arguments if tool["method"] == "POST" else None
    try:
        status, res = http_json(tool["method"], tool["path"], body, timeout)
    except urllib.error.URLError as e:          # server gone (restarted after a code update, or crashed): start it, retry once
        try:
            ensure_server()
            status, res = http_json(tool["method"], tool["path"], body, timeout)
        except Exception as e2:  # noqa: BLE001
            return "tool server call failed: %s: %s (first: %s)" % (type(e2).__name__, e2, e), True
    except Exception as e:  # noqa: BLE001
        return "tool server call failed: %s: %s" % (type(e).__name__, e), True
    text = json.dumps(res, indent=1)
    if isinstance(res, dict) and isinstance(res.get("markdown"), str) and res["markdown"]:
        text += "\n\nmarkdown (paste verbatim into the answer):\n" + res["markdown"]
    is_error = status >= 400 or (isinstance(res, dict) and "error" in res)
    return text, is_error


# ---------------------------------------------------------------- start / stop the tool server
_started_pid = None


def server_up(timeout=1.5):
    try:
        with urllib.request.urlopen(base_url() + "/openapi.json", timeout=timeout) as r:
            return r.status == 200
    except Exception:  # noqa: BLE001
        return False


def ensure_server(wait_s=60):
    """Start the tool server if it is not answering; remember the pid so only this server is stopped at exit."""
    global _started_pid
    if server_up():
        return False
    py = os.path.join(REPO, "build", "agent", "venv", "bin", "python")
    py = py if os.path.exists(py) else sys.executable
    os.makedirs(os.path.dirname(PID_FILE), exist_ok=True)
    env = dict(os.environ, CHIP_TOOLS_PORT=str(port()))
    log = open(SERVER_LOG, "ab")
    p = subprocess.Popen([py, SERVER_PY], cwd=REPO, env=env, stdout=log, stderr=log, stdin=subprocess.DEVNULL,
                         start_new_session=True)
    _started_pid = p.pid
    with open(PID_FILE, "w") as f:
        f.write(str(p.pid))
    atexit.register(stop_server)
    for _ in range(wait_s * 4):
        if server_up():
            return True
        if p.poll() is not None:
            break
        time.sleep(0.25)
    raise RuntimeError("tool server did not come up; see build/agent/hermes_bridge_toolserver.log")


def stop_server():
    """Kill by pid only what this process started."""
    global _started_pid
    pid, _started_pid = _started_pid, None
    if not pid:
        return
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError:
        pass
    try:
        os.remove(PID_FILE)
    except OSError:
        pass


# ---------------------------------------------------------------- MCP server
def build_server(all_tools=False):
    import mcp.types as types
    from mcp.server.lowlevel import Server

    state = {"tools": None}

    def tools():
        if state["tools"] is None:
            ensure_server()
            _, spec = http_json("GET", "/openapi.json", timeout=30)
            state["tools"] = openapi_to_tools(spec, load_curation(), all_tools)
        return state["tools"]

    async def on_list_tools(ctx, params):
        return types.ListToolsResult(tools=[
            types.Tool(name=n, description=t["description"], input_schema=t["schema"]) for n, t in tools().items()])

    async def on_call_tool(ctx, params):
        import asyncio
        t = tools().get(params.name)
        if t is None:
            return types.CallToolResult(content=[types.TextContent(type="text", text="unknown tool %r" % params.name)],
                                        is_error=True)
        text, err = await asyncio.get_running_loop().run_in_executor(None, forward, t, params.arguments or {})
        return types.CallToolResult(content=[types.TextContent(type="text", text=text)], is_error=err)

    return Server("open-ai-chip-tools", on_list_tools=on_list_tools, on_call_tool=on_call_tool)


async def main(all_tools=False):
    from mcp.server.stdio import stdio_server
    server = build_server(all_tools)
    async with stdio_server() as (r, w):
        await server.run(r, w, server.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    signal.signal(signal.SIGTERM, lambda *a: sys.exit(0))      # run atexit (stop_server) on SIGTERM
    asyncio.run(main("--all" in sys.argv[1:] or os.environ.get("CHIP_BRIDGE_ALL") == "1"))
