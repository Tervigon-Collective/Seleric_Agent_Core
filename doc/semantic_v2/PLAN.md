# Seleric semantic layer v2 — plan

One entity model, one metric id per business number, real drill-downs, end-to-end traceability.
Status of each phase: [PROGRESS.md](./PROGRESS.md). Phase 0 record: [PHASE0.md](./PHASE0.md).

## 1. Why

The agent path is Seleric_Agent → Seleric_Agent_Core MCP → Cube → ClickHouse `serve` → `gold`.
The audit on 2026-10-03 found the **physical layer healthy**: all 409 Cube view measures execute,
and the serve views match the repo except `web_events*`. The **semantics are broken**:

| Problem | Evidence (2026-10-03) |
|---|---|
| No relational model | 36 cubes, 1 join, 0 `drill_members`, 0 hierarchies; each metric locked to one view, so net profit (brand × day) cannot be drilled to channel, campaign or product |
| One number, many ids | 23 groups / 56 ids return identical live values (net_profit ×3, orders ×7, contribution_margin = gross_profit, total_operating_cost = net_cogs, meta/google/hourly/breakdown twins) |
| Four resolvers that disagree | glossary fuzzy match, concept layer, search, Seleric_Agent `metric_registry.yaml` — 77 of 163 phrases resolve to different ids ("roas" = gross in the agent, net in core) |
| Five+ channel vocabularies | dbt macros (2 copies), `multiIf` in 4 serve views, `channel_map.yaml`; 4 / 5 / 9 / 16 / 21-value channel columns |
| Misleading scope names | `*_all_channels` = Shopify only; Amazon absent, yet "amazon sales" returned Shopify numbers |
| Provenance stops at `cube_view` | no serve chain (up to 7 deep), gold tables, generated SQL or ClickHouse query id |

## 2. Decisions (confirmed with the user)

1. **Scope:** Shopify now. A `sales_channel` dimension keeps an `amazon` slot that returns an
   explicit "not yet supported" error. Amazon is added later with no metric renames.
2. **Old metric ids: hard cut** on the serve/agent surface. Old ids are rejected with an error
   naming the replacement. Gold is untouched (the Node dashboard reads gold directly).
3. **Channels are dynamic.** Classification rules are data, not code; new UTM sources are
   relabelled without a dbt run, deploy or fact rebuild.
4. **Date axis:** every domain defaults to **order date** (or its own entity's date when there is
   no order). **Finance alone uses event date** (returns / cancels on the day they happen); its
   metric ids carry the `pnl_` prefix. So `net_sales` (order date) ≠ dashboard Net Sales card;
   dashboard parity comes from `pnl_net_sales`.
5. **Production Postgres (seleric_stag) is not touched** (user instruction, Phase 1).
6. **Work stays in the three repos:** mage-ai, Seleric_Agent_Core, Seleric_Agent. Seleric_Agent work
   goes on its `gaurav` branch (synced to `origin/gaurav`, user 2026-10-03).
7. **Channels (user, 2026-10-03):** WhatsApp is its own platform **and P&L channel**
   (`finance_channel` meta | google | whatsapp | organic | unattributed). Meta organic sits under
   platform meta (its presence is built by the paid spend) and rolls up to Meta in the P&L, marked
   `is_paid = 0` / channel `meta_organic` everywhere. Google free listings and YouTube organic sit
   under google the same way; search-engine SEO stays platform organic. Owned pages (order-tracking
   product cards, wishlist) and unknown sources are organic.
8. **Phase 1 label diffs approved** (2026-10-03): 362 orders unattributed → organic/google free
   listing, 33 GA `(direct)` orders → unattributed, 43 orders other → whatsapp/email/sms,
   116,907 no-UTM sessions organic → unattributed.

## 3. Target model

