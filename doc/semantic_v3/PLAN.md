# Serve v3 + Cube v3 rebuild — implementation plan

## Context
The live audit (2026-10-14) showed the current `serve` (55 views) and Cube v2 (20 views, 233 catalogue metrics) produce wrong or ambiguous business numbers:
- exchange replacements are counted as sales;
- RTO is hidden inside cancels and returns;
- "COGS" is actually a landed variable cost, and its method changes in Sept 2026;
- `canonical_pnl` is event-dated and some drill columns are hard-coded;
- Google is collapsed to campaign level in serve;
- landing-page views come from Meta instead of the website;
- four P&L engines compete.

The owner decided the business rules in an interview. They are recorded in the spec: `/opt/seleric/Seleric_Agent_Core/doc/Seleric_Enterprise_Data_Model_v2_Two_Planes.md` v2.1, with facts in §0A, decisions in §0B and target semantics in §0C.

Goal: rebuild serve, then Cube and the catalogue, on one component ledger, then clean up the old versions. **Hard constraint (owner): no capability loss.** Every existing metric id, dimension, view, hierarchy, drill path and query mode must keep working or be refined. Nothing is removed silently. Work spans many sessions, so this plan defines phases, gates and a progress log.

**Interview rule (owner):** any new business decision found during the build stops work on that item. Ask the owner (AskUserQuestion, with evidence and ₹ impact), record the answer in spec §0B, then continue. Never assume.

## Decisions added in planning (write to spec §0B in Phase 0)
- **D-19 overridden:** product-level ad metrics (~25 `product_*` spend/ROAS/MER/CAC/delivery ids) are **kept** as an `allocated` family.
  - Allocation rule: attribution credit (default model) × line gross-sales share.
  - Every row carries `allocation_rule_version`.
  - Spend that can't be allocated appears as an "Unallocated" row.
  - These metrics are never used in default or overview answers.
  - The no-allocation period-match model (D-07) stays the default.
- **D-21:** the event-date `pnl_*` family (25 ids) is **kept** as an explicit family (dashboard-style, "processed in period"). It is built on the same component ledger, so closed-period totals reconcile with the order-cohort figures. The order-date cohort remains the default.
- **D-18 refined:** the `net_profit` ids are **kept**. They resolve to contribution after ads, and the label and description say opex is excluded.
- **D-13 relaxed for LPV only:** `landing_page_views` and `cost_per_landing_page_view` move to the website definition (D-17) with a major version bump. A hidden-by-default twin, `platform_reported_landing_page_views`, is kept for diagnostics. Other platform conversions stay hidden.
- **fct_line_pnl drift:** superseded, not applied live; its line-GST and shipping-tax SQL is reused in the ledger; the file is removed in Phase 9.
- **Cutover:** parallel build, then switch (confirmed by the owner).
- **D-23 scope:** agent path only; seleric_systems and Cube v1 are excluded (see Scope).
- **D-22 (RTO rule): four outcomes, evidence-based.**
  - Not shipped + cancelled → Cancellation.
  - Shipped + confirmed RTO (RTO tag, `cogs_return_basis='rto'`, or non-delivered courier terminal status) → RTO.
  - Delivered + confirmed return → Customer Return.
  - Conflicting or insufficient evidence → **Unknown**, kept visible.
  - Every row has an `outcome_evidence` code. Order date is kept, and each deduction is counted once.

## Architecture choices (technical, from repo conventions)
- **Build alongside, then cut over.** New objects get new names in the existing `serve` DB and a new Cube model `infra/cube/model_v3`, run as a `cube-v3` service on 127.0.0.1:4003. A `catalogue_v3` is generated from it. The MCP switches only after the gates pass, with rollback by an env flip (same pattern as the semantic-v2 cutover, PROGRESS.md 2026-10-04). Old views keep running until Phase 9.
- **Ordinary facts stay views** over `gold.* FINAL`:
  - DEFINER `serve_definer`;
  - deployed with `mage-ai/serve/apply_views.py --allow-new`;
  - files in a new domain directory `mage-ai/serve/core/views/`.
