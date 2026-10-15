# Semantic v3 rebuild — progress log

Plan: `doc/semantic_v3/PLAN.md` (copy of `plan/SEMANTIC_V3_REBUILD_PLAN.md`). Spec: `doc/Seleric_Enterprise_Data_Model_v2_Two_Planes.md` v2.2 (§0B decisions, §0C target semantics, §0D constraints).
Read this file and spec §0B at the start of every session; append an entry at the end.
**Checklist (done / not done / findings / decisions / open questions): `CHECKLIST.md`** — keep it current.
**Decision register: `DECISIONS.yaml`** (where each decision lives, evidence codes, tests, how to change); `scripts/check_decisions_v3.py` keeps spec, register and code in step.

## Status
| Phase | State | Gate |
|---|---|---|
| 0 Baseline + capability inventory | **done 2026-10-14** | **PASS** — CAPABILITY_MAP covers 100 % of catalogue_v2 ids + Cube v2 members (0 gaps) |
| 1 Order classification | **done 2026-10-14 (applied)** | **PASS** — v3_contracts 15/15 live; owner reviewed outcomes (D-22a–e) |
| 2 Component ledger | **done 2026-10-14 (applied)** | **PASS** — contracts 27/27 live; strict reconciliation (line-verified net-sales + cost bridges) 0 unexplained ₹ |
| 3 Ads, web, attribution | **done 2026-10-14 (applied)** | **PASS** — contracts 39/39 live (spend = gold, native grains, LPV, FIN-15, FIN-07 attribution) |
| 4–9 | not started | — |

## Where the work lives
- Agent Core: branch `semantic-v3`, worktree `/home/tervigon/work/agent_core_v3`. **Not** checked out in `/opt/seleric/Seleric_Agent_Core`: compose runs from there and bind-mounts `catalogue*/` into the live MCP, and Jenkins deploys `main` there. Commit locally; never push to `main`.
- **Spec / plan:** the committed copies are in this worktree. `/opt/seleric/Seleric_Agent_Core/doc/Seleric_Enterprise_Data_Model_v2_Two_Planes.md`,
  its `.bak` and `/opt/seleric/Seleric_Agent_Core/plan/` are untracked snapshots from 2026-10-14 and are now **stale**: record
  §0B answers only in the worktree copy.
