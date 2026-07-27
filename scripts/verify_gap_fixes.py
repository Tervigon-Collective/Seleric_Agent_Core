#!/usr/bin/env python
"""Verify remaining gap fixes vs dashboard."""
from __future__ import annotations

import json
import subprocess
import urllib.request
from pathlib import Path

NODE = Path(r"C:\SpacePeppers\SpacePeppers\Seleric_Dashboard\Node-Backend")
CUBE = "http://127.0.0.1:4001/cubejs-api/v1/load"
S, E = "2026-06-01", "2026-06-30"


def node(js: str) -> dict:
    out = subprocess.run(
        ["node", "-e", js], cwd=str(NODE), capture_output=True, text=True, timeout=240
    )
    blob = out.stdout + out.stderr
    if "JSON_START" not in blob:
        raise RuntimeError(blob[-800:])
    return json.loads(blob.split("JSON_START", 1)[1].split("JSON_END", 1)[0])


def cube(measures: list[str], td: str):
    view = measures[0].split(".", 1)[0]
    body = {
        "query": {
            "measures": measures,
            "timeDimensions": [{"dimension": td, "dateRange": [S, E]}],
            "filters": [{"member": f"{view}.brand_id", "operator": "equals", "values": ["20"]}],
        }
    }
    req = urllib.request.Request(
        CUBE, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}
    )
    try:
        r = json.load(urllib.request.urlopen(req, timeout=180))
    except Exception as e:
        return None, str(e)
    if r.get("error"):
        return None, str(r["error"])[:300]
    row = (r.get("data") or [{}])[0]
    return round(sum(float(row.get(m) or 0) for m in measures), 4), None


def main():
    hist = node(
        "require('dotenv').config();(async()=>{"
        "const a=require('./src/integrations/historicalAnalytics/analytics');"
        "const d=await a.getHistoricalDashboard(20,'2026-06-01','2026-06-30',{skipCache:true});"
        "process.stdout.write('JSON_START'+JSON.stringify({"
        "net:d.net_sales,cogs:d.total_cogs,np:d.net_profit,"
        "rc:d.returns_cancels.total_count,ncm:d.new_customer_metrics,ads:d.total_ad_spend"
        "})+'JSON_END');process.exit(0);})();"
    )
    meta = node(
        "require('dotenv').config();(async()=>{"
        "const a=require('./src/integrations/metaAttribution/analytics');"
        "const d=await a.getMetaAttribution(20,'2026-06-01','2026-06-30',{skipCache:true});"
        "const s=d.summary||d;"
        "process.stdout.write('JSON_START'+JSON.stringify({"
        "net:s.net_sales,cogs:s.net_cogs,np:s.net_profit,nroas:s.net_roas,groas:s.gross_roas,be:s.be_roas"
        "})+'JSON_END');process.exit(0);})();"
    )
    goog = node(
        "require('dotenv').config();(async()=>{"
        "const a=require('./src/integrations/googleAttribution/analytics');"
        "const d=await a.getGoogleAttribution(20,'2026-06-01','2026-06-30',{skipCache:true});"
        "const s=d.summary||d;"
        "process.stdout.write('JSON_START'+JSON.stringify({"
        "net:s.net_sales,cogs:s.net_cogs,np:s.net_profit,nroas:s.net_roas"
        "})+'JSON_END');process.exit(0);})();"
    )

    ncm = hist["ncm"]
    ltv = ncm["new_customer_revenue"] / ncm["new_customers"]
    cac = hist["ads"] / ncm["new_customers"]
    ratio = ltv / cac

    rows = [
        ("H.net", hist["net"], ["canonical_pnl.net_sales_all_channels_pnl"], "canonical_pnl.report_date", 1),
        ("H.cogs", hist["cogs"], ["canonical_pnl.total_operating_cost_all_channels"], "canonical_pnl.report_date", 1),
        ("H.np", hist["np"], ["canonical_pnl.net_profit_all_channels"], "canonical_pnl.report_date", 1),
        ("H.rc", hist["rc"], ["returns_cancels_all_channels.returns_cancels"], "returns_cancels_all_channels.report_date", 0),
        ("M.net", meta["net"], ["channel_pnl.meta_net_sales"], "channel_pnl.report_date", 1),
        ("M.cogs", meta["cogs"], ["channel_pnl.meta_net_cogs"], "channel_pnl.report_date", 1),
        ("M.np", meta["np"], ["channel_pnl.meta_net_profit"], "channel_pnl.report_date", 1),
        ("M.nroas", meta["nroas"], ["channel_pnl.meta_net_roas"], "channel_pnl.report_date", 0.02),
        ("G.net", goog["net"], ["channel_pnl.google_net_sales"], "channel_pnl.report_date", 1),
        ("G.cogs", goog["cogs"], ["channel_pnl.google_net_cogs"], "channel_pnl.report_date", 1),
        ("G.np", goog["np"], ["channel_pnl.google_net_profit"], "channel_pnl.report_date", 1),
        ("LTV.n", ncm["new_customers"], ["ltv_cac.new_customers"], "ltv_cac.report_date", 0),
        ("LTV.rev", ncm["new_customer_revenue"], ["ltv_cac.new_customer_revenue"], "ltv_cac.report_date", 1),
        ("LTV.x", ratio, ["ltv_cac.ltv_cac_ratio"], "ltv_cac.report_date", 0.02),
    ]

    print(f"{'metric':<10}{'dashboard':>16}{'cube':>16}{'delta':>12}  status")
    print("-" * 70)
    fail = 0
    for name, dv, ms, td, tol in rows:
        cv, err = cube(ms, td)
        if err:
            print(f"{name:<10}{float(dv):>16.4f}{'ERR':>16}{'':>12}  FAIL  {err[:90]}")
            fail += 1
            continue
        delta = round(cv - float(dv), 4)
        ok = abs(delta) <= tol
        if not ok:
            fail += 1
        print(f"{name:<10}{float(dv):>16.4f}{cv:>16.4f}{delta:>12.4f}  {'OK' if ok else 'FAIL'}")
    print("-" * 70)
    print("FAIL" if fail else "ALL OK", f"({fail} failing)" if fail else "")
    return 1 if fail else 0


if __name__ == "__main__":
    raise SystemExit(main())
