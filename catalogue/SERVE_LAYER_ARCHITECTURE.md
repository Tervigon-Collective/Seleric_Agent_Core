# Serve Layer Architecture & Cross-Layer Reconciliation

Status: baseline measured 2026-09-07. Enforced by `scripts/reconcile_layers.py`.

This document covers three things that are really one thing: what the serve
database owes the gold database, what OpenMetadata owes Cube, and what both owe
the client sitting in front of the agent.

---

## 0. How the layers are supposed to relate

```
gold (ClickHouse, 78 tables)          facts + conformed dimensions, ReplacingMergeTree
  |                                   physical model, GST/basis rules resolved here
  v   denormalise: fact + its dimensions, one row per business grain
serve (ClickHouse, 41 relations)      wide, flat, query-shaped output ports
  |                                   ONE relation per data-product output port
  v   1:1
Cube cubes (41)                       measures/dimensions with types + formats
  |
  v   compose: certified surface
Cube views (39)                       what a client may actually query
  |
  +--> OpenMetadata   domain / data product / contract / owner / DQ  (governs)
  +--> agent catalogue metrics, dimensions, modules, freshness       (navigates)
```

Two rules make this tractable, and both are now machine-checked:

1. **Every gold fact or mart reaches serve, or carries a written waiver.** An
   unreachable gold table is invisible to Cube, to OpenMetadata and to the agent
   — it is work that produced nothing a client can ask for.
2. **A Cube view is not a surface until three things name it**: an ontology
   domain (owner + module scope), an OpenMetadata data product (certification +
   contract), and `catalogue/views.yaml` (date axis + freshness gate). A view
   named by fewer than three is served without governance.

---

## 1. Baseline (measured, not estimated)

| Layer | Count | Gap |
|---|---:|---|
| gold tables | 78 | 26 facts/marts have **no serve port at all** |
| serve relations | 41 | all are ClickHouse `VIEW`s — no materialisation anywhere |
| Cube cubes | 41 | 3 belong to no certified view |
| Cube views | 39 | 11 claimed by **no** OpenMetadata data product |
| OM data products | 14 | `EventStream` is exposed by the agent but does not exist in OM |
| OM contracts | 31 | — |
| catalogue metrics | 238 | 34 sit on views no module can reach; **6 name a Cube member that does not exist** |
| Cube measures/dimensions | ~1000 | **958 carry no description** |

Baseline on 2026-09-07 after the fixes in §3.1: **53 blockers, 1293 warnings,
1 waived**. Run `py scripts/reconcile_layers.py` for the live list; exit code is
1 while any blocker stands, so it can gate CI directly.

---

## 2. Gold → serve: the denormalisation the serve layer is missing

### 2.1 What is unreachable

26 gold facts and marts have no path into serve. They fall into four groups,
and the group determines the fix:

| Group | Tables | Verdict |
|---|---|---|
| **Finance depth** | `fct_daily_pnl` (145 cols), `fct_finance_waterfall_daily`, `fct_payments`, `fct_product_variant_cost`, `fct_product_variant_cost_history` | Real gap. The waterfall and payment-level detail behind the P&L cannot be reached, so the agent can state net profit but cannot decompose it. |
| **Amazon depth** | `fct_amazon_ads_ads_daily`, `fct_amazon_ads_ad_groups_daily`, `fct_amazon_ads_ad_order_attribution`, `fct_amazon_order_items`, `fct_amazon_returns_daily`, `fct_amazon_sp_settlements`, `fct_amazon_sp_settlement_lines` | Real gap. Amazon ads stop at campaign grain while Meta reaches ad grain — the asymmetry is invisible to a client. |
| **Growth marts** | `mart_ad_funnel_daily`, `mart_landing_page_daily`, `mart_meta_ad_neurotag_daily` (123 cols), `mart_meta_ad_daily_combined`, `mart_meta_ad_daily_performance`, `mart_geo_*`, `mart_city_*`, `fct_city_weather_day` | Built and populated, served to nobody. Either port them or retire them — a mart with 29k rows and no consumer is a maintenance liability. |
| **Change history** | `fct_meta_ads_status_history`, `fct_google_ads_status_history`, `fct_order_attribution_credit` | Real gap, and the highest-value one: without status history the agent cannot answer "what changed before performance moved", which is the question clients actually ask. |

