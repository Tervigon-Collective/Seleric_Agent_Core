# 09 — Mart Build Spec (Phase 2, ClickHouse serve, author-only)

Executable spec for building the ~10 marts as ClickHouse serve relations by
**composition over the existing certified `serve.*` views** (no gold re-derivation).
Pattern proven by `mart_channel_daily` (see
`data_platform/mage-ai/serve/marts/`). Each mart is authored as a `__shadow` view,
parity-verified, then promoted. **Apply is manual/operator** (`serve/marts/apply_views.sh`
against `clickhouse.seleric.com`); this repo only holds the artifacts.

## Build loop (per mart)

1. Write `serve/marts/views/<mart>.sql` — `CREATE OR REPLACE VIEW serve.<mart>__shadow`
   composing the source serve views (UNION for multi-source/long, LEFT JOIN for
   enrich). Additive measures only; ratios derived downstream. Nullable numerics,
   `NULL` where a source lacks a column. `channel_type` discriminator for channel marts.
2. Add the file to `serve/marts/apply_views.sh`.
3. Write `serve/marts/parity/<mart>_parity.sql` — per-source diff = 0 + grain-uniqueness.
4. Operator applies + runs parity (also `serve/evaluations/parity_smoke_brand20.py`).
5. **P3 wiring (after apply, needs live Cube):** cube cube+view over `serve.<mart>`
   (`infra/cube/model/cubes/serve_<mart>.yml` + `views/serve_views.yml`), OM
   product+contract, `catalogue/views.yaml` + metric `cube_mapping` repoint, then
   rebind `catalogue/concepts/*.yaml` `resolves[].metric` and regenerate crosswalk.

## ⚠ Critical finding — serve views carry BASE columns; Cube computes the rest

Verified against the view SQL: `serve.*` views expose **base ex-GST columns only**.
The `*_all_channels` roll-ups, GST/taxes, and all ratios (ROAS, margins, MER, AOV,
avg_ltv, repeat_rate, and even count measures like `customers`) are computed in the
**Cube layer**, not the serve DB. Examples:

- `serve.canonical_pnl` → `net_sales_excl_tax`, `net_profit` (NOT `net_sales`,
  `net_margin_pct`, `net_sales_all_channels_pnl`, `taxes_on_net_sales`).
- `serve.customer_ltv` → `lifetime_net_revenue_excl_tax`, `lifetime_order_count`
  (NOT `avg_ltv`, `customers`, `repeat_rate` — those are Cube `count()`/derived).
