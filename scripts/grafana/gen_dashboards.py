#!/usr/bin/env python3
"""Generate examples/grafana/dashboards/*.json (the three chip dashboards) from the panel definitions below.
Panels query the SQLite datasource uid "chipdb" (table names: see scripts/grafana/export_db.py).
Docs: docs/GRAFANA.md. Run: python3 scripts/grafana/gen_dashboards.py
"""
import json
import os

OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
                   "examples", "grafana", "dashboards")
DS = {"type": "frser-sqlite-datasource", "uid": "chipdb"}


def target(sql, kind="table"):
    return [{"refId": "A", "datasource": DS, "queryText": sql, "rawQueryText": sql, "queryType": kind,
             "timeColumns": ["time"] if kind == "time series" else []}]


def panel(pid, title, ptype, sql, x, y, w, h, kind="table", opts=None, unit=None, overrides=None, desc=""):
    p = {"id": pid, "title": title, "type": ptype, "datasource": DS, "description": desc,
         "gridPos": {"x": x, "y": y, "w": w, "h": h}, "targets": target(sql, kind),
         "fieldConfig": {"defaults": {"unit": unit} if unit else {}, "overrides": overrides or []},
         "options": opts or {}}
    return p


def bar(pid, title, sql, x, y, w, h, unit=None, desc=""):
    return panel(pid, title, "barchart", sql, x, y, w, h, opts={
        "orientation": "horizontal", "legend": {"showLegend": False}, "xTickLabelRotation": 0,
        "showValue": "auto", "tooltip": {"mode": "single"}}, unit=unit, desc=desc)


