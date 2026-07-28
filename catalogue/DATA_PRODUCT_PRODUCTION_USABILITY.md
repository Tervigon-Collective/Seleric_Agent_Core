# Data Product Production Usability — Improvements Sheet

**Date:** 2026-07-21 (requery after fixes)  
**Scope:** OpenMetadata data products (10) × live Cube (`127.0.0.1:4001`) × dashboard reconciliation  
**Tenant:** brand_id=`20`, company_id=`19`  
**Window:** 2026-06-21 → 2026-07-20 (30d)  
**Requery:** **16/16 persona queries PASS**
**Independent re-verification (2026-07-21, second tester pass):** all three prior P0 fixes re-confirmed **live** through the Cube API after an initial mid-rebuild race (see note); **one new correctness bug found and fixed** — `channel_attribution` order/revenue inflation (P1-channel, now resolved).

**Verdict:** Paid media, commerce, product, session funnel, EventStream (event + daily), and marketing attribution are queryable and reconcile. Channel attribution is now fixed and reconciled. The one remaining production risk is **net-PROFIT basis drift vs dashboard** (net *sales* reconciles exactly — see reconciliation).

> **Rebuild non-atomicity (ops note):** During this pass the first battery ran while the serve views were being rebuilt, returning transient ClickHouse errors (`credited_net_revenue_excl_tax` unresolved) and stale/inflated web-event counts (~50M). Re-running after the rebuild settled gave correct results. Serve-view rebuilds are **not atomic** — queries mid-rebuild can error or return inflated data. Reinforces P1-web-pipeline (move to scheduled, atomic swaps).

---

## Fixes applied this pass

| ID | Fix | Result |
|---|---|---|
| P0-1 | `serve.order_attribution` DDL (`AS alias FINAL` + explicit column aliases) + redeploy | `credited_net_revenue_excl_tax` queryable |
| P0-2 | Backfill `gold.fct_web_events` from `atomic.events` (app_id `b529c3-fb` → brand 20); rebuild `gold.mart_web_events_daily` | Event grain **759,354** rows; 30d Cube events **573,847**; event ≡ daily |
| P0-4 | `attributed_orders` / related measures → `countDistinct(order_id)`; added `credit_rows`; credit-scaled gross/refund; AOV uses credited revenue | linear orders **2050** (= last_touch), credit_rows **2743** |
| **P1-channel** | `serve.channel_attribution_daily` counted **all 4 attribution models** (regression from P0-1 adding the models to `order_attribution`): a top-level `WHERE attribution_model='last_touch_v1'` bound to the view's own hardcoded **output-alias constant** (always true), disabling the filter → orders/revenue inflated ~3–4×. Fixed by pinning the source in a **subquery** (`FROM (SELECT * FROM serve.order_attribution WHERE attribution_model='last_touch_v1')`); redeployed CH view. | meta **6072→1423**, google 2204→540, org_shopify 617→87 (Shopify total **2050** = order_attribution LT = commerce). Verified end-to-end through Cube. |

---

## Persona requery summary (post-fix)

| Persona | Query | Status | Key numbers (30d) |
|---|---|---|---|
| CEO | canonical_pnl | PASS | ad spend 2,420,711.53; net_profit_all_channels 752,944 |
| CEO | sales_all_channels | PASS | total_sales 7,389,661 |
| CEO | orders_all_channels | PASS | 2,822 (Shopify 2,445 / Amazon 377) |
| Commerce | commerce_orders | PASS | orders 2,445; total_sales 6,731,773 |
| Commerce | amazon_commerce | PASS | active_orders 377 |
| Product | top SKUs | PASS | Pawveralls 1,457 units |
| PaidMedia | meta/google/amazon | PASS | Meta 1,715,503.70 / Google 565,661.80 / Amazon 139,546.03 |
| Attribution | by model | PASS | all models **2,050** distinct orders; linear credit_rows 2,743; credited net ≈ 2,338,215 |
| Attribution | by platform (LT) | PASS | meta 1,423 / google 540 / organic … |
| Attribution | channel_attribution | **FIXED** | meta 1,423 / google 540 / org_shopify 87 / amazon 336 / org_amazon 91 — reconciles (was 4× inflated) |
| Customer | LTV | PASS | 16,928 customers; repeat_rate 12.3% |
| Web | session_funnel | PASS | 113,141 sessions |
| Web | web_events | PASS | **573,847** events (was 0) |
| Web | web_events_daily | PASS | **573,847** events (matches event grain) |

