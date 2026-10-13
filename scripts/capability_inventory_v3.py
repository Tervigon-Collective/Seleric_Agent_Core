#!/usr/bin/env python3
"""Semantic v3 Phase 0 — capability inventory → doc/semantic_v3/CAPABILITY_MAP.yaml.

Lists everything the v2 semantic surface can do today, so the v3 rebuild can prove it lost nothing (spec §0D-1):

  - catalogue_v2 (through the MCP's own ``load_catalogue``): metrics with their bindings (hourly, Meta breakdown
    slices), supported dimensions / filters, valid_for restrictions; dimensions; hierarchies and every drill step;
    concepts and aliases; retired v1 ids (the "error names the replacement" behaviour);
  - Cube v2 ``/meta?extended=true``: every view and private cube, every measure / dimension / hierarchy member and
    its ``meta`` (v1, stable_key, composition, allocated), fiscal_year granularity;
  - the live MCP surface (tools, resources, resource templates, prompts) over HTTP on :8765;
  - query modes: metrics_query, metrics_drilldown, semantic_sql (Cube SQL API :15432, brand scoping by
    ``checkSqlAuth`` / ``queryRewrite`` in mage-ai/infra/cube/cube.js).

Each entry gets a ``target`` (keep / refine / replace / new / internal / tbd; ``retire`` only with an owner
decision) and the ``decision_ids`` (spec §0B) that justify a changed number, so Phase 7 can test "equal, or the
difference is explained by a decision ID". Targets come from the rule tables below, never from guesses: an id no
rule covers is ``tbd``.

  uv run python scripts/capability_inventory_v3.py                 # live: Cube :4002 + MCP :8765, write the map
  uv run python scripts/capability_inventory_v3.py --check         # rebuild, exit 1 on any coverage gap
  uv run python scripts/capability_inventory_v3.py --meta meta.json --mcp-surface mcp.json   # offline inputs

Gate (exit 1): a catalogue id, dimension, hierarchy, Cube member or MCP tool without an entry; an entry with an
unknown target; ``retire`` without ``owner_decision``.
"""
from __future__ import annotations

import argparse
import base64
import collections
import datetime
import hashlib
import hmac
import json
import os
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from seleric_mcp.catalogue_service.loader import load_catalogue  # noqa: E402

TARGETS = {"keep", "refine", "replace", "new", "internal", "tbd", "retire"}
OUT = ROOT / "doc" / "semantic_v3" / "CAPABILITY_MAP.yaml"
CUBE_ENV = Path(os.environ.get("SELERIC_CUBE_ENV", "/opt/seleric/mage-ai/infra/cube/.env"))

# --------------------------------------------------------------------------------------------------------------
# Decision rules (spec §0B). Families of decision ids, then per view / per id overrides.
# --------------------------------------------------------------------------------------------------------------
ORDER_POP = ["D-01", "D-08", "D-08a"]                 # replacements, zero-value and test orders leave the population
OUTCOMES = ["D-05", "D-22", "D-20"]                   # cancel / RTO / return / unknown, exchange edge cases
REVENUE = ["D-02", "D-02a", "D-02b", "D-09a", "D-10"]  # delivered = ACTIVE, ex-GST, shipping as order-level revenue
COSTS = ["D-03", "D-03a", "D-11", "D-11a"]            # COGS split layers, restock credit, back-fill
PERIOD = ["D-07", "D-06", "D-12"]                     # period match, default attribution model, as-of
FINANCE = ORDER_POP + OUTCOMES + REVENUE + COSTS + PERIOD

PRODUCT_AD_IDS = {  # line-allocated ad delivery on the product view (serve.product_ad_line today)
    "product_ad_spend", "product_clicks", "product_cost_per_landing_page_view", "product_cost_per_link_click",
    "product_cpc", "product_cpm", "product_ctr", "product_gross_roas", "product_net_roas", "product_hold_rate_15s",
    "product_hook_rate", "product_impressions", "product_landing_page_views", "product_link_clicks", "product_mer",
    "product_thruplays", "product_video_completion_rate",
}
PRODUCT_PNL_AD_IDS = {  # product_pnl ids that carry allocated spend
    "product_order_ad_spend", "product_be_roas", "product_cac", "product_cost_per_order", "product_ltv_cac_ratio",
    "product_net_profit", "product_contribution_margin", "product_contribution_margin_pct", "product_net_margin_pct",
}


