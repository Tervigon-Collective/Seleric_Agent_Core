#!/usr/bin/env python3
"""Generate catalogue_v3/ (semantic v3) from the live Cube v3 model + CAPABILITY_MAP + the v2 id map.

Copied from build_catalogue_v2.py (which stays untouched: catalogue_v2/ regenerates byte-identical, and the
rollback is an env flip). The v3 differences:

  * ONE date-axis switch: every metric carries ``date_axis`` (switchable / placement_only / native / cohort_only)
    from Cube's ``meta.date_axis``; switchable and placement-only metrics list their two time dimensions. The
    ``pnl_*`` / ``product_pnl_*`` ids are ALIASES of their base metric on ``event_date`` (metric_aliases.yaml),
    not metrics: they answer with the base metric's number (D-28).
  * v2 ids are mapped to v3 Cube members by ``meta.v2`` (Cube), CAPABILITY_MAP (alias rows) and the small
    PRODUCT_RENAME table below; ids that share a v3 member become same-axis aliases (kept ids, nothing removed).
  * website landing-page views (D-13r): no Meta-only restriction; the platform twin is hidden by default.

Inputs
  Cube v3 /v1/meta (CUBE_V3_URL)        views, measures (type / format / description / meta), dimensions
  doc/semantic_v3/CAPABILITY_MAP.yaml   alias rows (alias_of / alias_refused)
  catalogue/migrations/v2_id_map.yaml   v2 ids (member, date axis) and the v1 -> v2 hard cut
  catalogue_v3_src/concepts.yaml        hand-maintained concepts (date axis is a request parameter, not a row)
Output
  catalogue_v3/                         loaded by the MCP when SELERIC_CATALOGUE_DIR=catalogue_v3
  catalogue/migrations/v3_id_map.yaml   the resolved v2 id -> v3 member / alias table (generated record)

  uv run python scripts/build_catalogue_v3.py            # (re)generate, then load + integrity-check it
Everything here is derived; hand-maintained choices live in the tables at the top of this file.
"""
from __future__ import annotations

import base64
import collections
import hashlib
import hmac
import json
import os
import re
import shutil
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
V1 = ROOT / "catalogue"
OUT = ROOT / "catalogue_v3"
SRC = ROOT / "catalogue_v3_src"
ID_MAP_V2 = V1 / "migrations" / "v2_id_map.yaml"
ID_MAP_V3 = V1 / "migrations" / "v3_id_map.yaml"
CAPABILITY_MAP = ROOT / "doc" / "semantic_v3" / "CAPABILITY_MAP.yaml"
CUBE_V3_URL = os.environ.get("CUBE_V3_URL", "http://127.0.0.1:4003")
CUBE_ENV = Path(os.environ.get("SELERIC_CUBE_ENV", "/opt/seleric/mage-ai/infra/cube/.env"))

# ---------------------------------------------------------------- hand-maintained choices
# view -> (category, grain, v1 analogue view for cadence / description, datetime dimension, owner, default date dimension)
VIEW_INFO = {
    "commerce": ("commerce", "order", "commerce_orders", "order_created_at", "Commerce Ops", "order_date"),
    "finance": ("finance", "brand_day_channel_ad", "canonical_pnl", None, "Finance", "order_date"),
    "product_profit": ("product", "order_line_event", "product_performance", None, "Finance", "order_date"),
    "product_ads": ("product", "campaign_day_line", "product_performance", None, "Growth / Paid Media", "report_date"),
    "product_cost_changes": ("product", "sku_cost_version", None, "changed_at", "Finance", "changed_at"),
    "paid_media": ("paid_media", "ad_day", "meta_ad_performance", None, "Growth / Paid Media", "report_date"),
    "paid_media_hourly": ("paid_media", "ad_hour", "meta_ad_performance_hourly", "report_hour", "Growth / Paid Media", "report_date"),
    "paid_media_breakdowns": ("paid_media", "ad_day_breakdown", "meta_ad_breakdown_performance", None, "Growth / Paid Media", "report_date"),
    "web_funnel_hourly": ("web_analytics", "brand_day_hour_channel", "funnel_daily", "report_hour", "Growth / Web Analytics", "report_date"),
    "paid_media_changes": ("paid_media", "change_event", "meta_ads_status_history", "changed_at", "Growth / Paid Media", "changed_at"),
    "web_sessions": ("web_analytics", "session", "session_funnel", "session_start", "Growth / Web Analytics", "session_date"),
    "web_funnel": ("web_analytics", "brand_day", "funnel_daily", None, "Growth / Web Analytics", "report_date"),
    "web_events": ("web_analytics", "day_event_type", "web_events_daily", None, "Growth / Web Analytics", "report_date"),
    "web_event_detail": ("web_analytics", "event", "web_events", "event_timestamp", "Growth / Web Analytics", "event_date"),
    "attribution": ("attribution", "order_touch", "touchpoints", "touch_ts", "Growth / Attribution", "order_date"),
    "attribution_paths": ("attribution", "order_model", "attribution_paths", None, "Growth / Attribution", "order_date"),
    "customers": ("customer", "customer", "customer_ltv", None, "Growth / CRM", "last_order_at"),
    "returns": ("operations", "refund", "refund_events", "refund_created_at", "Commerce Ops", "refund_date"),
    "payments": ("operations", "transaction", "payments", None, "Finance", "transaction_date"),
    "funnel_orders": ("web_analytics", "order", "funnel_daily", None, "Growth / Web Analytics", "order_date"),
    "stock_movements": ("operations", "stock_movement", None, None, "Supply Chain Ops", "movement_date"),
    "stock_levels": ("operations", "stocked_item", None, None, "Supply Chain Ops", "last_movement_date"),
    "stock_layers": ("operations", "receipt_layer", None, None, "Supply Chain Ops", "receipt_date"),
    "stock_sync_errors": ("operations", "sync_error", None, None, "Supply Chain Ops", "error_date"),
    "purchase_orders": ("operations", "purchase_request", None, None, "Supply Chain Ops", "order_date"),
    "purchase_order_lines": ("operations", "purchase_request_line", None, None, "Supply Chain Ops", "order_date"),
    "quality_checks": ("operations", "quality_check", None, None, "Supply Chain Ops", "inspection_date"),
    "vendors": ("operations", "vendor", None, None, "Supply Chain Ops", "vendor_created_date"),
    "procurement_sampling": ("operations", "sampling_variant", None, None, "Supply Chain Ops", "variant_created_date"),
}
MODULES = {  # v1 module id -> v3 views (no ontology in v3; extra_views is the scope)
    "webanalytics": ["web_sessions", "web_funnel", "web_funnel_hourly", "web_events", "web_event_detail", "funnel_orders"],
    "commerce": ["commerce", "returns", "payments"],
    "product": ["product_profit", "product_ads", "product_cost_changes"],
    "paidmedia": ["paid_media", "paid_media_hourly", "paid_media_breakdowns", "paid_media_changes", "finance"],
    "attribution": ["attribution", "attribution_paths", "commerce"],
    "customer": ["customers", "commerce", "finance"],
    "finance": ["finance", "product_profit"],
    "operations": ["returns", "payments", "stock_movements", "stock_levels", "stock_layers", "stock_sync_errors", "purchase_orders", "purchase_order_lines", "quality_checks", "vendors", "procurement_sampling"],
}
# Website LPV (D-13r) exists for Meta AND Google: landing_page_views / cost_per_landing_page_view are no longer
# Meta-only. The Meta-only creative-video measures stay.
# Hourly twin views: each binds, measure by measure, to the same-named metric of its daily home view as the "hourly" binding.
HOURLY_HOMES = {"paid_media_hourly": "paid_media", "web_funnel_hourly": "web_funnel"}
META_ONLY = {"video_completion_rate", "thruplays", "hook_rate", "hold_rate_15s"}
HOURLY = ["ad_spend", "impressions", "clicks", "link_clicks", "landing_page_views", "cost_per_landing_page_view",
          "ctr", "cpc", "cpm"]