- **Heavy derived logic** goes into `semantic.*` tables filled by refreshable materialised views (`REFRESH EVERY 5 MINUTE … DEFINER semantic_refresher`), deployed by the new `serve/semantic_v3/apply_v3.py` (machinery copied from `serve/semantic/apply.py`; see "Never deploy the drift"). Serve then exposes them as thin views. This covers:
  - exchange-chain resolution;
  - outcome classification;
  - the component ledger;
  - the allocated product-ad family.
- **Monthly close (D-12):** `semantic.snap_component_ledger_monthly` is an append-only table keyed by `close_month`. It is written by a new script `serve/semantic/close_month.py`, run by cron/Mage on the 1st of each month. Serve views take an `as_of` mode: live, or a closed month.
- **Never deploy the drift.** `serve/semantic/apply.py` runs *every* `views/*.sql` file and DROP+CREATEs the MVs. Committed-but-unapplied `06b_fct_line_pnl.sql` would go live, and `07/08/09` would be rebuilt on top of it, changing live agent numbers. So:
  - v3 semantic objects live in `mage-ai/serve/semantic_v3/` with their own `apply_v3.py`, written by copying the MV/refresher machinery of `apply.py`;
  - `apply.py` is never run over the v2 files during the rebuild;
  - `apply_views.py` is always called with **explicit view names**. With no arguments it re-applies every live view from HEAD.
- **Production-write discipline.** There is no staging ClickHouse and no staging Cube, so every DDL apply is a production write.
  - Each apply step is shown to the owner and confirmed at that moment.
  - All commits go on `semantic-v3` branches. A push to Agent Core `main` triggers Jenkins `rsync --delete` + `compose up --build`, even for doc-only commits. So the spec, PROGRESS and the `cube-v3` compose service stay on the branch until cutover.
- **Gold is not changed.** Gold dbt fixes, such as a proper exchange parent key, are noted as follow-ups only. The one exception is the D-11 back-fill: if it needs data that only exists in silver, stop and ask.
- **Drift fix first:** commit `f645091` (`semantic.fct_line_pnl`) is on `main` but was never applied live. Phase 0 decides with the owner whether to apply it or leave it superseded. Recommendation: supersede it and reuse its SQL patterns (line GST, shipping as a composite supply).

## Target serve objects (all new; names from spec §8, refined)
| Object | Grain | Kind | Source / notes |
|---|---|---|---|
| `serve.core_dim_order_class` (semantic MV) | order | MV table | `order_type` (sale/exchange_replacement/zero_value/test), `root_order_id`, `chain_depth`, `outcome`, `outcome_evidence`. Replacement detection: `Ex-n-` name, `exchange-replacement` tag, `ex-origin` ≠ own name (310 originals self-tag), `is_exchange_replacement` |
| `serve.fct_order` | order (sale + replacement rows, typed) | view | `gold.fct_orders` + class; attribution keys (default model) denormalised; customer `is_new` (first placed, D-15), guest identity via hashed phone/email (D-16) |
| `serve.fct_order_line` | line | view | `gold.int_finance_order_lines`; line outcome; GST split; cost history |
| `serve.ledger_order_component` (semantic MV) | line/order × component × date_role | MV table | **The single finance spine.** One signed row per component (§0C C2/C3: gross_sales, shipping_revenue, cancelled/rto/returned_sales, exchange_price_adjustment, gst, product_cogs, product_cogs_credit, exchange_* costs, packaging, forward/reverse shipping, gateway, cod, checkout, weight). Each row carries `order_date` and `event_date`, plus `cost_method`/`cost_status` and `outcome_evidence`. Back-fill for D-11 uses rate cards; components with no rate are `missing` (D-11a) |
| `serve.fct_ad_delivery` | platform-native entity × day (+hour binding) × segment | view | Meta ad×day; Google campaign×adgroup×ad×device×network×day (PMax ad = NULL). No platform conversions except the LPV twin. Reach is never summed |
| `serve.fct_web_session` / `fct_web_event` | session / event | view (reuse `session_funnel`, `web_events` logic) | adds `is_lpv` (≥1 page view), `ad unknown`/`campaign unknown` members |
| `serve.bridge_order_attribution` | order × model × touch | view | `gold.fct_order_attribution` (last-touch, all orders) + `fct_order_attribution_credit` (4 models, 6,291 orders); `model` parameter, default `last_non_direct` |
| `serve.mart_period_profit` | brand × day × platform × campaign × ad (+ Unattributed + spend-without-orders rows) | view/MV | order-cohort contribution (attributed) − delivery-date spend; replaces `pnl_daily`/`order_pnl_daily`/`canonical_pnl`/`channel_pnl` as projections |
| `serve.mart_pnl_event` | same keys, event date | view | `pnl_*` family (D-21) from the same ledger |
| `serve.mart_product_ad_allocated` (semantic MV) | line × campaign/ad | MV table | allocated family (D-19 override); unallocated row |
| Kept and refined | — | — | dims (`dim_brand`, `dim_traffic_source`, `dim_product_variant`, `dim_campaign/adset/ad`), `meta_ads_breakdown_daily`, `ad_changes`, `customer_ltv`, `touchpoints`, `attribution_paths`, `payments`/`refund_events` (internal cash view), funnel marts — reused, with fixes only where the spec requires them |