### 2.2 What is served but not denormalised

37 cases where a serve relation carries a foreign key but none of the
attributes behind it, so a client can filter by an opaque id and nothing else:

- `serve.meta_ads_daily` / `meta_ads_hourly` / `meta_ads_breakdown_daily` carry
  `creative_id` but **no creative attributes** — no creative name, CTA, format,
  thumbnail, or destination URL. Creative performance analysis is impossible
  through the semantic layer even though `gold.dim_creative` has 41 columns.
- The same three carry `ad_id`/`adset_id` but no `dim_ad_neurohack_map` tags,
  no `dim_adset` bid strategy or budget, no `dim_adset_geo` targeting.
- `serve.google_ads_daily` and `google_ads_hourly` carry `campaign_id` but no
  `campaign_status`, `campaign_objective`, budget or bid strategy — while
  `serve.meta_ads_daily` and `serve.amazon_ads_daily` do carry
  `campaign_status`. This is exactly why `catalogue/dimensions/core.yaml`
  can map `campaign_status` for Meta and Amazon but not Google.
- `serve.session_funnel` carries the ad key set but no campaign/adset/ad names.
- `serve.purchase_sequence` carries `customer_id` but no customer attributes.

### 2.3 Target shape for a serve relation

A serve relation is an **output port**, not a query convenience. It should be:

- **One per data-product output port**, named for the business object, not the
  source table.
- **Denormalised to its own grain**: the fact's measures plus every descriptive
  attribute of the dimensions it keys into. If a client would want to group or
  filter by it, it is a column here — not a join they cannot express.
- **Basis-explicit in the column name** where two bases coexist
  (`gross_sales_excl_tax` vs `amazon_gross_revenue` incl. GST). The GST boundary
  between Shopify and Amazon is the single largest source of wrong answers, and
  naming is the cheapest defence.
- **Carrying its own provenance columns**: `is_final`, `source_basis`,
  `data_as_of`, `model_version` — the Meta/Google/Amazon ads relations already
  do this and it should be the standard, not the exception.

### 2.4 Materialisation

Every serve relation is currently a ClickHouse `VIEW`. That is correct for
thin projections but wrong for the wide denormalised relations described above:
each query re-executes the dimension joins against `ReplacingMergeTree` tables
with `FINAL`. Recommendation: keep views for 1:1 projections; move any relation
that joins two or more gold tables to a refreshable materialised target on the
gold sync cadence, and expose `data_as_of` so the freshness gate keeps working.

---

## 3. OpenMetadata ↔ Cube reconciliation

### 3.1 What was broken, and what has been fixed

Fixed in this pass:

- Four data products (`SessionFunnel`, `CustomerData`, `ChannelAttribution`,
  `AmazonCommercePerformance`) declared **zero** Cube output ports, so their
  certified views were governed by nothing. Cube ports added and
  `product_registry.yml` regenerated.
- `AmazonCommercePerformance` resolved to `amazon_finance_charge_types_daily`,
  a serve table name that is not a Cube view. Corrected.
- The Meta and Google **hourly** views belonged to no product and no domain.
  Assigned to their existing platform products.
- Six views certified in OpenMetadata (`touchpoints`, `attribution_paths`, and
  the four `AmazonAccounts` ports) were absent from `catalogue/views.yaml`, so
  the agent had no date axis for them and the staleness gate could not fail
  closed. Registered with date axis and freshness.
- Eight views belonged to no ontology domain, so no module could scope them and
  they inherited no owner. Mapped to Finance / Attribution / PaidMedia.

### 3.2 What still needs a decision (not a code change)

**Eleven Cube views are served to clients but claimed by no data product.** Each
needs an owning product before it can honestly be called certified:

| View | Nature | Suggested home |
|---|---|---|
| `orders_all_channels`, `sales_all_channels`, `returns_cancels_all_channels` | cross-channel unions, mixed GST basis per row | a new `Commerce.CrossChannelCommerce` product — the mixed basis is a contract term, not a footnote |
| `platform_attribution_commerce`, `channel_pnl`, `amazon_attribution_overview` | attribution roll-ups | `Attribution.ChannelAttribution`, or a new `Attribution.ChannelPnl` |
| `daily_pnl` | historical alias of `canonical_pnl` | `Finance.CanonicalPnl` — or deprecate it |
| `ltv_cac` | new-customer LTV/CAC with ad spend | `Customer.CustomerIntelligence`, or a new `Finance.UnitEconomics` |
| `product_ad_spend` | directional SKU-day allocated spend | `Product.ProductPerformance`, flagged directional |
| `web_events`, `web_events_daily` | event-grain stream | **`WebAnalytics.EventStream` — the agent registry and ontology already reference this product, but it does not exist in OpenMetadata.** Create it. |

