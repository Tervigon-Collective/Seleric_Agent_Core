"""Semantic v3 catalogue + planner (catalogue_v3/, doc/semantic_v3): one metric with a date_axis parameter (D-28),
event-date aliases, axis refusals (D-31 / D-34), native clocks (D-36), the Undated member (D-32), hidden twin (D-13r).

Everything runs against the committed catalogue_v3/ and a Cube double — no live Cube, no ClickHouse."""
from __future__ import annotations

import asyncio

import pytest
import yaml

from seleric_mcp.app.models import PlanError, QueryRequest, TimeRange
from seleric_mcp.app.query_planner import QueryPlanner
from seleric_mcp.catalogue_service.loader import load_catalogue
from seleric_mcp.catalogue_service.service import CatalogueService, ResolvedConcept, UnsupportedConcept
from seleric_mcp.config import PROJECT_ROOT
from conftest import FakeCube

V3_DIR = PROJECT_ROOT / "catalogue_v3"
V2_DIR = PROJECT_ROOT / "catalogue_v2"
V2_MAP = PROJECT_ROOT / "catalogue" / "migrations" / "v2_id_map.yaml"
CAPABILITY_MAP = PROJECT_ROOT / "doc" / "semantic_v3" / "CAPABILITY_MAP.yaml"
SEP = TimeRange(start="2026-09-01", end="2026-09-30")


@pytest.fixture(scope="module")
def v3() -> CatalogueService:
    return CatalogueService(load_catalogue(V3_DIR))


@pytest.fixture()
def cube() -> FakeCube:
    return FakeCube()


@pytest.fixture()
def planner(v3, cube, result_store) -> QueryPlanner:
    return QueryPlanner(v3, cube, result_store)


def run(planner: QueryPlanner, measures: list[str], axis: str | None = None, **kw) -> dict:
    req = QueryRequest(measures=measures, time_range=SEP, date_axis=axis, **kw)
    return asyncio.run(planner.run(req))


# ---------------------------------------------------------------- catalogue shape

def test_catalogue_v3_loads_as_semantic_v3(v3):
    assert v3.is_v2 and v3.is_v3
    assert v3.cat.semantic_version == 3
    assert len(v3.cat.views) == 29   # 18 + 9 ops views (stock / purchasing / quality / vendors) + product_cost_changes + web_funnel_hourly
    assert {"finance", "product_profit", "product_ads"} <= set(v3.cat.views)
    assert "pnl" not in v3.cat.views and "order_pnl" not in v3.cat.views and "product_pnl" not in v3.cat.views


def test_every_v2_metric_id_still_answers(v3):
    """No capability loss: each of the 233 v2 ids is a v3 metric or an alias of one."""
    v2_ids = set(yaml.safe_load(V2_MAP.read_text())["v2_metrics"])
    live = set(v3.cat.metrics) | set(v3.cat.metric_aliases)
    assert len(v2_ids) == 233
    assert v2_ids <= live, sorted(v2_ids - live)


def test_every_metric_declares_its_date_axis(v3):
    for m in v3.cat.metrics.values():
        assert m.date_axis in ("switchable", "placement_only", "native", "cohort_only"), m.id
        if m.date_axis == "cohort_only":
            assert m.date_axis_refusal and "order_date" in m.date_axis_refusal, m.id
        if m.date_axis_dimensions:
            assert set(m.date_axis_dimensions) == {"order_date", "event_date"}, m.id


def test_event_date_family_is_aliases_of_a_base_metric(v3):
    """pnl_* / product_pnl_* are not metrics: they are event_date aliases (D-28)."""
    cap = yaml.safe_load(CAPABILITY_MAP.read_text())["metrics"]
    aliases = {k for k, v in cap.items() if v.get("date_axis") == "alias"}
    assert len(aliases) == 50
    for aid in aliases:
        a = v3.cat.metric_aliases[aid]
        assert a.date_axis == "event_date" and a.id not in v3.cat.metrics
        assert a.metric in v3.cat.metrics, aid
        assert a.refused is None or a.metric.startswith("product_")  # the allocated family only


def test_alias_ids_match_the_capability_map(v3):
    cap = yaml.safe_load(CAPABILITY_MAP.read_text())["metrics"]
    for aid, row in cap.items():
        if row.get("date_axis") == "alias":
            assert v3.cat.metric_aliases[aid].metric == row["alias_of"]["metric"], aid


