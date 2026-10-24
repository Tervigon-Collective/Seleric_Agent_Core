"""build_dataset_query: alignment of per-view parts into one table, zero vs missing, partial hour, persistence,
scoping and caps. Runs on the committed catalogue_v3/ and a Cube double — no live Cube, no ClickHouse."""
from __future__ import annotations

import pytest

from seleric_mcp.app import dataset_builder as db_mod
from seleric_mcp.app.dataset_builder import DatasetBuilder, DatasetSpec, get_page, slice_rows
from seleric_mcp.app.dataset_store import DatasetError, DatasetStore
from seleric_mcp.app.models import FilterSpec, TimeRange
from seleric_mcp.app.query_planner import QueryPlanner
from seleric_mcp.catalogue_service.loader import load_catalogue
from seleric_mcp.catalogue_service.service import CatalogueService
from seleric_mcp.config import PROJECT_ROOT
from conftest import FakeCube

DAY = TimeRange(start="2026-10-22", end="2026-10-22")


@pytest.fixture(scope="module")
def v3() -> CatalogueService:
    return CatalogueService(load_catalogue(PROJECT_ROOT / "catalogue_v3"))


@pytest.fixture()
def cube() -> FakeCube:
    return FakeCube()


@pytest.fixture()
def store(db):
    return DatasetStore(db, max_rows=100, max_bytes_per_brand=1_000_000)


@pytest.fixture()
def builder(v3, cube, result_store, store, monkeypatch) -> DatasetBuilder:
    monkeypatch.setattr(db_mod, "_now_bucket", lambda grain: "2026-10-23 12:00" if grain == "hour" else "2026-10-23")
    return DatasetBuilder(QueryPlanner(v3, cube, result_store), store, default_brand_id="20")


def spend_row(hour: str, platform: str, v: str) -> dict:
    return {"paid_media_hourly.report_hour.hour": f"2026-10-22T{hour}:00:00.000", "paid_media_hourly.platform": platform,
            "paid_media_hourly.ad_spend": v}


def order_row(hour: str, platform: str, v: str) -> dict:
    return {"commerce.order_created_at.hour": f"2026-10-22T{hour}:00:00.000", "commerce.platform": platform, "commerce.orders": v}


def spec(**kw) -> DatasetSpec:
    base = dict(measures=["ad_spend", "orders"], time_range=DAY, grain="hour", dimensions=[])
    base.update(kw)
    return DatasetSpec(**base)


async def test_hourly_parts_from_two_views_align_on_hour_and_dimension(builder, cube):
    cube.by_prefix["paid_media_hourly"] = [spend_row("09", "meta", "100.5"), spend_row("10", "meta", "50")]
    cube.by_prefix["commerce"] = [order_row("10", "meta", "3"), order_row("11", "meta", "2")]
    out = await builder.build(spec(dimensions=["platform"]), module_key="", name="t")
    rows = {r["bucket"]: r for r in out["preview"]}
    assert set(rows) == {"2026-10-22 09:00", "2026-10-22 10:00", "2026-10-22 11:00"}
    assert rows["2026-10-22 10:00"]["ad_spend"] == 50 and rows["2026-10-22 10:00"]["orders"] == 3
    assert rows["2026-10-22 09:00"]["ad_spend"] == 100.5
    names = {c["name"]: c for c in out["columns"]}
    assert names["bucket"]["role"] == "time" and names["platform"]["role"] == "dimension"
    assert names["ad_spend"]["source_view"] == "paid_media_hourly" and names["orders"]["source_view"] == "commerce"
    assert out["handle"].startswith("ds_") and out["query_id"] == out["handle"]
    assert out["timezone"] == "Asia/Kolkata"


