# Semantic v3 rebuild — progress log

Plan: `doc/semantic_v3/PLAN.md` (copy of `plan/SEMANTIC_V3_REBUILD_PLAN.md`). Spec: `doc/Seleric_Enterprise_Data_Model_v2_Two_Planes.md` v2.2 (§0B decisions, §0C target semantics, §0D constraints).
Read this file and spec §0B at the start of every session; append an entry at the end.

## Status
| Phase | State | Gate |
|---|---|---|
| 0 Baseline + capability inventory | **done 2026-10-14** | **PASS** — CAPABILITY_MAP covers 100 % of catalogue_v2 ids + Cube v2 members (0 gaps) |
| 1–9 | not started | — |

## Where the work lives
- Agent Core: branch `semantic-v3`, worktree `/home/tervigon/work/agent_core_v3`. **Not** checked out in `/opt/seleric/Seleric_Agent_Core`: compose runs from there and bind-mounts `catalogue*/` into the live MCP, and Jenkins deploys `main` there. Commit locally; never push to `main`.
- mage-ai: no branch yet (no edits in Phase 0). `/opt/seleric/mage-ai/infra/cube` is bind-mounted into the live `cube-v2` (dev mode, hot reload), so mage-ai work must also go in a worktree (`semantic-v3`), not a branch switch in `/opt/seleric/mage-ai`.

## Open questions (ask the owner when the phase reaches them)
1. **D-11a** (Phase 2): historical rate card for COD handling, checkout fee, weight discrepancy before 2026-09.
2. **Allocated-family period rule** (Phase 4): same day / same period / reproduce today's `product_ad_line` `delivery_share`.
3. **Monthly-close history** (Phase 4): months before the first snapshot shown as "reconstructed from current state", or not offered?
4. **Unknown-outcome review** (Phase 1 checkpoint): counts + samples per evidence code.
5. **D-16 hashing proposal** (Phase 1): hashed phone/email keys in serve, no raw PII to the Agent.
6. **Stop the Cube v1 container?** (Phase 9).
7. Any unexplained ₹ in reconciliation (Phases 2–4, 7).
8. **query_log retention is 7 days** (Phase 9): the "14 days of zero reads" check needs a longer `system.query_log`
   TTL (a server config change) or two consecutive 7-day checks. Owner to choose.
9. **Live v2 defects (now):** fix in v2 (apply the missing columns / revert the model members — a production write),
   or leave broken until the v3 cutover? See Phase 0 findings F0-1, F0-2.
10. **Payments vs exchanges** (Phase 1): `payment_amount` / `payments.transactions` are `tbd` until we check whether
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
- **F0-3:** brand 28 (spend only) gets computed `net_profit` / `pnl_net_profit` (= −spend) instead of a refusal. That is a Phase 1 contract item.
- F0-1 and F0-2 are recorded as `live_defect` on the CAPABILITY_MAP entries; v3 must deliver these capabilities.

Gate: **PASS** (0 coverage gaps). Next: Phase 1 (order classification). First, open a mage-ai `semantic-v3` worktree.