def metric_rule(mid: str, view: str) -> tuple[str, list[str], str]:
    """(target, decision_ids, note) for one catalogue_v2 metric id."""
    lpv = "landing_page_view" in mid
    if view in ("paid_media", "paid_media_hourly", "paid_media_breakdowns"):
        if lpv:
            return "refine", ["D-13r", "D-17", "D-00"], ("major version bump: rebound to website LPV (sessions with "
                                                         "≥1 page view); platform figure kept as hidden twin "
                                                         "platform_reported_landing_page_views")
        return "keep", ["D-14"], "totals unchanged; Google gains native ad group / ad / device / network drill"
    if view == "paid_media_changes":
        return "keep", [], ""
    if view in ("web_events", "web_event_detail", "web_sessions"):
        return "keep", [], ""
    if view == "web_funnel":
        if mid in ("funnel_purchases", "funnel_revenue"):
            return "refine", ["D-09"], "label only: website purchase events are a tracking-quality metric"
        return "keep", [], ""
    if view == "funnel_orders":
        return "refine", ["D-09", "D-01"], "label: tracking-quality; replacement orders leave the population"
    if view == "commerce":
        ids = list(ORDER_POP)
        if any(k in mid for k in ("sales", "aov", "discount", "value", "revenue", "ltv", "refund")):
            ids += ["D-02b", "D-09a", "D-10"]
        if any(k in mid for k in ("cancel", "return", "refund", "net_")):
            ids += OUTCOMES + ["D-02a"]
        if "new_customer" in mid:
            ids += ["D-15", "D-16"]
        note = "incl-tax figure kept as a separately labelled metric (D-02b)" if "incl_tax" in mid else ""
        return "refine", ids, note
    if view == "order_pnl":
        ids = list(FINANCE)
        if mid.startswith("channel_"):
            ids += ["D-06"]
        if mid == "net_profit":
            return "refine", ids + ["D-18r"], "resolves to contribution after ads; label says opex excluded"
        return "refine", ids, ""
    if view == "pnl":
        ids = list(FINANCE) + ["D-21"]
        if mid == "pnl_net_profit":
            return "refine", ids + ["D-18r"], "event-date family; contribution after ads; opex excluded"
        return "refine", ids, "event-date family kept explicit, rebuilt on the component ledger"
    if view == "product":
        if mid in PRODUCT_AD_IDS:
            ids = ["D-19o", "D-06"] + (["D-13r", "D-17"] if lpv else [])
            return "refine", ids, "allocated family: attribution credit × line gross-sales share; never default"
        return "refine", ORDER_POP + OUTCOMES + ["D-02b", "D-09a", "D-03", "D-03a", "D-11"], ""
    if view == "product_pnl":
        ids = list(FINANCE)
        if mid.startswith("product_pnl_"):
            ids += ["D-21", "D-19o"]
            if mid == "product_pnl_net_profit":
                ids += ["D-18r"]
            return "refine", ids, "event-date product family; ad spend from the allocated family"
        if mid in PRODUCT_PNL_AD_IDS:
            ids += ["D-19o"] + (["D-18r"] if mid == "product_net_profit" else [])
            return "refine", ids, "allocated family; never default"
        return "refine", ids, ""
    if view == "returns":
        ids = ["D-01", "D-20", "D-22", "D-02b"] + (["D-03a"] if "cogs" in mid else [])
        return "refine", ids, "exchanges never enter return metrics; non-cash exchange credits are not refunds"
    if view in ("customers", "unit_economics"):
        ids = ORDER_POP + ["D-15", "D-16"] + (["D-07", "D-02b"] if mid in ("cac", "ltv", "ltv_cac_ratio") else [])
        return "refine", ids, ""
    if view == "attribution":
        if mid == "orders_with_touchpoints":
            return "refine", ORDER_POP, ""
        return "keep", [], ""
    if view == "attribution_paths":
        return "refine", ["D-01"], "replacement orders leave the order population"
    if view == "payments":
        return "refine", ["D-01"], ("non-cash exchange credits (gateway `manual` on exchange replacements, ₹6.2L success) "
                                    "are separated from cash; price-difference payments stay cash (Phase 1 finding)")
    return "tbd", [], "no rule"