async def test_zero_is_real_only_up_to_the_sources_last_bucket(builder, cube):
    cube.by_prefix["paid_media_hourly"] = [spend_row("09", "meta", "10"), spend_row("11", "meta", "5")]
    cube.by_prefix["commerce"] = [order_row("09", "meta", "1"), order_row("10", "meta", "1")]
    out = await builder.build(spec(), module_key="", name=None)
    ds = {r["bucket"]: r for r in get_page(builder.store.get(out["handle"], brand_key=None, module_key=""), columns=None, offset=0, limit=None)["rows"]}
    # no dimensions: the hourly spine is filled. ad_spend last observed 11:00 -> 10:00 is a real 0, 12:00+ is missing
    assert ds["2026-10-22 10:00"]["ad_spend"] == 0
    assert ds["2026-10-22 12:00"]["ad_spend"] is None
    # orders last observed 10:00 -> 11:00 is missing even though ad_spend has data there
    assert ds["2026-10-22 11:00"]["orders"] is None
    assert ds["2026-10-22 03:00"]["orders"] == 0  # at or before the source's last observed bucket
    assert len(ds) == 24


async def test_ratio_metric_is_never_filled_with_zero(builder, cube):
    cube.by_prefix["paid_media_hourly"] = [
        {"paid_media_hourly.report_hour.hour": "2026-10-22T09:00:00.000", "paid_media_hourly.ctr": "0.01"},
        {"paid_media_hourly.report_hour.hour": "2026-10-22T11:00:00.000", "paid_media_hourly.ctr": "0.02"},
    ]
    out = await builder.build(spec(measures=["ctr"]), module_key="", name=None)
    page = get_page(builder.store.get(out["handle"], brand_key=None, module_key=""), columns=None, offset=0, limit=None)["rows"]
    by = {r["bucket"]: r for r in page}
    assert by["2026-10-22 10:00"]["ctr"] is None


async def test_current_partial_hour_is_left_out_unless_asked(builder, cube):
    today = TimeRange(start="2026-10-23", end="2026-10-23")
    row = lambda h, v: {"paid_media_hourly.report_hour.hour": f"2026-10-23T{h}:00:00.000", "paid_media_hourly.ad_spend": v}  # noqa: E731
    cube.by_prefix["paid_media_hourly"] = [row("10", "1"), row("11", "2"), row("12", "3")]
    out = await builder.build(spec(measures=["ad_spend"], time_range=today), module_key="", name=None)
    buckets = [r["bucket"] for r in get_page(builder.store.get(out["handle"], brand_key=None, module_key=""), columns=None, offset=0, limit=5000)["rows"]]
    assert "2026-10-23 12:00" not in buckets and buckets[-1] == "2026-10-23 11:00"
    assert out["partial_bucket"] == "2026-10-23 12:00"
    kept = await builder.build(spec(measures=["ad_spend"], time_range=today, include_partial_hour=True), module_key="", name=None)
    ks = [r["bucket"] for r in get_page(builder.store.get(kept["handle"], brand_key=None, module_key=""), columns=None, offset=0, limit=5000)["rows"]]
    assert ks[-1] == "2026-10-23 12:00"


async def test_part_missing_a_requested_dimension_is_refused_as_unalignable(builder, cube):
    cube.by_prefix["paid_media_hourly"] = [spend_row("09", "meta", "1")]
    cube.by_prefix["commerce"] = [{"commerce.order_created_at.hour": "2026-10-22T09:00:00.000", "commerce.orders": "1"}]
    with pytest.raises(DatasetError) as e:
        await builder.build(spec(dimensions=["platform"]), module_key="", name=None)
    assert e.value.code == "unalignable" and e.value.extra["dimensions"] == ["platform"]


async def test_view_without_a_time_axis_for_the_grain_is_unalignable_or_refused(builder):
    # a daily-only view (no datetime dimension) cannot be asked for hours; the planner refuses before any join
    with pytest.raises(Exception) as e:
        await builder.build(spec(measures=["net_profit"]), module_key="", name=None)
    assert "hour" in str(e.value).lower()


async def test_unknown_grain_is_invalid_spec(builder):
    with pytest.raises(DatasetError) as e:
        await builder.build(spec(grain="week"), module_key="", name=None)
    assert e.value.code == "invalid_spec"


async def test_too_many_rows_refused_with_code(builder, cube, store):
    cube.by_prefix["paid_media_hourly"] = [spend_row("09", "meta", "1")]
    small = DatasetStore(store._db, max_rows=5, max_bytes_per_brand=1_000_000)
    b = DatasetBuilder(builder.planner, small, default_brand_id="20")
    with pytest.raises(DatasetError) as e:  # no dimensions -> 24 hourly spine rows > 5
        await b.build(spec(measures=["ad_spend"]), module_key="", name=None)
    assert e.value.code == "dataset_too_large" and e.value.extra["max_rows"] == 5


