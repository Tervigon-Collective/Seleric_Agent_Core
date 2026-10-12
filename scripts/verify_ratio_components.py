"""Reconcile every ratio metric's value against its independently computed components (SEM-14).

A ratio declares a numerator, denominator and (optional) multiplier. Cube is the
authoritative engine, but the catalogue must never claim a decomposition that does
not reconcile: for each ratio that resolves to component measures on a view, this
queries the ratio measure AND its components on real rows through Cube v2 and
checks

    |ratio − (Σnumerator / Σdenominator × multiplier)| / |ratio| <= tolerance

per brand and day, on the home view and on each binding view the ratio is served
from (hourly / breakdown). A miss means either the ratio contract or the component
resolution is wrong. Exit 1 on any miss.

    uv run python scripts/verify_ratio_components.py [--days 30] [--brand 20] [--tol 0.01]
"""

from __future__ import annotations

import argparse
import datetime as dt
import sys
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from build_catalogue_v2 import OUT  # noqa: E402
from verify_compositions import _load  # noqa: E402
from seleric_mcp.catalogue_service.loader import load_catalogue  # noqa: E402


def _view_axis(views: dict, view: str) -> str:
    axis = next(v["date_dimension"] for v in views if v["name"] == view)
    return axis if "." in axis else f"{view}.{axis}"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--brand", default="20")
    ap.add_argument("--tol", type=float, default=0.01, help="relative tolerance (1%%)")
    args = ap.parse_args()

    cat = load_catalogue(OUT)
    views = yaml.safe_load((OUT / "views.yaml").read_text())["views"]
    end = dt.date.today() - dt.timedelta(days=1)
    start = end - dt.timedelta(days=args.days)

    checked = failures = 0
    for mid, m in sorted(cat.metrics.items()):
        comps = m.ratio_components
        if not comps:
            continue
        rc = m.ratio_component_members
        if not rc:
            print(f"skip {mid}: components are cross-view (value from Cube only)")
            continue
        multiplier = float(comps.multiplier or 1)
        for view, members in sorted(rc.items()):
            ratio_member = next((b.measure for b in m.bindings if b.view == view), m.cube_mapping.measure)
            axis = _view_axis(views, view)
            try:
                rows = _load({
                    "measures": [ratio_member, *members],
                    "timeDimensions": [{"dimension": axis, "granularity": "day",
                                        "dateRange": [str(start), str(end)]}],
                    "filters": [{"member": f"{view}.brand_id", "operator": "equals", "values": [args.brand]}],
                    "limit": 5000,
                })
            except Exception as exc:  # a query that Cube rejects is itself a failure
                print(f"FAIL {mid} [{view}]: query error {exc}")
                failures += 1
                continue
            worst = (0.0, None, None)
            for r in rows:
                ratio = float(r.get(ratio_member) or 0)
                num = float(r.get(members[0]) or 0)
                den = float(r.get(members[1]) or 0)
                if den == 0:
                    continue
                expected = num / den * multiplier
                rel = abs(ratio - expected) / max(abs(expected), 1e-9)
                if rel > worst[0]:
                    worst = (rel, ratio, expected)
            ok = worst[0] <= args.tol
            checked += 1
            failures += not ok
            print(f"{'OK  ' if ok else 'FAIL'} {mid} [{view}] {members[0]}/{members[1]}"
                  f"{'×' + format(multiplier, 'g') if multiplier != 1 else ''}"
                  f"  ({len(rows)} days, worst rel {worst[0]:.4f}; e.g. {worst[1]} vs {worst[2]})")
    print(f"\n{checked} ratio/binding checks, {failures} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
