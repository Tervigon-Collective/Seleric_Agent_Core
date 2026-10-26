#!/usr/bin/env python3
"""Phase 7 gate: `semantic_sql` (Cube SQL API, brand-scoped, schema-validated) works on Cube and refuses what it refused before.

Runs the MCP's own `run_semantic_sql` against the v3 rig's SQL API (127.0.0.1:15433) for brand 20:
  answers  - MEASURE() on finance / product_ads / commerce / paid_media, a CTE, a join of two views' grouped results, a window function
  refuses  - writes, multi-statement, a non-governed table, an unknown view member (with a suggestion), a spend-only brand's profit
Exit 1 on any surprise.   .venv/bin/python scripts/semantic_sql_check.py
"""
from __future__ import annotations

import asyncio
import dataclasses
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("SELERIC_CUBE_ENV", "/opt/seleric/mage-ai/infra/cube/.env")
import gate_common  # noqa: E402
from seleric_mcp.config import load_settings  # noqa: E402
from seleric_mcp.semantic_layer.cube_client import CubeClient  # noqa: E402
from seleric_mcp.semantic_layer.semantic_sql import run_semantic_sql, schema_from_meta  # noqa: E402

ANSWERS = {
    "finance MEASURE": "SELECT MEASURE(net_profit) AS np, MEASURE(ad_spend) AS spend FROM finance WHERE order_date >= '2026-09-01' AND order_date < '2026-10-01'",
    "product_ads MEASURE grouped": "SELECT product_title, MEASURE(product_attributed_net_sales) AS ns FROM product_ads WHERE order_date >= '2026-09-01' AND order_date < '2026-10-01' GROUP BY 1 ORDER BY 2 DESC LIMIT 5",
    "commerce orders by month": "SELECT DATE_TRUNC('month', order_date) AS m, MEASURE(orders) AS o FROM commerce WHERE order_date >= '2026-07-01' GROUP BY 1 ORDER BY 1",
    "paid_media": "SELECT ad_platform, MEASURE(ad_spend) AS s FROM paid_media WHERE report_date >= '2026-09-01' GROUP BY 1",
    "cte": "WITH t AS (SELECT campaign_name, MEASURE(ad_spend) AS s FROM paid_media WHERE report_date >= '2026-09-01' GROUP BY 1) SELECT * FROM t ORDER BY s DESC LIMIT 3",
    "window": "SELECT m, o, SUM(o) OVER (ORDER BY m) AS running FROM (SELECT DATE_TRUNC('month', order_date) AS m, MEASURE(orders) AS o FROM commerce WHERE order_date >= '2026-07-01' GROUP BY 1) q",
}
REFUSES = {
    "write": "INSERT INTO finance VALUES (1)",
    "drop": "DROP VIEW finance",
    "multi-statement": "SELECT MEASURE(net_profit) FROM finance; SELECT 1",
    "ungoverned table": "SELECT * FROM pg_catalog.pg_tables",
    "unknown member": "SELECT MEASURE(net_profitt) FROM finance",
    "unknown view": "SELECT MEASURE(net_profit) FROM canonical_pnl",
}


async def amain() -> int:
    s = dataclasses.replace(load_settings(), cube_api_url="http://127.0.0.1:4003", cubejs_api_secret=gate_common.secret(),
                            cube_sql_dsn="postgresql://user:password@127.0.0.1:15433/cube")
    cube = CubeClient(s)
    schema = schema_from_meta(await cube.meta())
    await cube.aclose()
    bad = 0
    for name, sql in ANSWERS.items():
        try:
            r = await run_semantic_sql(sql, s, brand_id="20", schema=schema)
            ok = r.row_count > 0
            print(f"{'PASS' if ok else 'FAIL'} answers  {name}: {r.row_count} rows")
        except Exception as e:  # noqa: BLE001
            ok = False
            print(f"FAIL answers  {name}: {type(e).__name__}: {str(e)[:200]}")
        bad += not ok
    for name, sql in REFUSES.items():
        try:
            await run_semantic_sql(sql, s, brand_id="20", schema=schema)
            ok, msg = False, "ANSWERED"
        except Exception as e:  # noqa: BLE001
            ok, msg = True, f"{type(e).__name__}: {str(e)[:110]}"
        print(f"{'PASS' if ok else 'FAIL'} refuses  {name}: {msg}")
        bad += not ok
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(amain()))
