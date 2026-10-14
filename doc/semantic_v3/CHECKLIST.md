# Semantic v3 rebuild — checklist and notes

Status at 2026-10-14 (Phases 0–4 done). Companion to `PROGRESS.md` (the session log) and `PLAN.md` (the plan). Decisions live in spec §0B
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

## Phase 2 — Component ledger · DONE · applied to production · contracts 27/27 · reconciliation 0 unexplained ₹

- [x] `semantic.ledger_order_component` and its MV (refresh 5 min after the class MVs, ~277k rows). Served as `serve.ledger_order_component`.
  - One signed ex-GST row per order, line and component; `line_item_id = 0` for order-level postings.
  - Every row carries cohort_date (root sale date, the default), order_date, event_date, cost_method and cost_status.
- [x] **Revenue:**
  - gross_sales: line value after discount, ex-GST. An item swapped out inside the same order leaves gross (D-01c).
  - shipping_revenue: order level, composite-supply GST (D-10, D-25).
  - cancelled / rto / returned sales: item level (D-22i). The deduction is the refunded amount (D-22j), or the item value when the item came back unpaid (D-22k).
  - shipping reversed on cancel and RTO (D-10a).
  - exchange price adjustment by value difference (D-01a).
  - GST kept as memo rows.
- [x] **Costs:**
  - product_cogs, plus a credit for every unit that came back (D-03a).
  - fulfilment from gold's posted amounts: no packaging or shipping on pre-dispatch cancels (D-05); full packaging once shipped (D-05a); gateway fee kept (D-05b); reverse shipping wherever gold posted it (D-11d).
  - exchange family: replacement COGS and its credit, inventory recovery, exchange shipping and handling (D-01).
  - free-order costs counted (D-08c).
- [x] **Gaps and back-fill:**
  - legacy RTO reverse shipping back-filled from the line rate (₹2.75L, D-11c);
  - legacy COD handling, checkout fee and weight discrepancy left unrecorded (`not_recorded`, D-11a);
  - Unknown orders booked as cancelled (D-22h);
  - a failed exchange becomes a customer return (D-01d).
- [x] `serve.fct_payment`: payment_class is cash, exchange_credit or void (D-01b); exchange credits total ₹12.4L.
- [x] `v3_contracts.py` 27/27 live: ledger grain, one row per component, deductions ≤ gross, credit ≤ cost, no revenue on non-sale orders, no test rows, cost signs, item-level rule, payment checks.
- [x] `v3_reconcile_gold.py`. It now **can fail**: every line delta must match a named decision rule, and costs are bridged transform by transform from gold columns. Results are in `serve/evaluations/results/v3_reconcile_20261014.json`.
  - LINE: 0 mismatches vs gold after decision transforms.
  - ORDER (FIN-01): incl-GST matches to the paisa; the ex-GST difference vs gold's header is exactly the shipping GST.
  - PRODUCT: matches.
  - NET-SALES BRIDGE and COST BRIDGE: 0 unexplained ₹ and 0 unexplained lines, all history and Jul / Aug / Sep 2026.
  - Live runs use a snapshot cut: orders gold re-loaded after the last v3 refresh are reported as pending and excluded (247 in the final run).
- [x] Decision register `DECISIONS.yaml` (61 decisions) + `check_decisions_v3.py`: OK.
- [ ] **Open for Phase 7 sign-off:** v2's cost total (`order_pnl_daily.net_cogs` ₹1.619 Cr all history) is ₹17–18L below gold's posted line costs. This is v2's own superseded composition (e.g. which credits / fees it nets) and is not decomposed by cause. The gold → v3 cost leg is fully proven.
- [x] Event-date vs cohort cross-period bridge and the monthly close / `as_of` → Phase 4 (below).
- [ ] Channel / campaign / ad roll-ups → Phases 3–4.

### What changes for the business (brand 20, ex-GST)

| | v2 today | v3 | Δ |
|---|---|---|---|
| Net sales, all history | 3,53,93,640 | 3,49,77,590 | −4,16,050 |
| Total variable cost, all history | 1,61,94,074 | 1,74,67,633 | +12,73,559 |
| Contribution before ads, all history | 1,91,99,566 | 1,75,09,957 | −16,89,609 |
| Net sales, Sep 2026 (order date) | 16,97,336 | 15,81,406 | −1,15,930 |

