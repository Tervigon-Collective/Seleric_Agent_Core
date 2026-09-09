"""Tests for the actual MCP tool surface (gateway/server.py) — not just the
QueryPlanner underneath it. Catches interface-boundary gaps like the one found
2026-07-11: `sort` was added to QueryRequest/QueryPlanner and tested there, but
the `metrics_query` tool function itself had no `sort` parameter, so the fix
was unreachable by any real MCP caller. These tests call the actual registered
tool functions (via FastMCP's tool manager), with a fake Cube client swapped
in post-construction so no network access is needed.
"""

from __future__ import annotations

import pytest

from seleric_mcp.app.query_planner import QueryPlanner
from seleric_mcp.gateway.server import build_server


def _tool_fn(mcp, name: str):
    return mcp._tool_manager.get_tool(name).fn


@pytest.fixture()
def built_server(settings, fake_cube, result_store):
    mcp = build_server(settings)
    ctx = mcp._seleric_ctx
    # Swap the real (network-backed) planner for one wired to the fake cube,
    # reusing the server's own catalogue so this exercises the real catalogue,
    # not a test double of it.
    ctx.result_store = result_store  # keep ctx.result_store and the planner's store the
    # same instance, matching production wiring (AppContext.__init__ passes one
    # ResultStore to both) — metrics_drilldown's scope check reads ctx.result_store
    # directly, so a mismatched pair here silently breaks it (caught by
    # test_metrics_drilldown_denied_without_required_scope).
    ctx.planner = QueryPlanner(ctx.catalogue, fake_cube, ctx.result_store)
    return mcp, ctx


async def test_metrics_query_tool_accepts_and_threads_sort(built_server, fake_cube):
    mcp, ctx = built_server
    fake_cube.by_prefix["product_performance"] = [
        {"product_performance.product_title": "Scratch Lounge",
         "product_performance.net_line_revenue_ex_gst": "900"}
    ]
    fn = _tool_fn(mcp, "metrics_query")
    out = await fn(
        measures=["product_net_revenue"],
        dimensions=["product_title"],
        time_range={"start": "2026-07-09", "end": "2026-07-11"},
        sort=[{"field": "product_net_revenue", "direction": "desc"}],
        limit=10,
    )
    q = fake_cube.queries[0]
    assert q["order"] == {"product_performance.net_line_revenue_ex_gst": "desc"}
    assert q["limit"] == 10
    assert out["provenance"]["cube_view"] == "product_performance"


async def test_metrics_query_tool_without_sort_is_unchanged(built_server, fake_cube):
    """sort defaults to empty — no behavior change for every existing caller
    that doesn't pass it."""
    mcp, ctx = built_server
    fake_cube.by_prefix["commerce_orders"] = [
        {"commerce_orders.dashboard_net_sales_excl_gst": "100"}
    ]
    fn = _tool_fn(mcp, "metrics_query")
    out = await fn(measures=["commerce_net_revenue"], time_range={"preset": "last_7d"})
    assert "order" not in fake_cube.queries[0]
    assert "error" not in out


async def test_metrics_query_tool_surfaces_filter_value_case_correction(built_server, fake_cube):
    """The live 'Meta' vs 'meta' failure class, exercised through the real tool
    function end to end on payment_bucket's allowed_values."""
    mcp, ctx = built_server
    fake_cube.by_prefix["commerce_orders"] = [{"commerce_orders.orders": "1"}]
    fn = _tool_fn(mcp, "metrics_query")
    out = await fn(
        measures=["orders"],
        filters=[{"dimension": "payment_bucket", "operator": "equals", "values": ["COD"]}],
        time_range={"preset": "last_7d"},
    )
    bucket_filters = [
        f for f in fake_cube.queries[0]["filters"]
        if f["member"] == "commerce_orders.payment_bucket"
    ]
    assert bucket_filters[0]["values"] == ["cod"]
    assert any("case-corrected" in w for w in out["warnings"])


async def test_metrics_query_tool_rejects_unknown_metric(built_server):
    mcp, ctx = built_server
    fn = _tool_fn(mcp, "metrics_query")
    out = await fn(measures=["not_a_real_metric"], time_range={"preset": "last_7d"})
    assert "error" in out
    assert "suggestions" in out


