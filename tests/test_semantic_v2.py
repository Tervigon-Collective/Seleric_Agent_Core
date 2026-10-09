"""Semantic v2 catalogue + planner (catalogue_v2/, doc/semantic_v2): hard cut, one resolver,
concept axis filters, bindings, valid_for, unsupported values, hierarchy drill, provenance."""
from __future__ import annotations

import pytest
import yaml

from seleric_mcp.app.models import FilterSpec, PlanError, QueryRequest, TimeRange
from seleric_mcp.app.query_planner import QueryPlanner, _inline_params
from seleric_mcp.catalogue_service.loader import load_catalogue
from seleric_mcp.catalogue_service.service import (
    AmbiguousTerm,
    CatalogueService,
    ResolvedConcept,
    ResolvedTerm,
    RetiredTerm,
    UnknownTerm,
)
from seleric_mcp.config import PROJECT_ROOT
from conftest import FakeCube

V2_DIR = PROJECT_ROOT / "catalogue_v2"
ID_MAP = PROJECT_ROOT / "catalogue" / "migrations" / "v2_id_map.yaml"
SEP = TimeRange(start="2026-09-01", end="2026-09-30")


@pytest.fixture(scope="module")
def v2() -> CatalogueService:
    return CatalogueService(load_catalogue(V2_DIR))


class FakeCubeV2(FakeCube):
    async def sql(self, query: dict) -> dict:
        return {"sql": "SELECT sum(x) FROM serve.pnl_daily AS p WHERE p.brand_id = toFloat64(?)",
                "params": ["20"]}


@pytest.fixture()
def cube() -> FakeCubeV2:
    return FakeCubeV2()


@pytest.fixture()
def planner(v2, cube, result_store) -> QueryPlanner:
    return QueryPlanner(v2, cube, result_store)


# ---------------------------------------------------------------- catalogue shape

def test_catalogue_v2_loads_as_semantic_v2(v2):
    assert v2.is_v2
    assert len(v2.cat.metrics) == 231  # + the P&L line items that complete each composition (order, Finance and both product trees)
    assert len(v2.cat.views) == 20
    assert {"traffic", "product", "geo", "ad", "campaign"} <= set(v2.cat.hierarchies)


def test_v1_catalogue_is_unchanged(catalogue):
    assert not catalogue.is_v2
    assert catalogue.cat.retired == {}
    # v1 ids still resolve on the v1 catalogue (nothing switched before cutover)
    assert catalogue.resolve_metric_id("meta_spend") == ("meta_spend", None)


def test_every_metric_maps_to_a_v2_view_member(v2):
    meta_views = set(v2.cat.views)
    for m in v2.cat.metrics.values():
        assert m.cube_mapping.view in meta_views
        assert m.cube_mapping.measure.startswith(m.cube_mapping.view + ".")


def test_checkout_timing_metrics_are_unavailable_with_reason(v2):
    for mid in ("avg_seconds_to_checkout", "avg_seconds_to_purchase", "checkout_steps"):
        m = v2.cat.metrics[mid]
        assert m.status == "broken" and not m.is_queryable
        assert "server-side" in (m.unavailable_reason or "")


# ---------------------------------------------------------------- hard cut

def test_every_retired_v1_id_is_rejected_naming_its_replacement(v2, planner):
    maps = yaml.safe_load(ID_MAP.read_text())["maps"]
    retired = [m for m in maps if m["old"] != m["new"] and m["old"] not in v2.cat.metrics]
    assert len(retired) == len(v2.cat.retired) == 110  # channel_orders / product_orders / pnl_product_cost are v2 ids again
    for m in retired:
        term = v2.resolve_term(m["old"])
        assert isinstance(term, RetiredTerm), m["old"]
        assert term.replacement == m["new"]
        with pytest.raises(PlanError) as exc:
            planner._resolve_metrics([m["old"]])
        assert f"Use '{m['new']}'" in str(exc.value)


def test_retired_id_carries_its_filter(v2):
    term = v2.resolve_term("meta_spend")
    assert isinstance(term, RetiredTerm)
    assert term.replacement == "ad_spend" and term.filters == {"ad_platform": "meta"}
    assert "ad_platform = meta" in term.message


def test_retired_without_replacement_says_so(v2):
    term = v2.resolve_term("meta_reach")
    assert isinstance(term, RetiredTerm) and term.replacement is None
    assert "no replacement" in term.message


def test_redefined_id_is_live_not_retired(v2):
    m = v2.cat.metrics["return_revenue"]
    assert m.version == "2.0.0" and m.cube_mapping.view == "commerce"
    assert "return_revenue" not in v2.cat.retired