DIM_RULES: dict[str, tuple[str, list[str], str]] = {
    "order_status": ("refine", ["D-05", "D-22"], "four-outcome `outcome` + `outcome_evidence` added alongside"),
    "is_cancelled": ("refine", ["D-05", "D-22"], ""),
    "return_status": ("refine", ["D-22", "D-20"], ""),
    "refund_class": ("refine", ["D-01", "D-20"], "non-cash exchange credits separated from cash refunds"),
    "has_money_refund": ("refine", ["D-20"], ""),
    "is_refund": ("refine", ["D-01", "D-20"], ""),
    "is_refunded": ("refine", ["D-01", "D-20"], ""),
    "fulfillment_status": ("keep", ["D-02a"], "ACTIVE counts as delivered for revenue; raw status unchanged"),
    "is_new_customer": ("refine", ["D-15", "D-16"], "first placed order, guests matched by hashed phone/email"),
    "is_first_order": ("refine", ["D-15", "D-16", "D-01"], ""),
    "is_repeat_customer": ("refine", ["D-15", "D-16", "D-01"], ""),
    "lifetime_order_count": ("refine", ["D-16", "D-01"], ""),
    "order_seq": ("refine", ["D-16", "D-01"], ""),
    "days_since_prev_order": ("refine", ["D-16", "D-01"], ""),
    "first_order_at": ("refine", ["D-16", "D-01"], ""),
    "first_order_product_title": ("refine", ["D-16", "D-01"], ""),
    "first_order_product_type": ("refine", ["D-16", "D-01"], ""),
    "first_order_sku": ("refine", ["D-16", "D-01"], ""),
    "last_order_at": ("refine", ["D-16", "D-01"], ""),
    "is_placement_order": ("refine", ["D-01", "D-08"], "order_type (sale / exchange_replacement / zero_value / test)"),
    "attribution_model": ("refine", ["D-06"], "model parameter, default last_non_direct, never mixed in a total"),
    "order_pnl_row_type": ("refine", ["D-07"], "Unattributed + spend-without-orders rows"),
    "row_type": ("refine", ["D-07"], ""),
    "campaign_type": ("keep", ["D-14"], ""),
    "ad_id": ("refine", ["D-14", "D-17"], "Google PMax ad = NULL; web sessions without ad id → `ad unknown`"),
    "ad_name": ("refine", ["D-14", "D-17"], ""),
    "adset_id": ("refine", ["D-14"], "Google ad group is the adset level"),
    "adset_name": ("refine", ["D-14"], ""),
    "campaign_id": ("refine", ["D-14", "D-17"], "web sessions without campaign id → `campaign unknown`"),
    "campaign_name": ("refine", ["D-14", "D-17"], ""),
}
# Public Cube members no catalogue id binds (reachable through semantic_sql / metrics_query by member).
MEMBER_RULES: dict[str, tuple[str, list[str]]] = {
    "product.orders": ("refine", ORDER_POP),
    "product.product_attributed_revenue": ("refine", ORDER_POP + ["D-06", "D-02b", "D-09a"]),
    "payments.transactions": ("refine", ["D-01"]),
    "customers.first_order_variant_id": ("refine", ["D-16", "D-01"]),
}
ATTRIBUTED_DIMS = {"platform", "channel", "sub_channel", "finance_channel", "medium_group", "is_paid",
                   "acquisition_platform", "acquisition_channel", "acquisition_campaign"}

