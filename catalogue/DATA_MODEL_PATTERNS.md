# Data models and design patterns

What this stack actually does, measured — not what a style guide says it should.
Every number here came from the live warehouse, Cube `/meta` and the catalogue on
2026-09-07, and each pattern is enforced by `scripts/reconcile_layers.py`
(structural) or `scripts/check_data_quality.py` (data).

---

## 1. The layering model

```
bronze / silver ──► gold ──────────────► serve ─────────► Cube ─────────► agent
  raw + typed      facts + conformed     denormalised     measures &      catalogue
                   dimensions            output ports     dimensions      metric ids
                   ReplacingMergeTree    ClickHouse VIEW  cubes + views   238 metrics
```

The load-bearing idea: **`serve` is a set of data-product output ports, not a
query convenience.** One relation per port, denormalised to its own grain, named
for the business object. Everything downstream — Cube view, contract, domain,
ownership, freshness — hangs off that port.

---

## 2. Patterns that hold

### 2.1 Ratio-of-aggregates, always

**158 of 158** ratio-shaped measures (`*_rate`, `*_pct`, `cpc`, `cpm`, `ctr`,
`roas`, `mer`, `aov`, `*_per_*`) are Cube `type: number` computed expressions.
**Zero** are stored per row or averaged.

```yaml
- name: meta_ctr
  sql: "sum({CUBE}.meta_clicks) / nullIf(sum({CUBE}.meta_impressions), 0)"
  type: number
```

Never `type: avg` over a stored ratio — that weights every row equally and is
wrong the moment rows differ in size.

### 2.2 Deduplication is explicit, one of exactly two forms

Gold is `ReplacingMergeTree` throughout, so a read without deduplication sees
every unmerged version. `gold.fct_order_items` currently carries **81 active
parts**. Two accepted forms:

```sql
FROM gold.fct_orders AS o FINAL                       -- form A: FINAL after the alias
```
```sql
SELECT brand_id, order_id, argMax(col, _loaded_at)    -- form B: explicit argMax
FROM gold.fct_order_attribution GROUP BY brand_id, order_id
```

Form B is used where the view already aggregates. Both are correct; **neither is
optional**. Audited across all 43 serve SQL files — one violation found and
fixed (`amazon_finance_charge_types_daily`, §8.1 of the architecture doc).

### 2.3 The provenance quartet

Every serve relation should carry:

| Column | Meaning |
|---|---|
| `is_final` | the row is settled and will not restate |
| `source_basis` | which upstream produced it (`insights_api_daily`, …) |
| `data_as_of` | source watermark, the freshness gate's input |
| `model_version` | the modelling contract that produced this shape |

Conformance is **2.2 / 4 on average** and splits sharply — see §3.1.

### 2.4 Date axes are named for their semantics

| Axis | Meaning | Uses |
|---|---|---|
| `report_date` | platform/delivery day | 55 |
| `order_date` | placement day | 17 |
| `event_date` | when the cancel/return happened | 4 |
| `refund_date` | refund event day | 4 |
| `session_date` | session start | 2 |
| `*_at_ist` / `*_ts` | intraday timestamp for sub-daily grouping | 10 |

Placement and event axes are **not interchangeable**: a return is placed on one
day and returned on another, and summing across both double-counts. The serving
axis is declared once, in the port's contract
(`semantics.serving_date_axis`), never in the agent catalogue.

A DATE column grouped by hour collapses to midnight, which is why hourly ports
declare a separate `serving_datetime_axis` (`report_hour_ts`).

### 2.5 Denormalise to the grain; arrays for 1:many

A fact carrying `creative_id` and none of the creative's attributes is not
queryable — an agent can filter by an opaque id and nothing else. Join the
dimension in, but only when it is provably 1:1 on the join key under `FINAL`:

- `dim_creative`, `dim_ad`, `dim_adset`, `dim_campaign` — all 1:1, safe to join.
- `dim_ad_neurohack_map` — 4,137 rows / 2,037 ads, so a direct join **doubles
  every row**. Aggregate to arrays instead.

Always verify grain via a shadow relation before promoting: row count, distinct
key count and every total must be unchanged.

### 2.6 Configuration values are `max()`, never `sum()`

Budgets, bid amounts and limits describe a setting, not a quantity. Summing a
daily budget across 30 days or 50 ads produces a number with no meaning.

### 2.7 The catalogue metric id is the disambiguation layer