def test_get_metric_on_retired_id_returns_replacement_with_notice(v2):
    m, notice = v2.lookup_metric("total_ad_spend")
    assert m.id == "ad_spend" and "retired in semantic v2" in notice


# ---------------------------------------------------------------- one resolver

@pytest.mark.parametrize("text,metric,flt", [
    ("net sales", "net_sales", {}),
    ("sales", "net_sales", {}),
    ("gross sales", "gross_sales", {}),
    ("roas", "net_roas", {}),
    ("meta spend", "ad_spend", {"ad_platform": "meta"}),
    ("google ad spend", "ad_spend", {"ad_platform": "google"}),
    ("net profit", "net_profit", {}),
    ("meta net sales", "net_sales", {"finance_channel": "meta"}),
    ("orders", "orders", {}),
    ("cancelled orders", "cancelled_orders", {}),
    ("ns", "net_sales", {}),
    # Finance (event date) only when asked for explicitly
    ("p&l net sales", "pnl_net_sales", {}),
    ("finance net profit", "pnl_net_profit", {}),
    ("p&l roas", "pnl_net_roas", {}),
    ("meta roas", "mer", {"finance_channel": "meta"}),
    ("meta orders", "orders", {"finance_channel": "meta"}),
    ("cac", "cac", {}),
])
def test_resolver_answers(v2, text, metric, flt):
    term = v2.resolve_term(text)
    assert isinstance(term, ResolvedTerm), (text, term)
    assert term.metric_id == metric, text
    assert term.filter == flt, text


@pytest.mark.parametrize("text", ["net sales", "meta spend", "roas", "orders", "cpc", "sessions"])
def test_search_top_answer_equals_resolver(v2, text):
    term = v2.resolve_term(text)
    top = v2.search(text).matches[0]
    assert top.id == term.metric_id


def test_fuzzy_never_auto_resolves(v2):
    term = v2.resolve_term("net salse")
    assert not (isinstance(term, ResolvedTerm) and term.auto_resolved)
    assert isinstance(term, (AmbiguousTerm, UnknownTerm, ResolvedTerm))
    if isinstance(term, ResolvedTerm):  # only via a deterministic path (concept / glossary)
        assert not term.auto_resolved


def test_concept_axis_filters_channel_and_paid(v2):
    rc = v2.resolve_concept("roas", {"channel": "meta", "paid": "paid"})
    assert isinstance(rc, ResolvedConcept)
    assert rc.metric_id == "net_roas"  # order date unless Finance is asked
    assert rc.filter == {"finance_channel": "meta", "is_paid": "true"}
    fin = v2.resolve_concept("roas", {"channel": "meta", "date": "finance"})
    assert fin.metric_id == "pnl_net_roas"


def test_concept_text_detects_paid_without_tripping_on_prepaid(v2):
    rc = v2.resolve_concept("prepaid orders")
    assert isinstance(rc, ResolvedConcept)
    assert rc.metric_id == "prepaid_orders" and "is_paid" not in rc.filter


# ---------------------------------------------------------------- planner rules

async def test_hour_granularity_routes_to_hourly_binding(planner, cube):
    req = QueryRequest(measures=["ad_spend"], time_range=SEP, granularity="hour")
    out = await planner.run(req)
    q = cube.queries[-1]
    assert q["measures"] == ["paid_media_hourly.ad_spend"]
    assert q["timeDimensions"][0]["dimension"] == "paid_media_hourly.report_hour"
    assert any("hourly" in w for w in out["warnings"])


async def test_breakdown_dimension_routes_to_breakdown_binding(planner, cube):
    req = QueryRequest(measures=["impressions"], dimensions=["age"], time_range=SEP)
    await planner.run(req)
    q = cube.queries[-1]
    assert q["measures"] == ["paid_media_breakdowns.impressions"]
    assert "paid_media_breakdowns.age" in q["dimensions"]
    # every breakdown_type repeats all of Meta delivery: exactly one is pinned
    assert {"member": "paid_media_breakdowns.breakdown_type", "operator": "equals",
            "values": ["age_and_gender"]} in q["filters"]


@pytest.mark.parametrize("dims,slice_", [
    (["publisher_platform"], "publisher_platform"),
    (["publisher_platform", "platform_position"], "placement"),
    (["publisher_platform", "device_platform"], "platform_device"),
    (["region"], "region"),
])
async def test_breakdown_slice_follows_the_dimensions(planner, cube, dims, slice_):
    await planner.run(QueryRequest(measures=["ad_spend"], dimensions=dims, time_range=SEP))
    flt = [f for f in cube.queries[-1]["filters"] if f["member"] == "paid_media_breakdowns.breakdown_type"]
    assert flt == [{"member": "paid_media_breakdowns.breakdown_type", "operator": "equals", "values": [slice_]}]


