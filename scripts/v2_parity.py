#!/usr/bin/env python3
"""Semantic v2 parity: every entry of catalogue/migrations/v2_id_map.yaml, v1 Cube vs v2 Cube, queried back to back.

For each old id × brand × last full month: v1 `v1.member` on `v1.date_axis` vs v2 `member` (or `verify_member`) on
`date_axis` (or `verify_date_axis`) with the entry's filters, both scoped by the view's brand_id. Entries with
`known_diff` are expected to differ and are reported separately.

  uv run python scripts/v2_parity.py                       # brands 20,28 x last 3 full months
  uv run python scripts/v2_parity.py --only orders,meta_spend --months 1 --json out.json
Exit code 1 when an entry without known_diff differs.
"""
import argparse
import base64
import concurrent.futures as cf
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

import yaml

CORE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ID_MAP = os.path.join(CORE, "catalogue", "migrations", "v2_id_map.yaml")
V1_URL = os.environ.get("CUBE_V1_URL", "http://127.0.0.1:4001")
V2_URL = os.environ.get("CUBE_V2_URL", "http://127.0.0.1:4002")
CUBE_ENV = os.environ.get("SELERIC_CUBE_ENV", os.path.join(os.path.dirname(CORE), "mage-ai", "infra", "cube", ".env"))


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


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--brands", default="20,28")
    ap.add_argument("--months", type=int, default=3)
    ap.add_argument("--only", help="comma-separated old ids")
    ap.add_argument("--json", help="write every cell here")
    args = ap.parse_args()
    # v2 -> v2 consolidations (e.g. pnl_gross_cogs -> gross_cogs) carry no v1 member: nothing to compare
    maps = [m for m in yaml.safe_load(open(ID_MAP))["maps"] if m.get("v1")]
    if args.only:
        keep = set(args.only.split(","))
        maps = [m for m in maps if m["old"] in keep]
    tok = token(secret())
    months = last_full_months(args.months)
    jobs = [(m, b, s, e) for m in maps for b in args.brands.split(",") for s, e in months]

    def run(job):
        m, b, s, e = job
        mem = m.get("verify_member", m["member"])
        view = mem.split(".")[0]
        filt = {f"{view}.{k.split('.', 1)[1]}": v for k, v in (m.get("filters") or {}).items()}
        v1 = load(V1_URL, tok, m["v1"]["member"], m["v1"]["date_axis"], b, s, e)
        v2 = load(V2_URL, tok, mem, m.get("verify_date_axis", m["date_axis"]), b, s, e, filt)
        return {"old": m["old"], "new": m["new"], "brand": b, "month": s[:7], "v1": v1, "v2": v2,
                "match": same(v1, v2), "known_diff": m.get("known_diff")}

    with cf.ThreadPoolExecutor(6) as ex:
        cells = list(ex.map(run, jobs))
    if args.json:
        json.dump(cells, open(args.json, "w"), indent=1)
    by = {}
    for c in cells:
        by.setdefault(c["old"], []).append(c)
    unexpected = 0
    for old, cs in sorted(by.items()):
        bad = [c for c in cs if not c["match"]]
        if not bad:
            continue
        kind = "known diff" if cs[0]["known_diff"] else "DIFF"
        unexpected += not cs[0]["known_diff"]
        c = bad[0]
        print(f"  {kind:10s} {old:36s} → {c['new']:30s} {len(bad)}/{len(cs)} cells, e.g. "
              f"b{c['brand']} {c['month']}: v1={c['v1']} v2={c['v2']}")
    ok = sum(all(c["match"] for c in cs) for cs in by.values())
    print(f"{ok}/{len(by)} ids equal in every cell ({sum(c['match'] for c in cells)}/{len(cells)} cells); "
          f"unexpected differences: {unexpected}")
    return 1 if unexpected else 0


if __name__ == "__main__":
    sys.exit(main())
