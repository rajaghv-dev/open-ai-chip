"""pytest tests/tools/test_master_prompt.py: the generated master prompt (tools/prompts/master_prompt.txt) and its users.
Generator deterministic and up to date, size budget, all designs named, Open WebUI preset uses it, --context off keeps the
old system prompt byte-identical, --context on prepends it. No Ollama, no network.

Run: build/agent/venv/bin/python -m pytest -q tests/tools/test_master_prompt.py (also part of `make test`, section == tools)
Pass: every test passes.
Docs: tests/tools/TEST_MATRIX_TOOLS.md, docs/HERMES_AGENT.md, docs/HERMES_DESKTOP.md
"""
import hashlib
import json
import os
import subprocess
import sys

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "scripts", "lib"))
sys.path.insert(0, os.path.join(REPO, "scripts", "docs"))
sys.path.insert(0, os.path.join(REPO, "tools"))
sys.path.insert(0, os.path.join(REPO, "examples", "hermes_desktop"))
import repo  # noqa: E402
import make_master_prompt as MP  # noqa: E402
import hermes_agent as H  # noqa: E402
import install_preset as IP  # noqa: E402

PROMPT = os.path.join(REPO, "tools", "prompts", "master_prompt.txt")


def test_generator_deterministic_and_up_to_date():
    """Pins down: build() is deterministic and the committed file equals it."""
    assert MP.build() == MP.build()
    assert open(PROMPT, encoding="utf-8").read() == MP.build()
    r = subprocess.run([sys.executable, os.path.join(REPO, "scripts", "docs", "make_master_prompt.py"), "--check"],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stdout


def test_size_budget():
    """Pins down: <= MP.BUDGET_TOKENS (1400) tokens at 4 chars/token (8B model, num_ctx 8192)."""
    assert MP.tokens(open(PROMPT, encoding="utf-8").read()) <= MP.BUDGET_TOKENS


def test_mentions_all_designs_and_facts():
    """Pins down: every design of ALL_DESIGNS by exact name, plus the headline facts and tool routing."""
    t = open(PROMPT, encoding="utf-8").read()
    designs = repo.all_designs()
    assert len(designs) == 25
    for d in designs:
        assert d in t, d
    for k in ("sky130A", "LibreLane 3.0.2", "25 ns", "40 MHz", "25 small", "metrics.json", "RESULTS.md", "VALIDATION.md",
              "LESSONS.md", "make flow-all", "search_docs", "klayout_view", "list_skills", "run_make", "job_status",
              "claude_task", "never edits", "unknown"):
        assert k in t, k
    assert "{{" not in t and not any(l.startswith("#") for l in t.split("\n"))


def test_preset_uses_master_prompt():
    """Pins down: the Open WebUI preset (default and per-model) system prompt starts with the master prompt."""
    master = open(PROMPT, encoding="utf-8").read().strip()
    for model in (None, "qwen3:8b"):
        p = IP.load_preset(model)
        assert p["params"]["system"].startswith(master)
        assert "png_url" in p["params"]["system"]          # the tool-use details follow
    sugg = json.load(open(os.path.join(REPO, "examples", "hermes_desktop", "prompt_suggestions.json")))
    assert 4 <= len(sugg) <= 8 and all(s["content"] for s in sugg)
    assert "DEFAULT_PROMPT_SUGGESTIONS" in open(os.path.join(REPO, "examples", "hermes_desktop", "start.sh")).read()


def test_no_duplicate_header_text():
    """Pins down: the tool-use details do not repeat the master prompt (avoid duplicating text)."""
    sp = open(os.path.join(REPO, "examples", "hermes_desktop", "system_prompt.txt")).read()
    assert "23 small" not in sp and "sky130" not in sp


def test_context_off_is_byte_identical(monkeypatch):
    """Pins down: without --context / HERMES_CONTEXT the system prompts are exactly the old ones (prompt sha unchanged)."""
    monkeypatch.delenv("HERMES_CONTEXT", raising=False)
    assert H.with_context(H.SYSTEM) is H.SYSTEM
    assert H._hermes_system() == H.SYSTEM + H.tools_suffix(H.eda_tools.TOOLS)
    sha = hashlib.sha256(H._hermes_system().encode()).hexdigest()
    monkeypatch.setenv("HERMES_CONTEXT", "0")
    assert hashlib.sha256(H._hermes_system().encode()).hexdigest() == sha


def test_context_on_prepends(monkeypatch):
    """Pins down: HERMES_CONTEXT=1 puts the master prompt in front of the unchanged system prompt."""
    master = open(PROMPT, encoding="utf-8").read().strip()
    monkeypatch.setenv("HERMES_CONTEXT", "1")
    s = H._hermes_system()
    assert s.startswith(master) and H.SYSTEM in s
    assert s == master + "\n\n" + H.SYSTEM + H.tools_suffix(H.eda_tools.TOOLS)