async def test_all_registered_tools_are_the_expected_set(built_server):
    """Guards against a tool silently disappearing or a rename breaking every
    example in this test file without anything else catching it."""
    mcp, ctx = built_server
    names = {t.name for t in mcp._tool_manager.list_tools()}
    analytics_and_actions = {
        "catalogue_search_metrics",
        "catalogue_get_metric",
        "catalogue_get_ontology",
        "catalogue_related_metrics",
        "catalogue_list_dimensions",
        "catalogue_list_brands",
        "catalogue_resolve_brand",
        "catalogue_resolve_term",
        "catalogue_resolve_dimension",
        "modules_list",
        "metrics_query",
        "metrics_drilldown",
        "insights_explain",
        "actions_list_available",
        "actions_propose",
        "actions_commit",
        "actions_status",
    }
    meta_ads = {
        "meta_accounts_list", "meta_accounts_get",
        "meta_campaigns_list", "meta_campaigns_get", "meta_campaigns_create",
        "meta_campaigns_update", "meta_campaigns_set_status", "meta_campaigns_update_budget",
        "meta_adsets_list", "meta_adsets_get", "meta_adsets_create", "meta_adsets_update",
        "meta_adsets_set_status", "meta_adsets_update_budget", "meta_adsets_update_targeting",
        "meta_ads_list", "meta_ads_get", "meta_ads_create", "meta_ads_set_status",
        "meta_assets_upload_image", "meta_assets_upload_video",
        "meta_creatives_create_image", "meta_creatives_create_video", "meta_creatives_preview",
        "meta_insights_query",
    }
    google_ads = {
        "google_accounts_list_accessible", "google_query_gaql",
        "google_campaigns_list", "google_campaigns_get", "google_campaigns_set_status",
        "google_budgets_create", "google_campaigns_create",
    }
    assert names == analytics_and_actions | meta_ads | google_ads


# ---------- access_policy.scopes enforcement (was declared, never checked) ----------
# Every catalogue metric declares access_policy.scopes (default ["metrics:read"]),
# and caller-scope enforcement already existed for actions (actions/broker.py) —
# but metrics_query/metrics_drilldown never checked it, so a caller with zero
# granted scopes could query any metric. Fixed via _check_metric_scopes in
# gateway/server.py, mirroring the existing actions pattern exactly.

@pytest.fixture()
def built_server_no_scopes(fake_cube, result_store, tmp_path):
    """Same as built_server but the caller has been granted no scopes at all —
    exercises the denial path without needing to alter any real metric file."""
    from seleric_mcp.config import Settings

    settings = Settings(
        cube_api_url="http://cube.test",
        seleric_api_key="test-key",
        cubejs_api_secret="",
        pipeboard_mcp_url="http://pipeboard.test",
        pipeboard_token="pb-token",
        write_enabled=False,
        mcp_service_token="svc-token",
        approval_secret="approval-secret",
        caller_scopes=frozenset(),  # <- the only difference from the `settings` fixture
        db_path=tmp_path / "test_no_scopes.db",
    )
    mcp = build_server(settings)
    ctx = mcp._seleric_ctx
    ctx.result_store = result_store  # keep ctx.result_store and the planner's store the
    # same instance, matching production wiring (AppContext.__init__ passes one
    # ResultStore to both) — metrics_drilldown's scope check reads ctx.result_store
    # directly, so a mismatched pair here silently breaks it (caught by
    # test_metrics_drilldown_denied_without_required_scope).
    ctx.planner = QueryPlanner(ctx.catalogue, fake_cube, ctx.result_store)
    return mcp, ctx


async def test_metrics_query_denied_without_required_scope(built_server_no_scopes):
    mcp, ctx = built_server_no_scopes
    fn = _tool_fn(mcp, "metrics_query")
    out = await fn(measures=["commerce_net_revenue"], time_range={"preset": "last_7d"})
    assert "error" in out
    assert out["missing_scopes_by_metric"]["commerce_net_revenue"] == ["metrics:read"]
    assert "rows" not in out  # denied before Cube was ever queried


