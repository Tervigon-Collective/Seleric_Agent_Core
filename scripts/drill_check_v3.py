#!/usr/bin/env python3
"""Phase 7 drill-path gate: every hierarchy in catalogue_v3/hierarchies.yaml, on every view that carries it, drills level by level on Cube v3.

For each (hierarchy, view): the view's first count/sum measure grouped by level N → take the busiest member → the same measure grouped by
level N+1 filtered to that member must answer (rows may be empty only when the first level had no data for brand 20). Brand 20, Sep 2026.
Exit 1 on any Cube error.   .venv/bin/python scripts/drill_check_v3.py
"""
from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from pathlib import Path

import jwt
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import v2_parity  # noqa: E402

URL = os.environ.get("CUBE_V3_URL", "http://127.0.0.1:4003")


def load(q: dict):
    tok = jwt.encode({"iat": int(time.time()), "exp": int(time.time()) + 600}, v2_parity.secret(), algorithm="HS256")
    for _ in range(40):
        req = urllib.request.Request(f"{URL}/cubejs-api/v1/load", json.dumps({"query": q}).encode(), {"Authorization": tok, "Content-Type": "application/json"})
        try:
            d = json.load(urllib.request.urlopen(req, timeout=300))
        except urllib.error.HTTPError as e:
            return None, e.read().decode()[:300]
        if d.get("error") == "Continue wait":
            time.sleep(1)
            continue
        return d["data"], None
    return None, "Continue wait did not clear"


def main() -> int:
    meta = json.load(urllib.request.urlopen(urllib.request.Request(
        f"{URL}/cubejs-api/v1/meta", headers={"Authorization": jwt.encode({"iat": int(time.time())}, v2_parity.secret(), algorithm="HS256")}), timeout=60))
    cubes = {c["name"]: c for c in meta["cubes"]}
    hier = yaml.safe_load(open(ROOT / "catalogue_v3" / "hierarchies.yaml"))["hierarchies"]
    ok = bad = 0
    for hname, h in hier.items():
        for view in h.get("views", []):
            c = cubes.get(view)
            if not c:
                print(f"FAIL {hname}/{view}: view not in Cube meta")
                bad += 1
                continue
            dims = {d["name"].split(".")[1] for d in c["dimensions"]}
            measures = [m["name"] for m in c["measures"] if m.get("type") in ("count", "sum", "number") and not m["name"].endswith("_pct")]
            levels = [l for l in h["levels"] if l in dims]
            if not measures or len(levels) < 2:
                continue
            tdim = next((d["name"] for d in c["dimensions"] if d["type"] == "time"), None)
            base = {"measures": [measures[0]], "limit": 5}
            if tdim:
                base["timeDimensions"] = [{"dimension": tdim, "dateRange": ["2026-09-01", "2026-09-30"]}]
            if "brand_id" in dims:
                base["filters"] = [{"member": f"{view}.brand_id", "operator": "equals", "values": ["20"]}]
            for a, b in zip(levels, levels[1:]):
                top, err = load({**base, "dimensions": [f"{view}.{a}"], "order": {measures[0]: "desc"}})
                if err:
                    print(f"FAIL {hname}/{view} {a}: {err}")
                    bad += 1
                    continue
                val = next((r[f"{view}.{a}"] for r in top if r.get(f"{view}.{a}") not in (None, "")), None)
                q = {**base, "dimensions": [f"{view}.{b}"]}
                if val is not None:
                    q["filters"] = base.get("filters", []) + [{"member": f"{view}.{a}", "operator": "equals", "values": [str(val)]}]
                _, err = load(q)
                if err:
                    print(f"FAIL {hname}/{view} {a}→{b}: {err}")
                    bad += 1
                else:
                    ok += 1
    print(f"{ok}/{ok + bad} drill steps answer")
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