async def test_breakdown_filter_alone_pins_its_slice(planner, cube):
    req = QueryRequest(measures=["impressions"], time_range=SEP,
                       filters=[FilterSpec(dimension="gender", operator="equals", values=["female"])])
    await planner.run(req)
    assert {"member": "paid_media_breakdowns.breakdown_type", "operator": "equals",
            "values": ["age_and_gender"]} in cube.queries[-1]["filters"]


@pytest.mark.parametrize("dims,filters,msg", [
    (["age", "region"], [], "cannot be combined"),
    (["country"], [], "not available"),
    (["age"], [FilterSpec(dimension="breakdown_type", operator="equals", values=["region"])], "not in"),
    ([], [FilterSpec(dimension="breakdown_type", operator="equals", values=["region", "placement"])],
     "exactly one"),
])
async def test_breakdown_slices_that_would_double_count_are_refused(planner, dims, filters, msg):
    with pytest.raises(PlanError) as exc:
        await planner.run(QueryRequest(measures=["impressions"], dimensions=dims, filters=filters, time_range=SEP))
    assert msg in str(exc.value)


async def test_grouping_by_breakdown_type_is_allowed_with_warning(planner, cube):
    out = await planner.run(QueryRequest(measures=["impressions"], dimensions=["breakdown_type"], time_range=SEP))
    assert not any(f["member"].endswith("breakdown_type") for f in cube.queries[-1].get("filters", []))
    assert any("never sum across" in w for w in out["warnings"])


async def test_meta_only_metric_is_scoped_in_its_own_part(planner, cube):
    req = QueryRequest(measures=["ad_spend", "hook_rate"], time_range=SEP)
    out = await planner.run(req)
    assert out.get("composed")  # ad_spend unscoped, hook_rate scoped to meta: two parts
    scoped = [q for q in cube.queries if "paid_media.hook_rate" in q["measures"]]
    unscoped = [q for q in cube.queries if "paid_media.ad_spend" in q["measures"]]
    assert any(f["member"] == "paid_media.ad_platform" and f["values"] == ["meta"]
               for f in scoped[0].get("filters", []))
    assert not any(f["member"] == "paid_media.ad_platform" for f in unscoped[0].get("filters", []))


async def test_meta_only_metric_rejects_google(planner):
    req = QueryRequest(measures=["hook_rate"], time_range=SEP,
                       filters=[FilterSpec(dimension="ad_platform", operator="equals", values=["google"])])
    with pytest.raises(PlanError) as exc:
        await planner.run(req)
    assert "only valid for ad_platform = meta" in str(exc.value)


async def test_amazon_sales_channel_is_rejected(planner):
    req = QueryRequest(measures=["orders"], time_range=SEP,
                       filters=[FilterSpec(dimension="sales_channel", operator="equals", values=["amazon"])])
    with pytest.raises(PlanError) as exc:
        await planner.run(req)
    assert "not supported" in str(exc.value) and "Amazon" in str(exc.value)


async def test_term_filters_flow_into_the_query(planner, cube):
    req = QueryRequest(measures=["meta net sales"], time_range=SEP)
    out = await planner.run(req)
    q = cube.queries[-1]
    assert q["measures"] == ["commerce.net_sales"]
    assert {"member": "commerce.finance_channel", "operator": "equals", "values": ["meta"]} in q["filters"]
    assert any("finance_channel = meta" in w for w in out["warnings"])


async def test_unavailable_metric_is_refused_with_reason(planner):
    req = QueryRequest(measures=["avg_seconds_to_checkout"], time_range=SEP)
    with pytest.raises(PlanError) as exc:
        await planner.run(req)
    assert "server-side" in str(exc.value) or "broken" in str(exc.value)


async def test_hierarchy_drill_goes_to_next_level(planner, cube):
    cube.by_prefix["commerce"] = [{"commerce.orders": "5", "commerce.platform": "meta"}]
    out = await planner.run(QueryRequest(measures=["orders"], dimensions=["platform"], time_range=SEP))
    dims = planner.hierarchy_targets(out["query_id"], "traffic")
    assert dims == ["channel"]
    # conformed 2026-10-08: commerce carries ad_platform, so the ad drill is available on orders too
    assert planner.hierarchy_targets(out["query_id"], "ad")
    with pytest.raises(PlanError):
        planner.hierarchy_targets(out["query_id"], "product")  # an order has several products: no product drill


