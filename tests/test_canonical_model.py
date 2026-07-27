"""Acceptance tests for the 2026-07-11 canonical-model additions
(CANONICAL_DATA_MODEL.md): order_records / order_item_records record-grain
views, the campaign->order->city join path (Scenario B in
mcp_query_capability_catalogue.md), and the new P0 metrics batch. Mirrors the
conventions in test_query_planner.py — see that file for the base cases
(date math, single-view enforcement, breakdown guard) this file does not repeat.
"""

from __future__ import annotations

from datetime import date

import pytest

from seleric_mcp.app.models import FilterSpec, PlanError, QueryRequest, TimeRange
from seleric_mcp.app.query_planner import QueryPlanner


@pytest.fixture()
def planner(catalogue, fake_cube, result_store):
    return QueryPlanner(catalogue, fake_cube, result_store)


# ---------- Scenario B: campaign -> order -> city ----------


# ---------- new P0 commerce metrics (audit §4/§5.2: previously uncatalogued) ----------

async def test_order_status_breakdown_metrics_share_commerce_orders_view(planner, fake_cube):
    """cancelled_orders/refunded_orders/prepaid_orders/cod_orders were measures
    that existed on commerce_orders but had no catalogue entry — the exact
    failure class observed live before this refactor. Confirm they resolve and
    can be queried alongside 'orders'. Since the per-metric time axis fix
    (2026-07-14), placement-axis metrics (order_date) and event-axis metrics
    (event_date: cancelled/refunded) run as SEPARATE composed parts — one date
    filter must never span both axes.
    NOTE: active_orders is intentionally EXCLUDED here. The 2026-07-21 dashboard
    alignment pass marked it status=draft / dashboard_alignment.not_implemented
    (no standalone active-order count exists anywhere in Node-Backend), so it is
    not an approved catalogue metric the planner will resolve. Keeping it in this
    test would assert a definition the dashboard does not carry."""
    fake_cube.by_prefix["commerce_orders"] = [{"commerce_orders.orders": "10"}]
    req = QueryRequest(
        measures=["orders", "cancelled_orders", "refunded_orders",
                  "prepaid_orders", "cod_orders"],
        time_range=TimeRange(preset="last_7d"),
    )
    out = await planner.run(req)
    assert out["composed"] is True
    assert out["composition"] == "multi_time_axis"
    by_axis = {
        q["timeDimensions"][0]["dimension"]: set(q["measures"]) for q in fake_cube.queries
    }
    assert by_axis["commerce_orders.order_date"] == {
        "commerce_orders.orders",
        "commerce_orders.prepaid_orders",
        "commerce_orders.cod_orders",
    }
    assert by_axis["commerce_orders.event_date"] == {
        "commerce_orders.cancelled_orders",
        "commerce_orders.returned_orders",
    }
    assert all(p["provenance"]["cube_view"] == "commerce_orders" for p in out["parts"])


# ---------- primary-key hygiene (audit §2: 8 cubes previously had none) ----------

def test_serve_cubes_declare_primary_keys():
    """Every serve cube must declare its grain via primary_key dimensions
    (post-slim model: the 4 serve_* cubes are the whole physical surface)."""
    import yaml

    from seleric_mcp.config import cube_model_dir

    expected_keys = {
        "serve_commerce_orders.yml": {"brand_id", "order_id"},
        "serve_commerce_order_events.yml": {"brand_id", "order_id", "event_type"},
        "serve_commerce_performance_daily.yml": {"brand_id"},
        "serve_product_performance.yml": {"brand_id", "order_id", "line_item_id"},
    }
    cubes_dir = cube_model_dir() / "cubes"
    for filename, keys in expected_keys.items():
        doc = yaml.safe_load((cubes_dir / filename).read_text(encoding="utf-8"))
        dims = doc["cubes"][0]["dimensions"]
        pk = {d["name"] for d in dims if d.get("primary_key") is True}
        assert keys <= pk, f"{filename}: primary keys {pk} missing {keys - pk}"


