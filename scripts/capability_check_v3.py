#!/usr/bin/env python3
"""Phase 7 no-capability-loss gate, on the MCP planner path (not raw Cube).

Every v2 metric id is asked of the v2 stack (catalogue_v2 on Cube v2 :4002) and of the v3 stack (catalogue_v3 on
Cube v3 :4003) in the same task, so the two answers see the same refresh state (L-18). The planner is the layer
that owns aliases, date_axis, refusals and dimension scoping, so that is the layer that is certified.

Layers (select with --layer, default all):
  metrics   v2 id x brand x month, no dimensions: same number, or an explained difference, or a refusal.
  dims      v2 id x each dimension v2 supports (one month, brand 20): v3 answers, refuses with a reason, or is a gap.

Outcome per row: EQUAL | DIFF | V3_REFUSED | V3_ERROR | V2_ERROR | BOTH_ERROR.  Differences are *not* judged here;
`--attribute` joins them to decision ids (D-xx) by rule in a second pass, and anything left is UNEXPLAINED.

  .venv/bin/python scripts/capability_check_v3.py --layer metrics --out results/cap_metrics.json
  .venv/bin/python scripts/capability_check_v3.py --layer dims    --out results/cap_dims.json
Exit 1 when any row is DIFF / V3_ERROR / V3_REFUSED-without-reason (until attributed), 0 otherwise.
"""
from __future__ import annotations

import argparse
import asyncio
import dataclasses
import datetime
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("SELERIC_CUBE_ENV", "/opt/seleric/mage-ai/infra/cube/.env")

import v2_parity  # noqa: E402  (secret / tolerance)
from seleric_mcp.app.models import FilterSpec, PlanError, QueryRequest, TimeRange  # noqa: E402
from seleric_mcp.app.query_planner import QueryPlanner  # noqa: E402
from seleric_mcp.app.result_store import ResultStore  # noqa: E402
from seleric_mcp.catalogue_service.loader import load_catalogue  # noqa: E402
from seleric_mcp.catalogue_service.service import CatalogueService  # noqa: E402
from seleric_mcp.config import load_settings  # noqa: E402
from seleric_mcp.semantic_layer.cube_client import CubeClient  # noqa: E402
from seleric_mcp.storage.db import Database  # noqa: E402

MONTHS = [("2026-07-01", "2026-07-31"), ("2026-08-01", "2026-08-31"), ("2026-09-01", "2026-09-30")]


