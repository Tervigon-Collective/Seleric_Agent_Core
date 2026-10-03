# Semantic v2 — progress log

Plan: [PLAN.md](./PLAN.md). Branch `semantic-v2` in mage-ai and Seleric_Agent_Core (pushed;
`main` untouched until cutover).

**Paused 2026-10-03 at user request, in a consistent state.** The agent still runs on v1 Cube
(:4001) and the v1 catalogue — nothing user-facing has switched. Everything applied to ClickHouse
is committed on `semantic-v2` (working trees verified byte-identical to the pushed branches).

| Phase | Status | Commits |
|---|---|---|
| 0 Baseline & guards | ✅ done 2026-10-03 | Agent_Core c52bc55 · mage-ai 150c044 |
| 1 Conformed dimensions | ✅ done 2026-10-03 | mage-ai 150c044 |
| 2 Serve facts | ✅ done 2026-10-03 | mage-ai e1f8b6c |
| 3 Cube v2 model | ⏸ paused — serve ad dims done, Cube YAML not started | mage-ai 6f59a2e |
| 4 Catalogue v2 + MCP + registry | ⏳ not started | — |
| 5 Cutover | ⏳ not started | — |

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

## Phase 3 — Cube v2 model ⏸ (paused)
Done:
- `serve.dim_campaign / dim_adset / dim_ad` (DEFINER views over the conformed gold ad dims;
  `cube_serve` cannot read gold). `dim_campaign` adds the 52 historical Google campaigns
  (~₹10.5L spend, brand 20, Nov-2024 → Nov-2025) missing from `gold.dim_campaign`; 0 delivery
  campaigns unmatched; keys unique on (brand_id, platform, id).

Design settled (not yet written as Cube YAML) — see "Next steps":
- Cubes are internal (`public: false`); views are the only agent surface. Composite keys as a
  concat `pk` dimension. Joins: orders → traffic_source (key), brand, campaign/adset/ad (lt ids),
  order_lines (one_to_many, join carries `is_eligible_line = 1` so "orders by product" equals v1
  `product_orders`), order_events, order_sequence (1:1); sessions → traffic_source, ad dims;
  web_events → sessions; refunds → refund_lines → product_variant; pnl → ad dims.
- 14 views (not 12): commerce, product, paid_media, paid_media_hourly (binding), paid_media_breakdowns
  (binding), paid_media_changes, web_sessions, web_events, attribution, customers, returns, payments,
  pnl, pnl_channel (gross COGS only — different grain).

Collapse checks run against the baseline (brand 20, Sep 2026):
| Candidate | Result | Decision |
|---|---|---|
| `order_value_incl_tax` = sum(gross_revenue) vs `attributed_gross_revenue` | 2,525,682.70 = 2,525,682.70 | collapse ✅ |
| P&L returned+cancelled orders / revenue vs `returns_cancels_all_channels` / `return_cancel_revenue_all_channels` | 792 = 792; 1,614,685.40 = 1,614,685.40 | collapse into `pnl_*` ✅ |
| `orders_with_touchpoints`: `touch_distinct_orders` vs `attribution_path_orders` | 861 = 861 | collapse ✅ |
| touches ÷ orders (2.022) vs `avg_touch_count` from attribution_paths (1.957) | differ | keep attribution_paths as its own fact ❌ |
| web_events event-grain counts vs `web_events_daily` mart (all 754,689 vs 764,460; page views 354,421 vs 358,736) | differ ~1.3 % | do NOT move these metrics to event grain; keep the daily binding or find the cause ❌ |
| session bounce (0.148) vs `funnel_daily` bounce_rate (0.343) | different definitions | keep distinct ids ❌ |
| `funnel_purchases` (925) vs orders (1,097) | different populations | do NOT collapse into orders ❌ |
| candidate new-customer value 1,384.19 vs v1 `ltv` 1,729.78 | formula mismatch | port the exact v1 `ltv_cac_daily` definition before collapsing ❌ |
| `refunded_amount_incl_tax` 726,428.80 vs v1 `attributed_refund_amount` 728,927.80 | Δ 2,499 | find the population difference before collapsing ❌ |

## Next steps (to resume)
1. **Phase 3** — write `mage-ai/infra/cube/model_v2/` (fact + dim cubes, joins, `drill_members`,
   `fiscal_year` granularity, hierarchies where all levels sit in one cube; ad hierarchy is
   cross-cube → declare in the catalogue). Copy measure SQL from v1 cubes, renamed per the id map.
   Add a `cube-v2` service to `Seleric_Agent_Core/docker-compose.yml` on 127.0.0.1:4002 with
   `CUBE_SERVE_DB_*` creds and default DB `serve`. Verify: every v2 metric = baseline (or documented diff).
   First test that Cube 1.6.48 accepts custom `granularities` and `hierarchies`.
