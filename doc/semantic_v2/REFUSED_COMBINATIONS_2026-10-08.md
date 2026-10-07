# Refused metric × slice combinations (2026-10-08)

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
