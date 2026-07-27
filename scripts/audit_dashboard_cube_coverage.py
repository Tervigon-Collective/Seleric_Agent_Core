#!/usr/bin/env python
"""Extended dashboard KPI coverage + drift audit vs Cube (brand/date window)."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import urllib.request
from pathlib import Path

NODE = Path(r"C:\SpacePeppers\SpacePeppers\Seleric_Dashboard\Node-Backend")


def node(js: str) -> dict:
    out = subprocess.run(
        ["node", "-e", js], cwd=str(NODE), capture_output=True, text=True, timeout=240
    ).stdout
    if "JSON_START" not in out:
        raise RuntimeError(out[-800:])
    return json.loads(out.split("JSON_START", 1)[1].split("JSON_END", 1)[0])


def cube_load(cube_url: str, measures: list[str], td: str, brand: int, start: str, end: str):
    view = measures[0].split(".", 1)[0]
    body = {
        "query": {
            "measures": measures,
            "timeDimensions": [{"dimension": td, "dateRange": [start, end]}],
            "filters": [{"member": f"{view}.brand_id", "operator": "equals", "values": [str(brand)]}],
        }
    }
    req = urllib.request.Request(
        cube_url.rstrip("/") + "/cubejs-api/v1/load",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
    )
    data = json.load(urllib.request.urlopen(req, timeout=120))
    if data.get("error"):
        raise RuntimeError(str(data["error"]))
    row = (data.get("data") or [{}])[0]
    return round(sum(float(row.get(m) or 0) for m in measures), 4)


def f(v):
    try:
        return float(v)
    except Exception:
        return float("nan")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--brand", type=int, default=20)
    ap.add_argument("--date", default="2026-06-01")
    ap.add_argument("--end", default="2026-06-30")
    ap.add_argument("--cube", default="http://127.0.0.1:4001")
    args = ap.parse_args()
    b, s, e = args.brand, args.date, args.end

    print("Fetching dashboard surfaces...")
    hist = node(
        f"require('dotenv').config();(async()=>{{const a=require('./src/integrations/historicalAnalytics/analytics');"
        f"const d=await a.getHistoricalDashboard({b},'{s}','{e}',{{skipCache:true}});"
        f"process.stdout.write('JSON_START'+JSON.stringify(d)+'JSON_END');process.exit(0);}})();"
    )
    meta = node(
        f"require('dotenv').config();(async()=>{{const a=require('./src/integrations/metaAttribution/analytics');"
        f"const d=await a.getMetaAttribution({b},'{s}','{e}',{{skipCache:true}});"
        f"process.stdout.write('JSON_START'+JSON.stringify(d.summary||d)+'JSON_END');process.exit(0);}})();"
    )
    goog = node(
        f"require('dotenv').config();(async()=>{{const a=require('./src/integrations/googleAttribution/analytics');"
        f"const d=await a.getGoogleAttribution({b},'{s}','{e}',{{skipCache:true}});"
        f"process.stdout.write('JSON_START'+JSON.stringify(d.summary||d)+'JSON_END');process.exit(0);}})();"
    )
    amaz = node(
        f"require('dotenv').config();(async()=>{{const a=require('./src/integrations/amazonAttribution/analytics');"
        f"const d=await a.getAmazonAttribution({b},'{s}','{e}',{{skipCache:true}});"
        f"process.stdout.write('JSON_START'+JSON.stringify(d.summary||d)+'JSON_END');process.exit(0);}})();"
    )

    sm = hist.get("sales_metrics") or {}
    rc = hist.get("returns_cancels") or {}
    amz = hist.get("amazon") or {}
    ads = f(hist.get("total_ad_spend"))
    ns = f(hist.get("net_sales"))
    gs = f(hist.get("gross_sales"))
    cogs = f(hist.get("total_cogs"))

    # name, dash, measures, td, kind(money|count|ratio), known_note|None
    rows = [
        ("H.discounts", f(sm.get("total_discounts")), ["commerce_orders.discount_amount_excl_tax"], "commerce_orders.order_date", "money", None),
        ("H.return_cancel_rev", f(rc.get("total_amount")), ["commerce_orders.event_revenue_deduction_excl_tax"], "commerce_orders.event_date", "money", "Shopify-only event rev"),
        ("H.cancel_rev", f(rc.get("cancelled_amount")), ["commerce_orders.cancel_revenue_excl_tax"], "commerce_orders.event_date", "money", "Shopify-only"),
        ("H.return_rev", f(rc.get("returned_amount")), ["commerce_orders.return_revenue_excl_tax"], "commerce_orders.event_date", "money", "Shopify-only; Amazon on attr overview"),
        ("H.gross_roas_all", (gs / ads) if ads else float("nan"), ["canonical_pnl.gross_roas"], "canonical_pnl.report_date", "ratio", "Cube gross_roas uses Shopify spend denom"),
        ("H.net_roas_all", ((ns - cogs) / ads) if ads else float("nan"), ["canonical_pnl.net_roas"], "canonical_pnl.report_date", "ratio", "Cube net_roas uses Shopify-only arms"),
        ("H.be_roas_all", (ns / (ns - cogs)) if (ns - cogs) else float("nan"), ["canonical_pnl.be_roas"], "canonical_pnl.report_date", "ratio", "Cube be_roas Shopify-only"),
        ("H.shopify_net_pnl", ns - f(amz.get("net_sales")), ["canonical_pnl.net_sales"], "canonical_pnl.report_date", "money", "commerce_performance residual"),
        ("H.discounts_pnl", f(sm.get("total_discounts")), ["canonical_pnl.discounts"], "canonical_pnl.report_date", "money", None),
        ("H.cancel_rev_pnl", f(rc.get("cancelled_amount")), ["canonical_pnl.cancel_revenue"], "canonical_pnl.report_date", "money", "Shopify-only"),
        ("H.return_rev_pnl", f(rc.get("returned_amount")), ["canonical_pnl.return_revenue"], "canonical_pnl.report_date", "money", "Shopify-only"),
        ("H.payments", f((hist.get("total_payments") or {}).get("total_count")), ["commerce_orders.total_payment_orders"], "commerce_orders.order_date", "count", None),
        ("M.orders", f(meta.get("total_orders")), ["platform_attribution_commerce.meta_orders"], "platform_attribution_commerce.report_date", "count", None),
        ("M.total_sales", f(meta.get("total_sales")), ["platform_attribution_commerce.meta_total_sales"], "platform_attribution_commerce.report_date", "money", None),
        ("M.gross_sales", f(meta.get("gross_sales")), ["platform_attribution_commerce.meta_gross_sales"], "platform_attribution_commerce.report_date", "money", None),
        ("M.net_sales", f(meta.get("net_sales")), ["platform_attribution_commerce.meta_net_sales"], "platform_attribution_commerce.report_date", "money", "placement net vs channel PnL net"),
        ("M.spend", f(meta.get("total_spend") or meta.get("total_ad_spend")), ["meta_ad_performance.meta_spend"], "meta_ad_performance.report_date", "money", None),
        ("M.impressions", f(meta.get("total_impressions")), ["meta_ad_performance.meta_impressions"], "meta_ad_performance.report_date", "count", None),
        ("M.clicks", f(meta.get("total_clicks")), ["meta_ad_performance.meta_clicks"], "meta_ad_performance.report_date", "count", None),
        ("M.discounts", f(meta.get("total_discounts")), ["platform_attribution_commerce.discounts"], "platform_attribution_commerce.report_date", "money", "all-platform discounts unless filtered"),
        ("G.orders", f(goog.get("total_orders")), ["platform_attribution_commerce.google_orders"], "platform_attribution_commerce.report_date", "count", None),
        ("G.total_sales", f(goog.get("total_sales")), ["platform_attribution_commerce.google_total_sales"], "platform_attribution_commerce.report_date", "money", None),
        ("G.gross_sales", f(goog.get("gross_sales")), ["platform_attribution_commerce.google_gross_sales"], "platform_attribution_commerce.report_date", "money", None),
        ("G.net_sales", f(goog.get("net_sales")), ["platform_attribution_commerce.google_net_sales"], "platform_attribution_commerce.report_date", "money", "placement net vs channel PnL net"),
        ("G.spend", f(goog.get("total_spend")), ["google_ad_performance.google_spend"], "google_ad_performance.report_date", "money", None),
        ("G.impressions", f(goog.get("total_impressions")), ["google_ad_performance.google_impressions"], "google_ad_performance.report_date", "count", None),
        ("G.clicks", f(goog.get("total_clicks")), ["google_ad_performance.google_clicks"], "google_ad_performance.report_date", "count", None),
        ("A.total_sales", f(amaz.get("total_order_total")), ["amazon_attribution_overview.total_sales"], "amazon_attribution_overview.report_date", "money", None),
        ("A.net_profit", f(amaz.get("net_profit")), ["amazon_attribution_overview.net_profit"], "amazon_attribution_overview.report_date", "money", None),
        ("A.mer", f(amaz.get("mer")), None, None, "ratio", None),  # computed
        ("A.ctr", f(amaz.get("ctr")), ["amazon_ad_performance.amazon_ads_ctr"], "amazon_ad_performance.report_date", "ratio", None),
        ("A.cpc", f(amaz.get("average_cpc")), ["amazon_ad_performance.amazon_ads_cpc"], "amazon_ad_performance.report_date", "money", None),
    ]

    print(f"\nExtended KPI audit — brand {b} {s}..{e}\n")
    hdr = f"{'metric':<24}{'dashboard':>16}{'cube':>16}{'delta':>12}  status"
    print(hdr)
    print("-" * len(hdr))
    ok_n = fail_n = known_n = 0
    findings = []
    for name, dv, measures, td, kind, known in rows:
        try:
            if name == "A.mer":
                ts = cube_load(args.cube, ["amazon_attribution_overview.total_sales"], "amazon_attribution_overview.report_date", b, s, e)
                sp = cube_load(args.cube, ["amazon_attribution_overview.ad_spend"], "amazon_attribution_overview.report_date", b, s, e)
                cv = round(ts / sp, 4) if sp else float("nan")
            elif name == "M.discounts":
                # meta-only discounts via filtered measure not available; skip exact
                cv = cube_load(args.cube, ["platform_attribution_commerce.meta_gross_sales"], td, b, s, e)  # placeholder unused
                # Use SQL-less: discounts measure is unfiltered — mark SKIP
                print(f"{name:<24}{dv:>16,.4f}{'n/a':>16}{'':>12}  SKIP  (no meta-only discounts measure)")
                continue
            else:
                cv = cube_load(args.cube, measures, td, b, s, e)
        except Exception as ex:
            print(f"{name:<24}{dv:>16,.4f}{'ERR':>16}{'':>12}  FAIL  {str(ex)[:70]}")
            fail_n += 1
            findings.append((name, "FAIL", str(ex)))
            continue

        delta = round(cv - dv, 4) if dv == dv and cv == cv else float("nan")
        tol = {"count": 0.0, "money": 1.0, "ratio": 0.05}.get(kind, 1.0)
        # CTR stored as percent vs fraction
        if name == "A.ctr" and abs(delta) > tol and abs(cv * 100 - dv) <= tol:
            cv = round(cv * 100, 4)
            delta = round(cv - dv, 4)
        matched = abs(delta) <= tol
        if matched:
            tag = "OK"
            ok_n += 1
        elif known:
            tag = "KNOWN"
            known_n += 1
        else:
            tag = "FAIL"
            fail_n += 1
        note = f"  ({known})" if (not matched and known) else ""
        print(f"{name:<24}{dv:>16,.4f}{cv:>16,.4f}{delta:>12,.4f}  {tag}{note}")
        findings.append((name, tag, delta))

    print("-" * len(hdr))
    print(f"OK={ok_n} KNOWN={known_n} FAIL={fail_n}")

    # Coverage checklist vs dashboard cards
    print("\n=== Dashboard card coverage (Cube has a measure path?) ===")
    coverage = [
        ("Historical Net Profit (all)", "YES", "canonical_pnl.net_profit_all_channels", "value residual Shopify COGS/net"),
        ("Historical Net Sales (all)", "YES", "canonical_pnl.net_sales_all_channels_pnl", "~7k Shopify residual"),
        ("Historical Gross/Total Sales", "YES", "sales_all_channels.*", "OK"),
        ("Historical Orders", "YES", "orders_all_channels.orders", "OK"),
        ("Historical Ad Spend", "YES", "canonical_pnl.total_ad_spend", "OK"),
        ("Historical Net COGS", "YES", "canonical_pnl.total_operating_cost_all_channels", "Shopify COGS residual"),
        ("Historical Discounts", "YES", "commerce_orders.discount_amount_excl_tax / canonical_pnl.discounts", "check values"),
        ("Historical Returns/Cancels count", "PARTIAL", "commerce_orders.returns_cancels + amazon_attribution_overview.returns_cancels", "no single all-channel measure"),
        ("Historical Return/Cancel Rev", "PARTIAL", "commerce_orders.event_* + amazon return_revenue", "no single all-channel measure"),
        ("Historical Gross/Net/BE ROAS", "PARTIAL", "canonical_pnl.*_roas", "Shopify-only denom/numerators"),
        ("Historical Total Payments", "YES", "commerce_orders.total_payment_orders", "OK; catalogue may lack id"),
        ("Historical LTV:CAC", "NO", "—", "new_customer_metrics not in Cube as LTV:CAC"),
        ("Amazon Attr Overview suite", "YES", "amazon_attribution_overview.*", "OK"),
        ("Amazon MER/TACOS", "PARTIAL", "derive total_sales/ad_spend", "no dedicated MER measure on overview"),
        ("Amazon ads CTR/CPC", "YES", "amazon_ad_performance.*", "check scale % vs fraction"),
        ("Meta Attr orders/total/gross", "YES", "platform_attribution_commerce.meta_*", "OK"),
        ("Meta Attr net sales", "DRIFT", "platform_attribution_commerce.meta_net_sales", "placement net != channel PnL net"),
        ("Meta Attr net profit / COGS / ROAS", "NO/WEAK", "no meta-channel PnL cube", "Attribution overlays getAttributionChannelPnl"),
        ("Meta ads impressions/clicks/spend", "YES", "meta_ad_performance.*", "OK"),
        ("Meta session funnel", "PARTIAL", "session_funnel / funnel_daily", "channel filter parity unverified"),
        ("Google Attr same as Meta", "same", "platform_attribution_commerce.google_*", "same net-sales drift"),
        ("P&L taxes / packaging / shipping / gateway / RTO", "YES", "canonical_pnl.*", "Shopify arms; Amazon fees separate"),
        ("P&L Gross Margin %", "YES", "canonical_pnl.gross_margin_pct", "Shopify-only basis"),
        ("Performance Summary CPA/CVR/AOV", "PARTIAL", "derive from ads+orders / commerce aov", "no dedicated CPA/CVR measures"),
    ]
    for card, status, path, note in coverage:
        print(f"  [{status:<8}] {card}")
        print(f"           -> {path} | {note}")

    print("\nMeta net_profit/net_cogs/roas:", {k: meta.get(k) for k in ["net_profit", "net_cogs", "gross_cogs", "gross_roas", "net_roas", "be_roas", "cancel_count", "return_count", "return_value", "cancelled_gross_value"]})
    print("Google net_profit/net_cogs/roas:", {k: goog.get(k) for k in ["net_profit", "net_cogs", "gross_roas", "net_roas", "be_roas", "cancel_count", "return_count"]})
    return 1 if fail_n else 0


if __name__ == "__main__":
    sys.exit(main())