def test_website_lpv_is_not_meta_only_and_the_platform_twin_is_hidden(v3):
    lpv = v3.cat.metrics["landing_page_views"]
    assert lpv.valid_for == {}  # website LPV exists for Meta and Google (D-13r)
    twin = v3.cat.metrics["platform_reported_landing_page_views"]
    assert twin.hidden_by_default
    assert "platform_reported_landing_page_views" not in {m.id for m in v3.search("landing page views").matches}
    assert "platform_reported_landing_page_views" not in {m["id"] for m in v3.bootstrap()["metrics"]}
    assert v3.resolve_metric_id("platform_reported_landing_page_views") is not None  # reachable by id


def test_v2_catalogue_is_untouched_by_v3():
    v2 = CatalogueService(load_catalogue(V2_DIR))
    assert v2.cat.semantic_version == 2 and not v2.is_v3
    assert v2.cat.metric_aliases == {}
    assert all(m.date_axis is None for m in v2.cat.metrics.values())
    assert "pnl_net_profit" in v2.cat.metrics  # still a real id there (rollback = env flip)
    assert v2.metric_alias("pnl_net_profit") is None


# ---------------------------------------------------------------- resolution

def test_default_concept_is_order_date_and_pnl_wording_is_event_date(v3):
    r = v3.resolve_concept("net profit")
    assert isinstance(r, ResolvedConcept) and r.metric_id == "net_profit" and r.date_axis == "order_date"
    r = v3.resolve_concept("P&L net profit")
    assert r.metric_id == "net_profit" and r.date_axis == "event_date"  # same metric, a parameter
    r = v3.resolve_concept("returns processed in September")
    assert r.date_axis == "event_date"


def test_ambiguous_wording_stays_on_the_default(v3):
    """D-33: 'returns in September' is NOT an event-date request; the Agent adds the one-line event figure."""
    assert v3.question_axes("returns in September") == {}
    assert v3.question_axes("returns processed in September") == {"date": "finance"}


def test_event_date_alias_resolves_to_base_metric_and_axis(v3):
    r = v3.resolve_concept("pnl_net_profit")
    assert r.metric_id == "net_profit" and r.date_axis == "event_date"
    assert v3.resolve_metric_id("pnl_net_profit")[0] == "net_profit"


def test_cohort_only_metric_refuses_the_event_axis_at_resolution(v3):
    r = v3.resolve_concept("return rate", {"date": "finance"})
    assert isinstance(r, UnsupportedConcept) and "order_date" in r.reason


def test_allocated_event_date_alias_is_refused_with_a_pointer(v3):
    r = v3.resolve_concept("product_pnl_net_profit")
    assert isinstance(r, UnsupportedConcept) and "allocated" in r.reason and "order_date" in r.reason


# ---------------------------------------------------------------- planner: date axis

def test_default_axis_is_order_date(planner, cube):
    cube.by_prefix["finance"] = [{"finance.net_sales": "10"}]
    out = run(planner, ["order_pnl_net_sales"])
    td = cube.queries[0]["timeDimensions"][0]["dimension"]
    assert td == "finance.order_date"
    assert out["date_axis"] == "order_date" and out["date_axis_applied"] == {"order_pnl_net_sales": "order_date"}
    assert "undated" not in out


def test_event_axis_moves_the_date_filter_and_returns_the_undated_member(planner, cube):
    cube.by_prefix["finance"] = [{"finance.net_sales": "10"}]
    out = run(planner, ["order_pnl_net_sales"], "event_date")
    main, undated_q = cube.queries[0], cube.queries[1]
    assert main["timeDimensions"][0]["dimension"] == "finance.event_date"
    # Undated: no date range at all (whatever the range), flagged rows only (D-32)
    assert "timeDimensions" not in undated_q
    assert {"member": "finance.is_undated", "operator": "equals", "values": ["true"]} in undated_q["filters"]
    assert out["undated"]["label"] == "Undated" and "dated only" in out["undated"]["note"]
    assert out["date_axis"] == "event_date"
    assert any("decision date" in w for w in out["warnings"])