- `serve.sales_all_channels` → `shopify_net_sales` (NOT `net_sales`);
  `serve.orders_all_channels` has NO `orders` column (it's `count(distinct order_key)`);
  `serve.returns_cancels_all_channels` → `revenue` (NOT `return_cancel_revenue`).

**Consequences for mart building:**
1. **Compose over REAL view columns** — always `DESCRIBE serve.<view>` (or read the
   view's outer SELECT) first. Cube measure names ≠ view column names. Never guess.
2. **Marts carry base columns; the mart's Cube cube recomputes the derived measures**
   (all_channels, ratios, taxes) exactly as the current cubes do — do NOT re-derive
   them in the serve SQL (keeps the verified logic in one place, at the Cube layer).
3. This means several "marts" that only add derived measures are, at the serve level,
   thin base-column compositions; the consolidation value is realised in the Cube view.
4. **Open question for the data team:** since the derivations live in Cube, an
   alternative is to consolidate at the **Cube-view layer** (compose cubes) rather than
   new serve relations. The chosen substrate is ClickHouse serve; flagged for review.

## Refinements found while building

1. **Thin projections need NO new relation.** A single-source "mart" that is just
   `SELECT * FROM serve.<view>` adds surface for no value. For single-source domains
   the **existing serve view IS the mart** — no P2 SQL; P3 just rebrands it in
   cube/catalogue/OM (view name → mart name). This applies to `mart_order_items`
   (=product_performance), `mart_sessions` (=session_funnel), `mart_refunds`
   (=refund_events), `mart_refund_lines` (=return_lifecycle), `mart_payments`
   (=payments). Net new SQL is ~6 marts, not ~10.
2. **`finance_waterfall` is a finer grain** (brand×report_date×waterfall_key) than the
   daily P&L, so it is NOT joined into `mart_pnl_daily` (would fan out). It stays its
   own relation. `mart_pnl_daily` = `canonical_pnl` ⟕ `ltv_cac` only.

## Composition table (source serve views → mart)

| Mart | Grain | Compose from (existing serve.*) | Method |
|---|---|---|---|
| **mart_channel_daily** ✅ authored | brand×date×channel_type×channel | channel_attribution_daily, channel_pnl, sales/orders/returns_cancels_all_channels | UNION ALL by channel_type |
| **mart_pnl_daily** ✅ authored | brand×date | canonical_pnl ⟕ ltv_cac (waterfall excluded, see above) | LEFT JOIN on (brand_id,report_date) |
| **mart_customers** ✅ authored | brand×customer_id | customer_ltv ⟕ customer_data | LEFT JOIN on (brand_id,customer_id) |
| **mart_orders** spec | brand×order_id | commerce_orders ⟕ order_attribution(1:1) ⟕ purchase_sequence ⟕ platform_attribution_commerce; commerce_order_events rolled to order | LEFT JOIN; event revenue via GROUP BY subquery (never raw 1:M) |
| **mart_ads_daily** spec | brand×date×channel×campaign×adset×ad | meta_ads_daily (channel='meta'), google_ads_daily (channel='google'), ad_channel_pnl, meta_ad_attribution | UNION meta/google delivery + LEFT JOIN economics/attribution; platform-only cols NULL cross-platform |
| **mart_sessions_daily** spec | brand×date×channel | funnel_daily ⟕ web_events_daily | LEFT JOIN on (brand_id,report_date,channel) |
| **mart_ad_status** spec | brand×entity×changed_at | meta_ads_status_history (channel='meta'), google_ads_status_history (channel='google') | UNION ALL, add channel + normalize adgroup_id→adset_id |
| mart_order_items / mart_sessions / mart_refunds / mart_refund_lines / mart_payments | (source grain) | product_performance / session_funnel / refund_events / return_lifecycle / payments | **no new relation** — rebrand existing view at P3 |

`mart_orders` and `mart_ads_daily` are the two error-prone composites (event rollup;
cross-platform UNION+join) — author against a live ClickHouse and iterate on parity
rather than blind, given no offline CH here.

## Rules (carried from the physical model, §03)

- **Fan-out**: only LEFT JOIN across 1:1 / M:1. `mart_orders` must roll
  `commerce_order_events` (1:M) to order grain via a GROUP BY subquery before joining
  — never join raw event rows (double-counts order measures).
- **Non-additive** (reach, frequency, budgets, ratios): carry as `max`/derived, never
  summed. Drop ROAS/margin from marts; compute from components downstream.
- **Basis in column names**; keep `*_excl_tax` / `gross_*` / `net_*` distinct.
- **Provenance**: carry `is_final`, `model_version`, and expose `data_as_of` on
  materialized marts. Keep the source views' FINAL/argMax semantics (inherited by
  composing over them, not re-deriving).
- **channel** stays a dimension value; `channel_map.yaml` (catalogue) normalizes raw
  sources → canonical channel; a new channel is a data row, no mart change.

## Materialization (§03)

Multi-join wide marts (mart_orders, mart_ads_daily, mart_channel_daily, mart_pnl_daily,
mart_sessions) should move from VIEW to a refreshable `ReplacingMergeTree(model_version)`
target on the gold-sync cadence once parity passes; thin projections stay VIEWs.
(Deferred until parity is green and the CH gold-sync cutover is settled — see the
`CLICKHOUSE_SYNC_ENABLED=0` note in the platform env.)

## Verification gates (per mart, before promote)

- `serve/marts/parity/<mart>_parity.sql` → all diffs 0, grain unique.
- `serve/evaluations/parity_smoke_brand20.py` / `data_products_parity.py` still green.
- After P3 wiring: `Base_Agent/scripts/reconcile_layers.py` 0 blockers,
  `check_data_quality.py` grain-uniqueness + P&L identities on the mart.
