"""
title: Chip agent receipt
author: open-ai-chip
description: Open WebUI filter: appends a code-built receipt under every answer (model and digest, local endpoint, repo commit, tools called with the files and sha256 they read, context used, speed) and adds the repo passages as citations.
version: 1.0
Docs: examples/hermes_desktop/README.md, docs/HERMES_DESKTOP.md (section "Proof: local, repo, context")
Tests: tests/tools/test_proof.py
This file is the source text installed into Open WebUI by install_prompts.py (Functions API, global filter). It runs inside
Open WebUI, so it uses only the standard library and never raises: if the tool server is down the reply is unchanged.
  inlet : records the turn start with the tool server (POST /proof_record: user message head, time) and strips earlier receipts
          from the history so the model never sees or imitates them.
  outlet: records the system prompt Open WebUI actually used (__metadata__["system_prompt"], set after the inlet filters ran) and
          asks the tool server for the turn's data (POST /proof_turn: call log with file sha256s, context, model digest), builds
          the receipt with build_receipt() (plain code, the model writes none of it) and appends it to the assistant message;
          emits each repo passage as a citation (event type "source": path, line range, commit) so Open WebUI lists them as sources.
Filter API checked against open_webui 0.11.4 (utils/filter.py, utils/middleware.py outlet_filter_handler).
"""
import json
import re
import time
import urllib.request

from pydantic import BaseModel

MARK = "[agent memory digest]"
RECEIPT_MARK = "**Receipt**"
RECEIPT_RE = re.compile(r"\n*---\n\*\*Receipt\*\*.*\Z", re.S)


def _short(sha, n=8):
    return (sha or "")[:n] or "-"


def _usage_stats(usage):
    """(tokens_per_second, seconds) from Open WebUI's message usage dict (Ollama eval stats); None where absent."""
    if not isinstance(usage, dict):
        return None, None
    tps = usage.get("response_token/s") or usage.get("tokens_per_second")
    if not tps and usage.get("eval_count") and usage.get("eval_duration"):
        tps = usage["eval_count"] / (usage["eval_duration"] / 1e9)
    total = usage.get("total_duration")
    secs = (total / 1e9) if isinstance(total, (int, float)) and total > 1e6 else usage.get("total_seconds")
    return (round(float(tps), 1) if tps else None), (round(float(secs), 1) if secs else None)


def build_receipt(data, usage=None, latency_s=None):
    """The receipt text (Markdown) from the tool server's proof_turn data. Pure function: no network, no model."""
    m, r, ctx = data.get("model") or {}, data.get("repo") or {}, data.get("context") or {}
    lines = ["---", RECEIPT_MARK + " (built by code from the local call log, not written by the model)"]
    dig = m.get("digest")
    lines.append("- Model: %s%s, run by Ollama at %s (local)" % (m.get("base") or "?", (" digest " + dig) if dig else "", m.get("endpoint") or "127.0.0.1:11434"))
    lines.append("- Repo: commit %s%s" % (_short(r.get("commit"), 7), (" (dirty: %s changed files)" % r.get("dirty_count")) if r.get("dirty") else " (clean)"))
    calls = data.get("calls") or []
    if not calls:
        lines.append("- Tools called: none (answered from the prompt and memory, no repo file was read for this turn)")
    else:
        lines.append("- Tools called: " + ", ".join(c["tool"] for c in calls))
        for c in calls[:6]:
            fs = c.get("files") or []
            if not fs:
                lines.append("  - %s: no repo file read" % c["tool"])
                continue
            shown = ", ".join("%s sha256 %s%s" % (f["path"], _short(f.get("sha256")), "" if f.get("clean") in (True, None) else "*") for f in fs[:3])
            lines.append("  - %s read %s%s" % (c["tool"], shown, " (+%d more)" % (len(fs) - 3) if len(fs) > 3 else ""))
    cites = [(x["path"], x["lines"]) for c in calls for x in c.get("citations") or []]
    if cites:
        lines.append("- Passages: " + ", ".join("%s:%s" % p for p in list(dict.fromkeys(cites))[:6]) + " @ " + _short(r.get("commit"), 7))
    sysm, mem, mp = ctx.get("system") or {}, ctx.get("memory") or {}, ctx.get("master_prompt") or {}
    lines.append("- Context: master prompt sha256 %s, system prompt ~%s tokens (estimate); memory digest %s entries%s%s" % (
        _short(mp.get("sha256")), sysm.get("tokens_est", "?"), mem.get("entries", "?"),
        (" sha256 " + _short(mem.get("sha256"))) if mem.get("sha256") else "", "" if mem.get("recorded", True) else " (not recorded)"))
    tps, secs = _usage_stats(usage)
    secs = secs or (round(latency_s, 1) if latency_s else None)
    bits = []
    if tps:
        bits.append("%s tokens/s" % tps)
    if secs:
        bits.append("%s s" % secs)
    if bits:
        lines.append("- Speed: " + ", ".join(bits))
    if any(f.get("clean") is False for c in calls for f in c.get("files") or []):
        lines.append("  (* file differs from the committed version at HEAD)")
    return "\n".join(lines)