**Net-sales bridge**, all history (live, 247 pending orders excluded):

| Cause | ₹ |
|---|---|
| D-01 exchanged item is not a return | +6,82,905 |
| D-01 replacement not a sale | −7,62,090 |
| D-22i order outcome when no line has item evidence | −1,50,940 |
| D-22k item came back unpaid = item value | −1,35,984 |
| D-25 deduction basis: v2 header ratio → gold line | +81,088 |
| D-22e RTO-tagged active orders | −64,380 |
| D-01c in-order swap out of gross | −56,312 |
| D-02b/D-10/D-25 gross basis (tax split, FIN-01 proven) | +52,229 |
| D-01a exchange price adjustment | +36,048 |
| D-10a shipping reversed | −23,932 |
| D-20b replacement returned for cash | −13,298 |
| D-08 zero-value + test | +8,002 |
| D-22h / D-22g / tax-exclusive #1001 | −5,154 |
| **unexplained** | **0** |

**Cost bridge** gold-raw → v3, all history:

| Cause | ₹ |
|---|---|
| D-03a full product credit | +6,62,569 |
| D-01 exchange inventory recovery | +2,62,398 |
| D-05 no packaging / shipping on pre-dispatch cancel | +1,19,773 |
| D-11c RTO reverse shipping back-filled | −2,74,968 |
| D-01 replacement cost credit | −48,032 |
| D-05a full packaging | −41,060 |
| other D-01 | +3,025 |
| **unexplained** | **0** |

## Phase 3 — Ads, web, attribution · DONE · applied to production · contracts 39/39

- [x] **`serve.fct_ad_delivery`** (daily) at each platform's native grain:
  - Meta: ad × day.
  - Google: campaign × ad group × ad × device × network × day. PMax stops at campaign: the synthetic `pmax`, `pmax_*` and `camp_*` ids are NULL (D-14).
  - Measures native to one platform are NULL on the other. No platform conversions (D-13).
  - Meta's LPV is kept only as the hidden twin `platform_reported_landing_page_views` (D-13r). `reach_native` is per ad-day only, never summed.
- [x] **`serve.fct_ad_delivery_hourly`**: Meta ad × hour; Google campaign × hour × device × network. Device and network are in the key, which fixes v2's 304,008 duplicate rows.
- [x] **Spend = gold exactly:** spend, impressions and clicks per brand × platform × day (contract ADS-SPEND-GOLD).
- [x] **`serve.fct_web_session`**: website LPV = sessions with ≥ 1 page view (D-17, D-00).
  - Paid sessions without ids roll up to explicit `campaign unknown` / `ad unknown` members (C5).
  - No raw identity columns (D-16a). `website_purchase_value` is a tracking-quality metric only (D-09).
  - **`serve.fct_web_event`** carries the v2 event capability.
- [x] **`semantic.bridge_order_attribution`** + serve view: order × model × touch.
  - Models: `last_non_direct` (default), `last_touch`, `last_touch_journey`, `first_touch`, `linear`.
  - Default-model fallback to last-touch, flagged (D-06a). Campaign fill from same-platform last-touch, flagged (D-06b). Other models give `no_model_data` rows instead of mixing models.
  - Zero-value orders that root an exchange keep their attribution (D-01).
- [x] **FIN-07 (default model):** Σ credit × net sales over all members = brand net sales, every brand × cohort month. Sep 2026: ₹15,74,661 = ₹15,74,661.
- [x] **Spot checks, Sep 2026, brand 20:**
  - Meta website LPV 29,979 vs Meta-reported 21,067.
  - Google drill to ad group works for non-PMax campaigns.
  - Hourly = daily on overlapping days, except 3 days of partial data (₹164).
- [ ] **Hourly coverage:** Meta hourly exists only from 2026-04-01; Google hourly from 2024-11-15. Older hours are not in gold, so hourly answers before then are unavailable, never zero.
- [ ] Meta breakdowns (`meta_ads_breakdown_daily`), ad change log and dims (`dim_campaign` / `adset` / `ad`) are reused unchanged from v2. Cube v3 wires them in Phase 5.

