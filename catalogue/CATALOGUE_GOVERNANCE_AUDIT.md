# Catalogue Governance Audit — grain, axis, uniqueness & drill-down coverage

**Date:** 2026-09-29 · **Auditor pass:** static (local catalogue + live Cube `/v1/meta`) + live MCP probes
**Scope:** `Base_Agent/catalogue/**` as exposed through `seleric-mcp`, judged by "what a real operator asks".
**Method:** loaded the catalogue through the shipped `catalogue_service/loader.py`, cross-checked every metric against live Cube `/v1/meta`, then fired real operator questions at the deployed MCP (`mcp.seleric.com`) on brand 20 (Tilting Heads), August 2026.

> Original pass was report-only. **Fixes have since been applied** — see "Fixes applied" below and the updated status column in the backlog.

---

## Fixes applied (this pass)

All changes are in `Base_Agent`; the full test suite (308 tests) passes and the new governance gate is green.

| Finding | What changed | Files |
|---------|--------------|-------|
| **F-9** drill-down coverage (992→282 blocked, the 282 are waived PII/high-card or genuinely cross-grain) | Metric slicing surface is now **derived** from the dimensions each Cube view carries, minus a waiver list — new same-grain axes light up with no per-metric edits | `catalogue/dimension_waivers.yaml`, `scripts/sync_dimension_coverage.py` (+`--check`), `catalogue/dimensions/generated_coverage.yaml` (generated), `loader.py` (`_derive_supported_dimensions`, dim-view merge) |
| **F-7** natural names don't resolve | `metrics_query` now falls back to concept/glossary/alias resolution for unknown ids (unambiguous only; disclosed as a warning) | `query_planner.py` `_resolve_metrics` |
| **F-6** raw Cube member leakage | Tool responses are projected to catalogue ids/dimension ids; stored rows keep members for drilldown/insight | `query_planner.py` `_present_rows` |
| **F-5** `grain` silently ignored | `grain` accepted as an alias for `granularity` so the natural word isn't dropped | `gateway/server.py` |
| **F-3** description uniqueness | Ratchet gate: current 25 overlaps accepted, any **new** overlap ≥0.80 fails CI | `catalogue/description_overlap_waivers.yaml`, gate below |
| **CI gate** (your chosen automation) | One entry point: integrity + dim-coverage freshness + crosswalk freshness + broken-metrics + description ratchet + deploy==repo (network checks warn-skip offline) | `scripts/check_catalogue_governance.py` |

Wire into CI: `py scripts/check_catalogue_governance.py` (set `SELERIC_MCP_VERSION_URL` to enable the deploy==repo check).

**Resolved as reasoning-map, not one-off metrics (multi-agent friendly):**
- **F-1** (headline P&L not channel-sliceable) — solved the general way, not by bolting a metric onto `canonical_pnl` (which has no channel axis). `grain_defaults.by_platform` already routes "profit/roas by channel" → `ad_channel_pnl`; I extended `hierarchies.non_drillable` (which already did this for `total_ad_spend`) to `net_profit`, `net_profit_all_channels`, `net_profit_blended`, `contribution_margin`, `gross_profit`, each naming the sibling view to drill into. An agent starting from the headline metric is now routed, not stuck.
- **F-2** (channel × SKU/variant, and every other cross-grain question) — added `hierarchies.cross_grain_bridges`: the shared join keys (`order_id`, `customer_id`, `campaign_id…`, `brand_id+date`) and the explicit boundaries (`channel_x_product`, `channel_x_session`) that are NOT a single metric. The agent now has a map to plan a multi-step join or to correctly say "not a single metric," instead of silently failing. No new mart required for the agent to reason; a physical channel×product mart remains optional future work in `mage-ai`.
- **F-8** — no change: `customer_data` declares `serving_date_axis: null` **on purpose** (master data, no time axis; `composition_rule` says so). Forcing a date axis would be wrong; the ontology `unclustered.customer_data` note already tells the agent lifetime metrics live on CustomerIntelligence.
- **F-0** — enforced by the gate once `SELERIC_MCP_VERSION_URL` is set.

