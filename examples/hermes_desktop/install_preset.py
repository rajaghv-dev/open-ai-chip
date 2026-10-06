"""Create/update the "Hermes chip agent" model preset in a running Open WebUI (WEBUI_AUTH=False).
Docs: examples/hermes_desktop/README.md, docs/HERMES_DESKTOP.md, docs/AGENT_MODELS.md
Reads preset.json; '@system_prompt.txt' in params.system is replaced by the file text. Idempotent."""
import argparse
import json
import os
import sys
import urllib.error
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
    if s.startswith("@"):
        p["params"]["system"] = open(os.path.join(HERE, s[1:])).read().strip()
    return p


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


if __name__ == "__main__":
    sys.exit(main())