### 3.1 Conformed dimensions (one per entity, shared by every fact)
| Dimension | Drill path | Physical source |
|---|---|---|
| brand | brand (+ coverage flags has_commerce / has_meta_ads / has_google_ads / has_sessions) | `serve.dim_brand` ← `mage-ai/serve/semantic/brands.yaml` |
| time | year → fiscal_year (Apr–Mar) → quarter → month → week → day → hour* | Cube time dims + custom `fiscal_year` granularity, Asia/Kolkata |
| traffic (dynamic) | platform → channel → sub_channel; channel attributes medium_group (paid/owned/earned/none) and **is_paid**; P&L rollup platform → finance_channel | `serve.dim_traffic_source` ← rules YAML + gold facts (5-min refresh) |
| ad | ad_platform → campaign → adset / ad group → ad (→ creative) | existing conformed `gold.dim_campaign / dim_adset / dim_ad / dim_creative` |
| product | product_type → product → variant (sku) | `serve.dim_product_variant` (catalogue ∪ ordered variants) |
| customer | acquisition cohort → customer | `serve.customer_ltv` / `customer_data` |
| geo (on orders) | country → state → city → pincode | order shipping columns |
| sales_channel | shopify \| amazon (slot) | constant today |

\* hour only on facts with an hourly binding.

### 3.2 Facts (one cube per grain, one owning data product)
| Fact | Grain | Default date | serve object |
|---|---|---|---|
| orders | order | order_date | `commerce_orders` (+ traffic_source_key) |
| order_events | order × lifecycle event | order_date | `commerce_order_events` |
| order_sequence | order (1:1) | order_date | `purchase_sequence` |
| order_lines | line item | order_date | `product_performance` |
| ad_delivery | ad × day (hour binding) | report_date | `ad_delivery_daily` / `_hourly` |
| ad_breakdowns | ad × day × breakdown (Meta) | report_date | `meta_ads_breakdown_daily` |
| ad_changes | entity × change | changed_at | `ad_changes` |
| sessions | session | session_date | `session_funnel` (+ traffic_source_key) |
| web_events | event | event_date | `web_events` |
| touchpoints / attribution_paths | order × touch / order × model | order_date | `touchpoints` (+ key) / `attribution_paths` |
| customers | customer | first order | `customer_ltv` |
| refunds / refund_lines / payments | refund / line / txn | order_date | `refund_events` / `return_lifecycle` / `payments` |
| pnl | brand × day × finance_channel × campaign × adset × ad | **event date** | `pnl_daily` (5-min snapshot of `ad_channel_pnl_daily`) |
| pnl_channel | brand × day × finance_channel × is_paid | **event date** | `channel_pnl` (gross COGS; the only P&L fact with the paid / non-paid split) |

### 3.3 Data products → agent views (35 → 14)
commerce · product · paid_media · paid_media_hourly (binding) · paid_media_breakdowns (binding) ·
paid_media_changes · web_sessions · web_events · attribution · customers · returns · payments ·
pnl · pnl_channel (different grain: gross COGS, is_paid).
`finance_waterfall` is retired (broken upstream).

## 4. Metric rules (CI-enforced)
1. One number = one id = one home fact. Other products may *slice* it through joins, never redefine it.
2. Ids carry no scope / platform / grain / axis tokens. Those are dimensions, filters or granularity
   (`meta_spend` → `ad_spend` + `ad_platform=meta`; `*_hourly` → granularity=hour). Only exception:
   the Finance `pnl_` prefix (event date).
3. **Bindings:** a metric may have more than one physical binding, chosen deterministically (hour
   granularity → hourly fact; breakdown dims → breakdown fact). Each extra binding passes a
   reconciliation test.
4. Ratios declare numerator / denominator and are always recomputed. **Composites** spanning facts
   (CAC = `ad_spend` / `new_customers`) declare components and shared keys; the planner composes them.
5. `valid_for` constraints (Meta-only measures error on `ad_platform=google`;
   `sales_channel=amazon` errors as unsupported).
6. Supported dimensions are derived from the Cube join graph, not hand-written.
7. Metric YAML gains `home_product`, `fact`, `date_axis`, `additivity`, `bindings`, `valid_for`,
   `version` (semver; major when the number changes — e.g. an id that is kept but redefined).

The full old → new id table lives in `catalogue/migrations/v2_id_map.yaml` (Phase 4).

## 5. Resolution, drill-down, provenance (Seleric_Agent_Core)
- **One resolver:** concept + axes → exactly one metric. Glossary terms become concept synonyms;
  `resolve_term` and `search` delegate; fuzzy matching only suggests. Missing required axis →
  clarification; unsupported value → explicit error.