def test_all_serve_cubes_are_non_public():
    """Every raw cube must be public:false; only views are queryable."""
    import yaml

    from seleric_mcp.config import cube_model_dir

    cubes_dir = cube_model_dir() / "cubes"
    cube_files = sorted(cubes_dir.glob("serve_*.yml"))
    assert len(cube_files) >= 4
    not_private = []
    for f in cube_files:
        doc = yaml.safe_load(f.read_text(encoding="utf-8"))
        for cube in doc.get("cubes", []):
            if cube.get("public") is not False:
                not_private.append(cube.get("name"))
    assert not_private == [], f"cubes missing public:false: {not_private}"


# ---------- filter-value resolution (the "Meta" vs "meta" live failure) ----------
# A live query for lt_platform="Meta" (capitalized) silently returned zero rows
# instead of erroring, because ClickHouse string equality is case-sensitive and
# the real stored value is lowercase "meta" — evidenced across gold_channel_pnl,
# gold_meta_campaign_attribution, gold_neurohack_attribution's SQL, all of which
# filter/emit lt_platform/platform as 'meta'/'google'/'organic'. Fixed by
# DimensionDef.allowed_values + QueryPlanner._resolve_filter_values: exact match
# passes, case-insensitive match auto-corrects with a recorded warning, anything
# else is a hard PlanError with the real values as suggestions — never another
# silent empty result.

async def test_case_mismatched_filter_value_is_corrected_not_silently_empty(planner, fake_cube):
    fake_cube.by_prefix["commerce_orders"] = [{"commerce_orders.orders": "500"}]
    req = QueryRequest(
        measures=["orders"],
        filters=[FilterSpec(dimension="payment_bucket", values=["COD"])],  # wrong case, as sent live
        time_range=TimeRange(preset="last_7d"),
    )
    out = await planner.run(req)
    q = fake_cube.queries[0]
    # corrected to the real stored value before it ever reaches Cube
    assert {
        "member": "commerce_orders.payment_bucket",
        "operator": "equals",
        "values": ["cod"],
    } in q["filters"]
    assert any("case-corrected" in w for w in out["warnings"])
    assert any("case-corrected" in w for w in out["provenance"]["warnings"])


async def test_unknown_filter_value_rejected_with_real_suggestions(planner):
    req = QueryRequest(
        measures=["orders"],
        filters=[FilterSpec(dimension="payment_bucket", values=["bank transfer"])],  # not a bucket
        time_range=TimeRange(preset="last_7d"),
    )
    with pytest.raises(PlanError) as exc:
        await planner.run(req)
    assert "not a known value" in str(exc.value)
    assert set(exc.value.suggestions) == {"online", "cod", "paytm card machine", "manual"}


async def test_filter_value_without_allowed_values_passes_through_unchanged(planner, fake_cube):
    """Dimensions with no declared allowed_values (the overwhelming majority —
    SKUs, cities, product titles, etc.) must never be validated against a
    fabricated enum; any string is legitimate data."""
    fake_cube.by_prefix["product_performance"] = [
        {"product_performance.net_line_revenue_ex_gst": "1"}
    ]
    req = QueryRequest(
        measures=["product_net_revenue"],
        filters=[FilterSpec(dimension="sku", values=["TH-149-SCRATCHLOUNGE"])],
        time_range=TimeRange(preset="last_7d"),
    )
    out = await planner.run(req)
    q = fake_cube.queries[0]
    sku_filters = [f for f in q["filters"] if f["member"] == "product_performance.sku"]
    assert sku_filters[0]["values"] == ["TH-149-SCRATCHLOUNGE"]
    assert out["warnings"] == []


# ---------- sort / top-N ----------

async def test_sort_by_measure_overrides_default_date_order(planner, fake_cube):
    fake_cube.by_prefix["product_performance"] = [
        {"product_performance.product_title": "A",
         "product_performance.net_line_revenue_ex_gst": "900"}
    ]
    req = QueryRequest(
        measures=["product_net_revenue"],
        dimensions=["product_title"],
        time_range=TimeRange(preset="last_7d"),
        sort=[{"field": "product_net_revenue", "direction": "desc"}],
        limit=5,
    )
    await planner.run(req)
    q = fake_cube.queries[0]
    assert q["order"] == {"product_performance.net_line_revenue_ex_gst": "desc"}
    assert q["limit"] == 5


