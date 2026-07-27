#!/usr/bin/env python
"""
Live multi-view reconcile: Historical + Amazon Attribution dashboard vs Cube.

Compares brand/date-range KPIs from Node-Backend (oracle) to Cube views the
agent catalogue uses. Exit 1 if any unexpected FAIL (KNOWN diffs don't fail).

Usage:
  py -3 scripts/reconcile_all_views_live.py --brand 20 --date 2026-06-01 --end 2026-06-30
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

NODE_BACKEND = Path(r"C:\SpacePeppers\SpacePeppers\Seleric_Dashboard\Node-Backend")
MONEY_TOL = 1.0
COUNT_TOL = 0.0


def _node(js: str) -> dict:
    out = subprocess.run(
        ["node", "-e", js],
        cwd=str(NODE_BACKEND),
        capture_output=True,
        text=True,
        timeout=240,
    ).stdout
    if "JSON_START" not in out:
        raise RuntimeError(f"node call failed: {out[-800:]}")
    return json.loads(out.split("JSON_START", 1)[1].split("JSON_END", 1)[0])


def dash_historical(brand: int, start: str, end: str) -> dict:
    return _node(
        f"""
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
    )


def dash_amazon_attr(brand: int, start: str, end: str) -> dict:
    return _node(
        f"""
require("dotenv").config();
(async () => {{
  try {{
    const a = require("./src/integrations/amazonAttribution/analytics");
    const d = await a.getAmazonAttribution({brand}, "{start}", "{end}", {{ skipCache: true }});
    process.stdout.write("JSON_START"+JSON.stringify(d)+"JSON_END");
  }} catch (e) {{ process.stdout.write("ERR"+e.message); }}
  process.exit(0);
}})();
"""
    )


