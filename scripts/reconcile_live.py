#!/usr/bin/env python
"""
Live metric parity harness: LIVE dashboard backend  vs  LIVE Cube.

Why this exists: static CSV exports go stale the moment the pipeline reprocesses
a day (observed 2026-07-27: 2026-06-19 total_cogs moved 27,752 -> 58,670 between
a June CSV and the live backend). Reconciling the agent's Cube against a stale
export produces false "drift". This script always pulls BOTH sides live, so the
comparison is real.

Dashboard side: calls Node-Backend's own getHistoricalDashboard() via node
(bypasses HTTP/Firebase auth — same ClickHouse the API uses).
Cube side: POST /cubejs-api/v1/load against the running Cube (the chat agent's
data source).

The MEASURE MAP encodes the canonical dashboard-field -> Cube-measure binding
(the all-channel P&L measures, not the shopify-only ones). Edit it here when a
metric's canonical mapping changes; that keeps the agent, Cube and dashboard on
one definition.

Usage:
  Base_Agent/.venv/Scripts/python.exe scripts/reconcile_live.py \
      --brand 20 --date 2026-06-19 [--end 2026-06-19] [--cube http://127.0.0.1:4001]
Exit code 0 if every metric within tolerance, 1 otherwise (CI-friendly).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

# Node-Backend repo (dashboard oracle). Adjust if the checkout moves.
NODE_BACKEND = Path(r"C:\SpacePeppers\SpacePeppers\Seleric_Dashboard\Node-Backend")

# Canonical dashboard-field -> Cube measure(s). Sums when a list is given.
# time_dim picks the axis that matches the dashboard's definition.
MEASURE_MAP = {
    "total_sales":     dict(dash=("total_sales",),                 cube=["sales_all_channels.total_sales"],                 td="sales_all_channels.report_date"),
    "total_orders":    dict(dash=("total_orders",),                cube=["orders_all_channels.orders"],                     td="orders_all_channels.report_date"),
    "total_ad_spend":  dict(dash=("total_ad_spend",),              cube=["canonical_pnl.total_ad_spend"],                   td="canonical_pnl.report_date"),
    "net_sales":       dict(dash=("net_sales",),                   cube=["canonical_pnl.net_sales_all_channels_pnl"],       td="canonical_pnl.report_date"),
    "total_cogs":      dict(dash=("total_cogs",),                  cube=["canonical_pnl.total_operating_cost_all_channels"], td="canonical_pnl.report_date"),
    "net_profit":      dict(dash=("net_profit",),                  cube=["canonical_pnl.net_profit_all_channels"],          td="canonical_pnl.report_date"),
    "returns_cancels": dict(dash=("returns_cancels", "total_count"), cube=["commerce_orders.returns_cancels_orders"],        td="commerce_orders.event_date", known_diff="Shopify-only; Amazon returns/cancels excluded"),
    "total_payments":  dict(dash=("total_payments", "total_count"),  cube=["commerce_orders.total_payment_orders"], td="commerce_orders.order_date"),
    "gross_sales":     dict(dash=("gross_sales",),                 cube=["sales_all_channels.gross_sales"],                 td="sales_all_channels.report_date"),
}

MONEY_TOL = 1.0      # rupee tolerance for currency metrics
COUNT_TOL = 0.0      # counts must be exact


def dash_summary(brand: int, start: str, end: str) -> dict:
    js = f"""
require("dotenv").config();
(async () => {{
  try {{
    const a = require("./src/integrations/historicalAnalytics/analytics");
    const d = await a.getHistoricalDashboard({brand}, "{start}", "{end}", {{ skipCache: true }});
    process.stdout.write("JSON_START"+JSON.stringify(d)+"JSON_END");
  }} catch (e) {{ process.stdout.write("ERR"+e.message); }}
  process.exit(0);
}})();
"""
    out = subprocess.run(["node", "-e", js], cwd=str(NODE_BACKEND),
                         capture_output=True, text=True, timeout=180).stdout
    if "JSON_START" not in out:
        raise RuntimeError(f"dashboard call failed: {out[-400:]}")
    blob = out.split("JSON_START", 1)[1].split("JSON_END", 1)[0]
    return json.loads(blob)


def dig(d: dict, path: tuple):
    cur = d
    for k in path:
        cur = cur.get(k, {}) if isinstance(cur, dict) else None
    return cur if not isinstance(cur, dict) else None


def cube_value(cube_url: str, measures: list[str], td: str, brand: int, start: str, end: str) -> float:
    view = measures[0].split(".", 1)[0]
    body = {"query": {"measures": measures,
                      "timeDimensions": [{"dimension": td, "dateRange": [start, end]}],
                      "filters": [{"member": f"{view}.brand_id", "operator": "equals", "values": [str(brand)]}]}}
    req = urllib.request.Request(cube_url.rstrip("/") + "/cubejs-api/v1/load",
                                 data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    data = json.load(urllib.request.urlopen(req, timeout=90)).get("data", [])
    row = data[0] if data else {}
    return round(sum(float(row.get(m) or 0) for m in measures), 2)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--brand", type=int, default=20)
    ap.add_argument("--date", required=True, help="start date YYYY-MM-DD")
    ap.add_argument("--end", default=None, help="end date (defaults to --date)")
    ap.add_argument("--cube", default="http://127.0.0.1:4001")
    args = ap.parse_args()
    start, end = args.date, (args.end or args.date)

    d = dash_summary(args.brand, start, end)
    print(f"\nLIVE parity — brand {args.brand}  {start}..{end}\n")
    print(f"{'metric':<16}{'dashboard':>16}{'cube':>16}{'delta':>12}  status")
    print("-" * 74)
    failed = 0
    for name, cfg in MEASURE_MAP.items():
        dv = dig(d, cfg["dash"])
        dv = float(dv) if dv is not None else float("nan")
        cv = cube_value(args.cube, cfg["cube"], cfg["td"], args.brand, start, end)
        delta = round(cv - dv, 2)
        is_count = name in ("total_orders", "returns_cancels", "total_payments")
        tol = COUNT_TOL if is_count else MONEY_TOL
        ok = abs(delta) <= tol
        tag = "OK" if ok else ("KNOWN" if cfg.get("known_diff") else "FAIL")
        if not ok and tag == "FAIL":
            failed += 1
        note = f"  ({cfg['known_diff']})" if (not ok and cfg.get("known_diff")) else ""
        print(f"{name:<16}{dv:>16,.2f}{cv:>16,.2f}{delta:>12,.2f}  {tag}{note}")
    print("-" * 74)
    print(f"{'FAIL: ' + str(failed) if failed else 'ALL RECONCILED (within tolerance / known-diff)'}\n")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
