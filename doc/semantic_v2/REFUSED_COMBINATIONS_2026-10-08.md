# Refused metric × slice combinations (2026-10-08)

## Current state (second pass, 2026-10-08 afternoon IST)

Live versions: Cube model mage-ai `main` f6d0c57 · catalogue / MCP Agent_Core `main` be554dc (Jenkins #78) ·
agent image built from Seleric_Agent `conformed-dims` fea9914 (rollback tags `rollback-20261008f` / `-g`).
`gaurav` 5f79555 holds the same agent changes merged with a teammate's executor rework (90fc6f5, not yet deployed —
the next agent deploy should build from `gaurav`).

Same sweep (149 metrics × 7 slices, brand 20, 2026-09-24..30) on the live stack:

| Outcome | First pass | Now |
|---|---:|---:|
| Answered on the metric's own view | 752 | 814 |
| Answered through a conformed sibling | 60 | 92 |
| Answered through a grain twin | 67 | 66 |
| **Refused** | **115** | **50** |

What closed the gap (all checked against ClickHouse, brand totals unchanged):
- **Product slices on every domain.** Proxy members of the product family: `basket_*` on commerce, payments,
  attribution (orders / payments / touches of orders CONTAINING the product), `viewed_*` on web_sessions (sessions
  that viewed / added it), `first_order_product_*` on customers. Excluded wherever a grain twin carries the real
  product member (net sales by product stays line revenue). Declared once as `PROXY_FAMILIES` in the generator.
- **Ad spend / ROAS / MER by product** — `product_ad_spend`, `product_gross_roas`, `product_net_roas`,
  `product_mer`: campaign-day spend allocated over the lines of the orders each campaign drove (a model; Sep
  777,843 of 1,193,322 allocated — campaign-days with no attributed order sit on no product).
- **Unit economics by platform / campaign** (`ltv`, `cac`, `ltv_cac_ratio`), attribution paths by the order's traffic
  / campaign, touches by their session's campaign, funnel purchases / revenue / bounce by platform.
- **Twins from any scope** (session → event, attributed → product), proxies and platform sets derived from data —
  no metric / keyword / value literals in agent code or Cube SQL.
- Agent: comparisons judge the later window against the earlier one; a period to date cuts only its last day at
  the elapsed hours; daily-only metrics compare whole days; the question's named values (e.g. "Meta") constrain
  every prefetched query. Golden Q6 and the original "Meta campaigns … CPC, CPM, LPVs" question verified exact.

### Remaining refusals (50) — data limits, not pipeline gaps

| Group | Combinations | Why | What would close it |
|---|---|---|---|
| Ad delivery counts by product | clicks, impressions, ctr, cpc, cpm, landing_page_views, link_clicks, cost_per_landing_page_view, cost_per_link_click, thruplays, hook_rate, hold_rate_15s, video_completion_rate, status_changes | No ad source has a product dimension (Meta breakdowns: age / region / placement only) | Catalogue / DPA product-level ad insights ingested, or a campaign → product map maintained as data |
| Order-date P&L costs by product | shipping_cost, packaging_cost, payment_gateway_fees, rto_cost, operating_cost, taxes_on_net_sales, be_roas, cost_per_order, channel_cac | Costs exist per order-date × channel only; line-level opex (net COGS − product cost, 161k) does not reconcile with the P&L (123k, Sep) | `semantic.fct_order_pnl_daily` rebuilt at line grain with an agreed allocation rule |
| Finance (event-date) P&L by product | every `pnl_*` (22) | Catalogue policy: product sales exist on order date only | Same line-grain P&L, event-dated |
| CAC / LTV:CAC by product | cac, ltv_cac_ratio | New customers have no product spend basis | Product CAC = allocated spend ÷ new customers whose first order contains it (define, then model) |
| Funnel mart by product / campaign | funnel_purchases, funnel_revenue | `mart_funnel_daily` grain is day × channel | Add campaign / product to the mart, or use orders / product metrics (already answer it) |

## Open tasks

