# Semantic layer v2 — Phase 0 record (2026-10-03)

Plan: one entity model (conformed dims + facts with declared joins), one metric id per business number,
one resolver, end-to-end provenance. Phase 0 = baselines and guards, no behaviour change.

## Decisions
| Topic | Decision |
|---|---|
| Scope | Shopify now; `sales_channel` keeps an `amazon` slot that errors as unsupported |
| Old metric ids | Hard cut on the serve/agent surface; error names the replacement. Gold columns unchanged (Node reads gold) |
| Channels | Dynamic: rules in PG `core.traffic_source_rules` → `gold.dim_traffic_source`; facts carry `traffic_source_key`. **Superseded in Phase 1:** rules live in git (`mage-ai/serve/semantic/traffic_source_rules.yaml`) → `semantic.dim_traffic_source`; key computed at query time (PLAN.md §8) |
| Date axis | Order date everywhere; event date only in Finance (`pnl_*` ids) |

## Baseline
`catalogue/baselines/v1.json` — every certified metric (151) × brands 20, 28 × Jul/Aug/Sep 2026 = 906 rows,
each with the Cube member and date axis it was read from. v2 metrics must equal these on their declared axis,
or differ only by a documented, approved diff.

Regenerate: `uv run --with openpyxl python scripts/build_metric_inventory.py --baseline catalogue/baselines/v1.json`

Data gap seen: `session_avg_seconds_to_checkout`, `session_avg_seconds_to_purchase`, `session_checkout_steps`
are null/zero for brand 20 in all three months (checkout timing not populated).

## Gates (`build_metric_inventory.py --check`)
Report-only in `mage-ai/scripts/ci_quality_gates.sh` (`--warn-only`); blocking after the v2 cutover.

| Gate | v1 (2026-10-03) | v2 target |
|---|---|---|
| resolution conflicts | 77 | 0 |
| same-value groups among certified ids | 51 | 0 |
| phantom supported_dimensions | 27 | 0 |
| serve views without repo SQL | 2 | 0 |
| serve SQL not deployed | 2 | 0 |
| cube sql_table not live | 2 | 0 |
| glossary targets missing | 0 | 0 |
| agent registry drift | 13 | 0 |
| serve view depth > 1 | 12 | 0 |

## Trace: Cube answer → ClickHouse execution
Cube 1.6.48's ClickHouse driver assigns a random UUID `query_id`, so it cannot be injected. A deterministic lookup works instead:

1. `GET /cubejs-api/v1/sql?query=…` → `[sql, params]`; inline params in order.
2. Append `" \nFORMAT JSON"` (the driver's suffix).
3. `normalizedQueryHash(text)` equals `system.query_log.normalized_query_hash`; pick the `QueryFinish` row with
   `event_time` ≤ the load response's `lastRefreshTime` (a cached Cube answer points at the execution that
   produced it). Compare the full text as well, because the hash ignores literals.

Verified: hash 3730412819395286439 → query_id 665a9114-05ab-4ffc-8727-aba13579d6cc.

Cost note: that two-week `canonical_pnl` query read 1.15M rows (1.3 s) through 7 nested serve views.

## ClickHouse access
- `serve_definer` — no login (`HOST NONE`), `SELECT ON gold.*`. Owner of every v2 serve view.
- `cube_serve` — `SELECT ON serve.*` only; credentials in `mage-ai/infra/cube/.env` (`CUBE_SERVE_DB_*`, git-ignored).
  Not used until cutover.
- Verified: `cube_serve` reads a `DEFINER = serve_definer SQL SECURITY DEFINER` view, and is denied on v1 views
  (`SQL SECURITY INVOKER`, the server default) and on `gold.*`.
- **Rule for Phase 2:** every v2 serve view is created with `DEFINER = serve_definer SQL SECURITY DEFINER`.