def test_event_date_alias_implies_the_event_axis(planner, cube):
    cube.by_prefix["finance"] = [{"finance.net_profit": "5"}]
    out = run(planner, ["pnl_net_profit"])
    assert cube.queries[0]["timeDimensions"][0]["dimension"] == "finance.event_date"
    assert out["date_axis"] == "event_date"
    assert out["rows"][0]["pnl_net_profit"] == out["rows"][0]["net_profit"]  # the asked id is a column


def test_alias_contradicting_the_request_axis_is_refused(planner):
    with pytest.raises(PlanError, match="One date axis per query"):
        run(planner, ["pnl_net_profit"], "order_date")


def test_mixing_event_alias_with_a_plain_switchable_metric_is_refused(planner):
    with pytest.raises(PlanError, match="One date axis per query"):
        run(planner, ["pnl_net_profit", "order_pnl_net_sales"])


def test_explicit_axis_lets_plain_and_alias_ids_share_one_query(planner, cube):
    cube.by_prefix["finance"] = [{"finance.net_profit": "5", "finance.net_sales": "9"}]
    run(planner, ["pnl_net_profit", "order_pnl_net_sales"], "event_date")
    assert len([q for q in cube.queries if q.get("timeDimensions")]) == 1  # one date axis, one main query


@pytest.mark.parametrize("mid", ["return_rate", "net_aov", "repeat_rate", "unit_return_rate", "product_net_roas", "product_ltv_cac_ratio"])
def test_cohort_only_metrics_refuse_event_date(planner, mid):
    with pytest.raises(PlanError) as e:
        run(planner, [mid], "event_date")
    assert "order_date" in str(e.value)


def test_cohort_only_metrics_still_answer_on_order_date(planner, cube):
    cube.by_prefix["finance"] = [{"finance.return_rate": "0.1"}]
    out = run(planner, ["return_rate"], "order_date")
    assert out["date_axis_applied"] == {"return_rate": "order_date"}


def test_native_metrics_ignore_the_axis_and_say_so(planner, cube):
    cube.by_prefix["paid_media"] = [{"paid_media.ad_spend": "3"}]
    out = run(planner, ["ad_spend"], "event_date")
    assert cube.queries[0]["timeDimensions"][0]["dimension"] == "paid_media.report_date"
    assert out["date_axis_applied"]["ad_spend"].startswith("native")
    assert any("own clock" in w for w in out["warnings"])
    assert "undated" not in out


def test_placement_only_metrics_are_identical_on_both_axes(planner, cube):
    cube.by_prefix["finance"] = [{"finance.gross_sales": "7"}]
    out = run(planner, ["gross_sales"], "event_date")
    assert "identical on both axes" in out["date_axis_applied"]["gross_sales"]


def test_event_axis_ratio_is_labelled(planner, cube):
    cube.by_prefix["finance"] = [{"finance.net_roas": "1.2", "finance.contribution_margin": "1", "finance.ad_spend": "1"}]
    out = run(planner, ["net_roas"], "event_date")
    assert any("NULL when event-axis net sales are not positive" in w for w in out["warnings"])


def test_allocated_alias_is_refused_by_the_planner(planner):
    with pytest.raises(PlanError, match="order_date"):
        run(planner, ["product_pnl_net_profit"])


def test_same_measure_twin_id_answers_as_its_base(planner, cube):
    cube.by_prefix["product_profit"] = [{"product_profit.net_sales": "4"}]
    out = run(planner, ["product_net_revenue"])  # v2 product-view id
    assert cube.queries[0]["measures"] == ["product_profit.net_sales"]
    assert out["rows"][0]["product_net_revenue"] == out["rows"][0]["product_order_net_sales"]


def test_drilldown_inherits_the_date_axis(planner, cube, result_store):
    cube.by_prefix["finance"] = [{"finance.net_sales": "10"}]
    out = run(planner, ["order_pnl_net_sales"], "event_date")
    out2 = asyncio.run(planner.drilldown(out["query_id"], ["finance_channel"], []))
    assert out2["date_axis"] == "event_date"
    mains = [q for q in cube.queries if q.get("timeDimensions") and q["timeDimensions"][0]["dimension"].startswith("finance.")]
    assert mains[-1]["timeDimensions"][0]["dimension"] == "finance.event_date"


def test_hourly_granularity_keeps_working_on_the_default_axis(planner, cube):
    cube.by_prefix["commerce"] = [{"commerce.orders": "1"}]
    out = run(planner, ["orders"], granularity="hour")
    assert cube.queries[0]["timeDimensions"][0]["dimension"] == "commerce.order_created_at"
    assert out["date_axis"] == "order_date"


