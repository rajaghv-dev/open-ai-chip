"""pytest tests/tools/test_grafana_export.py: the local Grafana pieces. The SQLite export (schema, contents against the
committed designs/*/output/metrics.json, idempotent), the dashboards (valid JSON, unique panel ids, every panel query runs on
the exported db, datasource uid matches provisioning), the provisioning YAML (checked as text: no YAML library needed), the
Hermes MCP entry and wrapper (Keychain read, loopback only, no write tools), and that no secret or absolute home path is in a
repo file. No Grafana, no Keychain write, no network.

Run: build/agent/venv/bin/python -m pytest -q tests/tools/test_grafana_export.py
Pass: every test passes.
Docs: docs/GRAFANA.md
"""
import glob
import json
import os
import re
import sqlite3
import subprocess
import sys

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
EXPORT = os.path.join(REPO, "scripts", "grafana", "export_db.py")
DASH = os.path.join(REPO, "examples", "grafana", "dashboards")
PROV = os.path.join(REPO, "examples", "grafana", "provisioning")
FILES = ([EXPORT, os.path.join(REPO, "docs", "GRAFANA.md")]
         + glob.glob(os.path.join(REPO, "scripts", "grafana", "*")) + glob.glob(os.path.join(DASH, "*.json"))
         + glob.glob(os.path.join(PROV, "*", "*.yaml")) + glob.glob(os.path.join(REPO, "scripts", "hermes", "grafana_mcp.*"))
         + [os.path.join(REPO, "tests", "tools", "test_grafana_export.py")])