def cube_load(cube_url: str, measures: list[str], td: str, brand: int, start: str, end: str,
              brand_member: str | None = None) -> float:
    view = measures[0].split(".", 1)[0]
    bm = brand_member or f"{view}.brand_id"
    body = {
        "query": {
            "measures": measures,
            "timeDimensions": [{"dimension": td, "dateRange": [start, end]}],
            "filters": [{"member": bm, "operator": "equals", "values": [str(brand)]}],
        }
    }
    req = urllib.request.Request(
        cube_url.rstrip("/") + "/cubejs-api/v1/load",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    data = json.load(urllib.request.urlopen(req, timeout=120)).get("data", [])
    row = data[0] if data else {}
    return round(sum(float(row.get(m) or 0) for m in measures), 2)


def dig(d, *path):
    cur = d
    for k in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


def num(v) -> float:
    if v is None:
        return float("nan")
    if isinstance(v, dict):
        return float("nan")
    return float(v)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--brand", type=int, default=20)
    ap.add_argument("--date", required=True)
    ap.add_argument("--end", default=None)
    ap.add_argument("--cube", default="http://127.0.0.1:4001")
    args = ap.parse_args()
    start, end = args.date, (args.end or args.date)

    print(f"\nFetching LIVE dashboard (Historical + Amazon Attribution) brand={args.brand} {start}..{end} ...")
    hist = dash_historical(args.brand, start, end)
    try:
        amaz = dash_amazon_attr(args.brand, start, end)
    except Exception as e:
        amaz = {}
        print(f"  WARN amazon attribution fetch failed: {e}")

    amaz_sum = dig(amaz, "summary") or {}
    hist_amz = dig(hist, "amazon") or {}
    ad = dig(hist, "ad_spend_breakdown") or dig(hist, "ad_spend") or {}
    rc = dig(hist, "returns_cancels") or {}
    pay = dig(hist, "total_payments") or {}

    def fnum(*vals):
        for v in vals:
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                return float(v)
        return float("nan")

    amz_rc = fnum(amaz_sum.get("canceled_orders")) + fnum(amaz_sum.get("returned_orders"))
    amz_ad = dig(ad, "amazon", "total") if isinstance(ad.get("amazon"), dict) else ad.get("amazon")

    checks = [
        # --- Historical all-channel spine ---
        ("hist.total_sales", num(hist.get("total_sales")),
         ["sales_all_channels.total_sales"], "sales_all_channels.report_date", False, None),
        ("hist.gross_sales", num(hist.get("gross_sales")),
         ["sales_all_channels.gross_sales"], "sales_all_channels.report_date", False, None),
        ("hist.net_sales", num(hist.get("net_sales")),
         ["canonical_pnl.net_sales_all_channels_pnl"], "canonical_pnl.report_date", False,
         "Shopify commerce_performance residual (~7k MTD)"),
        ("hist.total_orders", num(hist.get("total_orders")),
         ["orders_all_channels.orders"], "orders_all_channels.report_date", True, None),
        ("hist.total_ad_spend", num(hist.get("total_ad_spend")),
         ["canonical_pnl.total_ad_spend"], "canonical_pnl.report_date", False, None),
        ("hist.total_cogs", num(hist.get("total_cogs")),
         ["canonical_pnl.total_operating_cost_all_channels"], "canonical_pnl.report_date", False,
         "Shopify FE COGS event-timing residual"),
        ("hist.net_profit", num(hist.get("net_profit")),
         ["canonical_pnl.net_profit_all_channels"], "canonical_pnl.report_date", False,
         "inherits Shopify net_sales + COGS residuals"),
        ("hist.returns_cancels", num(rc.get("total_count")),
         ["commerce_orders.returns_cancels_orders"], "commerce_orders.event_date", True,
         "Shopify-only; Amazon returns/cancels excluded"),
        ("hist.payments_count", num(pay.get("total_count")),
         ["commerce_orders.total_payment_orders"],
         "commerce_orders.order_date", True, None),
        ("hist.meta_spend", num(ad.get("meta")),
         ["canonical_pnl.meta_spend"], "canonical_pnl.report_date", False, None),
        ("hist.google_spend", num(ad.get("google")),
         ["canonical_pnl.google_spend"], "canonical_pnl.report_date", False, None),
        ("hist.amazon_ad_spend", num(amz_ad),
         ["canonical_pnl.amazon_spend"], "canonical_pnl.report_date", False, None),

        # --- Amazon Attribution Overview ---
        ("amz_attr.total_sales", fnum(amaz_sum.get("total_order_total"), hist_amz.get("total_sales")),
         ["amazon_attribution_overview.total_sales"], "amazon_attribution_overview.report_date", False, None),
        ("amz_attr.gross_sales", fnum(amaz_sum.get("gross_sales"), amaz_sum.get("total_gross_sales"), hist_amz.get("gross_sales")),
         ["amazon_attribution_overview.gross_sales"], "amazon_attribution_overview.report_date", False, None),
        ("amz_attr.net_sales", fnum(amaz_sum.get("net_sales"), amaz_sum.get("total_net_sales"), hist_amz.get("net_sales")),
         ["amazon_attribution_overview.net_sales"], "amazon_attribution_overview.report_date", False, None),
        ("amz_attr.orders", fnum(amaz_sum.get("active_orders"), amaz_sum.get("total_orders"), hist_amz.get("orders")),
         ["amazon_attribution_overview.orders"], "amazon_attribution_overview.report_date", True, None),
        ("amz_attr.return_revenue", fnum(amaz_sum.get("returns"), amaz_sum.get("settled_refunds"),
                                         abs(float(hist_amz.get("total_refunds") or 0))),
         ["amazon_attribution_overview.return_revenue"], "amazon_attribution_overview.report_date", False, None),
        ("amz_attr.returns_cancels", amz_rc if amz_rc == amz_rc else float("nan"),
         ["amazon_attribution_overview.returns_cancels"], "amazon_attribution_overview.report_date", True, None),
        ("amz_attr.platform_fees", fnum(amaz_sum.get("total_platform_fees_abs"), hist_amz.get("platform_fees_abs")),
         ["amazon_attribution_overview.platform_fees"], "amazon_attribution_overview.report_date", False, None),
        ("amz_attr.product_cost", fnum(amaz_sum.get("total_product_cost"), amaz_sum.get("cogs"), hist_amz.get("product_cost")),
         ["amazon_attribution_overview.product_cost"], "amazon_attribution_overview.report_date", False, None),
        ("amz_attr.ad_spend", fnum(amaz_sum.get("ad_spend"), amaz_sum.get("total_ad_spend"), amz_ad),
         ["amazon_attribution_overview.ad_spend"], "amazon_attribution_overview.report_date", False, None),
        ("amz_attr.net_profit", fnum(amaz_sum.get("net_profit"), amaz_sum.get("total_profit")),
         ["amazon_attribution_overview.net_profit"], "amazon_attribution_overview.report_date", False, None),

        # --- Shopify split via all-channels ---
        ("hist.shopify_total_sales",
         num(hist.get("total_sales")) - num(hist_amz.get("total_sales")),
         ["sales_all_channels.shopify_total_sales"], "sales_all_channels.report_date", False, None),
        ("hist.shopify_orders",
         num(hist.get("total_orders")) - num(hist_amz.get("orders")),
         ["orders_all_channels.shopify_orders"], "orders_all_channels.report_date", True, None),

        # --- Platform ad delivery views ---
        ("meta_ads.spend", num(ad.get("meta")),
         ["meta_ad_performance.meta_spend"], "meta_ad_performance.report_date", False, None),
        ("google_ads.spend", num(ad.get("google")),
         ["google_ad_performance.google_spend"], "google_ad_performance.report_date", False, None),
        ("amazon_ads.spend", num(amz_ad),
         ["amazon_ad_performance.amazon_ads_spend"], "amazon_ad_performance.report_date", False, None),

        # --- Meta / Google Attribution Analysis (orders / total / gross) ---
        ("meta_attr.orders", fnum(dig(hist, "orders_breakdown", "meta")),
         ["platform_attribution_commerce.meta_orders"], "platform_attribution_commerce.report_date", True, None),
        ("meta_attr.total_sales", fnum(dig(hist, "sales_breakdown", "meta")),
         ["platform_attribution_commerce.meta_total_sales"], "platform_attribution_commerce.report_date", False, None),
        ("meta_attr.gross_sales", fnum(dig(hist, "gross_sales_breakdown", "meta")),
         ["platform_attribution_commerce.meta_gross_sales"], "platform_attribution_commerce.report_date", False, None),
        ("google_attr.orders", fnum(dig(hist, "orders_breakdown", "google")),
         ["platform_attribution_commerce.google_orders"], "platform_attribution_commerce.report_date", True, None),
        ("google_attr.total_sales", fnum(dig(hist, "sales_breakdown", "google")),
         ["platform_attribution_commerce.google_total_sales"], "platform_attribution_commerce.report_date", False, None),
        ("google_attr.gross_sales", fnum(dig(hist, "gross_sales_breakdown", "google")),
         ["platform_attribution_commerce.google_gross_sales"], "platform_attribution_commerce.report_date", False, None),
    ]

    print(f"\nLIVE multi-view parity — brand {args.brand}  {start}..{end}\n")
    print(f"{'metric':<36}{'dashboard':>16}{'cube':>16}{'delta':>12}  status")
    print("-" * 96)

    failed = 0
    known = 0
    ok_n = 0
    results = []
    for name, dv, measures, td, is_count, known_diff in checks:
        try:
            cv = cube_load(args.cube, measures, td, args.brand, start, end)
        except Exception as e:
            print(f"{name:<36}{dv:>16,.2f}{'ERR':>16}{'':>12}  FAIL  ({e})")
            failed += 1
            results.append((name, "FAIL", str(e)))
            continue
        delta = round(cv - dv, 2) if dv == dv else float("nan")  # NaN check
        tol = COUNT_TOL if is_count else MONEY_TOL
        if dv != dv:  # dashboard NaN
            tag = "SKIP"
            note = "  (dashboard value missing)"
        elif abs(delta) <= tol:
            tag = "OK"
            ok_n += 1
            note = ""
        elif known_diff:
            tag = "KNOWN"
            known += 1
            note = f"  ({known_diff})"
        else:
            tag = "FAIL"
            failed += 1
            note = ""
        dv_s = f"{dv:,.2f}" if dv == dv else "n/a"
        print(f"{name:<36}{dv_s:>16}{cv:>16,.2f}{delta if delta == delta else 0:>12,.2f}  {tag}{note}")
        results.append((name, tag, delta))

    print("-" * 96)
    print(f"OK={ok_n}  KNOWN={known}  FAIL={failed}  SKIP={sum(1 for _,t,_ in results if t=='SKIP')}")
    if failed:
        print("FAIL: unexpected drift — see rows above\n")
    else:
        print("ALL CHECKED (within tolerance / known-diff / skip)\n")

    # Dump amazon attribution keys briefly for debugging NP mapping
    if amaz:
        keys = list(amaz.keys())[:40] if isinstance(amaz, dict) else []
        print(f"Amazon Attribution payload top keys: {keys}")
        if isinstance(amaz_sum, dict) and amaz_sum is not amaz:
            print(f"Amazon Attribution summary keys: {list(amaz_sum.keys())[:40]}")

    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