async def test_store_full_refused_and_delete_frees_it(builder, cube, store):
    cube.by_prefix["paid_media_hourly"] = [spend_row("09", "meta", "1")]
    tight = DatasetStore(store._db, max_rows=100, max_bytes_per_brand=400)
    b = DatasetBuilder(builder.planner, tight, default_brand_id="20")
    first = await b.build(spec(measures=["ad_spend"], dimensions=["platform"]), module_key="", name="a")
    with pytest.raises(DatasetError) as e:
        for _ in range(20):
            await b.build(spec(measures=["ad_spend"], dimensions=["platform"]), module_key="", name="x")
    assert e.value.code == "dataset_store_full"
    tight.delete(first["handle"], brand_key=None, module_key="")
    await b.build(spec(measures=["ad_spend"], dimensions=["platform"]), module_key="", name="again")


async def test_handles_are_scoped_by_module_and_brand(builder, cube, store):
    cube.by_prefix["paid_media_hourly"] = [spend_row("09", "meta", "1")]
    out = await builder.build(spec(measures=["ad_spend"], dimensions=["platform"]), module_key="growth", name=None)
    with pytest.raises(DatasetError) as e:
        store.get(out["handle"], brand_key=None, module_key="")
    assert e.value.code == "unknown_handle"
    with pytest.raises(DatasetError):
        store.get(out["handle"], brand_key="99", module_key="growth")
    assert store.get(out["handle"], brand_key="20", module_key="growth").row_count == out["row_count"]
    assert store.list(brand_key="20", module_key="growth")[0]["handle"] == out["handle"]
    assert store.list(brand_key="99", module_key="growth") == []
    store.delete(out["handle"], brand_key="20", module_key="growth")
    with pytest.raises(DatasetError):
        store.get(out["handle"], brand_key=None, module_key="growth")


async def test_brand_filter_scopes_the_stored_dataset(builder, cube, store):
    cube.by_prefix["paid_media_hourly"] = [spend_row("09", "meta", "1")]
    out = await builder.build(
        spec(measures=["ad_spend"], dimensions=["platform"], filters=[FilterSpec(dimension="brand_id", operator="equals", values=["24"])]),
        module_key="", name=None)
    assert store.get(out["handle"], brand_key="24", module_key="").brand_key == "24"
    with pytest.raises(DatasetError):
        store.get(out["handle"], brand_key="20", module_key="")


async def test_paging_and_slice(builder, cube, store):
    cube.by_prefix["paid_media_hourly"] = [spend_row(f"{h:02d}", p, "1") for h in range(0, 4) for p in ("meta", "google")]
    out = await builder.build(spec(measures=["ad_spend"], dimensions=["platform"]), module_key="", name=None)
    ds = store.get(out["handle"], brand_key=None, module_key="")
    page = get_page(ds, columns=None, offset=0, limit=3)
    assert page["returned"] == 3 and page["next_offset"] == 3 and page["row_count"] == 8
    last = get_page(ds, columns=["ad_spend"], offset=6, limit=10)
    assert last["next_offset"] is None and set(last["rows"][0]) == {"bucket", "ad_spend"}
    rows, schema = slice_rows(ds, columns=None, window={"start": "2026-10-22 01:00", "end": "2026-10-22 02:00"},
                              where=[{"column": "platform", "values": ["meta"]}])
    assert [r["bucket"] for r in rows] == ["2026-10-22 01:00", "2026-10-22 02:00"] and all(r["platform"] == "meta" for r in rows)
    with pytest.raises(DatasetError) as e:
        slice_rows(ds, columns=["nope"], window=None, where=None)
    assert e.value.code == "invalid_spec"
    with pytest.raises(DatasetError):
        slice_rows(ds, columns=None, window=None, where=[{"column": "ad_spend", "values": [1]}])


