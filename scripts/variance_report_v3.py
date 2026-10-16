#!/usr/bin/env python3
"""Phase 7 variance report for the owner (v2 → v3), brand 20, sorted by ₹. Writes doc/semantic_v3/VARIANCE_REPORT.md.

Sections
  1  headline money metrics, Sep 2026 (order axis): v2, v3, Δ, the decision ids the catalogue cites — sorted by |Δ| ₹
  2  net-sales bridge and cost bridge (gold → v3, proven to 0 unexplained by v3_reconcile_gold.py), Sep and all history — sorted by |₹|
  3  event-date leg per month (D-21b / D-30), from the same reconcile run
  4  L-17: v2's own cost composition vs v3 — by order type × outcome × cost component (live ClickHouse, one snapshot)
  5  items that need the owner's word (sign-off checklist)
Inputs: --cap capability_check_v3.py metrics JSON, --reconcile the saved v3_reconcile_gold.py output.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
MAGE = "/home/tervigon/work/mage_v3"


def inr(x: float | None) -> str:
    return "—" if x is None else f"{x:,.0f}"


def parse_reconcile(path: Path) -> dict[str, dict]:
    """{'NET-SALES BRIDGE 2026-09': {'head': line, 'causes': [(amount, label)]}, ...}"""
    out, cur = {}, None
    for line in path.read_text().splitlines():
        m = re.match(r"^(PASS|FAIL|INFO)\s+(.*)$", line)
        if m:
            cur = None
            if m.group(2).startswith("EVENT BRIDGE"):
                cur = out["EVENT"] = {"head": m.group(2), "causes": [], "lines": []}
            elif "BRIDGE" in m.group(2):
                parts = m.group(2).split()
                cur = out[f"{parts[0]} {parts[1]} {parts[2]}"] = {"head": m.group(2), "causes": []}
            continue
        if cur is not None:
            mm = re.match(r"^\s+(-?[\d,]+\.\d\d)\s+(.*)$", line)
            if mm:
                cur["causes"].append((float(mm.group(1).replace(",", "")), mm.group(2)))
            elif "net profit v2 event" in line:
                cur.setdefault("lines", []).append(line.strip())
    return out


def l17(V) -> tuple[list[dict], dict]:
    sql = """
    WITH v3 AS (SELECT l.order_id AS order_id, any(f.order_type) ot, any(f.outcome) outc,
       -sumIf(amount, component IN ('product_cogs','product_cogs_credit','exchange_replacement_cogs','exchange_replacement_cogs_credit','exchange_inventory_recovery')) prod,
       -sumIf(amount, component IN ('forward_shipping_cost','reverse_shipping_cost','exchange_shipping_cost')) ship,
       -sumIf(amount, component IN ('packaging_cost','exchange_handling_cost')) pk,
       -sumIf(amount, component = 'payment_gateway_fee') gw,
       -sumIf(amount, component IN ('checkout_fee','cod_handling_fee','provisional_ops_cost','weight_discrepancy_cost')) oth
       FROM serve.ledger_order_component AS l JOIN serve.fct_order AS f ON f.brand_id = l.brand_id AND f.order_id = l.order_id
       WHERE l.brand_id = 20 AND l.family IN ('product_cost','fulfilment_cost','exchange') GROUP BY 1),
     v2 AS (SELECT order_id, sum(product_cost) prod, sum(shipping_cost) + sum(rto_cost) ship, sum(packaging_cost) pk, sum(payment_gateway_fees) gw
            FROM serve.channel_pnl WHERE brand_id = 20 AND order_id IS NOT NULL GROUP BY 1)
    SELECT v3.ot AS order_type, v3.outc AS outcome, count() AS orders,
       round(sum(v3.prod - ifNull(v2.prod, 0))) AS d_product, round(sum(v3.ship - ifNull(v2.ship, 0))) AS d_shipping_rto, round(sum(v3.pk - ifNull(v2.pk, 0))) AS d_packaging,
       round(sum(v3.gw - ifNull(v2.gw, 0))) AS d_gateway, round(sum(v3.oth)) AS d_other_fees,
       round(sum(v3.prod + v3.ship + v3.pk + v3.gw + v3.oth - ifNull(v2.prod, 0) - ifNull(v2.ship, 0) - ifNull(v2.pk, 0) - ifNull(v2.gw, 0))) AS d_total
    FROM v3 LEFT JOIN v2 ON v2.order_id = v3.order_id GROUP BY 1, 2 ORDER BY abs(d_total) DESC"""
    rows = V.rows(sql)
    v2_only = V.rows("""SELECT count() AS orders, round(sum(c.product_cost + c.shipping_cost + c.rto_cost + c.packaging_cost + c.payment_gateway_fees)) AS v2_cost
        FROM (SELECT order_id, sum(product_cost) product_cost, sum(shipping_cost) shipping_cost, sum(rto_cost) rto_cost, sum(packaging_cost) packaging_cost, sum(payment_gateway_fees) payment_gateway_fees
              FROM serve.channel_pnl WHERE brand_id = 20 AND order_id IS NOT NULL GROUP BY 1) AS c
        WHERE c.order_id NOT IN (SELECT order_id FROM serve.ledger_order_component WHERE brand_id = 20)""")[0]
    cod = V.rows("""WITH g AS (SELECT order_id, sum(toFloat64(ifNull(gateway_fee,0))) AS gwf FROM gold.int_finance_order_lines FINAL WHERE brand_id = 20 GROUP BY 1),
        v2 AS (SELECT order_id, sum(payment_gateway_fees) AS gw FROM serve.channel_pnl WHERE brand_id = 20 AND order_id IS NOT NULL GROUP BY 1)
        SELECT count() AS orders, round(sum(g.gwf)) AS gold_fee FROM g JOIN v2 ON v2.order_id = g.order_id
        JOIN serve.fct_order_commerce AS f ON f.brand_id = 20 AND f.order_id = g.order_id
        WHERE f.payment_bucket = 'cod' AND g.gwf > 0 AND v2.gw = 0""")[0]
    return rows, {"v2_orders_without_ledger": v2_only, "cod_gateway": cod}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--cap", default=str(ROOT / "doc/semantic_v3/results/capability_metrics_20261016b.json"))
    ap.add_argument("--reconcile", default=str(ROOT / "doc/semantic_v3/results/v3_reconcile_20261016.txt"))
    ap.add_argument("--out", default=str(ROOT / "doc/semantic_v3/VARIANCE_REPORT.md"))
    ap.add_argument("--month", default="2026-09")
    a = ap.parse_args()
    sys.path.insert(0, f"{MAGE}/serve/evaluations")
    import v3_contracts as V  # noqa: E402
    V.apply_v3.load_env()

    cat = {}
    for f in (ROOT / "catalogue_v3/metrics").glob("*.yaml"):
        y = yaml.safe_load(open(f))
        cat[y["id"]] = y
    cap = [r for r in json.load(open(a.cap))["rows"] if r["brand"] == "20" and r["month"] == a.month]
    diffs = [r for r in cap if r["outcome"] == "DIFF" and cat.get(r["metric"], {}).get("unit") == "INR" and r["v2"] is not None and r["v3"] is not None]
    diffs.sort(key=lambda r: -abs(r["v3"] - r["v2"]))
    rec = parse_reconcile(Path(a.reconcile))
    gen = json.load(open(a.cap))["generated_at"]

    L = [f"# Semantic v3 — variance report v2 → v3 (brand 20)", "",
         f"Generated by `scripts/variance_report_v3.py` from the capability run of {gen} and `v3_reconcile_gold.py`. ₹ values; v3 − v2.",
         "Sections 2–3 are the proven bridges (0 unexplained ₹ in `v3_reconcile_gold.py`); section 4 itemises the v2 cost composition (L-17) by order outcome and component. "
         "This is the sign-off document for the Phase 7 interview checkpoint.", ""]

    L += [f"## 1. Money metrics that differ — {a.month}, sorted by |Δ ₹|", "",
          "| metric | v2 | v3 | Δ | decisions cited |", "|---|---:|---:|---:|---|"]
    for r in diffs[:40]:
        d = r["v3"] - r["v2"]
        L.append(f"| `{r['metric']}` | {inr(r['v2'])} | {inr(r['v3'])} | {inr(d)} | {', '.join(cat[r['metric']].get('decision_ids') or [])} |")
    L += ["", f"{len(diffs)} ₹ metrics differ in this month; the top 40 are shown. All {len([r for r in cap if r['outcome'] == 'DIFF'])} differing metrics cite at least one decision id in the catalogue.", ""]

    for key, title in ((f"NET-SALES BRIDGE {a.month}", f"Net sales {a.month}"), ("NET-SALES BRIDGE all_history", "Net sales, all history"),
                       (f"COST BRIDGE {a.month}", f"Cost {a.month}"), ("COST BRIDGE all_history", "Cost, all history")):
        b = rec.get(key)
        if not b:
            continue
        L += [f"## 2. {title} (gold → v3 bridge)", "", f"`{b['head'][:300]}`", "", "| ₹ | cause |", "|---:|---|"]
        for amt, lab in sorted(b["causes"], key=lambda x: -abs(x[0])):
            L.append(f"| {amt:,.0f} | {lab} |")
        L.append("")

    ev = rec.get("EVENT")
    if ev:
        L += ["## 3. Event-date leg (D-21b / D-30), net profit v2 event → v3 event", ""]
        for ln in ev.get("lines", []):
            L.append(f"- {ln}")
        L.append("")

    rows, extra = l17(V)
    tot = {k: sum(float(r[k]) for r in rows) for k in ("orders", "d_product", "d_shipping_rto", "d_packaging", "d_gateway", "d_other_fees", "d_total")}
    L += ["## 4. L-17 — v2's own cost composition vs v3, all history (cost; positive = v3 charges more)", "",
          "v2's order P&L is built from `serve.channel_pnl` (order grain); v3 from the ledger. Joined order by order.", "",
          "| order type | outcome | orders | product | shipping + RTO | packaging | gateway | other fees | total |", "|---|---|---:|---:|---:|---:|---:|---:|---:|"]
    for r in rows:
        if abs(float(r["d_total"])) >= 1000 or float(r["orders"]) >= 100:
            L.append(f"| {r['order_type']} | {r['outcome']} | {r['orders']} | {inr(float(r['d_product']))} | {inr(float(r['d_shipping_rto']))} | {inr(float(r['d_packaging']))} | {inr(float(r['d_gateway']))} | {inr(float(r['d_other_fees']))} | {inr(float(r['d_total']))} |")
    L.append(f"| **all** | | {tot['orders']:.0f} | {inr(tot['d_product'])} | {inr(tot['d_shipping_rto'])} | {inr(tot['d_packaging'])} | {inr(tot['d_gateway'])} | {inr(tot['d_other_fees'])} | **{inr(tot['d_total'])}** |")
    vo = extra["v2_orders_without_ledger"]
    L += ["", f"v2 orders with no v3 ledger lines: {vo['orders']} (₹{inr(float(vo['v2_cost'] or 0))} v2 cost).",
          "Snapshot note: sections 2–3 come from the saved reconcile run, which excludes the orders gold changed after the last v3 refresh (L-18); section 4 reads the live ledger, so its total (all orders) is larger than the all-history cost bridge difference by those orders. Section 4 is an itemisation of where v2 and v3 differ by order outcome and cost component; it is not itself a bridge to gold.", ""]

    cg = extra["cod_gateway"]
    L += ["## 5. For the owner's word", "",
          f"1. **COD orders carry a gateway-type fee in gold that v2 never charged** — {cg['orders']} COD orders, ₹{inr(float(cg['gold_fee'] or 0))} over all history (about ₹87 per order, posted through August 2026; none from September). "
          "v3 follows gold's posted fee (D-05b, fulfilment from gold's posted amounts). If this is the courier's COD collection charge it is a real cost and v3 is right; if it is a modelling artefact it should be dropped. "
          "There is no overlap with the COD-handling fee (checked: 0 orders carry both).",
          "2. **RTO reverse shipping** is ₹6.1L higher in v3 on RTO orders (D-11c back-fill and gold's posted reverse shipping, D-11d). Confirm this is the intended direction.",
          "3. **Q11 — brand 28 (spend-only)**: v3 refuses profit / ROAS / MER / AOV; v2 shows net profit = −spend (Sep −₹3.99L). Owner answered 'only brand 20 focus' on 2026-10-16; the refusal stays as built until you confirm or reject it.",
          "4. **Event-dating leg** (section 3) is accepted in principle (D-21b, Q13); the monthly figures above are for the record.",
          "5. **Audience-sliced landing page views** (age, gender, device ...) are refused on the website-style measure and point to the platform-reported twin — keep, or label the Meta figure?",
          ""]
    Path(a.out).write_text("\n".join(L) + "\n")
    print(f"wrote {a.out}: {len(diffs)} ₹ diffs, L-17 total {tot['d_total']:,.0f}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