BREAKDOWN_DIMS = ["breakdown_type", "age", "gender", "publisher_platform", "platform_position",
                  "impression_device", "device_platform", "country", "region"]
# serve.meta_ads_breakdown_daily repeats ALL of Meta delivery once per breakdown_type, so a query must
# pin exactly one. Dimension -> the breakdown_types that carry it (preferred first); [] = never populated.
BREAKDOWN_SLICES = {
    "age": ["age_and_gender"], "gender": ["age_and_gender"],
    "platform_position": ["placement"], "impression_device": ["placement"],
    "device_platform": ["platform_device"],
    "publisher_platform": ["publisher_platform", "placement", "platform_device"],
    "region": ["region"], "country": [],
}
# breakdown_types Meta returns without a field (sum 0, checked live by scripts/v2_gates.py --live)
SLICE_GAPS = {"landing_page_views": {"region"}, "platform_reported_landing_page_views": {"region"}}
UNAVAILABLE = {
    m: ("Not populated: Shopify's checkout_started pixel is server-side (no web-session id), so session "
        "checkout timestamps / step counts are always empty (dbt fct_session_funnel KNOWN GAP). "
        "Use checkout_rate / add_to_cart_to_checkout_rate instead.")
    for m in ("avg_seconds_to_checkout", "avg_seconds_to_purchase", "checkout_steps")
}
# Refund-level money on the returns view: its refund_lines → product dims would repeat the whole refund on every
# product in it (WildTrail Boots Sep: 108,224 refund-level vs 79,753 of its own lines). The line-grain
# product_refunded_amount_excl_tax / product_returns_excl_tax answer by product (concept twins).
_FANOUT = ("Refund-level amount: split by the refunded lines' product / restock it would repeat the whole refund "
           "on every line. Use the line-grain product_* twin.")
EXCLUDED_DIMS = {m: {d: _FANOUT for d in ("product_type", "product_title", "variant_title", "sku", "restock_type")}
                 for m in ("refunded_amount_excl_tax", "returns_excl_tax")}
# Website LPV (D-17) is counted from sessions, and a session carries no ad account (only campaign / ad ids), so an
# account slice or filter reads 0 LPV against real spend (QA AD01). Refused with a pointer, never answered with 0
# (owner 2026-10-17, Phase 7). The platform twin is account-aware; campaign-level slices work.
_NO_ACCOUNT = ("Website landing page views carry no ad account (sessions are attributed to a campaign / ad, not an "
               "account), so an account slice or filter would read 0. Slice by campaign_id / campaign_name instead, "
               "or use platform_reported_landing_page_views (Meta's own figure, diagnostic) for an account view.")
EXCLUDED_DIMS.update({m: {"account_id": _NO_ACCOUNT} for m in ("landing_page_views", "cost_per_landing_page_view")})
NOT_CERTIFIED = {"link_clicks", "thruplays", "hook_rate", "hold_rate_15s", "cost_per_link_click"}  # approved
# Every id the CAPABILITY_MAP marks ``refine`` keeps its id but its number (or label) changes for a named decision
# (semantic v3): its major version is bumped and the decisions are written into the description.
BUMP_MAJOR_FOR = "refine"
# Ratios whose declared human unit scales the raw numerator/denominator quotient
# (e.g. CPM = spend ÷ impressions × 1000). This is decomposition metadata only:
# Cube remains the authoritative engine that computes the published measure.
RATIO_MULTIPLIERS = {
    "cpm": 1000,
    "product_cpm": 1000,
}
RATIO_PARTS = {  # ratio -> component metrics (Cube recomputes every ratio from aggregates)
    "aov": ["total_sales", "orders"], "net_aov": ["net_sales", "orders"],
    "ctr": ["clicks", "impressions"], "cpc": ["ad_spend", "clicks"], "cpm": ["ad_spend", "impressions"],
    "cost_per_landing_page_view": ["ad_spend", "landing_page_views"],
    "cost_per_link_click": ["ad_spend", "link_clicks"], "hook_rate": ["impressions"], "hold_rate_15s": ["thruplays"],
    "video_completion_rate": ["impressions"], "product_gross_margin_pct": ["product_gross_profit", "product_net_revenue"],
    "units_per_order": ["units_sold", "orders"], "add_to_cart_rate": ["sessions"], "checkout_rate": ["sessions"],
    "add_to_cart_to_checkout_rate": ["sessions"], "checkout_to_purchase_rate": ["sessions"],
    "conversion_rate": ["sessions"], "product_view_rate": ["sessions"], "avg_page_depth": ["sessions"],
    "avg_seconds_to_add_to_cart": ["sessions"], "avg_seconds_to_checkout": ["sessions"],
    "avg_seconds_to_purchase": ["sessions"], "bounce_rate": ["funnel_purchases"],
    "events_per_session": ["web_events"], "avg_touch_count": ["touches"],
    "repeat_rate": ["repeat_customers", "customers"], "ltv": ["new_customers"], "cac": ["ad_spend", "new_customers"],
    "ltv_cac_ratio": ["ltv", "cac"],
    "new_customer_ltv": ["new_customers"],
    "product_gross_roas": ["product_ad_spend"], "product_net_roas": ["product_ad_spend"],
    "product_mer": ["product_ad_spend"],
    "product_ctr": ["product_clicks", "product_impressions"], "product_cpc": ["product_ad_spend", "product_clicks"],
    "product_cpm": ["product_ad_spend", "product_impressions"],
    "product_cost_per_landing_page_view": ["product_ad_spend", "product_landing_page_views"],
    "product_cost_per_link_click": ["product_ad_spend", "product_link_clicks"],
    "product_hook_rate": ["product_impressions"], "product_hold_rate_15s": ["product_thruplays"],
    "product_video_completion_rate": ["product_impressions"],
    "product_cac": ["product_new_customers"], "product_cost_per_order": ["product_orders"],
    "product_ltv_cac_ratio": ["product_new_customers"], "product_be_roas": ["product_contribution_margin"],
    "product_contribution_margin_pct": ["product_contribution_margin"], "product_net_margin_pct": ["product_net_profit"],
    "channel_cac": ["ad_spend", "channel_new_customers"], "cost_per_order": ["ad_spend", "channel_orders"],
    "return_rate": ["channel_returned_orders", "channel_orders"],
    "product_return_rate": ["product_order_returned_orders", "product_order_orders"],
    "unit_return_rate": ["returned_units", "units_ordered"],
    "session_bounce_rate": ["sessions"],
    # order-date P&L: its net sales (P&L basis) is the order_pnl_net_sales measure
    # (= contribution_margin + net_cogs on the same view). Ratios here divide by that
    # net-sales measure, not by net COGS.
    "contribution_margin_pct": ["contribution_margin", "order_pnl_net_sales"],
    "net_margin_pct": ["net_profit", "order_pnl_net_sales"],
    "mer": ["order_pnl_net_sales", "ad_spend"], "gross_roas": ["gross_sales", "ad_spend"],
    "net_roas": ["contribution_margin", "ad_spend"], "be_roas": ["order_pnl_net_sales", "contribution_margin"],
    # ops-app stock / receiving (semantic v3 cubes po_headers, po_lines, qc_checks)
    "po_fill_rate": ["po_received_units", "po_ordered_units"], "line_fill_rate": ["line_received_units", "line_ordered_units"],
    "qc_damage_rate": ["qc_damage_units", "qc_actual_units"], "qc_shortfall_rate": ["qc_shortfall_units", "qc_invoice_units"],
}
DIM_RENAME = {("commerce", "event_type"): "order_event_type",  # same member name, different meaning
              ("finance", "row_type"): "order_pnl_row_type",  # kept apart for stable ids (orders | ad_spend)
              ("product_ads", "row_type"): "allocation_row_type",  # allocated | unallocated
              ("paid_media", "row_type"): "paid_media_row_type",
              ("paid_media_hourly", "row_type"): "paid_media_row_type"}