async def test_day_grain_builds_from_daily_views_and_names_the_axis(builder, cube):
    cube.by_prefix["paid_media"] = [{"paid_media.report_date.day": "2026-10-22T00:00:00.000", "paid_media.ad_spend": "10"}]
    cube.by_prefix["commerce"] = [{"commerce.order_date.day": "2026-10-22T00:00:00.000", "commerce.orders": "4"}]
    out = await builder.build(spec(grain="day"), module_key="", name=None)
    row = out["preview"][0]
    assert row["bucket"] == "2026-10-22" and row["ad_spend"] == 10 and row["orders"] == 4
    assert out["limitations"] and "Amazon" in out["limitations"][0]


# ---------------------------------------------------------------- the MCP tool end to end

@pytest.fixture()
def tool(settings, cube, result_store, monkeypatch):
    import dataclasses

    from seleric_mcp.gateway.server import build_server

    monkeypatch.setattr(db_mod, "_now_bucket", lambda grain: "2026-10-23 12:00" if grain == "hour" else "2026-10-23")
    s = dataclasses.replace(settings, catalogue_dir=PROJECT_ROOT / "catalogue_v3", freshness_enforcement=False,
                            default_brand_id="20")
    mcp = build_server(s)
    ctx = mcp._seleric_ctx
    ctx.planner = QueryPlanner(ctx.catalogue, cube, result_store)
    ctx.dataset_builder.planner = ctx.planner
    return mcp._tool_manager.get_tool("build_dataset_query").fn


async def test_tool_build_get_slice_list_delete_round_trip(tool, cube):
    cube.by_prefix["paid_media_hourly"] = [spend_row("09", "meta", "10"), spend_row("10", "google", "4")]
    built = await tool(action="build", measures=["ad_spend"], time_range={"start": "2026-10-22", "end": "2026-10-22"},
                       dimensions=["platform"], grain="hour", name="spend")
    assert built["row_count"] == 2 and built["handle"].startswith("ds_")
    page = await tool(action="get", handle=built["handle"])
    assert page["returned"] == 2 and page["query_id"] == built["handle"]
    sl = await tool(action="slice", handle=built["handle"], where=[{"column": "platform", "values": ["google"]}], save=True)
    assert sl["row_count"] == 1 and sl["sliced_from"] == built["handle"] and sl["handle"] != built["handle"]
    listed = await tool(action="list")
    assert {d["handle"] for d in listed["datasets"]} == {built["handle"], sl["handle"]}
    assert (await tool(action="delete", handle=built["handle"])) == {"deleted": built["handle"]}
    gone = await tool(action="get", handle=built["handle"])
    assert gone["code"] == "unknown_handle"


async def test_tool_errors_are_structured(tool, cube):
    assert (await tool(action="build"))["code"] == "invalid_spec"
    assert (await tool(action="get"))["code"] == "invalid_spec"
    assert (await tool(action="explode", handle="ds_x"))["code"] in ("invalid_spec", "unknown_handle")
    bad = await tool(action="build", measures=["ad_spend"], time_range={"start": "2026-10-22", "end": "2026-10-22"}, grain="week")
    assert bad["code"] == "invalid_spec"
    missing = await tool(action="build", measures=["not_a_metric"], time_range={"start": "2026-10-22", "end": "2026-10-22"})
    assert missing["code"] == "invalid_spec" and "error" in missing


async def test_hourly_ad_changes_are_hourly_capable_and_align_with_spend(builder, cube):
    cube.by_prefix["paid_media_hourly"] = [spend_row("09", "meta", "10"), spend_row("10", "meta", "5")]
    cube.by_prefix["paid_media_changes"] = [{"paid_media_changes.changed_at.hour": "2026-10-22T09:00:00.000", "paid_media_changes.budget_changes": "3"}]
    out = await builder.build(spec(measures=["ad_spend", "budget_changes"]), module_key="", name=None)
    by = {r["bucket"]: r for r in out["preview"]}
    assert by["2026-10-22 09:00"]["budget_changes"] == 3 and by["2026-10-22 10:00"]["ad_spend"] == 5
    assert {c["source_view"] for c in out["columns"] if c["role"] == "measure"} == {"paid_media_hourly", "paid_media_changes"}


