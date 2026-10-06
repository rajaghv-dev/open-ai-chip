"""
title: Chip agent memory digest
author: open-ai-chip
description: Open WebUI filter (inlet): appends the agent memory digest (latest notes, last 5 runs) from the local chip tool server to the system prompt of every chat, so the model sees past runs without calling a tool.
version: 1.0
Docs: examples/hermes_desktop/README.md, docs/HERMES_DESKTOP.md, docs/AGENT_CONTEXT.md
Proof: after injecting, it tells the tool server what it injected (POST /proof_record part "memory": counts, sha256, first lines)
so show_context and the receipt can show it; best effort, never raises.
This file is the source text installed into Open WebUI by install_prompts.py (Functions API, global filter). It runs
inside Open WebUI, so it uses only the standard library and never raises: if the tool server is down the chat goes on
unchanged. Fetches POST <tool_server_url>/memory_digest (read-only).
"""
import hashlib
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

    def _record(self, body: dict, d: str, metadata=None):
        """Tell the tool server what was injected (proof_record); failures are ignored."""
        try:
            chat_id = (metadata or {}).get("chat_id") or (body.get("metadata") or {}).get("chat_id") or body.get("chat_id")
            head, _, runs = d.partition("LAST RUNS:")
            notes = [l for l in head.splitlines() if l.startswith("- ")]
            run_l = [l for l in runs.splitlines() if l.startswith("- ")]
            data = {"notes": len(notes), "runs": len(run_l), "entries": len(notes) + len(run_l), "recorded": True,
                    "sha256": hashlib.sha256(d.encode("utf-8")).hexdigest(), "chars": len(d), "head": d.splitlines()[:8]}
            req = urllib.request.Request(self.valves.tool_server_url.rstrip("/") + "/proof_record",
                                         data=json.dumps({"chat_id": chat_id, "part": "memory", "data": data}).encode(),
                                         method="POST", headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=self.valves.timeout_s).read()
        except Exception:
            pass

    def inlet(self, body: dict, __user__: dict = None, __metadata__: dict = None) -> dict:
        try:
            d = self._digest()
            msgs = body.get("messages") or []
            if not d or any(MARK in str(m.get("content", "")) for m in msgs if m.get("role") == "system"):
                return body
            self._record(body, d, __metadata__)
            block = MARK + "\n" + d
            if msgs and msgs[0].get("role") == "system":
                msgs[0]["content"] = str(msgs[0].get("content", "")) + "\n\n" + block
            else:
                msgs.insert(0, {"role": "system", "content": block})
            body["messages"] = msgs
        except Exception:
            pass
        return body
