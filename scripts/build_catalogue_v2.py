#!/usr/bin/env python3
"""Generate catalogue_v2/ (semantic v2) from the v2 id map + the live Cube v2 model.

Inputs
  catalogue/migrations/v2_id_map.yaml   v2 metrics (view member + date axis), old id -> new id + filters
  Cube v2 /v1/meta (CUBE_V2_URL)        views, measures (type / format / description), dimensions,
                                        hierarchies — the single source of what is queryable
  catalogue/ (v1)                       brands, dimension aliases / allowed values, glossary, concepts,
                                        concept aliases, access policies, view cadences — remapped
Output
  catalogue_v2/                         loaded by the MCP when SELERIC_CATALOGUE_DIR=catalogue_v2

  uv run python scripts/build_catalogue_v2.py            # (re)generate, then load + integrity-check it
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
OUT = ROOT / "catalogue_v2"
ID_MAP = V1 / "migrations" / "v2_id_map.yaml"
CUBE_V2_URL = os.environ.get("CUBE_V2_URL", "http://127.0.0.1:4002")
CUBE_ENV = Path(os.environ.get("SELERIC_CUBE_ENV", "/opt/seleric/mage-ai/infra/cube/.env"))

# ---------------------------------------------------------------- hand-maintained choices
# view -> (category, grain, v1 analogue view for cadence / description, datetime dimension, owner)
VIEW_INFO = {
    "commerce": ("commerce", "order", "commerce_orders", "order_created_at", "Commerce Ops"),
    "product": ("product", "order_line", "product_performance", None, "Commerce Ops"),
    "paid_media": ("paid_media", "ad_day", "meta_ad_performance", None, "Growth / Paid Media"),
    "paid_media_hourly": ("paid_media", "ad_hour", "meta_ad_performance_hourly", "report_hour", "Growth / Paid Media"),
    "paid_media_breakdowns": ("paid_media", "ad_day_breakdown", "meta_ad_breakdown_performance", None, "Growth / Paid Media"),
    "paid_media_changes": ("paid_media", "change_event", "meta_ads_status_history", None, "Growth / Paid Media"),
    "web_sessions": ("web_analytics", "session", "session_funnel", "session_start", "Growth / Web Analytics"),
    "web_funnel": ("web_analytics", "brand_day", "funnel_daily", None, "Growth / Web Analytics"),
    "web_events": ("web_analytics", "day_event_type", "web_events_daily", None, "Growth / Web Analytics"),
    "web_event_detail": ("web_analytics", "event", "web_events", "event_timestamp", "Growth / Web Analytics"),
    "attribution": ("attribution", "order_touch", "touchpoints", "touch_ts", "Growth / Attribution"),
    "attribution_paths": ("attribution", "order_model", "attribution_paths", None, "Growth / Attribution"),
    "customers": ("customer", "customer", "customer_ltv", None, "Growth / CRM"),
    "unit_economics": ("customer", "brand_day", "ltv_cac", None, "Growth / CRM"),
    "returns": ("operations", "refund", "refund_events", None, "Commerce Ops"),
    "payments": ("operations", "transaction", "payments", None, "Finance"),
    "pnl": ("finance", "brand_day_channel_ad", "canonical_pnl", None, "Finance"),
    # order-date P&L (decision 2026-10-04): every non-Finance domain reads the order date
    "order_pnl": ("finance", "brand_order_day_channel_ad", "channel_pnl", None, "Growth / Finance"),
}
MODULES = {  # v1 module id -> v2 views (no ontology in v2; extra_views is the scope)
    "webanalytics": ["web_sessions", "web_funnel", "web_events", "web_event_detail"],
    "commerce": ["commerce", "returns", "payments"],
    "product": ["product"],
    "paidmedia": ["paid_media", "paid_media_hourly", "paid_media_breakdowns", "paid_media_changes", "order_pnl"],
    "attribution": ["attribution", "attribution_paths", "commerce"],
    "customer": ["customers", "unit_economics", "commerce", "order_pnl"],
    "finance": ["pnl", "order_pnl"],
    "operations": ["returns", "payments"],
}
META_ONLY = {"landing_page_views", "cost_per_landing_page_view", "video_completion_rate", "thruplays",
             "hook_rate", "hold_rate_15s"}
HOURLY = ["ad_spend", "impressions", "clicks", "link_clicks", "landing_page_views", "ctr", "cpc", "cpm"]
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
SLICE_GAPS = {"landing_page_views": {"region"}}
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
# The funnel mart's session rows use session channel labels (organic / other / google_free_listing: 39 % of brand 20 Sep
# sessions) that carry no platform, so its bounce rate by platform would park them under an empty label; the
# session-grain twin classifies every session. Its purchases sit on the order's last-touch channel (all mapped).
_SESSION_LABELS = "Funnel sessions carry session channel labels with no platform; use the session twin session_bounce_rate."
EXCLUDED_DIMS["bounce_rate"] = {d: _SESSION_LABELS for d in ("platform", "finance_channel", "ad_platform", "is_paid",
                                                             "medium_group", "channel")}
NOT_CERTIFIED = {"link_clicks", "thruplays", "hook_rate", "hold_rate_15s", "cost_per_link_click"}  # approved
# kept id, number changed (event → order date). The P&L ids came back on 2026-10-04 as the ORDER-date twins of
# pnl_* (decision: every non-Finance domain reads the order date); their v1 event-date meaning is pnl_<id>.
ORDER_DATE_PNL = ["net_profit", "net_cogs", "product_cost", "shipping_cost", "packaging_cost", "payment_gateway_fees",
                  "rto_cost", "operating_cost", "contribution_margin", "contribution_margin_pct", "net_margin_pct",
                  "gross_profit", "taxes_on_net_sales", "mer", "gross_roas", "net_roas", "be_roas", "gross_cogs"]
REDEFINED = {"return_revenue": "2.0.0", "cancel_revenue": "2.0.0",
             **{m: "2.0.0" for m in ORDER_DATE_PNL if m != "gross_cogs"}}
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
    "product_gross_roas": ["product_attributed_revenue", "product_ad_spend"], "product_net_roas": ["product_ad_spend"],
    "product_mer": ["product_ad_spend"],
    "channel_cac": ["ad_spend", "channel_new_customers"], "cost_per_order": ["ad_spend", "channel_orders"],
    "session_bounce_rate": ["sessions"], "pnl_contribution_margin_pct": ["pnl_contribution_margin", "pnl_net_sales"],
    "pnl_net_margin_pct": ["pnl_net_profit", "pnl_net_sales"], "pnl_mer": ["pnl_net_sales", "ad_spend"],
    "pnl_gross_roas": ["gross_sales", "ad_spend"], "pnl_net_roas": ["pnl_contribution_margin", "ad_spend"],
    "pnl_be_roas": ["pnl_net_sales", "pnl_contribution_margin"],
    # order-date P&L: its net sales (P&L basis) = contribution_margin + net_cogs on the same view
    "contribution_margin_pct": ["contribution_margin", "net_cogs"],
    "net_margin_pct": ["net_profit", "contribution_margin", "net_cogs"],
    "mer": ["contribution_margin", "net_cogs", "ad_spend"], "gross_roas": ["gross_sales", "ad_spend"],
    "net_roas": ["contribution_margin", "ad_spend"], "be_roas": ["contribution_margin", "net_cogs"],
}
DIM_RENAME = {("commerce", "event_type"): "order_event_type",  # same member name, different meaning
              ("order_pnl", "row_type"): "order_pnl_row_type"}  # orders | ad_spend, not pnl's detail | reconciliation
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
    # or added it) and customer (first_order_*: the first order's product) views — see PROXY_OF
    "product": ["product_title", "basket_product_title", "viewed_product_title", "first_order_product_title"],
    "sku": ["sku", "basket_sku", "viewed_sku", "first_order_sku"],
    "product_type": ["product_type", "basket_product_type", "first_order_product_type"],
    "variant": ["variant_title", "basket_variant_title"],
}
# Proxy members: the product vocabulary with another meaning — the orders / sessions / customers that contain, viewed
# or first bought the product (Cube counts each order / session once under every product in it). They answer a
# product slice for a measure with no product grain (AOV, COD orders, conversion rate, repeat rate by product); a
# measure whose catalogue grain twin carries the real member answers there instead (net sales by product = line
# revenue, not the revenue of the orders containing it), so the proxy is excluded from it.
PROXY_OF = {"basket_product_title": "product_title", "viewed_product_title": "product_title",
            "first_order_product_title": "product_title", "basket_sku": "sku", "viewed_sku": "sku",
            "first_order_sku": "sku", "basket_product_type": "product_type",
            "first_order_product_type": "product_type", "basket_variant_title": "variant_title"}


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
           "ad_platform": ["meta", "google"], "sales_channel": ["shopify", "amazon"],
           "row_type": ["detail", "reconciliation"], "order_pnl_row_type": ["orders", "ad_spend"],
           "medium_group": ["paid", "owned", "earned", "none", "unclassified"]}
# v1 glossary ids that are live v2 ids again but whose v1 TERMS read best on another metric: "orders by channel" /
# "meta orders" need the full traffic hierarchy (commerce.orders carries platform → channel → sub_channel; the
# order-date channel P&L has no channel). channel_orders stays reachable by id and via the cac concept's grain.
GLOSSARY_REDIRECT = {"channel_orders": "orders"}
SALES_CHANNEL_REASON = "Amazon is a reserved sales channel; it is not yet supported on the agent surface."
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
           "product_ad_spend": "Product ad spend (allocated)", "product_attributed_revenue": "Product ad-attributed revenue",
           "product_gross_roas": "Product gross ROAS", "product_net_roas": "Product net ROAS", "product_mer": "Product MER",
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
    req = urllib.request.Request(f"{CUBE_V2_URL}/cubejs-api/v1/meta", headers={"Authorization": tok})
    return json.load(urllib.request.urlopen(req, timeout=60))


def ry(p: Path):
    return yaml.safe_load(p.read_text(encoding="utf-8"))


def wy(p: Path, doc, header: str = "") -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("w", encoding="utf-8") as fh:
        if header:
            fh.write(header)
        yaml.safe_dump(doc, fh, sort_keys=False, allow_unicode=True, width=110)


def main() -> int:
    idmap = ry(ID_MAP)
    meta = cube_meta()
    views_meta = {c["name"]: c for c in meta["cubes"] if c.get("type") == "view"}
    assert set(views_meta) == set(VIEW_INFO), set(views_meta) ^ set(VIEW_INFO)
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

    if OUT.exists():
        shutil.rmtree(OUT)
    hdr = "# GENERATED by scripts/build_catalogue_v2.py — edit the generator / id map, not this file.\n"

    # ---- views
    views_doc = []
    for name, (category, grain, v1_view, dt, _owner) in VIEW_INFO.items():
        vm = views_meta[name]
        time_dims = [d["name"].split(".", 1)[1] for d in vm["dimensions"] if d["type"] == "time"]
        default_axis = {"commerce": "order_date", "product": "order_date", "paid_media": "report_date",
                        "paid_media_hourly": "report_date", "paid_media_breakdowns": "report_date",
                        "paid_media_changes": "changed_at", "web_sessions": "session_date",
                        "web_funnel": "report_date", "web_events": "report_date", "web_event_detail": "event_date",
                        "attribution": "order_date", "attribution_paths": "order_date",
                        "customers": "last_order_at", "unit_economics": "report_date",
                        "returns": "refund_date", "payments": "transaction_date",
                        "pnl": "report_date", "order_pnl": "order_date"}[name]
        assert default_axis in time_dims, (name, default_axis, time_dims)
        if dt:
            assert dt in time_dims, (name, dt, time_dims)
        old = v1_views.get(v1_view) or {}
        cadence = ((old.get("freshness") or {}).get("expected_cadence")) or "hourly after gold sync, IST"
        if name == "paid_media_changes":
            cadence = "event-driven (rows only when an ad entity changes)"
        views_doc.append({
            "name": name,
            "title": vm.get("title") or name,
            "date_dimension": default_axis,
            **({"datetime_dimension": dt} if dt else {}),
            "freshness": {"source": f"Cube v2 view {name} (serve.* via cube_serve)", "expected_cadence": cadence},
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
    for did, entry in dims.items():
        old = v1_dims.get(V1_DIM_ALIASES.get(did, did)) or v1_dims.get(did)
        if old:
            entry["aliases"] = [a for a in (old.get("aliases") or []) if a != did]
            if old.get("allowed_values") and did not in ALLOWED:
                entry["allowed_values"] = old["allowed_values"]
        if did in ALLOWED:
            entry["allowed_values"] = ALLOWED[did]
        family = next((f for f, members in FAMILIES.items() if did in members), None)
        if family:
            entry["family"] = family
            entry["family_rank"] = FAMILIES[family].index(did)
        if did == "sales_channel":
            entry["unsupported_values"] = {"amazon": SALES_CHANNEL_REASON}
        if not entry["description"]:
            entry["description"] = entry["display_name"]
        if set(entry["views"]) == {"paid_media_breakdowns"} and did != "brand_id":
            slices = BREAKDOWN_SLICES.get(did)
            where = (f"breakdown_type {', '.join(slices)}" if slices else
                     "never populated — not answerable" if slices == [] else "one breakdown_type per query")
            entry["description"] = (f"Meta audience breakdown ({where}); Meta delivery only — Google reports no "
                                    f"audience breakdowns. {entry['description']}").strip()
    # display names must be unique per dimension list for readability
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
        # every view carrying all levels: a campaign drill must work on paid_media too, not only where the
        # ad drill is missing (live: "Hierarchy 'campaign' is not available on view(s) paid_media")
        vs = [v for v in views_meta if all(v in dims.get(lv, {}).get("views", {}) for lv in levels)]
        hier[hid] = {"display_name": hid.capitalize(), "levels": levels, "views": vs, "note": note}
    wy(OUT / "hierarchies.yaml", {"hierarchies": hier}, hdr)

    # ---- metrics
    measures = {m["name"]: m for vm in views_meta.values() for m in vm["measures"]}
    v2 = idmap["v2_metrics"]
    concept_rows = ry(ROOT / "catalogue_v2_src" / "concepts.yaml")["concepts"]
    view_dims = {v: {dim_id(v, d["name"]) for d in vm["dimensions"]} for v, vm in views_meta.items()}
    for mid, spec in sorted(v2.items()):
        view, member = spec["member"].split(".", 1)
        cm = measures[spec["member"]]
        category, grain, _v1view, _dt, owner = VIEW_INFO[view]
        axis = spec["date_axis"].split(".", 1)[1]
        ratio = (cm.get("aggType") or cm.get("type")) == "number"  # meta "type" is "number" for every measure
        fmt = cm.get("format")
        unit = "INR" if fmt == "currency" else ("ratio" if ratio else "count")
        if mid.startswith("avg_seconds"):
            unit = "seconds"
        olds = by_new.get(mid, [])
        replaces = sorted({m["old"] for m in olds if m["old"] != mid and m["old"] not in v2})
        formerly = sorted({m["old"] for m in olds if m["old"] != mid and m["old"] in v2})
        roles = sorted({r for m in olds for r in ((v1_metrics.get(m["old"]) or {}).get("access_policy") or {}).get("roles_allowed", [])})
        owners = collections.Counter((v1_metrics.get(m["old"]) or {}).get("data_owner") for m in olds if m["old"] in v1_metrics)
        desc = " ".join((cm.get("description") or "").split())
        axis_note = f"Date axis: {axis} ({'event date' if mid.startswith('pnl_') else 'IST'}); grain: {grain}."
        if mid in REDEFINED:
            home = next((n for n, ms in by_new.items() if n != mid and any(m["old"] == mid for m in ms)), None)
            axis_note += (f" Redefined in semantic v2 (version {REDEFINED[mid]}): until v2 the id {mid} meant the "
                          f"event-date P&L number, now {home}.")
        if formerly:
            axis_note += f" Also the home of the v1 meaning of: {', '.join(formerly)}."
        doc = {
            "id": mid,
            "display_name": human(mid),
            "category": category,
            "status": "broken" if mid in UNAVAILABLE else ("approved" if mid in NOT_CERTIFIED else "certified"),
            "description": f"{desc} {axis_note}".strip(),
            "formula": {"human_readable": desc or human(mid), "authoritative_source": "cube",
                        "depends_on": [d for d in RATIO_PARTS.get(mid, []) if d in v2 and d != mid]},
            "cube_mapping": {"view": view, "measure": spec["member"],
                             **({"time_dimension": spec["date_axis"]} if axis != default_axis_of[view] else {})},
            "aggregation": "ratio" if ratio else "additive",
            "unit": unit,
            **({"currency_default": "INR"} if fmt == "currency" else {}),
            "grain": grain,
            "data_owner": (owners.most_common(1)[0][0] if owners else owner) or owner,
            "access_policy": {"roles_allowed": roles or ["exec", "finance", "growth_lead", "analyst", "ops"],
                              "scopes": ["metrics:read"]},
            "version": REDEFINED.get(mid, "1.0.0"),
            "replaces": replaces,
        }
        if mid in UNAVAILABLE:
            doc["unavailable_reason"] = UNAVAILABLE[mid]
        excluded = dict(EXCLUDED_DIMS.get(mid, {}))
        twin_views = {t: v2[t]["member"].split(".", 1)[0] for t in _grain_twins(concept_rows, mid) if t in v2}
        for proxy, real in PROXY_OF.items():
            twin = next((t for t, tv in twin_views.items() if real in view_dims[tv]), None)
            if proxy in view_dims[view] and twin is not None:
                excluded[proxy] = (f"{proxy} counts the whole order / session / customer under each product in it; "
                                   f"by {real} this measure is its grain twin {twin}.")
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
        if view == "paid_media" and member in HOURLY:
            bindings.append({"name": "hourly", "view": "paid_media_hourly", "measure": f"paid_media_hourly.{member}",
                             "granularities": ["hour"]})
            bindings.append({"name": "breakdowns", "view": "paid_media_breakdowns",
                             "measure": f"paid_media_breakdowns.{member}", "dimensions": BREAKDOWN_DIMS,
                             "slice_dimension": "breakdown_type",
                             "slices": {d: [x for x in v if x not in SLICE_GAPS.get(member, ())]
                                        for d, v in BREAKDOWN_SLICES.items()},
                             "note": "Meta only: Google reports no audience breakdowns, so Google delivery "
                                     "is not in these rows."})
        if bindings:
            doc["bindings"] = bindings
        wy(OUT / "metrics" / f"{mid}.yaml", doc, hdr)

    # ---- hard cut
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
       hdr + "# Semantic v2 hard cut: every v1 metric id -> its v2 metric (+ filters). Old ids are REJECTED\n"
             "# with an error naming the replacement (catalogue.retired), never silently mapped.\n")

    # ---- glossary: v1 terms remapped through the id map (filters ride along)
    old_to = {m["old"]: m for m in maps}
    retired_ids = sorted((m["old"] for m in maps if m["old"] != m["new"] and m["old"] not in v2), key=len, reverse=True)
    id_re = re.compile(r"\b(" + "|".join(map(re.escape, retired_ids)) + r")\b")

    from seleric_mcp.catalogue_service.service import _AXIS_KEYWORDS_V2

    finance_phrases = [kw for kw, val in _AXIS_KEYWORDS_V2["date"] if val == "finance"]

    channel_words = _AXIS_KEYWORDS_V2["channel"]
    concept_src = ry(ROOT / "catalogue_v2_src" / "concepts.yaml")
    channel_axis_filters = concept_src["_channel_filters"]["channel"]  # value -> {dimension: value}

    def channel_filter(term: str, metric_id: str) -> dict | None:
        low = f" {term.lower()} "
        value = next((v for kw, v in channel_words if f" {kw} " in low), None)
        if value is None or value not in channel_axis_filters or metric_id not in v2:
            return None
        view = v2[metric_id]["member"].split(".", 1)[0]
        flt = dict(channel_axis_filters[value])
        return flt if all(view in dims.get(d, {}).get("views", {}) for d in flt) else None

    def finance_explicit(term: str) -> bool:
        low = f" {term.lower()} "
        return any(f" {kw} " in low or low.strip().startswith(kw) for kw in finance_phrases)

    def modernize(text: str | None) -> str | None:
        return id_re.sub(lambda mo: old_to[mo.group(1)]["new"], text) if text else text

    terms, dropped = [], []
    for t in ry(V1 / "glossary" / "terms.yaml")["terms"]:
        t = dict(t)
        if t.get("definition"):
            t["definition"] = " ".join(modernize(t["definition"]).split())
        cid, did = t.get("canonical_id"), t.get("canonical_dimension_id")
        if cid in GLOSSARY_REDIRECT:
            cid = t["canonical_id"] = GLOSSARY_REDIRECT[cid]
        if t.get("definition"):
            for old, new in GLOSSARY_REDIRECT.items():
                t["definition"] = " ".join(
                    new + w[len(old):] if w.rstrip(".,;:)") == old else w for w in t["definition"].split(" "))
        if cid:
            if cid in old_to:
                m = old_to[cid]
                t["canonical_id"] = m["new"]
                flt = {k.split(".", 1)[1]: v for k, v in (m.get("filters") or {}).items()}
                if flt:
                    t["filter"] = flt
            elif cid in v2:
                pass
            else:
                dropped.append((t["term"], cid)); continue
            # Order date is the default for every domain; Finance (pnl_*) only when the term itself asks for
            # the event-date P&L (the resolver's own date-axis phrases — one source, no second list).
            new = t["canonical_id"]
            if new.startswith("pnl_") and new[4:] in v2 and not finance_explicit(t["term"]):
                t["canonical_id"] = new[4:]
            # A term that names a channel ("meta orders", "google roas") must carry that channel's filter: v1
            # encoded it in the id, v2 does not. Channel words = the resolver's channel axis; the filter = the
            # concepts' channel axis_filters; applied only where the metric's view carries that dimension.
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

    # ---- concepts: hand-authored for v2 (catalogue_v2_src/concepts.yaml) — the v1 resolution tables
    #      encoded scope / platform as metric ids, which v2 turns into axis_filters.
    src = ry(ROOT / "catalogue_v2_src" / "concepts.yaml")
    concept_drops = []
    for c in src["concepts"]:
        wy(OUT / "concepts" / f"{c['id']}.yaml", c, hdr)
    wy(OUT / "aliases.yaml", {"aliases": src.get("aliases") or {}}, hdr)

    # ---- brands, modules, manifest
    shutil.copy(V1 / "brands.yaml", OUT / "brands.yaml")
    wy(OUT / "modules.yaml", {"modules": {
        mid: {"display_name": spec.get("display_name", mid), "description": spec.get("description", ""),
              "extra_views": MODULES[mid]}
        for mid, spec in (ry(V1 / "modules.yaml").get("modules") or {}).items() if mid in MODULES}}, hdr)
    meta_hash = hashlib.sha256(json.dumps(views_meta, sort_keys=True).encode()).hexdigest()[:12]
    wy(OUT / "catalogue.yaml", {"semantic_version": 2,
                                "cube_url_default": "http://cube-v2:4000",
                                "generated_from": {"id_map": hashlib.sha256(ID_MAP.read_bytes()).hexdigest()[:12],
                                                   "cube_v2_meta": meta_hash}}, hdr)

    # ---- load it back through the real loader (integrity check) and report
    sys.path.insert(0, str(ROOT / "src"))
    from seleric_mcp.catalogue_service.loader import load_catalogue  # noqa: E402
    cat = load_catalogue(OUT)
    print(f"catalogue_v2 OK (version {cat.version}): {len(cat.metrics)} metrics, {len(cat.views)} views, "
          f"{len(cat.dimensions)} dimensions, {len(cat.hierarchies)} hierarchies, {len(cat.glossary)} glossary terms, "
          f"{len(cat.concepts)} concepts, {len(cat.retired)} retired ids")
    if dropped:
        print(f"glossary terms dropped (target retired without replacement): {len(dropped)}: "
              + ", ".join(f"{t}→{c}" for t, c in dropped[:12]) + (" …" if len(dropped) > 12 else ""))
    if concept_drops:
        print(f"concept rows dropped: {concept_drops}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