def _edit_output(msg, fn):
    """Apply fn to the text of the last output_text part of a message's `output` list (Open WebUI 0.11 renders that, not content)."""
    out = msg.get("output")
    if not isinstance(out, list):
        return
    for item in reversed(out):
        if item.get("type") == "message" and isinstance(item.get("content"), list):
            for part in reversed(item["content"]):
                if part.get("type") == "output_text" and isinstance(part.get("text"), str):
                    part["text"] = fn(part["text"])
                    return
            return


def strip_receipt(text):
    return RECEIPT_RE.sub("", text) if isinstance(text, str) and RECEIPT_MARK in text else text


class Filter:
    class Valves(BaseModel):
        tool_server_url: str = "http://127.0.0.1:8770"
        timeout_s: float = 4.0
        priority: int = 10

    def __init__(self):
        self.valves = self.Valves()

    def _post(self, path, body):
        req = urllib.request.Request(self.valves.tool_server_url.rstrip("/") + path, data=json.dumps(body).encode(), method="POST",
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.valves.timeout_s) as r:
            return json.loads(r.read())

    @staticmethod
    def _chat_id(body, metadata):
        return (metadata or {}).get("chat_id") or (body.get("metadata") or {}).get("chat_id") or body.get("chat_id") or None

    def inlet(self, body: dict, __user__: dict = None, __metadata__: dict = None) -> dict:
        try:
            msgs = body.get("messages") or []
            for m in msgs:
                if m.get("role") == "assistant" and isinstance(m.get("content"), str):
                    m["content"] = strip_receipt(m["content"])
                    _edit_output(m, strip_receipt)
            user = next((str(m.get("content", "")) for m in reversed(msgs) if m.get("role") == "user"), "")
            cid = self._chat_id(body, __metadata__)
            self._post("/proof_record", {"chat_id": cid, "part": "turn", "data": {"user_head": user[:120], "since_ts": time.time() - 1}})
        except Exception:
            pass
        return body

    async def outlet(self, body: dict, __user__: dict = None, __event_emitter__=None, __metadata__: dict = None, __model__: dict = None) -> dict:
        try:
            msgs = body.get("messages") or []
            mid = body.get("id") or (__metadata__ or {}).get("message_id")
            target = next((m for m in reversed(msgs) if m.get("role") == "assistant" and (not mid or m.get("id") in (mid, None))), None)
            if not target or not isinstance(target.get("content"), str) or RECEIPT_MARK in target["content"]:
                return body
            cid = self._chat_id(body, __metadata__)
            sp = (__metadata__ or {}).get("system_prompt")      # set by Open WebUI after the inlet filters: preset system prompt + memory block
            if isinstance(sp, str) and sp:
                self._post("/proof_record", {"chat_id": cid, "part": "system", "data": {"text": sp.split(MARK)[0].rstrip()}})
            data = self._post("/proof_turn", {"chat_id": cid, "message_id": mid, "model": body.get("model")})
            usage = target.get("usage") or (target.get("info") or {}).get("usage") or target.get("info")
            block = build_receipt(data, usage, None)
            target["content"] = target["content"].rstrip() + "\n\n" + block
            _edit_output(target, lambda t: t.rstrip() + "\n\n" + block)       # the UI renders message["output"] when present
            if __event_emitter__:
                for s in data.get("sources") or []:
                    try:
                        await __event_emitter__({"type": "source", "data": s})
                    except Exception:
                        break
        except Exception:
            pass
        return body
