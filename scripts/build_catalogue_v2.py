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
    "pnl_channel": ("finance", "brand_day_channel", "channel_pnl", None, "Finance"),
}
MODULES = {  # v1 module id -> v2 views (no ontology in v2; extra_views is the scope)
    "webanalytics": ["web_sessions", "web_funnel", "web_events", "web_event_detail"],
    "commerce": ["commerce", "returns", "payments"],
    "product": ["product"],
    "paidmedia": ["paid_media", "paid_media_hourly", "paid_media_breakdowns", "paid_media_changes"],
    "attribution": ["attribution", "attribution_paths", "commerce"],
    "customer": ["customers", "unit_economics", "commerce"],
    "finance": ["pnl", "pnl_channel"],
    "operations": ["returns", "payments"],
}
META_ONLY = {"landing_page_views", "cost_per_landing_page_view", "video_completion_rate", "thruplays",
             "hook_rate", "hold_rate_15s"}
HOURLY = ["ad_spend", "impressions", "clicks", "link_clicks", "landing_page_views", "ctr", "cpc", "cpm"]
BREAKDOWN_DIMS = ["breakdown_type", "age", "gender", "publisher_platform", "platform_position",
                  "impression_device", "device_platform", "country", "region"]
UNAVAILABLE = {
    m: ("Not populated: Shopify's checkout_started pixel is server-side (no web-session id), so session "
        "checkout timestamps / step counts are always empty (dbt fct_session_funnel KNOWN GAP). "
        "Use checkout_rate / add_to_cart_to_checkout_rate instead.")
    for m in ("avg_seconds_to_checkout", "avg_seconds_to_purchase", "checkout_steps")
}
NOT_CERTIFIED = {"link_clicks", "thruplays", "hook_rate", "hold_rate_15s", "cost_per_link_click"}  # approved
REDEFINED = {"return_revenue": "2.0.0", "cancel_revenue": "2.0.0"}  # kept id, number changed (event → order date)
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
    "ltv_cac_ratio": ["ltv", "cac"], "pnl_contribution_margin_pct": ["pnl_contribution_margin", "pnl_net_sales"],
    "pnl_net_margin_pct": ["pnl_net_profit", "pnl_net_sales"], "pnl_mer": ["pnl_net_sales", "ad_spend"],
    "pnl_gross_roas": ["gross_sales", "ad_spend"], "pnl_net_roas": ["pnl_contribution_margin", "ad_spend"],
    "pnl_be_roas": ["pnl_net_sales", "pnl_contribution_margin"],
}
DIM_RENAME = {("commerce", "event_type"): "order_event_type"}  # same member name, different meaning
V1_DIM_ALIASES = {"shipping_state": "shipping_region", "order_status": "order_status",
                  "platform": "lt_platform", "channel": "lt_channel", "product_title": "product_title"}
ALLOWED = {"finance_channel": ["meta", "google", "whatsapp", "organic", "unattributed"],
           "ad_platform": ["meta", "google"], "sales_channel": ["shopify", "amazon"],
           "medium_group": ["paid", "owned", "earned", "none", "unclassified"]}
SALES_CHANNEL_REASON = "Amazon is a reserved sales channel; it is not yet supported on the agent surface."
DISPLAY = {"aov": "AOV", "net_aov": "Net AOV", "ctr": "CTR", "cpc": "CPC", "cpm": "CPM", "ltv": "LTV", "cac": "CAC",
           "ltv_cac_ratio": "LTV:CAC ratio", "pnl_mer": "P&L MER", "pnl_gross_roas": "P&L gross ROAS",
           "pnl_net_roas": "P&L net ROAS", "pnl_be_roas": "P&L break-even ROAS", "hold_rate_15s": "Hold rate (15 s)"}


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
                        "pnl": "report_date", "pnl_channel": "report_date"}[name]
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
        if did == "sales_channel":
            entry["unsupported_values"] = {"amazon": SALES_CHANNEL_REASON}
        if not entry["description"]:
            entry["description"] = entry["display_name"]
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
        ("campaign", ["campaign_name", "adset_name", "ad_name"], "Orders / sessions by their last-touch campaign."),
    ):
        vs = [v for v in views_meta if all(v in dims.get(lv, {}).get("views", {}) for lv in levels)]
        if hid == "campaign":
            vs = [v for v in vs if v not in hier.get("ad", {}).get("views", [])]
        hier[hid] = {"display_name": hid.capitalize(), "levels": levels, "views": vs, "note": note}
    wy(OUT / "hierarchies.yaml", {"hierarchies": hier}, hdr)

    # ---- metrics
    measures = {m["name"]: m for vm in views_meta.values() for m in vm["measures"]}
    v2 = idmap["v2_metrics"]
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
                             "measure": f"paid_media_breakdowns.{member}", "dimensions": BREAKDOWN_DIMS})
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

    def modernize(text: str | None) -> str | None:
        return id_re.sub(lambda mo: old_to[mo.group(1)]["new"], text) if text else text

    terms, dropped = [], []
    for t in ry(V1 / "glossary" / "terms.yaml")["terms"]:
        t = dict(t)
        if t.get("definition"):
            t["definition"] = " ".join(modernize(t["definition"]).split())
        cid, did = t.get("canonical_id"), t.get("canonical_dimension_id")
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
