"""Helpers the certification gates share: the Cube login, a catalogue + planner stack over a live Cube, one scalar query.

Used by axis_certify, golden_axis, drill_check, cube_member_sweep and semantic_sql_check.
"""
from __future__ import annotations

import base64
import dataclasses
import datetime
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
CUBE_URL = os.environ.get("CUBE_URL", "http://127.0.0.1:4003")
CUBE_ENV = os.environ.get("SELERIC_CUBE_ENV", str(ROOT.parent / "mage-ai" / "infra" / "cube" / ".env"))

from seleric_mcp.app.models import FilterSpec, PlanError, QueryRequest, TimeRange  # noqa: E402
from seleric_mcp.app.query_planner import QueryPlanner  # noqa: E402
from seleric_mcp.app.result_store import ResultStore  # noqa: E402
from seleric_mcp.catalogue_service.loader import load_catalogue  # noqa: E402
from seleric_mcp.catalogue_service.service import CatalogueService  # noqa: E402
from seleric_mcp.config import load_settings  # noqa: E402
from seleric_mcp.semantic_layer.cube_client import CubeClient  # noqa: E402
from seleric_mcp.storage.db import Database  # noqa: E402


def secret() -> str:
    if os.environ.get("CUBEJS_API_SECRET"):
        return os.environ["CUBEJS_API_SECRET"]
    for line in open(CUBE_ENV):
        if line.startswith("CUBEJS_API_SECRET="):
            return line.split("=", 1)[1].strip().strip("'\"")
    raise SystemExit(f"CUBEJS_API_SECRET not set and not in {CUBE_ENV}")


def token(key: str) -> str:
    b = lambda x: base64.urlsafe_b64encode(x).rstrip(b"=").decode()  # noqa: E731
    now = int(time.time())
    head = b(json.dumps({"alg": "HS256", "typ": "JWT"}).encode())
    body = b(json.dumps({"iat": now, "exp": now + 3600}).encode())
    return f"{head}.{body}." + b(hmac.new(key.encode(), f"{head}.{body}".encode(), hashlib.sha256).digest())


def last_full_months(n: int):
    first = datetime.date.today().replace(day=1)
    out = []
    for _ in range(n):
        end = first - datetime.timedelta(days=1)
        first = end.replace(day=1)
        out.append((first.isoformat(), end.isoformat()))
    return list(reversed(out))


def load(url, tok, member, axis, brand, start, end, filters=None):
    view = member.split(".")[0]
    flt = [{"member": f"{view}.brand_id", "operator": "equals", "values": [brand]}]
    flt += [{"member": k, "operator": "equals", "values": [str(v)]} for k, v in (filters or {}).items()]
    q = {"measures": [member], "timezone": "Asia/Kolkata", "filters": flt,
         "timeDimensions": [{"dimension": axis, "dateRange": [start, end]}]}
    req = urllib.request.Request(f"{url}/cubejs-api/v1/load?query=" + urllib.parse.quote(json.dumps(q)),
                                 headers={"Authorization": tok})
    for _ in range(60):
        try:
            d = json.load(urllib.request.urlopen(req, timeout=300))
        except urllib.error.HTTPError as e:
            d = json.loads(e.read() or b"{}")
        except Exception as e:  # noqa: BLE001
            return f"ERR {e}"
        if d.get("error") == "Continue wait":
            time.sleep(1)
            continue
        if d.get("error"):
            return "ERR " + str(d["error"])[:200]
        rows = d.get("data") or []
        try:
            return round(float(rows[0].get(member)), 6) if rows else None
        except (TypeError, ValueError):
            return None
    return "ERR timeout"


def same(a, b):
    if isinstance(a, str) or isinstance(b, str):
        return False
    if a is None or b is None:
        return (a or 0) == (b or 0)
    return abs(a - b) <= max(0.011, 1e-6 * abs(a))


MONTHS = [("2026-07-01", "2026-07-31"), ("2026-08-01", "2026-08-31"), ("2026-09-01", "2026-09-30")]


class Stack:
    def __init__(self, name: str, url: str, cat_dir: Path):
        s = dataclasses.replace(load_settings(), cube_api_url=url, cubejs_api_secret=secret(),
                                write_enabled=False, catalogue_dir=cat_dir)
        self.name = name
        self.svc = CatalogueService(load_catalogue(cat_dir))
        self.cube = CubeClient(s)
        self.planner = QueryPlanner(self.svc, self.cube, ResultStore(Database(":memory:")), default_brand_id="20")

    async def ask(self, req: QueryRequest) -> dict:
        try:
            out = await self.planner.run(req)
        except PlanError as e:
            return {"refused": str(e)[:400]}
        except Exception as e:  # noqa: BLE001
            return {"error": f"{type(e).__name__}: {str(e)[:300]}"}
        return {"columns": out.get("columns"), "rows": out.get("rows") or [], "warnings": out.get("warnings") or [],
                "axis": out.get("date_axis_applied") or out.get("date_axis")}


def scalar(res: dict, metric: str):
    """The value of `metric` in a no-dimension answer (None when there is no row / column)."""
    rows = res.get("rows") or []
    if not rows:
        return None
    row = rows[0]
    if metric in row:
        v = row[metric]
    elif len(row) == 1:
        v = next(iter(row.values()))
    else:  # alias answers carry both the base and the alias id
        v = row.get(metric)
    try:
        return None if v is None else round(float(v), 6)
    except (TypeError, ValueError):
        return v


def req(metric: str, start: str, end: str, brand: str, dims: list[str] | None = None, axis: str | None = None,
        limit: int | None = None) -> QueryRequest:
    return QueryRequest(measures=[metric], dimensions=dims or [], time_range=TimeRange(start=start, end=end),
                        filters=[FilterSpec(dimension="brand_id", operator="equals", values=[brand])],
                        date_axis=axis, limit=limit)