## Phase 4 — Marts · DONE · applied to production · contracts 57/57
- [x] **`semantic.mart_profit_fact`** (refresh 5 min, depends on the ledger and bridge MVs): one row-level fact with `cohort_date` and `event_date`.
  - Order rows: the component ledger joined, on the root sale, to the default attribution model (`last_non_direct`, credit 1) and the root order's dims (`finance_channel`, `sub_channel`, `is_paid`, `is_new_customer`, `shipping_region`, `payment_bucket`).
  - Spend rows: `fct_ad_delivery` rolled to platform × campaign × ad set × ad × day (D-07: delivery date, no allocation), with v2's `sub_channel` spend split (Meta placement share, Google campaign type).
  - Order counts (orders, new customers, Unknown, cancelled / RTO / returned) are rows of their own. Zero-value orders without attribution are `unattributed`.
  - `attribution_member` on every row: ad · ad_unknown · ad_not_applicable (PMax, D-14) · campaign_unknown · no_paid_platform.
- [x] **`serve.mart_period_profit`** (default, order-date cohort) and **`serve.mart_pnl_event`** (D-21): thin views; they derive net sales, taxes, product / fulfilment / exchange cost, `contribution_before_ads`, `ad_spend` and `contribution_after_ads` (= what the `net_profit` ids resolve to; opex excluded, D-18r) and carry `is_maturing` (D-12a) and `close_status`.
- [x] **`serve.mart_product_ad_allocated`** (explicit family, D-19o): campaign-day delivery (spend, impressions, clicks, link clicks, thruplays, 3 s / 100 % video views) spread over the credited sale lines by credit × line gross sales (D-27); landing page views from **website sessions** (D-27b); one `Unallocated` row per campaign-day with no credited line; `allocation_rule_version = d27_same_day_credit_v1`.
  - Sep 2026, brand 20: ₹7,85,606 allocated + ₹4,07,716 unallocated = ₹11,93,322 (= gold). Zero-sales lines (free gifts) carry no weight.