# Capabilities that only exist as behaviour (no catalogue id / Cube member) — named in the plan.
BEHAVIOURS = [
    ("query_mode.metrics_query", "keep", [], "Cube /load through the MCP resolver"),
    ("query_mode.metrics_drilldown", "keep", ["D-14"], "hierarchy drill; Google native levels added"),
    ("query_mode.semantic_sql", "keep", [], "Cube SQL API 127.0.0.1:15432; MCP semantic_sql view-name regex must "
                                            "accept v3 view names"),
    ("security.brand_scope_checkSqlAuth", "keep", [], "cube.js: user brand_<id> → securityContext.brand_id; "
                                                      "queryRewrite adds <cube>.brand_id = id to every member"),
    ("granularity.fiscal_year", "keep", [], "custom granularity on Cube time dimensions"),
    ("binding.hourly", "refine", [], "Google hourly = campaign×hour×device×network (no ad level); Ads.Delivery "
                                     "hourly has 304,008 duplicate rows on declared keys — fix at grain "
                                     "(doc/semantic_datasets/CERTIFICATION.md)"),
    ("binding.meta_breakdowns", "keep", [], "slice_dimension breakdown_type; Meta only"),
    ("meta.stable_key", "keep", [], "Cube member meta"),
    ("meta.composition", "keep", [], "Cube member meta + catalogue formula.composition"),
    ("meta.allocated", "refine", ["D-19o"], "allocated family carries allocation_rule_version"),
    ("inline_sql.ltv_cac_daily", "refine", [], "business SQL inside Cube moves to serve (Phase 5)"),
    ("inline_sql.funnel_channel", "refine", [], "business SQL inside Cube moves to serve (Phase 5)"),
    ("refusal.metric_x_slice", "keep", [], "1,379 / 1,379 answered on 2026-10-09 (direct / grain twin / conformed "
                                           "sibling); each previously refused combination stays refused or "
                                           "becomes supported, never a crash"),
    ("refusal.brand_28_spend_only", "new", [], "brand 28 (Commbitz) is spend-only (§0C C7): profitability, ROAS, AOV must "
                                              "be refused (plan Phase 1 contract). v2 does NOT refuse today: it computes "
                                              "net_profit = pnl_net_profit = −spend (finding F0-3)"),
    ("retired_id_error", "keep", [], "retired v1 ids rejected with an error naming the replacement"),
]

# Capabilities already broken on the live v2 surface (Phase 0 findings, 2026-10-14). Cube v2 hot-reloads model_v2 from
# mage-ai main, which is ahead of the live serve DDL (unapplied commits f645091, 8a2b333). v3 must deliver these.
LIVE_DEFECTS = {
    "taxes_on_net_sales": "Cube error: order_pnl_daily.taxes not in live semantic.fct_order_pnl_daily (f645091 unapplied)",
    "pnl_taxes_on_net_sales": "Cube error: pnl_daily.taxes not in live semantic.fct_pnl_daily (f645091 unapplied)",
    "product_taxes_on_net_sales": "Cube error: product_economics.taxes not in live serve.product_pnl (f645091 unapplied)",
    "product_pnl_taxes_on_net_sales": "Cube error: product_economics.taxes not in live serve.product_pnl (f645091)",
}
for _m, _src in (("order_pnl.taxes_on_net_sales", "taxes_on_net_sales"), ("pnl.pnl_taxes_on_net_sales", "pnl_taxes_on_net_sales"),
                 ("product_pnl.pl_taxes_on_net_sales", "product_pnl_taxes_on_net_sales")):
    LIVE_DEFECTS[_m] = LIVE_DEFECTS[_src]
for _m in ("first_order_variant_id", "first_order_sku", "first_order_product_title", "first_order_product_type"):
    LIVE_DEFECTS[f"customers.{_m}"] = ("Cube error: serve.customer_ltv.first_order_variant_id not live (8a2b333 unapplied); "
                                       "the first_order_product join fails for every query using it")

# New capabilities the spec adds (so the map is the full v3 contract, not only the v2 one).
NEW = [
    ("dimension.order_type", ["D-01", "D-08", "D-08a"]), ("dimension.root_order_id", ["D-01"]),
    ("dimension.outcome", ["D-22"]), ("dimension.outcome_evidence", ["D-22"]),
    ("dimension.cost_method", ["D-11", "D-11a"]), ("dimension.cost_status", ["D-11a"]),
    ("dimension.maturing", ["D-12a"]), ("dimension.as_of", ["D-12"]),
    ("dimension.allocation_rule_version", ["D-19o"]),
    ("dimension.google_device", ["D-14"]), ("dimension.google_network", ["D-14"]),
    ("metric.platform_reported_landing_page_views", ["D-13r"]),
    ("metric.rto_orders / rto_sales", ["D-05", "D-22"]), ("metric.unknown_outcome_orders", ["D-22"]),
    ("metric.exchange components", ["D-01"]), ("metric.gmv_incl_tax (separately labelled)", ["D-02b"]),
    ("metric.contribution_after_ads", ["D-18", "D-18r"]),
]


# --------------------------------------------------------------------------------------------------------------
def cube_secret() -> str:
    if os.environ.get("CUBEJS_API_SECRET"):
        return os.environ["CUBEJS_API_SECRET"]
    for line in CUBE_ENV.read_text().splitlines():
        if line.startswith("CUBEJS_API_SECRET="):
            return line.split("=", 1)[1].strip().strip("'\"")
    raise SystemExit(f"CUBEJS_API_SECRET not set and not in {CUBE_ENV}")