async def test_sort_by_dimension(planner, fake_cube):
    fake_cube.by_prefix["commerce_orders"] = [{"commerce_orders.orders": "1"}]
    req = QueryRequest(
        measures=["orders"],
        dimensions=["payment_method"],
        time_range=TimeRange(preset="last_7d"),
        sort=[{"field": "payment_method", "direction": "asc"}],
    )
    await planner.run(req)
    q = fake_cube.queries[0]
    assert q["order"] == {"commerce_orders.payment_method": "asc"}


async def test_sort_by_field_not_in_query_rejected(planner):
    """Sort target must be a requested measure or an already-valid dimension —
    never an invented field (requirement 9: no invented SQL/joins/fields)."""
    req = QueryRequest(
        measures=["orders"],
        time_range=TimeRange(preset="last_7d"),
        sort=[{"field": "net_revenue", "direction": "desc"}],  # different view entirely
    )
    with pytest.raises(PlanError, match="Cannot sort by"):
        await planner.run(req)


async def test_drilldown_inherits_sort(planner, fake_cube):
    fake_cube.by_prefix["commerce_orders"] = [{"commerce_orders.orders": "50"}]
    parent = await planner.run(
        QueryRequest(
            measures=["orders"],
            time_range=TimeRange(start=date(2026, 6, 1), end=date(2026, 6, 30)),
            sort=[{"field": "orders", "direction": "desc"}],
        )
    )
    await planner.drilldown(parent["query_id"], target_dimensions=["payment_method"], additional_filters=[])
    q = fake_cube.queries[-1]
    assert q["order"] == {"commerce_orders.orders": "desc"}


# ---------- remaining P0 batch: amazon ads, refunds, payment-method P&L ----------


# ---------- customer purchase sequence (retention/repeat-purchase, Q226-228/235/241) ----------
# Buildable entirely from gold_fct_orders (customer_id, order_date already
# exist) — no new source data, unlike inventory/fulfilment/etc. First cube in
# this model using ClickHouse window functions; not verified against a live
# instance (no live ClickHouse access) — see the cube's own header comment.


# ---------- currency in provenance (requirement 10 named it explicitly; declared
# on every metric's currency_default, never surfaced in build_provenance until now) ----------

async def test_currency_metric_reports_its_currency_in_provenance(planner, fake_cube):
    fake_cube.by_prefix["product_performance"] = [
        {"product_performance.net_line_revenue_ex_gst": "100"}
    ]
    out = await planner.run(
        QueryRequest(measures=["product_net_revenue"], time_range=TimeRange(preset="last_7d"))
    )
    assert out["provenance"]["currency"] == "INR"


async def test_non_currency_metric_reports_no_currency(planner, fake_cube):
    fake_cube.by_prefix["product_performance"] = [{"product_performance.units_sold": "1"}]
    out = await planner.run(
        QueryRequest(measures=["units_sold"], time_range=TimeRange(preset="last_7d"))
    )
    # units_sold is unit: count, no currency_default
    assert out["provenance"]["currency"] is None


# ---------- catalogue <-> Cube view reconciliation (requirement 8/12: prevent
# double counting / validate against deterministic SQL) ----------
# _check_integrity (loader.py) only verifies a dimension has *some* entry for
# a view's name in DimensionDef.views — it never checks that the qualified
# member (e.g. "refund_events.order_id") is actually in that view's includes:
# list in cube/model/views/*.yml. That gap let two catalogue dimension
# mappings point at members refund_events never exposed (order_id,
# return_status) — invisible to every offline test, would only have surfaced
# as a live Cube 400 error. This test makes that class of bug fail offline,
# for every view/dimension/metric in the catalogue, not just the two found.

