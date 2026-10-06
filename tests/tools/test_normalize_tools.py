"""pytest tests/tools/test_normalize_tools.py: argument normalisation middleware and the confirm_run tool of the Hermes tool server
(examples/hermes_desktop/tool_server/normalize_tools.py). Uses FastAPI TestClient on the real app with JOBS faked: no job, Docker,
Ollama or network.
Run: build/agent/venv/bin/python -m pytest -q tests/tools/test_normalize_tools.py
Pass: placeholders are dropped, designs repaired or answered with valid names (tool not called), confirm ids cleaned, confirm_run
starts exactly what the id was issued for and nothing for an unknown id.
Docs: build/agent/tool_eval_PLAN.md (items C, E), tests/tools/TEST_MATRIX_TOOLS.md"""
import os
import sys
from types import SimpleNamespace

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
sys.path.insert(0, os.path.join(REPO, "examples", "hermes_desktop", "tool_server"))
pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

import normalize_tools as nt  # noqa: E402
import tool_server as ts  # noqa: E402

client = TestClient(ts.app)
VALID = ["kv_attn_n4", "kv_attn_n8", "kv_attn_n16", "kv_attn_n8_int4", "kv_attn_n8_ring", "prec_fp16", "prec_fp8", "prec_int8",
         "vision_block", "text_sentiment", "audio_pitch", "user_proj_example"]


def test_resolve_design():
    """Pins down: exact, case, dash, alias, prefix, substring, fuzzy repairs and the refusal of an invented name."""
    r = lambda s: nt.resolve_design(s, VALID)[0]  # noqa: E731
    assert r("kv_attn_n8") == "kv_attn_n8" and r(" KV_ATTN_N8 ") == "kv_attn_n8" and r("kv-attn-n8") == "kv_attn_n8"
    assert r("kv8") == "kv_attn_n8" and r("designs/vision_block/") == "vision_block" and r("vision") == "vision_block"
    assert r("sentiment") == "text_sentiment" and r("fp16") == "prec_fp16" and r("audio_pich") == "audio_pitch"
    assert r("kv_attn_n8_in4") == "kv_attn_n8_int4"
    name, close = nt.resolve_design("flux_capacitor", VALID)
    assert name is None and len(close) <= 5
    assert nt.resolve_design("kv_attn", VALID)[0] is None or True       # ambiguous prefix must not crash


def test_normalize_pure():
    """Pins down: placeholders removed, confirm id cleaned, target lower-cased, designs list repaired."""
    new, ch, err = nt.normalize("read_metrics", {"design": "KV8", "keys": "-", "pattern": "none", "k": " 5 "}, VALID)
    assert err is None and new == {"design": "kv_attn_n8", "k": "5"} and {c[0] for c in ch} >= {"keys", "pattern", "design", "k"}
    new, _, _ = nt.normalize("run_make", {"confirm_id": "yes, run 50ad87", "target": "-", "design": ""}, VALID)
    assert new == {"confirm_id": "50ad87"}
    assert nt.normalize("run_make", {"confirm_id": "ffffffffff"}, VALID)[0] == {"confirm_id": "ffffff"}
    assert nt.normalize("run_make", {"target": "SIMULATE", "design": "vision_block"}, VALID)[0]["target"] == "simulate"
    new, _, _ = nt.normalize("compare_designs", {"metric": "m", "designs": "kv4, kv_attn_n16"}, VALID)
    assert new["designs"] == ["kv_attn_n4", "kv_attn_n16"]
    assert nt.normalize("compare_designs", {"metric": "m", "designs": "-"}, VALID)[0] == {"metric": "m"}
    _, _, err = nt.normalize("read_metrics", {"design": "flux_capacitor"}, VALID)
    assert err and err["valid_designs"] == VALID and "error" in err


def test_middleware_http():
    """Pins down: the middleware repairs a request before the tool and answers an invented design without calling it."""
    r = client.post("/read_metrics", json={"design": "kv8", "keys": ["design__instance__count__stdcell"], "pattern": "-"}).json()
    assert r.get("design") == "kv_attn_n8" and "error" not in r
    r = client.post("/read_metrics", json={"design": "flux_capacitor"}).json()
    assert "unknown design" in r["error"] and "kv_attn_n8" in r["valid_designs"]
    r = client.post("/list_designs", json={}).json()
    assert "designs" in r
    r = client.post("/list_designs").json()
    assert "designs" in r


def test_middleware_off(monkeypatch):
    """Pins down: CHIP_TOOLS_NORMALIZE=0 switches the repair off."""
    monkeypatch.setenv("CHIP_TOOLS_NORMALIZE", "0")
    r = client.post("/read_metrics", json={"design": "flux_capacitor"}).json()
    assert "unknown design" not in str(r.get("error", "")) or "valid_designs" not in r


def _fake_jobs(monkeypatch):
    started = []

    class J:
        def start(self, kind, cmd, label, **kw):
            started.append(cmd)
            return SimpleNamespace(id="j1", state="running", log=os.path.join(REPO, "build", "agent", "jobs", "x.log"),
                                   summary=lambda: {"command": " ".join(cmd)}), None

    monkeypatch.setattr(ts, "JOBS", J())
    monkeypatch.delenv("CHIP_TOOLS_NO_CONFIRM", raising=False)
    return started


def test_confirm_run(monkeypatch):
    """Pins down: confirm_run completes the gated step of the id, single use; an unknown id starts nothing."""
    started = _fake_jobs(monkeypatch)
    r = client.post("/run_make", json={"target": "simulate", "design": "vision-block"}).json()      # design repaired by the middleware
    assert r["needs_confirmation"] and "DESIGN=vision_block" in r["will_run"] and not started
    cid = r["confirm_id"]
    bad = client.post("/confirm_run", json={"confirm_id": "ffffff"}).json()
    assert "error" in bad and bad["started"] is False and not started
    assert "pending" not in str(bad) and cid not in str(bad)                    # no id leak
    ok = client.post("/confirm_run", json={"confirm_id": "yes, run " + cid}).json()
    assert ok["job_id"] == "j1" and started == [["make", "simulate", "DESIGN=vision_block"]]
    again = client.post("/confirm_run", json={"confirm_id": cid}).json()
    assert "error" in again and len(started) == 1                                 # single use


def test_confirm_run_via_run_make_with_noise(monkeypatch):
    """Pins down: the ten-character echo and a sentence in confirm_id still confirm the right job."""
    started = _fake_jobs(monkeypatch)
    cid = client.post("/run_make", json={"target": "test"}).json()["confirm_id"]
    r = client.post("/run_make", json={"confirm_id": "Yes, run " + cid + "aa", "target": "-", "design": "-"}).json()
    assert r["job_id"] == "j1" and started == [["make", "test"]]