def stat(pid, title, sql, x, y, w=4, h=4):
    return panel(pid, title, "stat", sql, x, y, w, h, opts={
        "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}, "colorMode": "value"})


def color_cell(field, mapping):
    return {"matcher": {"id": "byName", "options": field},
            "properties": [{"id": "custom.cellOptions", "value": {"type": "color-text"}},
                           {"id": "mappings", "value": [{"type": "value", "options": mapping}]}]}


PASSFAIL = {"PASS": {"color": "green", "index": 0}, "FAIL": {"color": "red", "index": 1}}

overview = [
    stat(1, "Designs", "SELECT COUNT(*) AS designs FROM designs", 0, 0),
    stat(2, "Signoff PASS", "SELECT COUNT(*) AS pass FROM designs WHERE signoff='PASS'", 4, 0),
    stat(3, "Frozen", "SELECT COUNT(*) AS frozen FROM designs WHERE frozen=1", 8, 0),
    stat(4, "Total std cells", "SELECT SUM(cells) AS cells FROM designs", 12, 0),
    stat(5, "Worst setup slack (ns)", "SELECT MIN(setup_ws) AS ns FROM designs", 16, 0),
    stat(6, "Worst hold slack (ns)", "SELECT MIN(hold_ws) AS ns FROM designs", 20, 0),
    panel(7, "All designs", "table",
          "SELECT name, family, cells, ff, die_um2, ROUND(util,3) AS util, ROUND(setup_ws,3) AS setup_ws_ns, "
          "setup_corner, ROUND(hold_ws,3) AS hold_ws_ns, hold_corner, drc, lvs, xor, antenna, slew_viol, "
          "ROUND(power_w*1000,4) AS power_mw, flow_s, peak_gb, frozen, signoff FROM designs ORDER BY name",
          0, 4, 24, 12, opts={"showHeader": True, "cellHeight": "sm"},
          overrides=[color_cell("signoff", PASSFAIL)],
          desc="Source: designs/*/output/{metrics,resources}.json, designs/FROZEN.json (make grafana-db)."),
    bar(8, "Std cells per design", "SELECT name, cells FROM designs ORDER BY cells DESC", 0, 16, 8, 10),
    bar(9, "Flip-flops per design", "SELECT name, ff FROM designs WHERE ff IS NOT NULL ORDER BY ff DESC",
        8, 16, 8, 10),
    bar(10, "Die area (um2)", "SELECT name, die_um2 FROM designs ORDER BY die_um2 DESC", 16, 16, 8, 10),
    bar(11, "Worst setup slack (ns), lowest first",
        "SELECT name, ROUND(setup_ws,3) AS setup_ws_ns FROM designs ORDER BY setup_ws ASC", 0, 26, 12, 10,
        desc="Worst over the nine timing corners, 25 ns clock."),
    bar(12, "Worst hold slack (ns), lowest first",
        "SELECT name, ROUND(hold_ws,3) AS hold_ws_ns FROM designs ORDER BY hold_ws ASC", 12, 26, 12, 10),
    bar(13, "Flow wall time (s)", "SELECT name, flow_s FROM designs WHERE flow_s IS NOT NULL ORDER BY flow_s DESC",
        0, 36, 12, 10, unit="s"),
    panel(14, "Signoff status by family", "table",
          "SELECT family, COUNT(*) AS designs, SUM(signoff='PASS') AS pass, SUM(frozen) AS frozen "
          "FROM designs GROUP BY family ORDER BY family", 12, 36, 12, 10),
]

runs = [
    stat(1, "Runs recorded", "SELECT COUNT(*) AS runs FROM runs", 0, 0),
    stat(2, "PASS", "SELECT COUNT(*) AS pass FROM runs WHERE result='PASS'", 4, 0),
    stat(3, "FAIL", "SELECT COUNT(*) AS fail FROM runs WHERE result='FAIL'", 8, 0),
    stat(4, "What-if runs", "SELECT COUNT(*) AS whatifs FROM whatifs", 12, 0),
    panel(5, "Recent jobs", "table",
          "SELECT time, result, design, ROUND(secs,1) AS secs, cmd, log FROM runs WHERE ts IS NOT NULL "
          "ORDER BY ts DESC LIMIT 40", 0, 4, 24, 12, opts={"showHeader": True, "cellHeight": "sm"},
          overrides=[color_cell("result", PASSFAIL)],
          desc="Source: build/agent/jobs/*.log and build/agent/memory/runs.md."),
    panel(6, "Pass / fail per hour", "timeseries",
          "SELECT (ts/3600)*3600 AS time, SUM(result='PASS') AS pass, SUM(result='FAIL') AS fail FROM runs "
          "WHERE ts IS NOT NULL GROUP BY 1 ORDER BY 1", 0, 16, 12, 9, kind="time series",
          opts={"legend": {"showLegend": True}, "tooltip": {"mode": "multi"}},
          overrides=[{"matcher": {"id": "byName", "options": "pass"},
                      "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "green"}},
                                     {"id": "custom.drawStyle", "value": "bars"}]},
                     {"matcher": {"id": "byName", "options": "fail"},
                      "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "red"}},
                                     {"id": "custom.drawStyle", "value": "bars"}]}]),
    bar(7, "Longest jobs (s)",
        "SELECT SUBSTR(cmd,1,40) || ' ' || SUBSTR(id,10,6) AS job, ROUND(secs,1) AS secs FROM runs "
        "WHERE secs IS NOT NULL ORDER BY secs DESC LIMIT 12", 12, 16, 12, 9, unit="s"),
    panel(8, "What-ifs vs committed baseline", "table",
          "SELECT tag, design, changes, ROUND(setup_ws,3) AS setup_ws, ROUND(base_setup_ws,3) AS base_setup_ws, "
          "ROUND(d_setup_ws,3) AS d_setup_ws, ROUND(hold_ws,3) AS hold_ws, ROUND(d_hold_ws,4) AS d_hold_ws, "
          "cells, d_cells, wall_s, peak_gb FROM whatifs ORDER BY tag", 0, 25, 24, 8,
          desc="Source: build/whatif/<design>__<tag>/ vs designs/<design>/output/metrics.json. Slack in ns."),
    bar(9, "What-if setup slack change vs baseline (ns)",
        "SELECT tag, ROUND(d_setup_ws,3) AS d_setup_ws FROM whatifs ORDER BY tag", 0, 33, 12, 8),
    panel(10, "Jobs by design", "table",
          "SELECT design, COUNT(*) AS jobs, SUM(result='PASS') AS pass, SUM(result='FAIL') AS fail FROM runs "
          "WHERE design != '' GROUP BY design ORDER BY jobs DESC", 12, 33, 12, 8),
]