# ---------------------------------------------------------------- v2 behaviour is unchanged (rollback safety)

def test_v2_planner_ignores_the_date_axis_parameter(cube, result_store):
    v2 = CatalogueService(load_catalogue(V2_DIR))
    pl = QueryPlanner(v2, cube, result_store)
    cube.by_prefix["commerce"] = [{"commerce.net_sales": "1"}]
    out = run(pl, ["net_sales"], "event_date")
    assert "date_axis" not in out
    assert len(cube.queries) == 1  # no Undated query on a v2 catalogue
    assert cube.queries[0]["timeDimensions"][0]["dimension"] == "commerce.order_date"


def test_attribution_slice_carries_the_coverage_of_the_period(planner, cube):
    """L-01: a channel / campaign slice also reports the share of orders credited under the default model."""
    cube.by_prefix["finance"] = [{"finance.net_sales": "1", "finance.finance_channel": "meta"}]
    cube.by_prefix["commerce"] = [{"commerce.attribution_model_used": "last_non_direct_v1", "commerce.orders": "90"},
                                  {"commerce.attribution_model_used": "last_touch_fallback", "commerce.orders": "10"}]
    out = run(planner, ["order_pnl_net_sales"], dimensions=["finance_channel"])
    cov = out["attribution_coverage"]
    assert cov["coverage_pct"] == 90.0 and cov["orders_by_model_used"]["last_touch_fallback"] == 10
    assert cov["default_model"] == "last_non_direct"


def test_no_coverage_block_without_an_attribution_slice(planner, cube):
    cube.by_prefix["finance"] = [{"finance.net_sales": "1"}]
    assert "attribution_coverage" not in run(planner, ["order_pnl_net_sales"])


def test_google_only_dimension_scopes_the_query_and_refuses_meta(planner, cube):
    """D-14: device / network are Google breakdowns; a slice by one is scoped to Google (disclosed)."""
    cube.by_prefix["paid_media"] = [{"paid_media.ad_spend": "5", "paid_media.device": "MOBILE"}]
    out = run(planner, ["ad_spend"], dimensions=["device"])
    assert {"member": "paid_media.ad_platform", "operator": "equals", "values": ["google"]} in cube.queries[0]["filters"]
    assert any("exists for ad_platform = google only" in w for w in out["warnings"])
    with pytest.raises(PlanError, match="google only"):
        run(planner, ["ad_spend"], dimensions=["device"],
            filters=[{"dimension": "ad_platform", "operator": "equals", "values": ["meta"]}])


def test_ad_level_slice_discloses_the_performance_max_gap(planner, cube):
    """L-03: Google PMax has no ad group / ad level; the answer says so whenever the slice is used."""
    cube.by_prefix["paid_media"] = [{"paid_media.ad_spend": "5", "paid_media.ad_name": "x"}]
    out = run(planner, ["ad_spend"], dimensions=["ad_name"])
    assert any("Performance Max" in w for w in out["warnings"])


def test_audience_slice_of_website_lpv_points_at_the_platform_twin(planner):
    """D-13r: no silent swap of a platform number under the website id; the refusal names the diagnostic twin."""
    with pytest.raises(PlanError) as e:
        run(planner, ["landing_page_views"], dimensions=["age"])
    assert "platform_reported_landing_page_views" in str(e.value)


def test_account_slice_or_filter_of_website_lpv_is_refused(planner):
    """Phase 7 (owner 2026-10-17): website sessions carry no ad account, so an account slice or filter would read 0
    LPV against real spend. Refused with a pointer, not answered with 0."""
    for mid in ("landing_page_views", "cost_per_landing_page_view"):
        with pytest.raises(PlanError) as e:
            run(planner, [mid], dimensions=["account_id"])
        assert "no ad account" in str(e.value)
        with pytest.raises(PlanError) as e:
            run(planner, [mid], filters=[{"dimension": "account_id", "operator": "equals", "values": ["1"]}])
        assert "no ad account" in str(e.value)