- mage-ai: branch `semantic-v3`, worktree `/home/tervigon/work/mage_v3` (`.env` is a symlink to the main checkout's, untracked). Earlier: no branch (no edits in Phase 0). `/opt/seleric/mage-ai/infra/cube` is bind-mounted into the live `cube-v2` (dev mode, hot reload), so mage-ai work must also go in a worktree (`semantic-v3`), not a branch switch in `/opt/seleric/mage-ai`.

## Open questions (ask the owner when the phase reaches them)
1. ~~**D-11a**~~ DECIDED 2026-10-14: no extra data; legacy COD / checkout / weight stay unrecorded.
2. **Allocated-family period rule** (Phase 4): same day / same period / reproduce today's `product_ad_line` `delivery_share`.
3. **Monthly-close history** (Phase 4): months before the first snapshot shown as "reconstructed from current state", or not offered?
4. ~~**Unknown-outcome review**~~ done 2026-10-14 → D-22a–e; 68 sale orders (₹96k) remain Unknown.
5. ~~**D-16 hashing proposal**~~ approved 2026-10-14 → D-16a.
6. **Stop the Cube v1 container?** (Phase 9).
7. Any unexplained ₹ in reconciliation (Phases 2–4, 7).
8. **query_log retention is 7 days** (Phase 9): the "14 days of zero reads" check needs a longer `system.query_log`
   TTL (a server config change) or two consecutive 7-day checks. Owner to choose.
9. ~~**Live v2 defects (now):**~~ **DECIDED 2026-10-14 (D-26): leave broken in v2 until the v3 cutover.** F0-1 / F0-2 break Agent answers today. Options (both are production writes; neither was done):
   (a) apply the missing serve DDL — for F0-2 only `serve.customer_ltv` from 8a2b333; for F0-1 the f645091 SQL, which
   D-25 says is superseded, so (a) is not available for F0-1 without reopening D-25; (b) revert the Cube model_v2 members
   added by 8a2b333 / f645091 (hot reload makes it live). Or leave them broken until the v3 cutover. Owner to choose.
11. **Brand 28 refusal vs no-loss** (Phase 1 / 7): v2 computes brand-28 `net_profit` / `pnl_net_profit` (= −spend),
    ROAS and AOV; v3 refuses them (plan Phase 1 contract). Phase 7 will see value → refusal with no D-id: owner to
    confirm, then add a decision id.
10. ~~**Payments vs exchanges**~~ answered from data (Phase 1): replacements carry `manual`-gateway sale transactions (₹6.2L success) = non-cash exchange credits → payments `refine [D-01]`; separate them in Phase 2. Was: `payment_amount` / `payments.transactions` are `tbd` until we check whether
    non-cash exchange credits sit in payment transactions (D-01).

## Log

### 2026-10-14 — Phase 0 (baseline + capability inventory)
Done (all reads `readonly=1`; no DDL, no Cube or MCP change, nothing pushed):
1. **Spec decisions:** D-19o / D-21 / D-18r / D-13r / D-22 (and D-23–D-25) were already in spec §0B v2.2, so no edit was needed.
   The spec, its v2.0 backup and the plan are committed on `semantic-v3` (82f39ca).
2. **Capability inventory:** `scripts/capability_inventory_v3.py` → `CAPABILITY_MAP.yaml` (1ef2758, refreshed in the closing commit).
   - Sources: catalogue_v2 through the MCP loader, live Cube v2 `/meta?extended=true`, and the live MCP over HTTP (23 tools,
     6 resources, 1 template, 4 prompts; saved as `mcp_surface_2026-10-14.json`).
   - Coverage: 233 metrics (49 keep / 183 refine / 1 tbd), 138 dimensions, 5 hierarchies + 100 drill steps,
     1,250 Cube v2 members (499 internal private-cube members), 30 concepts, 25 aliases, 147 retired ids,
     15 behaviours (query modes, brand scope, fiscal_year, hourly + breakdown bindings, meta keys, inline SQL,
     refusals) and 17 `new` v3 capabilities.
   - Every entry carries the §0B `decision_ids` that explain a changed number. No `retire` anywhere.
   - No platform-conversion member exists in Cube v2 except LPV, so D-13 does not conflict with the no-loss rule.
3. **DDL drift:** `scripts/serve_ddl_drift_v3.py` compares the two sides after ClickHouse `formatQuerySingleLine`. It folds out the
   view column lists, function-name case and CTE propagation.
   - Result: 59 same, 9 differ, 2 repo-only (`ddl_drift_2026-10-14.{md,json}`).
   - All real drift comes from three mage-ai commits of 2026-10-13 that are on `main` but not applied:
     - **f645091** — `semantic.fct_line_pnl` / `mv_fct_line_pnl` (repo-only); 07/08/09 rebuilt on it (adds `taxes`, `shipping_revenue`). Known; D-25.
     - **1db9f26** — `dim_product_variant` drops `product_cost_ex_gst` / `is_cost_set` in the repo; live still has them.
     - **8a2b333** — `serve.customer_ltv` adds `first_order_variant_id` in the repo; live lacks it.
4. **Number baseline:** `scripts/v3_baseline.py` → `catalogue/baselines/v2.json`.
   - Coverage: every catalogue_v2 metric plus each binding (hourly, breakdowns) × brands 20, 28 × Jul–Sep 2026, on Cube v2 = 1,494 rows.
   - Outcome: 24 errors (F0-1), 299 nulls (296 brand 28, which is spend-only; 3 brand-20 checkout-timing metrics, a known data gap).
   - The same script re-runs on v3 (`--url`, `--catalogue`) and compares two files (`--compare`) for Phase 7.
   - Anchors, brand 20: orders Jul / Aug / Sep = 2,091 / 1,980 / 1,096; ad_spend Sep = 1,193,322.06; net_profit Sep = −172,226.96; pnl_net_profit Sep = −1,527,829.44.
   - **Fingerprint:** `serve/fingerprint_views.py` was run through a wrapper that forces `readonly=1` → `serve_fingerprint_2026-10-14.json`.
     All 55 views hashed. On a second run, 53 match; `canonical_pnl` and `commerce_orders` changed (live data or `now()`), so they are unstable for fingerprint comparison.
5. **Consumers:** `CONSUMERS.md`.
   - The `query_log` window is only 7 days (Q8).
   - Agent path: Cube v2 = 640k reads. Cube v1 = 1.6k reads, the only reader of 13 views.
   - 7 views were never read: config and monitor views, `meta_ad_attribution_daily`, `platform_attribution_commerce`.
   - The `/opt/seleric/reports` scripts are inactive: last run 2026-09-14, one-off date ranges, no schedule. Per D-23 they are not migrated unless the owner says so.

Findings (live today, not caused by this work):
- **F0-1:** 4 tax metrics error in Cube v2 for every brand and month: `taxes_on_net_sales`, `pnl_taxes_on_net_sales`,
  `product_taxes_on_net_sales`, `product_pnl_taxes_on_net_sales`. model_v2 reads a `taxes` column that exists only in the unapplied f645091 SQL.
- **F0-2:** `customers` × `first_order_variant_id` / `first_order_sku` / `first_order_product_title` / `first_order_product_type` fails.
  model_v2 joins on `customer_ltv.first_order_variant_id`, which exists only in the unapplied 8a2b333 SQL.
- **Root cause of F0-1 and F0-2:** `cube-v2` runs dev mode and bind-mounts `/opt/seleric/mage-ai/infra/cube`, so every commit or checkout on that working tree changes
  the live Agent surface, while serve DDL is applied by hand. Rule for this rebuild: no branch switch in
  `/opt/seleric/mage-ai`; v3 work goes in a worktree.
- **Sweep for more defects:** every public Cube v2 member was queried once (brand 20, Sep 2026): 507 dimension and
  232 measure queries. The only failures are F0-1 (3 measures) and F0-2 (4 dimensions); the list is complete.
- **Commits that made them live:** `8a2b333` (model customers.yml + customer_ltv.sql) and `f645091` (model taxes
  members + fct_line_pnl SQL). Each commit changed the model and the SQL together; hot reload applied the model,
  and nobody applied the SQL.
- Cube v1 is confirmed to connect as `seleric_admin789` (`CUBEJS_DB_USER`); Cube v2 connects as `cube_serve`. The CONSUMERS.md classes are therefore exact.
- **F0-3:** brand 28 (spend only) gets computed `net_profit` / `pnl_net_profit` (= −spend) instead of a refusal. That is a Phase 1 contract item.
- F0-1 and F0-2 are recorded as `live_defect` on the CAPABILITY_MAP entries; v3 must deliver these capabilities.

Gate: **PASS** (0 coverage gaps). Next: Phase 1 (order classification). First, open a mage-ai `semantic-v3` worktree.

### 2026-10-14 — Phase 1 (order classification) — applied
Owner decisions (spec §0B): D-26 (leave v2 defects until v3), D-22a–e (outcome rulings), D-08b (C2P stays a cancelled
sale), D-16a (hashed person key). Owner approved the production apply.

Built (mage-ai `semantic-v3` 4fd4d8e + LowCardinality fix; review: `PHASE1_OUTCOME_REVIEW.md`):
- `semantic.core_order_class` / `mv_core_order_class` (refresh 5 min, ~4 s):
  - order_type: sale 23,579 · exchange_replacement 499 · zero_value 18 · test 1 (brand 20).
  - Exchange chain: parent from the ex-origin tag, the `Ex-n-` name, or attribution supersedes. All 499 resolve; max depth 5; `Ex-2-1-X` naming handled.
  - Outcome + evidence code; person_key; is_new_customer.
- `semantic.core_order_line_class` / `mv_core_order_line_class`: line outcome; kept lines in partial returns; in-order exchanges; outcome_quantity.
- `serve.fct_order`, `serve.fct_order_line` (DEFINER serve_definer): no raw PII; `src_*` amounts as recorded; is_maturing (45 d).
- `serve/semantic_v3/apply_v3.py`: applies only the v3 objects. `--preview` runs read-only with dependencies inlined.
- `serve/evaluations/v3_contracts.py`: grain, completeness, FIN-13, FIN-13-LINE, FIN-14, FIN-14-LINE, exchange roots, new customer, line quantity, spend-only scope, PII.
  - Result: **15/15 live**.
- Before and after the apply, every existing serve view fingerprint is unchanged (except `canonical_pnl`, which already varies between runs).

Brand 20 sale orders by outcome (all history):
- active 18,851; cancelled 462; rto 2,822; returned 1,008; exchanged 366; exchange_in_progress 2; unknown 68 (₹96k).
- The remaining Unknown is three groups: 52 cancelled after delivery, 11 returns with RTO evidence but delivered, 5 returns with no evidence.

Not done / carried forward:
- **Attribution keys on fct_order:** Phase 3. `fct_order_attribution_credit` covers 6,291 orders only, so last_non_direct coverage must be checked.
- **Line refund without a return** (66 sale lines; ADJUSTMENT orders, active outcome): their money treatment is a Phase 2 interview item.
- **cube_serve read check** of the new views was not run (the owner declined the credential-handling command). It is covered when Cube v3 queries them (Phase 5).
- New-customer vs gold: 22 sale orders differ (identity merge / ordering, D-15 / D-16a).

Next: Phase 2 — the component ledger (D-11a historical rate card is needed first).

### 2026-10-14 — Phase 1 follow-up (owner rulings D-22f / D-22g)
- D-22f: a line refunded with no return is `cancelled`. An order whose every line is refunded with no return is `cancelled` (45 sale + 7 other orders).
- D-22g: a returned quantity with no refund on an active order is `returned` (2 lines).
- SQL is updated in mage-ai `semantic-v3` (f0fe20b) and both rulings are in spec §0B. **Applied to production with the owner's OK; contracts 15/15 live.**
- Added `CHECKLIST.md`.

### 2026-10-14 — Phase 2 (component ledger) — applied
- Owner decisions: D-11a (no extra data), D-10a (shipping reversed on cancel / RTO), D-01a (value-difference price adjustment), D-22h (Unknown booked as cancelled), D-01b (exchange-credit gateways).
- Built (mage-ai `semantic-v3` 052de69):
  - `semantic.ledger_order_component` + `serve.ledger_order_component`, `serve.fct_payment`;
  - `serve/evaluations/v3_reconcile_gold.py`;
  - ledger and payment contracts in `v3_contracts.py`;
  - `apply_v3.py --live-deps`.
- Applied with the owner's OK. Live results:
  - contracts 26/26;
  - reconciliation: LINE / ORDER / PRODUCT pass, and the v2 bridge has 0 unexplained ₹ (all history: v2 3,53,55,650 → v3 3,49,43,898, Δ −4,11,752, every ₹ assigned to a decision id).
- Results are in `mage-ai/serve/evaluations/results/v3_*_20261014.json`. The full bridge table and findings F2-1…F2-8 are in `CHECKLIST.md`.
- Fingerprint after the apply: only live-data changes (gold CDC batch 23:29 + v2 refresh 23:35) and the new views.
- Next: Phase 3 (ads at native grain, website LPV, attribution bridge with the model parameter).

### 2026-10-14 — Phase 2 hardened (after review)
- The first bridge partitioned orders by outcome, so it could not fail. It was replaced:
  - every line's v3 deduction is compared with gold's own line deduction, and each delta must match a named decision rule;
  - costs are bridged gold-raw → v3 transform by transform, computed from gold columns only.
- The strict bridge surfaced rules I had applied without a ruling. The owner ruled on all of them: D-22i, D-22j, D-22k, D-11c, D-11d, D-01c, D-01d, D-05a, D-05b, D-08c (spec §0B + `DECISIONS.yaml`).
- Applied with the owner's OK (class + line class + ledger). Live results: contracts 27/27; LINE / ORDER / PRODUCT exact; net-sales and cost bridges 0 unexplained, all history and Jul / Aug / Sep.
- Business effect, all history: net sales −₹4.16L, variable cost +₹12.7L, contribution before ads −₹16.9L vs v2 (table in CHECKLIST).
- Open:
  - ~~F2-11~~ owner re-confirmed 2026-10-14: deduct;
  - F2-12 v2 cost-composition gap → Phase 7.
- Decision register: `DECISIONS.yaml` + `scripts/check_decisions_v3.py` (61 decisions, OK).

### 2026-10-14 — Phase 3 (ads, web, attribution) — applied
- Owner decisions: D-06a (last-touch fallback for the default model, flagged), D-06b (campaign fill from same-platform last-touch). D-22i re-confirmed.
- Built (mage-ai `semantic-v3` 3b81fd4…f06b66f):
  - `serve.fct_ad_delivery`, `fct_ad_delivery_hourly` (native grain);
  - `fct_web_session` (LPV, unknown members), `fct_web_event`;
  - `semantic.bridge_order_attribution` + serve view;
  - 12 new contracts (ADS-*, WEB-*, ATTR-*, FIN-15).
- Applied with the owner's OK. Live contracts 39/39. FIN-07 for attributed net sales is exact per brand × month.
- Findings F3-1…F3-5 in CHECKLIST. Register: 63 decisions (53 implemented), check OK.
- Next: Phase 4 (marts: period profit, event-date pnl_*, allocated product ads, monthly close). Owner questions Q2 (allocation period rule) and Q3 (close history) come up there.

### 2026-10-14 — Phase 4 (marts) — applied
- Owner decisions: D-27 (allocated rule: same day, default-model credit), D-27b (allocated LPV = website sessions), D-12b (months before the first close are reconstructed and flagged), D-21a (event-date family keeps the product-cost restock credit on the order date, as v2).
  - Evidence shown for D-21a: Sep 2026 event-date product cost differs by ₹3,60,032 between the two dating rules (Aug ₹26,475); v2's event minus cohort product cost is 0 in every month.
- Built (mage-ai `semantic-v3` e26f74c, c8ea8e8 and later; applied in two batches with the owner's OK, nothing in v2 touched):
  - `semantic.mart_profit_fact` + `serve.mart_period_profit` / `mart_pnl_event`;
  - `semantic.mart_product_ad_allocated` + `serve.mart_product_ad_allocated`;
  - `semantic.snap_profit_monthly` (append-only) + `serve.mart_period_profit_closed` / `mart_pnl_event_closed` + `serve/semantic_v3/close_month.py`;
  - `apply_v3.py` append-only table support; 21 new contracts (57/57 live); event bridge in `v3_reconcile_gold.py`.
- Live results: contracts 57/57; reconciliation: LINE / ORDER / PRODUCT and the Phase 2 net-sales and cost bridges still pass; EVENT BRIDGE passes (results in `serve/evaluations/results/v3_reconcile_p4_20261014.json`).
  - Anchors, brand 20 Sep 2026: cohort `net_profit` −1,72,227 (v2) → −2,89,624 (v3), Δ −1,17,397 = the cohort leg; event-date `pnl_net_profit` −15,27,829.44 → −15,52,748.81.
- Harness fix: COMPLETE-ORDER / COMPLETE-LINE now allow 60 s for loads in flight during a refresh (one order loaded during a refresh made them fail intermittently).
- Open (KNOWN_LIMITS): **L-24** event-dating leg not itemised by cause; **L-25** default attribution model only in the marts; **L-27** no month locked and no schedule yet.
- Next: Phase 5 (Cube v3 model, `cube-v3` on :4003). First owner item there: which month to lock first and the close schedule (can wait).
- Review round (same day): the mart order-outcome counts were compared with the classified outcomes (differences are exactly D-22h / D-22f / D-22g, now a line-based contract); the event-dating leg went to the owner (D-21b: accept v3 dating); snapshot-vs-live contracts got cut-awareness and a REFRESH-IN-FLIGHT gate; closed views take `is_maturing` from the close time; `close_month.py` inserts by column list and refuses on schema drift (L-28); D-21a scope noted. Phase 5 inputs for the allocated and product P&L ids are in CHECKLIST.

## 2026-10-14 (evening) — Phase 4.5 planned: date-axis switch (order_date / event_date)
- Scope check and owner interview (3 batches): decisions D-28 … D-37 recorded in DECISIONS.yaml and spec §0B; contract in spec §0C C8; limits L-29 … L-32; Phase 4.5 added to the plan (gate before Cube v3), with Phase 5 / 6 / 7 amendments.
- Superseded when 4.5 lands: D-21a (restock credit follows its deduction, D-30); D-21 amended (pnl_* become aliases, D-28).
- Findings (brand 20): no courier RTO timestamp (L-29); 50 outcome lines ₹77k + 2 refund-date-only RTO lines ₹1.9k Undated (L-30); Sep 2026 event net sales ₹4.21L vs ₹15.7L cohort; restock credit ₹4.26L event-dated vs ₹0.66L (D-30).
- Nothing built or applied. Open: Q12 (schema rebuild vs sentinel, owner confirms apply), Q13 (variance sign-off).

## 2026-10-14 (night) — Phase 4.5 built, not applied
- Owner ruled Q12: rebuild as new table versions (not sentinel). Built in `mage_v3`: ledger `event_date` strict + `event_date_source` (D-29, D-30), mart / snapshot as `_v2` tables with Nullable `event_date` + source, `serve.mart_profit` (both axes), `mart_product_profit` (line grain, same rule), thin `mart_period_profit` / `mart_pnl_event` and closed views, `close_month.py` repointed.
- Preview (`apply_v3.py --preview`, `v3_contracts.py --preview`): 65/65 contracts pass, incl. the new AXIS-* set. Undated in the previewed ledger: 75 rows / 45 orders / revenue deduction ₹79,945, consistent with the planning figure (52 lines, ₹79k).
- Not done: apply (production write, owner confirms), live `v3_contracts.py` / `v3_reconcile_gold.py` runs, `CAPABILITY_MAP.yaml` `date_axis` metadata (generated by `scripts/capability_inventory_v3.py`, needs live Cube / MCP), AXIS-BRIDGE at ad / product grain, AXIS-RATIO-CONTRACT, D-28 … D-37 stay `planned` until the apply, then become `implemented` and D-21a `superseded`.
- Note: the Sep 2026 `pnl_net_profit` anchor (−15,52,748.81) will move by the D-30 leg and by Undated items leaving Sep; update after the live reconcile.
- **Applied 2026-10-14 (owner OK):** `ledger_order_component_v2`, `mart_profit_fact_v2`, `snap_profit_monthly_v2`, `mart_product_ad_allocated` (MV re-pointed) and the serve views `ledger_order_component`, `mart_profit`, `mart_product_profit`, `mart_period_profit`, `mart_pnl_event`, both `_closed`. Live: `v3_contracts.py` 65/65; `v3_reconcile_gold.py` all legs PASS; Undated (brand 20) 69 rows, net sales −₹79,945. Old v3 tables left in place for rollback; D-28…D-37 register status flip still to do.

## 2026-10-15 — Phase 4.5 status check and register close-out
- State found: the Phase 4.5 serve build is applied live (contracts 65/65, reconcile all legs PASS). The mage-ai worktree (`mage_v3`, 12 files) and the Agent Core docs are still **uncommitted**.
- Done: register flipped. D-29, D-30, D-32, D-35, D-36, D-37 → implemented; D-21a → superseded (by D-30); D-28, D-31, D-34 stay planned and move to Phase 5 (they need Cube / catalogue capability metadata); D-33 stays Phase 6. L-32 resolved (rebuild, Q12). Added the D-35 reference to `close_month.py`. Docs re-rendered, `check_decisions_v3.py` OK (78 decisions, 67 implemented).
- Phase 4.5 still open: allocated-family `axis: cohort_only` metadata and `date_axis` on every CAPABILITY_MAP measure (needs live Cube / MCP via `capability_inventory_v3.py`); AXIS-BRIDGE at ad / product grain; AXIS-RATIO-CONTRACT; owner review of Sep 2026 and the last 6 months on both axes, the Undated list and the Q13 variance sign-off.

## 2026-10-15 — Phase 4.5 COMPLETE (gate PASS)
- Committed the live 4.5 code first (mage-ai `0a166fb`, Agent Core `66f6059`), then finished the open items.
- **Capability metadata:** `scripts/capability_inventory_v3.py` classes every one of the 233 catalogue metrics as `switchable` (47) · `placement_only` (43) · `native` (61) · `cohort_only` (32, incl. the 24 allocated-family ids) · `alias` (50 `pnl_*` / `product_pnl_*` ids → base metric on `event_date`). Cube v2 meta unchanged (sha 9afef9809513), so the only map changes are the new fields. The gate (`--check`) is the AXIS-RATIO-CONTRACT metadata half: D-31 table, refusal text with an `order_date` pointer, ratio contract on labelled ratios, placement columns proven by AXIS-PLACEMENT. Tamper-tested.
- **New base metrics to build in Phase 5/6** (an alias must not point at nothing; all on the finance view, never a commerce lookalike, so the campaign / ad drill survives): brand grain `channel_cancelled_orders`, `channel_returned_or_cancelled_orders`, `order_pnl_return_cancel_revenue` (`pnl_returned_orders` → existing `channel_returned_orders`); product grain `product_order_cancelled_orders`, `product_order_return_cancel_revenue`, `product_order_returned_or_cancelled_orders`. 6 `product_pnl_*` aliases (ad spend, net ROAS, MER, net margin %, net profit, BE-type) resolve to the allocated family: refused on `event_date` by D-34 (they answered before). Review fix (2026-10-15): `product_contribution_margin` / `_pct` are before ads, so they are switchable (not allocated) and their `product_pnl_*` aliases are live. The gate now also fails if an alias's base cannot slice by a dimension the alias supports today (time dimension = the axis, `row_type` = `order_pnl_row_type`), or if a new base has no class.
- **Contracts 67/67 live:** AXIS-PLACEMENT extended to 12 columns; new AXIS-BRIDGE-AD and AXIS-BRIDGE-PRODUCT (0 unexplained ₹; perturbed without the Undated leg they fail on 26 / 47 rows). AXIS-CLOSE: `close_month.py --restatements` and `--selftest` (read-only, no month locked).
- **Reconcile live: exit 0**, 0 unexplained ₹. Sep 2026 brand 20: event-date `pnl_net_profit` −11,96,265.14 (v2 −15,24,729.19), order-date −2,80,019.29; D-30 leg +3,57,080.
- **Owner checkpoint (all answered):** both axes accepted; Q13 variances and D-30 leg signed off; Undated kept visible (live: 52 orders / 84 lines / 257 ledger rows, −₹81,680 — the earlier "52 lines ₹79k" and "69 rows" were other units/moments); net AOV and LTV:CAC `order_date` only.
- Still deferred to later phases (not 4.5): Cube/MCP runtime of D-28, D-31, D-34 (Phase 5/6, now phase 5 in the register), D-33 Agent default (Phase 6), AXIS-NOMIX resolver half (Phase 6), `v3_id_map.yaml` (Phase 6). Next: Phase 5 (Cube v3).

## 2026-10-15 — Phase 5 started: Cube v3 model, increment 1 (finance)
- **Test rig (owner OK):** throwaway containers `seleric-cube-v3-test` (cubejs/cube:v1.6.48, dev mode off, `src/model_v3` from the `mage_v3` worktree, read-only `cube_serve` login via `--env-file`, 127.0.0.1:4003 / SQL 15433) and `seleric-cubestore-v3-test` on network `cube-v3-test-net`. Nothing in the live compose or cube-v2 changed. Restart after a model edit: `docker restart seleric-cube-v3-test`. Remove all three at the end of the phase.
- **Model:** `infra/cube/model_v3` = copy of model_v2 with the finance part rebased. One cube `profit` over `serve.mart_profit` (45 measures, both time dimensions `order_date` / `event_date`, `event_date_source`, `is_undated`, `attribution_member`, close statuses) and ONE view `finance` replacing the `pnl` and `order_pnl` twins (no `pnl_*` measures). Every measure carries `meta.date_axis` (and `meta.v2` = the v2 member it replaces, `meta.decisions`, `meta.ratio_contract`, a refusal text on cohort-only). Costs and deductions are shown positive, as v2; `cancel_revenue` / `rto_revenue` / `return_revenue` are separate (D-05); `net_sales` includes shipping and exchange adjustment; `discounts` is memo (already inside `gross_sales`, D-09a); `net_cogs` = product + fulfilment + exchange cost so `contribution_margin` = ledger contribution before ads; `shipping_cost` is forward only and `rto_cost` is reverse shipping. Ratios are NULL when net sales <= 0 (D-31).
- **Parity (`serve/evaluations/v3_cube_parity.py`)**: every finance measure on both axes (Undated included), per brand × month, equals the SQL it declares over `serve.mart_profit`, and `net_sales` / `contribution_margin` equal the component ledger directly. 90/90 + 4 ledger checks pass; perturbed once it fails. Sep 2026 brand 20 through Cube: net sales 15,87,454, contribution 9,13,303, net profit −2,80,019 (= serve).
- Capability map: `shipping_cost` is placement-only (forward shipping), proven by AXIS-PLACEMENT (now 13 columns incl. `provisional_ops_cost`); contracts 67/67.
- Remaining model_v2 views are still copies reading the v2 serve views (they compile and answer as v2). To rebase next: product / product_pnl (mart_product_profit + allocated family), commerce (fct_order), paid_media(+hourly, breakdowns), web_*, attribution(_paths), customers, unit_economics, returns, payments; then pre-aggregations, brand refusals, `cube-v3` compose service, F0-1 / F0-2.

## 2026-10-15 — Phase 5, increment 2: product / variant P&L
- **Applied with the owner's OK:** `serve.mart_product_profit` replaced (CREATE OR REPLACE VIEW, nothing read it; rollback = previous SQL in mage-ai `1a3e83d`). It is now order × line × event-date grain with the order dims (finance_channel, sub_channel, is_paid, is_new_customer, shipping_region, payment_bucket), the default-model attribution and every ledger component, so Cube can slice product P&L by channel / campaign / ad and count distinct orders. 0.7 s plain view, 39,454 rows. Live contracts 68/68 incl. the new `PRODUCT-DIMS-MART` (product-view dims agree with `mart_profit` on both axes; perturbed it fails on 9,734 groups). The payment-bucket / region rule is copied from the mart SQL, and that contract is the drift guard (a shared order-dims view is a possible later cleanup).
- **Cube:** cube `product_profit_fact` + view `product_profit` (34 measures: the finance P&L before ads by product / variant / SKU / type, `product_orders`, `product_new_customers`, cancelled / RTO / returned order counts, `units_sold`). View and cube names share one namespace, hence `_fact`. Parity test now covers both views: 166/166 (Cube = declared SQL = component ledger, both axes, Undated included).
- **Fix found by the parity test:** a fully cancelled order has net sales of ±1e-13 from float summation, which flipped the "NULL when net sales <= 0" ratio guard between runs. The guard now rounds to 2 decimals.
- Removed from model_v3: the v2 `product_pnl` view (its `pl_*` ids). Its after-ads ids (`pl_ad_spend`, net profit, ROAS, MER, CAC...) are the allocated cohort-only family and come with the allocated rebase next.

## 2026-10-15 — Phase 5, increments 3-4: paid media on website LPV, allocated family
- **Applied with the owner's OK (new views, nothing existing changed):** `serve.fct_paid_media_daily` (delivery rows UNION website LPV rows; `landing_page_views` = sessions with a page view; `campaign_key` / `ad_key` unknown members) and `serve.mart_product_ad_profit` (allocated rows + the line's cohort P&L, one row per allocated line, no fan-out). Live contracts 70/70 incl. `PAID-DAILY`, `ALLOC-PROFIT` (perturbed: they fail on 274 / 52).
- **Cube:** `paid_media` reads the union view: `landing_page_views` / `cost_per_landing_page_view` are the website figure (D-13r major change), `platform_reported_landing_page_views` is the hidden-by-default twin (daily, hourly, breakdowns); new dims `campaign_key`, `ad_key`, `row_type`, `device`, `network`. `product_ads` (cube `product_ads_fact`): 26 measures of the allocated family, all `date_axis: cohort_only` with the refusal text; Sep 2026 brand 20 through Cube: allocated ₹7,87,188 + Unallocated ₹4,06,134 = ₹11,93,322 = delivery.
- **Consequences to carry into Phase 6/7 (not silent):** (1) `landing_page_views` on the **hourly** and **breakdown** bindings is no longer available as an ad metric: the website has no hour grid (session_hour timezone vs ad hours unverified) and no age / gender / placement slice. Only the platform twin remains there. Needs an owner call if hourly website LPV is wanted. (2) `product_ltv_cac_ratio` is not in `product_ads` yet (needs LTV). (3) `product_contribution_margin(_pct)` are before-ads and live in `product_profit` (names `contribution_margin`, `contribution_margin_pct`).
- **Checks:** `v3_cube_parity.py` 195/195 (finance 90, product_profit 68, product_ads 26, ledger 8, paid_media 3); `v3_cube_smoke.py` every measure and dimension of every view answers: 728/732; the 4 failures are the known **F0-2** defect (`customers.first_order_*`, `serve.customer_ltv.first_order_variant_id` missing in the live v2 view). v3 must deliver it from a v3 object (D-26: no write to v2); done with the customers rebase.
- **Still on v2 sources (next):** commerce (→ `fct_order` + ledger; money from the ledger), product view units (`units_ordered`, `returned_units`, `unit_return_rate`, `units_per_order`: need line quantities by outcome in `mart_product_profit`), funnel_orders, web_sessions / web_funnel / web_events / web_event_detail (→ `fct_web_session`, `fct_web_event`), attribution(_paths) (→ `bridge_order_attribution`), customers (+F0-2), unit_economics (inline `ltv_cac_daily` SQL → serve), returns, payments (→ `fct_payment`, D-01b). Then: pre-aggregations per date dimension, brand 28 / hourly-coverage refusals, `cube-v3` compose service on :4003, remove the v2-only cubes (`order_lines`, `product_economics`, `line_ad_spend`) once nothing references them.
- **Test rig left running** (remove at the end of the phase): containers `seleric-cube-v3-test`, `seleric-cubestore-v3-test`, network `cube-v3-test-net`.
