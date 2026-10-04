#!/usr/bin/env python
"""
Attribution <-> Total Orders reconciliation guard (live, cube-internal invariants).

The agent/MCP attribution surface (serve.order_attribution, channel_attribution)
must reconcile to the certified Total Orders population (serve.orders_all_channels)
and match the dashboard channel split. This guards the 2026-07-31 repoint of
serve.order_attribution onto gold.fct_order_attribution (see memory
attribution-spine-wrong-gold-table) against a revert to the credit-table cohort,
which dropped ~155 orders/month and disagreed on channel assignment.

Invariants (brand 20, June 2026 unless overridden):
  I1  order_attribution.attributed_orders   == orders_all_channels.shopify_orders
  I2  sum channel_attribution.orders (all)     == orders_all_channels.orders
  I3  sum channel_attribution shopify channels == orders_all_channels.shopify_orders
  I4  touch_attributed_orders <= attributed_orders  (resolved is a subset)

Usage:
  py -3 scripts/reconcile_attribution_totals.py --brand 20 --start 2026-06-01 --end 2026-06-30
Exit 1 on any failure.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.request

CUBE = "http://127.0.0.1:4001/cubejs-api/v1/load"


def load(query: dict) -> list[dict]:
    req = urllib.request.Request(
        CUBE,
        data=json.dumps({"query": query}).encode(),
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r).get("data", [])


def brand_filter(cube: str, brand: int) -> dict:
    return {"member": f"{cube}.brand_id", "operator": "equals", "values": [str(brand)]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--brand", type=int, default=20)
    ap.add_argument("--start", default="2026-06-01")
    ap.add_argument("--end", default="2026-06-30")
    a = ap.parse_args()
    dr = [a.start, a.end]

    oac = load({
        "measures": ["serve_orders_all_channels.orders", "serve_orders_all_channels.shopify_orders"],
        "timeDimensions": [{"dimension": "serve_orders_all_channels.report_date", "dateRange": dr}],
        "filters": [brand_filter("serve_orders_all_channels", a.brand)],
    })[0]
    total_orders = int(oac["serve_orders_all_channels.orders"])
    shopify_orders = int(oac["serve_orders_all_channels.shopify_orders"])

    oa = load({
        "measures": [
            "serve_order_attribution.attributed_orders",
            "serve_order_attribution.touch_attributed_orders",
        ],
        "timeDimensions": [{"dimension": "serve_order_attribution.order_date", "dateRange": dr}],
        "filters": [brand_filter("serve_order_attribution", a.brand)],
    })[0]
    attributed_orders = int(oa["serve_order_attribution.attributed_orders"])
    touch_attributed = int(oa["serve_order_attribution.touch_attributed_orders"])

    ch = load({
        "measures": ["serve_channel_attribution_daily.orders"],
        "dimensions": ["serve_channel_attribution_daily.channel",
                       "serve_channel_attribution_daily.source_platform"],
        "timeDimensions": [{"dimension": "serve_channel_attribution_daily.report_date", "dateRange": dr}],
        "filters": [brand_filter("serve_channel_attribution_daily", a.brand)],
    })
    ch_all = sum(int(r["serve_channel_attribution_daily.orders"]) for r in ch)
    ch_shopify = sum(
        int(r["serve_channel_attribution_daily.orders"])
        for r in ch
        if r.get("serve_channel_attribution_daily.source_platform") == "shopify"
    )

    checks = [
        ("I1  order_attribution.attributed_orders == orders_all_channels.shopify_orders",
         attributed_orders, shopify_orders),
        ("I2  sum channel_attribution.orders (all)  == orders_all_channels.orders",
         ch_all, total_orders),
        ("I3  sum channel_attribution shopify       == orders_all_channels.shopify_orders",
         ch_shopify, shopify_orders),
    ]

    print(f"\nAttribution <-> Total Orders reconciliation  brand {a.brand}  {a.start}..{a.end}")
    print("-" * 78)
    failed = 0
    for label, got, want in checks:
        ok = got == want
        failed += not ok
        print(f"  [{'OK' if ok else 'FAIL'}] {label}\n         got={got}  want={want}")
    # I4: subset relation
    ok4 = touch_attributed <= attributed_orders
    failed += not ok4
    print(f"  [{'OK' if ok4 else 'FAIL'}] I4  touch_attributed_orders <= attributed_orders"
          f"\n         resolved={touch_attributed}  total={attributed_orders}"
          f"  (rate={touch_attributed / attributed_orders:.4f})")
    print("-" * 78)
    print("ALL RECONCILED" if failed == 0 else f"{failed} INVARIANT(S) FAILED")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
