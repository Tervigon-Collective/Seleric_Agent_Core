# 03 — Physical Model

How the ~10 marts (`02_LOGICAL_MODEL.md`) are implemented in ClickHouse and exposed
through Cube. The current physical conventions (`../../DATA_MODEL.md` Part III,
`../../catalogue/SERVE_LAYER_ARCHITECTURE.md`) carry over unchanged; this doc states
what is new.

## 1. Layering (unchanged shape, fewer relations)

```
gold (ClickHouse, ReplacingMergeTree)   facts + conformed dims, GST/basis resolved
  → serve marts (~12 relations)         wide, one per grain — REPLACES the 41 today
  → Cube cubes (~12, public:false)      measures/dimensions with type + format
  → Cube views (~12, 1:1 with marts)    the certified surface the agent queries
  → catalogue + OpenMetadata            concepts, metrics, contracts, governance
```

Net change: **41 serve relations / 37 views → ~12** (10 marts + 2 grain-variant
companions: `mart_sessions_daily`, `mart_refund_lines`; hourly/breakdown are
partitions of `mart_ads_daily`, not separate relations).

## 2. Materialization — the key physical change

Today every serve relation is a ClickHouse `VIEW` that re-runs dimension joins with
`FINAL` on each query. The wide marts join 2+ gold tables, so:

| Mart | Physical target | Reason |
|---|---|---|
| mart_orders, mart_order_items, mart_ads_daily, mart_channel_daily, mart_pnl_daily, mart_sessions | **materialized table**, refreshed on gold-sync cadence | multi-join wide relations; re-joining `ReplacingMergeTree` under `FINAL` per query is too costly at this width |
| mart_customers, mart_refunds(_lines), mart_payments, mart_ad_status | `VIEW` (thin) or materialized if cheap | fewer joins |

Materialized marts expose `data_as_of` so the freshness gate keeps working. Refresh
is idempotent (full replace or incremental by `report_date`/partition). Engine for
materialized marts: `ReplacingMergeTree(model_version)` on the mart PK so re-runs
dedupe; read with `FINAL` downstream.

## 3. Keys, sort & partition

| Mart | ORDER BY (sort key) | PARTITION BY |
|---|---|---|
| mart_orders | (brand_id, order_date, order_id) | toYYYYMM(order_date) |
| mart_order_items | (brand_id, order_date, order_id, line_item_id) | toYYYYMM(order_date) |
| mart_channel_daily | (brand_id, report_date, channel_type, channel) | toYYYYMM(report_date) |
| mart_ads_daily | (brand_id, report_date, channel, campaign_id, adset_id, ad_id, grain) | toYYYYMM(report_date) |
| mart_pnl_daily | (brand_id, report_date) | toYYYYMM(report_date) |
| mart_customers | (brand_id, customer_id) | brand_id |
| mart_sessions | (brand_id, session_date, session_id) | toYYYYMM(session_date) |
| mart_refunds / _lines | (brand_id, refund_date, refund_id[, line]) | toYYYYMM(refund_date) |
| mart_payments | (brand_id, transaction_date, transaction_id) | toYYYYMM(transaction_date) |
| mart_ad_status | (brand_id, entity_type, entity_id, changed_at) | toYYYYMM(changed_at) |

Primary key = the sort-key prefix that is unique at grain. Cube cubes declare the
same composite as `primary_key` (synthesize `concat(...)` where multi-part), so Cube
dedupes correctly — the existing pattern for inline-SQL cubes.

## 4. Type conventions

| Logical kind | ClickHouse | Cube type / format |
|---|---|---|
| ids, names, statuses, channel | `String` / `LowCardinality(String)` | string |
| dates (order_date, report_date, refund_date, session_date) | `Date` | time |
| timestamps (…_at_ist, changed_at, report_hour_ts) | `DateTime64(3,'Asia/Kolkata')` | time |
| money | `Decimal(18,4)` | sum/number, currency |
| counts | `UInt32/64` | count / count_distinct / sum, number |
| ratios (ctr, roas, margin_pct, aov) | `Float64` (derived) | number |
| flags (is_cod, is_final, …) | `UInt8` | boolean |
| budgets, reach, frequency | `Decimal`/`Float64` | **max / number — never sum** |