async def test_metrics_query_allowed_with_required_scope(built_server, fake_cube):
    """The default caller_scopes fixture ({"metrics:read", ...}) covers every
    catalogue metric's default scope — confirms the check doesn't break the
    common case, only the actually-unauthorized one."""
    mcp, ctx = built_server
    fake_cube.by_prefix["commerce_orders"] = [
        {"commerce_orders.dashboard_net_sales_excl_gst": "100"}
    ]
    fn = _tool_fn(mcp, "metrics_query")
    out = await fn(measures=["commerce_net_revenue"], time_range={"preset": "last_7d"})
    assert "error" not in out
    assert out["provenance"]["cube_view"] == "commerce_orders"


async def test_metrics_drilldown_denied_without_required_scope(built_server_no_scopes, fake_cube):
    mcp, ctx = built_server_no_scopes
    from datetime import date

    from seleric_mcp.app.models import QueryRequest, TimeRange

    # Seed a stored parent query directly through the planner (bypassing the
    # tool's own scope check, which we're not testing here) so there's
    # something to drill into.
    fake_cube.by_prefix["commerce_orders"] = [{"commerce_orders.orders": "1"}]
    parent = await ctx.planner.run(
        QueryRequest(measures=["orders"], time_range=TimeRange(start=date(2026, 6, 1), end=date(2026, 6, 30)))
    )
    fn = _tool_fn(mcp, "metrics_drilldown")
    out = await fn(parent["query_id"], target_dimensions=["payment_method"])
    assert "error" in out
    assert out["missing_scopes_by_metric"]["orders"] == ["metrics:read"]


# ---------- freshness fail-closed gate (metrics_query / metrics_drilldown) ----------
# Views declare expected_cadence in catalogue/views.yaml (e.g. "daily, T-1, IST");
# previously freshness was only *reported* (docs://data-freshness) and never
# blocked a numeric answer. stale_views() in gateway/server.py now refuses when a
# queried view's latest data date is confirmably older than cadence lag + grace.
# Probe failures / unparseable cadences never block (fail-closed only on
# CONFIRMED staleness, so a transient Cube hiccup can't take every metric down).

@pytest.fixture()
def built_server_fake_freshness(built_server, fake_cube):
    """built_server with the AppContext's own cube client ALSO swapped to the
    fake, so the freshness probe (ctx.cube.load) is programmable — mirrors
    production wiring where planner and AppContext share one client."""
    mcp, ctx = built_server
    ctx.cube = fake_cube
    return mcp, ctx


async def test_metrics_query_refuses_when_view_confirmed_stale(built_server_fake_freshness, fake_cube):
    mcp, ctx = built_server_fake_freshness
    # Probe on commerce_orders.order_date returns an ancient date -> stale.
    fake_cube.by_prefix["commerce_orders"] = [
        {"commerce_orders.order_date": "2020-01-01T00:00:00.000", "commerce_orders.orders": "5"}
    ]
    fn = _tool_fn(mcp, "metrics_query")
    out = await fn(measures=["orders"], time_range={"preset": "last_7d"})
    assert out["error"] == "stale_data"
    assert out["policy"] == "fail_closed"
    assert "commerce_orders" in out["stale_views"]
    assert out["stale_views"]["commerce_orders"]["metrics"] == ["orders"]
    assert "rows" not in out  # refused before the planner ran


async def test_metrics_query_passes_when_view_fresh(built_server_fake_freshness, fake_cube):
    from datetime import datetime
    from zoneinfo import ZoneInfo

    mcp, ctx = built_server_fake_freshness
    today_ist = datetime.now(ZoneInfo("Asia/Kolkata")).date().isoformat()
    fake_cube.by_prefix["commerce_orders"] = [
        {"commerce_orders.order_date": f"{today_ist}T00:00:00.000", "commerce_orders.orders": "5"}
    ]
    fn = _tool_fn(mcp, "metrics_query")
    out = await fn(measures=["orders"], time_range={"preset": "last_7d"})
    assert "error" not in out
    assert out["provenance"]["cube_view"] == "commerce_orders"


