"""Model switch (docs/AGENT_MODELS.md): registry is valid, every entry point has --model, HERMES_MODEL is honoured,
Docs: tests/tools/TEST_MATRIX_TOOLS.md, docs/AGENT_MODELS.md
defaults are unchanged. No Ollama needed."""
import json
import os
import subprocess
import sys

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
PY = sys.executable
ENTRY = ["tools/hermes_agent.py", "tools/eval/run_eval.py", "examples/hermes_harness/harness.py",
         "examples/hermes_harness/eval_harness.py", "examples/hermes_rag/rag_agent.py", "examples/hermes_rag/eval_rag.py",
         "examples/hermes_klayout_gui/agent.py", "examples/hermes_klayout_gui/demo.py", "examples/hermes_desktop/install_preset.py"]


def _env(**kw):
    e = {k: v for k, v in os.environ.items() if k != "HERMES_MODEL"}
    e.update(kw)
    return e


def test_registry_valid():
    d = json.load(open(os.path.join(REPO, "examples", "models.json")))
    ms = d["models"]
    assert 1 <= len(ms) <= 4
    tags = [m["tag"] for m in ms]
    assert len(set(tags)) == len(tags)
    assert "hermes3:8b" in tags and d["default"] == "hermes3:8b"
    for m in ms:
        for k in ("tag", "size_on_disk", "params", "license", "notes", "family"):
            assert m.get(k), (m.get("tag"), k)
    assert "/Users/" not in json.dumps(d)


@pytest.mark.parametrize("rel", ENTRY)
def test_entry_point_accepts_model(rel):
    out = subprocess.run([PY, os.path.join(REPO, rel), "--help"], capture_output=True, text=True, cwd=REPO, env=_env())
    assert out.returncode == 0, out.stderr[-500:]
    assert "--model" in out.stdout


def test_default_and_env():
    code = "import sys; sys.path.insert(0, 'tools'); import hermes_agent; print(hermes_agent.MODEL)"
    run = lambda **kw: subprocess.run([PY, "-c", code], capture_output=True, text=True, cwd=REPO, env=_env(**kw)).stdout.strip()
    assert run() == "hermes3:8b"
    assert run(HERMES_MODEL="gemma4:e2b") == "gemma4:e2b"


def test_flag_sets_model_in_harness_and_klayout(monkeypatch):
    sys.path[:0] = [os.path.join(REPO, "tools"), os.path.join(REPO, "examples", "hermes_harness"), os.path.join(REPO, "examples", "hermes_klayout_gui")]
    import hermes_agent
    import harness
    import agent
    monkeypatch.setattr(hermes_agent, "MODEL", "hermes3:8b")
    seen = []
    monkeypatch.setattr(harness, "run_episode", lambda *a, **k: seen.append(hermes_agent.MODEL) or
                        {"answer": "x", "tool_calls": [], "seconds": 0, "retries": 0, "steps": 0, "grounding": None})
    monkeypatch.setattr(sys, "argv", ["harness.py", "--model", "m:1b", "hello"])
    harness.main()
    assert seen == ["m:1b"]
    monkeypatch.setattr(hermes_agent, "MODEL", "hermes3:8b")
    monkeypatch.setattr(agent, "make_backend", lambda b: (_ for _ in ()).throw(RuntimeError("stop")))
    monkeypatch.setattr(sys, "argv", ["agent.py", "--model", "m:2b", "--dry-run"])
    with pytest.raises(RuntimeError):
        agent.main()
    assert hermes_agent.MODEL == "m:2b"


def test_think_switch(monkeypatch):
    sys.path.insert(0, os.path.join(REPO, "tools"))
    import hermes_agent
    sent = []

    class R:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def read(self): return b"{}"
    monkeypatch.setattr(hermes_agent.urllib.request, "urlopen", lambda req, timeout=0: sent.append(json.loads(req.data)) or R())
    monkeypatch.setattr(hermes_agent.json, "load", lambda r: {})
    hermes_agent._post("/api/chat", {"model": "hermes3:8b", "messages": []})
    hermes_agent._post("/api/chat", {"model": "qwen3.5:4b", "messages": []})
    assert "think" not in sent[0] and sent[1]["think"] is False


def test_install_preset_model_option():
    sys.path.insert(0, os.path.join(REPO, "examples", "hermes_desktop"))
    import install_preset
    d = install_preset.load_preset()
    assert d["base_model_id"] == "hermes3:8b" and d["name"] == "Hermes chip agent"
    p = install_preset.load_preset("qwen3.5:4b")
    assert p["base_model_id"] == "qwen3.5:4b" and p["name"] == "Hermes chip agent (qwen3.5:4b)" and p["id"] != d["id"]