Authoritative per-member type stays Cube `/meta`, validated at load by
`catalogue_service/validate.py`.

## 5. Provenance & correctness conventions (mandatory on every mart)

- **FINAL / argMax discipline** — marts read gold with `FINAL` or
  `argMax(...) GROUP BY key`; materialized marts store already-deduped rows.
- **Provenance columns** — `is_final` (report_date ≤ today−3), `source_basis`,
  `model_version`, `data_as_of`.
- **Non-additive typed correctly** — reach, frequency, distinct counts, rates,
  budgets are `count_distinct`/`number`/`max`, never `sum` (gated by
  `check_data_quality.py`).
- **Fan-out guards in metadata** — `mart_ads_daily.row_type` and `breakdown_type`
  declared as `fanout_dimension`/`required_filters` so a sum without the filter is
  rejected, not silently wrong.
- **Basis in column names** — `*_excl_tax`, `*_incl_tax`, `gross_*`, `net_*`.

## 6. Serve mart → Cube cube → Cube view mapping

One cube (`public:false`) and one view per mart; names align (`serve_mart_orders` →
view `mart_orders`). Grain-variant companions (`mart_sessions_daily`,
`mart_refund_lines`) get their own cube+view. `mart_ads_daily` hourly/breakdown are
selected via the `grain` dimension, not separate views.

| Mart (serve table) | Cube cube | Cube view | gold sources (verified lineage) |
|---|---|---|---|
| serve.mart_orders | serve_mart_orders | mart_orders | fct_orders, fct_order_attribution, fct_orders(seq), (order events) |
| serve.mart_order_items | serve_mart_order_items | mart_order_items | fct_order_items |
| serve.mart_channel_daily | serve_mart_channel_daily | mart_channel_daily | channel_attribution_daily, channel_pnl inputs, commerce (all-channels) |
| serve.mart_ads_daily | serve_mart_ads_daily | mart_ads_daily | fct_meta_ads_daily, fct_google_ads_daily, ad_channel_pnl, mart_meta_ad_daily_attribution |
| serve.mart_pnl_daily | serve_mart_pnl_daily | mart_pnl_daily | canonical_pnl inputs, finance_waterfall, ltv_cac inputs |
| serve.mart_customers | serve_mart_customers | mart_customers | fct_orders, dim_customers |
| serve.mart_sessions(_daily) | serve_mart_sessions(_daily) | mart_sessions(_daily) | fct_session_funnel, mart_funnel_daily, mart_web_events_daily |
| serve.mart_refunds(_lines) | serve_mart_refunds(_lines) | mart_refunds(_lines) | fct_refunds, fct_refund_line_items |
| serve.mart_payments | serve_mart_payments | mart_payments | fct_payments |
| serve.mart_ad_status | serve_mart_ad_status | mart_ad_status | fct_meta/google_ads_status_history |

## 7. Channel physical handling (extensibility)

- Channel is a **column** on `mart_channel_daily` and `mart_ads_daily` (long
  format). Adding WhatsApp = the loader emits rows with `channel='whatsapp'` +
  one entry in `catalogue/dimensions/channel_map.yaml` (raw source → canonical
  channel + channel_type + parent). No DDL, no new metric.
- `mart_pnl_daily.meta_spend/google_spend` remain as **derived** convenience
  columns (sum of `mart_channel_daily.ad_spend` filtered by channel) — compat only,
  not the by-channel answer.
- A mandatory `unattributed` / `other` channel row guarantees no revenue is dropped
  when a source is not yet mapped.

## 8. What is dropped from the physical model

Dead columns (0%-populated, per `SERVE_LAYER_ARCHITECTURE §9.2`) are **not carried**
into the marts: commerce `utm_source/medium/campaign`; session `device_type/
browser_family/os_family`; breakdown `country/impression_device`; customer
`accepts_marketing`; ad `adset_type/billing_event/bid_strategy`,
`campaign_buying_type`. Recent-window Meta `creative_*` is carried but null-flagged
(upstream loader regression, tracked separately).

Verification of parity (row/measure) and gates: `08_MIGRATION_PLAN.md`.
