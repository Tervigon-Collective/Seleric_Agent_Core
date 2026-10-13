#!/usr/bin/env python3
"""Query every public Cube view member once and report the ones that fail (live-defect sweep).

Each measure is queried alone; each dimension is queried with the view's first measure; each time dimension by day.
Every query is scoped to one brand and one month. Phase 0 used it on Cube v2 (findings F0-1 / F0-2), and Phase 7
runs it on v3.

  uv run python scripts/cube_member_sweep_v3.py                                  # Cube v2 :4002, brand 20, Sep 2026
  uv run python scripts/cube_member_sweep_v3.py --url http://127.0.0.1:4003 --brand 20 --month 2026-09
Exit 1 when any member fails.
"""
from __future__ import annotations

import argparse
import calendar
import concurrent.futures as cf
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
os.environ.setdefault("SELERIC_CUBE_ENV", "/opt/seleric/mage-ai/infra/cube/.env")
import v2_parity  # noqa: E402  (token / secret)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--url", default=os.environ.get("CUBE_V2_URL", "http://127.0.0.1:4002"))
    ap.add_argument("--brand", default="20")
    ap.add_argument("--month", default="2026-09")
    ap.add_argument("--workers", type=int, default=4)
    a = ap.parse_args()
    y, m = map(int, a.month.split("-"))
    rng = [f"{a.month}-01", f"{a.month}-{calendar.monthrange(y, m)[1]:02d}"]
    tok = v2_parity.token(v2_parity.secret())
    req = urllib.request.Request(f"{a.url}/cubejs-api/v1/meta", headers={"Authorization": tok})
    meta = json.load(urllib.request.urlopen(req, timeout=120))

    jobs = []
    for c in meta["cubes"]:
        if c.get("type") != "view":
            continue
        first = c["measures"][0]["name"] if c["measures"] else None
        td = next((d["name"] for d in c["dimensions"] if d["type"] == "time"), None)
        base = {"limit": 1, "filters": [{"member": f"{c['name']}.brand_id", "operator": "equals", "values": [a.brand]}]}
        tdr = [{"dimension": td, "dateRange": rng}] if td else []
        for mem in c["measures"]:
            jobs.append((mem["name"], {**base, "measures": [mem["name"]], "timeDimensions": tdr}))
        for d in c["dimensions"]:
            if d["type"] == "time":
                q = {**base, "timeDimensions": [{"dimension": d["name"], "granularity": "day", "dateRange": rng}]}
            else:
                q = {**base, "dimensions": [d["name"]], "timeDimensions": tdr}
            if first:
                q["measures"] = [first]
            jobs.append((d["name"], q))

    def run(job):
        name, q = job
        r = urllib.request.Request(f"{a.url}/cubejs-api/v1/load?query=" + urllib.parse.quote(json.dumps(q)),
                                   headers={"Authorization": tok})
        for _ in range(120):
            try:
                d = json.load(urllib.request.urlopen(r, timeout=300))
            except urllib.error.HTTPError as e:
                d = json.loads(e.read() or b"{}")
            except Exception as e:  # noqa: BLE001
                return name, f"EXC {e}"
            if d.get("error") == "Continue wait":
                time.sleep(1)
                continue
            return name, d.get("error")
        return name, "timeout"

    with cf.ThreadPoolExecutor(a.workers) as ex:
        bad = [(n, e) for n, e in ex.map(run, jobs) if e]
    print(f"{len(jobs)} members queried on {a.url} (brand {a.brand}, {a.month}); {len(bad)} failed")
    for n, e in bad:
        print(f"  {n}: {str(e)[:220]}".replace("\n", " "))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