This is the most important pattern in the stack, and the least obvious.

**Cube measure names are deliberately ambiguous.** 99 measure names appear on
three or more certified views. Measured over the same 30 days and brand:

| Name | Views | Range | Spread |
|---|---|---|---|
| `net_sales` | 5 | ₹58,117 … ₹33,86,315 | **58×** |
| `orders` | 11 | 70 … 1,963 | **28×** |

That is not a defect. `orders` on `commerce_orders` (Shopify placements, 1,847),
on `channel_attribution` (adds Amazon, 1,963), on `attribution_paths`
(attribution-matched only, 1,319) and on `amazon_order_item_pnl` (Amazon items,
70) are four genuinely different business questions.

The safety comes one layer up: each is a **separate catalogue metric with its own
id and a description that states the basis, the axis, the cohort, and what it is
NOT**, cross-referencing its siblings. `commerce_net_revenue` vs
`net_sales_all_channels` vs `meta_attribution_net_sales` are unambiguous where
`net_sales` is not.

**Consequence:** any consumer that reaches Cube without going through the
catalogue gets the ambiguous layer with none of the disambiguation, and will
silently pick one of five answers. That is the real cost of the unauthenticated
public Cube endpoint, beyond the data exposure itself.

### 2.8 One owner per fact, everything else generated

See §7 of `SERVE_LAYER_ARCHITECTURE.md`. ClickHouse owns lineage, the Cube model
owns view membership, OpenMetadata owns governance and grain, the catalogue owns
business semantics. `crosswalk.generated.yaml` derives the rest.

---

## 3. Anti-patterns found in this codebase

### 3.1 Ungoverned views skip every pattern

The single strongest correlation in the audit:

| | Provenance conformance |
|---|---|
| Views with an owning data product (30) | **2.87 / 4** |
| Views with **no** owning product (8) | **0.25 / 4** |

All eight also lack a data contract. **The contract is what forces the pattern** —
it declares grain, required columns, serving axis and quality tests, and nothing
downstream can resolve without it. A view without one drifts by default.

Fix ownership first; the modelling discipline follows.

### 3.2 Exposing dimensions that carry no signal

**24 agent-exposed dimensions** are empty or hold a single constant. Grouping by
one returns a single bucket that the agent presents as a real breakdown —
`commerce_orders.utm_source` puts 100% of revenue in one unlabelled row.

A dimension belongs in a certified view only when it varies. Five such
dimensions were introduced and removed in the same session, caught by
`check_data_quality.py`; the cube definitions were kept with a breadcrumb so
they can be re-exposed when the loader populates them.

### 3.3 Declaring lineage instead of deriving it

Nine views had hand-declared `gold_inputs` that disagreed with their own SQL —
`canonical_pnl` listed five **serve** tables as gold inputs plus one it never
reads, while its nine real inputs went undeclared. Lineage is read from the view
SQL now, never written by hand.

### 3.4 Picking a port alphabetically

An inline-SQL cube touches every table it reads, so "first table referenced" is
not the output port. This gave `daily_pnl` the `amazon_attribution_overview`
port instead of `canonical_pnl`, and with it the wrong contract. Resolution
order is now: view name → the product's declared port → the cube's own name.

### 3.5 Conflating grain with the serving axis

`grain.time_dimension` answers "what makes a row unique"; `serving_date_axis`
answers "what does a consumer filter on". `customer_ltv` has customer grain — no
time in its key — yet serves `last_order_at`. Conflating them made it look
axis-less.

---

## 4. Adding a new data product

1. Model the fact and its conformed dimensions in `gold`.
2. Write `serve/<domain>/views/<port>.sql` — denormalised to grain, `FINAL` or
   `argMax`, provenance quartet, basis-explicit column names.
3. Verify grain on a shadow relation before promoting.
4. Add the cube and include it in a certified view.
5. Declare the product in `mage-ai/openmetadata/products/` with both
   `- serve.<table>` and `- cube_view: <view>` output ports.
6. Write the contract: `grain.key`, `semantics.serving_date_axis`,
   `schema.required_columns`, `quality.tests`.
7. Write catalogue metrics with ids that disambiguate against every sibling
   sharing a Cube measure name.
8. `generate_product_registry.py` && `sync_catalogue_from_sources.py`.
9. `reconcile_layers.py` and `check_data_quality.py` must both come back clean.

Steps 5–6 are the ones that get skipped, and §3.1 is what that costs.