- **Seleric_Agent registry:** no own aliases; `catalogue_metric` must exist (CI).
- **Drill-down:** `drill: {hierarchy, to_level}` / "next level", validated against the Cube join
  graph; filters inherited and only narrowed; drill-through to records via `drill_members`.
- **Provenance v2:** metric versions, data product + contract version, cube members, Cube-generated
  SQL, serve views + DDL hash, gold tables, catalogue git sha, ClickHouse query id (resolved from
  `system.query_log` by `normalizedQueryHash` — see PHASE0.md).

## 6. Physical rules (mage-ai)
- Every serve view is `DEFINER = serve_definer SQL SECURITY DEFINER`; Cube v2 logs in as
  `cube_serve` (SELECT serve.* only). Writable derived tables live in `semantic.*`, written only
  by `semantic_refresher`.
- Business logic keeps exactly one home. v2 facts reuse the certified v1 views (adding join keys);
  where a certified product is a deep view chain (P&L) it is materialised as a 5-min snapshot
  instead of being re-implemented.
- Gold changes: none.

## 7. Phases
| Phase | Scope |
|---|---|
| 0 | Baseline values, inventory gates (report-only in CI), Cube→ClickHouse trace, serve-only ClickHouse users |
| 1 | Conformed dimensions: traffic (dynamic rules), brand, product; reuse ad dims |
| 2 | Serve facts: DEFINER everywhere, traffic keys, unified ad delivery / changes, P&L snapshot, channel_pnl on finance_channel |
| 3 | Cube v2 model (`mage-ai/infra/cube/model_v2`): fact + dim cubes with joins, hierarchies, drill members, fiscal year; 14 views; second Cube on 127.0.0.1:4002 (image pinned to `cubejs/cube:v1.6.48`) |
| 4 | Catalogue v2 + MCP (one resolver, bindings, hierarchy drill, composites, valid_for, provenance v2, hard cut) + Seleric_Agent registry |
| 5 | Cutover: MCP → Cube v2; gates blocking; inventory regenerated. v1 Cube stays up only for its remaining external consumer |

## 8. Deviations from the approved plan (and why)
| Plan said | Done instead | Why |
|---|---|---|
| Rules table `core.traffic_source_rules` in Postgres | Rules in git (`mage-ai/serve/semantic/traffic_source_rules.yaml`) → `semantic.cfg_*` in ClickHouse | User: do not touch production Postgres |
| Facts carry `traffic_source_key` via dbt (fact rebuild) | Key computed at query time from columns facts already have; dimension built by a ClickHouse refreshable view | No dbt / orchestrator change, no fact rebuild, rule edits apply within 5 min, zero risk to the hard-fail attribution stage |
| New `gold.dim_ad_hierarchy` | Reuse `gold.dim_campaign / dim_adset / dim_ad` | Already conformed across Meta + Google |
| `dim_brand` from PG `core.brands` | `serve/semantic/brands.yaml` | Postgres off-limits; brand identity now lives in the data layer |
| Serve views read gold only (depth ≤ 1) | Reuse certified views; P&L chain materialised | Re-implementing ~1,100 lines of finance SQL would create a second definition |
| `attribution_credit` fact | `touchpoints` + `attribution_paths` | No catalogue metric needs multi-model credit yet |
| Retire v1 cubes and serve views at cutover | v1 Cube on :4001 stays; agent moves to v2 on :4002 | seleric_systems (outside scope) still reads v1 `daily_pnl.*` via nginx `/cube/` |
| Channel split of P&L from `channel_pnl` `multiIf` | `channel_pnl` takes `finance_channel` from the traffic dimension | One classification for every fact |
| Traffic drill path medium_group → platform → channel → sub_channel | platform → channel → sub_channel; medium_group and `is_paid` are channel attributes | User put Meta organic under meta and Google free listings under google, so one platform holds paid and non-paid channels |
| v1 attribution views keep their own `multiIf` until cutover | `order_attribution` (and the views built on it) take labels from the dimension now, plus `lt_is_paid` etc. | Otherwise `channel_pnl` (Meta incl. organic) and `order_attribution` (Meta organic = unattributed) would disagree on the live v1 surface |
