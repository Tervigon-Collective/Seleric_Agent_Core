# Serve Revamp — Overview

Status: **design, for build**, 2026-09-26. Owner: analytics platform.
Consuming project: `Seleric_Agent` (`seleric_swarm`).

This folder is the single source of truth for the serve→cube→catalogue→OpenMetadata
revamp: **the target data models and the plan to build them.** Read in order:

| # | Doc | What it gives you |
|---|---|---|
| 00 | **OVERVIEW** (this) | Problem, goals, principles, target at a glance, migration approach |
| 01 | CONCEPTUAL_MODEL | Domains, business entities, the 28 concepts, governing rules |
| 02 | LOGICAL_MODEL | The ~10 wide marts as logical tables — grain, columns, keys, ERDs |
| 03 | PHYSICAL_MODEL | ClickHouse mart shape, materialization, serve→cube→view mapping, types |
| 04 | METRIC_CATALOGUE_AND_CONCEPTS | 1-metric-1-meaning rule, concept schema + resolves tables, aliases |
| 05 | CROSS_SYSTEM_CONSISTENCY | Source-of-truth ownership, generators/gates, OM rewrite |
| 06 | AGENT_INTEGRATION | Seleric_Agent resolution changes, retry reduction, multi-domain/level |
| 07 | DEPARTMENT_QUESTION_MAP | Dept × question → concept+axes+mart; proves no-overlap coverage |
| 08 | MIGRATION_PLAN | Phased rollout, compat window, verification, rollback |

Analysis appendix (why, in depth): `../../LOGICAL_DATA_MODEL_DESIGN.md`,
`../../DATA_MODEL.md`. Governance baseline: `../../catalogue/SERVE_LAYER_ARCHITECTURE.md`.

---

## 1. The problem

The stack is individually well-built and governed, but **agentic query resolution
is unreliable**, and the cause is structural:

- **Name-collision namespace.** 171 catalogue metrics whose names collide on
  business words: "revenue" → 7 metrics across 5 views, "sales" → 7 across 4,
  "orders" → 6 across 5. The agent inverts *concept → metric* by fuzzy matching,
  which flips on phrasing (P1-1) and drives retries.
- **37 overlapping serve views.** The same business idea is spread across views by
  axis (all-channels vs Shopify, attribution vs P&L, order vs daily vs event).
- **37% draft.** More than a third of metrics resolve to something uncertified, so
  the intended answer is often unreachable while a wrong sibling wins.
- **Wide per-channel columns** (`meta_spend`, `google_spend`, `channel_pnl.meta_*`)
  don't scale — a new channel (WhatsApp) means edits everywhere.

## 2. Goals

1. **One metric = one meaning.** Every metric maps to exactly one physical
   mart.measure; no two metrics mean the same thing at the same axis.
2. **Deterministic resolution.** A business concept + explicit axes resolves to one
   metric by lookup, not fuzzy rank — no phrasing flips, far fewer retries.
3. **Fewer models.** Collapse 37 serve views → **~10 wide agentic marts**, one per
   grain, denormalized so multi-domain questions are answerable in one query.
4. **Extensible by data, not code.** New channels/sub-channels are rows, not new
   metrics or columns.
5. **Consistency across all layers.** serve, Cube, catalogue and OpenMetadata stay
   aligned by construction, machine-gated.
6. **Depth.** Multi-domain (commerce + attribution + P&L in one answer) and
   multi-level (drill campaign→adset→ad, or channel→sub-channel) without new models.

## 3. Design principles

1. **OBT-per-grain + semantic/concept layer.** Wide denormalized table per business
   grain (joins pre-resolved so the agent never picks one), fronted by a concept
   layer so the agent picks a *concept + axes*, not a table. (Rationale:
   `../../DATA_MODEL.md`; star stays in gold, wide-per-grain in serve.)
2. **One fact per grain; never fan-out.** A mart pulls 1:1 / M:1 attributes freely,
   never a measure across 1:M without pre-grouping.
3. **Axes are first-class and closed.** `basis / scope / attribution / platform /
   grain / time_basis` — enumerated, with defaults, disclosed on use.
4. **Channel is a dimension value, not a name.** meta/google/whatsapp are rows in a
   channel-keyed mart; a normalization map is the only place a channel is declared.
5. **Basis in the column name.** `*_excl_tax` / `gross_*` / `net_*` / `*_incl_tax`.
6. **Provenance on every mart.** `is_final`, `source_basis`, `model_version`,
   `data_as_of`; FINAL/argMax discipline over ReplacingMergeTree gold.
7. **Single source of truth per fact; everything downstream generated** (§05).

## 4. Target at a glance — 37 views → ~10 marts

| Mart | Grain | Replaces |
|---|---|---|
| `mart_orders` | brand×order_id | commerce_orders, commerce_order_events(→order), order_attribution(1:1), purchase_sequence, platform_attribution_commerce |
| `mart_order_items` | brand×order_id×line_item_id | product_performance |
| `mart_channel_daily` | brand×report_date×channel | channel_attribution, channel_pnl, sales/orders/returns_cancels_all_channels |
| `mart_ads_daily` (+hourly, +breakdown partitions) | brand×report_date×channel×campaign×adset×ad | meta_ad_performance(+hourly,+breakdown), google_ad_performance(+hourly), ad_channel_pnl, meta_ad_attribution |
| `mart_pnl_daily` | brand×report_date | canonical_pnl, finance_waterfall, ltv_cac |
| `mart_customers` | brand×customer_id | customer_ltv, customer_data |
| `mart_sessions` (+ `_daily`) | brand×session_id / brand×report_date×channel | session_funnel, web_events, funnel_daily, web_events_daily |
| `mart_refunds` (+ `_lines`) | brand×refund_id / refund_line_item_id | refund_events, return_lifecycle |
| `mart_payments` | brand×transaction_id | payments |
| `mart_ad_status` | brand×entity_type×entity_id×changed_at | meta_ads_status_history, google_ads_status_history |
| `mart_attribution_journey` *(optional)* | brand×order_id×touch_id | touchpoints, attribution_paths |

Full column-level design: `02_LOGICAL_MODEL.md` / `03_PHYSICAL_MODEL.md`.

## 5. Migration approach — clean rebuild + short compat window

Nothing is in production yet, so we build the new marts + concept layer **alongside**
the current surface, point Cube/catalogue/OM at them, keep old metric ids and views
resolving as **compat aliases for one release**, then delete. No indefinite dual
maintenance. Phases and gates: `08_MIGRATION_PLAN.md`.

## 6. Success criteria

- Every current serve column traces to a mart column (no capability lost).
- Every catalogue metric binds to exactly one mart.measure; the department-question
  map (§07) shows no metric under two conflicting meanings.
- `reconcile_layers.py` = 0 unwaived blockers across concept→metric→mart→cube→view→OM.
- Concept golden-set test: every legal axis combo → the expected metric; illegal →
  explicit unsupported.
- `Seleric_Agent` eval: reduced retries, correct multi-domain / multi-level answers,
  no P1-1-class flips.
