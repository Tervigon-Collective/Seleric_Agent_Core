"""Check every catalogue formula.composition against the data, day by day.

A composition says a metric IS the signed sum of other metrics on its view (net profit = contribution margin −
ad spend …). Agents build breakdowns, waterfalls and bridges from it and promise they reconcile, so each one is
checked on real rows through Cube v2: per brand and day over the window, |total − Σ sign·part| must stay within
a cent-level tolerance. Exit 1 on any miss.

    uv run python scripts/verify_compositions.py [--days 120] [--brand 20]
"""

from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import hmac
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_catalogue_v2 import CUBE_ENV, CUBE_V2_URL, OUT  # noqa: E402

_TOLERANCE = 0.05  # currency units per day: float sums of paise-level rows


def _token() -> str:
    secret = next(
        line.split("=", 1)[1].strip().strip("'\"") for line in CUBE_ENV.open() if line.startswith("CUBEJS_API_SECRET=")
    )

    def b(x: bytes) -> str:
        return base64.urlsafe_b64encode(x).rstrip(b"=").decode()

    now = int(time.time())
    head, body = b(b'{"alg":"HS256","typ":"JWT"}'), b(json.dumps({"iat": now, "exp": now + 600}).encode())
    return f"{head}.{body}." + b(hmac.new(secret.encode(), f"{head}.{body}".encode(), hashlib.sha256).digest())


def _load(query: dict) -> list[dict]:
    url = f"{CUBE_V2_URL}/cubejs-api/v1/load?query=" + urllib.parse.quote(json.dumps(query))
    tok = _token()
    for _ in range(30):
        res = json.load(urllib.request.urlopen(urllib.request.Request(url, headers={"Authorization": tok}), timeout=120))
        if res.get("error") != "Continue wait":
            if "error" in res:
                raise RuntimeError(res["error"])
            return res["data"]
        time.sleep(2)
    raise TimeoutError(query)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=120)
    ap.add_argument("--brand", default="20")
    args = ap.parse_args()
    metrics = {d["id"]: d for d in (yaml.safe_load(p.read_text()) for p in (OUT / "metrics").glob("*.yaml"))}
    end = dt.date.today() - dt.timedelta(days=1)
    start = end - dt.timedelta(days=args.days)
    failures = 0
    for mid, m in sorted(metrics.items()):
        terms = (m.get("formula") or {}).get("composition") or []
        if not terms:
            continue
        view = m["cube_mapping"]["view"]
        axis = m["cube_mapping"].get("time_dimension") or next(
            v["date_dimension"] for v in yaml.safe_load((OUT / "views.yaml").read_text())["views"] if v["name"] == view
        )
        axis = axis if "." in axis else f"{view}.{axis}"
        members = [m["cube_mapping"]["measure"], *(metrics[t["metric"]]["cube_mapping"]["measure"] for t in terms)]
        rows = _load({
            "measures": members,
            "timeDimensions": [{"dimension": axis, "granularity": "day", "dateRange": [str(start), str(end)]}],
            "filters": [{"member": f"{view}.brand_id", "operator": "equals", "values": [args.brand]}],
            "limit": 5000,
        })
        worst = (0.0, None)
        for r in rows:
            total = float(r.get(members[0]) or 0)
            parts = sum(int(t["sign"]) * float(r.get(mm) or 0) for t, mm in zip(terms, members[1:], strict=True))
            gap = abs(total - parts)
            if gap > worst[0]:
                worst = (gap, r.get(f"{axis}.day"))
        ok = worst[0] <= _TOLERANCE
        failures += not ok
        formula = " ".join(f"{'+' if t['sign'] > 0 else '−'} {t['metric']}" for t in terms)
        print(f"{'OK  ' if ok else 'FAIL'} {mid} = {formula}  ({len(rows)} days, worst gap {worst[0]:.4f} on {worst[1]})")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
