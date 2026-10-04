# Serve Revamp — data models & plan

Single source of truth for the serve→cube→catalogue→OpenMetadata revamp: collapse
37 overlapping serve views into ~10 wide, unambiguous agentic marts, give every
metric one meaning behind a concept+axes resolver, and keep all layers consistent so
`Seleric_Agent` answers in-depth, multi-domain, multi-level questions with far fewer
retries.

## Read in order

| # | Doc | What it gives you |
|---|---|---|
| 00 | [OVERVIEW](00_OVERVIEW.md) | Problem, goals, principles, target at a glance, migration approach |
| 01 | [CONCEPTUAL_MODEL](01_CONCEPTUAL_MODEL.md) | Domains, entities, 28 concepts, axes, governing rules |
| 02 | [LOGICAL_MODEL](02_LOGICAL_MODEL.md) | The ~10 wide marts — grain, columns, keys, ERDs |
| 03 | [PHYSICAL_MODEL](03_PHYSICAL_MODEL.md) | ClickHouse shape, materialization, keys, serve→cube→view mapping, types |
| 04 | [METRIC_CATALOGUE_AND_CONCEPTS](04_METRIC_CATALOGUE_AND_CONCEPTS.md) | 1-metric-1-meaning rule, concept schema + resolves, aliases, resolver |
| 05 | [CROSS_SYSTEM_CONSISTENCY](05_CROSS_SYSTEM_CONSISTENCY.md) | Source-of-truth ownership, generators/gates, OM rewrite |
| 06 | [AGENT_INTEGRATION](06_AGENT_INTEGRATION.md) | Seleric_Agent resolution changes, retry reduction, multi-domain/level |
| 07 | [DEPARTMENT_QUESTION_MAP](07_DEPARTMENT_QUESTION_MAP.md) | Dept × question → concept+axes+mart; no-overlap proof |
| 08 | [MIGRATION_PLAN](08_MIGRATION_PLAN.md) | Phased rollout, compat window, verification, rollback |

## Analysis appendix (the "why", in depth)

- [`../../LOGICAL_DATA_MODEL_DESIGN.md`](../../LOGICAL_DATA_MODEL_DESIGN.md) —
  query-resolution diagnosis, the Concept Layer (§8), channel extensibility (§9).
- [`../../DATA_MODEL.md`](../../DATA_MODEL.md) — conceptual/logical/physical reference
  of the *current* model (the baseline this revamp changes).
- [`../../catalogue/SERVE_LAYER_ARCHITECTURE.md`](../../catalogue/SERVE_LAYER_ARCHITECTURE.md)
  — governance baseline (gold→serve→Cube→catalogue, reconcile gates).

## Status

- **Phase 0 (design docs): complete.**
- **Phase 1 (concept layer on the current physical layer): implemented & green.**
  27 concepts under `catalogue/concepts/`, `catalogue/aliases.yaml`,
  `catalogue/dimensions/channel_map.yaml`; `resolve_concept` +
  load-time integrity in `catalogue_service/`; MCP tool
  `catalogue_resolve_concept`; `tests/test_concepts.py` (25 tests) + full suite
  (257) passing. "revenue by channel" now resolves deterministically to
  `channel_orders` (fallback), no longer the channel-less P&L metric.
- **Phase 2 (ClickHouse marts): all 7 composite marts authored & validated
  read-only (author-only).** In `data_platform/mage-ai/serve/marts/views/`:
  `mart_channel_daily`, `mart_pnl_daily`, `mart_customers`, `mart_orders`,
  `mart_ads_daily`, `mart_sessions_daily`, `mart_ad_status` — each composed over the
  existing certified serve views, **validated against live ClickHouse read-only**
  (grain-unique at its key; measure parity vs sources = 0). The 5 single-source
  domains need no new relation (rebrand at P3). See [09](09_MART_BUILD_SPEC.md).
  **Not applied to production** — an operator runs `serve/marts/apply_views.sh`
  (`CREATE VIEW` is the only write); I only ran read-only `DESCRIBE`/`SELECT`.
- **Phases 3–5 (Cube, OpenMetadata, agent cutover): pending** — follow per-mart
  after apply (need the applied marts + live Cube). See [08](08_MIGRATION_PLAN.md).
