"""Live verify hour granularity against Cube (post datetime_dimension fix).

Run:  uv run python scripts/verify_hour_granularity.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from seleric_mcp.app.models import QueryRequest, TimeRange
from seleric_mcp.app.query_planner import PlanError, QueryPlanner
from seleric_mcp.app.result_store import ResultStore
from seleric_mcp.catalogue_service.loader import load_catalogue
from seleric_mcp.catalogue_service.service import CatalogueService
from seleric_mcp.config import PROJECT_ROOT, load_settings
from seleric_mcp.semantic_layer.cube_client import CubeClient
from seleric_mcp.storage.db import Database


async def main() -> int:
    settings = load_settings()
    cat = CatalogueService(load_catalogue(PROJECT_ROOT / "catalogue"))
    cube = CubeClient(settings)
    db = Database(PROJECT_ROOT / "var" / "verify_hour.db")
    store = ResultStore(db, ttl=timedelta(minutes=5))
    planner = QueryPlanner(
        cat, cube, store, default_brand_id=settings.default_brand_id or None
    )

    day = await planner.run(
        QueryRequest(
            measures=["orders", "total_sales"],
            time_range=TimeRange(start=date(2026, 7, 29), end=date(2026, 7, 29)),
            granularity="none",
        )
    )
    print("=== DAY TOTAL (granularity=none) ===")
    print(json.dumps(day.get("rows"), indent=2))

    hour = await planner.run(
        QueryRequest(
            measures=["orders", "total_sales"],
            time_range=TimeRange(start=date(2026, 7, 29), end=date(2026, 7, 29)),
            granularity="hour",
        )
    )
    rows = hour.get("rows") or []
    cq = (hour.get("provenance") or {}).get("cube_query") or {}
    print(f"\n=== HOURLY ({len(rows)} buckets) ===")
    print("timeDimensions:", json.dumps(cq.get("timeDimensions"), indent=2))
    for r in rows:
        print(r)

    # Prefer bare catalogue aliases so we don't double-count Cube-qualified keys.
    orders = sales = 0.0
    for r in rows:
        orders += float(r.get("orders") or r.get("commerce_orders.orders") or 0)
        sales += float(r.get("total_sales") or r.get("commerce_orders.total_sales") or 0)
    print(f"\nHOURLY SUM (created_at axis): orders={orders:.0f} total_sales={sales:.2f}")
    day_row = (day.get("rows") or [{}])[0]
    print(
        "DAY TOTAL (order_date axis): "
        f"orders={day_row.get('orders')} total_sales={day_row.get('total_sales')}"
    )
    print(
        "Note: hour buckets sum on order_created_at_ist calendar day; "
        "day card uses order_date — totals can differ."
    )

    try:
        await planner.run(
            QueryRequest(
                measures=["total_orders"],
                time_range=TimeRange(start=date(2026, 7, 29), end=date(2026, 7, 29)),
                granularity="hour",
            )
        )
        print("FAIL: total_orders hour should raise")
        return 1
    except PlanError as e:
        print(f"OK reject total_orders hour: {e}")

    await cube.aclose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