async def test_campaign_drill_works_on_ad_spend(planner, cube):
    # live 2026-10-04: 'campaign' asked on ad spend was refused ("its levels live under 'ad'"); 2026-10-08 every
    # view carrying the campaign levels has the campaign drill
    cube.by_prefix["paid_media"] = [{"paid_media.ad_spend": "5", "paid_media.ad_platform": "meta"}]
    out = await planner.run(QueryRequest(measures=["ad_spend"], dimensions=["ad_platform"], time_range=SEP))
    assert planner.hierarchy_targets(out["query_id"], "campaign")


async def test_wrong_hierarchy_names_the_one_that_covers_the_view(planner, cube):
    cube.by_prefix["paid_media"] = [{"paid_media.ad_spend": "5", "paid_media.ad_platform": "meta"}]
    out = await planner.run(QueryRequest(measures=["ad_spend"], dimensions=["ad_platform"], time_range=SEP))
    with pytest.raises(PlanError) as exc:
        planner.hierarchy_targets(out["query_id"], "traffic")  # ad delivery has no channel / sub_channel
    assert "Use hierarchy" in str(exc.value) and exc.value.suggestions


async def test_provenance_v2_has_versions_sql_and_trace(planner, cube):
    out = await planner.run(QueryRequest(measures=["pnl_net_profit"], time_range=SEP))
    sem = out["provenance"]["semantic"]
    assert sem["semantic_version"] == 2
    assert sem["metrics"][0] == {"id": "pnl_net_profit", "version": "1.0.0",
                                 "member": "pnl.pnl_net_profit", "view": "pnl"}
    assert sem["serve_objects"] == ["pnl_daily"]
    assert "toFloat64('20')" in sem["cube_sql"]
    assert "normalizedQueryHash(" in sem["clickhouse_trace"]["lookup_sql"]


def test_inline_params_skips_quoted_literals():
    sql = "SELECT 1 WHERE a = ? AND b = 'x?y' AND c = 'it''s?' AND d = ?"
    assert _inline_params(sql, ["1", "2"]) == "SELECT 1 WHERE a = '1' AND b = 'x?y' AND c = 'it''s?' AND d = '2'"


async def test_breakdown_binding_says_it_is_meta_only(planner):
    out = await planner.run(QueryRequest(measures=["impressions"], dimensions=["age"], time_range=SEP))
    assert any("Meta only" in w for w in out["warnings"])


async def test_metric_missing_from_a_slice_is_refused_not_zero(planner):
    # Meta's region breakdown carries no landing_page_views (sums to 0): refuse, never answer 0
    for kw in ({"dimensions": ["region"]},
               {"filters": [FilterSpec(dimension="breakdown_type", operator="equals", values=["region"])]}):
        with pytest.raises(PlanError) as exc:
            await planner.run(QueryRequest(measures=["landing_page_views"], time_range=SEP, **kw))
        assert "landing_page_views" in str(exc.value)


def test_binding_dimensions_are_on_the_metric_surface(v2):
    # introspection (bootstrap / get_metric) must show what the planner can route
    dims = set(v2.cat.metrics["ad_spend"].supported_dimensions)
    assert {"age", "gender", "publisher_platform", "region", "breakdown_type"} <= dims
    assert "country" not in dims  # never populated on the breakdown fact: refused, so not advertised
    assert "region" not in v2.cat.metrics["landing_page_views"].supported_dimensions
    assert "Meta delivery only" in v2.cat.dimensions["age"].description


def test_bootstrap_carries_what_the_agent_needs(v2, catalogue):
    b = v2.bootstrap()
    assert b["semantic_version"] == 2 and set(b["hierarchies"]) == set(v2.cat.hierarchies)
    assert len(b["metrics"]) == 228  # 231 minus the 3 unavailable checkout-timing metrics
    m = {x["id"]: x for x in b["metrics"]}
    assert m["hook_rate"]["valid_for"] == {"ad_platform": ["meta"]}
    assert m["ad_spend"]["extra_granularities"] == ["hour"] and "age" in m["ad_spend"]["supported_dimensions"]
    assert any("Meta only" in n for n in m["ad_spend"]["binding_notes"])
    d = {x["id"]: x for x in b["dimensions"]}
    assert d["channel"]["hierarchy"] == {"id": "traffic", "level": 2, "levels": ["platform", "channel", "sub_channel"]}
    assert "amazon" in d["sales_channel"]["unsupported_values"]
    assert "Meta delivery only" in d["age"]["description"]
    # v1 bootstrap unchanged
    assert "semantic_version" not in catalogue.bootstrap()
    assert "description" not in catalogue.bootstrap()["dimensions"][0]