async def test_metrics_query_not_blocked_when_probe_fails(built_server_fake_freshness, fake_cube):
    """Probe failure must NOT refuse (only confirmed staleness does). The
    planner's own error surfaces instead — and it is not 'stale_data'."""
    mcp, ctx = built_server_fake_freshness
    fake_cube.fail = True
    fn = _tool_fn(mcp, "metrics_query")
    out = await fn(measures=["orders"], time_range={"preset": "last_7d"})
    assert out.get("error") != "stale_data"


async def test_freshness_enforcement_can_be_disabled(built_server_fake_freshness, fake_cube):
    import dataclasses

    mcp, ctx = built_server_fake_freshness
    ctx.settings = dataclasses.replace(ctx.settings, freshness_enforcement=False)
    fake_cube.by_prefix["commerce_orders"] = [
        {"commerce_orders.order_date": "2020-01-01T00:00:00.000", "commerce_orders.orders": "5"}
    ]
    fn = _tool_fn(mcp, "metrics_query")
    out = await fn(measures=["orders"], time_range={"preset": "last_7d"})
    assert "error" not in out  # gate off -> stale data still answers


async def test_metrics_drilldown_refuses_when_parent_view_stale(built_server_fake_freshness, fake_cube):
    from datetime import date as _date

    from seleric_mcp.app.models import QueryRequest, TimeRange

    mcp, ctx = built_server_fake_freshness
    # Seed a parent while data is "fresh" (probe cache starts empty; seed via
    # planner directly so the tool-level gate isn't exercised yet).
    fake_cube.by_prefix["commerce_orders"] = [{"commerce_orders.orders": "1"}]
    parent = await ctx.planner.run(
        QueryRequest(measures=["orders"], time_range=TimeRange(start=_date(2026, 6, 1), end=_date(2026, 6, 30)))
    )
    # Now the probe sees an ancient latest date -> drilldown must refuse.
    fake_cube.by_prefix["commerce_orders"] = [
        {"commerce_orders.order_date": "2020-01-01T00:00:00.000"}
    ]
    fn = _tool_fn(mcp, "metrics_drilldown")
    out = await fn(parent["query_id"], target_dimensions=["payment_method"])
    assert out["error"] == "stale_data"
    assert "commerce_orders" in out["stale_views"]


async def test_catalogue_get_metric_aliases_cube_member(built_server):
    mcp, ctx = built_server
    fn = _tool_fn(mcp, "catalogue_get_metric")
    out = fn("sales_all_channels.total_sales")
    assert "error" not in out
    assert out["id"] == "total_sales_all_channels"
    assert out["query_as"] == {"measures": ["total_sales_all_channels"]}
    assert out.get("resolved_from") == "sales_all_channels.total_sales"


async def test_catalogue_get_metric_includes_openmetadata_block(built_server):
    mcp, ctx = built_server
    fn = _tool_fn(mcp, "catalogue_get_metric")
    out = fn("orders")
    assert "error" not in out
    om = out["openmetadata"]
    assert om["data_product"] == "CommercePerformance"
    assert om["entity_cluster"] == "commerce_order"
    assert "active_orders" in om["related_metrics"]
    assert all(mid in ctx.catalogue.cat.metrics for mid in om["related_metrics"])
    assert "value" not in om and "rows" not in om


async def test_catalogue_related_metrics_returns_cluster_neighbors(built_server):
    mcp, ctx = built_server
    fn = _tool_fn(mcp, "catalogue_related_metrics")
    out = fn("orders")
    assert out["entity_cluster"] == "commerce_order"
    assert "orders" not in out["related_metrics"]
    assert "active_orders" in out["related_metrics"]
    assert all(isinstance(mid, str) and mid in ctx.catalogue.cat.metrics for mid in out["related_metrics"])


async def test_catalogue_get_ontology_scopes_to_module(built_server):
    mcp, ctx = built_server
    fn = _tool_fn(mcp, "catalogue_get_ontology")
    out = fn(module="commerce")
    assert "error" not in out
    assert out["module"] == "commerce"
    assert {d["name"] for d in out["domains"]} == {"Commerce"}
    assert {dp["name"] for dp in out["data_products"]} >= {"CommercePerformance", "AmazonCommercePerformance"}
    cluster_ids = {c["id"] for c in out["entity_clusters"]}
    assert "commerce_order" in cluster_ids
    assert "finance_pnl" not in cluster_ids
    assert out["attribution_boundary"] is None