2. **Phase 4** — `catalogue/migrations/v2_id_map.yaml` (from the collapse table above + PLAN §4),
   metric YAML schema v2 in `catalogue_service/loader.py`, one resolver (`resolve_concept`), planner:
   bindings / hierarchy drill / composites (gross_roas, cac, ltv_cac_ratio) / `valid_for` / hard-cut
   rejection, provenance v2 (trace via PHASE0.md method). Update `Seleric_Agent/config/metric_registry.yaml`
   (drop aliases; ids must exist). Re-point `build_metric_inventory.py` gates at the v2 surface.
3. **Phase 5** — MCP `config.yaml cube.api_url` → :4002; gates blocking in CI; regenerate inventory.
   Keep v1 Cube for seleric_systems (`daily_pnl.*` via nginx `/cube/`) until that consumer moves.
4. **Merge** `semantic-v2` → `main` in both repos (mage-ai: a push to `main` triggers the Jenkins deploy
   that recreates Mage — merge when no dbt run is active, or via PR).

## Known gaps / risks
- **Uncommitted on `main`:** the live mage-ai checkout carries the semantic-v2 files uncommitted on
  `main`. Any other push to mage-ai `main` triggers a Jenkins deploy that overwrites the checkout:
  the files would disappear from disk (they remain on `origin/semantic-v2`). ClickHouse objects are
  not affected, but re-running the old per-domain `apply_views.sh` from `main` would revert the serve
  views to INVOKER / old `channel_pnl` buckets. Merge soon, or re-checkout files from the branch.
- **Live ClickHouse changes already in effect (v1 consumers see them):** `channel_pnl` buckets from
  the traffic dimension (33 GA `(direct)` orders organic → unattributed; totals unchanged);
  `ad_channel_pnl_daily` reconciliation fix (adds offset rows; totals unchanged); all serve views
  are DEFINER views (same results). New objects: `semantic.*` (3 refreshable views every 5 min,
  ~0.6 s each), `serve.dim_* / cfg_* / traffic_* / product_hierarchy_conflicts / ad_delivery_* /
  ad_changes / pnl_daily / web_events*` (DDL now in repo).
- **Business decisions open:** 10 unmapped traffic signatures (86 fact rows): product_card,
  igshopping/social, youtube/organic, go/product_sync, hazlnut/wishlist, th + numeric medium.
  Meta organic social is labelled platform `unattributed` / channel `meta_organic` for v1 parity —
  could become its own earned platform. Sessions: 116,907 no-UTM non-search sessions now `unattributed`.
- **Data quality:** 56 brand-20 products have no Shopify `product_type`; checkout-timing session metrics
  (`session_avg_seconds_to_checkout/_purchase`, `session_checkout_steps`) are null/zero Jul–Sep;
  ~4 % of Google campaigns (historical) missing from `gold.dim_campaign` (patched in serve, root cause in dbt).
- **New-signature lag:** a brand-new UTM combination is unlabelled for up to 5 min (dimension refresh);
  `channel_pnl` falls back to the dbt platform's finance bucket meanwhile; Cube joins would show null.
- **Gates** still measure the v1 surface (77 conflicts, 51 same-value ids, …); they only turn green once
  Phase 4/5 re-point them at v2. CI step is report-only and lives in the uncommitted `ci_quality_gates.sh`
  (on the branch).
- **seleric_systems** depends on v1 Cube `daily_pnl.*` (outside the three repos) — v1 cannot be retired.
- `product_ad_spend_daily.sql` stays in the repo but undeployed (as before); `apply_views.py` skips it.

## Rollback (if ever needed)
- Serve views: `git checkout main -- serve && python3 serve/apply_views.py` is NOT enough on its own
  (main files lack DEFINER, the tool refuses them) — use the per-domain `apply_views.sh` from `main`
  inside the Mage container, which restores the v1 SQL exactly.
- Drop v2-only objects: `DROP DATABASE semantic`; `DROP VIEW serve.{dim_traffic_source, cfg_traffic_source_rules,
  cfg_traffic_platforms, traffic_unmapped, traffic_hierarchy_conflicts, dim_brand, dim_product_variant,
  product_hierarchy_conflicts, ad_delivery_daily, ad_delivery_hourly, ad_changes, pnl_daily, dim_campaign,
  dim_adset, dim_ad}`; users `serve_definer`, `semantic_refresher`, `cube_serve`.