## Phases (each ends with a commit, a PROGRESS entry and a gate)
Create a progress folder: `/opt/seleric/Seleric_Agent_Core/doc/semantic_v3/` containing `PLAN.md` (a copy of this plan), `PROGRESS.md` and `CAPABILITY_MAP.yaml`. Branches: `semantic-v3` in mage-ai and in Seleric_Agent_Core. Seleric_Agent stays on its `gaurav` branch.

**Phase 0 — Baseline and capability inventory (no behaviour change)**
1. Write D-19o/D-21/D-18r/D-13r/D-22 into spec §0B. Commit the spec and its backup.
2. Capability inventory from **four sources**: Cube v2 `/meta`, catalogue_v2, the MCP tool surface (`metrics_query`, `metrics_drilldown`, `semantic_sql`, resources), and `cube.js` behaviour (`checkSqlAuth` brand scoping). Name these explicitly:
   - hourly bindings (Google hourly = campaign×hour×device×network, no ad level);
   - Meta breakdown sliced bindings;
   - `semantic_sql` on :15432 with brand scoping;
   - the `fiscal_year` granularity;
   - `stable_key` / `composition` / `allocated` meta;
   - hierarchies;
   - the `ltv_cac_daily` and `funnel_channel` inline SQL;
   - refused combinations.
   Known defect to fix at grain, not carry over: the `Ads.Delivery` hourly binding has 304,008 duplicate rows on its declared keys (`doc/semantic_datasets/CERTIFICATION.md`). Tool (new script `Seleric_Agent_Core/scripts/capability_inventory_v3.py`, reusing `capability_audit.py` and `build_semantic_inventory.py`). It lists every catalogue_v2 metric, dimension, hierarchy and drill path, every Cube v2 view/member, every refused combination (`doc/semantic_v2/REFUSED_COMBINATIONS_2026-10-08.md`) and every query mode (metrics_query, drilldown, semantic_sql) → `CAPABILITY_MAP.yaml`.
   - Each entry gets a target: `keep` / `refine(version bump)` / `replace(id)` / `new`.
   - `retire` is allowed only with an owner decision.
3. Repo ↔ live DDL diff for every serve and semantic object (`system.tables.create_table_query` vs the repo file, normalised). Record every drift in PROGRESS; known drift: `06b`/`07`/`08`/`09`.
4. Number baseline: snapshot every v2 metric × brands 20, 28 × Jul–Sep 2026 with the `v2_parity.py` harness (adapted to query v2 alone), and run `serve/fingerprint_views.py`.
5. Consumer activity check: `/opt/seleric/reports` scripts plus `system.query_log` reads per serve view (last 30 days, by user) → `doc/semantic_v3/CONSUMERS.md`.

