# 04 — Metric Catalogue & Concept Layer

How the agent goes from a business question to exactly one number, with no
ambiguity. Three artifacts: **unique metrics**, the **concept layer**, and the
**compat-alias registry**.

## 1. The uniqueness rule

> **One metric = one `mart.measure` = one meaning.** No two metrics may denote the
> same quantity at the same axis. A business word ("revenue") is a *concept*, not a
> metric; the axes pick the metric.

This is enforced structurally: a metric file's `cube_mapping` must point at a mart
measure that no other metric points at *for the same axis tuple*. The loader
(`catalogue_service/loader.py::_check_integrity`, extended) fails the catalogue if
two metrics collide on `(mart, measure, axes)`.

Effect on today's 171 metrics: the ~9 collision families (revenue×7, sales×7,
orders×6, profit×4, …) collapse into **one concept each**; the individual old ids
survive only as compat aliases (§4).

## 2. Concept schema

```yaml
# catalogue/concepts/<id>.yaml
id: sales
display_name: Sales / Revenue
one_liner: <board-readable, no SQL>
aliases: [revenue, sales, net sales, topline]         # term → concept (glossary feeds this)
axes:                                                  # closed vocab, one default each
  basis:       { values: [net, gross, total], default: net }
  scope:       { values: [company, shopify, product], default: company }
  attribution: { values: [none, last_touch, channel, platform], default: none }
  grain:       { values: [period, order, daily], default: period }
resolves:                                              # (axis tuple) → exactly one mart.measure
  - when: { basis: net, scope: company, attribution: none }
    metric: mart_pnl_daily.net_sales_all_channels_pnl
  - when: { basis: net, attribution: last_touch }
    metric: mart_orders.attributed_net_revenue
  - when: { basis: net, attribution: channel }
    metric: mart_channel_daily.net_revenue_excl_tax
    filter: { channel_type: attribution_closed_set }
    status: draft
    fallback: { metric: mart_channel_daily.orders, note: "channel net revenue uncertified; report orders or pnl_overview net_sales" }
disambiguation: >
  attribution=none (P&L) and last_touch/channel do NOT reconcile (~20%). If
  attribution is unstated, default to none and say so.
unsupported:
  - { basis: total, scope: product, reason: "no incl-GST measure at line grain" }
```

Properties: **deterministic** (tuple lookup, no fuzzy rank) · **defaults disclosed**
· **draft handled with fallback** · **unsupported returns a reason**, never a wrong
sibling · every `metric:` validated to exist at load.

## 3. The 28 concepts → mart.measure (axis-default binding)

Full axis tables live in each concept file; this is the default resolution + the
axes that apply. (Detailed `resolves[]` for the flagship concepts:
`../../LOGICAL_DATA_MODEL_DESIGN.md §8`, re-pointed here to marts.)

| Concept | Axes | Default → mart.measure |
|---|---|---|
| Sales/Revenue | basis, scope, attribution, grain | mart_pnl_daily.net_sales_all_channels_pnl |
| Orders | attribution, scope, status, grain | mart_orders.orders |
| Profit | basis, scope | mart_pnl_daily.net_profit_all_channels |
| Margin % | basis, scope | mart_pnl_daily.net_margin_pct |
| COGS/Operating Cost | scope, component | mart_pnl_daily.total_operating_cost_all_channels |
| Discounts | scope | mart_pnl_daily.discounts |
| Returns/Cancels | measure, action, scope | mart_channel_daily.returns_cancels |
| Taxes | — | mart_pnl_daily.taxes_on_net_sales |
| Contribution | — | mart_pnl_daily.contribution_margin |
| AOV | attribution | mart_orders.aov |
| ASP | — | mart_order_items.average_selling_price |
| Units Sold | basis | mart_order_items.units_sold |
| Ad Spend | channel(dim), grain | mart_pnl_daily.total_ad_spend / mart_ads_daily.ad_spend |
| ROAS | basis, scope, platform | mart_pnl_daily.net_roas_all_channels |
| MER | scope | mart_pnl_daily.mer |
| CAC | — | mart_pnl_daily.cac |
| LTV | horizon | mart_pnl_daily.ltv (first-order) / mart_customers.avg_ltv (lifetime) |
| LTV:CAC | — | mart_pnl_daily.ltv_cac_ratio |
| Ad Net Profit | channel, grain | mart_ads_daily.net_profit |
| Attributed Revenue | attribution, grain | mart_orders.attributed_net_revenue |
| Attributed Orders | attribution, grain | mart_orders.attributed_orders |
| New/Repeat Customers | type | mart_customers.customers |
| Repeat Rate | measure | mart_customers.repeat_rate |
| Impressions/Clicks/CTR/CPC/CPM | channel, grain | mart_ads_daily.impressions … |
| Reach/Frequency | channel | mart_ads_daily.reach / frequency |
| Sessions | grain | mart_sessions.sessions |
| Conversion Rate | source | mart_sessions.conversion_rate |
| Web Engagement | event | mart_sessions.product_views … |
| Attribution Quality | measure | mart_orders.attribution_rate |

**Channel/platform as a filter, not a metric:** "meta spend" = `mart_ads_daily.ad_spend`
WHERE channel=meta; "whatsapp revenue" resolves identically with channel=whatsapp —
no per-channel metric is ever created (this is what makes the model extensible).

## 4. Compat-alias registry

The old 171 metric ids keep resolving during the compat window, each expanding to a
concept resolution (mart.measure [+ filter]):

```yaml
# catalogue/aliases.yaml   (generated from the old metric files at cutover)
net_sales_all_channels:      { concept: sales,   axes: { basis: net, scope: company } }
commerce_net_revenue_daily:  { concept: sales,   axes: { basis: net, scope: shopify, grain: daily } }
attributed_net_revenue:      { concept: sales,   axes: { basis: net, attribution: last_touch } }
meta_spend:                  { concept: ad_spend, axes: { grain: campaign }, filter: { channel: meta } }
google_attribution_net_sales:{ concept: sales,   axes: { basis: net, attribution: platform }, filter: { channel: google } }
# … one row per retired metric id and per retired Cube member.
```

The agent and any saved query keep working; after the compat window the aliases are
deleted (`08_MIGRATION_PLAN.md`).

## 5. Draft policy

Drafts are bound as `status: draft` **with a fallback** (as in §2), so a concept
always yields a usable answer + an honest caveat — a draft never silently wins
(P1-1) or silently vanishes. Certifying vs deleting each of today's 64 drafts is a
separate data-quality decision tracked in the migration; the concept layer makes the
draft state explicit meanwhile.

## 6. Axis dictionary (canonical, shared)

Identical to `01_CONCEPTUAL_MODEL.md §4` — `basis / scope / attribution / platform /
grain / time_basis`, closed values, one default each, disclosed on use. This one
vocabulary spans all 28 concepts, which is what makes resolution composable rather
than per-metric.

## 7. Resolver

```
resolve(query):
  concept = alias/glossary match query → concept.id
  axes    = parse axis keywords ("net"/"gross","shopify","by channel","meta","last-touch","per campaign")
  axes    = fill_defaults(concept, axes)
  row     = concept.resolves.match(axes)
  if row is None:         return UNSUPPORTED(concept, axes, nearest_supported)
  if row.status == draft: return RESOLVED(row.fallback.metric, note=row.fallback.note)
  return RESOLVED(row.metric, row.filter, defaults_applied=..., disambiguation=concept.disambiguation)
```

Lives in `catalogue_service/service.py` as `resolve_concept` (new), fronting the
existing `resolve_term`/`search`. Agent integration: `06_AGENT_INTEGRATION.md`.
