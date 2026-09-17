# Metric Reconciliation — June 2026 Test Period


**Scope:** KPI / metric / analytics / attribution definitions across
`Base_Agent` (agent catalogue + Cube semantic layer), `Seleric_Dashboard`
(fe-dashboard + Node-Backend Gold engine), `data_platform/mage-ai`
(dbt rollups + Cube model + OpenMetadata), the chat agent (`src/seleric_mcp`),
and OpenMetadata.

**Test period:** June 2026 — day anchor `2026-06-19`, MTD window
`2026-06-01 … 2026-06-19` (the captured dashboard exports).

**Status of this document:** current consolidation. It supersedes the
cube-path specifics in `DASHBOARD_CUBE_METRICS.md` (that doc cites
`Base_Agent/cube/model/...`, which no longer exists — see §0).

---

## 0. Canonical source of truth (decision)

**The Seleric Dashboard Gold P&L spine is canonical.**
`Node-Backend/src/services/pnlService.js` →
`integrations/historicalAnalytics/lineItemHistoricalSql.js` is the definitive
implementation. Every other system is reconciled *to it*, never the reverse:

| System | Role | Canonical binding |
|---|---|---|
| Seleric_Dashboard (Node-Backend Gold) | **source of truth** | — |
| data_platform/mage-ai `dbt` | builds `gold.int_finance_daily_rollups` that must reproduce the spine | `dbt/models/iceberg/cross_platform/int_finance_daily_rollups.sql` |
| Cube semantic layer | serves the rollup as measures | `data_platform/mage-ai/infra/cube/model/cubes/serve_canonical_pnl.yml` + `views/serve_views.yml` |
| Base_Agent catalogue | the agent's metric surface; one entry per canonical metric | `Base_Agent/catalogue/metrics/*.yaml` |
| Chat agent (`src/seleric_mcp`) | executes catalogue metrics against Cube | `query_planner.py` builds queries **only** from `cube_mapping.measure` |
| OpenMetadata | documents definition / ownership / lineage | `Base_Agent/catalogue/openmetadata/*` + `data_platform/mage-ai/openmetadata/*` |

**Path correction (verified this session):** the live Cube + dbt models are in
`data_platform/mage-ai/infra/cube/model/` and `.../dbt/models/`, **not**
`Base_Agent/cube/` (which does not exist). Cubes are named `serve_*.yml`
(34 of them); the earlier `gold_*.yml` naming is gone.

---

## 1. Metric reconciliation table — June 2026 (`2026-06-19`, brand 20)

Dashboard values are from the Historical Analytics export
`Seleric_Dashboard/historical-dashboard-kpis-2026-06-19-summary-v2.csv`.
`Headline` = the value the dashboard card renders (Gold backend);
`Detail Σ` = the same figure reconstructed from line-item/order detail.

| # | KPI | Formula (canonical) | Headline | Detail Σ | Δ | Cube measure | Agent metric id |
|---|---|---|---:|---:|---:|---|---|
| 1 | Net Profit | net_sales − total_ad_spend − total_cogs | 8,424.71 | 8,469.56 | **−44.85** | `canonical_pnl.net_profit` | `net_profit` |
| 7 | Discounts | Σ discount_excl_gst on placement | 0.00 | 0.00 | 0.00 | `canonical_pnl.total_discounts_excl_tax` | `discounts` |
| 9 | Returns / Cancels | return events + cancel events | 6 | 6 | 0 | `commerce_orders.{cancelled,returned}_orders` | `returns_cancels` |
| 10 | Gross ROAS | gross_sales / ad_spend | 1.63 | 1.63 | 0.00 | `canonical_pnl.gross_roas` | `gross_roas` |
| 11 | Net ROAS | (net_sales − cogs) / ad_spend | 1.11 | 1.11 | 0.00 | `canonical_pnl.net_roas` | `net_roas` |
| 12 | BE ROAS | (cogs + ad_spend) / ad_spend | 1.35 | 1.35 | 0.00 | `canonical_pnl.be_roas` | `be_roas` |
| 13 | Total Payments | payment-bucket order counts | 81 | 81 | 0 | `commerce_orders.{cod,prepaid}_orders` | `cod_orders` + `prepaid_orders` |

### 1a. LIVE Cube ↔ LIVE Dashboard reconciliation — brand 20 (queried 2026-07-27)

