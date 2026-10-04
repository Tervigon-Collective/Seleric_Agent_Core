"""Live smoke check of the agent surface: Cube v2 (default http://127.0.0.1:4002) + catalogue_v2.

Run:  uv run python scripts/smoke_cube.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from seleric_mcp.catalogue_service.loader import load_catalogue
from seleric_mcp.catalogue_service.service import CatalogueService
from seleric_mcp.catalogue_service.validate import validate_against_cube
from seleric_mcp.config import load_settings
from seleric_mcp.semantic_layer.cube_client import CubeClient


async def main() -> int:
    settings = load_settings()
    cube = CubeClient(settings)
    print(f"Cube API: {settings.cube_api_url}")

    ok = await cube.health()
    print(f"1. /v1/meta health: {'OK' if ok else 'FAILED'}")
    if not ok:
        print("   Cube unreachable — start it via this repo's docker-compose.yml (service cube-v2)")
        return 1

    service = CatalogueService(load_catalogue(settings.catalogue_dir))
    drift = await validate_against_cube(service, cube)
    print(f"2. catalogue drift: checked={drift['checked']} broken={drift['broken']}")

    res = await cube.load(
        {
            "measures": ["commerce.net_sales", "commerce.orders"],
            "filters": [{"member": "commerce.brand_id", "operator": "equals",
                         "values": [settings.default_brand_id]}],
            "timeDimensions": [{"dimension": "commerce.order_date", "dateRange": "last 7 days"}],
            "limit": 10,
        }
    )
    print(f"3. commerce last-7d load (brand {settings.default_brand_id}): {len(res.data)} row(s)")
    if res.data:
        print(json.dumps(res.data[0], indent=2))
    await cube.aclose()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