# Three dimensions come from a Cube member called row_type; one shared display name made the v2 id `row_type` (the P&L
# view's, orders | ad_spend) resolve to the allocation dimension (found by capability_check_v3.py, Phase 7).
DIM_DISPLAY = {"order_pnl_row_type": "P&L row type", "allocation_row_type": "Allocation row type",
               "paid_media_row_type": "Paid media row type"}
DIM_V2_ALIAS = {"order_pnl_row_type": ["row_type"]}  # the v2 dimension id that now lives under another id
# One dimension id may mean a different clock per view (like order_date / report_date already do): say so.
DIM_DESC = {"event_date": "Event date. Finance, commerce and product P&L views: the day a cancel / RTO / return is booked (D-29); NULL = "
                          "the Undated member (D-32). Web event detail: the day of the web event (its own clock)."}
# Conformed dimension families: members that carry the SAME value vocabulary on different views (checked live
# 2026-10-08: ad_platform / finance_channel / platform agree on meta + google, acquisition_platform uses the traffic
# platform labels, acquisition_campaign holds campaign names). A filter / breakdown on one member is answered on a
# view that lacks it by the first family member that view has whose values cover it (agent side). Members listed
# in preference order. Regions are NOT a family: shipping (upper-case) vs IP geo vs customer province differ.
FAMILIES = {
    "platform": ["platform", "finance_channel", "ad_platform", "acquisition_platform"],
    "channel": ["channel", "acquisition_channel"],
    "campaign": ["campaign_name", "acquisition_campaign"],
    # product vocabulary on order / payment (basket_*: orders CONTAINING it), session (viewed_*: sessions that viewed
    # or added it), customer (first_order_*: the first order's product) and ad-change (sold_*: products the change's
    # campaign sold) views — see PROXY_OF
    "product": ["product_title", "basket_product_title", "viewed_product_title", "first_order_product_title",
                "sold_product_title"],
    "sku": ["sku", "basket_sku", "viewed_sku", "first_order_sku", "sold_sku"],
    "product_type": ["product_type", "basket_product_type", "first_order_product_type", "sold_product_type"],
    "variant": ["variant_title", "basket_variant_title"],
}
# Proxy families: after the first member, the members carry the same vocabulary with another meaning — the orders /
# sessions / customers that contain, viewed or first bought the product (Cube counts each order / session once under
# every product in it). They answer a product slice for a measure with no product grain (AOV, conversion rate,
# repeat rate by product); a measure whose catalogue grain twin carries the first member answers there instead (net
# sales by product = line revenue, not the revenue of the orders containing it), so the proxies are excluded from it.
PROXY_FAMILIES = ("product", "sku", "product_type", "variant")
PROXY_OF = {proxy: FAMILIES[f][0] for f in PROXY_FAMILIES for proxy in FAMILIES[f][1:]}


def _grain_twins(concepts: list[dict], mid: str) -> list[str]:
    """The concepts' scope-axis twins of *mid* — the rule CatalogueService.grain_twins applies at run time."""
    out: list[str] = []
    for c in concepts:
        scope = (c.get("axes") or {}).get("scope")
        if not scope:
            continue
        rows = c.get("resolves") or []
        for r in rows:
            when = r.get("when") or {}
            if r["metric"] != mid:
                continue
            own = when.get("scope", scope["default"])
            base = {k: v for k, v in when.items() if k != "scope"}
            for t in rows:
                tw = t.get("when") or {}
                if (tw.get("scope", scope["default"]) != own and t["metric"] != mid
                        and all(tw.get(k, v) == v for k, v in base.items())):
                    out.append(t["metric"])
    return list(dict.fromkeys(out))
V1_DIM_ALIASES = {"shipping_state": "shipping_region", "order_status": "order_status",
                  "platform": "lt_platform", "channel": "lt_channel", "product_title": "product_title"}
ALLOWED = {"finance_channel": ["meta", "google", "whatsapp", "organic", "unattributed"],
           "ad_platform": ["meta", "google"],
           "order_pnl_row_type": ["orders", "ad_spend"], "allocation_row_type": ["allocated", "unallocated", "organic", "order_level"],
           "medium_group": ["paid", "owned", "earned", "none", "unclassified"]}