async def test_hierarchy_drill_from_a_filtered_level_goes_to_the_next(planner, cube):
    # "Meta orders" (platform pinned by a filter) drilled on traffic -> channel, not platform again
    cube.by_prefix["commerce"] = [{"commerce.orders": "5"}]
    out = await planner.run(QueryRequest(
        measures=["orders"], time_range=SEP,
        filters=[FilterSpec(dimension="platform", operator="equals", values=["meta"])]))
    assert planner.hierarchy_targets(out["query_id"], "traffic") == ["channel"]
    out2 = await planner.run(QueryRequest(
        measures=["orders"], dimensions=["channel"], time_range=SEP,
        filters=[FilterSpec(dimension="platform", operator="equals", values=["meta"])]))
    assert planner.hierarchy_targets(out2["query_id"], "traffic") == ["sub_channel"]



def test_order_date_is_the_default_and_only_finance_reads_the_event_date(v2):
    """Decision 2026-10-04: performance, ads and every non-Finance domain read the ORDER date; only Finance
    (pnl_* ids, event date) — reached only when the question asks for it."""
    for c in v2.cat.concepts.values():
        axis = c.axes.get("date")
        if axis is not None:
            assert axis.default == "order", c.id
    pnl_terms = [t.term for t in v2.cat.glossary if t.canonical_id and t.canonical_id.startswith("pnl_")]
    assert pnl_terms and all(any(k in t.lower() for k in ("pnl", "p&l", "finance", "event date")) for t in pnl_terms)
    # every pnl_ metric has its order-date twin (same id without the prefix)
    for mid in v2.cat.metrics:
        if mid.startswith("pnl_"):
            assert mid[4:] in v2.cat.metrics, mid
    m = v2.cat.metrics["net_roas"]
    assert m.version == "2.0.0" and m.cube_mapping.view == "order_pnl"


def test_concept_axis_keyed_by_its_dimension_is_placed_by_value(v2):
    # live 2026-10-05: axes={"ad_platform": "meta"} was dropped and platform defaulted to "all"
    by_axis = v2.resolve_concept("ad spend", {"platform": "meta"})
    by_dim = v2.resolve_concept("ad spend", {"ad_platform": "meta"})
    assert by_dim.metric_id == by_axis.metric_id
    assert by_dim.filter == by_axis.filter == {"ad_platform": "meta"}
    assert "platform" not in by_dim.defaults_applied
    # a key no axis can take is still ignored (the question's own axes ride along on every call)
    assert v2.resolve_concept("ad spend", {"date": "order"}).metric_id == "ad_spend"


# ---------------------------------------------------------------- product grain (2026-10-06)

@pytest.mark.parametrize("text, axes, metric", [
    ("net sales", None, "net_sales"),                          # order grain stays the default
    ("net sales", {"scope": "product"}, "product_net_revenue"),  # a 2-key product row used to tie and lose
    ("gross sales", {"scope": "product"}, "product_gross_sale"),
    ("SKU wise gross sale", None, "product_gross_sale"),
    ("gross sale per product", None, "product_gross_sale"),
    ("revenue by variant", None, "product_net_revenue"),
    ("cogs per product", None, "product_net_cogs"),          # total COGS split by product, not product cost
    ("product cost", None, "product_cost"),                  # the order-date COGS component, not the grain
    ("net profit", {"scope": "product"}, "product_net_profit"),    # P&L basis, allocated ad spend (2026-10-08)
    ("margin", {"scope": "product"}, "product_net_margin_pct"),
    ("gross profit", {"scope": "product"}, "product_gross_profit"),
    ("discounts per sku", None, "product_discounts"),
])
def test_product_scope_resolves_to_the_product_grain_metric(v2, text, axes, metric):
    r = v2.resolve_concept(text, axes)
    assert isinstance(r, ResolvedConcept), r
    assert r.metric_id == metric


def test_product_scope_without_a_product_metric_is_refused_not_answered_at_order_grain(v2):
    r = v2.resolve_concept("sales", {"basis": "total", "scope": "product"})  # incl. GST: order level only
    assert r.kind == "unsupported_concept", r
    # Finance (event-date) net sales by product: the P&L spread over its lines (serve.product_pnl, 2026-10-08)
    assert v2.resolve_concept("sales", {"basis": "net", "scope": "product", "date": "finance"}).metric_id == "product_pnl_net_sales"


