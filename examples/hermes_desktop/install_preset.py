"""Create/update the "Hermes chip agent" model preset in a running Open WebUI (WEBUI_AUTH=False).
Docs: examples/hermes_desktop/README.md, docs/HERMES_DESKTOP.md, docs/AGENT_MODELS.md
Reads preset.json; params.system is "@file" or a list of "@file" (paths relative to this directory): replaced by the file
text(s), joined by a blank line. The preset uses the generated master prompt (tools/prompts/master_prompt.txt) followed by
system_prompt.txt (tool-use details). Idempotent."""
import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
BASE = os.environ.get("WEBUI_URL", "http://127.0.0.1:8080")


def call(path, body=None, token=None):
    req = urllib.request.Request(BASE + path, method="POST" if body is not None else "GET",
                                 data=json.dumps(body).encode() if body is not None else None)
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", "Bearer " + token)
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read() or b"null")


def load_preset(model=None):
    """model: Ollama tag for an extra preset "<name> (<tag>)"; None keeps the default preset (hermes3:8b) unchanged."""
    p = json.load(open(os.path.join(HERE, "preset.json")))
    if model and model != p["base_model_id"]:
        p["id"] = p["id"] + "-" + model.replace(":", "-").replace(".", "_")
        p["name"] = "%s (%s)" % (p["name"], model)
        p["base_model_id"] = model
    s = p["params"].get("system", "")
    parts = s if isinstance(s, list) else [s]
    if all(x.startswith("@") for x in parts) and parts:
        p["params"]["system"] = "\n\n".join(open(os.path.join(HERE, x[1:])).read().strip() for x in parts)
    return p


def hide_other_models(token, keep):
    """Hide every model except the preset from the model picker: an override entry per base model with meta.hidden=true
    (Open WebUI 0.11.x has no MODEL_FILTER_LIST). The entries stay ACTIVE: the preset's own base model (hermes3:8b) must
    remain resolvable, and the other Ollama models stay installed. Returns the hidden ids."""
    hidden = []
    seen = {m["id"]: m for m in call("/api/models", None, token).get("data", [])}
    for r in call("/api/v1/models/all", None, token) or []:  # override entries that are not listed (e.g. inactive)
        if not r.get("base_model_id"):
            seen.setdefault(r["id"], r)
    for mid, m in seen.items():
        if mid == keep:
            continue
        body = {"id": mid, "base_model_id": None, "name": (m.get("info") or {}).get("name") or m.get("name") or mid,
                "meta": {"hidden": True}, "params": {}, "is_active": True, "access_control": None}
        try:
            call("/api/v1/models/create", body, token)
        except urllib.error.HTTPError:  # entry exists (e.g. from an earlier run): rewrite it
            call("/api/v1/models/model/update", body, token)
        hidden.append(mid)
    return hidden


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--model", default=None, help="Ollama tag: create/update one extra preset for this model (default: the hermes3:8b preset)")
    a = ap.parse_args(argv)
    token = call("/api/v1/auths/signin", {"email": "admin@localhost", "password": "x"})["token"]
    p = load_preset(a.model)
    try:
        call("/api/v1/models/model?id=" + p["id"], None, token)
        exists = True
    except urllib.error.HTTPError:
        exists = False
    if exists:
        call("/api/v1/models/model/update", p, token)
        print("preset updated:", p["name"])
    else:
        call("/api/v1/models/create", p, token)
        print("preset created:", p["name"])
    if not a.model:  # the extra per-model presets are an opt-in exception; the default run locks the list down
        hid = hide_other_models(token, p["id"])
        print("hidden from the UI (still installed in Ollama): %d model(s)" % len(hid))


if __name__ == "__main__":
    sys.exit(main())