---

## Cube ↔ Dashboard reconciliation (unchanged spend story)

| Metric | Dashboard | Cube | Severity |
|---|---:|---:|---|
| Meta / Google / Amazon ad spend | match | match | OK |
| Amazon active orders | 377 | 377 | OK |
| Total sales | 7,365,881 | 7,389,661 | Low (~0.3%) |
| **Net sales (Shopify)** | 5,027,719 (meta 3,230,658 + google 1,335,914 + organic 461,147) | canonical_pnl.net_sales **5,027,720** | **OK — reconciles exactly** |
| Net profit | 369,624 (incl. Amazon overlay −300,706) | 466,533 (net_profit_incl_amazon) / 530,859 (Meta+Google Shopify) | **High** — Amazon pending-refund + return-label overlay is Node-backend-only, not in canonical_pnl |

---

## Remaining backlog

### Still open

| ID | Issue | Priority |
|---|---|---|
| P0-3 | Net **profit** diverges: dashboard applies an Amazon pending-refund + return-label overlay (Node-backend only) that `canonical_pnl` does not. Net *sales* reconciles exactly. Governance: agents must state which net-profit basis they quote. | High (governance) |
| P1-1 | Ad views lack purchase/ROAS; use P&L + attribution packs | Medium |
| P1-2 | Amazon `net_revenue_excl_tax` NULL on channel_attribution (no ex-GST decomposition exists for Amazon — by design) | Medium |
| P1-3 | Customer LTV lifetime-only | Low |
| ~~P1-web-pipeline~~ | **RESOLVED (pipeline models added).** Replaced the one-shot `atomic.events→gold.fct_web_events` CH INSERT with two pipeline-native dbt gold models: `dbt/models/iceberg/snowplow/gold/fct_web_events.sql` (event grain, from silver `trino_snowplow_events` + `fct_session_funnel` for FINE channel) and `mart_web_events_daily.sql` (daily rollup). Both registered in `utils/trino_to_clickhouse.py` (`ICEBERG_GOLD_TO_CLICKHOUSE` + `TABLE_PLATFORM=analytics`). `channel` now matches `session_funnel` by construction and self-heals as sessions mature (both are full-refresh gold tables). Mart aggregation validated against the existing CH table (exact match, 07-13). **Activates on next `tag:iceberg` gold run + `sync_iceberg_gold_to_clickhouse`** (needs Trino access — could not execute from this host). Serve-view rebuild atomicity remains a separate ops item. | High (ops) |

### Closed this pass

- P0-1 serve attribution schema drift (re-verified live)  
- P0-2 empty EventStream event grain (+ inflated daily mart rebuilt; re-verified 573,847 both grains)  
- P0-4 credit-grain order double-count (re-verified 2050 across all 4 models)  
- **P1-channel** channel_attribution 3–4× order/revenue inflation — root-caused (alias-shadowed model filter), fixed via subquery, redeployed, verified through Cube  

---

## Production readiness by data product (updated)

| # | Data product | Ready? | Gate |
|---|---|---|---|
| 1 | Amazon Ads Performance | Yes (spend/traffic) | — |
| 2 | Amazon Commerce Performance | Yes | Amazon total_sales vs dashboard ~3.8% |
| 3 | Channel Attribution | **Yes** | Fixed this pass; reconciles with order_attribution LT. Amazon net_revenue_excl_tax NULL by design |
| 4 | Commerce Performance | Yes | Don’t equate net_revenue_excl_tax with dashboard net sales |
| 5 | Customer Intelligence | Yes (lifetime) | — |
| 6 | Google Ads Performance | Yes (spend/traffic) | — |
| 7 | Marketing Attribution | Yes | Prefer `attributed_orders` (distinct) + `credit_rows` |
| 8 | Meta Ads Performance | Yes (spend/traffic) | Exact spend match |
| 9 | Product Performance | Yes | — |
| 10 | Session Funnel | Yes | — |
| — | EventStream event + daily | Yes | Schedule ongoing backfill from `atomic.events` |

---

## Artifacts

- Requery JSONL: temp scratchpad `persona_requery.jsonl`
- Matrix CSV: `Base_Agent/catalogue/data_product_usability_matrix.csv`
- Cube: `serve_order_attribution.yml` + `serve_views.yml` (`credit_rows`)
- Serve SQL: `serve/attribution/views/order_attribution.sql`