def test_grain_twins_come_from_the_concepts_scope_axis(v2):
    assert v2.grain_twins("net_sales") == ["product_net_revenue"]
    assert v2.grain_twins("gross_sales") == ["product_gross_sale"]
    assert v2.grain_twins("net_cogs") == ["product_net_cogs"]
    assert v2.grain_twins("net_profit") == ["product_net_profit"]
    assert v2.grain_twins("pnl_net_sales") == ["product_pnl_net_sales"]  # event-date P&L by product
    assert v2.grain_twins("total_sales") == []        # incl. GST: no product twin
    rows = {m["id"]: m for m in v2.bootstrap()["metrics"]}
    assert rows["net_sales"]["grain_twins"] == ["product_net_revenue"]
    for twin in ("product_net_revenue", "product_gross_sale", "product_net_cogs"):
        assert "product_title" in v2.cat.metrics[twin].supported_dimensions


def test_order_pnl_carries_sub_channel_and_order_attributes(v2):
    dims = set(v2.cat.metrics["net_profit"].supported_dimensions)
    assert {"sub_channel", "is_new_customer", "shipping_region", "payment_bucket", "campaign_name"} <= dims


# ---- 2026-10-08 conformed-dimension pass: every slice the warehouse can answer has a metric that carries it

def test_grain_twins_cover_marts_cac_orders_and_refunds(v2):
    # brand × day marts -> session grain (traffic / campaign / device slices)
    assert v2.grain_twins("page_views") == ["session_page_views", "event_page_views"]
    assert v2.grain_twins("product_view_events") == ["session_product_views", "event_product_views"]
    assert v2.grain_twins("ltv") == ["new_customer_ltv"]
    assert v2.grain_twins("bounce_rate") == ["session_bounce_rate"]
    # CAC -> the order-date channel P&L (channel / campaign slices); brand total identical
    assert v2.grain_twins("cac") == ["channel_cac", "product_cac"]  # channel grain first
    # order-level -> product-level (an order holds several products)
    assert v2.grain_twins("orders") == ["product_orders", "channel_orders"]
    assert v2.grain_twins("new_customers") == ["channel_new_customers", "product_new_customers"]
    assert v2.grain_twins("refunded_amount_excl_tax") == ["product_refunded_amount_excl_tax"]
    assert v2.grain_twins("returns_excl_tax") == ["product_returns_excl_tax"]
    # a refund-level amount never splits by the refund lines' product (it would repeat the whole refund)
    assert "product_title" not in v2.cat.metrics["refunded_amount_excl_tax"].supported_dimensions
    assert "product_title" in v2.cat.metrics["refund_count"].supported_dimensions  # refunds containing it
    assert "product_title" in v2.cat.metrics["product_refunded_amount_excl_tax"].supported_dimensions
    assert v2.grain_twins("return_revenue") == ["product_return_revenue"]


def test_twins_carry_the_slices_their_base_metric_lacks(v2):
    cat = v2.cat
    for base, twin, dims in (("page_views", "session_page_views", {"platform", "ad_platform", "campaign_name"}),
                             ("add_to_carts", "event_add_to_carts", {"product_title", "sku"}),
                             ("orders", "product_orders", {"product_title", "sku"})):
        assert not dims <= set(cat.metrics[base].supported_dimensions)
        assert dims <= set(cat.metrics[twin].supported_dimensions), (twin, dims)
    # unit economics carries the platform family and campaign itself (first-order last touch / spend campaign)
    for mid in ("cac", "ltv", "ltv_cac_ratio"):
        assert {"platform", "finance_channel", "ad_platform", "campaign_name"} <= set(cat.metrics[mid].supported_dimensions)


def test_platform_family_is_conformed_across_domains(v2):
    cat = v2.cat
    # one "meta" filter name works on orders, sessions, page views, spend, P&L, refunds and product lines
    for mid in ("orders", "net_sales", "sessions", "session_page_views", "ad_spend", "ctr", "net_profit",
                "channel_cac", "refund_count", "product_net_revenue"):
        assert "ad_platform" in cat.metrics[mid].supported_dimensions, mid
        assert "finance_channel" in cat.metrics[mid].supported_dimensions, mid
    assert {d.id for d in cat.dimensions.values() if d.family == "platform"} == {
        "ad_platform", "finance_channel", "platform", "acquisition_platform"}
    b = v2.bootstrap()
    fam = {d["id"]: d.get("family") for d in b["dimensions"]}
    assert fam["ad_platform"] == fam["acquisition_platform"] == "platform" and fam["campaign_name"] == "campaign"