# v1 glossary ids that are live v2 ids again but whose v1 TERMS read best on another metric: "orders by channel" /
# "meta orders" need the full traffic hierarchy (commerce.orders carries platform → channel → sub_channel; the
# order-date channel P&L has no channel). channel_orders stays reachable by id and via the cac concept's grain.
GLOSSARY_REDIRECT = {"channel_orders": "orders"}
DISPLAY = {"aov": "AOV", "net_aov": "Net AOV", "ctr": "CTR", "cpc": "CPC", "cpm": "CPM", "ltv": "LTV", "cac": "CAC",
           "ltv_cac_ratio": "LTV:CAC ratio", "pnl_mer": "P&L MER", "pnl_gross_roas": "P&L gross ROAS",
           "pnl_net_roas": "P&L net ROAS", "pnl_be_roas": "P&L break-even ROAS",
           "mer": "MER", "gross_roas": "Gross ROAS", "net_roas": "Net ROAS", "be_roas": "Break-even ROAS",
           "net_cogs": "Net COGS", "gross_cogs": "Gross COGS", "rto_cost": "RTO cost",
           "contribution_margin_pct": "Contribution margin %", "net_margin_pct": "Net margin %",
           "taxes_on_net_sales": "Taxes on net sales", "hold_rate_15s": "Hold rate (15 s)",
           "channel_orders": "Orders (by channel / campaign)",
           "channel_new_customers": "New customers (by channel / campaign)",
           "channel_cac": "CAC (by channel / campaign)", "cost_per_order": "Cost per order (CPA)",
           "session_page_views": "Page views (sessions)", "session_bounce_rate": "Bounce rate (sessions)",
           "product_orders": "Orders with the product",
           "product_ad_spend": "Product ad spend (allocated)",
           "product_gross_roas": "Product gross ROAS", "product_net_roas": "Product net ROAS", "product_mer": "Product MER",
           "product_ctr": "Product CTR", "product_cpc": "Product CPC", "product_cpm": "Product CPM",
           "product_cac": "Product CAC", "product_ltv_cac_ratio": "Product LTV:CAC ratio",
           "product_be_roas": "Product break-even ROAS", "product_cost_per_order": "Product cost per order (CPA)",
           "product_cost_per_landing_page_view": "Product cost per LPV", "product_landing_page_views": "Product LPVs",
           "product_pnl_net_sales": "Product P&L net sales", "product_pnl_net_cogs": "Product P&L net COGS",
           "product_pnl_net_profit": "Product P&L net profit", "product_pnl_mer": "Product P&L MER",
           "product_pnl_net_roas": "Product P&L net ROAS", "product_pnl_be_roas": "Product P&L break-even ROAS",
           "product_pnl_net_margin_pct": "Product P&L net margin %",
           "product_pnl_contribution_margin_pct": "Product P&L contribution margin %",
           "product_contribution_margin_pct": "Product contribution margin %", "product_net_margin_pct": "Product net margin %",
           "product_rto_cost": "Product RTO cost", "product_pnl_rto_cost": "Product P&L RTO cost",
           "funnel_order_purchases": "Funnel purchases (orders)", "funnel_order_revenue": "Funnel revenue (orders)",
           "product_refunded_amount_excl_tax": "Product refunded amount (ex-GST)",
           "product_returns_excl_tax": "Product returns value (ex-GST)",
           "event_count": "Web events (events)", "event_page_views": "Page views (events)", "event_product_views": "Product views (events)",
           "event_collection_views": "Collection views (events)", "event_add_to_carts": "Add-to-cart events (events)",
           "event_site_searches": "Site searches (events)", "new_customer_ltv": "LTV (by channel / campaign)"}


def human(mid: str) -> str:
    if mid in DISPLAY:
        return DISPLAY[mid]
    if mid.startswith("pnl_"):
        return "P&L " + mid[4:].replace("_", " ")
    if mid.startswith("order_pnl_"):
        return mid[len("order_pnl_"):].replace("_", " ").capitalize() + " (P&L basis)"
    if mid.startswith("product_order_"):
        return "Product " + mid[len("product_order_"):].replace("_", " ") + " (P&L basis)"
    if mid.startswith("product_pnl_"):
        return "Product P&L " + mid[len("product_pnl_"):].replace("_", " ")
    return mid.replace("_", " ").capitalize()


# ---------------------------------------------------------------- inputs
def cube_meta() -> dict:
    secret = os.environ.get("CUBEJS_API_SECRET")
    if not secret:
        secret = next(l.split("=", 1)[1].strip().strip("'\"") for l in CUBE_ENV.open()
                      if l.startswith("CUBEJS_API_SECRET="))
    b = lambda x: base64.urlsafe_b64encode(x).rstrip(b"=").decode()  # noqa: E731
    now = int(time.time())
    head, body = b(b'{"alg":"HS256","typ":"JWT"}'), b(json.dumps({"iat": now, "exp": now + 600}).encode())
    tok = f"{head}.{body}." + b(hmac.new(secret.encode(), f"{head}.{body}".encode(), hashlib.sha256).digest())
    req = urllib.request.Request(f"{CUBE_V3_URL}/cubejs-api/v1/meta", headers={"Authorization": tok})
    return json.load(urllib.request.urlopen(req, timeout=60))


def ry(p: Path):
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def wy(p: Path, doc, header: str = "") -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as fh:
        if header:
            fh.write(header)
        yaml.safe_dump(doc, fh, sort_keys=False, allow_unicode=True, width=110)


# ---------------------------------------------------------------- v2 id -> v3 member
# v2 `product` view members that live under another name on product_profit (the v3 product P&L before ads).
PRODUCT_RENAME = {
    "product_gross_sale": "gross_sales", "product_discounts": "discounts", "product_cancel_revenue": "cancel_revenue",
    "product_return_revenue": "return_revenue", "product_net_revenue": "net_sales", "product_gross_cogs": "gross_cogs",
    "product_net_cogs": "net_cogs", "product_line_cost": "product_cost",
    # v2 line gross profit = net line revenue - net COGS = contribution before ads
    "product_gross_profit": "contribution_margin", "product_gross_margin_pct": "contribution_margin_pct",
}
# v2 ids whose v3 home has another name and no meta.v2 pointer.
EXPLICIT_MEMBER = {
    "cac": "finance.channel_cac",  # unit_economics.cac was brand-day spend / new customers: finance.channel_cac
    "product_order_orders": "product_profit.product_orders",
    "product_order_returned_orders": "product_profit.product_returned_orders",
    "product_new_customers": "product_profit.product_new_customers",
}
# When two v2 ids land on one v3 member the first matching rule picks the real id; the other becomes a same-axis
# alias (kept id, same number). Order = preference.
CANON_PREFIX = ("product_order_",)
CANON_IDS = ("product_contribution_margin", "product_contribution_margin_pct", "product_orders", "channel_cac")
# New base ids the event-date aliases point at (capability_inventory_v3.py "base: new"): fixed names, so an alias
# never points at nothing.
EXPLICIT_NEW = {
    "channel_cancelled_orders": "finance.channel_cancelled_orders",
    "channel_returned_or_cancelled_orders": "finance.channel_returned_or_cancelled_orders",
    "order_pnl_return_cancel_revenue": "finance.return_cancel_revenue",
    "product_order_cancelled_orders": "product_profit.product_cancelled_orders",
    "product_order_return_cancel_revenue": "product_profit.return_cancel_revenue",
    "product_order_returned_or_cancelled_orders": "product_profit.product_returned_or_cancelled_orders",
}
AXIS_VIEWS = {"finance", "commerce", "product_profit", "product_ads"}
# Platform-native dimensions (D-14): exist for these ad_platform values only; a slice by one is scoped to them.
DIM_VALID_FOR = {"device": {"ad_platform": ["google"]}, "network": {"ad_platform": ["google"]}}
# Coverage caveats shown whenever the dimension is used (L-02, L-03).
_PMAX = ("Google Performance Max (about 74 % of Google spend) has no ad group / ad level: its rows stop at the campaign "
         "(device and network are available) and show no ad group or ad (L-03).")
_MANUAL_PAY = ("Since 2026-08-26 the checkout app records every payment with the gateway / method literally 'manual'. Where gold says manual "
               "and the order tags name exactly one method, v3 resolves it (tag phonepe = online, tag cod = COD; gold's explicit value always "
               "wins; D-46). What is still 'manual' is unclassified, not a payment type: untagged orders (drafts, POS, some exchanges). "
               "'Advance online, balance on delivery' orders (tag phonepe, partially paid, from Oct 2026) count as online (L-35).")