async def test_catalogue_get_ontology_paidmedia_includes_attribution_boundary(built_server):
    mcp, ctx = built_server
    fn = _tool_fn(mcp, "catalogue_get_ontology")
    out = fn(module="paidmedia")
    assert out["module"] == "paidmedia"
    assert out["attribution_boundary"]
    assert "platform_reported_roas" in out["attribution_boundary"]["excluded_from_certified"]


async def test_catalogue_resolve_term_aliases_cube_member(built_server):
    mcp, ctx = built_server
    fn = _tool_fn(mcp, "catalogue_resolve_term")
    out = fn("orders_all_channels.orders")
    assert out["kind"] == "resolved"
    assert out["metric_id"] == "total_orders"


async def test_catalogue_resolve_dimension_channel_is_ambiguous(built_server):
    mcp, ctx = built_server
    fn = _tool_fn(mcp, "catalogue_resolve_dimension")
    out = fn("channel")
    assert out["kind"] == "ambiguous"
    ids = {c["dimension_id"] for c in out["candidates"]}
    assert "channel" in ids
    assert "lt_channel" in ids
    assert "commerce_net_revenue_daily" not in str(out)


async def test_catalogue_list_dimensions_query_without_view(built_server):
    mcp, ctx = built_server
    fn = _tool_fn(mcp, "catalogue_list_dimensions")
    out = fn(query="channel")
    assert "error" not in out
    ids = {d["id"] for d in out["dimensions"]}
    assert "channel" in ids
    assert "lt_channel" in ids
    empty = fn()
    assert "error" in empty


async def test_insights_explain_rejects_composed_parent(built_server, fake_cube):
    from datetime import date as _date

    from seleric_mcp.app.models import QueryRequest, TimeRange

    mcp, ctx = built_server
    fake_cube.by_prefix["sales_all_channels"] = [
        {"sales_all_channels.total_sales": "100"}
    ]
    fake_cube.by_prefix["orders_all_channels"] = [
        {"orders_all_channels.orders": "2"}
    ]
    parent = await ctx.planner.run(
        QueryRequest(
            measures=["total_sales_all_channels", "total_orders"],
            time_range=TimeRange(start=_date(2026, 6, 1), end=_date(2026, 6, 30)),
        )
    )
    assert parent.get("composed") is True
    fn = _tool_fn(mcp, "insights_explain")
    out = fn(parent["query_id"])
    assert "error" in out
    assert "multi-view composition" in out["error"]
    assert out["part_query_ids"]


async def test_scopes_apply_to_cube_member_measure_ref(built_server_no_scopes):
    mcp, ctx = built_server_no_scopes
    fn = _tool_fn(mcp, "metrics_query")
    out = await fn(
        measures=["commerce_orders.dashboard_net_sales_excl_tax"],
        time_range={"preset": "last_7d"},
    )
    # Must resolve Cube member → commerce_net_revenue and still deny scopes.
    assert "error" in out
    assert "missing_scopes_by_metric" in out
    assert "commerce_net_revenue" in out["missing_scopes_by_metric"]


# ---------- module scoping (dashboard access boundary) ----------
# catalogue/modules.yaml + gateway helpers scope a call to one dashboard
# module. Effective module = config pin (gateway.module) if set, else the
# per-call `module` arg, else none (unscoped = unchanged). In-module metrics
# pass; out-of-module metrics are hard-refused before Cube is touched.

def _pinned_settings(tmp_path, module):
    from seleric_mcp.config import Settings

    return Settings(
        cube_api_url="http://cube.test",
        seleric_api_key="test-key",
        cubejs_api_secret="",
        pipeboard_mcp_url="http://pipeboard.test",
        pipeboard_token="pb-token",
        write_enabled=False,
        mcp_service_token="svc-token",
        approval_secret="approval-secret",
        caller_scopes=frozenset({"metrics:read"}),
        db_path=tmp_path / "test_pinned.db",
        module=module,
    )


