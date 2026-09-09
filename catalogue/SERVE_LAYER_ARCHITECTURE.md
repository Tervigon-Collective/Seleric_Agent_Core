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

Baseline on 2026-09-07 after the fixes in §3.1 and the source-of-truth work in
§7: **50 blockers, 1296 warnings, 1 waived**. Run `py scripts/reconcile_layers.py` for the live list; exit code is
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

Closed 2026-09-09 (no longer a product-assignment decision):

- `Commerce.CrossChannelCommerce` owns `orders_all_channels`, `sales_all_channels`, `returns_cancels_all_channels` (mixed GST is a contract term). Agent registry now exposes the product.
- `Attribution.ChannelAttribution` owns `channel_pnl` and `platform_attribution_commerce`; `AmazonCommercePerformance` owns `amazon_attribution_overview`.
- `daily_pnl` is an alias port of `Finance.CanonicalPnl`. Prefer `canonical_pnl` for new catalogue metrics.
- `ltv_cac` is on `Customer.CustomerIntelligence`.
- `WebAnalytics.EventStream` exists in OpenMetadata and the agent registry (`web_events`, `web_events_daily`).
- `serve.amazon_orders` has a cube and is a certified `amazon_orders` view.
- `product_ad_spend` was removed from Cube and `catalogue/views.yaml` — `serve.product_ad_spend_daily` does not exist. Restore only after the table is rebuilt.

Declared lineage now matches ClickHouse gold-closure in `catalogue/openmetadata/registry.yaml` (verified 2026-09-09). `canonical_pnl` no longer claims `gold.fct_daily_pnl`; `commerce_orders` no longer claims `gold.dim_customers`.

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

---

## 7. Source of truth: who owns which fact

The same facts used to be restated in five files across three repos, so adding a
Cube view meant five edits and any one of them could silently rot. Each fact now
has exactly one owner, and everything derived from it is generated.

| Fact | Owner | Read from |
|---|---|---|
| table + column existence | ClickHouse | `system.tables` / `system.columns` |
| **gold → serve lineage** | ClickHouse | the serve view's own SQL — never declared |
| Cube view names, members, types | Cube model | `/cubejs-api/v1/meta` + `model/cubes/*.yml` |
| view → serve table (output port) | OM product spec | `output_ports` |
| domain, owner, certification | `openmetadata/domains|products/*.yml` | |
| grain, required columns, DQ tests | `openmetadata/contracts/*.yml` | |
| **serving date axis** | `openmetadata/contracts/*.yml` | `semantics.serving_date_axis` |
| freshness SLA | `openmetadata/slos/freshness.yml` | |
| business prose, entity clusters, attribution boundary, metric semantics | `catalogue/` | hand-authored — no system derives these |

`serving_date_axis` is deliberately distinct from `grain.time_dimension`: a
customer-grain port has no time column in its key but still serves an axis to
filter on. Conflating them is why `customer_ltv` looked axis-less.

### The generator

`scripts/sync_catalogue_from_sources.py` reads those owners and writes
`catalogue/openmetadata/crosswalk.generated.yaml` — domains, data products, and
per-view provenance (owning product, serve table, **verified** gold inputs,
contract, date axis, freshness, members). Output is byte-deterministic for
identical sources, so `--check` distinguishes real drift from a re-run and can
gate CI.

The loader overlays it at startup. Generated values win; entries the generator
cannot derive are **preserved, not dropped**, so a Cube view no product claims
yet keeps working and is reported as `VIEW_HAND_MAINTAINED` until it gets an
owner. That makes this a migration with a shrinking backlog rather than a
big-bang cutover.

### Adding a Cube view now

1. Add the cube + view to `mage-ai/infra/cube/model/`.
2. Declare `- serve.<table>` and `- cube_view: <view>` on the owning product.
3. Add a contract with `grain` and `semantics.serving_date_axis`.
4. `py infra/openmetadata/generate_product_registry.py`
5. `py scripts/sync_catalogue_from_sources.py`

Domain mapping, lineage, contract binding, date axis and freshness all follow.
No edit to `ontology.yaml`, `views.yaml`, `registry.yaml` or `contracts.yaml`.

### What this found on the first run

- `canonical_pnl` declared 5 "gold inputs" that were **serve** tables plus one
  table it never reads; its 9 real inputs were undeclared. 9 views had wrong or
  incomplete lineage.
- The agent-side contract list was stale by 6 contracts and pinned
  `attribution_order_contract_v1`, superseded by v2 in OM.
- `serve.meta_ads_hourly` and `serve.google_ads_hourly` were served and
  cube-exposed but declared as ports by nothing and had **no contract**. Both
  contracts now exist.
- `EventStream` was referenced by the ontology and the agent registry but had no
  OpenMetadata product. Created — WebAnalytics is now fully governed.
- All 39 date axes reproduce the hand-maintained values exactly, from the
  contracts and the Cube model.


---

## 8. Duplicates and denormalisation (2026-09-07)

### 8.1 Duplicates — audited, one real bug

