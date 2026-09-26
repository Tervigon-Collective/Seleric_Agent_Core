# 07 — Department Question Map

Proves the model answers each department's real questions **deterministically** and
**without overlap**: every question resolves to one `concept + axes → mart.measure`,
and no metric appears under two conflicting meanings. Format:
*question → concept · axes → mart.measure [filter]*.

## Finance / Leadership

| Question | Resolution |
|---|---|
| Net profit last month? | profit · {basis:net, scope:all_channels} → mart_pnl_daily.net_profit_all_channels |
| What's our net sales (all channels)? | sales · {basis:net, scope:company} → mart_pnl_daily.net_sales_all_channels_pnl |
| Net COGS and its breakdown? | cogs · {scope:all_channels} → mart_pnl_daily.total_operating_cost_all_channels (+ product/shipping/packaging/gateway/rto components) |
| Blended ROAS? MER? | roas · {basis:net, scope:company} → mart_pnl_daily.net_roas_all_channels · mer → mart_pnl_daily.mer |
| Contribution margin? | contribution → mart_pnl_daily.contribution_margin |
| Show the P&L waterfall | (waterfall) → mart_pnl_daily.line_* by waterfall_key |

## Growth / Paid Media

| Question | Resolution |
|---|---|
| Meta spend last week? | ad_spend · {grain:campaign} filter channel=meta → mart_ads_daily.ad_spend |
| ROAS by campaign (both platforms)? | roas · {platform:cross} → mart_ads_daily.net_roas by channel,campaign |
| CTR / CPC / CPM for Google? | impressions/clicks/ctr … filter channel=google → mart_ads_daily.* |
| Net profit by campaign? | ad_net_profit · {grain:campaign} → mart_ads_daily.net_profit |
| Attributed revenue by last-touch channel? | attributed_revenue · {attribution:last_touch} → mart_orders.attributed_net_revenue by lt_channel |
| What changed before performance moved? | (status) → mart_ad_status.status_changes/budget_changes |
| Creative/hook/hold performance? | (delivery meta-only) → mart_ads_daily.hook_rate/hold_rate_15s by creative_id |

## Commerce

| Question | Resolution |
|---|---|
| Total orders / AOV this month? | orders → mart_orders.orders · aov → mart_orders.aov |
| Revenue by channel? | sales · {attribution:channel} → mart_channel_daily.net_revenue_excl_tax (filter channel_type=attribution_closed_set) |
| Sales by state / city / pincode? | sales · {basis:total, scope:company} → mart_orders.total_sales by shipping_region/city/pincode |
| COD vs prepaid split? | orders · {status:cod|prepaid} → mart_orders.cod_orders / prepaid_orders |
| Discounts given? | discounts → mart_pnl_daily.discounts (or mart_orders.discount_amount_excl_tax by dim) |

## Product

| Question | Resolution |
|---|---|
| Best-selling SKUs by units? | units_sold → mart_order_items.units_sold by sku |
| Most profitable product? | (product profit) → mart_order_items.gross_profit_ex_gst by sku |
| Product margin %? | margin · {scope:product} → mart_order_items.product_gross_margin_pct |
| Average selling price? | asp → mart_order_items.average_selling_price |
| Top returned products? | (returns by product) → mart_refund_lines.refunded_units by sku |

## Operations (Returns/Refunds)

| Question | Resolution |
|---|---|
| Return/refund amount this month? | returns_cancels · {measure:revenue} → mart_refunds.refunded_amount_excl_tax |
| Returned orders count? | returns_cancels · {measure:orders, action:return} → mart_refunds.refund_count (orders → mart_orders.returned_orders) |
| RTO / recovered COGS on returns? | (recovery) → mart_refund_lines.rto_cost_refund / recovered_cogs |
| Returns by state / payment type? | → mart_refunds.* by shipping_region / payment_bucket |

## Customer

| Question | Resolution |
|---|---|
| LTV (new-customer first-order)? Lifetime LTV? | ltv · {horizon:first_order} → mart_pnl_daily.ltv · {horizon:lifetime} → mart_customers.avg_ltv |
| CAC? LTV:CAC? | cac → mart_pnl_daily.cac · ltv:cac → mart_pnl_daily.ltv_cac_ratio |
| Repeat rate / repeat customers? | repeat_rate → mart_customers.repeat_rate · new/repeat → mart_customers.customers |
| Acquisition channel mix? | → mart_customers.customers by acquisition_channel |

## Web / Funnel

| Question | Resolution |
|---|---|
| Sessions and conversion rate? | sessions → mart_sessions.sessions · conversion_rate → mart_sessions.conversion_rate |
| Funnel drop-off (pdp→atc→checkout)? | → mart_sessions.pdp/atc/checkout_rate |
| Product / collection views, add-to-carts? | web_engagement → mart_sessions.product_views / collection_views / add_to_carts |
| Conversion by channel? | → mart_sessions.conversion_rate by channel (funnel_fine) |

## No-overlap proof (the collision families, resolved)

Each old collision now maps to distinct `mart.measure` selected by axis — the same
question always lands on the same one, and two different questions never land on the
same metric with different meanings:

| Business word | Axis that disambiguates | Distinct destinations |
|---|---|---|
| "revenue" | attribution + scope | mart_pnl_daily.net_sales_all_channels_pnl · mart_orders.attributed_net_revenue · mart_channel_daily.net_revenue_excl_tax · mart_order_items.net_line_revenue_ex_gst |
| "orders" | attribution + scope + status | mart_orders.orders · mart_channel_daily.orders · mart_ads_daily.orders · mart_order_items.product_orders |
| "profit" | basis + scope | mart_pnl_daily.net_profit_all_channels · mart_pnl_daily.gross_profit · mart_ads_daily.net_profit · mart_order_items.gross_profit_ex_gst |
| "spend" | channel filter | mart_ads_daily.ad_spend (channel=meta/google/…) · mart_pnl_daily.total_ad_spend |
| "returns" | measure + scope | mart_refunds.refunded_amount_excl_tax · mart_orders.returned_orders · mart_channel_daily.return_cancel_revenue · mart_refund_lines.refunded_units |

## Multi-domain & multi-level (single-query coverage)

- "Last week: revenue, orders, new-customer %, and top last-touch channel" → one
  `mart_orders` query.
- "Meta+Google: spend, ROAS, net profit, attributed orders by campaign" → one
  `mart_ads_daily` query; drill campaign→adset→ad on the same mart.
- "Net profit and its channel split" → `mart_pnl_daily` + `mart_channel_daily`
  (reconcile).
- "Conversion rate by channel, then by placement" → `mart_sessions`, drill the
  DIM_CHANNEL hierarchy level.
