# Dashboard ↔ Cube coverage matrix (brand 20, June 2026 live)

Audited 2026-07-27 (re-verified after gap closes). Cube = agent semantic layer; Dashboard = Node-Backend oracle.

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
| Net Sales | `canonical_pnl.net_sales_all_channels_pnl` | **OK** | Shopify events + Amazon Attribution net |
| Total Orders | `orders_all_channels.orders` | **OK** | |
| Total Ad Spend | `canonical_pnl.total_ad_spend` | **OK** | Meta+Google+Amazon |
| Net COGS / TOC | `canonical_pnl.total_operating_cost_all_channels` | **OK** | ACTIVE_OR_KEPT + retained product cost |
| Net Profit | `canonical_pnl.net_profit_all_channels` | **OK** | |
| Discounts | `commerce_orders.discount_amount_excl_tax` / `canonical_pnl.discounts` | **OK** | Shopify |
| Returns/Cancels count | `returns_cancels_all_channels.returns_cancels` | **OK** | Shopify events + Amazon delivery-date |
| Return/Cancel revenue | `commerce_orders.event_*` + `amazon_attribution_overview.return_revenue` | **PARTIAL** | No single all-channel revenue measure |
| Gross ROAS | `canonical_pnl.gross_roas_all_channels` | **OK** | New all-channel measure |
| Net ROAS | `canonical_pnl.net_roas_all_channels` | **OK** | |
| BE ROAS | `canonical_pnl.be_roas_all_channels` | **OK** | |
| Total Payments | `commerce_orders.total_payment_orders` | **OK** | Catalogue id `total_payments` |
| LTV:CAC | `ltv_cac.ltv_cac_ratio` | **OK** | First-order AOV / CAC; revenue = dashboard net (incl. unpaid COD) |

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
| Net Sales | `channel_pnl.meta_net_sales` / `google_net_sales` | **OK** | Event-date returns/cancels via channel_pnl |
| Returns/Cancels count | `channel_pnl.*_returns_cancels` | **OK** | |
| Ad Spend / Impressions / Clicks | `meta_ad_performance` / `google_ad_performance` | **OK** | |
| Net COGS / Net Profit / ROAS | `channel_pnl.meta_*` / `google_*` | **OK** | FE lifecycle COGS + ads |
| Session funnel | `session_funnel` / `funnel_daily` | **PARTIAL** (`unproven`) | Channel-filter parity with the dashboard is not live-proven. Do not claim a dashboard match. |

**Do not use** `platform_attribution_commerce.*_net_sales` for Overview Net Sales (placement net). Catalogue points at `channel_pnl`.

---

## P&L / Forecast

| Card | Cube path | Status |
|---|---|---|
| Gross / Net Sales / TOC / Net Profit | `canonical_pnl` all-channel measures | **OK** |
| Product / Packaging / Shipping / Gateway / RTO | `canonical_pnl.*` | **OK** Shopify arms |
| Amazon Fees | `canonical_pnl` / `amazon_attribution_overview.platform_fees` | **OK** for Attribution fees |
| Taxes | `canonical_pnl.taxes_on_net_sales` | **PARTIAL** (`drift_corrected`) | Cube = Shopify net × 18%, not GST ledger. Node-Backend strips Amazon then adds actual Amazon tax (`taxes_on_net_sales.yaml`). |
| Meta/Google/Amazon Ads | `canonical_pnl.*_spend` | **OK** |

---

## Semantic footguns (meaning drift)

| If you ask for… | Wrong Cube measure | Correct |
|---|---|---|
| Amazon Net Profit | `marketplace_net_payout` / `amazon_net_payout` | `amazon_attribution_overview.net_profit` → catalogue `amazon_net_profit` |
| Meta/Google Attribution Net Sales | `platform_attribution_commerce.*_net_sales` | `channel_pnl.*_net_sales` → catalogue `meta_attribution_net_sales` / `google_attribution_net_sales` |
| All-channel Gross ROAS | `canonical_pnl.gross_roas` (Shopify-only) | `canonical_pnl.gross_roas_all_channels` → catalogue `gross_roas_all_channels` |
| Historical Net Profit | `canonical_pnl.net_profit` (Shopify-only Cube) | `canonical_pnl.net_profit_all_channels` — dashboard blended card; Amazon pending-refund overlay is Node-only and is **not** in certified Cube |
| Returns/Cancels (All) | `commerce_orders.returns_cancels_orders` alone | `returns_cancels_all_channels.returns_cancels` |
| New-customer LTV revenue | — | `ltv_cac.new_customer_revenue` (= attributed_net_revenue for is_new_customer=1; dashboard net incl. unpaid COD) |

**How these are fixed (routing, not value rewrite):**
1. Catalogue ids already map to the Correct column.
2. Glossary defaults bare "Gross ROAS" / "Amazon Net Profit" to the Correct id.
3. Cube measure *titles* label the Wrong side as NOT Overview / NOT Net Profit / Shopify-only.
4. Agent prompt §3f-bis lists the traps so `catalogue_resolve_term` mistakes get overridden.

---

## Optional polish

1. All-channel return/cancel **revenue** single measure (count already OK).
2. Tighten Meta/Google Net Sales further if line-item `RETURN_DEDUCTION_EXCL` ever diverges again.
3. Session funnel channel-filter live parity.

## Harness

```bash
py -3 scripts/verify_gap_fixes.py
py -3 scripts/reconcile_all_views_live.py --brand 20 --date 2026-06-01 --end 2026-06-30
py -3 scripts/audit_dashboard_cube_coverage.py --brand 20 --date 2026-06-01 --end 2026-06-30
```