- [x] **Monthly close (D-12, D-12b):** append-only `semantic.snap_profit_monthly`, locked views `serve.mart_period_profit_closed` / `mart_pnl_event_closed`, and `serve/semantic_v3/close_month.py`.
  - The script locks a month's rows on both axes at the moment it runs. It refuses a month that has not ended, a month already closed, and a stale or running refresh. Dry run tested on Sep 2026 and Oct 2026.
  - Live views label each month `open` (current month), `restatable` (has a snapshot) or `reconstructed` (no snapshot, rebuilt from today's state, D-12b).
- [x] **`apply_v3.py`** accepts plain append-only tables (marker `-- v3:append-only`, one CREATE TABLE, no MV); the destructive-statement check is unchanged.
- [x] **Contracts 57/57 live** (13 mart, 4 allocated, 4 close). They can fail: the cross-period bridge contract was perturbed and caught.
  - MART-CONTRIB-LEDGER / MART-NETSALES-LEDGER: mart = ledger, per brand × cohort month.
  - MART-SPEND-GOLD: spend = gold Meta + Google, per brand × platform × day, to the paisa.
  - MART-MEMBERS, MART-AD-KEYS, MART-ORDERS, MART-OUTCOME-ORDERS, MART-EVENT-COHORT-ALL.
  - **MART-EVENT-BRIDGE:** event − cohort per brand × month = the cross-period bridge computed from the ledger, 0 unexplained ₹.
  - ALLOC-SUM / LPV / SHARE / GRAIN; CLOSE-ONCE / SCOPE / STATUS / VIEW-SCOPE.
- [x] **Reconciliation to v2 `pnl_*`** (`v3_reconcile_gold.py`, EVENT BRIDGE): all history v2 event = v2 cohort and v3 event = v3 cohort (so the proven cohort bridge carries over); ad spend = v2 every month; v3 product cost has no event timing (D-21a).
  - Sep 2026 `pnl_net_profit`: v2 −15,27,829.44 → v3 −15,52,748.81 (Δ −24,919) = cohort leg −1,17,397 + event-dating leg +92,478.
  - The cohort leg is bridged with causes (Phase 2). The event-dating leg is not itemised by cause (**L-24**, open): Jul −1.50L, Aug +0.48L, Sep +0.92L, nets to 0 over all history.
- [x] **Owner rulings:** D-27 (same-day default-model credit), D-27b (website LPV), D-12b (reconstructed and flagged), D-21a (restock credit on the order date in the event family; Sep event-date product cost moved by ₹3,60,032 before this ruling).
- [x] Nothing existing changed: only the new `semantic.*` / `serve.*` objects have new DDL; v2 objects untouched.
- [ ] **No month is locked yet** and there is no schedule (L-27). The owner chooses the first close (Sep 2026 is `maturing` until mid-Nov, D-12a) and approves the cron / Mage schedule.
- [ ] Default model only in the marts (L-25); the event-dating leg (L-24) goes to the Phase 7 variance report.
- [ ] Cube v3 wiring of these views → Phase 5.

## Phase 5 — Cube v3 · NOT STARTED
- [ ] `infra/cube/model_v3`; `cube-v3` service on :4003 (dev mode off); pre-aggregations; move the inline SQL (`ltv_cac_daily`, `funnel_channel`) into serve.
- [ ] Deliver the F0-1 / F0-2 capabilities. Brand 28 profit / ROAS / AOV must be refused.

## Phase 6 — Catalogue v3 + MCP · NOT STARTED
## Phase 7 — Certification (no-loss gate, variance sign-off) · NOT STARTED
## Phase 8 — Cutover · NOT STARTED
## Phase 9 — Cleanup · NOT STARTED

---

## How decisions are stored, and how to change one

- **`doc/semantic_v3/DECISIONS.yaml`** is the machine-readable register, with one entry per decision (58). Each entry has:
  - `ruling`, `options`, `evidence` (₹);
  - `status` (implemented / planned / scope / process / superseded) and `phase`;
  - `implemented_in` (file + rule), `evidence_codes` (the `outcome_evidence` values the data carries), `tests` (contracts / reconciliation checks that guard it), `change_how`.
- **Spec §0B** holds the same decisions in business wording. Every row has a register entry.
- **The code cites the decision id** at each rule, so `grep D-22i` finds every place it lives.
- **Every data row carries its evidence code** (`outcome_evidence`, `line_outcome_evidence`, `cost_method`), so a number traces back to its decision.
- **`scripts/check_decisions_v3.py --mage /home/tervigon/work/mage_v3`** fails if spec, register and code drift apart: a decision with no entry, a cited file that no longer mentions its id, code citing an unknown id, or an evidence code with no decision.
- **Known limits** live in `doc/semantic_v3/KNOWN_LIMITS.yaml` (limit, impact, what it affects, handled_by decisions, status, next step).
- **Readable copies are generated:** `scripts/render_semantic_v3_docs.py` writes `DECISIONS.md`, `KNOWN_LIMITS.md` and the "All decisions taken" / "Known limits" sections of the plan (`plan/SEMANTIC_V3_REBUILD_PLAN.md` = `doc/semantic_v3/PLAN.md`). Edit the YAML, then re-render; the checker fails on stale copies.

To change a decision:
1. Find it in `DECISIONS.yaml` and open the files in `implemented_in`; the rule is marked with its id.
2. Edit the rule, update the register entry and the spec §0B row (date + new ruling), and run `check_decisions_v3.py`.
3. Preview: `apply_v3.py --check`, `v3_contracts.py --preview --inline-all`, `v3_reconcile_gold.py --preview`. The bridge shows the ₹ moved by the change under the decision's own cause.
4. Owner OK → `apply_v3.py --apply <objects>` → live contracts + reconciliation → note it in PROGRESS.

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
| D-22h | Unknown orders are booked as cancelled in the ledger (outcome stays visible) |
| D-10a | Shipping revenue reversed on cancelled and RTO orders; kept on returns and exchanges |
| D-01a | Exchange price adjustment = replacement value − exchanged item value (ex-GST, root cohort) |
| D-01b | Exchange credits = manual, store credit, partially_paid, blank gateways on replacements |
| D-11a | No extra data: legacy COD / checkout / weight costs stay unrecorded; rates present in data are used (D-11) |
| D-22i | Deductions are item level; the order outcome applies to all lines only when no line has item evidence |
| D-22j | A deduction is the refunded amount (gold's line deduction) |
| D-22k | Item came back with nothing refunded (unpaid COD RTO) → deduct the item value |
| D-11c / D-11d | RTO reverse shipping back-filled from the line rate; reverse shipping kept wherever gold posted it |
| D-01c / D-01d | In-order swaps leave gross; a failed exchange becomes a return |
| D-06a | Default model: orders without last_non_direct credit fall back to last-touch (flagged); other models show no_model_data |
| D-06b | Session without campaign id: fill from the same-platform last-touch (flagged) |
| D-05a / D-05b / D-08c | Full packaging once shipped; gateway fee kept on cancels; free-order costs counted |

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
| F2-1 | Gold's shipping_cost is the full placed amount even on unshipped cancellations (refunded_shipping_cost not netted); packaging and gateway are netted | v3 uses placed amounts and outcome rules (D-05) |
| F2-2 | Gold's order header ex-GST treats shipping as tax-free (₹23.2k vs composite-supply GST) | explained, D-25 |
| F2-3 | v2 gross is derived from the order header with an approximate tax ratio; v3 uses line GST, so incl-GST ties to the paisa | bridge cause "gross basis" |
| F2-4 | Legacy RTO lines have no reverse-shipping posting (1,861 lines) although the RTO rate is on the line | back-filled ₹2.75L (D-11) |
| F2-5 | Order #1001 was priced tax-exclusive (header gross = subtotal + tax) | ₹72, listed in the bridge |
| F2-6 | 12 gold payments belong to orders missing from gold.fct_orders | upstream orphans, not in fct_payment |
| F2-7 | Exchange credits ₹12.4L were counted as payments in v2 `payment_amount` | separated (D-01b) |
| F2-8 | Product cost missing on 247 brand-20 lines (0.8 %) | shown as `missing`, never zero |
| F2-9 | Gold re-loads orders continuously (CDC); a live check can catch gold newer than the 5-min v3 refresh | live checks use a snapshot cut and report pending orders |
| F2-10 | 97 returned lines (₹1.51L, mostly unpaid COD RTO) carry no gold deduction or refund at all | D-22k |
| F2-11 | 116 returned lines (₹1.43L) have return / refund evidence only at order level | D-22i (order outcome applies) — **owner re-confirmed 2026-10-14: deduct** |
| F2-12 | v2 net_cogs is ₹17–18L below gold's posted line costs (v2 composition) | open, Phase 7 variance sign-off |
| F3-1 | Multi-touch credit exists only from 2026-06-01 (journey tracking); ~8 % of later orders lack it too | D-06a fallback, coverage shown |
| F3-2 | Google sessions often lack campaign (29 %) and ad (88 %) ids; credited Google touches 45 % without campaign | D-06b fill + `campaign unknown` |
| F3-3 | Google synthetic ids: `pmax` ad, `pmax_*` and `camp_*` ad groups (camp_ = zero-spend placeholders) | NULL in v3 (D-14) |
| F3-4 | Meta hourly data only from 2026-04-01 | hourly answers unavailable before then |
| F3-5 | One free order was exchanged into a paid item (₹2,117.80 price difference) | root keeps attribution (D-01) |

## Still Unknown after the rulings (brand 20 sale orders)

68 orders, ₹96k:
- 52 cancelled after delivery
- 11 returns with RTO evidence that were delivered
- 5 returns with no evidence

## Open questions for the owner

| # | Question | Needed by |
|---|---|---|
| Q2 | Allocated-family period rule → D-27 same day, default-model credit; LPV → D-27b website sessions | Phase 4 · answered |
| Q3 | Monthly-close history before the first snapshot → D-12b reconstructed and flagged | Phase 4 · answered |
| Q6 | Stop the Cube v1 container? | Phase 9 |
| Q7 | Any unexplained ₹ in reconciliation | Phases 2–4, 7 |
| Q8 | query_log retention: longer TTL or two 7-day checks | Phase 9 |
| Q11 | Brand 28 value → refusal change | Phase 7 |