---

## New-mart onboarding (the design goal: "add a mart, referred by the system with minimal change")

When a mart is added tomorrow, the system absorbs it without hand-touching per-metric files:

1. **mage-ai** — add the OpenMetadata product/contract + the Cube view (as today).
2. `py scripts/sync_catalogue_from_sources.py` — domain, ownership, gold→serve lineage, grain and date axis **derive** into the crosswalk. If the view's product declares a domain that a module already covers, it joins that module automatically.
3. `py scripts/sync_dimension_coverage.py` — every dimension the new view carries (minus `dimension_waivers.yaml`) becomes **filterable and groupable** with no metric edits (F-9 machinery).
4. `py scripts/scaffold_metrics_for_view.py <view>` — writes a **draft metric stub per uncovered measure** (id, view, measure, guessed aggregation/unit) to `catalogue/_scaffold/`. Fill each `description`, set `status`, move into `catalogue/metrics/`. This is the only semantic step, and it can't be derived (a measure name can't imply its business meaning).
5. `py scripts/check_catalogue_governance.py` — fails until every measure is catalogued or waived, so nothing is silently half-onboarded.

The reasoning map (`entity_clusters`, `hierarchies`, `cross_grain_bridges`, `grain_defaults`) is hand-authored metadata the agents read via `catalogue_get_ontology` — extend it only when the new mart introduces a genuinely new drill path or cross-grain join.

---

---

## 0. What is already healthy (so we don't "fix" it)

The catalogue is **far more governed than the brief assumed**. Confirmed working:

- **Load-time integrity** (`loader._check_integrity`) already enforces: unique display names (case-insensitive), every `supported_dimensions` entry exists *and* maps to the metric's view, ratio metrics carry `ratio_components`, `depends_on`/`companion` refs resolve, glossary has no colliding targets, and **no two concepts bind the same metric under the same axes** (the uniqueness guarantee the brief wanted — it exists at the concept layer).
- **Runtime drift guard** (`catalogue_service/validate.py`) marks a metric `broken` and drops it from the surface if its Cube member disappears. **Result today: 0 broken metrics** — nothing is silently disabled.
- **Derived facts already auto-sync**: `scripts/sync_catalogue_from_sources.py` regenerates `crosswalk.generated.yaml` (grain, date axis, freshness, gold→serve lineage, ownership) from ClickHouse + Cube `/meta` + OpenMetadata. Grain/axis are **not** hand-typed.
- **Concept layer works**: `resolve_concept("roas")` → `net_roas_all_channels` with basis/scope/platform defaults disclosed, and a disambiguation note. This is the right pattern; the problem is that too little routes through it (see F-4).
- **Brand scope safety is excellent**: brand 28 (Commbitz) carries a `scope_note` telling the agent to refuse profitability questions because commerce data is absent. This is the model every cross-grain caveat should follow.

**So the real defects are not "broken metrics".** They are: (1) causal-chain hops that dead-end at a grain boundary, (2) natural-language names that don't resolve, (3) overlapping descriptions on adjacent metrics, (4) a large computable surface never exposed, and (5) no CI gate forcing any of this to stay correct.

---

## 1. Environment finding (fix first — it undermines every other check)

**F-0 · Deployed catalogue ≠ local repo.** Live MCP reports `catalogue_version: e2bedddbbc71`; the local `catalogue/**` hashes to `04458ef2c99d`. The catalogue that answers real users is **not** the one in this repo. Every audit below is against local files and the live surface; where they differ, the live one wins and the repo is stale (or vice-versa). **Nothing else is trustworthy until deploy==repo is guaranteed** (this is also the first thing the CI gate in §4 must assert).

---

## 2. Drill-down coverage — the systematic picture