DIM_NOTES = {
    **{d: _PMAX for d in ("adset_id", "adset_name", "adset_status", "ad_id", "ad_name", "ad_status", "ad_format", "creative_type", "ad_key")},
    "campaign_key": "Paid website sessions without a campaign id roll up as the explicit member 'campaign unknown' (Google ~29 % of sessions, L-02).",
    **{d: _MANUAL_PAY for d in ("payment_bucket", "payment_method", "payment_gateway", "is_cod")},
}
# Names a new v3 measure may not take because a v2 id already means something else on another view.
NEW_ID_PREFIX = {"finance": "order_pnl_", "product_profit": "product_order_"}


def resolve_ids(measures: dict, v2: dict, cap: dict):
    """-> (real: {id: 'view.measure'}, aliases: {id: {...}}, new: {id: 'view.measure'}). Every v2 id lands in
    exactly one of real / aliases; v3 measures nothing points at get a new id."""
    by_v2 = collections.defaultdict(list)
    for name, x in measures.items():
        ptr = (x.get("meta") or {}).get("v2")
        if ptr:
            by_v2[ptr].append(name)
    real: dict[str, str] = {}
    aliases: dict[str, dict] = {}
    for mid, spec in v2.items():
        c = cap[mid]
        if c.get("date_axis") == "alias":
            a = c["alias_of"]
            aliases[mid] = {"metric": a["metric"], "date_axis": a["date_axis"],
                            **({"refused": c["alias_refused"]} if c.get("alias_refused") else {}),
                            "reason": "event-date id (D-28): the base metric on date_axis=event_date"}
            continue
        view, name = spec["member"].split(".", 1)
        cand = by_v2.get(spec["member"]) or by_v2.get(f"{view}.{mid}")
        member = cand[0] if cand else None
        if member is None and mid in EXPLICIT_MEMBER:
            member = EXPLICIT_MEMBER[mid]
        if member is None and spec["member"] in measures:
            member = spec["member"]
        if member is None and view == "product_pnl" and name.startswith("pl_") and f"product_profit.{name[3:]}" in measures:
            member = f"product_profit.{name[3:]}"
        if member is None and view == "product":
            n = PRODUCT_RENAME.get(mid, name)
            if f"product_profit.{n}" in measures:
                member = f"product_profit.{n}"
        assert member and member in measures, f"v2 id {mid} ({spec['member']}) has no v3 member"
        real[mid] = member
    # two ids on one member -> one real id, the rest same-axis aliases
    claim = collections.defaultdict(list)
    for mid, member in real.items():
        claim[member].append(mid)
    for member, ids in claim.items():
        if len(ids) < 2:
            continue
        pick = next((i for i in ids if i in CANON_IDS), None) or next((i for i in ids if i.startswith(CANON_PREFIX)), None)
        assert pick, f"{member}: no canonical id among {ids}"
        for i in ids:
            if i != pick:
                aliases[i] = {"metric": pick, "date_axis": None, "reason": f"same measure as {pick} in v3 ({member})"}
                del real[i]
    used = set(real.values())
    new: dict[str, str] = dict(EXPLICIT_NEW)
    for nid, member in EXPLICIT_NEW.items():
        assert member in measures and member not in used and nid not in v2, (nid, member)
    used |= set(EXPLICIT_NEW.values())
    # base of every alias must be a real (or fixed new) id
    for mid, a in aliases.items():
        assert a["metric"] in real or a["metric"] in new, f"alias {mid} -> {a['metric']} is not a v3 metric"
    taken = set(v2) | set(aliases) | set(new)
    for member in sorted(measures):
        if member in used:
            continue
        view, name = member.split(".", 1)
        if view in ("paid_media_breakdowns", *HOURLY_HOMES):
            # a binding of the home metric of the same name (hourly / breakdown home of the same number)
            home = HOURLY_HOMES.get(view, "paid_media")
            assert f"{home}.{name}" in measures, f"{member}: no {home} measure to bind to"
            continue
        cand = name
        if cand in taken:
            cand = NEW_ID_PREFIX.get(view, f"{view}_") + name
        assert cand not in taken and cand not in new, f"new id collision: {member} -> {cand}"
        new[cand] = member
        taken.add(cand)
    return real, aliases, new


