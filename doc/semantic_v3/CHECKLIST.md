# Semantic v3 rebuild — checklist and notes

Status at 2026-10-14. Companion to `PROGRESS.md` (the session log) and `PLAN.md` (the plan). Decisions live in spec §0B
(`doc/Seleric_Enterprise_Data_Model_v2_Two_Planes.md`). `[x]` done · `[ ]` not done · `[~]` partly done / waiting.

Where the work is:
- Agent Core: `/home/tervigon/work/agent_core_v3`, branch `semantic-v3`
- mage-ai: `/home/tervigon/work/mage_v3`, branch `semantic-v3`
- Nothing is pushed. Live checkouts in `/opt/seleric/*` stay on `main`; never switch branches there.

---

## Phase 0 — Baseline and capability inventory · DONE · gate PASS

- [x] Spec decisions D-19o, D-21, D-18r, D-13r and D-22 are in §0B (they already were). The spec, its backup and the plan are committed.
- [x] Capability inventory → `CAPABILITY_MAP.yaml`.
  - Script: `scripts/capability_inventory_v3.py`.
  - Coverage: 233 metrics, 138 dimensions, 5 hierarchies, 100 drill steps, 1,250 Cube members, 23 MCP tools, 6 resources, 4 prompts, 15 behaviours, 17 new.
  - Every entry has a target and its decision ids. 0 gaps, no `tbd` left, no `retire`.
- [x] Repo ↔ live DDL drift (`scripts/serve_ddl_drift_v3.py` → `ddl_drift_2026-10-14.*`): 59 same, 9 differ, 2 repo-only. All of it comes from 3 unapplied commits.
- [x] Number baseline: `scripts/v3_baseline.py` → `catalogue/baselines/v2.json`, 1,494 values; it can compare v2 with v3 in Phase 7.
- [x] Serve fingerprint: all 55 views (`serve_fingerprint_2026-10-14.json`). 2 views change between runs on their own: `canonical_pnl` and `commerce_orders`.
- [x] Consumer check → `CONSUMERS.md`.
- [x] Live-defect sweep: `scripts/cube_member_sweep_v3.py` queried 739 members; 7 fail (F0-1, F0-2).

## Phase 1 — Order classification · DONE · applied to production · contracts 15/15

- [x] `semantic.core_order_class` and `mv_core_order_class` (refresh every 5 min, about 4 s).
  - Order type: sale / exchange_replacement / zero_value / test.
  - Exchange chain to the root sale: all 499 replacements resolve, max depth 5.
  - Outcome with an evidence code, hashed person key, new-customer flag.
- [x] `semantic.core_order_line_class` and its MV: line outcome, kept lines in partial returns, in-order exchanges, outcome quantity.
- [x] `serve.fct_order` and `serve.fct_order_line` (DEFINER serve_definer, no raw PII).
- [x] `serve/semantic_v3/apply_v3.py`: touches only v3 objects; `--preview` runs read-only.
- [x] `serve/evaluations/v3_contracts.py`: grain, completeness, FIN-13, FIN-14, exchange roots, new customer, PII, spend-only scope.
- [x] Owner reviewed outcome counts and samples (Q4) → D-22a–e. Later added D-22f/g.
- [x] Before and after the apply, existing serve views are unchanged (fingerprints).
- [x] **D-22f / D-22g** applied to production 2026-10-14: 52 orders cancelled (`refund_without_return_all_lines`), 15 lines cancelled, 2 lines returned. Contracts 15/15 live.
- [ ] Attribution keys on `fct_order` (default model `last_non_direct`). Moved to Phase 3.
- [ ] Cube login (`cube_serve`) read check of the new views. Not run: the owner declined the credential command. Covered in Phase 5.

## Phase 2 — Component ledger · NOT STARTED
- [ ] `serve.ledger_order_component`: one signed row per component (gross sales, shipping revenue, cancelled / RTO / returned sales, exchange price adjustment, GST, product COGS + credit, exchange costs, packaging, shipping, gateway, COD, checkout, weight).
- [ ] D-03 split COGS layers, D-03a restock credit, D-10 shipping as order-level revenue, D-02b ex-GST, D-11 back-fill.
- [ ] Separate non-cash exchange credits (`manual` gateway on replacements, ₹6.2L) from cash payments (D-01).
- [ ] Tests: FIN-01 (order = Σ lines + order-level postings), each component counted once, cost coverage %.
- [ ] Reconciliation report vs `canonical_pnl` / `channel_pnl`: every ₹ explained by a decision id.
- [ ] **Blocked on D-11a:** historical rate card for COD handling, checkout fee and weight discrepancy before 2026-09.

## Phase 3 — Ads, web, attribution · NOT STARTED
- [ ] `fct_ad_delivery` at native grain (Google ad × device × network; PMax ad = NULL). Fix the 304,008 duplicate hourly rows.
- [ ] Website LPV (D-17) with `ad unknown` / `campaign unknown` members; platform LPV twin hidden.
- [ ] `bridge_order_attribution` with a model parameter (default last_non_direct). Check coverage: the credit table has only 6,291 orders.
- [ ] Tests: spend = gold per platform and day; sessions roll up (C5); FIN-15.