def cube_token() -> str:
    b = lambda x: base64.urlsafe_b64encode(x).rstrip(b"=").decode()  # noqa: E731
    now = int(time.time())
    head = b(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    body = b(json.dumps({"iat": now, "exp": now + 3600}).encode())
    return f"{head}.{body}." + b(hmac.new(cube_secret().encode(), f"{head}.{body}".encode(), hashlib.sha256).digest())


def fetch_meta(url: str) -> dict:
    req = urllib.request.Request(f"{url}/cubejs-api/v1/meta?extended=true", headers={"Authorization": cube_token()})
    return json.load(urllib.request.urlopen(req, timeout=120))


def fetch_mcp_surface(url: str) -> dict:
    tok = os.environ.get("MCP_SERVICE_TOKEN") or subprocess.run(
        ["docker", "exec", "seleric-mcp-mcp-1", "printenv", "MCP_SERVICE_TOKEN"],
        capture_output=True, text=True, check=True).stdout.strip()
    sid = None

    def call(method, params=None, notify=False):
        nonlocal sid
        body = {"jsonrpc": "2.0", "method": method, **({"params": params} if params is not None else {}),
                **({} if notify else {"id": 1})}
        h = {"Authorization": f"Bearer {tok}", "Content-Type": "application/json",
             "Accept": "application/json, text/event-stream", **({"mcp-session-id": sid} if sid else {})}
        r = urllib.request.urlopen(urllib.request.Request(url, json.dumps(body).encode(), h), timeout=60)
        sid = r.headers.get("mcp-session-id") or sid
        txt = r.read().decode()
        if notify:
            return None
        data = [ln[5:] for ln in txt.splitlines() if ln.startswith("data:")]
        return json.loads(data[0] if data else txt)

    call("initialize", {"protocolVersion": "2025-03-26", "capabilities": {},
                        "clientInfo": {"name": "capability_inventory_v3", "version": "1"}})
    call("notifications/initialized", notify=True)
    out = {}
    for method, key in [("tools/list", "tools"), ("resources/list", "resources"),
                        ("resources/templates/list", "resourceTemplates"), ("prompts/list", "prompts")]:
        out[key] = call(method, {}).get("result", {}).get(key, [])
    return out


# --------------------------------------------------------------------------------------------------------------
def entry(target: str, decision_ids=(), note: str = "", **extra) -> dict:
    e = {"target": target, "decision_ids": sorted(set(decision_ids))}
    if note:
        e["note"] = note
    e.update({k: v for k, v in extra.items() if v not in (None, [], {}, "")})
    return e


def build(cat, meta: dict, mcp: dict) -> dict:
    out: dict = collections.OrderedDict()
    member_targets: dict[str, tuple[str, list[str]]] = {}

    # metrics
    metrics = {}
    for mid, m in sorted(cat.metrics.items()):
        target, ids, note = metric_rule(mid, m.cube_mapping.view)
        bindings = [{"name": b.name, "view": b.view, "measure": b.measure,
                     **({"granularities": b.granularities} if b.granularities else {}),
                     **({"slice_dimension": b.slice_dimension} if b.slice_dimension else {})} for b in m.bindings]
        metrics[mid] = entry(target, ids, note, view=m.cube_mapping.view, member=m.cube_mapping.measure,
                             status=m.status, version=m.version, grain=m.grain, aggregation=m.aggregation,
                             bindings=bindings, supported_dimensions=sorted(m.supported_dimensions),
                             supported_filters=sorted(m.supported_filters),
                             valid_for={k: sorted(v) for k, v in m.valid_for.items()},
                             excluded_dimensions=dict(m.excluded_dimensions),
                             replaces=sorted(m.replaces), live_defect=LIVE_DEFECTS.get(mid))
        for mem in [m.cube_mapping.measure] + [b.measure for b in m.bindings] + list(m.companion_measures):
            member_targets.setdefault(mem, (target, ids))
    out["metrics"] = metrics

    # dimensions
    dims = {}
    for did, d in sorted(cat.dimensions.items()):
        if did in DIM_RULES:
            t, ids, note = DIM_RULES[did]
        elif did in ATTRIBUTED_DIMS:
            t, ids, note = "keep", ["D-06"], "dimension kept; on order views the value follows the default model"
        else:
            t, ids, note = "keep", [], ""
        dims[did] = entry(t, ids, note, views=dict(sorted(d.views.items())), is_time=d.is_time or None)
        for mem in d.views.values():
            member_targets.setdefault(mem, (t, ids))
    out["dimensions"] = dims

    # hierarchies + drill steps
    hier, drills = {}, {}
    for hid, h in sorted(cat.hierarchies.items()):
        ids = ["D-14"] if hid in ("ad", "campaign") else []
        note = "Google: account → campaign type → campaign → ad group → ad; PMax stops at campaign" if ids else ""
        hier[hid] = entry("refine" if ids else "keep", ids, note, levels=h.levels, views=h.views)
        for v in h.views:
            for a, b in zip(h.levels, h.levels[1:]):
                drills[f"{hid}:{v}:{a}->{b}"] = entry("keep", ids)
    out["hierarchies"] = hier
    out["drill_paths"] = drills

    # Cube v2 members
    cubes = {}
    for c in sorted(meta["cubes"], key=lambda c: c["name"]):
        public = c.get("public", True)
        members = {}
        for kind in ("measures", "dimensions", "hierarchies"):
            for mem in c.get(kind) or []:
                name = mem["name"]
                if not public:
                    t, ids = "internal", []
                elif name in member_targets:
                    t, ids = member_targets[name]
                elif name in MEMBER_RULES:
                    t, ids = MEMBER_RULES[name]
                elif kind == "hierarchies" and name.split(".", 1)[1] in hier:
                    t, ids = hier[name.split(".", 1)[1]]["target"], hier[name.split(".", 1)[1]]["decision_ids"]
                elif name.endswith(".brand_id"):
                    t, ids = "keep", []
                else:
                    t, ids = "tbd", []
                m_meta = {k: v for k, v in (mem.get("meta") or {}).items()
                          if k in ("stable_key", "composition", "allocated")}
                grans = [g["name"] for g in mem.get("granularities") or [] if isinstance(g, dict)]
                members[name] = entry(t, ids, live_defect=LIVE_DEFECTS.get(name), kind={"measures": "measure", "dimensions": "dimension", "hierarchies": "hierarchy"}[kind], type=mem.get("type"), meta=m_meta,
                                      granularities=grans or None, alias=mem.get("aliasMember"))
        cubes[c["name"]] = {"type": c.get("type"), "public": public, "members": members}
    out["cube_v2"] = cubes

    # catalogue extras
    out["concepts"] = {k: entry("refine" if k in ("profit", "roas", "sales", "returns_cancels", "orders", "cogs",
                                                  "landing_page_views", "margin", "customers", "refunds")
                                else "keep",
                                {"profit": ["D-18r", "D-07"], "roas": ["D-09", "D-06"], "sales": ["D-02b", "D-01"],
                                 "returns_cancels": ["D-22"], "orders": ["D-01", "D-08"], "cogs": ["D-03"],
                                 "landing_page_views": ["D-13r", "D-17"], "margin": ["D-03", "D-18r"],
                                 "customers": ["D-15", "D-16"], "refunds": ["D-01", "D-20"]}.get(k, []),
                                "defaults move to order-date cohort / last_non_direct / contribution after ads"
                                if k in ("profit", "roas", "margin") else "")
                       for k in sorted(cat.concepts)}
    out["concept_aliases"] = {k: entry("keep") for k in sorted(cat.concept_aliases)}
    out["retired_ids"] = {d.old: entry("keep", note=f"error names {d.new}") for d in
                          sorted(cat.deprecations, key=lambda d: d.old)}

    # MCP surface
    out["mcp"] = {
        "tools": {t["name"]: entry("keep", note=("view-name regex must accept v3 names"
                                                 if t["name"] == "semantic_sql" else ""))
                  for t in sorted(mcp["tools"], key=lambda t: t["name"])},
        "resources": {str(r.get("uri") or r["name"]): entry("keep", name=r["name"]) for r in mcp["resources"]},
        "resource_templates": {r["uriTemplate"]: entry("keep", name=r["name"]) for r in mcp["resourceTemplates"]},
        "prompts": {p["name"]: entry("keep") for p in mcp["prompts"]},
    }
    out["behaviours"] = {k: entry(t, ids, n) for k, t, ids, n in BEHAVIOURS}
    out["new"] = {k: entry("new", ids) for k, ids in NEW}
    return out


def gate(cat, meta: dict, mcp: dict, cmap: dict) -> list[str]:
    errs = []
    for mid in cat.metrics:
        if mid not in cmap["metrics"]:
            errs.append(f"metric without entry: {mid}")
    for did in cat.dimensions:
        if did not in cmap["dimensions"]:
            errs.append(f"dimension without entry: {did}")
    for hid in cat.hierarchies:
        if hid not in cmap["hierarchies"]:
            errs.append(f"hierarchy without entry: {hid}")
    for c in meta["cubes"]:
        mem = cmap["cube_v2"].get(c["name"], {}).get("members", {})
        for kind in ("measures", "dimensions", "hierarchies"):
            for m in c.get(kind) or []:
                if m["name"] not in mem:
                    errs.append(f"cube member without entry: {m['name']}")
    for t in mcp["tools"]:
        if t["name"] not in cmap["mcp"]["tools"]:
            errs.append(f"MCP tool without entry: {t['name']}")

    def walk(node, path):
        if isinstance(node, dict) and "target" in node:
            if node["target"] not in TARGETS:
                errs.append(f"{path}: unknown target {node['target']}")
            if node["target"] == "retire" and not node.get("owner_decision"):
                errs.append(f"{path}: retire without owner_decision")
            return
        if isinstance(node, dict):
            for k, v in node.items():
                walk(v, f"{path}/{k}")
    walk(cmap, "")
    return errs


def summary(cmap: dict) -> dict:
    s = {}
    for sect, node in cmap.items():
        cnt = collections.Counter()

        def walk(n):
            if isinstance(n, dict) and "target" in n:
                cnt[n["target"]] += 1
            elif isinstance(n, dict):
                for v in n.values():
                    walk(v)
        walk(node)
        s[sect] = dict(sorted(cnt.items()))
    return s


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--catalogue", default=str(ROOT / "catalogue_v2"))
    ap.add_argument("--cube-url", default=os.environ.get("CUBE_V2_URL", "http://127.0.0.1:4002"))
    ap.add_argument("--mcp-url", default="http://127.0.0.1:8765/mcp")
    ap.add_argument("--meta", help="saved /meta?extended=true JSON instead of the live Cube")
    ap.add_argument("--mcp-surface", help="saved MCP list JSON instead of the live server")
    ap.add_argument("--out", default=str(OUT))
    ap.add_argument("--check", action="store_true", help="do not write; exit 1 on a coverage gap")
    a = ap.parse_args()

    cat = load_catalogue(Path(a.catalogue))
    meta = json.load(open(a.meta)) if a.meta else fetch_meta(a.cube_url)
    mcp = json.load(open(a.mcp_surface)) if a.mcp_surface else fetch_mcp_surface(a.mcp_url)
    cmap = build(cat, meta, mcp)
    errs = gate(cat, meta, mcp, cmap)
    s = summary(cmap)
    doc = {
        "generated_by": "scripts/capability_inventory_v3.py",
        "generated_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sources": {"catalogue": os.path.relpath(a.catalogue, ROOT), "cube_meta": a.meta or a.cube_url,
                    "cube_meta_sha": hashlib.sha256(json.dumps(meta, sort_keys=True).encode()).hexdigest()[:12],
                    "mcp": a.mcp_surface or a.mcp_url, "spec": "doc/Seleric_Enterprise_Data_Model_v2_Two_Planes.md "
                                                               "v2.2 §0B"},
        "targets": {"keep": "same id, same number", "refine": "same id; number or label changes, explained by "
                    "decision_ids (version bump where the number changes)", "replace": "new id; old id errors "
                    "naming it", "new": "added by v3", "internal": "private Cube cube (no direct capability; "
                    "covered by the public view members)", "tbd": "no decision rule yet — must be settled before "
                    "Phase 7", "retire": "only with owner_decision"},
        "summary": s,
        "gate": {"coverage_gaps": len(errs), "passed": not errs},
        **cmap,
    }
    for e in errs:
        print("GAP", e, file=sys.stderr)
    print(json.dumps(s, indent=1))
    print(f"gate: {'PASS' if not errs else 'FAIL'} ({len(errs)} gaps)")
    if not a.check:
        Path(a.out).write_text("# GENERATED by scripts/capability_inventory_v3.py — edit the rules there.\n"
                               + yaml.safe_dump(doc, sort_keys=False, allow_unicode=True, width=120))
        print("wrote", a.out)
    return 1 if errs else 0


if __name__ == "__main__":
    sys.exit(main())