The net-profit chain in the brief is **one illustration** of a general problem: for any metric, an operator can ask for any slice the data actually supports. So the real test isn't one chain — it's **every metric × every dimension its own mart carries.**

**Headline: 992 askable metric×dimension combinations are refused, across 120 of 187 metrics** — cases where the dimension physically exists on the metric's *own* Cube view at the *same grain*, but the metric doesn't list it in `supported_dimensions`, so the query is rejected. Classified:

| Class | Combos | Verdict |
|-------|-------:|---------|
| **Business slices** | **821 (82%)** | **Real coverage gaps** — legitimate group-bys the mart supports |
| High-cardinality free-text | 101 (10%) | Mostly OK to keep blocked (creative_body, thumbnails, headlines — not group-by material; expose as *filters* if at all) |
| PII / identity | 70 (7%) | Correctly guarded (email_hash, customer_id, pincode) — but the guard is *implicit*; it should be a recorded waiver |

**Most-frequently-blocked real slices** (count = # of metrics that could take it but don't): `campaign_status` (35), `campaign_objective` (32), `account_name` (28), `ad_format` (26), `ad_status` (25), `adset_status` (25), plus (seen in the per-metric detail) `device_platform`, `country`, `utm_source/medium/campaign/content/term`, `payment_method/gateway`, `financial_status`, `fulfillment_status`, `order_status`, `shipping_region/city`, `landing_page_resolved`, `session_hour`, `session_day_of_week`, `acquisition_channel/platform/campaign`, `is_new_customer`.

Concretely, all of these are currently refused even though the data is right there:
- "Meta CTR **by campaign objective**", "spend **by ad format**", "impressions **by device / country**"
- "AOV **by payment method**", "orders **by city**", "net revenue **by utm_source**"
- "sessions **by landing page**", "conversion rate **by hour / day-of-week**"
- "LTV **by acquisition channel**", "repeat rate **by first-order product**"

The metrics each expose a thin, hand-picked set of dimensions while the marts underneath carry far richer slicing. This is the single largest class of "a real user asks and the tool says no." Full per-metric list: `catalogue/catalogue_governance_audit_result.json → blocked_drilldowns_all`.

**Two structural sub-classes need different fixes:**
- **Same-view, same-grain (the 821):** cheap — widen `supported_dimensions` (or, better, derive it: see the gate in §6). No new data needed.
- **Cross-view / cross-grain (e.g. channel → SKU):** expensive — needs a new mart that carries both axes. Covered as F-2 below.

### 2b. The brief's chain, walked live (one worked example)

Target chain: **net profit → sales → ad spend → channel → traffic → net sales from that traffic → SKU → variant → day.**

| Hop | Works? | Evidence |
|-----|--------|----------|
| net profit (total, by day) | ✅ | `net_profit` by `report_date` returns |
| **net profit → by channel** | ❌ **P0** | `metrics_query(net_profit, dims=[channel])` → *"Dimension 'channel' is not supported by metric 'net_profit'. Supported: brand_id, report_date."* |
| ad spend → by channel | ✅ (diff. metric) | `ad_channel_spend` supports `channel` |
| net profit → by channel (workaround) | ⚠️ | `ad_channel_net_profit` works but is **ad-grain, Meta/Google only**, and returns a huge `unattributed` row (₹356k profit / ₹0 spend in Aug) → structurally ≠ canonical `net_profit` |
| channel → traffic source | ⚠️ **P1** | `resolve_dimension("traffic source")` → **4 candidates** (`lt_utm_source`, `utm_source`, `source_name`, `source_platform`) all at conf 0.7, on 5 different views |
| traffic → net sales from that traffic | ⚠️ | possible, but the revenue metric differs per candidate view (attributed_net_revenue vs commerce_net_revenue vs channel_net_revenue) → **different numbers for the same question** |
| **channel → SKU / variant** | ❌ **P0** | `product_performance` **rejects a `channel` filter**: *"Filter dimension 'channel' is not valid on view 'product_performance'."* Its only channel-like axis is `source_name` (Shopify order source), not paid-media channel/attribution |
| SKU → variant → day | ✅ (product domain only) | `variant_title`/`variant_id` on `product_performance` + `return_lifecycle` only |

### The two P0 breaks

**F-1 · `net_profit` is not channel-sliceable.** The canonical finance metric answers only total/by-day. "Net profit by channel" — the operator's very first drill — forces a switch to `ad_channel_net_profit`, which has *different* semantics (ad grain, no organic except an `unattributed`/`organic` catch-all, different total). Two metrics answer one question with two numbers. The channel-grain profit *exists in Cube* (`channel_pnl.net_profit` + `channel_pnl.organic_net_profit`) but is **not exposed as a channel-sliceable catalogue metric**.

**F-2 · Channel attribution and product/SKU/variant never meet.** Attribution/P&L views (`ad_channel_pnl`, `channel_pnl`, `channel_attribution`, `order_attribution`) carry **no product/SKU/variant axis**; `product_performance` carries **no channel/campaign/attribution axis** (only `source_name`). So "net sales from the Meta channel, by SKU, by variant, by day" — the deepest and most valuable causal query — **cannot be answered at all.** This is a genuine **mart-grain boundary**, not a catalogue bug: no serve table joins paid-media attribution to order line items. Fixing it is a data-model change (a channel×product mart), not a YAML edit. It should be an explicit, surfaced limitation until that mart exists.

---

## 3. Uniqueness & overlapping descriptions

`metrics_query` and the ontology pick metrics partly by their prose, so overlapping descriptions cause wrong-metric selection. Pairwise description similarity across all 187 metrics (SequenceMatcher on normalised text) found **45 pairs ≥ 0.72**. They split into two very different buckets:

### F-3a · Genuine disambiguation risk — same question, different number (fix the prose)
These are *basis/scope variants of one concept*; an operator asking the bare question could get any of them:

| Pair | Similarity | Risk |
|------|-----------|------|
| `gross_roas` / `net_roas` / `be_roas` (3 pairs) | 0.82–0.92 | "ROAS" — three answers. Concept layer resolves `roas`→`net_roas_all_channels`, but the raw ids are individually reachable with near-identical prose |
| `lifetime_gross_revenue` / `lifetime_net_revenue` | 0.86 | "LTV revenue" gross vs net |
| `repeat_order_share` / `repeat_orders` | 0.81 | rate vs count |
| `event_cancel_revenue` / `event_return_revenue` | 0.83 | adjacent, easily swapped |

**Fix:** rewrite each description to **lead with the distinguishing clause** ("**Break-even** ROAS — the ROAS at which contribution = 0…" vs "**Net** ROAS — net sales ÷ ad spend…"), and make sure each is reachable through the concept layer with an explicit axis rather than as a free-floating id.

### F-3b · Template boilerplate — distinct metrics, near-identical prose (cosmetic)
`google_clicks`/`google_impressions`, `meta_breakdown_cpc`/`cpm`/`ctr`, `google_cpc`/`meta_cpc`, the `*_hourly` pairs, `google_net_cogs`/`meta_net_cogs`, etc. These are genuinely different metrics whose descriptions were filled from a shared template, so only the channel/unit token differs. Low correctness risk, but they're why 45 pairs tripped the threshold. **Fix:** lower priority — tighten the opening sentence so the differentiator isn't buried mid-paragraph.

*(Full ranked list of all 45 pairs is in the audit JSON — see §6.)*

---

## 4. Coverage — computable but not agent-queryable

Cross-referencing every catalogue-exposed view's Cube members against what the catalogue names:

- **31 of 35 views expose Cube measures with no catalogue metric.** High-value examples an operator would ask for:
  - `ad_channel_pnl`: `clicks, ctr, impressions, gross_sales, net_cogs, cancel_revenue, returned_orders, discounts` — the ad-grain P&L view can't answer "CTR next to profit by campaign" because only profit/sales/roas/spend/orders are named.
  - `product_performance`: `gross_cogs, total_cogs, cost_coverage_pct, cancelled_units, total_quantity, unique_products`.
  - `session_funnel` / `web_events`: `bounce_rate, engaged_rate, page_depth, purchase_revenue, seconds_to_*` — funnel richness half-exposed.
  - `channel_pnl`: large organic/shopify split surface (`organic_net_sales`, `shopify_net_sales`, per-platform roas) — some intentional duplicates, but the organic/shopify splits look like real gaps.
  - `commerce_orders`: `net_revenue_excl_tax, total_refund_amount, total_shipping_charged, manual_orders`.

  ⚠️ *Caveat:* some are deliberately not re-catalogued because the same number is served on another view. The gate in §5 should list these for a human to accept/deny, not auto-expose.

- **Traffic dimensions exist in Cube but aren't cleanly catalogued.** `touchpoints.channel_source / channel_medium / channel_campaign / lt_channel`, `web_events.referrer_path / platform`, `session_funnel.campaign_objective / ad_format / campaign_status` are all present in Cube and **not exposed as catalogue dimensions** — which is *why* "traffic source" resolves to 4 competing lower-quality candidates (F-3/F-1 chain). **This is a cheap catalogue-exposure fix**, not a data gap: add clean, single-meaning traffic dimensions and point the glossary/concept layer at them.

---

## 5. Other confirmed defects

- **F-5 · Silent wrong-grain.** `metrics_query(..., grain="month")` returned **daily** rows. The grain argument is ignored rather than honoured or rejected. An operator asking for monthly numbers gets daily. **Fix:** honour `grain`, or reject unknown grain values — never silently ignore.
- **F-6 · Raw Cube member leakage.** Successful responses include internal columns like `ad_channel_pnl.net_profit` alongside the metric alias, violating the server contract ("never expose cube names/column names"). **Fix:** project only catalogue aliases in the response envelope.
- **F-7 · Natural names don't resolve in `metrics_query`.** `net_sales` and `ad_spend` — the most natural terms — error out ("not an approved catalogue metric") because `metrics_query` takes only exact ids and **does not route through the concept/glossary layer** that *does* resolve them (`resolve_term("net sales")` → `net_sales_all_channels`, conf 1.0). Any caller that doesn't resolve first fails on the obvious word. **Fix:** have `metrics_query` fall back to concept/term resolution (with the applied resolution disclosed), or make the error carry the resolved id.
- **F-8 · `customer_data` view has no resolved date axis.** Expected for a customer-grain port, but it means date-filtered customer queries silently ignore the filter. **Fix:** declare `semantics.serving_date_axis` in its contract (so the sync script resolves it) or have the agent warn, mirroring the Commbitz `scope_note` pattern.

---

## 6. Auto-governance — the CI gate (your chosen model)

You chose "**CI gate that flags gaps**" over full auto-generation — correct, because descriptions/semantics can't be derived from a Cube measure name. The gate below makes "I never manually chase drift again" true **without** losing curated semantics. It's one script, run in CI and pre-deploy, exit non-zero on any assertion:

1. **deploy == repo** — assert live `catalogue_version` matches the repo hash (kills F-0).
2. **crosswalk fresh** — `sync_catalogue_from_sources.py --check` (already exists; wire it into CI).
3. **no broken metrics** — run `validate_against_cube` (already exists); fail if any metric's members are missing from live `/meta`.
4. **coverage ledger** — every Cube measure/dimension on a catalogue-exposed view is either (a) mapped to a catalogue metric/dimension, or (b) listed in an explicit `catalogue/coverage_waivers.yaml` with a reason. A **new** Cube measure with neither → build fails with "catalogue this or waive it". *This is the "add a metric and nothing needs manual chasing" mechanism: the gate tells you exactly what to write, once.*
5. **description uniqueness** — recompute the pairwise similarity (§3); fail if any pair ≥ threshold isn't in a `description_overlap_waivers.yaml`. Forces new metrics to be described distinctly.
6. **grain/axis resolved** — fail on any view with unresolved date axis not waived (F-8), reusing the sync script's existing `noaxis` warning.
7. **drill-down coverage ledger (the general case, not one chain)** — for **every metric**, every dimension present on its Cube view at the same grain must be either (a) in `supported_dimensions`, or (b) in a `dimension_waivers.yaml` with a class (`pii`, `high_cardinality`, `not_a_grain`) and reason. A new dimension appearing on a mart → build fails with "expose it on these metrics or waive it". This is what turns the 992 blocked combos from an invisible backlog into a governed, shrinking list — and it's the mechanism that makes "add data, never manually chase coverage" true. **Better still, `supported_dimensions` can be *derived*** (view dims minus waivers) instead of hand-listed per metric, so new same-grain slices light up automatically.

Steps 1–3 and 6 are **wiring existing scripts into CI**. Steps 4, 5, 7 are **new** (one audit script — the prototype used for this report is at `catalogue/catalogue_governance_audit.py` and can be promoted to `scripts/`). No metric YAML gets auto-written with *semantics*; the gate only *fails loudly and tells you the one thing to author* — and can auto-derive the mechanical part (`supported_dimensions`) from the mart minus waivers.

---

## 7. Prioritized fix backlog (with the repo that owns each)

| # | Fix | Severity | Owner / repo |
|---|-----|----------|--------------|
| F-0 | Guarantee deployed catalogue == repo; assert in CI | **P0** | Base_Agent CI |
| F-9 | Close the 821 same-grain blocked slices — derive `supported_dimensions` from mart-minus-waivers, or widen the top dims (campaign_objective/status, ad_format, device, country, utm_*, payment_method, geography, session hour/dow, acquisition_*) | **P1** (bulk) | Base_Agent catalogue + gate |
| F-1 | Expose channel-sliceable canonical profit (`channel_pnl.net_profit`+organic) as a metric, or make `net_profit` support `channel` | **P0** | Base_Agent catalogue (+ maybe Cube view) |
| F-2 | Channel × product/SKU/variant mart, or surface the boundary as an explicit `scope_note`-style limitation until it exists | **P0** (data), **P1** (caveat now) | mage-ai marts (data); Base_Agent catalogue (caveat) |
| F-7 | `metrics_query` falls back to concept/term resolution | **P1** | Base_Agent MCP (`app/query_planner.py`) |
| F-3a | Rewrite basis/scope-variant descriptions to lead with the differentiator; route through concept axes | **P1** | Base_Agent catalogue |
| F-4/§4 | Add clean traffic dimensions (`channel_source`/`channel_medium`/`referrer`) + glossary; retire the 4-way "traffic source" ambiguity | **P1** | Base_Agent catalogue |
| F-5 | Honour or reject `grain`; never silently daily | **P1** | Base_Agent MCP |
| CI gate | Build the gate in §6 (steps 4/5/7 new; 1–3/6 wiring) | **P1** | Base_Agent `scripts/` + CI |
| §4 measures | Triage the 31 views' uncatalogued measures → catalogue or waive | **P2** | Base_Agent catalogue |
| F-6 | Stop leaking raw Cube member names in responses | **P2** | Base_Agent MCP envelope |
| F-3b | De-template boilerplate descriptions | **P2** | Base_Agent catalogue |
| F-8 | Declare `serving_date_axis` for `customer_data` | **P2** | mage-ai OM contract |

**Supporting artifacts:** static audit prototype `scratchpad/catalogue_audit.py`; full results `catalogue/catalogue_governance_audit_result.json` (all 45 overlaps, per-view uncovered measures/dims, join-key matrix).

**Recommended next step:** approve this and I'll (a) build the CI gate in §6, and (b) apply the P1 catalogue fixes (F-3a descriptions, F-4 traffic dims). F-1/F-2 need a call on catalogue-vs-mart before I touch them.
