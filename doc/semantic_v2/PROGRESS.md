# Semantic v2 — progress log

Plan: [PLAN.md](./PLAN.md). Branch `semantic-v2` in mage-ai and Seleric_Agent_Core (pushed;
`main` untouched until cutover).

| Phase | Status | Commits |
|---|---|---|
| 0 Baseline & guards | ✅ done 2026-10-03 | Agent_Core c52bc55 · mage-ai 150c044 |
| 1 Conformed dimensions | ✅ done 2026-10-03 | mage-ai 150c044 |
| 2 Serve facts | ✅ done 2026-10-03 | mage-ai e1f8b6c |
| 3 Cube v2 model | 🔄 in progress | — |
| 4 Catalogue v2 + MCP + registry | ⏳ | — |
| 5 Cutover | ⏳ | — |

## Phase 0 — baseline and guards ✅
Details: [PHASE0.md](./PHASE0.md).
- Baseline `catalogue/baselines/v1.json`: 151 certified metrics × brands 20, 28 × Jul–Sep 2026 (906 rows).
- `scripts/build_metric_inventory.py --check [--warn-only] [--baseline PATH]`; report-only step in
  mage-ai `scripts/ci_quality_gates.sh`.
- Gate scores (v1): resolution conflicts 77 · same-value certified ids 51 · phantom dims 27 ·
  registry drift 13 · serve depth > 1: 12 · serve/cube drift 2 each.
- Cube → ClickHouse trace: `normalizedQueryHash(v1/sql with params inlined + " \nFORMAT JSON")`
  matches `system.query_log` (verified on a live query).
- ClickHouse users: `serve_definer` (no login), `cube_serve` (SELECT serve.* only; creds in
  mage-ai `infra/cube/.env`).

## Phase 1 — conformed dimensions ✅
Code: `mage-ai/serve/semantic/` (README there is the operating manual).
- `serve.dim_traffic_source`: 2,938 signatures; medium_group → platform → channel → sub_channel;
  rules in `traffic_source_rules.yaml` (14 rules, 11 platforms), applied by `apply.py`, refreshed
  every 5 min. 10 signatures / 86 fact rows still unmapped (need a business decision:
  product_card, igshopping/social, youtube/organic, go/product_sync, hazlnut/wishlist).
- `serve.dim_brand` (7 brands + live coverage flags), `serve.dim_product_variant` (1,457 variants,
  458 products; 56 brand-20 products have no Shopify product_type).
- Hierarchy integrity views (`serve.traffic_hierarchy_conflicts`, `product_hierarchy_conflicts`) — empty.
- Verified against v1, every order (23,633): all label differences explained by a rule —
  unattributed→organic 362 (gclid-less Google; v1 views disagreed), other→unattributed 33 (GA
  `(direct)`), other→whatsapp/email/sms 43; channel-only clean-ups otherwise.
- Sessions: 116,907 of 436,674 organic → unattributed (no-UTM, non-search; now consistent with orders).

## Phase 2 — serve facts ✅
- All serve views are DEFINER views; `serve/apply_views.py` (dependency-ordered apply) and
  `serve/fingerprint_views.py` (row-hash diff). 39/43 views bit-identical after re-apply.
- `traffic_source_key` on `commerce_orders`, `session_funnel`, `touchpoints` — old columns
  bit-identical; every key resolves.
- `channel_pnl` channel = `finance_channel` from the dimension; totals unchanged; 33 `(direct)`
  orders organic → unattributed.
- Fixed a v1 bug: `ad_channel_pnl_daily` reconciliation skipped cells present only in gold; now all
  65 brand × month × channel cells (Jan–Oct) reconcile exactly to `channel_pnl`.
- New: `ad_delivery_daily` / `_hourly` (Meta ∪ Google; hourly within 0.1 % of daily),
  `ad_changes`, `semantic.fct_pnl_daily` → `serve.pnl_daily` (5-min snapshot, equals live).
- `web_events` / `web_events_daily` DDL captured into the repo.

## Phase 3 — Cube v2 model 🔄
- Model: `mage-ai/infra/cube/model_v2/`; instance: second Cube container on 127.0.0.1:4002,
  ClickHouse login `cube_serve`.