@pytest.fixture(scope="module")
def db(tmp_path_factory):
    path = str(tmp_path_factory.mktemp("grafana") / "chip.db")
    r = subprocess.run([sys.executable, EXPORT, "--db", path], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return path


def rows(db, sql):
    con = sqlite3.connect(db)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def test_schema_and_counts(db):
    tables = {r[0] for r in rows(db, "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"designs", "runs", "whatifs", "eval_scores", "proof_calls", "meta"} <= tables
    n_designs = len(glob.glob(os.path.join(REPO, "designs", "*", "output", "metrics.json")))
    assert rows(db, "SELECT COUNT(*) FROM designs")[0][0] == n_designs >= 20


def test_design_values_match_metrics(db):
    m = json.load(open(os.path.join(REPO, "designs", "vision_block", "output", "metrics.json")))
    r = rows(db, "SELECT cells, ff, die_um2, drc, lvs, xor, antenna, signoff, family FROM designs "
                 "WHERE name='vision_block'")[0]
    assert r[0] == m["design__instance__count__stdcell"]
    assert r[1] == m["design__instance__count__class:sequential_cell"]
    assert r[2] == m["design__die__area"]
    assert r[3:7] == (0, 0, 0, 0) and r[7] == "PASS" and r[8] == "tiny_ai"


def test_worst_slack_is_min_over_corners(db):
    name, ws = rows(db, "SELECT name, setup_ws FROM designs ORDER BY setup_ws LIMIT 1")[0]
    m = json.load(open(os.path.join(REPO, "designs", name, "output", "metrics.json")))
    vals = [v for k, v in m.items() if k.startswith("timing__setup__ws__corner:")]
    assert abs(ws - min(vals)) < 1e-9


def test_frozen_flag(db):
    frozen = json.load(open(os.path.join(REPO, "designs", "FROZEN.json")))["designs"]
    got = {r[0] for r in rows(db, "SELECT name FROM designs WHERE frozen=1")}
    assert got == set(frozen)


def test_idempotent(tmp_path):
    p = str(tmp_path / "a.db")
    for _ in range(2):
        assert subprocess.run([sys.executable, EXPORT, "--db", p], capture_output=True).returncode == 0
    assert rows(p, "SELECT COUNT(*) FROM designs")[0][0] == len(glob.glob(os.path.join(REPO, "designs", "*", "output", "metrics.json")))


def dashboards():
    out = [json.load(open(p)) for p in sorted(glob.glob(os.path.join(DASH, "*.json")))]
    assert len(out) == 3
    return out


def test_dashboards_valid_and_queries_run(db):
    uids = set()
    for d in dashboards():
        assert d["uid"] not in uids
        uids.add(d["uid"])
        ids = [p["id"] for p in d["panels"]]
        assert len(ids) == len(set(ids)) and ids
        for p in d["panels"]:
            assert p["datasource"]["uid"] == "chipdb"
            for t in p["targets"]:
                assert t["datasource"]["uid"] == "chipdb" and t["queryText"] == t["rawQueryText"]
                cur = rows(db, t["queryText"])   # raises if a table or column is wrong
                assert cur is not None
    assert uids == {"chip-overview", "chip-runs", "chip-agent"}


def test_dashboards_regenerate_identically(tmp_path):
    before = {p: open(p).read() for p in glob.glob(os.path.join(DASH, "*.json"))}
    subprocess.run([sys.executable, os.path.join(REPO, "scripts", "grafana", "gen_dashboards.py")], check=True,
                   capture_output=True)
    for p, txt in before.items():
        assert open(p).read() == txt, "dashboards are generated: run scripts/grafana/gen_dashboards.py"


def test_provisioning_text():
    ds = open(os.path.join(PROV, "datasources", "chip.yaml")).read()
    assert "apiVersion: 1" in ds and "uid: chipdb" in ds and "type: frser-sqlite-datasource" in ds
    assert "__CHIP_DB_PATH__" in ds and "/Us" + "ers" not in ds
    pr = open(os.path.join(PROV, "dashboards", "chip.yaml")).read()
    assert "apiVersion: 1" in pr and "__CHIP_DASH_DIR__" in pr and "type: file" in pr


def test_setup_script_binds_loopback_and_disables_telemetry():
    s = open(os.path.join(REPO, "scripts", "grafana", "setup_grafana.sh")).read()
    for needle in ("http_addr = 127.0.0.1", "http_port = 3000", "reporting_enabled = false", "check_for_updates = false",
                   "allow_sign_up = false", "[auth.anonymous]\nenabled = false", "--uninstall", "--apply"):
        assert needle in s, needle
    assert "admin:admin" not in s


def test_mcp_entry_and_wrapper():
    e = json.load(open(os.path.join(REPO, "scripts", "hermes", "grafana_mcp.json")))
    g = e["mcp_servers"]["grafana"]
    assert g["env"] == {"GRAFANA_URL": "http://127.0.0.1:3000"}
    assert set(g["tools"]["include"]) <= {"search_dashboards", "get_dashboard_summary", "get_dashboard_panel_queries",
                                          "list_datasources", "get_dashboard_property"}
    assert not any(re.match(r"(update|create|delete|install)_", t) or t in ("grafana_api_request", "query_sql")
                   for t in g["tools"]["include"])
    w = open(os.path.join(REPO, "scripts", "hermes", "grafana_mcp.sh")).read()
    assert "security find-generic-password -s open-ai-chip-grafana-mcp -w" in w
    assert "-disable-write" in w and "exec " in w and "127.0.0.1" in w
    assert "glsa" + "_" not in w and os.access(os.path.join(REPO, "scripts", "hermes", "grafana_mcp.sh"), os.X_OK)


def kc(service):
    r = subprocess.run(["security", "find-generic-password", "-s", service, "-w"], capture_output=True, text=True)
    return r.stdout.strip() if r.returncode == 0 else ""


def test_no_secrets_or_home_paths_in_repo_files():
    secrets = [s for s in (kc("open-ai-chip-grafana"), kc("open-ai-chip-grafana-mcp")) if len(s) > 8]
    for p in FILES:
        if not os.path.isfile(p):
            continue
        txt = open(p, errors="replace").read()
        if not p.endswith("test_grafana_export.py"):
            assert "glsa" + "_" not in txt, p
        assert "/Us" + "ers/" not in txt, p
        for s in secrets:
            assert s not in txt, "secret found in " + os.path.relpath(p, REPO)
