# Semantic v2 — progress log

Plan: [PLAN.md](./PLAN.md). Work happens on branch `semantic-v2` in mage-ai and Seleric_Agent_Core
and is merged to `main` at each stable point (first merge 2026-10-03, after the channel decisions).
Seleric_Agent work goes on its `gaurav` branch.

**Status 2026-10-04:** Phases 0–3 done and closed; both repos merged to `main` and deployed (mage-ai Jenkins
#224, Agent_Core #50). Cube v2 runs as `cube-v2` on 127.0.0.1:4002 next to v1
(:4001); 149 / 151 certified v1 metrics equal on v2 in every cell, the 2 differences documented. The agent
still runs on v1 Cube and the v1 catalogue — nothing user-facing has switched. Next: Phase 4 (catalogue v2 +
MCP resolver on the id map).

| Phase | Status | Commits |
|---|---|---|
| 0 Baseline & guards | ✅ done 2026-10-03 | Agent_Core c52bc55 · mage-ai 150c044 |
| 1 Conformed dimensions | ✅ done 2026-10-03 | mage-ai 150c044 |
| 2 Serve facts | ✅ done 2026-10-03 | mage-ai e1f8b6c |
| Channel decisions (pre-Phase 3) | ✅ done 2026-10-03 | mage-ai 393496b, c721983 |
| 3 Cube v2 model | ✅ closed 2026-10-04 | mage-ai 6f59a2e, f3361eb, 04e59a7 · Agent_Core 7e9bc17, 229d322 (all on `main`) |
| 4 Catalogue v2 + MCP + registry | 🟡 in progress (paused 2026-10-04) | Agent_Core branch `semantic-v2-p4` · Seleric_Agent branch `semantic-v2` · mage-ai `semantic-v2` |
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

## Phase 4 — catalogue v2 + MCP + registry 🟡 (paused 2026-10-04, resume here)
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
- **Tests**: `tests/test_semantic_v2.py` 40/40 pass; existing suite unchanged (296 pass, 8 pre-existing
  `test_canonical_model` failures from the moved Cube path).
- **Seleric_Agent**: `scripts/migrate_metric_registry_v2.py` (line edits, comments kept) → registry on v2 ids,
  `catalogue_filters` for 15 platform entries + 4 legacy meta_attr_*; aliases kept only where the MCP v2
  resolver agrees (10 dropped, e.g. "roas" → net ROAS wins over the agent's gross). Agent code: `catalogue_filters`
  sent by series + runner; live overlay only from unfiltered entries. Business-state fixtures moved to v2 ids.

Open (resume list):
1. Seleric_Agent unit suite: 1 new failure `tests/unit/test_v3_ui_connect.py::test_v3_runner_ns_uses_live_catalogue_id_not_llm`
   (expects a v1 id for "ns"; update to `pnl_net_sales`). The 2 drilldown failures are pre-existing on gaurav.
2. Gate script `scripts/v2_gates.py` (not written yet): resolution conflicts on catalogue_v2 (v1 had 77),
   registry drift, hard-cut coverage, live duplicate scan (use a relative tolerance — absolute 0.02 flags rates).
3. Known resolver limit: "atc sessions" / "product view sessions" resolve to `sessions` via concept-in-text
   (disclosed as a warning). 11 glossary terms dropped (targets retired without replacement).
4. Test MCP instance on catalogue_v2 + cube-v2 (second container, e.g. :8766) and live smoke; verify the
   provenance ClickHouse lookup against system.query_log.
5. Docs: PLAN §4/§5 vs. what was built; then Phase 5 cutover (MCP → cube-v2 + catalogue_v2, merge
   Seleric_Agent semantic-v2 into gaurav at the same time — v2 ids do not exist on the v1 MCP).

## Next steps
1. **Phase 4** — catalogue v2 on `catalogue/migrations/v2_id_map.yaml` (`v2_metrics` = the 106 v2 ids with
   member + date axis; `maps` = old → new + filters). Metric YAML schema v2 in `catalogue_service/loader.py`
   (`home_product`, `fact`, `date_axis`, `additivity`, `bindings` — e.g. ad_spend → paid_media / paid_media_hourly /
   paid_media_breakdowns —, `valid_for`, `version`), one resolver (`resolve_concept`), planner (bindings, hierarchy
   drill, composites gross_roas / cac / ltv_cac_ratio, `valid_for`, hard-cut rejection naming `new` + filters),
   brand member per v2 view, provenance v2 (trace via PHASE0.md). Update `Seleric_Agent/config/metric_registry.yaml`
   on the `gaurav` branch (drop aliases; ids must exist) — the teammate's uncommitted files there must be
   committed first. Re-point `build_metric_inventory.py` gates at the v2 surface; add `scripts/v2_parity.py` to CI.
2. **Phase 5** — MCP `config.yaml cube.api_url` → :4002 (in-network: `http://cube-v2:4000`); gates blocking in CI;
   regenerate inventory. Keep v1 Cube for seleric_systems (`daily_pnl.*` via nginx `/cube/`).
3. **Merge** `semantic-v2` → `main` at each stable point. mage-ai: a push to `main` triggers the Jenkins deploy
   (CI gates → rsync into /opt/seleric/mage-ai → rebuild + restart `mageai-local`); the dbt orchestrator runs back
   to back (~25 min, ~10 s gaps), so push right after a run completes. **Seleric_Agent_Core also deploys on a
   push to `main`** (Jenkins job `Seleric-Agent-Core`: rsync --delete into /opt/seleric/Seleric_Agent_Core +
   `docker compose up -d --build` → MCP and v1 Cube restart, ~1 min) — even for doc-only commits. That deploy
   reads `mage-ai/infra/cube/.env.v2` as the `jenkins` user: keep it group-readable (640, group tervigon) —
   build #49 failed at compose config load while it was 600.

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
- **Data quality:** 56 brand-20 products have no Shopify `product_type`; checkout-timing session metrics
  (`session_avg_seconds_to_checkout/_purchase`, `session_checkout_steps`) are null/zero Jul–Sep;
  ~4 % of Google campaigns (historical) missing from `gold.dim_campaign` (patched in serve, root cause in dbt).
- **New-signature lag:** a brand-new UTM combination is unlabelled for up to 5 min (dimension refresh);
  `channel_pnl` falls back to the dbt platform's finance bucket meanwhile; Cube joins would show null.
- **Gates** still measure the v1 surface (77 conflicts, 51 same-value ids, …); they only turn green once
  Phase 4/5 re-point them at v2. CI step is report-only (`ci_quality_gates.sh`, on `main` since 2026-10-03).
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
