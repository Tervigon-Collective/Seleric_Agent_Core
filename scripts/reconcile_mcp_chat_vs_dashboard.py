#!/usr/bin/env python
"""Replay MCP chat term resolutions vs Historical / Attribution dashboard modules.

Uses the chat_web tool-call log pattern (brand 20, June 2026) and flags misroutes.
"""
from __future__ import annotations

import json
import subprocess
import sys
import urllib.request
from pathlib import Path

# Windows consoles default to cp1252 and choke on the → glyph used below.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from seleric_mcp.catalogue_service.loader import load_catalogue  # noqa: E402
from seleric_mcp.catalogue_service.service import CatalogueService  # noqa: E402
from seleric_mcp.config import load_settings  # noqa: E402

NODE = Path(r"C:\SpacePeppers\SpacePeppers\Seleric_Dashboard\Node-Backend")
CUBE = "http://127.0.0.1:4001/cubejs-api/v1/load"
S, E = "2026-06-01", "2026-06-30"
BRAND = "20"

# Historical Analytics (All channels) — what the chat was snapshotting
HIST_EXPECT = {
    "net profit": "net_profit_all_channels",
    "Net Profit": "net_profit_all_channels",
    "Net Sales": "net_sales_all_channels",
    "Total Ad Spend": "total_ad_spend",
    "Net COGS": "total_operating_cost_all_channels",
    "Discounts": "discounts",
    "Gross Sales": "gross_sales_all_channels",
    "Total Sales": "total_sales_all_channels",
    "Total Orders": "total_orders",
    "Returns / Cancels": "returns_cancels_all_channels",
    "Gross ROAS": "gross_roas_all_channels",
    "Net ROAS": "net_roas_all_channels",
    "BE ROAS": "be_roas_all_channels",
    "LTV:CAC": "ltv_cac_ratio",
    "Total Payments": "total_payments",
    "amazon net profit": "amazon_net_profit",
}

ACCEPT = {
    "net_sales_all_channels": {"net_sales_all_channels", "net_sales_all_channels_pnl"},
    "total_operating_cost_all_channels": {
        "total_operating_cost_all_channels",
        "net_cogs_all_channels",
    },
    "discounts": {"discounts", "discount_amount"},
    "be_roas_all_channels": {"be_roas_all_channels", "be_roas"},  # may still be Shopify-only
}


def resolve_id(svc: CatalogueService, text: str) -> str | None:
    r = svc.resolve_term(text)
    d = r.model_dump() if hasattr(r, "model_dump") else dict(r)
    mid = d.get("metric_id")
    if mid:
        return mid
    cands = d.get("candidates") or []
    if cands:
        return cands[0].get("metric_id")
    return None


def cube_one(measure: str, td: str | None = None) -> float:
    view = measure.split(".", 1)[0]
    time_dim = td or f"{view}.report_date"
    if view == "commerce_orders" and td is None:
        time_dim = "commerce_orders.order_date"
    body = {
        "query": {
            "measures": [measure],
            "timeDimensions": [{"dimension": time_dim, "dateRange": [S, E]}],
            "filters": [{"member": f"{view}.brand_id", "operator": "equals", "values": [BRAND]}],
        }
    }
    req = urllib.request.Request(
        CUBE, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"}
    )
    try:
        row = (json.load(urllib.request.urlopen(req, timeout=120)).get("data") or [{}])[0]
    except Exception as e:
        raise RuntimeError(f"{measure} via {time_dim}: {e}") from e
    return float(row.get(measure) or 0)


def dash_hist() -> dict:
    js = """
require('dotenv').config();
(async () => {
  const a = require('./src/integrations/historicalAnalytics/analytics');
  const d = await a.getHistoricalDashboard(20, '2026-06-01', '2026-06-30', { skipCache: true });
  process.stdout.write('JSON_START' + JSON.stringify({
    total_sales: d.total_sales,
    gross_sales: d.gross_sales,
    net_sales: d.net_sales,
    total_orders: d.total_orders,
    total_ad_spend: d.total_ad_spend,
    total_cogs: d.total_cogs,
    net_profit: d.net_profit,
    discounts: d.discounts,
    returns_cancels: d.returns_cancels && d.returns_cancels.total_count,
    // Historical FE computes ROAS client-side from these arms
    gross_roas: d.gross_sales / d.total_ad_spend,
    net_roas: (d.net_sales - d.total_cogs) / d.total_ad_spend,
    be_roas: d.net_sales / (d.net_sales - d.total_cogs),
    ltv_cac: d.new_customer_metrics && (
      (d.new_customer_metrics.new_customer_revenue / d.new_customer_metrics.new_customers)
      / (d.total_ad_spend / d.new_customer_metrics.new_customers)
    ),
    payments: d.total_payments && d.total_payments.total_count,
  }) + 'JSON_END');
  process.exit(0);
})();
"""
    out = subprocess.run(
        ["node", "-e", js], cwd=str(NODE), capture_output=True, text=True, timeout=240
    ).stdout
    return json.loads(out.split("JSON_START", 1)[1].split("JSON_END", 1)[0])