def _view_members() -> dict[str, set[str]]:
    import yaml

    from seleric_mcp.config import cube_model_dir

    members: dict[str, set[str]] = {}
    for f in (cube_model_dir() / "views").glob("*.yml"):
        doc = yaml.safe_load(f.read_text(encoding="utf-8")) or {}
        for v in doc.get("views", []):
            name = v.get("name")
            if not name:
                continue
            s: set[str] = set()
            for c in v.get("cubes", []):
                for inc in c.get("includes", []):
                    s.add(inc.get("alias") or inc.get("name") if isinstance(inc, dict) else inc)
            members[name] = s
    return members


def test_every_catalogue_dimension_mapping_exists_in_its_view(catalogue):
    view_members = _view_members()
    problems = []
    for d in catalogue.cat.dimensions.values():
        for view, qualified in d.views.items():
            member = qualified.split(".", 1)[1] if "." in qualified else qualified
            if view not in view_members or member not in view_members[view]:
                problems.append((d.id, view, qualified))
    assert problems == [], f"catalogue dimensions pointing at members their view doesn't expose: {problems}"


def test_every_catalogue_metric_mapping_exists_in_its_view(catalogue):
    view_members = _view_members()
    problems = []
    for m in catalogue.cat.metrics.values():
        needed = [m.cube_mapping.measure]
        if m.cube_mapping.measure_pct:
            needed.append(m.cube_mapping.measure_pct)
        if m.ratio_components:
            needed += [m.ratio_components.numerator, m.ratio_components.denominator]
        for qualified in needed:
            member = qualified.split(".", 1)[1] if "." in qualified else qualified
            view = m.cube_mapping.view
            if view not in view_members or member not in view_members[view]:
                problems.append((m.id, view, qualified))
    assert problems == [], f"catalogue metrics pointing at members their view doesn't expose: {problems}"


def test_every_cube_has_at_least_one_primary_key():
    """Sweep across all 39 cubes, not just the 8 originally found missing one
    (CUBE_SEMANTIC_AUDIT.md §2) — found a 9th, gold_refund_events, this way."""
    import yaml

    from seleric_mcp.config import cube_model_dir

    cubes_dir = cube_model_dir() / "cubes"
    # The physical cubes are named serve_*.yml in the slim model (the earlier
    # gold_*.yml naming is gone). Globbing gold_* made this sweep silently
    # vacuous — the exact "renamed upstream, test still points at the old name"
    # drift this suite is meant to catch. Sweep serve_*.yml and assert non-empty.
    cube_files = sorted(cubes_dir.glob("serve_*.yml"))
    assert cube_files, "no serve_*.yml cubes found — cube_model_dir wiring is broken"
    missing = []
    for f in cube_files:
        doc = yaml.safe_load(f.read_text(encoding="utf-8"))
        for cube in doc.get("cubes", []):
            dims = cube.get("dimensions", [])
            if not any(d.get("primary_key") is True for d in dims):
                missing.append(cube.get("name"))
    assert missing == [], f"cubes with no primary_key dimension: {missing}"


# ---------- catalogue <-> OpenMetadata crosswalk drift (goal: Base Agent and
# OpenMetadata must carry the SAME metric definitions) ----------
# openmetadata/metrics.yaml is auto-generated by
# scripts/sync_openmetadata_catalogue.py, but nothing tests it stays in sync.
# If a catalogue metric is added, renamed, re-categorised, or re-pointed at a
# different cube view and the sync isn't re-run, OpenMetadata silently ships a
# stale definition while the agent uses the new one — value/definition drift
# between two of the six systems this reconciliation covers. These tests make
# that fail offline in CI, forcing a regenerate.

def _om_metrics_crosswalk() -> dict:
    import yaml

    from tests.conftest import CATALOGUE_DIR

    doc = yaml.safe_load((CATALOGUE_DIR / "openmetadata" / "metrics.yaml").read_text(encoding="utf-8"))
    return doc.get("metrics", {}) or {}


