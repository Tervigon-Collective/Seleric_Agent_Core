# Deprecated (semantic v1)

Moved here on 2026-10-04, after the semantic v2 cutover (doc/semantic_v2/PROGRESS.md, Phase 5). Nothing in
`src/`, `tests/`, the Dockerfile, `docker-compose.yml` or CI imports, mounts or runs these files. They target the
v1 surface (Cube v1 :4001, `catalogue/` v1, v1 view names such as `canonical_pnl`, `commerce_orders`,
`sales_all_channels`) and are kept for history and for a v1 rollback, not for use. History: `git log --follow`.

Still live and NOT deprecated: `catalogue/` (v1 catalogue — the rollback path via
`SELERIC_CATALOGUE_DIR=catalogue`, phrase seeds for the gates, and `catalogue/migrations/v2_id_map.yaml`), the v1
`cube` compose service (frozen; seleric_systems reads it through nginx `/cube/`), `cube/` (Jenkins checks
`cube/.env`).

## scripts/
| File | Was | Replaced by |
|---|---|---|
| build_metric_inventory.py | v1 inventory + `--check` gates over Cube v1 / catalogue v1 | `scripts/build_metric_inventory_v2.py` (inventory), `scripts/v2_gates.py` (gates); probe phrases in `scripts/probe_phrases.py` |
| sync_catalogue_from_sources.py, sync_dimension_coverage.py, scaffold_metrics_for_view.py, check_catalogue_governance.py | v1 catalogue authoring loop (crosswalk, dimension coverage, metric stubs, governance gate) writing into `catalogue/` | `scripts/build_catalogue_v2.py` generates `catalogue_v2/` from the id map + Cube v2 meta; `scripts/v2_gates.py` |
| reconcile_layers.py, check_data_quality.py | gold → serve → Cube → OpenMetadata → v1 catalogue reconciliation; data checks keyed on the v1 crosswalk | `scripts/v2_parity.py` (v1 → v2 values), `v2_gates.py --live` (filters, slices, duplicates), inventory `Lineage` sheet |
| reconcile_live.py, reconcile_all_views_live.py, reconcile_attribution_totals.py, reconcile_mcp_chat_vs_dashboard.py, audit_dashboard_cube_coverage.py | live reconciliations over v1 Cube views / v1 ids | `scripts/v2_parity.py`, `scripts/v2_gates.py --live` |
| verify_gap_fixes.py, verify_hour_granularity.py | one-off verifications (2026-07) of v1 fixes | — (hourly is the v2 `hourly` binding, tested in `tests/test_semantic_v2.py`) |
| run_golden_questions.py, run_business_query_suite.py | golden questions over v1 commerce ids; SSE suite against the old Base_Agent `chat_web` on Windows | `scripts/v2_gates.py` resolution gate (529 phrases) |

## docs/
| File | Was |
|---|---|
| MCP_Agent_Architecture_MVP.md | 2026-07 MVP architecture (Phases 0–2) |
| DATA_MODEL.md, LOGICAL_DATA_MODEL_DESIGN.md, serve-revamp/ | v1 serve-revamp model and plan — superseded by doc/semantic_v2/PLAN.md |
| DASHBOARD_CUBE_METRICS.md | v1 dashboard ↔ Cube v1 metric map |
| anthropic_pattern.md | 2026-07 agent pattern notes |

## catalogue_v1_reports/
Audit and coverage reports generated over the v1 catalogue / Cube v1 (2026-06 … 2026-09): governance audit
(+ its script and JSON result), Cube audits, dashboard alignment / coverage, data-product usability, goal
status, query coverage, metric inventory matrix (csv/md), June reconciliation, serve-layer architecture,
canonical data model. Current equivalents: `doc/METRIC_INVENTORY.xlsx` and doc/semantic_v2/.

## doc/
`METRIC_INVENTORY_v1.xlsx` — the v1 inventory (2026-10-03). Current: `doc/METRIC_INVENTORY.xlsx`.

## compose/
`docker-compose.v2test.yml` — the Phase 4 test MCP (:8766) on catalogue_v2 + cube-v2; removed after the
cutover, when the production MCP moved to the same setting.