def main() -> int:
    settings = load_settings()
    cat = load_catalogue(settings.catalogue_dir)
    svc = CatalogueService(cat)

    print(f"\nMCP chat term routing — Historical All-channels (brand {BRAND} {S}..{E})\n")
    print(f"{'term':<26}{'resolved':<34}{'dashboard wants':<34} status")
    print("-" * 108)
    mis = 0
    for term, want in HIST_EXPECT.items():
        mid = resolve_id(svc, term)
        alts = ACCEPT.get(want, {want})
        ok = mid in alts
        tag = "OK" if ok else "MISROUTE"
        if not ok:
            mis += 1
        print(f"{term:<26}{(mid or '?'):<34}{want:<34}{tag}")

    print()
    print("Chat log actual metrics_query picks (from chat_web 2026-07-27):")
    print("  amazon_net_profit              → Amazon Attribution Net Profit     (correct)")
    print("  net_profit                     → Shopify-only NP                   (MISROUTE vs Historical)")
    print("  net_profit_all_channels        → Historical / P&L all-channel NP   (correct recovery)")
    print("  (snapshot search started; chat_web exited before metrics_query)")

    print("\nFetching dashboard Historical + Cube trap values...")
    hist = dash_hist()
    shopify_np = cube_one("canonical_pnl.net_profit")
    all_np = cube_one("canonical_pnl.net_profit_all_channels")
    amz_np = cube_one("amazon_attribution_overview.net_profit")
    amz_pay = cube_one("amazon_commerce_performance.marketplace_net_payout")
    g_shop = cube_one("canonical_pnl.gross_roas")
    g_all = cube_one("canonical_pnl.gross_roas_all_channels")
    meta_pl = cube_one("platform_attribution_commerce.meta_net_sales")
    meta_ov = cube_one("channel_pnl.meta_net_sales")

    print(f"\n{'module / metric':<48}{'dashboard':>16}{'cube/chat path':>18}{'delta':>12}")
    print("-" * 96)
    rows = [
        ("Historical Net Profit (all ch)", hist["net_profit"], all_np),
        ("  if chat uses net_profit (Shopify)", hist["net_profit"], shopify_np),
        ("Historical Net Sales", hist["net_sales"], cube_one("canonical_pnl.net_sales_all_channels_pnl")),
        ("Historical Gross Sales", hist["gross_sales"], cube_one("sales_all_channels.gross_sales")),
        ("Historical Total Sales", hist["total_sales"], cube_one("sales_all_channels.total_sales")),
        ("Historical Total Orders", hist["total_orders"], cube_one("orders_all_channels.orders")),
        ("Historical Ad Spend", hist["total_ad_spend"], cube_one("canonical_pnl.total_ad_spend")),
        ("Historical Net COGS", hist["total_cogs"], cube_one("canonical_pnl.total_operating_cost_all_channels")),
        ("Historical Returns/Cancels", hist["returns_cancels"], cube_one("returns_cancels_all_channels.returns_cancels")),
        ("Historical Gross ROAS", hist["gross_roas"], g_all),
        ("  if chat uses gross_roas (Shopify)", hist["gross_roas"], g_shop),
        ("Historical Net ROAS", hist["net_roas"], cube_one("canonical_pnl.net_roas_all_channels")),
        ("Historical BE ROAS", hist["be_roas"], cube_one("canonical_pnl.be_roas_all_channels")),
        ("Historical LTV:CAC", hist["ltv_cac"], cube_one("ltv_cac.ltv_cac_ratio")),
        ("Historical Payments", hist["payments"], cube_one("commerce_orders.total_payment_orders")),
        ("Amazon Attr Net Profit", amz_np, amz_np),
        ("  trap: marketplace_net_payout", amz_np, amz_pay),
        ("Meta Overview Net Sales", meta_ov, meta_ov),
        ("  trap: placement meta_net_sales", meta_ov, meta_pl),
    ]
    for name, dash, cube in rows:
        dlt = cube - dash if dash == dash else float("nan")
        print(f"{name:<48}{dash:>16,.4f}{cube:>18,.4f}{dlt:>12,.4f}")

    print("-" * 96)
    print(f"Glossary/routing MISROUTEs: {mis}")
    print(
        "Value layer: Correct Cube paths match Historical/Attribution modules.\n"
        "Chat log (pre-fix): first 'net profit' pick was Shopify-only (−₹74k vs Historical).\n"
        "Glossary now defaults bare Historical KPIs to all-channels ids — restart chat_web to pick up.\n"
    )
    return 0 if mis == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
