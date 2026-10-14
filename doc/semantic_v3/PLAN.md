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

## Decisions added in planning (written to spec §0B in Phase 0 — all now in the checklist below)
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
- [x] Phase 0 baseline · [x] Phase 1 order classification · [x] Phase 2 component ledger · [x] Phase 3 ads / web / attribution · [x] Phase 4 marts (all applied 2026-10-14)
- [ ] 5 Cube v3 (next) · [ ] 6 catalogue v3 + MCP · [ ] 7 certification · [ ] 8 cutover · [ ] 9 cleanup

## All decisions taken (planning + interviews during the build)
<!-- BEGIN GENERATED: decisions -->
67 decisions (61 implemented). `[x]` = implemented (or a scope / process rule), `[ ]` = decided, built in the named phase. Full table: `doc/semantic_v3/DECISIONS.md`; source: `doc/semantic_v3/DECISIONS.yaml`; business wording: spec §0B.

#### Orders, outcomes and customers
- [x] **D-01** Exchange treatment — Continuation + separate exchange lifecycle. Original order is the only commercial sale (keeps order date, customer, attribution). Replacement = linked fulfilment child: no order count, no sales revenue.… _(implemented, phase 1)_ · `01_core_order_class.sql`, `03_ledger_order_component.sql`, `fct_order.sql`
- [x] **D-01d** Exchange whose replacement was cancelled / RTO (customer never got the swap) — original becomes a customer return when all its replacements failed (6 originals; others have a delivered replacement). Code `exchange_failed_replacement`. _(implemented, phase 2)_ · `01_core_order_class.sql`
- [x] **D-02** Revenue recognition basis — owner: "all Actives means actually delivered" → net sales = delivered. D-02a DECIDED 2026-10-14: an ACTIVE line (not cancelled / RTO / returned) is treated as delivered; missing delivery flags are considered incomplete… _(implemented, phase 1)_ · `01_core_order_class.sql`
- [x] **D-02a** Revenue recognition basis — see D-02 (sub-decision recorded in that row) _(implemented, phase 1)_ · `01_core_order_class.sql`
- [x] **D-05** Cancel vs RTO vs return as separate concepts — Three concepts. *Cancelled* (before dispatch: no fulfilment cost), *RTO* (shipped, not delivered: forward + reverse shipping, no revenue, stock recovered), *Customer return* (delivered then returned: revenue reversed,… _(implemented, phase 2)_ · `03_ledger_order_component.sql`
- [x] **D-08** Draft / POS / test / revenue-adjustment orders — count as sales orders — draft orders (293), POS orders (113), revenue-adjustment orders (45). Excluded — zero-value/free orders (gross after discount ≤ ₹1). Test orders: see D-08a. _(implemented, phase 1)_ · `01_core_order_class.sql`
- [x] **D-08a** Test orders — excluded everywhere. _(implemented, phase 1)_ · `01_core_order_class.sql`, `03_ledger_order_component.sql`, `fct_payment.sql`
- [x] **D-08b** COD order cancelled and re-placed as prepaid (C2P) — cancelled sale (today's behaviour); the pair is linked (`superseded_by_order_id`). Code `cancel_cod_to_prepaid`. _(implemented, phase 1)_ · `01_core_order_class.sql`
- [x] **D-15** New-customer definition — first placed order (regardless of outcome). _(implemented, phase 1)_ · `01_core_order_class.sql`
- [x] **D-16** Guest identity — guest checkouts matched to customers by phone/email for repeat/LTV. Feasible: `gold.dim_customers` has `phone`, `phone_hash`, `email_hash`. *Proposed (not yet decided): use hashed keys in serve, no raw PII to the Agent.* _(implemented, phase 1)_ · `01_core_order_class.sql`
- [x] **D-16a** Identity key in serve — hashed person key (phone_hash, else email_hash, else customer id; customers sharing an email hash merged); no raw phone / email / name / street address in serve, Cube or the Agent. _(implemented, phase 1)_ · `01_core_order_class.sql`, `fct_order.sql`
- [x] **D-20** Exchange edge cases — Evidence-based. (a) Exchange-tagged with no replacement: money refund exists → customer return; otherwise `exchange_in_progress` (not a return). (b) Replacement later returned/refunded in cash → customer return of the… _(implemented, phase 1)_ · `01_core_order_class.sql`
- [x] **D-22** RTO / return / cancel evidence rule — four outcomes — Not shipped + cancelled → *Cancellation*; shipped + confirmed RTO → *RTO*; delivered + confirmed return → *Customer Return*; conflicting or insufficient evidence → *Unknown*. Never assume every shipped… _(implemented, phase 1)_ · `01_core_order_class.sql`, `02_core_order_line_class.sql`
- [x] **D-22a** Bare `RTO` tag on unshipped cancellations — real RTO — shipment record lost on cancellation. Evidence code `cancel_rto_tag_shipment_record_lost`. _(implemented, phase 1)_ · `01_core_order_class.sql`
- [x] **D-22b** RETURN with merchant-created return record, no money refund, no delivery status, no RTO evidence — RTO. Code `return_record_no_money_no_delivery`. _(implemented, phase 1)_ · `01_core_order_class.sql`
- [x] **D-22c** Shipped cancellation, never delivered, no RTO evidence — RTO. Code `cancel_shipped_never_delivered`. _(implemented, phase 1)_ · `01_core_order_class.sql`
- [x] **D-22d** RETURN with money refunded but no delivery status — customer return (all 256). Codes `return_request_money_refund`, `return_money_refund_only`. _(implemented, phase 1)_ · `01_core_order_class.sql`
- [x] **D-22e** ACTIVE order with RTO evidence (Logisy rto_in_transit / rto_delivered tags, courier `failure`, or the 2025 bare `RTO` tag) — RTO — tag is newer evidence than the Shopify status; overrides D-02a for these orders. Code `active_with_rto_evidence`. _(implemented, phase 1)_ · `01_core_order_class.sql`
- [x] **D-22f** Line refunded with no return (ACTIVE / ADJUSTMENT orders) — cancelled. Line code `line_refund_without_return`; an order whose every line is refunded with no return is `cancelled` (`refund_without_return_all_lines`). _(implemented, phase 1)_ · `01_core_order_class.sql`, `02_core_order_line_class.sql`
- [x] **D-22g** Returned quantity with no refund on an ACTIVE order — returned. Line code `line_return_qty_without_refund`. _(implemented, phase 1)_ · `02_core_order_line_class.sql`
- [x] **D-22h** Ledger treatment of Unknown-outcome orders — treat as cancelled in the ledger (sales deducted, product cost credited, no fulfilment cost, shipping revenue reversed); `outcome='unknown'` stays visible on every row. _(implemented, phase 2)_ · `03_ledger_order_component.sql`
- [x] **D-22i** Which lines a cancel / RTO / return deducts — item level for everything — only lines with item evidence (returned / refunded / exchanged quantity, or gold line cancelled / returned revenue) are deducted; other lines of the order stay sold. Clarified: when NO line… _(implemented, phase 2)_ · `02_core_order_line_class.sql`

#### Revenue, GST and deductions
- [x] **D-02b** GST — ex-GST for all revenue, margin and ROAS metrics; GST (CGST/SGST/IGST) is its own component; tax-inclusive GMV only as a separately labelled metric. _(implemented, phase 2)_ · `03_ledger_order_component.sql`
- [x] **D-09** Ad revenue measure — primary first-party ad revenue = attributed gross sale (Shopify, at placement) → *Gross ROAS*; *Net ROAS* also provided (attributed revenue net of cancels/RTO/returns as of cutoff). Website purchase-event value is a… _(implemented, phase 3)_ · `fct_web_event.sql`, `fct_web_session.sql`
- [x] **D-09a** Ad revenue measure — see D-09 (sub-decision recorded in that row) _(implemented, phase 2)_ · `03_ledger_order_component.sql`
- [x] **D-10** Shipping charged to customer — Order-level revenue. Shipping charged is an explicit order-level net-sales component (ex-GST), not spread to lines; product-level sales exclude it; order/brand totals include it. _(implemented, phase 2)_ · `03_ledger_order_component.sql`
- [x] **D-10a** Shipping revenue on undelivered / returned orders — reversed for cancelled and RTO orders (and Unknown, D-22h); returned and exchanged orders keep it. _(implemented, phase 2)_ · `03_ledger_order_component.sql`
- [x] **D-22j** How much a cancel / RTO / return deducts — The refunded amount: the deduction is gold's line cancelled / returned revenue (ex-GST) when present. _(implemented, phase 2)_ · `03_ledger_order_component.sql`
- [x] **D-22k** Item came back but nothing was refunded (unpaid COD RTO) — Deduct the item value (nothing paid + item returned = no sale). _(implemented, phase 2)_ · `03_ledger_order_component.sql`
- [x] **D-25** Committed-but-unapplied `semantic.fct_line_pnl` (mage-ai `f645091`) — superseded — never applied; its line-GST and shipping-tax SQL reused in the new ledger; file removed in cleanup. _(implemented, phase 2)_ · `03_ledger_order_component.sql`
- [x] **D-20b** Exchange edge cases — see D-20 (sub-decision recorded in that row) _(implemented, phase 2)_ · `03_ledger_order_component.sql`
- [x] **D-01a** Exchange price-adjustment basis — value difference — replacement value − exchanged item value, ex-GST, on the root order cohort; 0 when the replacement is cancelled / RTO. In-order exchanges (F-10): − exchanged-out line value. _(implemented, phase 2)_ · `03_ledger_order_component.sql`
- [x] **D-01b** Which payments are non-cash exchange credits — on exchange replacements, gateways manual, shopify_store_credit, partially_paid and blank / 'other' are exchange credits (refunds on exchanged originals through them too); COD and named card / UPI gateways are cash.… _(implemented, phase 2)_ · `fct_payment.sql`
- [x] **D-01c** In-order exchange (item swapped inside the same order) in gross sales — remove from gross — gross = what the customer finally kept; memo row `in_order_exchange_swapped_out` keeps the placed value traceable. _(implemented, phase 2)_ · `02_core_order_line_class.sql`, `03_ledger_order_component.sql`

#### Costs
- [x] **D-03** COGS definition and return recovery — Split layers. COGS = product (landed purchase) cost only. Packaging, forward/reverse shipping, gateway, COD handling, RTO, checkout fee, weight discrepancy = separate *fulfilment & payment cost* components. Gross… _(implemented, phase 2)_ · `03_ledger_order_component.sql`
- [x] **D-03a** Returned/RTO stock cost recovery — Always credit back product COGS for every returned or RTO unit (and exchanged-back unit); reverse shipping/handling remain costs. _(implemented, phase 2)_ · `03_ledger_order_component.sql`
- [x] **D-05a** Packaging on shipped orders when gold refunds it — charge full packaging once shipped. _(implemented, phase 2)_ · `03_ledger_order_component.sql`
- [x] **D-05b** Gateway fee on pre-dispatch cancellations — keep as cost (the gateway charged it). _(implemented, phase 2)_ · `03_ledger_order_component.sql`
- [x] **D-08c** Costs of zero-value / free orders — count the costs in contribution (no revenue, D-08). _(implemented, phase 2)_ · `03_ledger_order_component.sql`
- [x] **D-11** COGS era break 2026-09 — Back-fill with rates. Legacy era recomputed with the same component set using the rate cards/rules effective at the time; values marked `cost_method='estimated_backfill'`. _(implemented, phase 2)_ · `03_ledger_order_component.sql`
- [x] **D-11a** Back-fill rates not present in data — leave it — no extra data. Legacy COD handling, checkout fee and weight discrepancy stay as gold has them (absent) with `cost_method='not_recorded'`; never estimated. Rates that *are* in the data (packaging, shipping,… _(implemented, phase 2)_ · `03_ledger_order_component.sql`
- [x] **D-11c** Legacy RTO reverse-shipping back-fill — add from the line rate (`estimated_backfill`). _(implemented, phase 2)_ · `03_ledger_order_component.sql`
- [x] **D-11d** Reverse shipping posted by gold on kept / pre-dispatch cancelled lines — Keep the cost wherever gold posted it. _(implemented, phase 2)_ · `03_ledger_order_component.sql`

#### Ads, website and attribution
- [x] **D-00** Source of truth for ad-facing LPV and revenue — DECIDED (2026-10-14, owner instruction) — platform-reported LPV/revenue must not be used as the ad metric _(implemented, phase 3)_ · `04_bridge_order_attribution.sql`, `fct_web_session.sql`
- [x] **D-06** Default attribution model for campaign/ad revenue — `last_non_direct` is the default; model must be a parameter so the default can be changed later; other models selectable explicitly and never mixed in one total. _(implemented, phase 3)_ · `04_bridge_order_attribution.sql`
- [x] **D-06a** Default-model attribution for orders without last_non_direct credit — Fall back to gold last-touch, rows flagged model_used = last_touch_fallback; other models show an explicit no_model_data row (never mixed). _(implemented, phase 3)_ · `04_bridge_order_attribution.sql`
- [x] **D-06b** Credited session without a campaign id — Fill from the order's same-platform last-touch (campaign_source = last_touch_fill); otherwise campaign unknown. _(implemented, phase 3)_ · `04_bridge_order_attribution.sql`
- [x] **D-13** Platform-reported conversions (Meta purchases/value/LPV, Google conversions/value) — Hide completely — not exposed in `serve`, Cube or to the Agent. Relaxed by D-13r for LPV only: hidden-by-default diagnostic twin `platform_reported_landing_page_views` is kept. _(implemented, phase 3)_ · `fct_ad_delivery.sql`, `fct_ad_delivery_hourly.sql`
- [x] **D-13r** LPV ids — `landing_page_views` / `cost_per_landing_page_view` rebound to website LPV (D-17), major version bump; hidden-by-default `platform_reported_landing_page_views` kept for diagnostics. All other platform conversions stay… _(implemented, phase 3)_ · `fct_ad_delivery.sql`, `fct_ad_delivery_hourly.sql`
- [x] **D-14** Google hierarchy — Native per platform — account → campaign type → campaign → ad group → ad (non-PMax) + device and network as Google-only breakdowns; PMax stops at campaign (+device/network). Cross-platform overview uses only common… _(implemented, phase 3)_ · `04_bridge_order_attribution.sql`, `fct_ad_delivery.sql`, `fct_ad_delivery_hourly.sql`, `fct_web_session.sql`
- [x] **D-17** Website LPV definition — LPV = website sessions attributed to the platform/campaign/ad (UTM/click-id) with ≥1 tracked page view. Sessions without `ad_id`/`campaign_id` roll up as explicit `ad unknown` / `campaign unknown` members (Meta 19% /… _(implemented, phase 3)_ · `fct_web_session.sql`

#### Profit, periods and product ads
- [x] **D-07** Ad cost allocation to orders/products — Period match, no allocation. Period profit = contribution of orders placed in the period − ad spend delivered in the period. Campaign drill = attributed contribution (default model D-06) − that campaign's spend;… _(implemented, phase 4)_ · `05_mart_profit_fact.sql`, `mart_period_profit.sql`
- [x] **D-12** As-of / closing cutoff — Live + monthly close. Default = latest state; locked month-end snapshots make closed months reproducible; recent cohorts flagged `maturing` for a defined window (D-12a). _(implemented, phase 4)_ · `07_snap_profit_monthly.sql`, `mart_period_profit_closed.sql`, `semantic_v3/close_month.py`
- [x] **D-12a** Maturity window — 45 days after order date a cohort is `maturing`. _(implemented, phase 1)_ · `fct_order.sql`, `ledger_order_component.sql`
- [ ] **D-18** Net profit — Contribution only. Top published metric = *contribution after ads*. No metric named "net profit" until an opex source exists; Agent explains the gap. `canonical_pnl.net_profit` retired with it. Refined by D-18r:… _(planned, phase 4)_
- [ ] **D-18r** `net_profit` ids — ids kept; resolve to contribution after ads; description and Agent answer state opex is excluded. _(planned, phase 6)_
- [x] **D-19** Product-level ad metrics — Credit-based, no spend split. Product drill shows attributed orders/sales per campaign/ad (which products each ad sold); `product_ad_spend`, `product_gross_roas`, `product_net_roas`, `product_mer`,… _(superseded)_
- [x] **D-19o** Product-level ad metrics vs no-capability-loss — kept as an explicit `allocated` family — allocation = attribution credit (default model) × line gross-sales share; `allocation_rule_version` on every row; Unallocated spend row so totals reconcile; never used in… _(implemented, phase 4)_ · `06_mart_product_ad_allocated.sql`, `mart_product_ad_allocated.sql`
- [x] **D-21** Event-date P&L family (`pnl_*`, 25 ids) — kept as explicit family, rebuilt on the same component ledger. Order-date cohort stays the default. Reconciliation: equal over all history; per period, event − cohort = itemised cross-period bridge with 0 unexplained ₹… _(implemented, phase 4)_ · `05_mart_profit_fact.sql`, `mart_pnl_event.sql`

#### Scope and process
- [x] **D-04** Amazon in overview roll-up — Out of scope for this rebuild. Brand overview is labelled D2C (Shopify) only; Amazon remains a documented gap; Amazon gold tables are untouched (not deleted) _(scope)_
- [x] **D-23** Rebuild scope and consumers — `seleric_systems` web dashboard is not used — it and Cube v1 are excluded. Scope = Seleric Agent, Core MCP, Cube v3, new canonical serve. Migrate only `/opt/seleric/reports` scripts still in use. Reconcile new serve… _(process)_
- [x] **D-24** Cutover style — parallel build (new serve objects, Cube v3 on :4003, catalogue_v3), switch after the no-capability-loss gate; rollback = env flip; old objects dropped after 2 stable weeks + zero-read check. _(process)_
- [x] **D-26** Live v2 defects F0-1 (4 tax metrics error) / F0-2 (customers × first-order product errors) — leave broken in v2; v3 delivers them (CAPABILITY_MAP `live_defect`). No v2 production write. _(implemented, phase 0)_ · `(no code: v2 left unchanged)`

#### Other
- [x] **D-12b** Monthly close: months before the first snapshot — reconstructed, flagged. Earlier months are rebuilt from today's state and carry close_status = reconstructed (never presented as locked); real closes carry locked. _(implemented, phase 4)_ · `mart_period_profit.sql`, `semantic_v3/close_month.py`
- [x] **D-21a** Event-date family: date of the product-cost restock credit — order date, as v2. In mart_pnl_event the product_cogs_credit stays on the order's placement date; every other component falls on its own event date. _(implemented, phase 4)_ · `05_mart_profit_fact.sql`
- [x] **D-27** Allocated product-ad family: period rule — same day, default-model credit. A campaign-day's spend is spread over the lines of orders credited to that campaign (last_non_direct) on that cohort day, by credit × line gross-sales share; spend with no credited order… _(implemented, phase 4)_ · `06_mart_product_ad_allocated.sql`
- [x] **D-27b** Allocated product family: LPV source — website sessions (D-17). Product LPV = website LPV sessions allocated by the same rule as spend; changes vs today's Meta-reported product_landing_page_views. _(implemented, phase 4)_ · `06_mart_product_ad_allocated.sql`
<!-- END GENERATED: decisions -->

## Known limits
<!-- BEGIN GENERATED: limits -->
27 known limits (6 open). `[x]` = accepted and shown to users, `[ ]` = open or planned. Full table: `doc/semantic_v3/KNOWN_LIMITS.md`; source: `doc/semantic_v3/KNOWN_LIMITS.yaml`.

- [x] **L-01** (Attribution, accepted) Multi-touch attribution (last_non_direct, first_touch, linear, journey last-touch) exists only from 2026-06-01; ~8 % of later orders lack it too. Impact: 16,484 pre-June sale orders (₹2.87 Cr gross) + 811 since (₹17.5L) use the last-touch fallback under the default model; other models show no_model_data. Handled by D-06a. Next: rows carry model_used; Agent shows coverage % with every model answer (Phase 6)
- [x] **L-02** (Attribution, accepted) Google website sessions often lack campaign (29 %) and ad (88 %) ids; 45 % of credited Google touches have no campaign. Impact: 792 Google orders (₹17.8L) + 279 Meta (₹5.6L) credited to a session without campaign; 328 filled from last-touch, the rest sit in `campaign unknown`. Handled by D-06b, D-17. Next: tracking fix upstream (gclid → campaign mapping) would shrink `campaign unknown`
- [x] **L-03** (Ads, accepted) Google Performance Max (74 % of Google spend) has no ad group / ad level. Impact: PMax drill stops at campaign (+ device / network); synthetic pmax / pmax_* / camp_* ids are NULL. Handled by D-14. Next: none — platform limitation
- [x] **L-04** (Ads, accepted) Hourly delivery exists only from 2026-04-01 for Meta and 2024-11-15 for Google; Google hourly has no ad group / ad level. Impact: hourly answers before those dates are unavailable (not zero). Next: Cube v3 refuses hourly queries before coverage starts (Phase 5)
- [x] **L-05** (Ads, accepted) Meta reach is only valid per ad-day; it cannot be summed across ads or days, and frequency cannot be averaged. Impact: no brand / campaign reach totals. Next: expose reach only at native grain (reach_native)
- [x] **L-06** (Ads, accepted) Platform-reported conversions (Meta purchases / value, Google conversions) are hidden; website purchase value is a tracking-quality metric only. Impact: platform ROAS is not available as a business metric. Handled by D-00, D-13, D-13r, D-09. Next: none
- [x] **L-07** (Costs, accepted) COD handling, checkout fee and weight discrepancy were never recorded before 2026-09. Impact: legacy-era variable cost is understated by those three components (shown as cost_method not_recorded, never estimated). Handled by D-11a. Next: none unless a historical rate card appears
- [x] **L-08** (Costs, accepted) Legacy shipping, packaging, gateway and RTO costs are per-unit rate-card estimates, not courier / gateway invoices. Impact: cost_method rate_card / estimated_backfill; legacy RTO reverse shipping back-filled (₹2.75L). Handled by D-11, D-11c. Next: invoice-based costs would be a new cost source
- [x] **L-09** (Costs, accepted) Product cost is missing on 247 brand-20 lines (0.8 %); 410 lines since 2026-09 are provisional (one lump estimated_ops_cost). Impact: shown as cost_status missing / provisional, never zero. Handled by D-03. Next: cost data owner fills product cost; provisional rows finalise upstream
- [x] **L-10** (Orders, accepted) 68 sale orders (₹96k) have conflicting or no outcome evidence (Unknown). Impact: booked as cancelled in the ledger; outcome stays `unknown` and visible. Handled by D-22, D-22h. Next: none
- [x] **L-11** (Orders, accepted) Delivery and RTO evidence depends on the era — bare RTO tag (Dec 2024 – Nov 2025), no tagging (Jan – Jun 2026), Logisy tags (Jul 2026 on); courier status is mostly empty on older returns. Impact: outcomes for those eras follow owner rulings on evidence patterns, not direct proof. Handled by D-22a, D-22b, D-22c, D-22d, D-22e. Next: none
- [x] **L-12** (Revenue, accepted) Gold's order header treats shipping as tax-free; v3 taxes shipping at the principal line rate (composite supply). Impact: ex-GST view differs from gold's header by ₹23k (= shipping GST); incl-GST ties to the paisa. Handled by D-25, D-10. Next: none
- [x] **L-13** (Revenue, accepted) Exchange price adjustment is a value difference; same-value exchanges show ₹134 average noise from discounts. Impact: small residual on same-value exchanges. Handled by D-01a. Next: none
- [x] **L-14** (Revenue, accepted) No operating-expense source in the warehouse. Impact: top figure is contribution after ads; `net_profit` ids mean contribution after ads (opex excluded). Handled by D-18, D-18r. Next: add an opex source to enable true net profit
- [x] **L-15** (Scope, accepted) Amazon (marketplace + Amazon Ads) is out of scope; brand overview = D2C Shopify only. Impact: brand 20 Amazon sales and spend are not in any total. Handled by D-04. Next: separate project
- [ ] **L-16** (Scope, planned) Brand 28 is spend-only (no commerce); brands 24–27 have 1–32 orders. Impact: profit / ROAS / AOV must be refused for brand 28 (v2 computes −spend today, F0-3); 24–27 flagged low-volume, not certified. Next: refusal in Cube v3 / catalogue v3 (Phases 5–6); owner confirms the value → refusal change in Phase 7 (Q11)
- [ ] **L-17** (Reconciliation, open) v2's cost total (order_pnl_daily.net_cogs) is ₹17–18L below gold's posted line costs; that v2-internal composition is not decomposed by cause. Impact: the v2 → v3 contribution change (−₹16.9L all history) is only partly itemised (gold → v3 is fully proven). Next: decompose v2 cost composition for the Phase 7 variance report
- [x] **L-18** (Freshness, accepted) v3 semantic tables refresh every 5 minutes while gold reloads continuously (CDC). Impact: up to ~5 min lag; live checks exclude orders gold changed after the last refresh and report them as pending. Next: none
- [ ] **L-19** (Data quality, open) 12 gold payments belong to orders missing from gold.fct_orders. Impact: excluded from serve.fct_payment. Next: upstream (dbt) investigation
- [ ] **L-20** (Web, open) Checkout-timing session metrics (seconds to checkout / purchase, checkout steps) are structurally empty — Shopify checkout events are server-side without a session id. Impact: those metrics stay unavailable (data-quality waiver). Next: dbt fix (plumb cart journey checkout time into sessions)
- [ ] **L-21** (Operations, open) system.query_log keeps 7 days. Impact: the "14 days of zero reads" rule before dropping old views cannot run as written. Next: owner chooses longer TTL or two 7-day checks (Q8)
- [ ] **L-22** (Operations, planned) Live v2 defects — 4 tax metrics and customers × first-order product fail in Cube v2 today. Impact: those Agent answers error until the v3 cutover. Handled by D-26. Next: delivered by v3 (Phase 5–8)
- [x] **L-23** (Model, accepted) Event-date P&L, monthly close / as-of snapshots, allocated product-ad metrics and period profit were not built. Impact: resolved in Phase 4 — serve.mart_period_profit, mart_pnl_event, mart_product_ad_allocated and the closed views exist. Handled by D-07, D-12, D-19o, D-21. Next: Cube v3 wires them in Phase 5
- [ ] **L-24** (Marts, open) The event-date family (pnl_*) dates cancels / RTO / returns by the v3 ledger event date; v2 dated some of them differently. Impact: per month, v3 − v2 net profit = cohort leg (bridged, with causes) + an event-dating leg that is NOT itemised by cause (brand 20 Jul ₹-1.50L, Aug +₹0.48L, Sep +₹0.92L); it nets to 0 over all history (v2 event = v2 cohort, v3 event = v3 cohort). Handled by D-21, D-21a. Next: Phase 7 variance report to the owner; itemise the dating leg per order if the owner wants the individual months signed off
- [x] **L-25** (Marts, accepted) Period profit and the event-date family use the default attribution model only (last_non_direct, credit 1 per order). Impact: campaign / ad profit under first_touch, linear or last_touch is not offered; those models stay in serve.bridge_order_attribution for attribution questions. Handled by D-06. Next: none — fractional credits would break the exact roll-up (FIN-07); no v2 capability did this
- [x] **L-26** (Allocated family, accepted) Spend is allocated only within a campaign-day that has a credited sale line with sales; the rest is an `Unallocated` row. Impact: Sep 2026 brand 20: ₹7.86L allocated, ₹4.08L unallocated of ₹11.93L. Free-gift lines (zero sales) get no share. Website LPV of sessions without a campaign id stays out of the allocation. Handled by D-19o, D-27, D-27b. Next: none — owner chose the same-day rule (D-27); never used in default answers
- [ ] **L-27** (Monthly close, open) No month is locked yet; the close script exists but has not run, and there is no schedule. Impact: every past month is labelled `reconstructed` (rebuilt from today's state); `serve.mart_*_closed` is empty. Handled by D-12, D-12b. Next: owner decides when to lock the first month (Sep 2026 is still `maturing` until mid-Nov, D-12a) and approves the cron / Mage schedule
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

## Phases — checklist (each ends with a commit, a PROGRESS entry and a gate)
Progress folder: `doc/semantic_v3/` holds `PLAN.md` (= this file), `PROGRESS.md` (session log), `CHECKLIST.md` (details and findings), `CAPABILITY_MAP.yaml`, `DECISIONS.yaml`, `KNOWN_LIMITS.yaml`. Branches: `semantic-v3` in mage-ai (worktree `/home/tervigon/work/mage_v3`) and Seleric_Agent_Core (worktree `/home/tervigon/work/agent_core_v3`). Nothing is pushed. Seleric_Agent stays on `gaurav`.
`[x]` done · `[ ]` not done · `[~]` partly done.

### Phase 0 — Baseline and capability inventory (no behaviour change) · DONE 2026-10-14 · gate PASS
- [x] Write D-19o / D-21 / D-18r / D-13r / D-22 into spec §0B. Commit the spec and its backup.
- [x] Capability inventory → `CAPABILITY_MAP.yaml` (`scripts/capability_inventory_v3.py`).
  - Sources: catalogue_v2, Cube v2 `/meta`, the live MCP surface, cube.js brand scoping.
  - Coverage: hourly + Meta breakdown bindings, `semantic_sql`, fiscal_year, meta keys, hierarchies, inline SQL, refusals.
  - Targets: keep / refine / replace / new; no `retire`.
- [x] Repo ↔ live DDL diff (`scripts/serve_ddl_drift_v3.py`): all drift = 3 unapplied mage-ai commits (f645091, 1db9f26, 8a2b333).
- [x] Number baseline (`scripts/v3_baseline.py` → `catalogue/baselines/v2.json`, 1,494 values) + serve fingerprint (55 views).
- [x] Consumer check → `CONSUMERS.md` (query_log keeps only 7 days).
- [x] Gate: CAPABILITY_MAP covers 100 % of catalogue_v2 ids and Cube v2 members (0 gaps).

### Phase 1 — Order classification · DONE 2026-10-14 · applied
- [x] Implement D-01, D-05 / D-22, D-08, D-08a, D-15, D-16 (+ D-22a–i, D-08b, D-16a, D-01d from interviews):
  - `semantic.core_order_class`, `core_order_line_class`;
  - `serve.fct_order`, `fct_order_line`.
- [x] Contracts (`mage-ai/serve/evaluations/v3_contracts.py`): grain, FIN-13, FIN-14, every replacement resolves to a root, Unknown reported, brand 28 has no orders.
- [x] Interview checkpoint: outcome counts and samples reviewed by the owner (Unknown 2,372 → 68 orders).
- [ ] Brand 28 / 24–27 refusal itself → Cube v3 / catalogue v3 (Phases 5–6).

### Phase 2 — Component ledger · DONE 2026-10-14 · applied
- [x] Implement C2/C3, D-03 / D-03a, D-10, D-11, D-02 / D-02a, D-02b (+ D-01a–c, D-05a/b, D-08c, D-10a, D-11a/c/d, D-22h/j/k).
  - `semantic.ledger_order_component` + serve view;
  - `serve.fct_payment` (cash vs exchange credit).
- [x] Tests: FIN-01 (order = Σ lines + order postings, incl-GST to the paisa), each component once, cost coverage.
- [x] Reconciliation (`v3_reconcile_gold.py`): line / order / product vs gold exact; v2 → v3 net-sales and gold → v3 cost bridges, every ₹ assigned to a decision id, 0 unexplained.
- [ ] v2's own cost composition vs gold (₹17–18L) not itemised → Phase 7 variance report (limit L-17).

### Phase 3 — Ads, web, attribution · DONE 2026-10-14 · applied
- [x] `serve.fct_ad_delivery` (daily) + `fct_ad_delivery_hourly` at native grain:
  - Google ad group × ad × device × network;
  - PMax / camp_ synthetic ids NULL;
  - hourly duplicates fixed.
- [x] Website LPV (D-17) with `campaign unknown` / `ad unknown` members: `serve.fct_web_session`; events in `serve.fct_web_event`.
- [x] `bridge_order_attribution` with the model parameter: default last_non_direct, last-touch fallback (D-06a), campaign fill (D-06b).
- [x] Tests: spend = gold per platform and day; session members roll up (C5); FIN-15; credits Σ = 1; FIN-07 attributed net sales = brand. Contracts 39/39 live.

### Phase 4 — Marts · DONE (applied 2026-10-14, contracts 57/57 live)
- [x] `semantic.mart_profit_fact`: ONE fact for both date axes (cohort + event) and for orders and ad spend. `serve.mart_period_profit` (default, order-date cohort) and `serve.mart_pnl_event` (D-21, event date) are thin views over it, so they reconcile over all history by construction.
- [x] `serve.mart_product_ad_allocated` (D-19o): same-day, default-model-credit allocation of campaign-day delivery to sale lines (D-27); website LPV (D-27b); an `Unallocated` row, so Σ allocated + unallocated = delivery exactly.
- [x] FIN-07 roll-ups: mart contribution and net sales = the ledger and spend = gold, per brand × month; every row has an attribution member (ad · ad_unknown · ad_not_applicable · campaign_unknown · no_paid_platform), so nothing is dropped.
- [x] Event − cohort: per brand × month it equals an itemised cross-period bridge computed independently from the ledger (MART-EVENT-BRIDGE, can fail). All history: event = cohort in v2 and in v3. Sep 2026 `pnl_net_profit` ties to the v2 anchor: −15,27,829.44 → −15,52,748.81 (cohort leg −1,17,397 + event-dating leg +92,478).
- [x] Monthly close (D-12, D-12b): append-only `semantic.snap_profit_monthly`, `serve.mart_*_closed`, `serve/semantic_v3/close_month.py` (refuses an unfinished month, a closed month, a stale refresh). Live views label each month `open` / `restatable` / `reconstructed`.
- [ ] No month is locked yet and there is no schedule (L-27): the owner chooses the first close and approves the schedule.

### Phase 5 — Cube v3 model (`mage-ai/infra/cube/model_v3`, copied from model_v2 then rebased)
- [ ] Same conventions: one root fact per view, `public:false` cubes, `meta.v2` mapping (in place of `meta.v1`), composite pk, `nullIf` counts, fiscal_year.
- [ ] Platform-native drill hierarchies (Meta / Google per D-14), with a cross-platform common view.
- [ ] Pre-aggregations on the hot grains: period profit by day, ad delivery by day, orders by day.
- [ ] Move the inline business SQL now in Cube (`ltv_cac_daily`, `funnel_channel`) into serve.
- [ ] Brand capability refusals: brand 28 profit / ROAS / AOV; hourly queries before coverage starts (L-04, L-16).
- [ ] `cube-v3` service in `Seleric_Agent_Core/docker-compose.yml` on :4003 (image `cubejs/cube:v1.6.48`, `CUBEJS_SCHEMA_PATH=src/model_v3`, dev mode **off**). Runbook: after a model edit, `docker restart` the cube-v3 container, then regenerate the catalogue.

### Phase 6 — Catalogue v3 + MCP
- [ ] `scripts/build_catalogue_v2.py` → parameterise it as `build_catalogue.py --version 3`; id map `catalogue/migrations/v3_id_map.yaml` from CAPABILITY_MAP; update `VIEW_INFO`.
- [ ] Concepts default to the order-date cohort, `last_non_direct` and contribution after ads. The `allocated` and `pnl_*` families are reachable only on explicit request.
- [ ] Extend the `valid_for` rules: Google-only and Meta-only dims; PMax has no ad level. Coverage % is shown with every attribution-model answer (L-01).
- [ ] `tests/test_semantic_v3.py`. Point `validate_catalogue.py`, `smoke_cube.py`, `v2_gates.py` and `verify_compositions.py` at :4003.

### Phase 7 — Capability and number certification (the cutover gate)
- [ ] **No-loss gate:** every CAPABILITY_MAP entry passes a live query on v3.
  - Same metric id, same dims and drills: answers are equal, or the difference is explained by a decision ID.
  - Every drill path in `hierarchies.yaml` still works.
  - `semantic_sql` still works.
  - Every previously refused combination is still refused or is now supported (never a crash).
  - Includes the F0-1 / F0-2 capabilities (L-22).
- [ ] `v3_baseline.py --compare` v2 vs v3, back to back; `cube_member_sweep_v3.py` on :4003.
- [ ] Run the 40-question `certify_qa.py` and the golden questions (`serve/evaluations/*golden_questions.yml`).
- [ ] Variance report for the owner, sorted by ₹, incl. the v2 cost-composition leg (L-17). **Interview checkpoint: the owner signs off the variances** (incl. Q11, brand 28 value → refusal).

### Phase 8 — Cutover
- [ ] Migrate the Seleric_Agent registry / services / evals and the MCP guards / prompts to the v3 names. The `/opt/seleric/reports` scripts are inactive and are not migrated (D-23).
- [ ] MCP env → `CUBE_API_URL=http://cube-v3:4000`, `SELERIC_CATALOGUE_DIR=catalogue_v3`.
- [ ] Run the Seleric_Agent registry check.
- [ ] Watch 48h. Rollback = env flip.

### Phase 9 — Cleanup of old versions (only after 2 weeks stable and a zero-consumer check)
- [ ] Drop the old serve views that CAPABILITY_MAP marks as replaced:
  - `canonical_pnl`, `pnl_daily`, `order_pnl_daily`, `channel_pnl`, `ad_channel_pnl_daily`, `product_pnl`, `product_ad_line`;
  - the `*_all_channels` wrappers;
  - `finance_waterfall_daily`, `ltv_cac_daily`, `meta_ad_attribution_daily`, `platform_attribution_commerce`.
- [ ] Drop the `semantic.fct_*_pnl` + `mv_*` objects; remove the superseded `06b_fct_line_pnl.sql` (D-25).
- [ ] Retire `cube-v2` and `catalogue_v2`.
- [ ] Gold clean-up candidates (owner approval each): `fct_product_variant_cost_history__bak_20261001`, `int_payment_reconciliation__dbt_backup`.
- [ ] **Do not touch** the Amazon gold tables (D-04). Cube v1 serve views may go once unread; stopping the Cube v1 container is an owner decision (Q6).
- [ ] Before each drop: fingerprint, then a zero-read check. query_log keeps 7 days, so this needs a longer TTL or two 7-day checks (Q8, L-21).

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

## Open questions — checklist (ask the owner when the phase reaches them)
- [x] Q1 **D-11a** (Phase 2): historical rate card → answered: no extra data; legacy COD / checkout / weight stay unrecorded.
- [x] Q2 **Allocated-family period rule** (Phase 4) → answered: D-27 same day, default-model credit; D-27b website LPV.
- [x] Q3 **Monthly-close history** (Phase 4) → answered: D-12b reconstructed and flagged. Also D-21a: the restock credit stays on the order date in the event family.
- [x] Q4 **Unknown-outcome review** (Phase 1) → answered: D-22a–e (Unknown 2,372 → 68 orders).
- [x] Q5 **D-16 hashing** (Phase 1) → answered: D-16a hashed person key.
- [ ] Q6 **Stop the Cube v1 container?** (Phase 9).
- [ ] Q7 Any unexplained ₹ in reconciliation (Phases 2–4, 7). Phases 2–4: none (the event-dating leg is not itemised by cause, L-24).
- [ ] Q8 **query_log keeps 7 days** (Phase 9): longer TTL or two consecutive 7-day checks?
- [x] Q9 **Live v2 defects** → answered: D-26, leave them until v3.
- [x] Q10 **Payments vs exchanges** → answered: D-01b exchange credits.
- [ ] Q11 **Brand 28 value → refusal** (Phase 7): confirm the change in the variance sign-off.

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