def test_every_catalogue_metric_is_in_openmetadata_crosswalk(catalogue):
    """Every catalogue metric (the Base Agent's metric surface) must have an
    OpenMetadata crosswalk entry — otherwise OM lineage/ownership is missing for
    a metric the agent can answer, and the two systems disagree on what exists."""
    om = _om_metrics_crosswalk()
    missing = sorted(mid for mid in catalogue.cat.metrics if mid not in om)
    assert missing == [], (
        "catalogue metrics with no OpenMetadata crosswalk entry "
        f"(re-run scripts/sync_openmetadata_catalogue.py): {missing}"
    )


def test_order_attribution_cube_is_single_model_no_fanout():
    """serve.order_attribution stores 4 attribution models per order (each with
    the full order's credited net revenue). Summing across all of them inflated
    every money measure ~4x (2026-07-27: attributed_net_revenue 4,572,946 vs the
    true last-touch 1,143,237). The cube is declared last-touch, so its source
    MUST be scoped to a single model. Guard against a revert to the unscoped
    `sql_table: serve.order_attribution` that reintroduces the fan-out."""
    import yaml

    from seleric_mcp.config import cube_model_dir

    f = cube_model_dir() / "cubes" / "serve_order_attribution.yml"
    doc = yaml.safe_load(f.read_text(encoding="utf-8"))
    cube = doc["cubes"][0]
    sql = (cube.get("sql") or "").lower()
    assert cube.get("sql_table") is None, (
        "serve_order_attribution must NOT use raw sql_table (all 4 attribution "
        "models) — that reintroduces the ~4x attributed-revenue fan-out"
    )
    assert "attribution_model" in sql and "last_touch" in sql, (
        "serve_order_attribution.sql must scope to a single attribution model "
        f"(e.g. last_touch_v1); got: {sql!r}"
    )


def test_sales_all_channels_amazon_is_exgst_and_excludes_cancels():
    """serve_sales_all_channels Amazon leg was incl-GST + included cancels and
    mislabeled TOTAL (order_total_header) as GROSS. Fixed 2026-07-27 to match the
    Amazon Attribution dashboard: gross = effective_gross_revenue - revenue_tax
    (ex-GST), cancels excluded. Guard against a revert to the incl-GST gross or a
    missing cancel filter."""
    import yaml

    from seleric_mcp.config import cube_model_dir

    f = cube_model_dir() / "cubes" / "serve_sales_all_channels.yml"
    sql = yaml.safe_load(f.read_text(encoding="utf-8"))["cubes"][0]["sql"].lower()
    assert "revenue_tax" in sql, (
        "amazon_gross_sales must be ex-GST (effective_gross_revenue - revenue_tax)"
    )
    assert "order_status" in sql and "cancel" in sql, (
        "amazon sales must exclude cancelled orders (order_status filter)"
    )
    assert "operational_revenue_incl_gst" not in sql, (
        "amazon_net_sales must not use the incl-GST operational column"
    )


def test_amazon_attribution_overview_uses_delivery_date_returns_and_net_profit():
    """Dashboard Amazon Attribution cards use return_delivery_date Returns Report
    and Net Profit = Net Sales − Fees − Product − Ads — never marketplace_net_payout."""
    import yaml

    from seleric_mcp.config import cube_model_dir

    f = cube_model_dir() / "cubes" / "serve_amazon_attribution_overview.yml"
    raw = yaml.safe_load(f.read_text(encoding="utf-8"))["cubes"][0]
    sql = raw["sql"].lower()
    measures = {m["name"] for m in raw["measures"]}
    assert "return_delivery_date" in sql
    assert "fct_amazon_return_items" in sql
    assert "cogs_product" in sql
    assert "spend" in sql
    assert {"net_profit", "return_revenue", "returns_cancels", "net_sales"} <= measures


