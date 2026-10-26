#!/usr/bin/env python3
"""Phase 7 date-axis certification (D-28), on the MCP planner path against Cube.

  1. every event-date alias (pnl_* / product_pnl_*) answers through its id with the same number as its base metric asked with
     date_axis=event_date (brand 20, Jul / Aug / Sep);
  2. asking an event-date alias with date_axis=order_date is refused with a reason (never a crash, never a silent switch);
  3. a cohort-only metric on event_date, the allocated family on event_date, and an event-date id with a plain switchable metric (and no explicit axis) in one
     query are refused with a reason.
Exit 1 on any failure.   .venv/bin/python scripts/axis_certify.py [--out results/axis_certify.json]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import gate_common as C  # noqa: E402


async def amain(a) -> int:
    v3 = C.Stack("v3", a.cube_url, ROOT / "catalogue")
    aliases = {k: v for k, v in v3.svc.cat.metric_aliases.items() if v.date_axis == "event_date"}
    out, bad = [], 0

    def rec(kind, mid, month, ok, detail):
        nonlocal bad
        bad += not ok
        out.append({"check": kind, "metric": mid, "month": month, "ok": ok, "detail": detail})

    sem = asyncio.Semaphore(a.workers)

    async def same(aid, al, s, e):
        async with sem:
            ra = await v3.ask(C.req(aid, s, e, "20"))
            rb = await v3.ask(C.req(al.metric, s, e, "20", axis="event_date"))
        if "rows" not in ra or "rows" not in rb:
            # refused identically on both is a consistent answer (e.g. an allocated base on event_date)
            ok = ("refused" in ra) == ("refused" in rb) and ("error" not in ra and "error" not in rb)
            rec("alias=base@event_date", aid, s[:7], ok, {"alias": ra.get("refused") or ra.get("error"), "base": rb.get("refused") or rb.get("error")})
            return
        va, vb = C.scalar(ra, aid), C.scalar(rb, al.metric)
        ok = (va is None and vb is None) or (isinstance(va, (int, float)) and isinstance(vb, (int, float)) and C.same(va, vb))
        rec("alias=base@event_date", aid, s[:7], ok, {"alias": va, "base": vb, "axis": ra.get("axis")})

    async def wrong_axis(aid, al):
        async with sem:
            r = await v3.ask(C.req(aid, "2026-09-01", "2026-09-30", "20", axis="order_date"))
        rec("alias@order_date refused", aid, "2026-09", "refused" in r and len(r["refused"]) > 20, r.get("refused") or "answered!")

    await asyncio.gather(*[same(i, al, s, e) for i, al in aliases.items() for s, e in C.MONTHS],
                         *[wrong_axis(i, al) for i, al in aliases.items()])

    # refusals
    cohort = [m for m, x in v3.svc.cat.metrics.items() if x.date_axis == "cohort_only"][:5]
    for mid in cohort:
        r = await v3.ask(C.req(mid, "2026-09-01", "2026-09-30", "20", axis="event_date"))
        rec("cohort_only@event_date refused", mid, "2026-09", "refused" in r, r.get("refused") or r.get("error") or "answered!")
    for mid in ("product_ad_spend", "product_net_profit"):
        r = await v3.ask(C.req(mid, "2026-09-01", "2026-09-30", "20", axis="event_date"))
        rec("allocated@event_date refused", mid, "2026-09", "refused" in r, r.get("refused") or r.get("error") or "answered!")
    q = C.req("pnl_net_profit", "2026-09-01", "2026-09-30", "20")
    q = q.model_copy(update={"measures": ["pnl_net_profit", "net_sales"]})
    r = await v3.ask(q)
    rec("mixed axes refused", "pnl_net_profit+net_sales", "2026-09", "refused" in r, r.get("refused") or r.get("error") or "answered!")

    n = len(out)
    fails = [x for x in out if not x["ok"]]
    print(f"{n - len(fails)}/{n} axis checks pass ({len(aliases)} event-date aliases, {len(cohort)} cohort-only metrics probed)")
    for x in fails[:20]:
        print("FAIL", x["check"], x["metric"], x["month"], x["detail"])
    if a.out:
        Path(a.out).parent.mkdir(parents=True, exist_ok=True)
        Path(a.out).write_text(json.dumps(out, indent=1, default=str))
    await v3.cube.aclose()
    return 1 if bad else 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cube-url", default="http://127.0.0.1:4003")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--out", default=str(ROOT / "doc" / "semantic" / "results" / "axis_certify.json"))
    return asyncio.run(amain(ap.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