Gold is `ReplacingMergeTree` throughout, so duplicates are a *read* problem, not a
storage problem: a query without `FINAL` sees every unmerged version.

- **Storage:** 70 of 72 gold tables have zero duplicate sorting keys right now.
  The exceptions are `_ch_sync_snapshot_state` (infra) and
  `fct_product_variant_cost_history` (16.1%, and unserved).
- **Reads:** all 43 serve view definitions were audited alias-aware
  (`FROM gold.t AS x FINAL`, not just `FROM gold.t FINAL`). Five relations read
  without `FINAL` but dedupe explicitly with `argMax(...) ... GROUP BY key`,
  which is equivalent and correct: `order_attribution`, `channel_pnl`,
  `amazon_attribution_overview`, `amazon_commerce_daily`,
  `amazon_return_items_delivered`.
- **One genuine bug**, now fixed: `serve.amazon_finance_charge_types_daily` did
  `sum(e.amount)` / `sum(1)` over `gold.fct_amazon_sp_finance_events` and
  LEFT JOINed `gold.dim_amazon_charge_type` with **no `FINAL` on either side**,
  while its own header comment claimed `FINAL`. Latent, not active — both
  sources happen to be merged — but `fct_amazon_sp_finance_events` carries 12
  active parts, so any re-ingest inflates every charge amount until a background
  merge catches up. Totals unchanged after the fix (₹456,548.39 / 33,326 lines).

Note `gold.fct_order_items` runs **81 active parts** and `fct_orders` 63. Nothing
reads them without `FINAL` today, so this is safe — but it is the reason `FINAL`
discipline is not optional here.

### 8.2 Denormalisation — `serve.meta_ads_daily` v2

The relation carried `creative_id`, `adset_id` and `campaign_id` and none of the
attributes behind them, so an agent could filter by an opaque id and nothing
else. Now joined from gold, each verified 1:1 on `(brand_id, key)` under `FINAL`
so the grain cannot change:

| Source | Added |
|---|---|
| `dim_creative` | name, title, body, status, CTA type, thumbnail, destination URL |
| `dim_ad` | ad_format, headline, destination URL |
| `dim_adset` | type, optimization goal, billing event, bid strategy/amount, budgets |
| `dim_campaign` | buying type, bid strategy, budgets, start/end date |
| `dim_ad_neurohack_map` | tag codes, hack names, categories — **as arrays** |

Neurohack tags are 1:many (4,137 rows / 2,037 ads); joining them directly would
have doubled every row, so they are aggregated to arrays and exposed as joined
strings. Budgets are `max()` measures, never `sum()` — summing a budget across
days or ads is meaningless.

**Verified before promotion** via a shadow view: 23,841 rows → 23,841 rows,
identical unique grain key, identical spend / impressions / clicks, zero original
columns lost. Contract bumped to v2 with a `grain_unchanged_by_dim_joins` test.

This closed 5 of the 6 `NOT_DENORMALISED` findings on the Meta relations and
brought `dim_creative`, `dim_ad`, `dim_adset`, `dim_campaign` and
`dim_ad_neurohack_map` into the serve layer. `dim_adset_geo` remains.

### 8.3 What denormalising immediately exposed

**`creative_id` has stopped populating in `gold.fct_meta_ads_daily`.** Coverage
by month:

| … 2026-06 | 2026-07 | 2026-08 | 2026-09 |
|---|---|---|---|
| 99–100% | 91.5% | **13.3%** | **0%** |

So creative-level analysis works on history but returns `(unnamed)` for 92% of
last-30-day spend — not a modelling fault, an upstream Meta loader regression
that started in early August 2026 and nobody had noticed, because nothing
downstream had ever tried to use the column. **This needs a loader fix.**

Related upstream sparsity: only 68 of 1,830 Meta ad sets in `gold.dim_adset`
carry `bid_strategy` (~2%), so `adset_bid_strategy` is documented as
"null means unknown, never absent".

### 8.4 Six broken metrics closed

`session_avg_page_depth`, `session_avg_seconds_to_{add_to_cart,checkout,purchase}`,
`avg_attribution_confidence` and `avg_days_between_orders` each named a ratio
numerator Cube did not expose. All six columns existed in serve; they were simply
never surfaced as measures. Added to the cubes and their views. The server's
boot-time drift check now reports `"broken": []` for the first time.

### 8.5 Closed (2026-09-09)

`reconcile_layers.py` is **0 unwaived blockers**. Remaining findings are explicit
waivers: unused/broken gold facts and marts (`fct_daily_pnl`, cost tables,
settlement upstream, platform-reported Amazon ad-order attribution, unused
growth/geo marts), join-only cubes (`serve_commerce_order_events`,
`serve_product_ad_spend_daily`), 1:many `dim_adset_geo`, and status-history
ports that must not join *current* dims onto snapshot change rows.

New certified ports: Meta/Google status history; Amazon ads ad-group and
product-ad grain; Amazon order items and returns-daily; finance waterfall and
Shopify payments. Cube members and catalogue metrics are fully described.

### 8.5 historically open (superseded)

The 2026-09-07 snapshot below is kept as provenance. It is no longer the live gate:

- **26 gold facts/marts with no serve port** — now ported or waived.
- **9 Cube views with no owning data product** — products now claim them.
- `serve.amazon_orders` has no cube — cube + view exist.
- 28 remaining `NOT_DENORMALISED` — Google/Meta/session/purchase denormalised;
  `dim_adset_geo` waived (1:many).
- ~950 Cube members still undescribed — descriptions applied 2026-09-09.


---

## 9. Logic, reconciliation and data bug hunt (2026-09-07)

Codified as `scripts/check_data_quality.py` (data-level, slow) alongside
`scripts/reconcile_layers.py` (structural, fast). Waivers in
`catalogue/data_quality_waivers.yaml`.

### 9.1 Clean — verified, not assumed

| Check | Result |
|---|---|
| Serve relations unique on their contract grain | **29 / 29 clean** |
| Catalogue `aggregation` vs Cube measure type | **0 mismatches** (238 metrics vs 990 measures) |
| Non-additive quantities exposed as `sum` (reach, distinct, rates) | **0** |
| Ad spend: platform view vs `canonical_pnl` | **exact**, Meta / Google / Amazon |
| Hourly vs daily spend + impressions | **exact**, Meta and Google |
| P&L accounting identities | **all hold exactly** |

Three P&L identities *appeared* to break. All three were wrong assumptions on my
part about this model's shape, and each is explicitly documented in the
catalogue: `total_operating_cost` is a declared alias of `net_cogs`;
`operating_cost` (ship + pack + gateway + RTO) is already *inside* `net_cogs`;
`contribution_margin` equals `gross_profit` because opex is folded into COGS.
The real identities — `net_cogs = product_cost + operating_cost`,
`gross_profit = net_sales − net_cogs`, `net_profit = contribution_margin −
total_ad_spend` — hold to the paisa.

Likewise the ~20% spread between `meta_attr_net_revenue` (₹16.86L),
`meta_attribution_net_sales` (₹13.84L) and `meta_attribution_total_sales`
(₹17.24L) is **deliberate and documented**: ad-day table oracle vs Overview card
with event-date returns deducted vs incl-GST. Each description states what it is
NOT and cross-references the others.

### 9.2 Real bugs found

**UNMAPPED_TENANT — `brand_id = 28`.** Present in 7 serve relations with
**₹23.45L of ad spend** flowing into `canonical_pnl` through today, but absent
from `catalogue/brands.yaml`. The agent cannot name it or answer any question
about it, while its rows land in every all-brand aggregate. (`brand_id = 0` also
appears in `meta_ad_attribution_daily` — a null-key sentinel.)

**COVERAGE_REGRESSION — the whole Meta creative block, not just `creative_id`.**
`creative_id`, `creative_type`, `creative_name/title/status/cta/thumbnail/
image/destination`, `ad_format`, `ad_headline` all fell from ~89% to ~5% in the
last 30 days, across `meta_ad_performance`, `_hourly` and
`meta_ad_breakdown_performance`. One upstream root cause, ten affected
dimensions. Totals stay correct throughout, which is exactly why no
totals-based check would ever catch it.

**DEAD_DIMENSION — 24 dimensions the agent can group by that carry no signal.**
Worst offenders:

- `commerce_orders.utm_source / utm_medium / utm_campaign` — **0 of 22,798
  orders** populated, and empty in `gold.fct_orders` too. "Revenue by UTM
  source" looks answerable and returns one unlabelled bucket holding 100% of
  revenue.
- `session_funnel.device_type / browser_family / os_family` — 0 of 367,503
  sessions. "Conversion rate by device" is a top-five analytics question.
- `meta_ad_breakdown_performance.country / impression_device` — 0 of 293,786
  rows, on the view whose entire purpose is geo/device breakdowns. The real
  breakdowns live in `breakdown_type` (region, age_and_gender, placement,
  platform_device, publisher_platform); these two columns are modelling
  leftovers.
- `amazon_ad_performance.campaign_status / campaign_type / bidding_strategy /
  portfolio_id / daily_budget` — all constant or empty.
- `customer_data.accepts_marketing` — constant `0` for all 29,105 customers.
  Nobody is marked opted-in; treat as unusable for consent decisions.

**Five dead dimensions this session introduced.** `adset_type`,
`adset_billing_event`, `adset_bid_strategy`, `campaign_buying_type` and
`campaign_bid_strategy` came from the §8.2 denormalisation, but
`gold.dim_adset` / `dim_campaign` hold a single constant for Meta. Removed from
the certified view (definitions kept on the cube, with a breadcrumb) and dropped
from 28 metric allowlists. The structural gate then caught the dangling
`campaign_buying_type` entry left in `catalogue/dimensions/core.yaml` — the two
gates cross-checking each other.

### 9.3 Waivers

19 `brand_id` / account findings are waived with reasons: Amazon SP, Snowplow and
hourly ad ingest are onboarded for brand 20 only, so single-tenancy is the correct
state of the world there. Each waiver stops being valid the moment a second brand
is connected, and the finding reappears automatically.
