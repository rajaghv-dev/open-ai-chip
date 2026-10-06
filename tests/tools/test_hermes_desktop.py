"""pytest tests/tools/test_hermes_desktop.py: scripts, preset, app builder, docs links (always on).
HERMES_DESKTOP_LIVE=1 adds a live check: start.sh brings up both servers and one chat completion returns.

Run: build/agent/venv/bin/python -m pytest -q tests/tools/test_hermes_desktop.py (also part of `make test`, section == tools)
Pass: every test passes or is skipped (opt-in tests need their env flag).
Docs: tests/tools/TEST_MATRIX_TOOLS.md, docs/HERMES_DESKTOP.md, examples/hermes_desktop/README.md
"""
import json
import os
import subprocess
import urllib.request

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
D = os.path.join(REPO, "examples", "hermes_desktop")


def read(*p):
    return open(os.path.join(REPO, *p), encoding="utf-8").read()


@pytest.mark.parametrize("rel", ["setup_webui.sh", "start.sh", "stop.sh", "desktop/make_app.sh"])
def test_scripts_parse(rel):
    """Pins down: scripts parse."""
    path = os.path.join(D, rel)
    assert os.access(path, os.X_OK)
    subprocess.run(["bash", "-n", path], check=True)


def test_preset_and_connection():
    """Pins down: preset and connection."""
    p = json.load(open(os.path.join(D, "preset.json")))
    assert p["base_model_id"] == "hermes3:8b" and p["params"]["temperature"] == 0
    assert p["params"]["function_calling"] == "legacy"
    assert "server:chip" in p["meta"]["toolIds"]
    assert os.path.exists(os.path.join(D, p["params"]["system"].lstrip("@")))
    c = json.load(open(os.path.join(D, "tool_server_connection.json")))
    assert c[0]["info"]["id"] == "chip" and c[0]["url"] == "http://127.0.0.1:8770"
    assert c[0]["path"] == "openapi.json"
    assert "{{TOOLS}}" in read("examples", "hermes_desktop", "tools_prompt.txt")


def test_start_script_settings():
    """Pins down: start script settings."""
    s = read("examples", "hermes_desktop", "start.sh")
    for k in ("WEBUI_AUTH=False", "ANONYMIZED_TELEMETRY=false", "DO_NOT_TRACK=true", "SCARF_NO_ANALYTICS=true",
              "OLLAMA_BASE_URL=http://127.0.0.1:11434", "127.0.0.1", "build/webui", "TOOL_SERVER_CONNECTIONS"):
        assert k in s
    assert ".venv" not in read("examples", "hermes_desktop", "setup_webui.sh").replace("build/webui/venv", "")


def test_app_builder_paths():
    """Pins down: app builder paths."""
    s = read("examples", "hermes_desktop", "desktop", "make_app.sh")
    assert "build/desktop" in s and "Hermes Chip Agent.app" in s and "--install" in s
    assert os.path.exists(os.path.join(D, "desktop", "app.py"))
    assert "start.sh" in read("examples", "hermes_desktop", "desktop", "app.py")


def test_no_home_paths():
    """Pins down: no home paths."""
    for root, _, files in os.walk(D):
        if "tool_server" in root or "__pycache__" in root:
            continue
        for f in files:
            assert "/Users/" not in open(os.path.join(root, f), encoding="utf-8").read(), f
    assert "/Users/" not in read("docs", "HERMES_DESKTOP.md")


def test_docs_links():
    """Pins down: docs links."""
    doc = read("docs", "HERMES_DESKTOP.md")
    assert "mermaid" in doc and "sequenceDiagram" in doc and "File > Add to Dock" in doc
    for f in ("README.md", "CLAUDE.md", os.path.join("docs", "HERMES_FROM_TERMINAL.md")):
        assert "HERMES_DESKTOP.md" in read(f), f


@pytest.mark.skipif(os.environ.get("HERMES_DESKTOP_LIVE") != "1", reason="opt-in: HERMES_DESKTOP_LIVE=1")
def test_live_start_and_chat():
    """Pins down: live start and chat."""
    subprocess.run(["bash", os.path.join(D, "start.sh")], check=True, timeout=600)
    try:
        for url in ("http://127.0.0.1:8770/health", "http://127.0.0.1:8080/health"):
            assert urllib.request.urlopen(url, timeout=10).status == 200
        def call(path, body):
            r = urllib.request.Request("http://127.0.0.1:8080" + path, json.dumps(body).encode(),
                                       {"Content-Type": "application/json"})
            return json.loads(urllib.request.urlopen(r, timeout=300).read())
        tok = call("/api/v1/auths/signin", {"email": "admin@localhost", "password": "x"})["token"]
        r = urllib.request.Request("http://127.0.0.1:8080/api/chat/completions", json.dumps(
            {"model": "hermes-chip-agent", "stream": False, "tool_ids": ["server:chip"],
             "messages": [{"role": "user", "content": "How many standard cells does vision_block have?"}]}).encode(),
            {"Content-Type": "application/json", "Authorization": "Bearer " + tok})
        d = json.loads(urllib.request.urlopen(r, timeout=300).read())
        assert "297" in d["choices"][0]["message"]["content"]
    finally:
        subprocess.run(["bash", os.path.join(D, "stop.sh")])
