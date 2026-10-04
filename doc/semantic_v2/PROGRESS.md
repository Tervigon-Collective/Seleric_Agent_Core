# Semantic v2 — progress log

Plan: [PLAN.md](./PLAN.md). Work happens on branch `semantic-v2` in mage-ai and Seleric_Agent_Core
and is merged to `main` at each stable point (first merge 2026-10-03, after the channel decisions).
Seleric_Agent work goes on its `gaurav` branch.

**Status 2026-10-04 (Phase 5):** the agent surface is on semantic v2. The MCP (`seleric-mcp-mcp-1`, :8765 /
mcp.seleric.com) serves `catalogue_v2` on `cube-v2` (:4002); Seleric_Agent `gaurav` carries the v2 registry.
v1 Cube (:4001) stays up for seleric_systems only. All repos on their deploy branches (mage-ai `main`,
Agent_Core `main`, Seleric_Agent `gaurav`).

| Phase | Status | Commits |
|---|---|---|
| 0 Baseline & guards | ✅ done 2026-10-03 | Agent_Core c52bc55 · mage-ai 150c044 |
| 1 Conformed dimensions | ✅ done 2026-10-03 | mage-ai 150c044 |
| 2 Serve facts | ✅ done 2026-10-03 | mage-ai e1f8b6c |
| Channel decisions (pre-Phase 3) | ✅ done 2026-10-03 | mage-ai 393496b, c721983 |
| 3 Cube v2 model | ✅ closed 2026-10-04 | mage-ai 6f59a2e, f3361eb, 04e59a7 · Agent_Core 7e9bc17, 229d322 (all on `main`) |
| 4 Catalogue v2 + MCP + registry | ✅ built 2026-10-04 (branches, not on main) | Agent_Core `semantic-v2-p4` e9043db · Seleric_Agent `semantic-v2` 22e1d29 · mage-ai `semantic-v2` c4751e8 |
| 5 Cutover | ✅ 2026-10-04 | mage-ai 2c2655d (main, Jenkins #225) · Agent_Core main (this commit) · Seleric_Agent gaurav 22e1d29 |

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

## Channel decisions applied (2026-10-03, user) ✅
Rules 15–27 in `mage-ai/serve/semantic/traffic_source_rules.yaml`; full table and verification in
`mage-ai/serve/semantic/README.md` ("Channel decisions 2026-10-03").
- **WhatsApp** = own platform and own P&L channel (`finance_channel = whatsapp`). WhatsApp UTMs
  (`utm_medium` whatsapp / wa, Bitespeed no-medium or "- wa" flows) win over dbt's meta / google label.
  WhatsApp signatures 41 → 82.
- **Meta organic** under platform meta, channel `meta_organic`, `medium_group = earned`, `is_paid = 0`;
  rolls up to Meta in the P&L (was unattributed). Incl. igshopping and Pragma DM replies.
- **Google organic** = one channel `google_organic` under google, `is_paid = 0` (mirrors `meta_organic`),
  sub_channel `free_listing` (incl. `go/product_sync`) / `organic_search` (Google SEO, 294 orders) /
  `youtube`; paid Shopping unchanged; non-Google engines stay platform organic. No-UTM sessions referred
  by youtube.com → google_organic / youtube (469); by a search engine → organic_search (369; was
  unattributed via rule 3).
- product_card → organic `order_tracking_page`; hazlnut → organic `wishlist`; catch-all other →
  organic `organic_other` (still reported in `serve.traffic_unmapped`). `utm_source=th` is NOT organic:
  its UTMs are Meta campaign / ad ids ("TH-383-SUSPENDER-20JUNE") → paid Meta `meta_other` (1 session).
- Model change: hierarchy is platform → channel → sub_channel; `medium_group` / `is_paid` are channel
  attributes (rules may set medium_group); `traffic_hierarchy_conflicts` also checks medium_group and
  finance_channel per channel. Unmapped: 0. Conflicts: none.
- Serve views: `order_attribution` labels from the dimension + `lt_is_paid`, `lt_medium_group`,
  `lt_sub_channel`, `lt_finance_channel`, `traffic_source_key`; `channel_attribution_daily` +whatsapp,
  `is_paid` in grain; `platform_attribution_commerce` +whatsapp, `is_paid`; `meta_ad_attribution_daily`
  paid clicks only; `channel_pnl` + `is_paid` (spend = 1).
- Verified: brand-month totals unchanged in `channel_pnl` / `channel_attribution_daily` /
  `platform_attribution_commerce`; `ad_channel_pnl_daily` and `pnl_daily` reconcile to `channel_pnl` in all
  2,894 cells; `order_attribution.lt_finance_channel` order counts = `channel_pnl` placement orders;
  v1 Cube serves the new labels. 987 orders relabelled (531 unattributed → Meta organic, ₹10.9L).
  P&L net sales Jan–Sep 2026: meta +₹5.77L, google +₹3.60L, whatsapp +₹0.93L, organic −₹4.31L,
  unattributed −₹5.99L (google / organic include the Google SEO move, ₹2.87L / 160 orders). v1 `organic_*` / `meta_*` channel measures move accordingly (intended).
- Phase 1 label diffs approved by the user (362 / 33 / 43 orders, 116,907 sessions).

## Phase 3 — Cube v2 model ✅ (2026-10-04)
Model: `mage-ai/infra/cube/model_v2/` (README there: views, rules, gotchas). Service: `cube-v2` in
`docker-compose.yml` (image pinned `cubejs/cube:v1.6.48`, `127.0.0.1:4002`, login `cube_serve` via the
git-ignored `mage-ai/infra/cube/.env.v2`, default DB `serve`). Id map: `catalogue/migrations/v2_id_map.yaml`.
Parity: `scripts/v2_parity.py`.

Prep (same day):
- `ad_channel_pnl_daily` / `pnl_daily` gain `is_paid` (user decision): detail rows paid for meta / google,
  reconciliation rows cut per (channel, is_paid); all 3,382 brand × day × channel × is_paid cells reconcile to
  `channel_pnl`. Meta Jul 2026: 67 organic orders / ₹1.58L in their own non-paid rows.
- Baseline `catalogue/baselines/v1.json` regenerated after the channel decisions (6 channel P&L metrics moved by
  the decisions, 8 by live-data drift).
- `serve.dim_campaign / dim_adset / dim_ad` (earlier): DEFINER views over the conformed gold ad dims, + 52
  historical Google campaigns missing from `gold.dim_campaign`.

Model:
- 27 cubes, all `public: false`: 6 conformed dims (brand, traffic_source, product_variant, campaign, adset, ad) +
  21 facts. Hierarchies: traffic (platform → channel → sub_channel), product (type → product → variant), geo
  (country → state → city → pincode). `fiscal_year` (Apr–Mar) on every time dimension. Composite `pk`s.
- **18 views** (plan said 14): commerce, product, paid_media, paid_media_hourly, paid_media_breakdowns,
  paid_media_changes, web_sessions, web_funnel, web_events, web_event_detail, attribution, attribution_paths,
  customers, unit_economics, returns, payments, pnl, pnl_channel. One root fact per view, because the agent
  scopes a view by exactly one `brand_id` member and unjoined facts would each need their own.
- 106 v2 metrics (view members); 151 certified v1 ids map onto 102 of them. Collapses: the 17 same-value groups
  (orders ×7, net profit ×4, ad spend ×5, new customers ×3, …), per-platform ids → one id + `ad_platform` filter,
  hourly ids → hourly binding, finance ids → `pnl_*`. `pnl_orders` hidden (= orders).
- Renamed for clarity: canonical `gross_profit` / `contribution_margin` (net sales − net COGS) →
  `pnl_contribution_margin`; `pnl_gross_profit` is the Overview tile (gross sales − gross COGS − spend).
  `return_revenue` / `cancel_revenue` are now ORDER-date (commerce); the event-date P&L ones are `pnl_*`.

Verification (2026-10-04, brands 20 + 28 × Jul–Sep 2026, v1 and v2 queried back to back):
- **149 / 151 certified ids equal in every cell (900 / 906).** Known differences (flagged `known_diff` in the
  id map): `finance_waterfall_net_profit` (retired, broken upstream → `pnl_net_profit`); `returns_cancels`
  (v1 counted ClickHouse's LEFT JOIN filler `order_id = 0` as an order: 489 vs true 488 — fixed in v2 with
  `nullIf`; same fix on `refund_lines`).
- Same-value scan of all v2 metrics: no duplicates left except the three checkout-timing metrics that are
  empty (data gap, below).
- Drill / join checks (brand 20, Sep 2026): Σ parts = total for orders by traffic hierarchy / geo / campaign,
  net sales by platform × is_paid, product revenue by product hierarchy, spend by platform × campaign × adset and
  by hour, sessions by traffic, P&L by finance_channel × is_paid and by campaign, refund lines by product,
  touches by platform. Fiscal year and Meta → channel → is_paid drill work.
- `cube_serve` ran 3,024 queries, 0 errors; Cube → ClickHouse trace (PHASE0.md) applies unchanged.

Collapse checks (from the pause) — resolved:
- LTV: v1 `ltv_cac_daily` definition ported (`unit_economics.ltv`), equal to v1.
- Web events: counts stay on the daily mart (`web_events`); event grain is `web_event_detail`.
- Refunds: not collapsed — `refund_amount` (incl. tax, order-date cohort, commerce) and
  `refunded_amount_excl_tax` (refund date, returns) are different numbers; both equal v1.

Collapse checks run against the baseline at the pause (brand 20, Sep 2026):
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

## Phase 4 — catalogue v2 + MCP + registry ✅ (built 2026-10-04, on branches)
Work lives on branches only (NOT on `main` — a push to Agent_Core / mage-ai `main` deploys):
- Agent_Core worktree `/home/tervigon/work/agent_core_v2`, branch **`semantic-v2-p4`** (from main e7cc6b2).
- Seleric_Agent worktree `/home/tervigon/work/seleric_agent_v2`, branch **`semantic-v2`** (from origin/gaurav 431ed60).
- mage-ai live checkout, branch `semantic-v2` (Cube model additions; not on main).
The live agent / MCP still run v1 (catalogue/ + Cube :4001). Nothing user-facing changed.

Done:
- **Cube v2 model** (mage-ai): + `hook_rate`, `hold_rate_15s`, `cost_per_link_click` on paid_media (equal to v1);
  `pnl_gross_sales` / `pnl_discounts` hidden (identical to `gross_sales` / `discounts`: placements are on order
  date). `cube-v2` now mounts the stable parent `mage-ai/infra/cube` with `CUBEJS_SCHEMA_PATH=src/model_v2`
  (a model_v2 directory bind mount went stale when git recreated the folder → empty model; fixed, applied live).
- **Id map** covers all 187 v1 ids: 165 map (151 certified + 14 uncertified incl. the approved Meta video ones),
  22 drafts retired without replacement. 109 v2 metrics.
- **Loader schema v2** (`semantic_version` manifest; v1 behaviour unchanged): metric `bindings`, `valid_for`,
  `version`, `unavailable_reason`, `replaces`; dimension `unsupported_values`; glossary `filter`; concept
  `axis_filters`; `hierarchies.yaml`; hard-cut `retired` map; v2 integrity checks.
- **`catalogue_v2/`** generated by `scripts/build_catalogue_v2.py` from the id map + live Cube v2 meta + v1
  (brands, dim aliases, glossary remapped). Hand-authored v2 concepts in `catalogue_v2_src/concepts.yaml`
  (axes date / channel / paid / platform as filters). Loads + passes integrity: 109 metrics, 18 views,
  122 dims, 5 hierarchies (traffic, product, geo, ad, campaign), 414 glossary terms, 27 concepts, 124 retired.
- **MCP code** (active only for a v2 catalogue): `SELERIC_CATALOGUE_DIR` env; one resolver order
  (retired → id → concept alias → glossary → normalized name → concept-in-text → fuzzy SUGGESTS only);
  `search` top hit = resolver answer; retired ids rejected naming replacement + filters; term filters flow into
  the query; bindings (hour → hourly, breakdown dims → breakdowns); Meta-only metrics scoped to meta in their
  own query part; `sales_channel = amazon` rejected; hierarchy drill (`metrics_drilldown hierarchy=`);
  v2 ontology; provenance v2 (metric versions, members, binding, Cube SQL, serve objects, ClickHouse
  `normalizedQueryHash` lookup SQL; `CubeClient.sql`).
- **Tests**: `tests/test_semantic_v2.py` 52/52 pass; existing suite unchanged (296 pass, 8 pre-existing
  `test_canonical_model` failures from the moved Cube path).
- **Seleric_Agent**: `scripts/migrate_metric_registry_v2.py` (line edits, comments kept) → registry on v2 ids,
  `catalogue_filters` for 15 platform entries + 4 legacy meta_attr_*; aliases kept only where the MCP v2
  resolver agrees (10 dropped, e.g. "roas" → net ROAS wins over the agent's gross). Agent code: `catalogue_filters`
  sent by series + runner; live overlay only from unfiltered entries. Business-state fixtures moved to v2 ids.

- **One resolver everywhere:** in v2, `resolve_concept` without axes delegates to the term resolver (exact metric
  names, retired ids and unavailable metrics answer identically on every tool); `search` keeps the resolver
  answer as top hit through its grain filter.
- **Meta breakdown slices (found by the live smoke, fixed):** `serve.meta_ads_breakdown_daily` repeats ALL Meta
  delivery once per `breakdown_type` (age_and_gender / placement / platform_device / publisher_platform /
  region — each sums to the same spend and impressions). An unpinned "impressions by age" returned a 6.3M null
  bucket on 1.6M real impressions. Bindings now carry `slice_dimension` + `slices`: the planner pins the one
  `breakdown_type` that carries the requested dims (publisher_platform alone → publisher_platform; with
  position / impression_device → placement; with device_platform → platform_device), refuses mixes (age +
  region), `country` (never populated) and a metric a slice lacks (`landing_page_views` by region sums to 0 at
  Meta; `thruplays` too, not on the binding), and warns the binding is Meta-only. Grouping BY breakdown_type is
  allowed with a "never sum across rows" warning.
- **Gates** `scripts/v2_gates.py` (534 phrases: inventory probes + v1 glossary + v1/v2 concept aliases + v1/v2
  registry aliases): resolution conflicts **0** (v1: 77), registry drift **0**, hard-cut coverage gaps **0**,
  sliced bindings **0** (static + live full-copy check), duplicate numbers **0** (live, relative tolerance).
  4 phrases resolve to nothing (dimension words). Run from a worktree with
  `SELERIC_CUBE_ENV=/opt/seleric/mage-ai/infra/cube/.env … v2_gates.py --agent <v2 registry checkout> [--live]`.
  mage-ai `ci_quality_gates.sh` runs it report-only (skips until the script is on Agent_Core main).
- **Test MCP** `docker-compose.v2test.yml` (project `seleric-mcp-v2test`, 127.0.0.1:8766, own data volume,
  joins `seleric-mcp_default` to reach `cube-v2`; not deployed by Jenkins). Startup drift check: 109 / 109
  metrics on cube-v2, 0 broken. Live smoke over MCP (brand 20, Sep 2026): retired `meta_spend` refused naming
  `ad_spend` + `ad_platform = meta`; "meta net sales" → `pnl_net_sales` + `finance_channel = meta` (266,815);
  roas + {meta, paid} → `pnl_net_roas` + `finance_channel = meta, is_paid = true`; ad_spend + hook_rate composed
  (hook_rate scoped to meta in its own part); hook_rate on google refused; hourly ad_spend → hourly binding
  (24 rows); impressions by age → age_and_gender slice (sums to Meta's 1,585,690); amazon refused; checkout
  timing refused with reason; orders 1,097 = Σ by platform = Σ drilled to channel (hierarchy traffic).
- **Provenance trace verified:** the `clickhouse_trace.lookup_sql` of each part finds its query in
  `system.query_log` (user `cube_serve`, tables `serve.pnl_daily` / `serve.ad_delivery_daily` only).
- Seleric_Agent unit suite back to its gaurav baseline (745 pass, 2 pre-existing drilldown failures) after
  `test_v3_ui_connect` moved to `pnl_net_sales`.

Decisions taken in Phase 4 (catalogue wins over the agent registry):
- "roas" = **net** ROAS (`pnl_net_roas`); the registry alias that meant gross was dropped. Gross ROAS is asked
  explicitly ("gross roas").
- `pnl_gross_sales` / `pnl_discounts` / `pnl_orders` hidden in Cube v2 (same numbers as `gross_sales` /
  `discounts` / `orders`).
- Checkout-timing metrics stay in the catalogue as `broken` with an `unavailable_reason` (refused, never 0).

Known limits (accepted):
- "atc sessions" / "product view sessions" resolve to `sessions` via concept-in-text (disclosed as a warning).
- 11 v1 glossary terms dropped (their targets were retired without replacement).
- Composites (CAC, LTV:CAC, gross ROAS) are composed inside Cube v2 views (`unit_economics`), not by the
  planner; drill-through to records (`drill_members`) is not exposed by the MCP yet.

## Next steps
1. **Phase 5 cutover — done 2026-10-04:**
   - mage-ai `semantic-v2` → `main` (2c2655d, pushed right after an orchestrator run; Jenkins #225 green). Includes
     the `is_paid` fix: Cube sends boolean filters as 'true'/'false' and the serve columns are UInt8, so every
     paid-only filter failed until `sql: toBool({CUBE}.is_paid)` (found by the pre-cutover registry sweep).
   - Pre-cutover checks: registry sweep over the test MCP (every registry entry + its catalogue_filters: 86 ok,
     3 = the refused checkout-timing metrics); v2 gates incl. the new **live filters** gate (347 metric + filter
     pairs run on Cube v2) all 0; agent code/config scanned for retired ids (only comments / the test fake LLM).
   - Agent_Core `semantic-v2-p4` → `main`: `mcp` on `CUBE_API_URL http://cube-v2:4000` + `SELERIC_CATALOGUE_DIR
     catalogue_v2` (Dockerfile ships catalogue_v2; cube-v2 stable parent mount now on main).
   - Seleric_Agent `semantic-v2` fast-forwarded into `gaurav`. The live checkout held a teammate's uncommitted
     (already deployed) edits in 6 files: backed up to `/home/tervigon/work/backup_seleric_agent_wip_20261004T063343Z`
     (patch + files + SHA256SUMS), stashed, fast-forwarded, restored (no overlapping hunks; dry-run first);
     they remain uncommitted and live, exactly as before.
   - **Rollback:** Agent_Core — revert the `mcp` env in `docker-compose.yml` (`CUBE_API_URL http://cube:4000`, drop
     `SELERIC_CATALOGUE_DIR`; catalogue/ is still shipped) and push; Seleric_Agent — `git reset --hard 431ed60` on a
     stash of the WIP (or revert the commits 431ed60..22e1d29) and rebuild. v1 Cube and catalogue/ are untouched.
   - **Switch (2026-10-04 06:39–06:41 UTC):** agent images pre-built; Agent_Core main pushed (Jenkins #51 green,
     MCP recreated 06:40:34, drift check 109 / 0 broken), agent restarted 06:41:21 — ~47 s with MCP v2 / agent v1.
   - **Verified after the switch:** smoke + registry sweep on https://mcp.seleric.com/mcp identical to the test MCP
     (orders 1,097 = Σ platform = Σ channel); agent end-to-end (thread user `semantic-v2-smoke`): "net sales last
     month" ₹528,106.92 (= `pnl_net_sales`), "Meta ad spend + Meta paid orders" ₹855,043.07 / 587, "impressions by
     age" rows = the age_and_gender slice; no retired-id / PlanError / traceback in agent or MCP logs. Test MCP
     removed (container, volume, image).
   - Follow-ups:
     - **CI gates cannot block yet:** the `jenkins` user cannot reach uv (`/home/tervigon/.local` is 700), so
       both semantic steps in `ci_quality_gates.sh` SKIP in Jenkins. Install uv for jenkins (e.g. /usr/local/bin,
       root) and give it a writable uv cache, then turn the v2 step blocking.
     - Agent answer arithmetic: the "impressions by age" narration said 1,572,690 while its rows sum to 1,585,690
       (answer_audit did not catch it); the agent also did not say the breakdown is Meta-only although the MCP
       warning says so. Seleric_Agent-side.
     - ~~Regenerate the inventory on the v2 surface~~ — done (below). Add `v2_parity.py` to CI (needs uv for jenkins).
2. **Post-cutover cleanup (2026-10-04):**
   - **New inventory** `doc/METRIC_INVENTORY.xlsx` from `scripts/build_metric_inventory_v2.py`, read entirely from the
     live surface (catalogue_v2, Cube v2 meta / generated SQL / values, ClickHouse DDL lineage, agent registry,
     gates, v1 parity): README · Metrics (109: member, axis, bindings, valid_for, serve objects, gold sources,
     former v1 ids, concepts, glossary, registry entries, Sep / Aug values, Cube SQL) · Metric Variants (347
     metric + filter pairs with sources and values) · Retired v1 ids (146: 108 equal, 2 known diffs, 14 not
     comparable — v1 side has no axis —, 22 without replacement) · Views · Dimensions · Hierarchies · Concepts ·
     Glossary · Agent Registry · Resolution (529 phrases) · Gates · Lineage (18 serve objects → gold). The v1
     workbook is `deprecated/doc/METRIC_INVENTORY_v1.xlsx`.
   - **Deprecated** (moved to `deprecated/`, manifest in deprecated/README.md; nothing live imports, mounts or runs
     them): 16 v1 scripts (v1 inventory/gates, v1 catalogue authoring loop, v1 reconciliations, one-off
     verifications, golden-question suites), 6 v1 design docs + `doc/serve-revamp/`, 18 v1 catalogue audit
     reports, the v1 inventory workbook, the Phase 4 test-MCP compose file. `scripts/probe_phrases.py` keeps the
     probe list the gates need.
   - **Defaults now v2:** `config.py` `catalogue_dir` → `catalogue_v2`, Cube default → :4002 (`config.yaml` too);
     `smoke_cube.py`, `validate_catalogue.py`, `capability_audit.py` run on v2. v1 unit tests pin
     `catalogue_dir=catalogue` explicitly (conftest `settings`, two gateway-test fixtures). `catalogue/DEPRECATED.md`
     marks the v1 catalogue frozen (rollback + id map + phrase seeds). README rewritten for v2.
   - **Kept on purpose:** v1 `cube` service + `mage-ai/infra/cube/model` (seleric_systems queries canonical_pnl,
     daily_pnl, channel_pnl, meta/google ad performance, order_attribution, … through nginx `/cube/`), `cube/`
     (Jenkins checks `cube/.env`), `catalogue/`, `sync_openmetadata_catalogue.py` (v1 tests).
3. Keep v1 Cube (:4001) for seleric_systems (`daily_pnl.*` via nginx `/cube/`).
4. **Deploy notes:** mage-ai: a push to `main` triggers the Jenkins deploy (CI gates → rsync into
   /opt/seleric/mage-ai → rebuild + restart `mageai-local`); the dbt orchestrator runs back to back (~25 min,
   ~10 s gaps), so push right after a run completes. **Seleric_Agent_Core also deploys on a push to `main`**
   (Jenkins job `Seleric-Agent-Core`: rsync --delete into /opt/seleric/Seleric_Agent_Core + `docker compose up -d
   --build` → MCP and v1 Cube restart, ~1 min) — even for doc-only commits. That deploy reads
   `mage-ai/infra/cube/.env.v2` as the `jenkins` user: keep it group-readable (640, group tervigon) — build #49
   failed at compose config load while it was 600.
5. ~~Live compose drift~~ — resolved by the Phase 5 merge (cube-v2 stable parent mount is on main).

## Order date by default — only Finance on event date (2026-10-04, user decision) ✅
Performance, ads and every non-Finance domain read the ORDER date; only Finance reads the event date. v2 had
inherited the v1 glossary, which sent 163 terms ("revenue", "net sales", "ROAS", "campaign roas", "how are my
ads doing", "returns", …) to event-date `pnl_*` metrics, and the sales / returns concepts defaulted to finance.
- **Data (mage-ai, ClickHouse live):** `serve.channel_pnl` arms also carry `order_id` + the order's placement
  date (`order_date`; ad spend: delivery date) — event-date numbers and row count unchanged (12 measures,
  89,518 rows checked). `semantic.fct_order_pnl_daily` (5-min refresh) → `serve.order_pnl_daily`: the same arms
  grouped by order date, with each order's last-touch campaign / adset / ad; spend per campaign from
  `ad_delivery_daily` (= channel_pnl spend). Brand 20 Sep 2026 on order date: net sales (P&L basis) ₹17.5 L,
  net COGS ₹6.8 L, spend ₹11.9 L, net profit −₹1.2 L, net ROAS 0.90, MER 1.47 (event date: net profit −₹15.5 L —
  July / August returns land in September).
- **Cube v2:** view `order_pnl` (order date) — net COGS and its parts, operating cost, contribution margin (+%),
  net profit, net margin %, gross profit, gross COGS, taxes, MER, gross / net / break-even ROAS. Ids are the
  `pnl_*` names without the prefix: `pnl_X` = event date (Finance), `X` = order date. 17 of them are v1 ids
  redefined at version 2.0.0 (their v1 event-date meaning is `pnl_X`). Gross COGS, gross profit, gross ROAS and
  product cost are booked at placement, so both axes are identical: one id (the order-date one); `pnl_gross_cogs`,
  `pnl_gross_profit`, `pnl_gross_roas`, `pnl_product_cost` retired, view `pnl_channel` removed (still 18 views).
- **Catalogue:** every concept date axis defaults to `order`; finance rows keep `pnl_*`. Glossary terms move to
  the order-date twin unless the term itself asks for Finance (the resolver's date-axis phrases: p&l, pnl, event
  date, profit and loss, finance, financial — one source). Channel-named terms ("meta roas", "meta orders",
  "google new customers" — 21) now carry their channel filter (the concepts' channel axis_filters). 123 metrics,
  111 retired ids.
- **Agent registry:** entries follow their own domain — commerce / performance (`metric.net_sales`,
  `metric.net_roas`, `metric.gross_roas`) → order date; Finance entries keep `pnl_*`; 5 Finance aliases ("np",
  "net profit", "blended roas", …) dropped because the resolver now answers them on order date.
- **Checks:** gates 0 / 0 / 0 / 0 / 0 / 0 live (425 metric + filter pairs; the duplicate gate caught the 4
  identical-on-both-axes ids); parity 149 / 165 equal (the 2 known diffs; the rest have no v1 date axis);
  MCP suite 359 pass (8 known), agent suite 745 / 0.
- **Open — two "net sales on order date":** `net_sales` (commerce, the dashboard Commerce figure: order-level
  post-refund net revenue, cancelled → 0) is the order-date net sales; the order-date P&L uses the P&L basis
  (gross − discounts − returns − cancels, returns incl. pending) — Sep 2026 ₹19.5 L vs ₹17.5 L. Profit / ROAS are
  on the P&L basis so their components add up; aligning the two definitions is a business decision.

## Known gaps / risks
- ~~Uncommitted on `main`~~ — resolved 2026-10-03: `semantic-v2` merged to `main` in both repos.
- **Live ClickHouse changes already in effect (v1 consumers see them):** `channel_pnl` buckets from
  the traffic dimension (33 GA `(direct)` orders organic → unattributed; totals unchanged);
  `ad_channel_pnl_daily` reconciliation fix (adds offset rows; totals unchanged); all serve views
  are DEFINER views (same results). New objects: `semantic.*` (3 refreshable views every 5 min,
  ~0.6 s each), `serve.dim_* / cfg_* / traffic_* / product_hierarchy_conflicts / ad_delivery_* /
  ad_changes / pnl_daily / web_events*` (DDL now in repo).
- ~~Business decisions open~~ — resolved 2026-10-03 (see "Channel decisions applied").
- ~~Google organic search placement~~ — resolved 2026-10-03: in `google_organic` under google.
- **Remaining no-UTM referrals still unattributed (sessions only):** `other_referral` 244,
  `social_unpaid` 12 — the referrer domain is not in the signature, so they cannot be placed.
- **Email / SMS UTMs vs click ids:** only WhatsApp UTMs override dbt's meta / google label; an email or
  SMS link that picked up an fbclid / gclid stays paid (e.g. 2 `pragma/sms` sessions under meta).
- **`pnl_daily` / `ad_channel_pnl_daily` have no `is_paid`:** Meta organic orders sit in the Meta
  reconciliation / unattributed rows there; only `channel_pnl` (pnl_channel) splits paid vs non-paid.
- **Checkout-timing metrics empty** (v2 `web_sessions.avg_seconds_to_checkout`, `avg_seconds_to_purchase`,
  `checkout_steps`; v1 `session_avg_seconds_to_checkout`, `session_avg_seconds_to_purchase`,
  `session_checkout_steps`). Brand 20 Jul–Sep (the only brand with sessions): 6,867 sessions reached checkout and
  4,541 purchased, yet `first_checkout_at` / `checkout_step_count` are never set and `seconds_to_purchase` is never
  > 0. Root cause (documented in dbt `fct_session_funnel.sql`, "KNOWN GAP"): Shopify's `checkout_started` pixel is
  server-side (`platform='app'`, no Snowplow session id), so session-native checkout counters are structurally
  zero; `stage_reached_checkout` uses the cart_token bridge instead. `avg_seconds_to_add_to_cart` works (≈96 % of
  add-to-cart sessions timed). Fix = dbt: plumb `int_cart_journey.first_checkout_at` (cart_token grain) through
  `attr_snowplow_sessions`. Until then Phase 4 marks the three as unavailable (data-quality waiver) so the agent
  does not answer "0 seconds".
- **`cube-v2` runs CUBEJS_DEV_MODE=true** like v1 (hot reload of model_v2; bound to 127.0.0.1). Revisit at cutover.
- **Brand 20 September returns (seen during Phase 5, not a v2 issue — v1 identical):** 747 returned orders on
  1,097 orders, return revenue ₹1.53M on event date (Jul 134, Aug 487) → net sales ₹528k < net COGS ₹883k, so
  net ROAS is negative. Possibly exchanges counted as returns (Logisy exchange tags) — investigate upstream.
- **Data quality:** 56 brand-20 products have no Shopify `product_type`; checkout-timing session metrics
  (`session_avg_seconds_to_checkout/_purchase`, `session_checkout_steps`) are null/zero Jul–Sep;
  ~4 % of Google campaigns (historical) missing from `gold.dim_campaign` (patched in serve, root cause in dbt).
- **New-signature lag:** a brand-new UTM combination is unlabelled for up to 5 min (dimension refresh);
  `channel_pnl` falls back to the dbt platform's finance bucket meanwhile; Cube joins would show null.
- **Gates:** the v1 inventory gates still measure the v1 surface (77 conflicts, 51 same-value ids, …) and stay
  red by design; the v2 gates (`scripts/v2_gates.py`) are 0 on catalogue_v2. Both are report-only in CI until
  the Phase 5 cutover.
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