def test_row_type_is_split_by_value_domain(v2):
    cat = v2.cat
    assert cat.dimensions["row_type"].allowed_values == ["detail", "reconciliation"]
    assert set(cat.dimensions["row_type"].views) == {"pnl"}
    assert cat.dimensions["order_pnl_row_type"].allowed_values == ["orders", "ad_spend"]


def test_cost_per_order_and_campaign_cac_resolve(v2):
    assert v2.resolve_concept("cost per order").metric_id == "cost_per_order"
    assert v2.resolve_concept("cac").metric_id == "cac"
    assert v2.resolve_concept("cac", {"scope": "attributed"}).metric_id == "channel_cac"
    assert v2.resolve_concept("page views", {"scope": "session"}).metric_id == "session_page_views"


async def test_conformed_sibling_answers_a_dimension_the_view_lacks(planner, cube):
    # order_pnl has no traffic `platform`: its conformed sibling finance_channel answers "net profit by platform"
    cube.by_prefix["order_pnl"] = [{"order_pnl.net_profit": "5", "order_pnl.finance_channel": "meta"}]
    out = await planner.run(QueryRequest(measures=["net_profit"], dimensions=["platform"], time_range=SEP,
                                         filters=[FilterSpec(dimension="platform", values=["meta"])]))
    q = cube.queries[-1]
    assert "order_pnl.finance_channel" in q["dimensions"]
    assert {"member": "order_pnl.finance_channel", "operator": "equals", "values": ["meta"]} in q["filters"]
    assert any("conformed sibling 'finance_channel'" in w for w in out["warnings"])
    # customers: the acquisition platform is the only platform member
    cube.by_prefix["customers"] = [{"customers.repeat_rate": "0.3"}]
    await planner.run(QueryRequest(measures=["repeat_rate"], time_range=SEP,
                                   filters=[FilterSpec(dimension="ad_platform", values=["meta"])]))
    assert {"member": "customers.acquisition_platform", "operator": "equals",
            "values": ["meta"]} in cube.queries[-1]["filters"]


async def test_conformed_sibling_never_holds_a_value_it_lacks(planner, cube):
    # email is a traffic platform, not a P&L channel: refused, never silently answered as another slice
    with pytest.raises(PlanError):
        await planner.run(QueryRequest(measures=["net_profit"], time_range=SEP,
                                       filters=[FilterSpec(dimension="platform", values=["email"])]))


async def test_measure_filter_and_text_operators(planner, cube):
    cube.by_prefix["paid_media"] = [{"paid_media.ad_spend": "5", "paid_media.campaign_name": "TH-1"}]
    await planner.run(QueryRequest(
        measures=["ad_spend"], dimensions=["campaign_name"], time_range=SEP,
        filters=[FilterSpec(dimension="ad_spend", operator="gt", values=["10,000"]),
                 FilterSpec(dimension="campaign_name", operator="starts_with", values=["TH-"]),
                 FilterSpec(dimension="campaign_name", operator="notContains", values=["TEST"])]))
    flt = cube.queries[-1]["filters"]
    assert {"member": "paid_media.ad_spend", "operator": "gt", "values": ["10000"]} in flt
    assert {"member": "paid_media.campaign_name", "operator": "startsWith", "values": ["TH-"]} in flt
    assert {"member": "paid_media.campaign_name", "operator": "notContains", "values": ["TEST"]} in flt
    with pytest.raises(PlanError):  # a metric filter is a comparison on a number
        await planner.run(QueryRequest(measures=["ad_spend"], time_range=SEP,
                                       filters=[FilterSpec(dimension="ad_spend", operator="contains", values=["x"])]))


async def test_refund_level_amount_is_never_split_by_its_lines_product(planner, cube):
    with pytest.raises(PlanError) as exc:
        await planner.run(QueryRequest(measures=["refunded_amount_excl_tax"], time_range=SEP,
                                       filters=[FilterSpec(dimension="product_title", values=["X"])]))
    assert exc.value.suggestions == ["product_refunded_amount_excl_tax"]
    with pytest.raises(PlanError):
        await planner.run(QueryRequest(measures=["refunded_amount_excl_tax"], dimensions=["product_title"],
                                       time_range=SEP))


def test_spelled_out_ad_ratios_and_lpvs_resolve_to_their_own_metric(v2):
    # the planner's slot reader spells abbreviations out; "cost per click" fell to CTR and was deduped away
    for text, mid in (("cost per click", "cpc"), ("cost per mille", "cpm"),
                      ("cost per thousand impressions", "cpm"), ("click-through rate", "ctr"),
                      ("LPVs (landing page views)", "landing_page_views"), ("lpvs", "landing_page_views"),
                      ("page views", "page_views"), ("impressions", "impressions")):
        assert v2.resolve_concept(text).metric_id == mid, text