async def test_a_source_with_later_data_makes_empty_buckets_real_zeros(v3, cube, result_store, store, monkeypatch):
    monkeypatch.setattr(db_mod, "_now_bucket", lambda grain: "2026-10-23 12:00" if grain == "hour" else "2026-10-23")
    seen = []

    async def latest(view, brand):
        seen.append((view, brand))
        return "2026-10-23"  # every view holds data after the range -> the whole range has landed

    b = DatasetBuilder(QueryPlanner(v3, cube, result_store), store, default_brand_id="20", latest_date=latest, hourly_notes={})
    cube.by_prefix["commerce"] = [order_row("09", "meta", "1")]  # sparse: one order, nothing after 09:00
    out = await b.build(spec(measures=["orders"]), module_key="", name=None)
    page = get_page(store.get(out["handle"], brand_key=None, module_key=""), columns=None, offset=0, limit=None)["rows"]
    by = {r["bucket"]: r for r in page}
    assert by["2026-10-22 23:00"]["orders"] == 0 and out["stats"]["orders"]["null"] == 0
    assert out["sources"][0]["watermark_basis"] == "the source holds later data" and seen == [("commerce", "20")]


async def test_the_range_that_reaches_now_falls_back_to_the_last_observed_bucket(v3, cube, result_store, store, monkeypatch):
    monkeypatch.setattr(db_mod, "_now_bucket", lambda grain: "2026-10-23 12:00" if grain == "hour" else "2026-10-23")

    async def latest(view, brand):
        return "2026-10-23"  # not after the range end (today) -> cannot vouch for the tail

    b = DatasetBuilder(QueryPlanner(v3, cube, result_store), store, default_brand_id="20", latest_date=latest, hourly_notes={})
    cube.by_prefix["commerce"] = [{"commerce.order_created_at.hour": "2026-10-23T09:00:00.000", "commerce.orders": "1"}]
    out = await b.build(spec(measures=["orders"], time_range=TimeRange(start="2026-10-23", end="2026-10-23")), module_key="", name=None)
    assert out["sources"][0]["watermark_basis"] == "last observed bucket" and out["sources"][0]["latest_complete_bucket"] == "2026-10-23 09:00"
    page = get_page(store.get(out["handle"], brand_key=None, module_key=""), columns=None, offset=0, limit=None)["rows"]
    assert {r["bucket"]: r["orders"] for r in page}["2026-10-23 10:00"] is None


async def test_hourly_notes_are_attached_for_the_views_that_have_one(v3, cube, result_store, store, monkeypatch):
    monkeypatch.setattr(db_mod, "_now_bucket", lambda grain: "2026-10-23 12:00" if grain == "hour" else "2026-10-23")
    b = DatasetBuilder(QueryPlanner(v3, cube, result_store), store, default_brand_id="20", hourly_notes={"commerce": "NOTE-C"})
    cube.by_prefix["commerce"] = [order_row("09", "meta", "1")]
    hourly = await b.build(spec(measures=["orders"]), module_key="", name=None)
    assert "NOTE-C" in hourly["limitations"]
    cube.by_prefix["commerce"] = [{"commerce.order_date.day": "2026-10-22T00:00:00.000", "commerce.orders": "1"}]
    daily = await b.build(spec(measures=["orders"], grain="day"), module_key="", name=None)
    assert "NOTE-C" not in daily["limitations"]


async def test_a_part_at_cubes_row_cap_is_refused_not_stored_incomplete(v3, cube, result_store, store, monkeypatch):
    monkeypatch.setattr(db_mod, "_now_bucket", lambda grain: "2026-10-23 12:00")
    b = DatasetBuilder(QueryPlanner(v3, cube, result_store), store, default_brand_id="20", hourly_notes={}, part_row_cap=3)
    cube.by_prefix["paid_media_hourly"] = [spend_row(f"{h:02d}", "meta", "1") for h in range(3)]
    with pytest.raises(DatasetError) as e:
        await b.build(spec(measures=["ad_spend"], dimensions=["platform"]), module_key="", name=None)
    assert e.value.code == "dataset_too_large" and e.value.extra["part_row_cap"] == 3
    assert store.list(brand_key=None, module_key="") == []