def test_v2_report_date_slice_reads_as_the_query_axis(planner, cube):
    """No capability loss: pnl_* ids were sliced by report_date (the event clock); on v3 it is the axis of the query."""
    cube.by_prefix["finance"] = [{"finance.net_profit": "5", "finance.event_date": "2026-09-01"}]
    out = run(planner, ["pnl_net_profit"], dimensions=["report_date"])
    assert "finance.event_date" in cube.queries[0]["dimensions"]
    assert any("'report_date' read as event_date" in w for w in out["warnings"])


def test_product_slices_of_the_v2_product_view_exist(v3):
    for d in ("platform", "channel", "medium_group", "shipping_city", "shipping_state", "is_cod", "payment_method",
              "payment_gateway", "is_cost_set", "price", "in_catalogue"):
        assert "product_profit" in v3.cat.dimensions[d].views, d
    for d in ("adset_name", "order_date", "platform", "is_cost_set"):
        assert "product_ads" in v3.cat.dimensions[d].views, d


def test_definition_lookup_answers_alias_and_retired_ids(v3):
    """catalogue_get_metric on an event-date alias, a same-measure twin, and a v1 id whose replacement is an alias."""
    for ref, base in (("pnl_net_profit", "net_profit"), ("product_gross_sale", "product_order_gross_sales"),
                      ("cac", "channel_cac"), ("ad_channel_net_profit", "net_profit")):
        m, notice = v3.lookup_metric(ref)
        assert m.id == base and notice, ref


def test_cube_refusal_is_a_plan_error_not_a_crash(planner, cube):
    """A Cube v3 `REFUSED:` (spend-only brand, axis mixing ...) reaches the caller as a PlanError with the user text."""
    from seleric_mcp.semantic_layer.cube_client import CubeRefused, _refusal_reason

    body = '{"error":"Error: REFUSED: finance.net_profit for a spend-only brand (brand 28). Ask for ad spend."}'
    assert _refusal_reason("Cube load failed: 500 — " + body) == "finance.net_profit for a spend-only brand (brand 28). Ask for ad spend."
    assert _refusal_reason("Cube load failed: 500 — plain failure") is None

    async def refuse(query):
        raise CubeRefused("finance.net_profit for a spend-only brand (brand 28). Ask for ad spend.")

    cube.load = refuse
    with pytest.raises(PlanError, match="spend-only brand"):
        run(planner, ["net_profit"])


def test_every_v2_dimension_id_resolves_to_itself(v3):
    """No dimension changes meaning silently: each v2 dimension id resolves to the same id in v3 (the v2 P&L
    `row_type` once resolved to `allocation_row_type` through the shared display name)."""
    from seleric_mcp.catalogue_service.service import CatalogueService as S

    v2 = S(load_catalogue(V2_DIR))
    moved = {d: (v3.resolve_dimension(d).id if v3.resolve_dimension(d) else None) for d in v2.cat.dimensions}
    assert {d: r for d, r in moved.items() if r != d} == {"row_type": "order_pnl_row_type"}


def test_report_date_on_a_cohort_only_metric_reads_as_order_date(planner, cube):
    cube.by_prefix["finance"] = [{"finance.ltv": "5", "finance.order_date": "2026-09-01"}]
    out = run(planner, ["ltv"], dimensions=["report_date"])
    assert "finance.order_date" in cube.queries[0]["dimensions"]
    assert any("'report_date' read as order_date" in w for w in out["warnings"])


# ---------------------------------------------------------------- hourly twins

def test_hour_granularity_routes_the_funnel_metrics_to_the_hourly_funnel(planner, cube):
    """last-touch funnel figures by hour: each web_funnel metric has an hourly binding on web_funnel_hourly (report_hour)."""
    out = asyncio.run(planner.run(QueryRequest(measures=["funnel_purchases", "funnel_revenue", "bounce_rate"], time_range=SEP, granularity="hour")))
    q = cube.queries[-1]
    assert sorted(q["measures"]) == sorted(f"web_funnel_hourly.{m}" for m in ("funnel_purchases", "funnel_revenue", "bounce_rate"))
    assert q["timeDimensions"][0]["dimension"] == "web_funnel_hourly.report_hour"
    assert any("hourly" in w for w in out["warnings"])


def test_day_granularity_stays_on_the_daily_funnel(planner, cube):
    asyncio.run(planner.run(QueryRequest(measures=["funnel_purchases"], time_range=SEP, granularity="day")))
    assert cube.queries[-1]["measures"] == ["web_funnel.funnel_purchases"]

