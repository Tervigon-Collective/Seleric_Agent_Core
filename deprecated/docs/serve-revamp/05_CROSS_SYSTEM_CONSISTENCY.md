# 05 — Cross-System Consistency

The revamp touches four systems (serve marts, Cube, catalogue+concepts,
OpenMetadata) plus the agent. They stay aligned **by construction**, not by manual
edits in five places. This preserves the existing discipline
(`../../catalogue/SERVE_LAYER_ARCHITECTURE.md §7`) and extends it to the concept
layer.

## 1. Source of truth — one owner per fact

| Fact | Owner (edit here) | Read by |
|---|---|---|
| table/column existence, gold→mart lineage | **ClickHouse** (`system.tables/columns`, the mart's own SQL) | reconcile, sync |
| mart definitions (SQL, grain, joins, materialization) | **`data_platform/mage-ai/infra/cube/model/`** (mart SQL + cube yml) | Cube, sync |
| Cube view names, members, types | **Cube model** (`/meta` + `cubes/*.yml`) | catalogue validate |
| mart ↔ serve output port, domain, owner, certification | **OpenMetadata** (`.../openmetadata/{domains,products,contracts}/*.yml`) | sync, registry |
| grain, required columns, DQ tests, serving date axis | **OM contracts** (`contracts/*.yml → semantics.serving_date_axis`) | freshness gate |
| business prose, glossary, ontology, **concepts + axes** | **`Base_Agent/catalogue/`** (`concepts/*.yaml`, `glossary`, `ontology`, `dimensions/channel_map.yaml`) | resolver, agent |

Rule (unchanged): **a mart is a governed surface only when three things name it** —
an OM product (owner+certification), an OM contract (grain+date axis+DQ), and
`catalogue/views.yaml` (date axis + freshness). The concept layer adds a fourth
requirement for the *agent* surface: every concept `resolves[].metric` must bind to
a live Cube member.

## 2. The generator flow (edit once, propagate)

```
edit mart SQL + cube yml + OM product/contract
        │
        ├─ py infra/openmetadata/generate_product_registry.py     # OM product registry
        │
        └─ py scripts/sync_catalogue_from_sources.py              # → catalogue/openmetadata/crosswalk.generated.yaml
                 (domains, products, per-view provenance, verified gold inputs, date axis, freshness, members)
        │
        └─ py scripts/sync_openmetadata_catalogue.py              # OM ↔ catalogue registry/contracts overlay
```

The loader overlays `crosswalk.generated.yaml` at startup; generated values win,
hand-maintained entries are preserved and flagged until they get an owner
(shrinking-backlog migration, not big-bang). **No hand edits** to
`views.yaml`/`registry.yaml`/`contracts.yaml`/`ontology.yaml` for lineage — edit the
owner, regenerate.

## 3. OpenMetadata rewrite — one product per mart

Today: 14 data products across 37 views (e.g. `CommercePerformance` +
`CrossChannelCommerce` both on commerce). Target: **one product per mart**, one
primary contract per product:

| OM Data Product | Domain | Mart (primary serve table) | Contract |
|---|---|---|---|
| Orders | Commerce | serve.mart_orders | mart_orders_contract_v1 |
| OrderItems | Product | serve.mart_order_items | mart_order_items_contract_v1 |
| ChannelDaily | Attribution/Commerce | serve.mart_channel_daily | mart_channel_daily_contract_v1 |
| AdsDaily | PaidMedia | serve.mart_ads_daily | mart_ads_daily_contract_v1 |
| PnlDaily | Finance | serve.mart_pnl_daily | mart_pnl_daily_contract_v1 |
| Customers | Customer | serve.mart_customers | mart_customers_contract_v1 |
| Sessions | WebAnalytics | serve.mart_sessions | mart_sessions_contract_v1 |
| Refunds | Operations | serve.mart_refunds | mart_refunds_contract_v1 |
| Payments | Finance | serve.mart_payments | mart_payments_contract_v1 |
| AdStatus | PaidMedia | serve.mart_ad_status | mart_ad_status_contract_v1 |

Each contract keeps the existing shape (`grain`, `time_dimension`,
`required_columns`, `quality_tests`) — now one per mart instead of per view. Modules
(`catalogue/modules.yaml`) map 1:1 to domains, unchanged.

## 4. The consistency gate (fail closed)

Extend `scripts/reconcile_layers.py` with a **concept-alignment section** asserting
the full chain:

```
concept.resolves[].metric  →  catalogue metric  →  mart.measure (Cube member)
                                                 →  Cube view  →  serve mart  →  gold (verified)
                                                 →  OM product + contract + date axis
```

Blocks (exit 1) on: a concept pointing at a missing metric/member; a metric not
bound to any concept or alias; a mart column with no gold lineage; a Cube view with
no OM product; two metrics colliding on `(mart, measure, axes)` (the uniqueness
rule). `scripts/check_data_quality.py` adds: mart grain-uniqueness, P&L identities on
`mart_pnl_daily`, non-additive-not-summed, and channel catch-all present.

Waivers stay in `catalogue/reconciliation_waivers.yaml` (visible, attributable).

## 5. What each system ends up with

- **serve**: ~12 relations (was 41), wide, materialized where multi-join.
- **Cube**: ~12 cubes (`public:false`) + ~12 views (was 41/37).
- **OpenMetadata**: ~10 products, 1 primary contract each (was 14/31).
- **catalogue**: unique metrics + `concepts/*.yaml` + `aliases.yaml` +
  `channel_map.yaml`; glossary reframed term→concept.
- **agent** (`Seleric_Agent`): resolves via concepts — `06_AGENT_INTEGRATION.md`.

All regenerated from the owners in §1; `reconcile_layers.py` = 0 unwaived blockers is
the definition of "consistent".