Gate: CAPABILITY_MAP covers 100% of catalogue_v2 ids and Cube v2 members.

**Phase 1 — Order classification (semantic MV + `fct_order`, `fct_order_line`)**
- Implement D-01, D-05/D-22, D-08, D-08a, D-15, D-16, D-20.
- Contract tests include brand 28 (spend only): profitability, ROAS and AOV must be refused, not computed. Brands 24–27 are flagged low-volume.
- Tests (new `mage-ai/serve/evaluations/v3_contracts.py`, same exit-code convention as the existing parity scripts):
  - grain uniqueness;
  - FIN-13: each order has exactly one type and one outcome;
  - FIN-14: replacements add 0 orders and 0 gross sales;
  - every replacement resolves to a root;
  - the Unknown count is reported.
- Interview checkpoint: show the counts of the four outcomes (Unknown in particular), with samples, to the owner before moving on.

**Phase 2 — Component ledger**
- Implement C2/C3, the D-03/D-03a restock credit, the D-10 shipping charge, the D-11 back-fill, D-02/D-02a (ACTIVE = delivered) and D-02b ex-GST.
- Tests:
  - FIN-01: order = Σ lines + order-level postings;
  - each component counted once;
  - cost coverage %.
- Reconciliation report against today's `canonical_pnl` / `channel_pnl`. Every difference is explained by a decision ID (exchange, RTO, COGS split, …). Interview checkpoint on any unexplained ₹ difference.

**Phase 3 — Ads, web, attribution**
- `fct_ad_delivery` at native grain (Google ad×device×network, PMax NULL ad).
- Website LPV (D-17) with unknown members.
- `bridge_order_attribution` with the model parameter.
- Tests:
  - spend in the new fact = spend in gold, per platform and day;
  - session totals roll up exactly (C5);
  - FIN-15: no platform-sourced measures under generic names.

**Phase 4 — Marts**
- `mart_period_profit` (default), `mart_pnl_event` (D-21) and `mart_product_ad_allocated` (D-19o).
- FIN-07 roll-up tests: Σ campaigns + Unattributed = brand, for sales, spend and contribution.
- Event-date vs order-date totals reconcile over closed months.
- Monthly close script and `as_of` mode (D-12, D-12a maturing flag).

**Phase 5 — Cube v3 model** (`mage-ai/infra/cube/model_v3`, copied from model_v2 then rebased)
- Same conventions: one root fact per view, `public:false` cubes, `meta.v2` mapping (in place of `meta.v1`), composite pk, `nullIf` counts, fiscal_year.
- Platform-native drill hierarchies (Meta / Google per D-14), with a cross-platform common view.
- Pre-aggregations on the hot grains: period profit by day, ad delivery by day, orders by day. Cube Store is already running.
- Move the inline business SQL now in Cube (`ltv_cac_daily`, `funnel_channel`) into serve.
- Runbook: with dev mode off, model edits need `docker restart` of the cube-v3 container, then regenerate the catalogue.
- Add a `cube-v3` service to `Seleric_Agent_Core/docker-compose.yml` on :4003 (image `cubejs/cube:v1.6.48`, `CUBEJS_SCHEMA_PATH=src/model_v3`, dev mode **off**).

**Phase 6 — Catalogue v3 + MCP**
- `scripts/build_catalogue_v2.py` → parameterise it as `build_catalogue.py --version 3`, with the id map `catalogue/migrations/v3_id_map.yaml` derived from CAPABILITY_MAP. Update `VIEW_INFO`.
- Concepts default to the order-date cohort, `last_non_direct` and contribution after ads. The `allocated` and `pnl_*` families are reachable only on explicit request.
- Extend the `valid_for` rules: Google-only and Meta-only dims; PMax has no ad level.
- Update `tests/test_semantic_v2.py` → add `test_semantic_v3.py`. Point `validate_catalogue.py`, `smoke_cube.py`, `v2_gates.py` and `verify_compositions.py` at :4003.

