#!/usr/bin/env python3
"""Semantic v3 number baseline: every catalogue metric (and each of its bindings) × brands × months on one Cube.

Phase 0 snapshots catalogue_v2 on Cube v2 (:4002) → catalogue/baselines/v2.json. Phase 7 re-runs it against v2 and
v3 back to back (Jul–Sep cohorts keep maturing, so a stored snapshot alone goes stale) and compares with --compare.

Each row: metric id, member, date axis, brand, month, value — or an explicit error / null (brand 28 is spend-only, so
commerce / P&L members are null or refused there: that is the expected answer, recorded, not skipped).

  uv run python scripts/v3_baseline.py                                   # v2, brands 20,28, Jul–Sep 2026
  uv run python scripts/v3_baseline.py --url http://127.0.0.1:4003 --catalogue catalogue_v3 --out /tmp/v3.json
  uv run python scripts/v3_baseline.py --compare catalogue/baselines/v2.json /tmp/v3.json
Exit 0 (snapshot) / 1 when --compare finds a difference.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import datetime
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
sys.path.insert(0, str(ROOT / "scripts"))
os.environ.setdefault("SELERIC_CUBE_ENV", "/opt/seleric/mage-ai/infra/cube/.env")

import v2_parity  # noqa: E402  (token / secret / tolerance)
from seleric_mcp.catalogue_service.loader import load_catalogue  # noqa: E402

MONTHS = [("2026-07-01", "2026-07-31"), ("2026-08-01", "2026-08-31"), ("2026-09-01", "2026-09-30")]


def load(url: str, tok: str, member: str, axis: str, brand: str, start: str, end: str):
    view = member.split(".")[0]
    q = {"measures": [member], "timezone": "Asia/Kolkata",
         "filters": [{"member": f"{view}.brand_id", "operator": "equals", "values": [brand]}],
         "timeDimensions": [{"dimension": axis, "dateRange": [start, end]}]}
    req = urllib.request.Request(f"{url}/cubejs-api/v1/load?query=" + urllib.parse.quote(json.dumps(q)),
                                 headers={"Authorization": tok})
    for _ in range(120):
        try:
            d = json.load(urllib.request.urlopen(req, timeout=300))
        except urllib.error.HTTPError as e:
            d = json.loads(e.read() or b"{}")
        except Exception as e:  # noqa: BLE001
            return {"error": str(e)[:200]}
        if d.get("error") == "Continue wait":
            time.sleep(1)
            continue
        if d.get("error"):
            return {"error": str(d["error"])[:300]}
        rows = d.get("data") or []
        v = rows[0].get(member) if rows else None
        try:
            return {"value": None if v is None else round(float(v), 6)}
        except (TypeError, ValueError):
            return {"value": None}
    return {"error": "timeout"}


def jobs(cat, brands, months):
    out = []
    for mid, m in sorted(cat.metrics.items()):
        targets = [("primary", m.cube_mapping.measure, m.cube_mapping.time_dimension)]
        targets += [(b.name, b.measure, b.time_dimension) for b in m.bindings]
        for name, member, axis in targets:
            view = member.split(".")[0]
            if not axis:
                vd = cat.views.get(view)
                axis = f"{view}.{vd.date_dimension}" if vd and vd.date_dimension else None
            elif "." not in axis:
                axis = f"{view}.{axis}"
            for brand in brands:
                for start, end in months:
                    out.append({"metric": mid, "binding": name, "member": member, "date_axis": axis,
                                "status": m.status, "brand": brand, "month": start[:7], "start": start, "end": end})
    return out


def compare(a_path: str, b_path: str) -> int:
    a, b = json.load(open(a_path)), json.load(open(b_path))
    key = lambda r: (r["metric"], r["binding"], r["brand"], r["month"])  # noqa: E731
    bi = {key(r): r for r in b["rows"]}
    diff = missing = 0
    for r in a["rows"]:
        o = bi.get(key(r))
        if o is None:
            missing += 1
            print("MISSING", *key(r))
            continue
        va, vb = r.get("value", r.get("error")), o.get("value", o.get("error"))
        if isinstance(va, str) or isinstance(vb, str) or not v2_parity.same(va, vb):
            if va != vb:
                diff += 1
                print("DIFF", *key(r), va, "→", vb)
    print(f"{len(a['rows'])} rows; {diff} differ; {missing} missing in {b_path}")
    return 1 if diff or missing else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", default=os.environ.get("CUBE_V2_URL", "http://127.0.0.1:4002"))
    ap.add_argument("--catalogue", default=str(ROOT / "catalogue_v2"))
    ap.add_argument("--brands", default="20,28")
    ap.add_argument("--out", default=str(ROOT / "catalogue" / "baselines" / "v2.json"))
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--compare", nargs=2, metavar=("A", "B"))
    a = ap.parse_args()
    if a.compare:
        return compare(*a.compare)

    cat = load_catalogue(Path(a.catalogue))
    todo = jobs(cat, a.brands.split(","), MONTHS)
    tok = v2_parity.token(v2_parity.secret())
    started = time.time()
    with cf.ThreadPoolExecutor(a.workers) as ex:
        res = list(ex.map(lambda j: load(a.url, tok, j["member"], j["date_axis"], j["brand"], j["start"], j["end"]),
                          todo))
    rows = []
    for j, r in zip(todo, res):
        j = {k: v for k, v in j.items() if k not in ("start", "end")}
        rows.append({**j, **r})
    errs = sum("error" in r for r in rows)
    nulls = sum(r.get("value", 0) is None for r in rows)
    doc = {"generated_by": "scripts/v3_baseline.py",
           "generated_at": datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "cube_url": a.url, "catalogue": os.path.relpath(a.catalogue, ROOT), "brands": a.brands.split(","),
           "months": [m[0][:7] for m in MONTHS], "timezone": "Asia/Kolkata",
           "counts": {"rows": len(rows), "errors": errs, "nulls": nulls, "seconds": round(time.time() - started)},
           "rows": rows}
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(doc, indent=1))
    print(json.dumps(doc["counts"]), "→", a.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
