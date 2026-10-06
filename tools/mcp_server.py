#!/usr/bin/env python3
"""MCP stdio server exposing tools/eda_tools.py (read-only chip-result inspection).

Run with the venv python:  build/agent/venv/bin/python tools/mcp_server.py
Written for the mcp 2.x low-level API (handlers passed to Server(...)); each tool uses the exact
schema from eda_tools.TOOLS and dispatches to eda_tools.call.
Docs: tools/README.md, docs/HERMES_AGENT.md
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import eda_tools  # noqa: E402

import mcp.types as types  # noqa: E402
from mcp.server.lowlevel import Server  # noqa: E402
from mcp.server.stdio import stdio_server  # noqa: E402


async def on_list_tools(ctx, params):
    return types.ListToolsResult(tools=[
        types.Tool(name=t["function"]["name"], description=t["function"]["description"],
                   input_schema=t["function"]["parameters"]) for t in eda_tools.TOOLS])


# is_error mirrors eda_tools' {"error": ...} contract so MCP clients can tell a failed call from an empty result.
async def on_call_tool(ctx, params):
    res = eda_tools.call(params.name, params.arguments or {})
    return types.CallToolResult(content=[types.TextContent(type="text", text=json.dumps(res, indent=1))],
                                is_error="error" in res)


server = Server("open-ai-chip-eda", on_list_tools=on_list_tools, on_call_tool=on_call_tool)


async def main():
    async with stdio_server() as (r, w):
        await server.run(r, w, server.create_initialization_options())


if __name__ == "__main__":
    import asyncio
    asyncio.run(main())
