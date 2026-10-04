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
    assert len(v2.cat.metrics) == 123
    assert len(v2.cat.views) == 18
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
    assert len(retired) == len(v2.cat.retired) == 111
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
    with pytest.raises(PlanError):
        planner.hierarchy_targets(out["query_id"], "ad")  # commerce has no ad_platform level


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
    assert len(b["metrics"]) == 120  # 123 minus the 3 unavailable checkout-timing metrics
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