@pytest.fixture()
def built_server_pinned(fake_cube, result_store, tmp_path):
    """Instance hard-pinned to the webanalytics (funnel) module."""
    mcp = build_server(_pinned_settings(tmp_path, "webanalytics"))
    ctx = mcp._seleric_ctx
    ctx.result_store = result_store
    ctx.planner = QueryPlanner(ctx.catalogue, fake_cube, ctx.result_store)
    return mcp, ctx


def test_modules_list_tool_returns_all_modules(built_server):
    mcp, ctx = built_server
    out = _tool_fn(mcp, "modules_list")()
    ids = {m["id"] for m in out["modules"]}
    assert ids == {
        "webanalytics", "commerce", "product", "paidmedia",
        "attribution", "customer", "finance", "operations",
    }
    assert out["active_module"] is None  # unscoped instance


async def test_metrics_query_module_allows_in_module(built_server, fake_cube):
    mcp, ctx = built_server
    fake_cube.by_prefix["commerce_orders"] = [
        {"commerce_orders.dashboard_net_sales_excl_gst": "100"}
    ]
    fn = _tool_fn(mcp, "metrics_query")
    out = await fn(
        measures=["commerce_net_revenue"],
        time_range={"preset": "last_7d"},
        module="commerce",
    )
    assert "out_of_module_metrics" not in out
    assert out["provenance"]["cube_view"] == "commerce_orders"


async def test_metrics_query_module_refuses_out_of_module(built_server, fake_cube):
    mcp, ctx = built_server
    fn = _tool_fn(mcp, "metrics_query")
    out = await fn(
        measures=["attributed_net_revenue"],
        time_range={"preset": "last_7d"},
        module="commerce",
    )
    assert out["module"] == "commerce"
    assert out["out_of_module_metrics"] == ["attributed_net_revenue"]
    assert "rows" not in out  # refused before Cube was queried
    assert fake_cube.queries == []


async def test_metrics_query_unknown_module_errors(built_server):
    mcp, ctx = built_server
    fn = _tool_fn(mcp, "metrics_query")
    out = await fn(
        measures=["commerce_net_revenue"],
        time_range={"preset": "last_7d"},
        module="not_a_module",
    )
    assert "error" in out
    assert "commerce" in out["valid_modules"]


async def test_metrics_query_no_module_is_full_access(built_server, fake_cube):
    """Backward compatibility: with no module in effect, any metric is reachable
    regardless of which domain it lives in."""
    mcp, ctx = built_server
    fake_cube.by_prefix["order_attribution"] = [
        {"order_attribution.attributed_net_revenue": "500"}
    ]
    fn = _tool_fn(mcp, "metrics_query")
    out = await fn(measures=["attributed_net_revenue"], time_range={"preset": "last_7d"})
    assert "out_of_module_metrics" not in out
    assert out["provenance"]["cube_view"] == "order_attribution"


async def test_pinned_instance_forces_its_module(built_server_pinned, fake_cube):
    """A per-call module that contradicts the pin is refused; omitting module
    still applies the pin and refuses out-of-module metrics."""
    mcp, ctx = built_server_pinned
    fn = _tool_fn(mcp, "metrics_query")

    # Contradicting the pin -> refused with the pin named.
    out = await fn(
        measures=["commerce_net_revenue"],
        time_range={"preset": "last_7d"},
        module="commerce",
    )
    assert out["pinned_module"] == "webanalytics"

    # No module arg -> pin applies; a commerce metric is out of the funnel module.
    out2 = await fn(measures=["commerce_net_revenue"], time_range={"preset": "last_7d"})
    assert out2["module"] == "webanalytics"
    assert "commerce_net_revenue" in out2["out_of_module_metrics"]
    assert fake_cube.queries == []

    # modules_list reports the active pin.
    assert _tool_fn(mcp, "modules_list")()["active_module"] == "webanalytics"


async def test_pinned_instance_allows_in_module_metric(built_server_pinned, fake_cube):
    mcp, ctx = built_server_pinned
    fn = _tool_fn(mcp, "metrics_query")
    # add_to_cart_events is a WebAnalytics metric -> passes the module guard.
    out = await fn(measures=["add_to_cart_events"], time_range={"preset": "last_7d"})
    assert "out_of_module_metrics" not in out
    assert "pinned_module" not in out