1. **Deploy `gaurav`** (5f79555) — it merges the teammate's executor rework with these fixes; the running image is
   `conformed-dims` fea9914. Re-run golden Q6 / Q17 / Q19-22 after.
2. **Answer audit false positive** — `total_mismatch` read "173 products" as a stated table total (golden Q21 failed
   once after 4 revisions; passed on replay). A count of rows is not a column total.
3. **Ratio "best and worst" rankings** — a breakdown sorted by a ratio lets tiny-volume rows win (product ROAS: a
   5 INR-spend product at −190×). The entity-comparison path already selects by `volume_metric`; the breakdown path
   does not, and "worst" took the 10th of a descending top 10.
4. **Large breakdowns and model arithmetic** — golden Q17 fetched the right 103 rows, then summed them by hand and
   the provenance gate rejected the invented sums (correctly). Group totals should come from a second grouped query
   or `run_python`, not prose.
5. **Proxy notes in summaries** — a sibling swap says "same values on this metric's view"; for `basket_*` /
   `viewed_*` it should also carry the dimension's description (orders / sessions containing the product).
6. Repo DDL `serve/customer/views/ltv_cac_daily.sql` (c73ed02) reads columns `serve.order_attribution` lacks — fix or
   drop it; the Cube model now carries platform / campaign itself.
7. Cube v2 runs in dev mode off the mage-ai working tree: a YAML error or a joined cube without a primary key takes
   the whole model down instantly. Validate YAML before saving; consider a pre-save check.

---

## First pass (morning) — history

The agent still refuses these combinations after the conformed-dimensions work went live:
- Cube model: mage-ai `main` 13aa39b
- Catalogue and MCP: Agent_Core `main` aea452c
- Agent: Seleric_Agent `gaurav` 1ab4a5e

Each one is a `ModelRetry` that names the metrics that *can* answer the slice. None of them is a crash or a wrong number. They are handled later; this file is the backlog.

## How they were found

The sweep ran every catalogue metric (145) through the deployed agent's own `query_metrics`, with no LLM involved. Setup:
- **Brand:** 20.
- **Period:** 2026-09-24 .. 2026-09-30.
- **Slices (7):** breakdown by `platform`, `ad_platform`, `finance_channel`, `campaign_name` and `product_title`; the filter `ad_platform = meta`; and a `report_date` series.
- **Run:** inside `seleric_agent-api-1`, with the MCP at `http://mcp:8765/mcp`.

| Outcome | Count |
|---|---:|
| Answered on the metric's own view | 752 |
| Answered through a conformed sibling dimension | 60 |
| Answered through a catalogue grain twin | 67 |
| **Refused (this list)** | **115** |
| Total | 994 |

Refusals by slice:

| Slice | Refused |
|---|---:|
| `product_title` | 93 |
| `campaign_name` | 6 |
| `platform` | 4 |
| `ad_platform` | 4 |
| `finance_channel` | 4 |
| `ad_platform = meta` (filter) | 4 |

## A. Slices other than product (22)

| Metric(s) | Refused slices | Why | What would fix it |
|---|---|---|---|
| `avg_touch_count` (view `attribution_paths`) | platform, ad_platform, finance_channel, meta filter, campaign_name | `serve.attribution_paths` has no traffic key or campaign; the Cube `attribution_path` cube has no joins. | Join `attribution_path` → `fact_orders` (brand_id, order_id), then expose the order's traffic and campaign dims in the view, as the returns and payments views do. |
| `funnel_purchases`, `funnel_revenue` (view `web_funnel`) | platform, ad_platform, finance_channel, meta filter, campaign_name | The `serve.funnel_daily` mart is brand × day; its `channel` column is not in the conformed vocabulary. | Session-grain twins on `web_sessions`, for example `session_purchases` (stage_purchased / shopify_order_id) and `session_purchase_revenue` (purchase_revenue). Declare them as `scope: session` rows of the funnel concept. Reconcile against the mart's brand totals first. |
| `ltv_cac_ratio` (view `unit_economics`) | platform, ad_platform, finance_channel, meta filter, campaign_name | Live `serve.ltv_cac_daily` is brand × day only. See the note below for why the repo DDL can't fix it. | Rebuild the `ltv_cac_daily` cube as Cube `sql:`: new-customer orders from `serve.order_attribution` (lt_platform, lt_finance_channel, lt_campaign_id), UNION ad spend from `serve.ad_delivery_daily` (ad_platform, campaign_id). Carry platform, finance_channel, ad_platform and campaign directly. Sep brand totals to hold: revenue 1,183,009.62, 687 new customers, spend 1,193,322.06, ratio 0.9914. |
| `touches`, `orders_with_touchpoints` (view `attribution`) | campaign_name | `touchpoints` joins only `traffic_source`. | Join `touchpoints` → `sessions` (brand_id, session_id) for the touch's campaign, or → `fact_orders` for the order's campaign. Pick one meaning and document it. |

