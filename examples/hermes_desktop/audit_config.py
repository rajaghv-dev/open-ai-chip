"""Audit the running Open WebUI against the lock-down in start.sh / install_preset.py: prints setting -> expected -> actual,
exit 1 on any drift (0 if all match, 2 if Open WebUI is not reachable). Reads only (admin config export + model list).
Usage: build/agent/venv/bin/python examples/hermes_desktop/audit_config.py   (run after start.sh)
Docs: docs/HERMES_DESKTOP.md, examples/hermes_desktop/README.md"""
import json
import os
import sys
import urllib.request

BASE = os.environ.get("WEBUI_URL", "http://127.0.0.1:8080")
PRESET_ID = "hermes-chip-agent"

# config key (from /api/v1/configs/export) -> expected value
EXPECTED = {
    "ui.default_models": PRESET_ID,
    "ui.enable_signup": False,
    "ui.enable_community_sharing": False,
    "ui.enable_user_webhooks": False,
    "openai.enable": False,
    "direct.enable": False,
    "code_execution.enable": False,
    "code_interpreter.enable": False,
    "web.search.enable": False,
    "image_generation.enable": False,
    "evaluation.arena.enable": False,
    "channels.enable": False,
    "memories.enable": False,
    "auth.enable_api_keys": False,
    "user.permissions.features.code_interpreter": False,
    "user.permissions.features.web_search": False,
    "user.permissions.features.image_generation": False,
    "user.permissions.features.direct_tool_servers": False,
    "ollama.enable": True,
    "ollama.base_urls": ["http://127.0.0.1:11434"],
}


def flatten(d, p=""):
    out = {}
    for k, v in d.items():
        if isinstance(v, dict):
            out.update(flatten(v, p + k + "."))
        else:
            out[p + k] = v
    return out


SLASH = ["/harden", "/soc-run", "/wrapper", "/notes", "/precision", "/add-engine"]
FILTER_ID = "chip_memory_digest"
RECEIPT_ID = "chip_receipt"


def compare(flat, models, tool_conns, prompts=None, filt=None, receipt=None):
    """Pure function: returns [(setting, expected, actual, ok)]."""
    rows = [(k, v, flat.get(k, "<missing>"), flat.get(k, "<missing>") == v) for k, v in EXPECTED.items()]
    ids = sorted(c.get("info", {}).get("id") for c in tool_conns)
    rows.append(("tool connectors", ["chip"], ids, ids == ["chip"]))
    urls = [c.get("url") for c in tool_conns]
    rows.append(("tool connector urls", ["http://127.0.0.1:8770"], urls, urls == ["http://127.0.0.1:8770"]))
    shown = sorted(m["id"] for m in models if not ((m.get("info") or {}).get("meta") or {}).get("hidden"))
    rows.append(("models shown in picker", [PRESET_ID], shown, shown == [PRESET_ID]))
    if prompts is not None:
        have = sorted(p.lstrip("/") for p in prompts)
        want = sorted(c.lstrip("/") for c in SLASH)
        rows.append(("slash prompts installed", want, have, set(want) <= set(have)))
    if filt is not None:
        st = [bool(filt.get("is_active")), bool(filt.get("is_global"))]
        rows.append(("memory filter active, global", [True, True], st, st == [True, True]))
    if receipt is not None:
        st = [bool(receipt.get("is_active")), bool(receipt.get("is_global"))]
        rows.append(("receipt filter active, global", [True, True], st, st == [True, True]))
    return rows


def api(path, token=None, body=None):
    req = urllib.request.Request(BASE + path, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    if token:
        req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def main():
    try:
        tok = api("/api/v1/auths/signin", body={"email": "admin@localhost", "password": "x"})["token"]
        flat = flatten(api("/api/v1/configs/export", tok))
        models = api("/api/models", tok)["data"]
        prompts = [p["command"] for p in api("/api/v1/prompts/", tok) or []]
        try:
            filt = api("/api/v1/functions/id/" + FILTER_ID, tok) or {}
        except OSError:
            filt = {}
        try:
            receipt = api("/api/v1/functions/id/" + RECEIPT_ID, tok) or {}
        except OSError:
            receipt = {}
    except OSError as e:
        print("Open WebUI not reachable at %s (%s): run scripts/hermes.sh first" % (BASE, e), file=sys.stderr)
        return 2
    rows = compare(flat, models, flat.get("tool_server.connections", []), prompts, filt, receipt)
    w = max(len(r[0]) for r in rows)
    print("%-*s  %-30s  %-30s  %s" % (w, "setting", "expected", "actual", "ok"))
    for k, e, a, ok in rows:
        print("%-*s  %-30s  %-30s  %s" % (w, k, json.dumps(e)[:30], json.dumps(a)[:30], "ok" if ok else "DRIFT"))
    bad = [r for r in rows if not r[3]]
    print("%d checks, %d drift" % (len(rows), len(bad)))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