**Phase 7 — Capability and number certification (the cutover gate)**
- **No-loss gate:** every CAPABILITY_MAP entry passes a live query on v3.
  - Same metric id, same dims and drills: answers are equal, or the difference is explained by a decision ID.
  - Every drill path in `hierarchies.yaml` still works.
  - `semantic_sql` still works.
  - Every previously refused combination is still refused or is now supported (never a crash).
- Run the 40-question `certify_qa.py` and the golden questions (`serve/evaluations/*golden_questions.yml`).
- A variance report for the owner, sorted by ₹. **Interview checkpoint: the owner signs off the variances.**

**Phase 8 — Cutover**
- Migrate the active `/opt/seleric/reports` scripts, the Seleric_Agent registry/services/evals and the MCP guards/prompts to the v3 names.
- MCP env → `CUBE_API_URL=http://cube-v3:4000`, `SELERIC_CATALOGUE_DIR=catalogue_v3`.
- Run the Seleric_Agent registry check.
- Watch 48h. Rollback = env flip.

**Phase 9 — Cleanup of old versions** (only after 2 weeks stable and a zero-consumer check)
- Drop the old serve views that CAPABILITY_MAP marks as replaced, including:
  - `canonical_pnl`, `pnl_daily`, `order_pnl_daily`, `channel_pnl`, `ad_channel_pnl_daily`, `product_pnl`, `product_ad_line`;
  - the `*_all_channels` wrappers;
  - `finance_waterfall_daily`, `ltv_cac_daily`, `meta_ad_attribution_daily`, `platform_attribution_commerce`.
- Drop the `semantic.fct_*_pnl` + `mv_*` objects.
- Retire `cube-v2` and `catalogue_v2`.
- Gold clean-up candidates (owner approval each): `fct_product_variant_cost_history__bak_20261001`, `int_payment_reconciliation__dbt_backup`.
- **Do not touch** the Amazon gold tables (D-04). Cube v1 itself is excluded from this rebuild: its serve views may be dropped once the read log shows them unused. Stopping the Cube v1 container is a separate owner decision (ask in Phase 9).
- Before each drop: `fingerprint_views.py`, then a `system.query_log` check for 14 days of zero reads.

## Scope and consumers (owner decision D-23, 2026-10-14)
- **In scope:** Seleric_Agent (`config/metric_registry.yaml`, `src/seleric_swarm/services/{metrics,catalogue_bootstrap}.py`, `eval/regress/regression.json`, `tests/unit/test_semantic_toolset.py`, branch `gaurav`), Core MCP (`src/seleric_mcp/app/query_planner.py` canonical_pnl guard, `gateway/prompts.py`, `semantic_layer/semantic_sql.py` view-name regex, `catalogue_service/service.py`, `semantic_inventory/*`, `catalogue_v2_src/datasets.yaml` → `catalogue_v3_src/datasets.yaml`), Cube v3, the new serve model, `/opt/seleric/test_agent/eval.py`.
- **Excluded (unused, owner):** `seleric_systems` web dashboard and Cube v1 (:4001, `infra/cube/model`). No compatibility is built for them. Their serve views are retired once the read log shows no active reads.
- **Reports:** `/opt/seleric/reports/build_organic_report.py` and `build_channel_split.py` read `serve.commerce_orders` / `serve.order_attribution`. Phase 0 checks whether they are still used (cron, recent runs or file changes, `system.query_log` user/host). Migrate only the active ones, in Phase 8.
- **Unaffected (gold only):** ReportAutomation, ad-creative and the seleric_systems scripts. Gold is not changed.
- **Stale mage-ai scripts** referencing the retired `serve.dw_*` / `ds_*` (`scripts/reconcile_api_to_ch.py`, `e2e_api_to_serve_report.py`, `audit_2026_coverage.py`, `purge_meta_nov_brand20.py`) are listed for Phase 9 cleanup with the owner's OK. `serve.amazon_*` users (`export_amazon_accounts_schedules.py`, OpenMetadata DQ) stay untouched (D-04).
- **OpenMetadata** contracts/lineage for retired views: update in Phase 9 via `py infra/openmetadata/apply_governance.py`.