Note on `ltv_cac_daily`: the repo DDL `serve/customer/views/ltv_cac_daily.sql` (c73ed02) adds `channel`, `sub_channel` and `campaign_name`. It **cannot be applied**: live `serve.order_attribution` has no columns with those names. It has `lt_*` columns instead.

## B. By product (93)

Grouped by the Cube view the metric reads.

### B1. Paid media: no product exists in the source (14)

`ad_spend`, `clicks`, `impressions`, `ctr`, `cpc`, `cpm`, `landing_page_views`, `link_clicks`, `cost_per_landing_page_view`, `cost_per_link_click`, `thruplays`, `hook_rate`, `hold_rate_15s`, `video_completion_rate`

- Meta and Google report delivery per campaign, ad set and ad, never per product. `serve.meta_ads_breakdown_daily` has region, platform_device, age_and_gender, placement and publisher_platform, but no product breakdown. `dim_campaign` has no product link.
- **Spend only:** the repo has a revenue-weighted allocation model, `serve/product/views/product_ad_spend_daily.sql`. It allocates campaign-day spend × the SKU's share of that campaign's attributed revenue. It is not deployed in ClickHouse. Deploying it, or rebuilding it as a Cube `sql:` cube, would answer `ad_spend` and ROAS by product as a *directional* allocation.
- Clicks, impressions, CPM, CTR, CPC, LPVs and the video metrics have no defensible product basis. They stay refused unless a campaign → product mapping is introduced.

### B2. Commerce, order grain (14)

`aov`, `net_aov`, `total_sales`, `order_value_incl_tax`, `cancelled_orders`, `cod_orders`, `prepaid_orders`, `returned_orders`, `returned_or_cancelled_orders`, `return_cancel_revenue`, `refund_amount`, `new_customers`, `new_customer_ltv`, `payment_orders`

- These are order-level amounts and counts. A product has many orders and an order has many products.
- **Fix:** add "basket" dims (for example `basket_product_title`, `basket_sku`) through a `fact_orders` → `order_lines` one_to_many join. Cube's multiplied-measure handling then de-duplicates per order, so each value means "orders containing product X". One order counts under every product in it, so the groups don't add up to the total.
- Put the basket dims in the `product_title` / `sku` family at a lower rank than the twins. Exclude them (`EXCLUDED_DIMS`) for metrics that already have a `scope: product` twin, so `net_sales` by product keeps routing to `product_net_sales`.
- `return_cancel_revenue` and `refund_amount` could instead get line-grain twins, as `product_refunded_amount_excl_tax` does.

### B3. Order-date channel P&L, `order_pnl` (14)

`channel_orders`, `channel_new_customers`, `channel_cac`, `cost_per_order`, `mer`, `gross_roas`, `net_roas`, `be_roas`, `shipping_cost`, `packaging_cost`, `payment_gateway_fees`, `rto_cost`, `operating_cost`, `taxes_on_net_sales`