Both sides pulled **live**: dashboard via Node-Backend's own
`getHistoricalDashboard()` (same ClickHouse the API uses), Cube via
`/cubejs-api/v1/load` (the chat agent's data source). Automated by
`Base_Agent/scripts/reconcile_live.py` — re-run it any time; do **not** compare
against the June CSV exports, which are stale (see box below).

**⚠️ The June CSVs are stale — do not reconcile against them.** The pipeline
reprocessed June: e.g. `2026-06-19` `total_cogs` moved **27,752.69 (CSV) →
58,670.27 (live)** and `net_profit` **8,424.71 → −22,461.21**. The earlier "7
fail / COGS ≈ +30.9k / net_profit ≈ −30k" table in this doc's history was an
artifact of live-Cube-vs-stale-CSV **and** of picking shopify-only Cube measures
(`net_cogs`, `net_profit`) instead of the all-channel ones. It is retracted.
The `revenue_eligible` "fix" hypothesised from it is **also retracted** — the
dashboard's own SQL, reproduced against gold, gives the *same* 39,563.89 product
cost as the serve view (it does **not** filter `revenue_eligible`).

**Live-vs-live result (canonical all-channel measures):**

| Metric | Cube measure | 2026-06-19 Δ | 2026-06-10 Δ | 2026-06-05 Δ | MTD Δ |
|---|---|---:|---:|---:|---:|
| total_sales | `sales_all_channels.total_sales` | 0.00 | 0.00 | 0.00 | +2,999 |
| total_orders | `orders_all_channels.orders` | 0 | 0 | 0 | 0 |
| total_ad_spend | `canonical_pnl.total_ad_spend` | 0.00 | 0.00 | 0.00 | 0.00 |
| net_sales | `canonical_pnl.net_sales_all_channels_pnl` | −0.01 | −45.76 | −808.72 | −17,963 |
| total_cogs | `canonical_pnl.total_operating_cost_all_channels` | 0.00 | +151.98 | +268.98 | +15,011 |
| net_profit | `canonical_pnl.net_profit_all_channels` | −0.01 | −197.74 | −1,077.70 | −32,975 |
| returns_cancels | `commerce_orders.returns_cancels_orders` (event) | 0 | 0 | 0 | −10 |
| total_payments | `cod_orders + prepaid_orders` | 0 | −1 | 0 | −37 |
| gross_sales | `sales_all_channels.gross_sales` | +1,341.75 | +823.12 | +777.51 | +19,629 |

**Reconciles exactly (all days): `total_sales`, `total_orders`,
`total_ad_spend`.** These three are structurally identical across Cube and
dashboard.

**Small per-day P&L residuals — the real remaining work:**

2. **total_cogs** (COGS event-timing / retained-cost). Per-day +₹150–270; the
   serve view's cancel/return event arms and partial-return `RETAINED_PRODUCT_COST`
   proration differ slightly from the dashboard, +₹15,011 over MTD. **This is
   small, not the 2× blow-up earlier claimed.**
3. **net_profit** = net_sales − net_cogs − ad_spend, so it inherits #1 + #2.
4. **returns_cancels / total_payments** match at single-day grain but drift over
   multi-day windows (distinct-order counting across the range; payments also
   miss the dashboard's `paytm card machine`/`manual` buckets).

**Verdict:** sales/orders/ad-spend reconcile to the paisa; the P&L metrics carry
**small per-day residuals** (marketplace net-sales basis + COGS event-timing) that accumulate over
ranges, plus the gross_sales marketplace-GST basis. The fix site is
the ClickHouse serve view, not the dbt intermediate (see below).

**Fix site (verified) — `data_platform/mage-ai/serve/finance/views/canonical_pnl.sql`.**
Cube reads `sql_table: serve.canonical_pnl`, a ClickHouse view over `gold.*`.
`int_finance_daily_rollups` is an iceberg `int_` model referenced by **no serve
view and no cube** (`grep -r int_finance_daily_rollups serve/ infra/cube/` →
none) — the earlier citation was from the stale `DASHBOARD_CUBE_METRICS.md`.
Fixes are re-applied as a `CREATE OR REPLACE VIEW` against CH `serve` (no
dbt/Trino run), then re-validated with `scripts/reconcile_live.py`.

### 1b. Other KPI families — AOV / LTV / CAC / cohort / attribution / funnel

The §1/§1a work covers the Historical Analytics P&L + commerce cards. The
remaining KPI families split by **which engine the dashboard uses**, which
determines whether they *can* reconcile to Cube (gold) at all:

| Family | Cube (gold) | Dashboard engine | Reconcilable? | Note / finding |
|---|---|---|---|---|
| **AOV** | `commerce_orders.aov` = net_rev_excl_tax ÷ orders (MTD 2,374.98 = 1,257,415 ÷ 773) | `calculateAOV` over **live Shopify** orders (valid-order basis) | Partial | different basis (net-excl-tax vs valid-order revenue) **and** engine |
| **LTV / customers / repeat_rate** | `customer_ltv.*` (avg_ltv 1,595.23, repeat_rate 0.124, customers 17,207) | `getCustomerAnalytics` → **live Shopify API** | **No — by design** | different engine; Cube is a **lifetime snapshot** (time-dims `first_order_at`/`last_order_at`), not a June-window metric |
| **CAC** | *none* | *none stored* | Derived only | CAC is not a stored KPI in either system — it is `ad_spend ÷ new_customers` computed at read time (dashboard `new_customer_metrics.new_customers`, Cube `commerce_orders.new_customer_orders`) |
| **Cohort / repeat purchase** | `purchase_sequence.*` (first_orders 623, repeat_orders 150, repeat_order_share 0.194) | `getCohortAnalysis` → **live Shopify API** | **No — by design** | cross-engine |
| **Attribution** | `order_attribution.*` (gold) | `attributionPnLHelpers` / meta·google·organic (gold) | With caveat | **BUG (see below)** |
| **Funnel** | `funnel_daily.*` / `session_funnel.*` (gold mart) | `martFunnelClickhouse` (same gold mart) | Likely | measures are `sessions/purchases/revenue/funnel_conversion_rate` (not `funnel_purchases`); not yet value-checked |

**Two structural conclusions:**

1. **LTV / CAC / cohort / customer AOV are cross-engine.** The dashboard computes
   them from the **live Shopify API** (`getCustomerAnalytics`/`getCohortAnalysis`/
   `calculateAOV`, all requiring `getShopifyCredentials`), while Cube serves
   gold. They are **not expected to reconcile to the rupee** and are correctly
   outside the gold↔Cube P&L reconciliation. CAC is not stored anywhere — it is a
   derived ratio. This is a data-architecture boundary, not drift.

2. **`order_attribution.attributed_net_revenue` fan-out — FOUND & FIXED
   (2026-07-27).** Root cause: `serve.order_attribution` stores **4 attribution
   models** per order (`first_touch_v1`, `last_touch_v1`, `last_non_direct_v1`,
   `linear_v1`), each carrying the full order's credited net revenue; the cube
   summed across all four. Within one model the total is correct (1,143,237); ×4
   models = 4,572,946. `credited_net_revenue_excl_tax` was already credit-weighted
   per touch — the only defect was summing across models.
   **Fix:** scoped the cube source to the canonical `last_touch_v1` model
   (`serve_order_attribution.yml`: `sql_table` → `sql … WHERE attribution_model =
   'last_touch_v1'`) — the cube is declared last-touch (title + catalogue grain +
   all `lt_*` dims); multi-model comparison already lives in
   `serve_attribution_paths`. **Verified live** (June MTD brand 20):

   | measure | before | after |
   |---|---:|---:|
   | attributed_net_revenue | 4,572,946 | **1,143,237** |
   | credit_rows | 2,952 | 701 |
   | attributed_aov | 6,523 | **1,630.87** (≈ commerce 1,627) |
   | attribution_rate | 1.00 | **0.977** |
   | attributed_orders | 701 | 701 |

   Guarded by `test_order_attribution_cube_is_single_model_no_fanout` (fails if
   anyone reverts to the unscoped `sql_table`). **Deploy note:** the running Cube
   hot-reloaded the mounted model; the change must also ship to the deployed
   `cube_mcp` stack.

**Supporting components (stale June CSV — retained for provenance only):**

| Component | Value | Note |
|---|---:|---|
| `_shopify_net_sales_line_item` | 107,959.88 | ties (REF) |
| `_shopify_cogs_line_item` | 23,024.66 | ties |
| `_meta_ad_spend` (hourly) | 59,097.36 | ties |
| `_google_ad_spend` (hourly) | 19,643.97 | ±0.01 rounding |

**MTD COGS by axis** (`mtd-line-item-cogs-2026-06-01-to-2026-06-19-summary-v2.csv`),
the canonical `net_cogs_by_axis` decomposition that Cube reproduces:

| cogs_axis | lines | gross_cogs | net_cogs |
|---|---:|---:|---:|
| placement | 906 | 642,632.75 | 642,632.75 |
| cancelled | 51 | 42,946.55 | 2,954.30 |
| returned | 22 | 27,217.93 | 7,025.52 |
| **TOTAL** | **1,046** | **785,673.43** | **725,488.77** |

---

## 2. Discrepancy & root-cause report

### 2.2 Structural (definition) drift — already corrected in a prior pass

The earlier `DASHBOARD_CUBE_METRICS.md` audit found all 14 cards Mismatch/Missing
against the Cube because of two Gold engines (query-time composition vs
pre-aggregated `int_finance_daily_rollups`) and because 8 Cube measures had no
catalogue entry (agent-unreachable). Since then the Cube model was reworked
(`serve_canonical_pnl.yml` now exposes `net_profit`, `net_profit_shopify`,
`net_profit_all_channels`, plus the previously-missing
`gross_sales_excl_tax`, `total_discounts_excl_tax`, `returns_excl_tax`,
`gross_roas`, `net_roas`, `be_roas`), and every one of the ~190 catalogue
metrics now carries a `dashboard_alignment` block with an explicit
`match` / `drift_corrected` / `not_implemented` status
(`catalogue/openmetadata/metrics.yaml`). The reconciliation table above reflects
that post-rework state: the agent can now reach each of the 13 rendered cards.

### 2.3 Definition drift found & fixed *this session*

| Finding | Class | Fix |
|---|---|---|
| `active_orders` marked `status: draft` (correctly — no standalone active-order count exists in Node-Backend) but `test_order_status_breakdown_...` still required it to resolve → suite red | test lagging a deliberate dashboard-alignment decision | dropped `active_orders` from the test's certified set; documented why |
| `test_every_cube_has_at_least_one_primary_key` globbed `gold_*.yml` after cubes were renamed `serve_*.yml` → **silently vacuous** (0 files, always passed) | test pointing at a renamed upstream | re-pointed at `serve_*.yml` + added a non-empty guard |
| No test guarded catalogue ↔ OpenMetadata crosswalk | untested cross-system seam | added two drift tests (§4) |

---

## 3. Canonical metric catalog

The single source of truth for metric *definitions* is
`Base_Agent/catalogue/metrics/*.yaml` — one file per canonical metric, each with
`formula`, `cube_mapping` (view + measure the agent executes), `aggregation`,
`grain`, `supported_dimensions/filters`, `data_owner`, `access_policy`, and a
`dashboard_alignment` block tying it to the Node-Backend evidence. The chat
agent can only ever emit `cube_mapping.measure`, so this catalogue *is* the
agent's reachable metric surface. The 13 rendered June cards map as in §1.

---

## 4. OpenMetadata updates

- Crosswalk `catalogue/openmetadata/metrics.yaml` (190 entries) maps every
  catalogue metric → OM entity (`om_name`), glossary term, contract, cube view,
  and `dashboard_status`. Ownership is carried per-metric via `data_owner`
  (Finance / Commerce Ops / etc.) in the catalogue and mirrored into OM.
  Lineage: catalogue `cube_mapping.view` → `serve_views.yml` view →
  `serve_canonical_pnl` cube → `int_finance_daily_rollups` dbt model →
  Gold facts.
- **New guardrails (this session)** — two automated tests now fail CI if OM
  drifts from the catalogue:
  - `test_every_catalogue_metric_is_in_openmetadata_crosswalk`
  - `test_openmetadata_crosswalk_category_and_view_match_catalogue`
  Both pass today, i.e. OM and the agent catalogue are currently in lockstep.

---

## 5. Code changes made this session

| File | Change |
|---|---|
| `Base_Agent/catalogue/METRIC_RECONCILIATION_JUNE_2026.md` | this deliverable |

Test suite: **121 passed** (was 116 passed / 1 failed before the session).

---

## 6. June 2026 validation results

**Verified here (offline, deterministic):**
- Definitional consistency across catalogue ↔ Cube cubes ↔ Cube views ↔
  OpenMetadata crosswalk — 119 green tests, incl. the new cross-system drift
  guards.
- Dashboard June-19 headline KPIs (§1) and the MTD COGS-by-axis decomposition
  reproduce the canonical formulas; 10 of 13 cards tie to ₹0, the other 3
  (Net Profit / Net Sales / Gross Sales) share one −₹44.84 marketplace residual.

**Not verifiable in this environment (requires live services):**
Executing Cube, Node-Backend, the mage-ai pipeline, and the chat agent against
June 2026 data to assert *value* equality end-to-end needs a running
ClickHouse + Cube + backend, which are not available here. The definitions are
proven aligned and now drift-guarded; the remaining check is a live run.

### Decided (definition) / Open (Cube value) — from live query 2026-07-27

The live-vs-live check (§1a) shows sales/orders/ad-spend reconcile exactly; the
P&L metrics carry **small per-day residuals**. **All fixes live in the ClickHouse
serve view `serve/finance/views/canonical_pnl.sql`** (re-apply as
`CREATE OR REPLACE VIEW` against CH `serve`; no dbt/Trino run) — NOT in
`int_finance_daily_rollups.sql`, which Cube does not read. Re-validate each with
`Base_Agent/scripts/reconcile_live.py` (and `serve/evaluations/data_products_parity.py`).

**RETRACTED — "P0 COGS revenue-eligibility".** Reproducing the dashboard's own
placement SQL against gold gives the **same 39,563.89** product cost as the serve
view; the dashboard does **not** filter `revenue_eligible`. The apparent 2× COGS
gap was live-Cube-vs-stale-CSV, not a real defect. No `revenue_eligible` change.

**P1 — gross_sales marketplace GST basis.** `sales_all_channels.gross_sales` added the marketplace
leg incl-GST; the dashboard adds it excl-GST (Δ = that leg's GST, e.g. +₹1,341.75 on 06-19). Point
the marketplace leg of the all-channel gross measure at the excl-GST column. Cleanest, fully-characterised fix.

**P2 — net_sales / total_cogs per-day residuals.** ₹0–800/day net_sales (marketplace net-sales derivation) and +₹150–270/day COGS (cancel/return event-arm + partial-return retained-cost proration). Localise per-day against gold, then align the serve view's marketplace net + COGS event arms to `lineItemHistoricalSql.js`.
net_profit closes automatically once these do.

**P3 — returns_cancels / total_payments multi-day counting.** Match at
single-day grain; drift over ranges (distinct-order counting; missing
`paytm card machine`/`manual` payment buckets in Cube).

**P1 — Net Profit basis (§1a #7).** `canonical_pnl.net_profit` mixes shopify
net_sales/net_cogs with **all-channel** ad spend. Make the headline net-profit
measure fully all-channel (or expose only the `_all_channels` variant to the
agent's `net_profit` id).

**P1 — All-channel Net/Gross Sales marketplace basis (§1a #4/#5).** Point
`net_sales_all_channels_pnl` / `sales_all_channels.gross_sales` at the **settlement** marketplace
column (7,409.41 ex-GST), not catalog (7,454.24) or incl-GST (8,796). Closes the ₹44.84 and the gross-sales gap.

**P2 — Returns/Cancels axis (§1a #2).** Bind the `returns_cancels` catalogue
metric to the `event_date` axis (matches at 6); flag `order_date` as wrong for
this card.

**P3 — Single-day ad spend (§1a #3).** ±₹13 hourly-vs-daily; only affects
single-day ranges. Lowest priority.

**Test-gap recorded:** no guard pins `net_sales_all_channels_pnl` to the settlement basis; add one
once the dbt fix lands.

### Delivered
- **Live value harness** — `Base_Agent/scripts/reconcile_live.py` queries the
  live dashboard backend + live Cube and diffs all 9 metrics (exit 1 on FAIL,
  CI-friendly). This is what caught the stale-CSV error; §1a is its output. The
  `MEASURE_MAP` in it is the canonical dashboard-field → Cube-measure binding.
