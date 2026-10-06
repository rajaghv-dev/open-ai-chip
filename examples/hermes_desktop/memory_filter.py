"""
title: Chip agent memory digest
author: open-ai-chip
description: Open WebUI filter (inlet): appends the agent memory digest (latest notes, last 5 runs) from the local chip tool server to the system prompt of every chat, so the model sees past runs without calling a tool.
version: 1.0
Docs: examples/hermes_desktop/README.md, docs/HERMES_DESKTOP.md, docs/AGENT_CONTEXT.md
This file is the source text installed into Open WebUI by install_prompts.py (Functions API, global filter). It runs
inside Open WebUI, so it uses only the standard library and never raises: if the tool server is down the chat goes on
unchanged. Fetches POST <tool_server_url>/memory_digest (read-only).
"""
import json
import urllib.request

from pydantic import BaseModel

MARK = "[agent memory digest]"


class Filter:
    class Valves(BaseModel):
        tool_server_url: str = "http://127.0.0.1:8770"
        timeout_s: float = 2.0

    def __init__(self):
        self.valves = self.Valves()

    def _digest(self):
        req = urllib.request.Request(self.valves.tool_server_url.rstrip("/") + "/memory_digest", data=b"{}",
                                     method="POST", headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.valves.timeout_s) as r:
            return json.loads(r.read()).get("digest", "")

    def inlet(self, body: dict, __user__: dict = None) -> dict:
        try:
            d = self._digest()
            msgs = body.get("messages") or []
            if not d or any(MARK in str(m.get("content", "")) for m in msgs if m.get("role") == "system"):
                return body
            block = MARK + "\n" + d
            if msgs and msgs[0].get("role") == "system":
                msgs[0]["content"] = str(msgs[0].get("content", "")) + "\n\n" + block
            else:
                msgs.insert(0, {"role": "system", "content": block})
            body["messages"] = msgs
        except Exception:
            pass
        return body