## Reconciliation to gold (owner requirement; runs in Phases 2–4 and 7)
New script `mage-ai/serve/evaluations/v3_reconcile_gold.py`. It uses the same `_env.py` and exit codes, and writes `results/v3_reconcile_*.json`. For brand 20, each closed month plus the full history, it checks independently against `gold.int_finance_order_lines` / `fct_orders` / ads facts:
1. **Line:** each ledger component per line = the gold source column, after the documented decision transforms (exchange, RTO, split COGS, back-fill). Every row differs by 0 or by a named decision ID.
2. **Order:** Σ line components + order-level postings = order values (gross, shipping charged, GST).
3. **Product / variant:** Σ lines = product totals.
4. **Channel / campaign / ad:** Σ members + Unattributed = brand, for sales, contribution and spend. Spend = gold Meta + Google spend per day, exactly.
5. **Business:** the brand period total = Σ order cohort.
   - Event-date (`pnl_*`) and order-date totals are equal **over all history**.
   - Per period: event − cohort = an itemised **cross-period bridge**: prior cohorts' cancels/RTO/returns/exchange adjustments landing in this period, minus this cohort's events landing later. The bridge must have 0 unexplained ₹. (They are *not* equal per closed month; spec §4.1.)
6. **Bridge to today:** new vs `canonical_pnl` / `pnl_daily` / `order_pnl_daily`, split by cause (exchange, RTO, COGS layer, GST, shipping, back-fill). Unexplained ₹ ≠ 0 means stop and interview.
The tolerance is 0 in paise for sums of the same Decimal source. Float sources (ads spend) use an explicit rounding row, never a widened tolerance.

## Open questions to seed in PROGRESS.md (ask the owner when the phase reaches them)
1. **D-11a** (Phase 2): historical rate card for COD handling, checkout fee and weight discrepancy before 2026-09.
2. **Allocated-family period rule** (Phase 4): which spend is allocated to which orders: same-day, same period, or first reproduce today's `product_ad_line` `delivery_share` for parity?
3. **Monthly close history** (Phase 4): snapshots exist only from the first close onward. Are earlier months shown as "reconstructed from current state", or not offered?
4. **Unknown-outcome review** (Phase 1 checkpoint): counts plus samples per evidence code.
5. **D-16 hashing proposal** (Phase 1): hashed phone/email keys in serve, no raw PII to the Agent.
6. **Stop the Cube v1 container?** (Phase 9).
7. Any unexplained ₹ in reconciliation (Phases 2–4, 7).

## Session hand-off protocol
- At the start of each session, read `doc/semantic_v3/PROGRESS.md` (current phase, last gate, open questions) and spec §0B.
- At the end of each session, append to PROGRESS.md: what was done, commits, gate results, and open interview items.
- An open business question blocks only its own item. Record it under "Open questions" in PROGRESS.md.

## Verification (end-to-end)
- `python3 mage-ai/serve/apply_views.py --dry-run <names>`, then `--allow-new <names>` (always with explicit names); `python3 mage-ai/serve/semantic_v3/apply_v3.py --check` before any v3 MV apply. Each apply is confirmed with the owner (production writes).
- `python3 mage-ai/serve/evaluations/v3_contracts.py` (grain, FIN-01/07/13/14/15) → exit 0.
- `uv run pytest -m "not live"` and `uv run pytest tests/test_semantic_v3.py` in Seleric_Agent_Core.
- `CUBE_V3_URL=http://127.0.0.1:4003 uv run python scripts/validate_catalogue.py`, `smoke_cube.py`, `v2_gates.py --live` (pointed at v3), and `verify_compositions.py --brand 20`.
- `uv run python scripts/capability_check_v3.py` → 100% of CAPABILITY_MAP pass.
- Spot-check by hand: Sep-2026, brand 20:
  - exchange replacements = 0 orders;
  - RTO, Return, Cancel and Unknown counts;
  - Meta LPV = website sessions;
  - Google drill to ad group for a non-PMax campaign;
  - Σ campaigns + Unattributed = brand net sales.