class Stack:
    def __init__(self, name: str, url: str, cat_dir: Path):
        s = dataclasses.replace(load_settings(), cube_api_url=url, cubejs_api_secret=v2_parity.secret(),
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


def classify(r2: dict, r3: dict, mid: str) -> tuple[str, object, object]:
    v2 = scalar(r2, mid) if "rows" in r2 else None
    v3 = scalar(r3, mid) if "rows" in r3 else None
    if "error" in r2 and "error" in r3:
        return "BOTH_ERROR", None, None
    if "error" in r3:
        return "V3_ERROR", v2, None
    if "refused" in r3:
        return ("V3_REFUSED" if "refused" not in r2 and "error" not in r2 else "BOTH_ERROR"), v2, None
    if "error" in r2 or "refused" in r2:
        return "V2_ERROR", None, v3
    if v2 is None and v3 is None:
        return "EQUAL", v2, v3
    if isinstance(v2, (int, float)) and isinstance(v3, (int, float)) and v2_parity.same(v2, v3):
        return "EQUAL", v2, v3
    # a v2 NULL is an empty cohort; v3 zero on an empty cohort is the same answer
    if (v2 in (None, 0) and v3 in (None, 0)):
        return "EQUAL", v2, v3
    return "DIFF", v2, v3


def req(metric: str, start: str, end: str, brand: str, dims: list[str] | None = None, axis: str | None = None,
        limit: int | None = None) -> QueryRequest:
    return QueryRequest(measures=[metric], dimensions=dims or [], time_range=TimeRange(start=start, end=end),
                        filters=[FilterSpec(dimension="brand_id", operator="equals", values=[brand])],
                        date_axis=axis, limit=limit)


async def layer_metrics(v2: Stack, v3: Stack, brands: list[str], workers: int) -> list[dict]:
    ids = sorted(set(v2.svc.cat.metrics))
    jobs = [(m, b, s, e) for m in ids for b in brands for s, e in MONTHS]
    sem = asyncio.Semaphore(workers)
    rows: list[dict] = []

    async def one(m, b, s, e):
        async with sem:
            r2, r3 = await asyncio.gather(v2.ask(req(m, s, e, b)), v3.ask(req(m, s, e, b)))
        cls, a, c = classify(r2, r3, m)
        row = {"layer": "metrics", "metric": m, "brand": b, "month": s[:7], "outcome": cls, "v2": a, "v3": c}
        if "refused" in r3:
            row["v3_reason"] = r3["refused"]
        if "error" in r3:
            row["v3_error"] = r3["error"]
        if "error" in r2:
            row["v2_error"] = r2["error"]
        if "refused" in r2:
            row["v2_reason"] = r2["refused"]
        if r3.get("axis"):
            row["v3_axis"] = r3["axis"]
        rows.append(row)

    await asyncio.gather(*[one(*j) for j in jobs])
    return sorted(rows, key=lambda r: (r["metric"], r["brand"], r["month"]))


async def layer_dims(v2: Stack, v3: Stack, brand: str, workers: int, only: set | None = None) -> list[dict]:
    s, e = MONTHS[-1]
    jobs = []
    for mid, m in sorted(v2.svc.cat.metrics.items()):
        for d in getattr(m, "supported_dimensions", None) or []:
            if only is None or (mid, d) in only:
                jobs.append((mid, d))
    sem = asyncio.Semaphore(workers)
    rows: list[dict] = []

    async def one(m, d):
        async with sem:
            r2, r3 = await asyncio.gather(v2.ask(req(m, s, e, brand, [d], limit=500)),
                                          v3.ask(req(m, s, e, brand, [d], limit=500)))
        if "error" in r3:
            cls = "V3_ERROR"
        elif "refused" in r3:
            cls = "V3_REFUSED" if "rows" in r2 else "BOTH_ERROR"
        elif "rows" in r3 and "rows" in r2:
            cls = "ANSWERED"
        elif "rows" in r3:
            cls = "V2_ERROR"
        else:
            cls = "BOTH_ERROR"
        row = {"layer": "dims", "metric": m, "dimension": d, "brand": brand, "month": s[:7], "outcome": cls}
        if "rows" in r2 and "rows" in r3:
            row["v2_rows"], row["v3_rows"] = len(r2["rows"]), len(r3["rows"])
            row["v2_total"] = round(sum(float(x.get(m) or 0) for x in r2["rows"] if isinstance(x.get(m), (int, float, str))
                                        and str(x.get(m)).replace(".", "", 1).replace("-", "", 1).isdigit()), 4)
            row["v3_total"] = round(sum(float(x.get(m) or 0) for x in r3["rows"] if isinstance(x.get(m), (int, float, str))
                                        and str(x.get(m)).replace(".", "", 1).replace("-", "", 1).isdigit()), 4)
        for k, r in (("v3", r3), ("v2", r2)):
            if "refused" in r:
                row[f"{k}_reason"] = r["refused"]
            if "error" in r:
                row[f"{k}_error"] = r["error"]
        rows.append(row)

    await asyncio.gather(*[one(*j) for j in jobs])
    return sorted(rows, key=lambda r: (r["metric"], r["dimension"]))


async def amain(a) -> int:
    v2 = Stack("v2", a.v2_url, ROOT / "catalogue_v2")
    v3 = Stack("v3", a.v3_url, ROOT / "catalogue_v3")
    t0 = time.time()
    rows: list[dict] = []
    if a.layer in ("metrics", "all"):
        rows += await layer_metrics(v2, v3, a.brands.split(","), a.workers)
    if a.layer in ("dims", "all"):
        only = None
        if a.rerun:  # re-ask only the pairs a previous run did not answer on v3 (after a fix), keep the rest
            prev = json.load(open(a.rerun))["rows"]
            only = {(r["metric"], r["dimension"]) for r in prev if r["layer"] == "dims" and r["outcome"] in ("V3_ERROR", "V3_REFUSED", "BOTH_ERROR")}
        fresh = await layer_dims(v2, v3, a.dim_brand, a.workers, only)
        if a.rerun:
            redone = {(r["metric"], r["dimension"]) for r in fresh}
            rows += [r for r in prev if r["layer"] == "dims" and (r["metric"], r["dimension"]) not in redone]
        rows += fresh
    counts: dict = {}
    for r in rows:
        counts.setdefault(r["layer"], {}).setdefault(r["outcome"], 0)
        counts[r["layer"]][r["outcome"]] += 1
    doc = {"generated_by": "scripts/capability_check_v3.py",
           "generated_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "v2_url": a.v2_url, "v3_url": a.v3_url, "counts": counts, "seconds": round(time.time() - t0), "rows": rows}
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=1, default=str))
    print(json.dumps(counts), f"→ {out}")
    await v2.cube.aclose()
    await v3.cube.aclose()
    bad = {"DIFF", "V3_ERROR", "V3_REFUSED"}
    return 1 if any(r["outcome"] in bad for r in rows) else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--layer", choices=["metrics", "dims", "all"], default="all")
    ap.add_argument("--v2-url", default="http://127.0.0.1:4002")
    ap.add_argument("--v3-url", default="http://127.0.0.1:4003")
    ap.add_argument("--brands", default="20,28")
    ap.add_argument("--dim-brand", default="20")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--rerun", help="a previous --layer dims JSON: re-ask only its V3_ERROR / V3_REFUSED pairs")
    ap.add_argument("--out", default=str(ROOT / "doc" / "semantic_v3" / "results" / "capability_check.json"))
    return asyncio.run(amain(ap.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
