import pytest

from seleric_mcp.app import insight_engine as ie
from seleric_mcp.catalogue_service.loader import load_catalogue
from seleric_mcp.catalogue_service.service import CatalogueService
from seleric_mcp.config import PROJECT_ROOT


def _metric(catalogue, mid):
    return catalogue.get_metric(mid)


@pytest.fixture(scope="module")
def v2():
    return CatalogueService(load_catalogue(PROJECT_ROOT / "catalogue"))


def test_additive_totals_and_delta(catalogue):
    m = _metric(catalogue, "commerce_net_revenue")
    cur = [{"commerce_orders.dashboard_net_sales_excl_tax": "100"},
           {"commerce_orders.dashboard_net_sales_excl_tax": "150"}]
    prev = [{"commerce_orders.dashboard_net_sales_excl_tax": "200"}]
    totals = ie.compute_totals(cur, prev, [m])
    t = totals[0]
    assert t["current"]["value"] == 250
    assert t["compare"]["value"] == 200
    assert t["delta"] == 50
    assert t["pct_change"] == 25.0
    assert t["current"]["method"] == "sum"


def test_ratio_recomputed_from_components(catalogue):
    m = _metric(catalogue, "units_per_order")
    rows = [
        {"product_performance.units_per_order": "10", "product_performance.units_sold": "100",
         "product_performance.product_orders": "20"},
        {"product_performance.units_per_order": "2", "product_performance.units_sold": "100",
         "product_performance.product_orders": "80"},
    ]
    total = ie.metric_total(rows, m)
    # 200/100 = 2.0, NOT sum(10+2)=12 nor mean=6
    assert total["value"] == 2.0
    assert total["recomputed"] is True


def test_ratio_without_components_flagged_approximation(catalogue):
    m = _metric(catalogue, "aov")  # no ratio_components catalogued
    rows = [{"commerce_orders.aov": "3"}, {"commerce_orders.aov": "5"}]
    total = ie.metric_total(rows, m)
    assert total["value"] == 4.0
    assert total["recomputed"] is False
    assert total["method"] == "mean_of_rows_approximation"


def test_top_movers_with_new_and_disappeared(catalogue):
    m = _metric(catalogue, "product_net_revenue")
    cube_query = {"dimensions": ["product_performance.product_title"]}
    cur = [
        {"product_performance.product_title": "A", "product_performance.net_line_revenue_ex_gst": "300"},
        {"product_performance.product_title": "B", "product_performance.net_line_revenue_ex_gst": "100"},
    ]
    prev = [
        {"product_performance.product_title": "A", "product_performance.net_line_revenue_ex_gst": "100"},
        {"product_performance.product_title": "C", "product_performance.net_line_revenue_ex_gst": "50"},
    ]
    movers = ie.compute_top_movers(cur, prev, [m], cube_query)
    by_key = {mv["key"]["product_performance.product_title"]: mv for mv in movers}
    assert by_key["A"]["delta"] == 200 and by_key["A"]["status"] == "existing"
    assert by_key["B"]["status"] == "new"
    assert by_key["C"]["status"] == "disappeared" and by_key["C"]["delta"] == -50
    # delta_total = 400-150 = 250; A contributes 200/250 = 80%
    assert by_key["A"]["contribution_pct"] == 80.0
    # ranked by |delta|
    assert movers[0]["key"]["product_performance.product_title"] == "A"


def test_anomaly_sigma_outlier(catalogue):
    m = _metric(catalogue, "commerce_net_revenue")
    cube_query = {
        "timeDimensions": [{"dimension": "commerce_orders.order_date", "granularity": "day"}]
    }
    rows = [
        {"commerce_orders.order_date": f"2026-06-{d:02d}",
         "commerce_orders.dashboard_net_sales_excl_tax": "100"}
        for d in range(1, 21)
    ]
    rows[10]["commerce_orders.dashboard_net_sales_excl_tax"] = "1000"  # spike
    anomalies = ie.compute_anomalies(rows, [m], cube_query)
    assert any(a["type"] == "sigma_outlier" and a["date"] == "2026-06-11" for a in anomalies)


def test_anomaly_flat_zero_run(catalogue):
    m = _metric(catalogue, "commerce_net_revenue")
    cube_query = {
        "timeDimensions": [{"dimension": "commerce_orders.order_date", "granularity": "day"}]
    }
    rows = [
        {"commerce_orders.order_date": f"2026-06-{d:02d}",
         "commerce_orders.dashboard_net_sales_excl_tax": "0" if 5 <= d <= 8 else "100"}
        for d in range(1, 21)
    ]
    anomalies = ie.compute_anomalies(rows, [m], cube_query)
    assert any(a["type"] == "flat_zero_run" and a["days"] == 4 for a in anomalies)


def test_no_compare_note(catalogue):
    m = _metric(catalogue, "commerce_net_revenue")
    report = ie.explain([{"commerce_orders.dashboard_net_sales_excl_tax": "5"}], None, [m], {})
    assert report["top_movers"] == []
    assert "note" in report


# ---------------------------------------------------------------- SEM-14 v2 ratio components

def test_v2_ratio_recomputes_from_resolved_members(v2):
    """Catalogued components are metric ids, but stored rows carry the qualified
    Cube members; the recompute must resolve them."""
    m = v2.get_metric("ctr")
    rows = [{"paid_media.clicks": "5", "paid_media.impressions": "100"},
            {"paid_media.clicks": "5", "paid_media.impressions": "100"}]
    total = ie.metric_total(rows, m)
    assert total["value"] == 0.05
    assert total["recomputed"] is True
    assert total["method"] == "sum(paid_media.clicks)/sum(paid_media.impressions)"


def test_v2_cpm_recompute_applies_the_multiplier(v2):
    """CPM = spend ÷ impressions × 1000; the period recompute must scale too."""
    m = v2.get_metric("cpm")
    rows = [{"paid_media.ad_spend": "500", "paid_media.impressions": "100000"}]
    total = ie.metric_total(rows, m)
    assert total["value"] == 5.0
    assert total["method"] == "sum(paid_media.ad_spend)/sum(paid_media.impressions)*1000"


def test_ratio_zero_denominator_falls_back_to_cube_measure(v2):
    """A zero denominator must not divide by zero: fall back to the authoritative
    Cube measure rather than emitting inf/nan."""
    m = v2.get_metric("ctr")
    rows = [{"paid_media.ctr": "0", "paid_media.clicks": "0", "paid_media.impressions": "0"}]
    total = ie.metric_total(rows, m)
    assert total["recomputed"] is False
    assert total["value"] == 0.0