def test_amazon_net_sales_catalogue_matches_exgst_report_return_basis(catalogue):
    """Canonical Amazon net-sales basis (2026-07-27, verified live vs the Amazon
    Attribution dashboard = 214,753.49): ex-GST gross (effective_gross_revenue -
    revenue_tax, non-canceled) MINUS report returns on return_delivery_date.
    This SUPERSEDES the earlier 'settlement basis / revenue_principal' narrative,
    which came from a stale June CSV (retracted). Guard both Amazon net-sales
    metric descriptions so they stay on the ex-GST report-return basis and never
    revert to an incl-GST column."""
    for mid in ("amazon_net_sales", "net_sales_all_channels"):
        m = catalogue.cat.metrics[mid]
        formula = m.formula.human_readable.lower()
        assert "ex-gst" in formula and "return" in formula, (
            f"{mid} formula must be the ex-GST report-return basis; got: {formula}"
        )
        assert "revenue_principal" not in formula and "incl_gst" not in formula, (
            f"{mid} formula must not use the superseded settlement/incl-GST basis; got: {formula}"
        )


def test_amazon_catalogue_points_at_attribution_overview(catalogue):
    """Amazon Attribution card metrics must bind to amazon_attribution_overview,
    not the refund-posted amazon_commerce_performance returned_* axis / net_payout."""
    expected = {
        "amazon_gross_sales": "amazon_attribution_overview.gross_sales",
        "amazon_net_sales": "amazon_attribution_overview.net_sales",
        "amazon_total_sales": "amazon_attribution_overview.total_sales",
        "amazon_return_revenue": "amazon_attribution_overview.return_revenue",
        "amazon_returned_orders": "amazon_attribution_overview.returned_orders",
        "amazon_returns_cancels": "amazon_attribution_overview.returns_cancels",
        "amazon_return_count": "amazon_attribution_overview.returns_cancels",
        "amazon_net_profit": "amazon_attribution_overview.net_profit",
    }
    for mid, measure in expected.items():
        m = catalogue.cat.metrics[mid]
        assert m.cube_mapping.view == "amazon_attribution_overview", mid
        assert m.cube_mapping.measure == measure, mid
    # net payout stays on commerce daily and must not be aliased as net profit
    assert catalogue.cat.metrics["amazon_net_payout"].cube_mapping.view == (
        "amazon_commerce_performance"
    )


def test_dbt_rollup_amazon_gross_uses_settlement_source():
    """Legacy guard on the dbt intermediate int_finance_daily_rollups (NOT the
    cube's source — the cube reads serve.canonical_pnl / serve.sales_all_channels
    views over gold; see METRIC_RECONCILIATION_JUNE_2026 §1a). Kept only to catch
    an accidental basis change in that intermediate. The Cube pipeline's
    source (effective_gross_revenue from fct_amazon_sp_order_pnl), matching the
    canonical basis. Skips when the mage-ai pipeline repo isn't checked out
    alongside Base_Agent (Base_Agent tests must not hard-fail without it)."""
    from seleric_mcp.config import cube_model_dir

    # cube_model_dir(): data_platform/mage-ai/infra/cube/model
    mage_root = cube_model_dir().parents[2]  # -> data_platform/mage-ai
    sql = mage_root / "dbt" / "models" / "iceberg" / "cross_platform" / "int_finance_daily_rollups.sql"
    if not sql.exists():
        import pytest

        pytest.skip(f"dbt rollup not present at {sql} (pipeline repo not checked out)")
    text = sql.read_text(encoding="utf-8")
    assert "fct_amazon_sp_order_pnl" in text
    assert "effective_gross_revenue" in text, (
        "amazon_gross_revenue must derive from the settlement column "
        "effective_gross_revenue, not catalog gross"
    )


def test_openmetadata_crosswalk_category_and_view_match_catalogue(catalogue):
    """For every metric present in both, category and cube view must agree.
    A divergence means OM's documented lineage points at a different semantic
    layer object than the one the agent actually queries."""
    om = _om_metrics_crosswalk()
    mismatches = []
    for mid, m in catalogue.cat.metrics.items():
        entry = om.get(mid)
        if entry is None:
            continue
        if entry.get("category") != m.category:
            mismatches.append((mid, "category", m.category, entry.get("category")))
        if entry.get("cube_view") != m.cube_mapping.view:
            mismatches.append((mid, "cube_view", m.cube_mapping.view, entry.get("cube_view")))
    assert mismatches == [], (
        "OpenMetadata crosswalk disagrees with catalogue "
        f"(field, catalogue, om): {mismatches}"
    )
