# Dashboard ↔ Cube coverage matrix (brand 20, June 2026 live)

Audited 2026-07-27. Cube = agent semantic layer; Dashboard = Node-Backend oracle.

## Legend
| Tag | Meaning |
|---|---|
| **OK** | Value matches (≤ ₹1 / exact count / ≤ 0.05 ratio) |
| **NEAR** | Same definition; small residual (< ₹5k or < 0.5%) |
| **PARTIAL** | Measure exists but scope/axis differs — use noted path |
| **DRIFT** | Wrong meaning or large value gap |
| **MISSING** | No Cube measure / catalogue id yet |

---

## Historical Analytics (All channels)

| Dashboard card | Cube path | Status | Notes |
|---|---|---|---|
| Total Sales | `sales_all_channels.total_sales` | **OK** | |
| Gross Sales | `sales_all_channels.gross_sales` | **OK** | |
| Net Sales | `canonical_pnl.net_sales_all_channels_pnl` | **NEAR** | −₹6.9k Shopify `commerce_performance` residual |
| Total Orders | `orders_all_channels.orders` | **OK** | |
| Total Ad Spend | `canonical_pnl.total_ad_spend` | **OK** | Meta+Google+Amazon |
| Net COGS / TOC | `canonical_pnl.total_operating_cost_all_channels` | **NEAR** | +₹15.1k Shopify FE COGS residual |
| Net Profit | `canonical_pnl.net_profit_all_channels` | **NEAR** | inherits net+COGS residuals (−₹22k) |
| Discounts | `commerce_orders.discount_amount_excl_tax` / `canonical_pnl.discounts` | **OK** | Shopify |
| Returns/Cancels count | `commerce_orders.returns_cancels_orders` + `amazon_attribution_overview.returns_cancels` | **PARTIAL** | No single all-channel measure; sum = 270+24=294 |
| Return/Cancel revenue | `commerce_orders.event_*` + `amazon_attribution_overview.return_revenue` | **PARTIAL** | Shopify event + Amazon delivery-date |
| Gross ROAS | `canonical_pnl.gross_roas_all_channels` | **OK** | New all-channel measure |
| Net ROAS | `canonical_pnl.net_roas_all_channels` | **NEAR** | inherits P&L residuals |
| BE ROAS | `canonical_pnl.be_roas_all_channels` | **NEAR** | |
| Total Payments | `commerce_orders.total_payment_orders` | **OK** | Catalogue id `total_payments` |
| LTV:CAC | — | **MISSING** | Needs new-customer revenue + spend cube |

Shopify-only ROAS (`canonical_pnl.gross_roas` / `net_roas` / `be_roas`) remain for Shopify cards.

---

## Amazon Attribution Overview

| Card | Cube path | Status |
|---|---|---|
| Total / Gross / Net Sales | `amazon_attribution_overview.*` | **OK** |
| Orders / Returns/Cancels / Refunds | same | **OK** |
| Fees / Product Cost / Ad Spend / Net Profit | same | **OK** |
| MER / TACOS | `amazon_attribution_overview.mer` / `.tacos` | **OK** |
| CTR / CPC | `amazon_ad_performance.*` | **OK** |
| Settlement Net Payout | `amazon_commerce_performance.marketplace_net_payout` | **OK meaning** — **not** Net Profit |

---

## Meta / Google Attribution Overview

| Card | Cube path | Status | Notes |
|---|---|---|---|
| Orders / Total / Gross Sales | `channel_pnl.meta_*` / `google_*` (also `platform_attribution_commerce`) | **OK** | |
| Discounts | `channel_pnl.meta_discounts` / `google_discounts` | **OK** | |
| Net Sales | `channel_pnl.meta_net_sales` / `google_net_sales` | **NEAR** | Meta −₹1.6k / Google −₹4.7k vs line-item RETURN_DEDUCTION (events view) |
| Returns/Cancels count | `channel_pnl.*_returns_cancels` | **OK** | |
| Ad Spend / Impressions / Clicks | `meta_ad_performance` / `google_ad_performance` | **OK** | |
| Net COGS / Net Profit / ROAS | — | **MISSING** | Dashboard overlays `getAttributionChannelPnl` FE lifecycle COGS; not yet in Cube |
| Session funnel | `session_funnel` / `funnel_daily` | **PARTIAL** | Channel filter parity not live-proven |

**Do not use** `platform_attribution_commerce.*_net_sales` for Overview Net Sales (placement net, +₹64k Meta drift). Catalogue now points at `channel_pnl`.

---

## P&L / Forecast

| Card | Cube path | Status |
|---|---|---|
| Gross / Net Sales / TOC / Net Profit | `canonical_pnl` all-channel measures | **NEAR** (same residuals as Historical) |
| Product / Packaging / Shipping / Gateway / RTO | `canonical_pnl.*` | **OK** Shopify arms |
| Amazon Fees | `canonical_pnl` / `amazon_attribution_overview.platform_fees` | **OK** for Attribution fees |
| Taxes | `canonical_pnl.taxes_on_net_sales` | **PARTIAL** | Estimate (net × 18%), not full GST ledger |
| Meta/Google/Amazon Ads | `canonical_pnl.*_spend` | **OK** |

---

## Semantic footguns (meaning drift)

| If you ask for… | Wrong Cube measure | Correct |
|---|---|---|
| Amazon Net Profit | `marketplace_net_payout` / `amazon_net_payout` | `amazon_attribution_overview.net_profit` |
| Meta Attribution Net Sales | `platform_attribution_commerce.meta_net_sales` | `channel_pnl.meta_net_sales` |
| All-channel Gross ROAS | `canonical_pnl.gross_roas` (Shopify-only) | `canonical_pnl.gross_roas_all_channels` |
| Historical Net Profit | `canonical_pnl.net_profit` (blended Shopify) or `net_profit_shopify` | `canonical_pnl.net_profit_all_channels` |
| Returns/Cancels (All) | `commerce_orders.returns_cancels_orders` alone | + `amazon_attribution_overview.returns_cancels` |

---

## Still to build (priority)

1. **Meta/Google channel Net COGS + Net Profit + ROAS** — port `buildChannelMetricsSql` cost arms into `channel_pnl` (or join FE lifecycle costs by channel).
2. **All-channel Returns/Cancels single measure** — Shopify event + Amazon delivery-date.
3. **LTV:CAC** — new-customer metrics cube matching Historical `new_customer_metrics`.
4. Tighten Meta/Google Net Sales to line-item `RETURN_DEDUCTION_EXCL` (closes ~₹2–5k).

## Harness

```bash
py -3 scripts/reconcile_all_views_live.py --brand 20 --date 2026-06-01 --end 2026-06-30
py -3 scripts/audit_dashboard_cube_coverage.py --brand 20 --date 2026-06-01 --end 2026-06-30
```