def test_product_slices_reach_every_domain(v2):
    cat = v2.cat
    # twins from any scope: a session / attributed metric reaches the product grain too
    assert v2.grain_twins("add_to_carts")[0] == "event_add_to_carts"
    assert v2.grain_twins("channel_orders") == ["product_orders", "orders"]
    # spend and ROAS by product: the allocation-model twins on the product view
    assert v2.grain_twins("ad_spend") == ["product_ad_spend"]
    assert v2.grain_twins("net_roas") == ["product_net_roas"]
    assert v2.grain_twins("gross_roas") == ["product_gross_roas"]
    assert v2.grain_twins("mer") == ["product_mer"]
    for mid in ("product_ad_spend", "product_net_roas"):
        assert {"product_title", "sku", "ad_platform", "campaign_name"} <= set(cat.metrics[mid].supported_dimensions)
    # order / session / customer measures with no product grain slice by the proxy members of the product family
    assert "basket_product_title" in cat.metrics["aov"].supported_dimensions
    assert "basket_product_title" in cat.metrics["payment_amount"].supported_dimensions
    assert "viewed_product_title" in cat.metrics["conversion_rate"].supported_dimensions
    assert "first_order_product_title" in cat.metrics["repeat_rate"].supported_dimensions
    families = {d.id: d.family for d in cat.dimensions.values()}
    assert {families[d] for d in ("product_title", "basket_product_title", "viewed_product_title",
                                  "first_order_product_title")} == {"product"}
    # ... never where a grain twin carries the real member (net sales by product = line revenue)
    for mid in ("net_sales", "orders", "add_to_carts"):
        supported = set(cat.metrics[mid].supported_dimensions)
        assert not {"basket_product_title", "viewed_product_title"} & supported, mid
    # attribution paths / touches by the order's / session's campaign; funnel purchases by platform
    assert {"finance_channel", "campaign_name"} <= set(cat.metrics["avg_touch_count"].supported_dimensions)
    assert "campaign_name" in cat.metrics["touches"].supported_dimensions
    assert "basket_product_title" in cat.metrics["touches"].supported_dimensions  # touches of orders containing it
    assert {"platform", "ad_platform"} <= set(cat.metrics["funnel_purchases"].supported_dimensions)


def test_a_terms_own_axis_words_win_over_the_questions(v2):
    # live 2026-10-08: "… net ROAS … product gross sale" passed basis=gross (from "gross sale") with "net ROAS",
    # which resolved to product_gross_roas and was labelled net ROAS
    assert v2.resolve_concept("net roas", {"basis": "gross", "scope": "product"}).metric_id == "product_net_roas"
    assert v2.resolve_concept("roas", {"basis": "gross"}).metric_id == "gross_roas"  # an open axis is still filled



def test_several_values_of_one_axis_are_compared_not_a_scope(v2):
    # golden Q17 2026-10-09: "sales by Meta campaign, Google sub-channel, organic, WhatsApp" read channel=meta
    assert "channel" not in v2.question_axes("Show sales by Meta campaign, Google sub-channel, organic and WhatsApp")
    assert v2.question_axes("meta sales last week")["channel"] == "meta"
    assert v2.question_axes("product cost last month")["scope"] == "all"  # the longer phrase wins over "product"


def test_every_composition_is_a_same_view_signed_sum_of_additive_metrics(v2):
    """formula.composition (Cube meta.composition) is what breakdowns and bridges reconcile against; the loader
    rejects a term on another view or a non-additive term, so every declared tree here is usable as is."""
    composed = {m.id: m for m in v2.cat.metrics.values() if m.formula.composition}
    assert composed, "no metric declares a composition"
    for m in composed.values():
        for t in m.formula.composition:
            part = v2.cat.metrics[t.metric]
            assert part.cube_mapping.view == m.cube_mapping.view
            assert part.aggregation == m.aggregation == "additive"
            assert t.sign in (1, -1)


def test_a_hyphenated_axis_word_is_read_with_axes_passed(v2):
    """'break-even ROAS' resolved to be_roas bare but to net_roas whenever the agent passed axes: the axis
    keywords were matched against the hyphenated text. A hyphen and a space spell the same word."""
    for axes, want in (({}, "be_roas"), ({"date": "order"}, "be_roas"), ({"scope": "product"}, "product_be_roas"),
                       ({"date": "finance"}, "pnl_be_roas")):
        assert v2.resolve_concept("break-even roas", axes=axes).metric_id == want