**Declared lineage does not match actual SQL** (26 findings). Most consequential:
`canonical_pnl` — the P&L view — declares almost none of the nine gold tables it
actually reads, and `commerce_orders` claims `gold.dim_customers`, which it does
not read (which is also why it carries no customer attributes). A client shown
this lineage is shown something untrue.

**`serve.amazon_orders`** exists, is governed as an `AmazonCommercePerformance`
output port, and has no cube — built, then never exposed.

---

## 4. Semantic navigation

The single largest navigability gap: **958 of ~1000 Cube measures and dimensions
carry no description at all**, across all 41 cubes. `serve_session_funnel` alone
has 65 undescribed members. Cube's `/meta` endpoint is what any client-side
explorer reads, so today that surface is a list of names with no meaning
attached. The agent partly compensates through `catalogue/metrics/*.yaml`, but
only for the 238 members that have a catalogue metric.

Improved in this pass — `catalogue://cubes/{view}` now renders, per view:

- owning **domain**, **data product**, serve table, gold inputs and contract
- domain grain and its written scope/boundary notes
- the date axis, or an explicit warning when the view is master data with none
- every metric with its description **and** formula
- every dimension with its description, synonyms, and allowed values

Standards the gate now enforces:

- A **view description** must state grain, date axis, scope and boundary. Seven
  are still one-liners (all four `AmazonAccounts` ports, `touchpoints`,
  `attribution_paths`, `return_lifecycle`).
- A **metric description** must state basis, scope, date axis and what it is
  *not*, so the agent cannot silently substitute a sibling. Seventeen are still
  under 60 characters — mostly raw platform counters (`google_clicks` at 18
  chars, `meta_clicks` at 23) where the ambiguity between platform-reported and
  first-party is precisely the trap.
- Every metric needs a worked example (122 missing) and every shared dimension a
  description (45 missing).
- 34 metrics map to views no module resolves to, so a module-scoped deployment
  cannot reach them.
- Six ratio metrics name a numerator Cube does not expose
  (`session_funnel.page_depth`, `session_funnel.seconds_to_*`,
  `order_attribution.attribution_confidence`,
  `purchase_sequence.days_since_prev_order`). Their own measure resolves, so only
  the ratio component is broken — the gate checks `ratio_components` as well as
  `cube_mapping.measure`, and validates both against Cube's live `/meta` rather
  than the view YAML, since a view that includes a whole cube cannot be resolved
  statically. This matches the server's own boot-time drift check exactly.

---

## 5. Remediation order

1. **Governance truth** — assign the 11 unowned views, create `EventStream`,
   fix `canonical_pnl` and `commerce_orders` lineage. Cheap, and everything
   downstream inherits it.
2. **Descriptions** — Cube member descriptions for the top offenders, then the
   17 thin metric descriptions. Highest ratio of client-visible value to effort.
3. **Denormalisation** — creative attributes onto the Meta relations, campaign
   status/objective/budget onto Google, then the dimension attributes listed in
   §2.2. This is what unblocks questions clients already ask.
4. **Coverage** — status history first (change-vs-performance questions), then
   Amazon ad/ad-group grain, then finance depth. Retire the geo/weather/neurotag
   marts or port them; do not leave them in limbo.
5. **Materialisation** — once the wide relations exist, move them off `VIEW`.

---

## 6. Using the gate

```bash
py scripts/reconcile_layers.py                      # full report, exit 1 on blockers
py scripts/reconcile_layers.py --section coverage   # gold -> serve -> Cube only
py scripts/reconcile_layers.py --severity BLOCKER   # CI-relevant subset
py scripts/reconcile_layers.py --json               # machine-readable
```

Accepted gaps go in `catalogue/reconciliation_waivers.yaml` keyed by
`CODE:subject`. A waived finding still prints, marked `WAIVED`, and never gates
— so accepted debt stays visible and attributable instead of disappearing.