agent = [
    stat(1, "Tool calls logged", "SELECT COUNT(*) AS calls FROM proof_calls", 0, 0),
    stat(2, "Distinct tools", "SELECT COUNT(DISTINCT tool) AS tools FROM proof_calls", 4, 0),
    stat(3, "Non-200 calls", "SELECT COUNT(*) AS errors FROM proof_calls WHERE status != 200", 8, 0),
    stat(4, "Median call (ms)", "SELECT ms FROM proof_calls ORDER BY ms LIMIT 1 OFFSET "
         "(SELECT COUNT(*)/2 FROM proof_calls)", 12, 0),
    panel(5, "Tool-calling eval (overall)", "table",
          "SELECT backend, model, date, cases AS scored, passed, accuracy_pct FROM eval_scores WHERE grp='ALL' "
          "ORDER BY backend", 0, 4, 24, 5,
          desc="Source: examples/hermes_desktop/eval_tools/results_summary_*.json."),
    panel(6, "Eval accuracy by group (%)", "barchart",
          "SELECT grp, MAX(CASE WHEN backend='direct' THEN accuracy_pct END) AS direct, "
          "MAX(CASE WHEN backend='hermes' THEN accuracy_pct END) AS hermes FROM eval_scores WHERE grp!='ALL' "
          "GROUP BY grp ORDER BY grp", 0, 9, 24, 10,
          opts={"orientation": "auto", "legend": {"showLegend": True}, "showValue": "auto"}),
    bar(7, "Tool calls by tool (proof log)",
        "SELECT tool, COUNT(*) AS calls FROM proof_calls GROUP BY tool ORDER BY calls DESC LIMIT 20",
        0, 19, 12, 11, desc="Source: build/agent/proof/calls.jsonl."),
    panel(8, "Tool calls per hour", "timeseries",
          "SELECT (ts/3600)*3600 AS time, COUNT(*) AS calls FROM proof_calls GROUP BY 1 ORDER BY 1", 12, 19, 12, 11,
          kind="time series", opts={"legend": {"showLegend": False}},
          overrides=[{"matcher": {"id": "byName", "options": "calls"},
                      "properties": [{"id": "custom.drawStyle", "value": "bars"}]}]),
]


def dash(uid, title, panels, desc):
    return {"uid": uid, "title": title, "description": desc, "tags": ["open-ai-chip"], "schemaVersion": 39,
            "version": 1, "editable": True, "timezone": "browser", "refresh": "",
            "time": {"from": "now-30d", "to": "now"}, "panels": panels, "templating": {"list": []},
            "annotations": {"list": []}}


def main():
    os.makedirs(OUT, exist_ok=True)
    for fn, d in (("overview.json", dash("chip-overview", "open-ai-chip overview", overview,
                                         "Designs, area, timing slack, signoff and freeze status.")),
                  ("runs.json", dash("chip-runs", "Runs and jobs", runs,
                                     "Tool-server jobs, durations, pass/fail and what-if results.")),
                  ("agent.json", dash("chip-agent", "Agent", agent,
                                      "Hermes tool-calling eval scores and the tool-call proof log."))):
        with open(os.path.join(OUT, fn), "w") as f:
            json.dump(d, f, indent=1)
            f.write("\n")
        print("wrote", fn)


if __name__ == "__main__":
    main()
