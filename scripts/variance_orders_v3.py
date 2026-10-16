#!/usr/bin/env python3
"""Phase 7 variance attribution, order-count family: v2 set − v3 set, order by order.

An order-count metric difference is not "explained" by a label: the order sets are compared. For each metric
(orders, cod_orders, prepaid_orders, payment_orders), brand and month:

    v3 count = v2 count − (orders only in v2, grouped by their v3 order_type) + (orders only in v3, grouped by flag)

must hold exactly, and both counts must equal the Cube answers recorded by capability_check_v3.py (same refresh
state, else the row says `snapshot drift`). A remainder is UNEXPLAINED and exits 1 (interview rule: stop, ask).

  .venv/bin/python scripts/variance_orders_v3.py --cap doc/semantic_v3/results/capability_metrics.json
Needs read access to ClickHouse through the mage-ai worktree's evaluation harness (--mage).
"""
from __future__ import annotations

import argparse
import calendar
import json
import sys
from pathlib import Path

DEFAULT_MAGE = "/home/tervigon/work/mage_v3"
MONTHS = ["2026-07", "2026-08", "2026-09"]

# (metric id, v2 extra predicate on serve.commerce_orders c, v3 extra predicate on serve.fct_order_commerce o)
FAMILY = {
    "orders": ("1", "1"),
    "cod_orders": ("c.payment_bucket = 'cod' AND c.is_draft_order = 0", "o.payment_bucket = 'cod' AND o.is_draft_order = 0"),
    "prepaid_orders": ("c.payment_bucket = 'online' AND c.is_draft_order = 0", "o.payment_bucket = 'online' AND o.is_draft_order = 0"),
    "payment_orders": ("c.payment_bucket IN ('cod','online','manual','paytm card machine') AND c.is_draft_order = 0",
                       "o.payment_bucket IN ('cod','online','manual','paytm card machine') AND o.is_draft_order = 0"),
}
DECISION = {"exchange_replacement": "D-01 exchange replacement is not a sale", "zero_value": "D-08 zero-value order is not a sale",
            "test": "D-08a test order excluded"}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--mage", default=DEFAULT_MAGE)
    ap.add_argument("--cap", help="capability_check_v3.py metrics JSON, to tie the sets to the Cube answers")
    ap.add_argument("--brand", default="20")
    ap.add_argument("--out")
    a = ap.parse_args()
    sys.path.insert(0, f"{a.mage}/serve/evaluations")
    import v3_contracts as V  # noqa: E402

    V.apply_v3.load_env()
    cube = {}
    if a.cap:
        for r in json.load(open(a.cap))["rows"]:
            if r["brand"] == a.brand:
                cube[(r["metric"], r["month"])] = (r["v2"], r["v3"])
    out, bad = [], 0
    for metric, (p2, p3) in FAMILY.items():
        for m in MONTHS:
            y, mo = map(int, m.split("-"))
            lo, hi = f"{m}-01", f"{m}-{calendar.monthrange(y, mo)[1]:02d}"
            sql = f"""
            WITH v2 AS (SELECT DISTINCT c.order_id AS order_id FROM serve.commerce_orders AS c
                        WHERE c.brand_id = {a.brand} AND c.order_date BETWEEN '{lo}' AND '{hi}'
                          AND c.is_commerce_placement_order = 1 AND {p2}),
                 v3 AS (SELECT DISTINCT o.order_id AS order_id FROM serve.fct_order_commerce AS o
                        WHERE o.brand_id = {a.brand} AND o.order_date BETWEEN '{lo}' AND '{hi}'
                          AND o.is_sale_order = 1 AND {p3})
            SELECT 'v2_total' AS k, '' AS t, count() AS n FROM v2
            UNION ALL SELECT 'v3_total', '', count() FROM v3
            UNION ALL SELECT 'removed', ifNull(f.order_type, 'not_in_v3_at_all'), count()
                      FROM v2 LEFT JOIN serve.fct_order AS f ON f.brand_id = {a.brand} AND f.order_id = v2.order_id
                      WHERE v2.order_id NOT IN (SELECT order_id FROM v3) GROUP BY 2
            UNION ALL SELECT 'added', multiIf(f.is_draft_order = 1, 'draft', f.is_revenue_adjustment = 1, 'revenue_adjustment', 'other_not_in_v2_flag'), count()
                      FROM v3 JOIN serve.fct_order AS f ON f.brand_id = {a.brand} AND f.order_id = v3.order_id
                      WHERE v3.order_id NOT IN (SELECT order_id FROM v2) GROUP BY 2
            """
            rows = V.rows(sql)
            v2n = next(r["n"] for r in rows if r["k"] == "v2_total")
            v3n = next(r["n"] for r in rows if r["k"] == "v3_total")
            removed = {r["t"]: r["n"] for r in rows if r["k"] == "removed"}
            added = {r["t"]: r["n"] for r in rows if r["k"] == "added"}
            rest = v3n - (v2n - sum(removed.values()) + sum(added.values()))
            cv2, cv3 = cube.get((metric, m), (None, None))
            drift = cv2 is not None and (round(cv2) != v2n or round(cv3) != v3n)
            ok = rest == 0
            bad += not ok
            out.append({"metric": metric, "month": m, "v2": v2n, "v3": v3n, "removed_by_type": removed,
                        "added_by_flag": added, "unexplained": rest, "cube_v2": cv2, "cube_v3": cv3,
                        "snapshot_drift": bool(drift)})
            lab = {k: f"{v} ({DECISION.get(k, k)})" for k, v in removed.items()}
            print(("PASS " if ok else "FAIL ") + f"{metric:15s} {m} v2 {v2n} → v3 {v3n}: removed {lab}; added {added}; "
                  f"unexplained {rest}" + ("; snapshot drift vs Cube" if drift else ""))
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(out, indent=1))
    return 1 if bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