## Phase 4 — Marts · NOT STARTED
- [ ] `mart_period_profit` (default), `mart_pnl_event` (D-21), `mart_product_ad_allocated` (D-19o).
- [ ] FIN-07 roll-ups; event vs cohort cross-period bridge; monthly close + `as_of` (D-12, D-12a).
- [ ] Owner questions: allocated-family period rule (Q2), monthly-close history (Q3).

## Phase 5 — Cube v3 · NOT STARTED
- [ ] `infra/cube/model_v3`; `cube-v3` service on :4003 (dev mode off); pre-aggregations; move the inline SQL (`ltv_cac_daily`, `funnel_channel`) into serve.
- [ ] Deliver the F0-1 / F0-2 capabilities. Brand 28 profit / ROAS / AOV must be refused.

## Phase 6 — Catalogue v3 + MCP · NOT STARTED
## Phase 7 — Certification (no-loss gate, variance sign-off) · NOT STARTED
## Phase 8 — Cutover · NOT STARTED
## Phase 9 — Cleanup · NOT STARTED

---

## Decisions taken in this rebuild (spec §0B)

| ID | Decision |
|---|---|
| D-26 | Live v2 defects F0-1 / F0-2 stay broken until v3 |
| D-22a | Bare `RTO` tag on an unshipped cancellation (949 orders, ₹18.9L) = RTO |
| D-22b | RETURN with a return record, no money refund and no delivery status (707, ₹13.8L) = RTO |
| D-22c | Shipped cancellation that was never delivered (346, ₹7.0L) = RTO |
| D-22d | RETURN with money refunded but no delivery status (256, ₹5.8L) = customer return |
| D-22e | ACTIVE order with RTO evidence (46, ₹88k) = RTO |
| D-22f | Line refunded with no return = cancelled; an order with every line refunded and no return = cancelled |
| D-22g | Returned quantity with no refund on an ACTIVE order = returned |
| D-08b | COD → prepaid conversion (11) stays a cancelled sale, linked to the new order |
| D-16a | Hashed person key only; no raw PII in serve, Cube or the Agent |

## Findings

| # | Finding | Status |
|---|---|---|
| F0-1 | 4 tax metrics error in Cube v2 (model reads `taxes`; SQL f645091 not applied) | left until v3 (D-26) |
| F0-2 | `customers` × first-order product (4 dims) errors (8a2b333 not applied) | left until v3 (D-26) |
| F0-3 | Brand 28 (spend only) gets net_profit = −spend instead of a refusal | v3 refuses it; owner to confirm at Phase 7 (Q11) |
| F0-4 | Cube v2 hot-reloads the model from `/opt/seleric/mage-ai`, so a commit there changes live answers while serve DDL is applied by hand | rule: work only in worktrees |
| F0-5 | 3 mage-ai commits are on `main` but not applied (f645091, 1db9f26, 8a2b333) | f645091 superseded (D-25); others carried into v3 |
| F0-6 | `system.query_log` keeps only 7 days | open (Q8) |
| F0-7 | `/opt/seleric/reports` scripts are inactive (last run 2026-09-14) | not migrated (D-23) |
| F0-8 | 7 serve views had no reads in 7 days; 13 are read only by Cube v1 | input for Phase 9 |
| F1-1 | Replacement names chain as `Ex-2-1-X` (second exchange of `Ex-1-X`); 310 originals self-tag `ex-origin` | handled |
| F1-2 | RTO evidence differs by era: bare `RTO` tag (Dec 2024 – Nov 2025), none (Jan – Jun 2026), Logisy tags (Jul 2026 on) | ruled D-22a–e |
| F1-3 | `is_delivery_recognized` is derived from the ACTIVE class, so it is not delivery evidence | not used |
| F1-4 | Courier status is mostly empty on RETURN orders before 2026-07 | ruled D-22b/d |
| F1-5 | Replacements carry `manual`-gateway "sale" transactions (₹6.2L) = non-cash exchange credits counted as payments | Phase 2 (D-01) |
| F1-6 | Duplicate identities are rare (7 phone groups, 5 email groups); the 40 guest orders have no contact data | D-16a |
| F1-7 | New-customer flag differs from gold on 22 sale orders (identity merge / ordering) | explained by D-15 / D-16a |
| F1-8 | `fct_order_attribution_credit` covers 6,291 orders only | Phase 3 check |

## Still Unknown after the rulings (brand 20 sale orders)

68 orders, ₹96k:
- 52 cancelled after delivery
- 11 returns with RTO evidence that were delivered
- 5 returns with no evidence

## Open questions for the owner

| # | Question | Needed by |
|---|---|---|
| Q1 | D-11a historical rate card (COD handling, checkout fee, weight discrepancy before 2026-09) | Phase 2 |
| Q2 | Allocated-family period rule | Phase 4 |
| Q3 | Monthly-close history before the first snapshot | Phase 4 |
| Q6 | Stop the Cube v1 container? | Phase 9 |
| Q7 | Any unexplained ₹ in reconciliation | Phases 2–4, 7 |
| Q8 | query_log retention: longer TTL or two 7-day checks | Phase 9 |
| Q11 | Brand 28 value → refusal change | Phase 7 |

