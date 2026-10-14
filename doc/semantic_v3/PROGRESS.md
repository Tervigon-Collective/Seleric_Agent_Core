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
| 3 Ads, web, attribution | in progress | spend = gold; sessions roll up (C5); FIN-15 |
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