def main() -> int:
    idmap = ry(ID_MAP_V2)
    cap = ry(CAPABILITY_MAP)["metrics"]
    meta = cube_meta()
    views_meta = {c["name"]: c for c in meta["cubes"] if c.get("type") == "view"}
    assert set(views_meta) == set(VIEW_INFO), set(views_meta) ^ set(VIEW_INFO)
    measures = {m["name"]: m for vm in views_meta.values() for m in vm["measures"]}
    v1_metrics = {ry(p)["id"]: ry(p) for p in (V1 / "metrics").glob("*.yaml")}
    v1_views = {v["name"]: v for v in ry(V1 / "views.yaml")["views"]}
    v1_dims = {}
    for p in (V1 / "dimensions").glob("*.yaml"):
        for d in (ry(p) or {}).get("dimensions") or []:
            v1_dims.setdefault(d["id"], d)

    maps = idmap["maps"]
    by_new = collections.defaultdict(list)
    for m in maps:
        by_new[m["new"]].append(m)
    retired_none = {r["old"]: r for r in idmap.get("retired_without_replacement") or []}
    v2 = idmap["v2_metrics"]
    real, aliases, new = resolve_ids(measures, v2, cap)
    member_of = {**real, **new}  # every v3 metric id -> view.measure
    unbound = set(measures) - set(member_of.values())
    assert all(m.split(".")[0] in ("paid_media_breakdowns", *HOURLY_HOMES) for m in unbound), unbound

    if OUT.exists():
        shutil.rmtree(OUT)
    hdr = "# GENERATED by scripts/build_catalogue_v3.py — edit the generator / concepts source, not this file.\n"

    # ---- views
    views_doc = []
    for name, (category, grain, v1_view, dt, _owner, default_axis) in VIEW_INFO.items():
        vm = views_meta[name]
        time_dims = [d["name"].split(".", 1)[1] for d in vm["dimensions"] if d["type"] == "time"]
        assert default_axis in time_dims, (name, default_axis, time_dims)
        if dt:
            assert dt in time_dims, (name, dt, time_dims)
        old = v1_views.get(v1_view) or {}
        cadence = ((old.get("freshness") or {}).get("expected_cadence")) or "hourly after gold sync, IST"
        if name == "paid_media_changes":
            cadence = "event-driven (rows only when an ad entity changes)"
        if name == "web_funnel_hourly":
            cadence = "hourly after the orchestrator's dw/mart stage (about 1 h behind; paused UTC 19-21 = 00:30-03:30 IST), IST"
        if name in ("finance", "product_profit", "commerce", "product_ads", "returns", "payments", "customers"):
            cadence = "every 5 minutes after gold (semantic v3 tables), IST"
        views_doc.append({
            "name": name,
            "title": vm.get("title") or name,
            "date_dimension": default_axis,
            **({"datetime_dimension": dt} if dt else {}),
            "freshness": {"source": f"Cube v3 view {name} (serve.* via cube_serve)", "expected_cadence": cadence},
            "description": " ".join((vm.get("description") or "").split()),
        })
    wy(OUT / "views.yaml", {"views": views_doc}, hdr)
    default_axis_of = {v["name"]: v["date_dimension"] for v in views_doc}

    # ---- dimensions (one id per conformed member name; a few renamed where meaning differs)
    dims: dict[str, dict] = {}
    for vname, vm in views_meta.items():
        for d in vm["dimensions"]:
            member = d["name"]
            short = member.split(".", 1)[1]
            did = DIM_RENAME.get((vname, short), short)
            entry = dims.setdefault(did, {"id": did, "display_name": d.get("shortTitle") or human(did),
                                          "description": " ".join((d.get("description") or "").split()),
                                          "is_time": d["type"] == "time", "views": {}, "aliases": []})
            entry["views"][vname] = member
    for did, label in DIM_DISPLAY.items():
        if did in dims:
            dims[did]["display_name"] = label
    for did, names in DIM_V2_ALIAS.items():
        if did in dims:
            dims[did]["aliases"] = sorted({*dims[did]["aliases"], *names})
    for vname, vm in views_meta.items():
        by_source: dict[str, list[str]] = {}
        for d in vm["dimensions"]:
            by_source.setdefault(d.get("aliasMember") or "", []).append(d["name"].split(".", 1)[1])
        for d in vm["dimensions"]:
            key = (d.get("meta") or {}).get("stable_key")
            source = d.get("aliasMember") or ""
            if not key or "." not in source:
                continue
            short = d["name"].split(".", 1)[1]
            members = by_source.get(f"{source.split('.', 1)[0]}.{key}") or []
            if len(members) != 1:
                continue
            did = DIM_RENAME.get((vname, short), short)
            dims[did].setdefault("stable_key_by_view", {})[vname] = DIM_RENAME.get((vname, members[0]), members[0])
    for did, entry in dims.items():
        by_view = entry.pop("stable_key_by_view", None)
        if by_view:
            keys = sorted(by_view.values(), key=lambda k: (-list(by_view.values()).count(k), k))
            entry["stable_key"] = keys[0]
    for did, entry in dims.items():
        old = v1_dims.get(V1_DIM_ALIASES.get(did, did)) or v1_dims.get(did)
        if old:
            entry["aliases"] = [a for a in (old.get("aliases") or []) if a != did]
            if old.get("allowed_values") and did not in ALLOWED:
                entry["allowed_values"] = old["allowed_values"]
        if did in ALLOWED:
            entry["allowed_values"] = ALLOWED[did]
        if did in DIM_DESC:
            entry["description"] = DIM_DESC[did]
        if did in DIM_VALID_FOR:
            entry["valid_for"] = DIM_VALID_FOR[did]
        if did in DIM_NOTES:
            entry["coverage_note"] = DIM_NOTES[did]
        family = next((f for f, members in FAMILIES.items() if did in members), None)
        if family:
            entry["family"] = family
            entry["family_rank"] = FAMILIES[family].index(did)
        if not entry["description"]:
            entry["description"] = entry["display_name"]
        if set(entry["views"]) == {"paid_media_breakdowns"} and did != "brand_id":
            slices = BREAKDOWN_SLICES.get(did)
            where = (f"breakdown_type {', '.join(slices)}" if slices else
                     "never populated — not answerable" if slices == [] else "one breakdown_type per query")
            entry["description"] = (f"Meta audience breakdown ({where}); Meta delivery only — Google reports no "
                                    f"audience breakdowns. {entry['description']}").strip()
    wy(OUT / "dimensions" / "core.yaml", {"dimensions": sorted(dims.values(), key=lambda x: x["id"])}, hdr)

    def dim_id(view: str, member: str) -> str:
        short = member.split(".", 1)[1]
        return DIM_RENAME.get((view, short), short)

    # ---- hierarchies (from Cube) + the cross-cube ad drill path
    hier: dict[str, dict] = {}
    for vname, vm in views_meta.items():
        for h in vm.get("hierarchies") or []:
            hid = h["name"].split(".", 1)[1]
            levels = [dim_id(vname, lv) for lv in h["levels"]]
            e = hier.setdefault(hid, {"display_name": hid.replace("_", " ").capitalize(), "levels": levels, "views": []})
            assert e["levels"] == levels, (hid, e["levels"], levels)
            e["views"].append(vname)
    for hid, levels, note in (
        ("ad", ["ad_platform", "campaign_name", "adset_name", "ad_name"], "Cross-cube drill path (fact → ad dims)."),
        ("campaign", ["campaign_name", "adset_name", "ad_name"], "Campaign drill (orders / sessions / refunds by "
         "their last-touch campaign; ad delivery and P&L by their own)."),
    ):
        vs = [v for v in views_meta if all(v in dims.get(lv, {}).get("views", {}) for lv in levels)]
        hier[hid] = {"display_name": hid.capitalize(), "levels": levels, "views": vs, "note": note}
    wy(OUT / "hierarchies.yaml", {"hierarchies": hier}, hdr)

    # ---- metrics
    concept_src = ry(SRC / "concepts.yaml")
    concept_rows = concept_src["concepts"]
    view_dims = {v: {dim_id(v, d["name"]) for d in vm["dimensions"]} for v, vm in views_meta.items()}
    id_of_member = {member: mid for mid, member in member_of.items()}
    v3_family: dict[str, str] = {}
    for mid in sorted(member_of):
        member = member_of[mid]
        view, mname = member.split(".", 1)
        cm = measures[member]
        mmeta = cm.get("meta") or {}
        category, grain, _v1view, _dt, owner, default_axis = VIEW_INFO[view]
        ratio = (cm.get("aggType") or cm.get("type")) == "number"
        fmt = cm.get("format")
        unit = "INR" if fmt == "currency" else ("ratio" if ratio else "count")
        if mid.startswith("avg_seconds"):
            unit = "seconds"
        olds = by_new.get(mid, [])
        replaces = sorted({m["old"] for m in olds if m["old"] != mid and m["old"] not in v2})
        formerly = sorted({a for a, spec in aliases.items() if spec["metric"] == mid})
        roles = sorted({r for m in olds for r in ((v1_metrics.get(m["old"]) or {}).get("access_policy") or {}).get("roles_allowed", [])})
        owners = collections.Counter((v1_metrics.get(m["old"]) or {}).get("data_owner") for m in olds if m["old"] in v1_metrics)
        desc = " ".join((cm.get("description") or "").split())
        label = human(mid)
        if mmeta.get("allocated") and "allocated" not in label.lower():
            label += " (allocated)"
        capdoc = cap.get(mid) or {}
        # Cube declares meta.date_axis on the date-aware views (finance, commerce, product_*); every other view
        # (ads, web, attribution, customers, cash) runs on its own clock = native (D-36). CAPABILITY_MAP agrees.
        axis_class = mmeta.get("date_axis") or ("native" if view not in AXIS_VIEWS else None)
        assert axis_class in ("switchable", "placement_only", "native", "cohort_only"), (member, axis_class)
        if capdoc.get("date_axis") not in (None, "alias") and mid in v2 and view in AXIS_VIEWS:
            assert capdoc["date_axis"] == axis_class or (axis_class == "cohort_only" and capdoc["date_axis"] in ("cohort_only", "native")), \
                f"{mid}: Cube says {axis_class}, CAPABILITY_MAP says {capdoc['date_axis']}"
        refine = capdoc.get("target") == BUMP_MAJOR_FOR
        decisions = list(dict.fromkeys(list(mmeta.get("decisions") or []) + list(capdoc.get("decision_ids") or [])))
        axis_note = f"Grain: {grain}. Date axis: {axis_class.replace('_', ' ')}."
        if axis_class in ("switchable", "placement_only"):
            axis_note += " date_axis=order_date (default, orders placed in the period) or event_date (outcomes on the day they happen)."
        elif axis_class == "native":
            axis_note += " Ignores date_axis (its own clock, D-36)."
        else:
            axis_note += " Order-date cohort only (refuses event_date)."
        if refine:
            axis_note += (f" Redefined in semantic v3 ({'; '.join(decisions[:8])}"
                          f"{'…' if len(decisions) > 8 else ''}): the id is kept, the number may change.")
        if formerly:
            axis_note += f" Also answers the ids: {', '.join(formerly)}."
        composition = []
        for term in mmeta.get("composition") or []:
            part = id_of_member.get(f"{view}.{term['member']}")
            assert part is not None, f"{mid}: composition member {view}.{term['member']} has no catalogue id"
            composition.append({"metric": part, "sign": int(term["sign"])})
        canon = lambda i: aliases[i]["metric"] if i in aliases and aliases[i]["date_axis"] is None else i  # noqa: E731
        parts = [canon(d) for d in RATIO_PARTS.get(mid, [])]
        depends_on = [d for d in dict.fromkeys(parts) if d in member_of and d != mid] or [t["metric"] for t in composition]
        major = int((capdoc.get("version") or "1.0.0").split(".")[0])
        version = f"{major + 1}.0.0" if refine else (capdoc.get("version") or "1.0.0")
        doc = {
            "id": mid,
            "display_name": label,
            "category": category,
            "status": "broken" if mid in UNAVAILABLE else ("approved" if mid in NOT_CERTIFIED else "certified"),
            "description": f"{desc} {axis_note}".strip(),
            "formula": {"human_readable": desc or human(mid), "authoritative_source": "cube",
                        "depends_on": depends_on, **({"composition": composition} if composition else {})},
            "cube_mapping": {"view": view, "measure": member},
            "aggregation": "ratio" if ratio else "additive",
            **({"ratio_components": {"numerator": RATIO_PARTS[mid][0], "denominator": RATIO_PARTS[mid][1],
                                     **({"multiplier": RATIO_MULTIPLIERS[mid]} if mid in RATIO_MULTIPLIERS else {})}}
               if ratio and len(RATIO_PARTS.get(mid, [])) == 2 and all(p in member_of for p in RATIO_PARTS[mid]) else {}),
            "unit": unit,
            **({"currency_default": "INR"} if fmt == "currency" else {}),
            "grain": grain,
            "data_owner": (owners.most_common(1)[0][0] if owners else owner) or owner,
            "access_policy": {"roles_allowed": roles or ["exec", "finance", "growth_lead", "analyst", "ops"],
                              "scopes": ["metrics:read"]},
            "version": version,
            "replaces": replaces,
            "date_axis": axis_class,
        }
        if axis_class in ("switchable", "placement_only"):
            td = {n: f"{view}.{n}" for n in ("order_date", "event_date") if n in {d["name"].split(".", 1)[1] for d in views_meta[view]["dimensions"]}}
            if set(td) == {"order_date", "event_date"}:
                doc["date_axis_dimensions"] = td
            else:  # a placement-only measure on a view with one clock (customers): the axis cannot change it
                assert axis_class == "placement_only", (member, axis_class, td)
                doc["date_axis"] = "native"
        if axis_class == "cohort_only":
            assert mmeta.get("refusal"), f"{member}: cohort_only without a refusal text"
            doc["date_axis_refusal"] = mmeta["refusal"]
        if mmeta.get("ratio_contract"):
            doc["ratio_contract"] = mmeta["ratio_contract"]
        if mmeta.get("hidden_by_default"):
            doc["hidden_by_default"] = True
        if mmeta.get("decisions"):
            doc["decision_ids"] = list(mmeta["decisions"])
        if mid in UNAVAILABLE:
            doc["unavailable_reason"] = UNAVAILABLE[mid]
        if split := mmeta.get("split_by"):
            doc["split_by"] = dim_id(view, f"{view}.{split}")
        excluded = dict(EXCLUDED_DIMS.get(mid, {}))
        if excluded:
            doc["excluded_dimensions"] = excluded
        valid_for: dict[str, list[str]] = {}
        reasons = []
        if mid in META_ONLY:
            valid_for["ad_platform"] = ["meta"]
            reasons.append("Meta-only measure (Google reports no such field).")
        if valid_for:
            doc["valid_for"] = valid_for
            doc["valid_for_reason"] = " ".join(reasons)
        bindings = []
        twin = next((h for h, home in HOURLY_HOMES.items() if home == view and f"{h}.{mname}" in measures), None)
        if twin:
            bindings.append({"name": "hourly", "view": twin, "measure": f"{twin}.{mname}", "granularities": ["hour"]})
        if view == "paid_media" and f"paid_media_breakdowns.{mname}" in measures:
            bindings.append({"name": "breakdowns", "view": "paid_media_breakdowns",
                             "measure": f"paid_media_breakdowns.{mname}", "dimensions": BREAKDOWN_DIMS,
                             "slice_dimension": "breakdown_type",
                             "slices": {d: [x for x in v if x not in SLICE_GAPS.get(mname, ())]
                                        for d, v in BREAKDOWN_SLICES.items()},
                             "note": "Meta only: Google reports no audience breakdowns, so Google delivery "
                                     "is not in these rows."})
        if bindings:
            doc["bindings"] = bindings
        wy(OUT / "metrics" / f"{mid}.yaml", doc, hdr)

    # ---- aliases: ids that answer through a base metric (event-date family, same-measure twins)
    alias_rows = []
    for aid, a in sorted(aliases.items()):
        alias_rows.append({"id": aid, "metric": a["metric"], **({"date_axis": a["date_axis"]} if a["date_axis"] else {}),
                           **({"refused": a["refused"]} if a.get("refused") else {}), "reason": a["reason"]})
    wy(OUT / "metric_aliases.yaml", {"metric_aliases": alias_rows}, hdr)

    # ---- hard cut: every v1 id -> its v3 id (alias ids stay valid answers)
    deps = []
    for m in maps:
        if m["old"] == m["new"]:
            continue
        flt = {k.split(".", 1)[1]: v for k, v in (m.get("filters") or {}).items()}
        reason = m.get("known_diff") or m.get("note") or "Semantic v2: one id per number; scope / platform are filters."
        deps.append({"old": m["old"], "new": m["new"], **({"filters": flt} if flt else {}), "reason": reason})
    for old, r in sorted(retired_none.items()):
        deps.append({"old": old, "new": "(none)", "reason": r["reason"]})
    wy(OUT / "deprecations.yaml", {"deprecations": deps},
       hdr + "# Hard cut: every v1 metric id -> its metric (+ filters). Old ids are REJECTED with an error naming the\n"
             "# replacement (catalogue.retired), never silently mapped. A replacement may be a v3 alias id.\n")

    # ---- glossary: v1 terms remapped through the id map (filters ride along)
    old_to = {m["old"]: m for m in maps}
    retired_ids = sorted((m["old"] for m in maps if m["old"] != m["new"] and m["old"] not in v2), key=len, reverse=True)
    id_re = re.compile(r"\b(" + "|".join(map(re.escape, retired_ids)) + r")\b")

    from seleric_mcp.catalogue_service.service import _AXIS_KEYWORDS_V3 as _AXIS_KEYWORDS_V2  # noqa: E402

    finance_phrases = [kw for kw, val in _AXIS_KEYWORDS_V2["date"] if val == "finance"]
    channel_words = _AXIS_KEYWORDS_V2["channel"]
    channel_axis_filters = concept_src["_channel_filters"]["channel"]
    all_ids = set(member_of) | set(aliases)

    def channel_filter(term: str, metric_id: str) -> dict | None:
        low = f" {term.lower()} "
        value = next((v for kw, v in channel_words if f" {kw} " in low), None)
        if value is None or value not in channel_axis_filters or metric_id not in member_of:
            return None
        view = member_of[canon_id(metric_id)].split(".", 1)[0]
        flt = dict(channel_axis_filters[value])
        return flt if all(view in dims.get(d, {}).get("views", {}) for d in flt) else None

    def canon_id(mid: str) -> str:
        return aliases[mid]["metric"] if mid in aliases else mid

    def finance_explicit(term: str) -> bool:
        low = f" {term.lower()} "
        return any(f" {kw} " in low or low.strip().startswith(kw) for kw in finance_phrases)

    def modernize(text: str | None) -> str | None:
        return id_re.sub(lambda mo: old_to[mo.group(1)]["new"], text) if text else text

    terms, dropped = [], []
    for t in ry(V1 / "glossary" / "terms.yaml")["terms"]:
        t = dict(t)
        if t.get("definition"):
            t["definition"] = " ".join(modernize(t["definition"]).replace(" (include reconciliation rows)", "").split())
        cid, did = t.get("canonical_id"), t.get("canonical_dimension_id")
        if cid in GLOSSARY_REDIRECT:
            cid = t["canonical_id"] = GLOSSARY_REDIRECT[cid]
        if t.get("definition"):
            for old, newid in GLOSSARY_REDIRECT.items():
                t["definition"] = " ".join(
                    newid + w[len(old):] if w.rstrip(".,;:)") == old else w for w in t["definition"].split(" "))
        if cid:
            if cid in old_to:
                m = old_to[cid]
                t["canonical_id"] = m["new"]
                flt = {k.split(".", 1)[1]: v for k, v in (m.get("filters") or {}).items()}
                if flt:
                    t["filter"] = flt
            elif cid in all_ids:
                pass
            else:
                dropped.append((t["term"], cid)); continue
            newid = t["canonical_id"]
            # order date is the default; the event-date alias only when the term itself asks for it
            if newid.startswith("pnl_") and newid[4:] in all_ids and not finance_explicit(t["term"]):
                t["canonical_id"] = newid[4:]
            if not t.get("filter"):
                flt = channel_filter(t["term"], t["canonical_id"])
                if flt:
                    t["filter"] = flt
        if did:
            new_did = did if did in dims else next((k for k, v in V1_DIM_ALIASES.items() if v == did and k in dims), None)
            if new_did is None:
                if not cid:
                    dropped.append((t["term"], did)); continue
                t.pop("canonical_dimension_id")
            else:
                t["canonical_dimension_id"] = new_did
        terms.append(t)
    wy(OUT / "glossary" / "terms.yaml", {"terms": terms}, hdr)

    # ---- concepts: hand-maintained for v3 (catalogue_v3_src/concepts.yaml)
    plain_alias = {i: a["metric"] for i, a in aliases.items() if a["date_axis"] is None}
    for c in concept_src["concepts"]:
        for r in c.get("resolves") or []:
            r["metric"] = plain_alias.get(r["metric"], r["metric"])  # same-measure twins resolve to the real id
            assert r["metric"] in member_of, (c["id"], r["metric"])  # no row may name an event-date alias (D-28)
        wy(OUT / "concepts" / f"{c['id']}.yaml", c, hdr)
    wy(OUT / "aliases.yaml", {"aliases": concept_src.get("aliases") or {}}, hdr)

    # ---- brands, modules, manifest
    shutil.copy(V1 / "brands.yaml", OUT / "brands.yaml")
    wy(OUT / "modules.yaml", {"modules": {
        mid: {"display_name": spec.get("display_name", mid), "description": spec.get("description", ""),
              "extra_views": MODULES[mid]}
        for mid, spec in (ry(V1 / "modules.yaml").get("modules") or {}).items() if mid in MODULES}}, hdr)
    meta_hash = hashlib.sha256(json.dumps(views_meta, sort_keys=True).encode()).hexdigest()[:12]
    wy(OUT / "catalogue.yaml", {"semantic_version": 3,
                                "cube_url_default": "http://cube-v3:4000",
                                "generated_from": {"id_map": hashlib.sha256(ID_MAP_V2.read_bytes()).hexdigest()[:12],
                                                   "capability_map": hashlib.sha256(CAPABILITY_MAP.read_bytes()).hexdigest()[:12],
                                                   "cube_v3_meta": meta_hash}}, hdr)

    # ---- generated record of the id resolution
    wy(ID_MAP_V3, {
        "version": 1, "generated_by": "scripts/build_catalogue_v3.py",
        "description": "Semantic v3: every v2 metric id -> the v3 Cube member that answers it, or the base metric "
                       "(+ date axis) it is an alias of. New ids are v3 measures with no v2 id.",
        "metrics": {**{i: {"member": m} for i, m in sorted(real.items())},
                    **{i: {"alias_of": a["metric"], **({"date_axis": a["date_axis"]} if a["date_axis"] else {}),
                           **({"refused": a["refused"]} if a.get("refused") else {})} for i, a in sorted(aliases.items())}},
        "new": {i: {"member": m} for i, m in sorted(new.items())},
    }, "# GENERATED by scripts/build_catalogue_v3.py\n")

    # ---- load it back through the real loader (integrity check) and report
    sys.path.insert(0, str(ROOT / "src"))
    from seleric_mcp.catalogue_service.loader import load_catalogue  # noqa: E402
    cat = load_catalogue(OUT)
    print(f"catalogue_v3 OK (version {cat.version}): {len(cat.metrics)} metrics, {len(aliases)} aliases, "
          f"{len(cat.views)} views, {len(cat.dimensions)} dimensions, {len(cat.hierarchies)} hierarchies, "
          f"{len(cat.glossary)} glossary terms, {len(cat.concepts)} concepts, {len(cat.retired)} retired ids")
    print(f"v2 ids: {len(v2)} = {len(real)} real + {len(aliases)} aliases; new v3 ids: {len(new)}")
    if dropped:
        print(f"glossary terms dropped (target retired without replacement): {len(dropped)}: "
              + ", ".join(f"{t}→{c}" for t, c in dropped[:12]) + (" …" if len(dropped) > 12 else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