- `serve.order_pnl_daily` is a view over `semantic.fct_order_pnl_daily`, a MergeTree fed by `mv_fct_order_pnl_daily`. Its grain is brand × order_date × channel / campaign; it has no order_id and no line.
- **Fix:** a line-grain P&L model in `serve/semantic/views/08_fct_order_pnl_daily.sql` (mage-ai). It would allocate the order-level costs (shipping, packaging, gateway, RTO) and the attributed ad spend to lines by revenue share. That is a pipeline change upstream of Cube.
- Until then, the product-grain answers are the existing twins: `product_net_sales`, `product_gross_profit`, `product_return_revenue`, `product_cancel_revenue`.

### B4. Finance P&L, `pnl_*` (21)

`pnl_net_sales`, `pnl_net_cogs`, `pnl_return_revenue`, `pnl_cancel_revenue`, `pnl_return_cancel_revenue`, `pnl_returned_orders`, `pnl_cancelled_orders`, `pnl_returned_or_cancelled_orders`, `pnl_taxes_on_net_sales`, `pnl_shipping_cost`, `pnl_packaging_cost`, `pnl_payment_gateway_fees`, `pnl_rto_cost`, `pnl_operating_cost`, `pnl_contribution_margin`, `pnl_contribution_margin_pct`, `pnl_net_profit`, `pnl_net_margin_pct`, `pnl_mer`, `pnl_net_roas`, `pnl_be_roas`

- The Finance P&L (event date, `serve.pnl_daily`) is brand × day by design.
- Revenue, COGS, return and cancel lines can get `scope: product` twins on the product view, which carries line-level revenue, return, cancel, net_cogs and tax. Declare them only where the definitions agree (Finance uses the event date; the product view uses the order date).
- The cost lines, margin, profit and ratios need the same line-grain allocation as B3.

### B5. Web sessions (16)

`sessions`, `session_bounce_rate`, `session_page_views`, `session_product_views`, `session_collection_views`, `add_to_carts`, `remove_from_carts`, `site_searches`, `avg_page_depth`, `avg_seconds_to_add_to_cart`, `product_view_rate`, `add_to_cart_rate`, `checkout_rate`, `add_to_cart_to_checkout_rate`, `checkout_to_purchase_rate`, `conversion_rate`

- A session is not one product. `serve.web_events` carries product_title and sku per event; `serve.session_funnel` does not.
- **Fix:** "viewed product" dims through a `sessions` → `web_event` one_to_many join, with Cube de-duplicating per session. Each value would mean "sessions that viewed product X".
- Event-grain twins (`event_product_views`, `event_add_to_carts`) already answer the event counts by product. `add_to_carts` → `event_add_to_carts` still needs a `scope: event` row in `web_engagement` if it is not routed.

### B6. Customers and unit economics (6)

`customers`, `repeat_customers`, `repeat_rate` (view `customers`); `ltv`, `cac`, `ltv_cac_ratio` (view `unit_economics`)

- `customers` can already be sliced by `first_order_sku` (sku family), but `product_title` has no family member there.
- **Fix:** a `first_order_product_title` dimension (join `dim_product_variant` on the first-order SKU) in a `product_title` family.
- `ltv`, `cac` and `ltv_cac_ratio` by product need the basket dims from B2 (LTV) and the product spend allocation from B1 (CAC).

### B7. Others (5)

- **`avg_touch_count`, `touches`, `orders_with_touchpoints`:** per order or per touch. They need basket dims, as in B2.
- **`payment_amount`:** payment-transaction grain. Basket dims through its existing `fact_orders` join.
- **`status_changes`:** ad change log. No product, as in B1.

## Re-running the sweep

1. Copy the script into the container and run it:
   ```
   docker cp sweep_agent.py seleric_agent-api-1:/tmp/sweep_agent.py
   docker exec -w /app -e SELERIC_MCP_URL=http://mcp:8765/mcp seleric_agent-api-1 python /tmp/sweep_agent.py
   ```
2. Read the results. The first output line is the tally. Each refused combination is one JSON line: `[metric, slice, "RETRY", message]`.

The script calls `semantic.query_metrics` for every `snapshot.metrics` × slice, as described above.
