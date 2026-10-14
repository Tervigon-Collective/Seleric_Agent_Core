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

## Status (see doc/semantic_v3/PROGRESS.md and CHECKLIST.md)
Phases 0–3 done and applied to production (2026-10-14); Phase 4 next.

## All decisions taken (planning + interviews during the build)
<!-- BEGIN GENERATED: decisions -->
63 decisions (53 implemented). Source: `doc/semantic_v3/DECISIONS.yaml` (where each lives, evidence codes, tests, how to change); business wording: spec §0B; check: `scripts/check_decisions_v3.py`.

#### Orders, outcomes and customers

| ID | Decision | Ruling | Status | Lives in | Evidence / ₹ |
|---|---|---|---|---|---|
| D-01 | Exchange treatment | Continuation + separate exchange lifecycle. Original order is the only commercial sale (keeps order date, customer, attribution). Replacement = linked fulfilment child: no order count, no sales revenue. Price-difference charges/refunds adjust the original… | implemented (P1) | `01_core_order_class.sql`, `03_ledger_order_component.sql`, `fct_order.sql` | F-07–F-10: 498 replacement orders, ₹8.39L counted as sales; ~390 originals counted as returns; ₹7.14L non-cash "refunds" |
| D-01d | Exchange whose replacement was cancelled / RTO (customer never got the swap) | original becomes a customer return when all its replacements failed (6 originals; others have a delivered replacement). Code `exchange_failed_replacement`. | implemented (P2) | `01_core_order_class.sql` | 17 replacements, ₹37.7k exchanged value |
| D-02 | Revenue recognition basis | owner: "all Actives means actually delivered" → net sales = delivered. D-02a DECIDED 2026-10-14: an ACTIVE line (not cancelled / RTO / returned) is treated as delivered; missing delivery flags are considered incomplete tracking. Net sales = ACTIVE lines as… | implemented (P1) | `01_core_order_class.sql` | F-14: ₹22.2L ACTIVE not yet delivered; of which ₹21.5L (1,226 lines, 90% COD) are >45 days old and still lack delivery evidence |
| D-02a | Revenue recognition basis | see D-02 (sub-decision recorded in that row) | implemented (P1) | `01_core_order_class.sql` | F-14: ₹22.2L ACTIVE not yet delivered; of which ₹21.5L (1,226 lines, 90% COD) are >45 days old and still lack delivery evidence |
| D-05 | Cancel vs RTO vs return as separate concepts | Three concepts. *Cancelled* (before dispatch: no fulfilment cost), *RTO* (shipped, not delivered: forward + reverse shipping, no revenue, stock recovered), *Customer return* (delivered then returned: revenue reversed, reverse shipping, restock). Each has its… | implemented (P2) | `03_ledger_order_component.sql` | F-11: ≥643 shipped "cancellations" |
| D-08 | Draft / POS / test / revenue-adjustment orders | count as sales orders — draft orders (293), POS orders (113), revenue-adjustment orders (45). Excluded — zero-value/free orders (gross after discount ≤ ₹1). Test orders: see D-08a. | implemented (P1) | `01_core_order_class.sql` | F-06 |
| D-08a | Test orders | excluded everywhere. | implemented (P1) | `01_core_order_class.sql`, `03_ledger_order_component.sql`, `fct_payment.sql` | F-05 |
| D-08b | COD order cancelled and re-placed as prepaid (C2P) | cancelled sale (today's behaviour); the pair is linked (`superseded_by_order_id`). Code `cancel_cod_to_prepaid`. | implemented (P1) | `01_core_order_class.sql` | 11 pairs, ₹19.5k, since 2026-08-29 (tags logisy/pragma_c2p_cancelled, attribution supersedes_order_id) |
| D-15 | New-customer definition | first placed order (regardless of outcome). | implemented (P1) | `01_core_order_class.sql` | — |
| D-16 | Guest identity | guest checkouts matched to customers by phone/email for repeat/LTV. Feasible: `gold.dim_customers` has `phone`, `phone_hash`, `email_hash`. *Proposed (not yet decided): use hashed keys in serve, no raw PII to the Agent.* | implemented (P1) | `01_core_order_class.sql` | — |
| D-16a | Identity key in serve | hashed person key (phone_hash, else email_hash, else customer id; customers sharing an email hash merged); no raw phone / email / name / street address in serve, Cube or the Agent. | implemented (P1) | `01_core_order_class.sql`, `fct_order.sql` | 7 phone + 5 email groups share identities across 41,180 customer ids; 40 guest orders carry no contact data |
| D-20 | Exchange edge cases | Evidence-based. (a) Exchange-tagged with no replacement: money refund exists → customer return; otherwise `exchange_in_progress` (not a return). (b) Replacement later returned/refunded in cash → customer return of the root sale's line on the root order date. | implemented (P1) | `01_core_order_class.sql` | F-07–F-10 (≈54 exchange-tagged RETURN originals have no replacement order) |
| D-22 | RTO / return / cancel evidence rule | four outcomes — Not shipped + cancelled → *Cancellation*; shipped + confirmed RTO → *RTO*; delivered + confirmed return → *Customer Return*; conflicting or insufficient evidence → *Unknown*. Never assume every shipped cancellation is RTO or every delivered… | implemented (P1) | `01_core_order_class.sql`, `02_core_order_line_class.sql` | Ambiguous: 256 shipped no-status cancels, 898 shipped no-status returns, 93+84 shipped non-delivered, 38 delivered cancels |
| D-22a | Bare `RTO` tag on unshipped cancellations | real RTO — shipment record lost on cancellation. Evidence code `cancel_rto_tag_shipment_record_lost`. | implemented (P1) | `01_core_order_class.sql` | 949 orders, ₹18.9L ex-GST, Apr–Nov 2025, 97% COD; unfulfilled, no tracking; 93% of 2025-Q3 cancellations carry the tag |
| D-22b | RETURN with merchant-created return record, no money refund, no delivery status, no RTO evidence | RTO. Code `return_record_no_money_no_delivery`. | implemented (P1) | `01_core_order_class.sql` | 707 orders, ₹13.8L, Jan–Jun 2026 (no RTO tagging existed then), 94% COD, return reason `unknown` |
| D-22c | Shipped cancellation, never delivered, no RTO evidence | RTO. Code `cancel_shipped_never_delivered`. | implemented (P1) | `01_core_order_class.sql` | 346 orders, ₹7.0L, mostly Oct–Dec 2025, 98% COD, fulfilled with tracking |
| D-22d | RETURN with money refunded but no delivery status | customer return (all 256). Codes `return_request_money_refund`, `return_money_refund_only`. | implemented (P1) | `01_core_order_class.sql` | 256 orders, ₹5.8L (125 with return request, 131 without) |
| D-22e | ACTIVE order with RTO evidence (Logisy rto_in_transit / rto_delivered tags, courier `failure`, or the 2025 bare `RTO` tag) | RTO — tag is newer evidence than the Shopify status; overrides D-02a for these orders. Code `active_with_rto_evidence`. | implemented (P1) | `01_core_order_class.sql` | 46 orders, ₹88k (24 Logisy/courier, 22 bare tag) |
| D-22f | Line refunded with no return (ACTIVE / ADJUSTMENT orders) | cancelled. Line code `line_refund_without_return`; an order whose every line is refunded with no return is `cancelled` (`refund_without_return_all_lines`). | implemented (P1) | `01_core_order_class.sql`, `02_core_order_line_class.sql` | 76 lines (66 on sale orders, ₹1.1L ex-GST); on 52 orders every line is refunded with no return |
| D-22g | Returned quantity with no refund on an ACTIVE order | returned. Line code `line_return_qty_without_refund`. | implemented (P1) | `02_core_order_line_class.sql` | 2 sale lines (₹3.2k) |
| D-22h | Ledger treatment of Unknown-outcome orders | treat as cancelled in the ledger (sales deducted, product cost credited, no fulfilment cost, shipping revenue reversed); `outcome='unknown'` stays visible on every row. | implemented (P2) | `03_ledger_order_component.sql` | 68 sale orders, ₹96k |
| D-22i | Which lines a cancel / RTO / return deducts | item level for everything — only lines with item evidence (returned / refunded / exchanged quantity, or gold line cancelled / returned revenue) are deducted; other lines of the order stay sold. Clarified: when NO line of the order has item evidence, the… | implemented (P2) | `02_core_order_line_class.sql` | RTO orders: 132 lines ₹1.99L had no item evidence; returned orders with no line evidence: 116 lines ₹1.43L |

#### Revenue, GST and deductions

| ID | Decision | Ruling | Status | Lives in | Evidence / ₹ |
|---|---|---|---|---|---|
| D-02b | GST | ex-GST for all revenue, margin and ROAS metrics; GST (CGST/SGST/IGST) is its own component; tax-inclusive GMV only as a separately labelled metric. | implemented (P2) | `03_ledger_order_component.sql` | F-13 |
| D-09 | Ad revenue measure | primary first-party ad revenue = attributed gross sale (Shopify, at placement) → *Gross ROAS*; *Net ROAS* also provided (attributed revenue net of cancels/RTO/returns as of cutoff). Website purchase-event value is a tracking-quality metric only. D-09a… | implemented (P3) | `fct_web_event.sql`, `fct_web_session.sql` | F-25 |
| D-09a | Ad revenue measure | see D-09 (sub-decision recorded in that row) | implemented (P2) | `03_ledger_order_component.sql` | F-25 |
| D-10 | Shipping charged to customer | Order-level revenue. Shipping charged is an explicit order-level net-sales component (ex-GST), not spread to lines; product-level sales exclude it; order/brand totals include it. | implemented (P2) | `03_ledger_order_component.sql` | F-13: ₹1.52L |
| D-10a | Shipping revenue on undelivered / returned orders | reversed for cancelled and RTO orders (and Unknown, D-22h); returned and exchanged orders keep it. | implemented (P2) | `03_ledger_order_component.sql` | shipping charged incl. GST: cancelled ₹4.5k, RTO ₹23.1k, returned ₹1.8k, exchanged ₹5.3k |
| D-22j | How much a cancel / RTO / return deducts | The refunded amount: the deduction is gold's line cancelled / returned revenue (ex-GST) when present. | implemented (P2) | `03_ledger_order_component.sql` | no line had a partial refund below item value on 2026-10-14 (₹0 effect today) |
| D-22k | Item came back but nothing was refunded (unpaid COD RTO) | Deduct the item value (nothing paid + item returned = no sale). | implemented (P2) | `03_ledger_order_component.sql` | 97 lines, ₹1.51L |
| D-25 | Committed-but-unapplied `semantic.fct_line_pnl` (mage-ai `f645091`) | superseded — never applied; its line-GST and shipping-tax SQL reused in the new ledger; file removed in cleanup. | implemented (P2) | `03_ledger_order_component.sql` | not live in ClickHouse |
| D-20b | Exchange edge cases | see D-20 (sub-decision recorded in that row) | implemented (P2) | `03_ledger_order_component.sql` | F-07–F-10 (≈54 exchange-tagged RETURN originals have no replacement order) |
| D-01a | Exchange price-adjustment basis | value difference — replacement value − exchanged item value, ex-GST, on the root order cohort; 0 when the replacement is cancelled / RTO. In-order exchanges (F-10): − exchanged-out line value. | implemented (P2) | `03_ledger_order_component.sql` | Pragma tags check: lower −₹4.1k, higher +₹8.2k, same ≈ 0 (₹134 mean noise) |
| D-01b | Which payments are non-cash exchange credits | on exchange replacements, gateways manual, shopify_store_credit, partially_paid and blank / 'other' are exchange credits (refunds on exchanged originals through them too); COD and named card / UPI gateways are cash. `serve.fct_payment.payment_class`. | implemented (P2) | `fct_payment.sql` | replacements: manual ₹7.6L + ₹1.6L pending, blank/'other' ₹2.36L, partially_paid ₹42k, COD ₹26k |
| D-01c | In-order exchange (item swapped inside the same order) in gross sales | remove from gross — gross = what the customer finally kept; memo row `in_order_exchange_swapped_out` keeps the placed value traceable. | implemented (P2) | `02_core_order_line_class.sql`, `03_ledger_order_component.sql` | 32 lines, ₹79k |

#### Costs

| ID | Decision | Ruling | Status | Lives in | Evidence / ₹ |
|---|---|---|---|---|---|
| D-03 | COGS definition and return recovery | Split layers. COGS = product (landed purchase) cost only. Packaging, forward/reverse shipping, gateway, COD handling, RTO, checkout fee, weight discrepancy = separate *fulfilment & payment cost* components. Gross profit = net sales − product COGS;… | implemented (P2) | `03_ledger_order_component.sql` | F-15, F-20 |
| D-03a | Returned/RTO stock cost recovery | Always credit back product COGS for every returned or RTO unit (and exchanged-back unit); reverse shipping/handling remain costs. | implemented (P2) | `03_ledger_order_component.sql` | F-20 |
| D-05a | Packaging on shipped orders when gold refunds it | charge full packaging once shipped. | implemented (P2) | `03_ledger_order_component.sql` | ₹41k refunded packaging on shipped lines |
| D-05b | Gateway fee on pre-dispatch cancellations | keep as cost (the gateway charged it). | implemented (P2) | `03_ledger_order_component.sql` | ₹30k |
| D-08c | Costs of zero-value / free orders | count the costs in contribution (no revenue, D-08). | implemented (P2) | `03_ledger_order_component.sql` | 18 orders, ₹15.8k |
| D-11 | COGS era break 2026-09 | Back-fill with rates. Legacy era recomputed with the same component set using the rate cards/rules effective at the time; values marked `cost_method='estimated_backfill'`. | implemented (P2) | `03_ledger_order_component.sql` | F-16 |
| D-11a | Back-fill rates not present in data | leave it — no extra data. Legacy COD handling, checkout fee and weight discrepancy stay as gold has them (absent) with `cost_method='not_recorded'`; never estimated. Rates that *are* in the data (packaging, shipping, gateway, RTO per unit) are used per D-11… | implemented (P2) | `03_ledger_order_component.sql` | F-16: legacy rows have RTO rate (variant cost history) but no COD-handling, checkout-fee or weight-discrepancy rates before 2026-09 |
| D-11c | Legacy RTO reverse-shipping back-fill | add from the line rate (`estimated_backfill`). | implemented (P2) | `03_ledger_order_component.sql` | 1,861 legacy RTO lines with no posting, rate on the line, ₹2.75L |
| D-11d | Reverse shipping posted by gold on kept / pre-dispatch cancelled lines | Keep the cost wherever gold posted it. | implemented (P2) | `03_ledger_order_component.sql` | ₹20k |

#### Ads, website and attribution

| ID | Decision | Ruling | Status | Lives in | Evidence / ₹ |
|---|---|---|---|---|---|
| D-00 | Source of truth for ad-facing LPV and revenue | DECIDED (2026-10-14, owner instruction) — platform-reported LPV/revenue must not be used as the ad metric | implemented (P3) | `04_bridge_order_attribution.sql`, `fct_web_session.sql` | F-25, F-26 |
| D-06 | Default attribution model for campaign/ad revenue | `last_non_direct` is the default; model must be a parameter so the default can be changed later; other models selectable explicitly and never mixed in one total. | implemented (P3) | `04_bridge_order_attribution.sql` | F-29 coverage |
| D-06a | Default-model attribution for orders without last_non_direct credit | Fall back to gold last-touch, rows flagged model_used = last_touch_fallback; other models show an explicit no_model_data row (never mixed). | implemented (P3) | `04_bridge_order_attribution.sql` | 16,484 pre-June sale orders ₹2.87 Cr + 811 since ₹17.5L |
| D-06b | Credited session without a campaign id | Fill from the order's same-platform last-touch (campaign_source = last_touch_fill); otherwise campaign unknown. | implemented (P3) | `04_bridge_order_attribution.sql` | 792 Google ₹17.8L + 279 Meta ₹5.6L; 303 / 25 fillable |
| D-13 | Platform-reported conversions (Meta purchases/value/LPV, Google conversions/value) | Hide completely — not exposed in `serve`, Cube or to the Agent. Relaxed by D-13r for LPV only: hidden-by-default diagnostic twin `platform_reported_landing_page_views` is kept. | implemented (P3) | `fct_ad_delivery.sql`, `fct_ad_delivery_hourly.sql` | F-25, F-26 |
| D-13r | LPV ids | `landing_page_views` / `cost_per_landing_page_view` rebound to website LPV (D-17), major version bump; hidden-by-default `platform_reported_landing_page_views` kept for diagnostics. All other platform conversions stay hidden. | implemented (P3) | `fct_ad_delivery.sql`, `fct_ad_delivery_hourly.sql` | F-26 |
| D-14 | Google hierarchy | Native per platform — account → campaign type → campaign → ad group → ad (non-PMax) + device and network as Google-only breakdowns; PMax stops at campaign (+device/network). Cross-platform overview uses only common levels. | implemented (P3) | `04_bridge_order_attribution.sql`, `fct_ad_delivery.sql`, `fct_ad_delivery_hourly.sql`, `fct_web_session.sql` | F-23, F-24 |
| D-17 | Website LPV definition | LPV = website sessions attributed to the platform/campaign/ad (UTM/click-id) with ≥1 tracked page view. Sessions without `ad_id`/`campaign_id` roll up as explicit `ad unknown` / `campaign unknown` members (Meta 19% / Google 88% of sessions lack ad_id). | implemented (P3) | `fct_web_session.sql` | F-27, F-28 |

#### Profit, periods and product ads

| ID | Decision | Ruling | Status | Lives in | Evidence / ₹ |
|---|---|---|---|---|---|
| D-07 | Ad cost allocation to orders/products | Period match, no allocation. Period profit = contribution of orders placed in the period − ad spend delivered in the period. Campaign drill = attributed contribution (default model D-06) − that campaign's spend; *Unattributed/organic* contribution and *spend… | planned (P4) |  | §6.5 |
| D-12 | As-of / closing cutoff | Live + monthly close. Default = latest state; locked month-end snapshots make closed months reproducible; recent cohorts flagged `maturing` for a defined window (D-12a). | planned (P4) |  | §4 |
| D-12a | Maturity window | 45 days after order date a cohort is `maturing`. | implemented (P1) | `fct_order.sql`, `ledger_order_component.sql` | F-11, F-14 |
| D-18 | Net profit | Contribution only. Top published metric = *contribution after ads*. No metric named "net profit" until an opex source exists; Agent explains the gap. `canonical_pnl.net_profit` retired with it. Refined by D-18r: existing `net_profit` ids are kept and resolve… | planned (P4) |  | F-32 |
| D-18r | `net_profit` ids | ids kept; resolve to contribution after ads; description and Agent answer state opex is excluded. | planned (P6) |  | ids exist today |
| D-19 | Product-level ad metrics | Credit-based, no spend split. Product drill shows attributed orders/sales per campaign/ad (which products each ad sold); `product_ad_spend`, `product_gross_roas`, `product_net_roas`, `product_mer`, `product_landing_page_views` and spend allocation in… | superseded |  | §0C-C5 |
| D-19o | Product-level ad metrics vs no-capability-loss | kept as an explicit `allocated` family — allocation = attribution credit (default model) × line gross-sales share; `allocation_rule_version` on every row; Unallocated spend row so totals reconcile; never used in default/overview answers. Period rule for… | planned (P4) |  | ~25 `product_*` spend/ROAS/MER/CAC/delivery ids exist today |
| D-21 | Event-date P&L family (`pnl_*`, 25 ids) | kept as explicit family, rebuilt on the same component ledger. Order-date cohort stays the default. Reconciliation: equal over all history; per period, event − cohort = itemised cross-period bridge with 0 unexplained ₹ (not equal per month, see §4.1). | planned (P4) |  | dashboard-style "processed in period" |

#### Scope and process

| ID | Decision | Ruling | Status | Lives in | Evidence / ₹ |
|---|---|---|---|---|---|
| D-04 | Amazon in overview roll-up | Out of scope for this rebuild. Brand overview is labelled D2C (Shopify) only; Amazon remains a documented gap; Amazon gold tables are untouched (not deleted) | scope |  | F-02 |
| D-23 | Rebuild scope and consumers | `seleric_systems` web dashboard is not used — it and Cube v1 are excluded. Scope = Seleric Agent, Core MCP, Cube v3, new canonical serve. Migrate only `/opt/seleric/reports` scripts still in use. Reconcile new serve financial components to gold at line,… | process |  | consumer scan 2026-10-14 |
| D-24 | Cutover style | parallel build (new serve objects, Cube v3 on :4003, catalogue_v3), switch after the no-capability-loss gate; rollback = env flip; old objects dropped after 2 stable weeks + zero-read check. | process |  | no staging ClickHouse/Cube |
| D-26 | Live v2 defects F0-1 (4 tax metrics error) / F0-2 (customers × first-order product errors) | leave broken in v2; v3 delivers them (CAPABILITY_MAP `live_defect`). No v2 production write. | implemented (P0) | `(no code: v2 left unchanged)` | Phase 0 sweep 2026-10-14: 7 of 739 Cube v2 members fail; caused by unapplied mage-ai f645091 / 8a2b333 |
<!-- END GENERATED: decisions -->

## Known limits
<!-- BEGIN GENERATED: limits -->
23 known limits (4 open). Source: `doc/semantic_v3/KNOWN_LIMITS.yaml`.

| ID | Area | Limit | Impact | Handled by | Status | Next step |
|---|---|---|---|---|---|---|
| L-01 | Attribution | Multi-touch attribution (last_non_direct, first_touch, linear, journey last-touch) exists only from 2026-06-01; ~8 % of later orders lack it too. | 16,484 pre-June sale orders (₹2.87 Cr gross) + 811 since (₹17.5L) use the last-touch fallback under the default model; other models show no_model_data. | D-06a | accepted | rows carry model_used; Agent shows coverage % with every model answer (Phase 6) |
| L-02 | Attribution | Google website sessions often lack campaign (29 %) and ad (88 %) ids; 45 % of credited Google touches have no campaign. | 792 Google orders (₹17.8L) + 279 Meta (₹5.6L) credited to a session without campaign; 328 filled from last-touch, the rest sit in `campaign unknown`. | D-06b, D-17 | accepted | tracking fix upstream (gclid → campaign mapping) would shrink `campaign unknown` |
| L-03 | Ads | Google Performance Max (74 % of Google spend) has no ad group / ad level. | PMax drill stops at campaign (+ device / network); synthetic pmax / pmax_* / camp_* ids are NULL. | D-14 | accepted | none — platform limitation |
| L-04 | Ads | Hourly delivery exists only from 2026-04-01 for Meta and 2024-11-15 for Google; Google hourly has no ad group / ad level. | hourly answers before those dates are unavailable (not zero). | — | accepted | Cube v3 refuses hourly queries before coverage starts (Phase 5) |
| L-05 | Ads | Meta reach is only valid per ad-day; it cannot be summed across ads or days, and frequency cannot be averaged. | no brand / campaign reach totals. | — | accepted | expose reach only at native grain (reach_native) |
| L-06 | Ads | Platform-reported conversions (Meta purchases / value, Google conversions) are hidden; website purchase value is a tracking-quality metric only. | platform ROAS is not available as a business metric. | D-00, D-13, D-13r, D-09 | accepted | none |
| L-07 | Costs | COD handling, checkout fee and weight discrepancy were never recorded before 2026-09. | legacy-era variable cost is understated by those three components (shown as cost_method not_recorded, never estimated). | D-11a | accepted | none unless a historical rate card appears |
| L-08 | Costs | Legacy shipping, packaging, gateway and RTO costs are per-unit rate-card estimates, not courier / gateway invoices. | cost_method rate_card / estimated_backfill; legacy RTO reverse shipping back-filled (₹2.75L). | D-11, D-11c | accepted | invoice-based costs would be a new cost source |
| L-09 | Costs | Product cost is missing on 247 brand-20 lines (0.8 %); 410 lines since 2026-09 are provisional (one lump estimated_ops_cost). | shown as cost_status missing / provisional, never zero. | D-03 | accepted | cost data owner fills product cost; provisional rows finalise upstream |
| L-10 | Orders | 68 sale orders (₹96k) have conflicting or no outcome evidence (Unknown). | booked as cancelled in the ledger; outcome stays `unknown` and visible. | D-22, D-22h | accepted | none |
| L-11 | Orders | Delivery and RTO evidence depends on the era — bare RTO tag (Dec 2024 – Nov 2025), no tagging (Jan – Jun 2026), Logisy tags (Jul 2026 on); courier status is mostly empty on older returns. | outcomes for those eras follow owner rulings on evidence patterns, not direct proof. | D-22a, D-22b, D-22c, D-22d, D-22e | accepted | none |
| L-12 | Revenue | Gold's order header treats shipping as tax-free; v3 taxes shipping at the principal line rate (composite supply). | ex-GST view differs from gold's header by ₹23k (= shipping GST); incl-GST ties to the paisa. | D-25, D-10 | accepted | none |
| L-13 | Revenue | Exchange price adjustment is a value difference; same-value exchanges show ₹134 average noise from discounts. | small residual on same-value exchanges. | D-01a | accepted | none |
| L-14 | Revenue | No operating-expense source in the warehouse. | top figure is contribution after ads; `net_profit` ids mean contribution after ads (opex excluded). | D-18, D-18r | accepted | add an opex source to enable true net profit |
| L-15 | Scope | Amazon (marketplace + Amazon Ads) is out of scope; brand overview = D2C Shopify only. | brand 20 Amazon sales and spend are not in any total. | D-04 | accepted | separate project |
| L-16 | Scope | Brand 28 is spend-only (no commerce); brands 24–27 have 1–32 orders. | profit / ROAS / AOV must be refused for brand 28 (v2 computes −spend today, F0-3); 24–27 flagged low-volume, not certified. | — | planned | refusal in Cube v3 / catalogue v3 (Phases 5–6); owner confirms the value → refusal change in Phase 7 (Q11) |
| L-17 | Reconciliation | v2's cost total (order_pnl_daily.net_cogs) is ₹17–18L below gold's posted line costs; that v2-internal composition is not decomposed by cause. | the v2 → v3 contribution change (−₹16.9L all history) is only partly itemised (gold → v3 is fully proven). | — | open | decompose v2 cost composition for the Phase 7 variance report |
| L-18 | Freshness | v3 semantic tables refresh every 5 minutes while gold reloads continuously (CDC). | up to ~5 min lag; live checks exclude orders gold changed after the last refresh and report them as pending. | — | accepted | none |
| L-19 | Data quality | 12 gold payments belong to orders missing from gold.fct_orders. | excluded from serve.fct_payment. | — | open | upstream (dbt) investigation |
| L-20 | Web | Checkout-timing session metrics (seconds to checkout / purchase, checkout steps) are structurally empty — Shopify checkout events are server-side without a session id. | those metrics stay unavailable (data-quality waiver). | — | open | dbt fix (plumb cart journey checkout time into sessions) |
| L-21 | Operations | system.query_log keeps 7 days. | the "14 days of zero reads" rule before dropping old views cannot run as written. | — | open | owner chooses longer TTL or two 7-day checks (Q8) |
| L-22 | Operations | Live v2 defects — 4 tax metrics and customers × first-order product fail in Cube v2 today. | those Agent answers error until the v3 cutover. | D-26 | planned | delivered by v3 (Phase 5–8) |
| L-23 | Model | Event-date P&L, monthly close / as-of snapshots, allocated product-ad metrics and period profit are not built yet. | only order-cohort figures exist in v3 so far. | D-07, D-12, D-19o, D-21 | planned | Phase 4 |
<!-- END GENERATED: limits -->

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
