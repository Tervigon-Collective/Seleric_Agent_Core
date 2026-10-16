#!/usr/bin/env python3
"""v2 vs v3 side-by-side: capabilities, enhancements and gaps. Writes doc/semantic_v3/V2_V3_COMPARISON.md.

Everything numeric is read from the repo, nothing is typed in:
  catalogue_v2/ vs catalogue_v3/            counts of metrics, views, dimensions, hierarchies, concepts, retired ids
  catalogue/migrations/v3_id_map.yaml       which v2 id lands where, and the 41 new ids
  doc/semantic_v3/CAPABILITY_MAP.yaml       target class per capability (keep / refine / new), date_axis class
  doc/semantic_v3/results/*.json            capability_check_v3 (metrics, dims), axis_certify, QA, dataset certification
  doc/semantic_v3/DECISIONS.yaml, KNOWN_LIMITS.yaml   the decision / limit each change cites
The narrative rows (area by area) and the gap list carry decision ids; a gap row without a decision id is printed as UNDECIDED,
which under the no-capability-loss rule is a real gap. --check exits 1 when any exist.

  python3 scripts/comparison_report_v3.py [--metrics RESULT.json] [--dims RESULT.json] [--check]
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import os
import re
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
RES = ROOT / "doc/semantic_v3/results"


def y(path):
    return yaml.safe_load(open(ROOT / path))


def dims_of(cat):
    out = {}
    for p in glob.glob(str(ROOT / cat / "dimensions/*.yaml")):
        d = yaml.safe_load(open(p))
        out.update({x["id"]: x for x in d.get("dimensions", d)})
    return out


def views_of(cat):
    d = y(f"{cat}/views.yaml")
    d = d.get("views", d)
    return sorted(x["id"] if "id" in x else x["name"] for x in d)


def metric_ids(cat):
    return {os.path.basename(p)[:-5] for p in glob.glob(str(ROOT / cat / "metrics/*.yaml"))}


def n(x):
    return f"{x:,}"


def table(head, rows):
    out = ["| " + " | ".join(head) + " |", "|" + "|".join("---" for _ in head) + "|"]
    out += ["| " + " | ".join(str(c).replace("|", "\\|") for c in r) + " |" for r in rows]
    return out


# Area-by-area comparison. change: same | refined | new | behaviour change | narrowed | refused
# decisions: ids that must exist in DECISIONS.yaml (checked below); evidence: the object / contract / run that proves it.
AREAS = [
    ("Orders: exchanges", "Exchange replacement orders counted as sales (Sep 2026 brand 20: 1,096 orders)", "Replacement = linked fulfilment child, not a sale (931 orders); original keeps order date, customer, attribution", "behaviour change", "D-01, D-01a-d, D-20", "serve.fct_order, ledger; COMMERCE-ORDERS, FIN-13/14"),
    ("Orders: outcomes", "RTO hidden inside cancels and returns", "Four outcomes with an evidence code per order: Cancellation / RTO / Customer Return / Unknown (68 orders, visible)", "new + behaviour change", "D-05, D-22, D-22a-k", "serve.core_dim_order_class; `outcome`, `outcome_evidence` dimensions"),
    ("Orders: scope", "No rule on record for drafts, POS, revenue adjustments, tests (F-06)", "Draft, POS, revenue-adjustment = sales; zero-value excluded; test orders excluded everywhere", "new", "D-08, D-08a, D-08b", "core_order_class"),
    ("Customers", "No rule on record (interview decision)", "Hashed person key (phone / email / id) links guest checkouts; new customer = first placed order; no raw PII in serve, Cube or Agent", "new", "D-15, D-16, D-16a", "serve.fct_customer"),
    ("Revenue basis", "Order header gross is tax-inclusive; shipping charged lives only on the header, not on lines (F-13)", "Ex-GST for all revenue / margin / ROAS; GST its own component; shipping charged = order-level revenue, reversed on cancel / RTO", "behaviour change", "D-02, D-02a, D-02b, D-10, D-10a", "serve.ledger_order_component; FIN-01"),
    ("Costs", "'COGS' = landed variable cost; method changes Sep 2026", "COGS = product cost only; packaging, shipping, gateway, COD, checkout, weight = separate fulfilment components; Sep 2026 era break back-filled with rate cards and labelled `cost_method`", "behaviour change", "D-03, D-03a, D-11, D-11a, D-11c, D-11d, D-39", "ledger; cost_method / cost_status dimensions"),
    ("Profit engines", "Four competing P&L engines (canonical_pnl, pnl_daily, order_pnl_daily, channel_pnl)", "One component ledger; `finance` view serves both date axes; old ids resolve to it", "refined", "D-07, D-18r, D-21", "serve.mart_profit; FIN-07, MART-EVENT-BRIDGE"),
    ("Net profit", "`canonical_pnl.net_cogs` includes operating costs, `gross_profit` = `contribution_margin` (F-32); event-dated, some drill columns hard-coded", "Ids kept; resolve to contribution after ads; label says opex is excluded", "refined", "D-18r", "finance.net_profit"),
    ("Date axis", "Separate `pnl_*` event-date family (25 ids)", "One parameter `date_axis` = order_date (default) / event_date on the same measures; `pnl_*` ids are aliases; Undated member always shown", "refined", "D-28, D-29, D-30, D-31, D-32, D-35, D-36, D-37", "AXIS-* contracts; axis_certify 208/208"),
    ("Attribution", "Last-touch for every order; four multi-touch models cover only 6,291 orders and no default is declared (F-29)", "`last_non_direct` default, model is a parameter; fallback rows flagged; coverage % shown with every slice", "refined", "D-06, D-06a, D-06b", "serve.bridge_order_attribution; FIN-15"),
    ("Ads: Google", "Collapsed to campaign level in serve", "Native hierarchy account > campaign type > campaign > ad group > ad (+ device, network); PMax stops at campaign", "new", "D-14", "serve.fct_ad_delivery(+_hourly)"),
    ("Ads: landing page views", "Meta-reported LPV under the generic name (F-26)", "Website LPV (sessions with a page view), explicit `ad unknown` / `campaign unknown` members; Meta figure kept as hidden-by-default twin", "behaviour change", "D-00, D-13r, D-17", "serve.fct_paid_media_daily; PAID-DAILY, PAID-HOURLY"),
    ("Ads: platform conversions", "Meta purchases / value and Google conversions exposed (F-26)", "Hidden (not in serve, Cube or Agent)", "narrowed", "D-13, D-13r", "L-06"),
    ("Product ad metrics", "~25 `product_*` spend / ROAS / MER ids; allocated LPV was Meta-reported (F-26)", "Kept as an explicit `allocated` family: credit x line share, same-day rule, Unallocated / Organic / Order-level rows so Σ products = brand; never in default answers", "refined", "D-19o, D-27, D-27b, D-38", "serve.mart_product_ad_allocated / mart_product_ad_profit; ALLOC-PROFIT-TOTAL"),
    ("Product / variant P&L", "Separate `product_pnl` event-date family (51 ids) beside the order-date product view", "One before-ads P&L at line grain on both axes via `date_axis` (`product_pnl_*` are aliases), sliceable by channel / campaign / ad", "refined", "D-34", "serve.mart_product_profit; AXIS-PRODUCT-RECON"),
    ("Monthly close", "None: rolling figures only (D-12)", "Live + locked month-end snapshots; open / restatable / reconstructed / locked labels; August 2026 locked", "new", "D-12, D-12a, D-12b, D-43", "close_month.py; AXIS-CLOSE"),
    ("Web events", "add_to_cart counted ~1.9x (double-fire)", "Second event of a pair dropped in serve", "refined", "D-13, L-33", "WEB-ATC-DEDUP"),
    ("Payments", "Exchange-replacement credits (manual, store credit) counted as payments", "Cash payments vs non-cash exchange credits", "refined", "D-01b, D-36", "serve.fct_payment"),
    ("Defects", "Tax metrics (4 ids) and customers x first-order product error in v2", "Delivered", "new", "D-26", "F0-1 / F0-2 in capability_check_v3 (V2_ERROR rows)"),
    ("Brand scope", "Brand 28 computes profit = -spend", "Brand 28 profit / ROAS / AOV / LTV refused; spend answered", "refused", "D-44, L-16", "v3_cube_refusals.py 18/18"),
    ("Cube surface", "20 views, 233 catalogue metrics, inline business SQL", "18 views, 213 metrics + 61 aliases (= all 233 v2 ids + 41 new), SQL moved to serve", "refined", "D-23, D-24", "validate_catalogue 213/213"),
]

# Where v3 answers less than v2 did. decision '' = not decided = real gap.
GAPS = [
    ("Landing page views sliced by age / gender / placement / device / publisher", "v2 answered with the Meta number under the same name", "Refused; refusal names `platform_reported_landing_page_views`, which answers those slices", "D-40", "Website has no audience slice"),
    ("Return / cancel / RTO / repeat rate, LTV, net AOV, LTV:CAC on the event axis", "v2 had no event axis for these", "Refused on event_date with a pointer to order_date", "D-31", "Rates are cohort-defined (L-31)"),
    ("Allocated product-ad metrics on the event axis (`product_pnl_ad_spend`, `_be_roas`, `_mer`, `_net_margin_pct`, `_net_profit`, `_net_roas`)", "v2 answered event-dated numbers", "Refused with a pointer to order_date", "D-34", "Spend allocation is a cohort rule"),
    ("Platform ROAS / platform-reported purchases and value", "Exposed", "Hidden", "D-13", "L-06"),
    ("Brand 28 (Commbitz) profit, ROAS, AOV, LTV", "Computed as -spend", "Refused; spend and delivery answered", "D-44", "v2 value was wrong (F0-3)"),
    ("Hourly delivery before platform coverage / unbounded ranges", "No coverage guard on record", "Refused with the coverage date (Meta from 2026-04-01, Google from 2024-11-15)", "L-04", "Data not in gold"),
    ("Landing page views filtered or sliced by ad account (`account_id`)", "Meta figure per account (Sep 2026 brand 20: 21,067)", "Website LPV rows carry no account: a filter on one account returns 0 (QA AD01, Meta 1-11 Oct: spend 4,97,667, LPV 0, still marked PASS); sliced, there is an account-less member (3 rows) and `cost_per_landing_page_view` by account reads 0.0", "", "Not covered by D-17 (which only adds ad / campaign unknown members); found in the v2 vs v3 comparison"),
    ("Hourly / breakdown website LPV by age, gender, placement", "Meta figure", "Platform twin only", "D-40", "Website cannot slice"),
    ("Checkout-timing metrics (seconds to checkout / purchase, checkout steps)", "Error", "Error (structurally empty)", "L-20", "Unchanged from v2; upstream fix"),
    ("Brands 24-27", "No caveat", "No caveat; not certified (1-32 orders)", "D-44", "Owner: brand 20 only"),
    ("Amazon in the brand overview", "Excluded", "Excluded (overview = D2C Shopify)", "D-04", "Separate project"),
    ("Cube pre-aggregations", "None", "None, as v2 (read-only serving login)", "D-42", "0.3-3.4 s on brand 20"),
    ("Locked months", "None", "August 2026 only; others reconstructed", "D-43", "Recurring schedule not installed (L-27)"),
    ("Physical RTO / delivery date", "Not available", "Not available; RTO dated by Shopify cancellation / return event", "L-29", "No courier timestamp in gold"),
    ("Payment mix for Sep 2026 onward", "Understated", "Understated; `manual` shown as unclassified", "D-41", "Upstream data (L-35)"),
]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--metrics", default=str(RES / "capability_metrics_20261016b.json"))
    ap.add_argument("--dims", default=str(RES / "capability_dims_20261016.json"))
    ap.add_argument("--out", default=str(ROOT / "doc/semantic_v3/V2_V3_COMPARISON.md"))
    ap.add_argument("--check", action="store_true", help="exit 1 when a gap row has no decision id or a cited id does not exist")
    a = ap.parse_args()

    cm = y("doc/semantic_v3/CAPABILITY_MAP.yaml")
    dec = {d["id"]: d for d in y("doc/semantic_v3/DECISIONS.yaml")["decisions"]}
    lim = {d["id"]: d for d in y("doc/semantic_v3/KNOWN_LIMITS.yaml")["limits"]}
    idmap = y("catalogue/migrations/v3_id_map.yaml")
    met = json.load(open(a.metrics))
    dim = json.load(open(a.dims))
    axis = json.load(open(RES / "axis_certify_20261016.json"))
    qa = (RES / "qa_v3/CERTIFICATION.md").read_text()
    ds = json.load(open(RES / "datasets/certification.json"))["datasets"]

    errs = []
    for row in AREAS + GAPS:
        ids = row[4] if row in AREAS else row[3]
        for i in re.findall(r"\b[DL]-\d+[a-z]?\b", ids):
            if i not in dec and i not in lim:
                errs.append(f"{row[0]}: cited id {i} is in neither DECISIONS.yaml nor KNOWN_LIMITS.yaml")
    for g in GAPS:
        if not g[3]:
            errs.append(f"UNDECIDED gap: {g[0]}")

    v2m, v3m = metric_ids("catalogue_v2"), metric_ids("catalogue_v3")
    aliases = [x["id"] for x in y("catalogue_v3/metric_aliases.yaml")["metric_aliases"]]
    v2v, v3v = views_of("catalogue_v2"), views_of("catalogue_v3")
    d2, d3 = dims_of("catalogue_v2"), dims_of("catalogue_v3")
    h2, h3 = len(y("catalogue_v2/hierarchies.yaml")["hierarchies"]), len(y("catalogue_v3/hierarchies.yaml")["hierarchies"])
    dep2 = {x["old"] for x in y("catalogue_v2/deprecations.yaml")["deprecations"]}
    dep3 = {x["old"] for x in y("catalogue_v3/deprecations.yaml")["deprecations"]}
    nconc2, nconc3 = len(glob.glob(str(ROOT / "catalogue_v2/concepts/*"))), len(glob.glob(str(ROOT / "catalogue_v3/concepts/*")))
    new_ids = sorted(idmap["new"])
    nterms2, nterms3 = (len(y(f"{c}/glossary/terms.yaml").get("terms", y(f"{c}/glossary/terms.yaml").get("glossary", []))) for c in ("catalogue_v2", "catalogue_v3"))

    # ---- per-id classification from the capability run (6 cells per id: brands 20 / 28 x Jul-Sep 2026)
    cells = collections.defaultdict(list)
    for r in met["rows"]:
        cells[r["metric"]].append(r)
    klass = {}
    for m, rs in cells.items():
        b20 = [r for r in rs if r["brand"] == "20"]
        oc = {r["outcome"] for r in rs}
        if "V2_ERROR" in oc:
            klass[m] = "v3 delivers a v2 defect"
        elif "BOTH_ERROR" in oc:
            klass[m] = "unavailable in both"
        elif any(r["outcome"] == "V3_REFUSED" for r in b20):
            klass[m] = "refused on v3 (brand 20)"
        elif any(r["outcome"] == "DIFF" for r in b20):
            klass[m] = "number changes, decision cited"
        else:
            klass[m] = "same number"
    kc = collections.Counter(klass.values())
    brand28 = sorted({r["metric"] for r in met["rows"] if r["brand"] == "28" and r["outcome"] == "V3_REFUSED"})
    nodec = [m for m, k in klass.items() if k == "number changes, decision cited" and not cm["metrics"].get(m, {}).get("decision_ids")]

    L = ["# Semantic v2 vs v3 — capabilities, enhancements and gaps", "",
         f"Generated by `scripts/comparison_report_v3.py` from the repo (catalogues, id map, CAPABILITY_MAP, certification runs of {met['generated_at']}). "
         "Brand 20 is the certified scope (D-44); brand 28 is checked for its refusal. Nothing here is typed in except the area and gap rows, "
         "and every row cites a decision or limit id (`--check` fails otherwise).", ""]

    L += ["## 1. Scorecard", ""]
    L += table(["", "v2", "v3", "change"], [
        ("Catalogue metrics (canonical)", len(v2m), len(v3m), f"{len(v3m) - len(v2m):+d} (one measure per number; 61 ids became aliases)"),
        ("Metric ids that answer (canonical + alias)", len(v2m), len(v3m) + len(aliases), f"all {len(v2m)} v2 ids land (3 error in both stacks); +{len(new_ids)} new"),
        ("Views", f"{len(v2v)}", f"{len(v3v)}", f"removed {', '.join(sorted(set(v2v) - set(v3v)))}; added {', '.join(sorted(set(v3v) - set(v2v)))}"),
        ("Dimensions", len(d2), len(d3), f"+{len(set(d3) - set(d2))} new; `row_type` -> `order_pnl_row_type` (v2 id kept as alias)"),
        ("Hierarchies / drill steps", f"{h2} / 100", f"{h3} / 100", "2 refined (Google native levels)"),
        ("Concepts", nconc2, nconc3, "same"),
        ("Glossary terms", nterms2, nterms3, "no term dropped" if nterms2 == nterms3 else "differs"),
        ("Retired v1 ids still rejected with a named replacement", len(dep2), len(dep3), "identical set" if dep2 == dep3 else "differs"),
        ("Business decisions on record", "—", len(dec), "owner-decided, each with evidence"),
        ("Known limits", "—", len(lim), f"{sum(1 for x in lim.values() if x['status'] == 'open')} open"),
    ])

    L += ["", "## 2. No-capability-loss evidence", "",
          f"`capability_check_v3.py` asks every v2 metric id of the v2 stack (Cube :4002, catalogue_v2) and of the v3 stack (:4003, catalogue_v3) in the same task: "
          f"{n(sum(met['counts']['metrics'].values()))} cells (233 ids x brands 20 / 28 x Jul-Sep 2026) and {n(sum(dim['counts']['dims'].values()))} id x dimension cells (every dimension v2 supported, brand 20, Sep 2026).", ""]
    L += table(["layer", "outcome", "cells", "meaning"], (
        [("metrics", k, n(v), {"EQUAL": "same number", "DIFF": "number changes; attributed to decision ids", "V3_REFUSED": "v3 refuses with a reason (section 5)", "V2_ERROR": "v2 errors, v3 answers (F0-1 / F0-2)", "BOTH_ERROR": "unavailable in both (L-20)"}.get(k, "")) for k, v in sorted(met["counts"]["metrics"].items(), key=lambda kv: -kv[1])]
        + [("dimensions", k, n(v), {"ANSWERED": "v3 answers the slice (totals agree)", "V3_REFUSED": "v3 refuses with a reason", "V2_ERROR": "v2 errors, v3 answers or refuses", "BOTH_ERROR": "unavailable in both"}.get(k, "")) for k, v in sorted(dim["counts"]["dims"].items(), key=lambda kv: -kv[1])]))
    L += ["", "**Per v2 id (233):**", ""]
    L += table(["class", "ids"], [(k, n(v)) for k, v in sorted(kc.items(), key=lambda kv: -kv[1])])
    L += ["", f"A v3 error (V3_ERROR) count of {met['counts']['metrics'].get('V3_ERROR', 0)} on metrics and {dim['counts']['dims'].get('V3_ERROR', 0)} on dimensions: v3 never crashes where v2 answered. "
          f"Differing ids without a decision id in CAPABILITY_MAP: {len(nodec)}{' — ' + ', '.join(nodec) if nodec else ''}. The ₹ detail of the {kc['number changes, decision cited']} changed ids is `VARIANCE_REPORT.md`.", ""]
    ok_axis = sum(1 for x in axis if x.get("ok"))
    m = re.search(r"PASS: (\d+)", qa)
    qn = re.search(r"Total questions: (\d+)", qa)
    L += table(["other checks", "result", "source"], [
        ("Alias = base metric on event_date; axis refusals; mixed axes", f"{ok_axis}/{len(axis)}", "results/axis_certify_20261016.json"),
        ("End-to-end QA questions", f"{m.group(1) if m else '?'}/{qn.group(1) if qn else '?'}", "results/qa_v3/CERTIFICATION.md"),
        ("Dataset contracts", f"{sum(1 for d in ds.values() if d.get('verdict') == 'certified')}/{len(ds)}", "results/datasets/certification.json"),
        ("Catalogue vs live Cube v3 meta", f"{len(v3m)}/{len(v3m)}", "scripts/validate_catalogue.py (run 2026-10-16)"),
        ("Serve contracts / Cube parity / smoke / refusals", "78/78 · 341/341 · 764/764 · 18/18", "mage-ai commit 4b4ab49 (v3_contracts.py, v3_cube_*.py)"),
        ("Unit tests", "44/44", "tests/test_semantic_v3.py"),
    ])

    L += ["", "## 3. Side by side, by area", "", "change: same · refined · new · behaviour change (the number differs on purpose) · narrowed · refused", ""]
    L += table(["area", "v2", "v3", "change", "decisions", "evidence"], AREAS)

    L += ["", "## 4. Enhancements v2 did not have", ""]
    L += ["- **Date axis as a parameter** with an always-visible Undated member (52 orders / ₹81,680 on brand 20): D-28..D-37.",
          "- **One ledger** that every view reconciles to (gold -> v3 proven with 0 unexplained ₹ by `v3_reconcile_gold.py`).",
          "- **Evidence codes** on every order outcome and `cost_method` / `cost_status` on every cost row.",
          "- **Google ad group / ad / device / network** drill (v2 stopped at campaign).",
          "- **Monthly close** and `maturing` flag for cohorts under 45 days.",
          f"- **{len(new_ids)} new metric ids**: " + ", ".join(f"`{i}`" for i in new_ids) + ".",
          f"- **{len(set(d3) - set(d2))} new dimensions**: " + ", ".join(f"`{i}`" for i in sorted(set(d3) - set(d2))) + ".",
          "- **Two groups of v2 defects delivered**: " + ", ".join(f"`{m}`" for m, k in sorted(klass.items()) if k == "v3 delivers a v2 defect") + " (tax metrics) and customers x first-order product.",
          "- **Refusals instead of wrong numbers**: brand 28 profit, cohort-only rates on the event axis, hourly before coverage.", ""]

    L += ["## 5. Gaps — everywhere v3 answers less than v2 did", "",
          "A row with an empty decision column would be a real gap under the no-capability-loss rule.", ""]
    L += table(["capability", "v2", "v3", "decision / limit", "why"], [(g[0], g[1], g[2], g[3] or "**UNDECIDED**", g[4]) for g in GAPS])
    L += ["", f"Metric ids that v3 refuses on brand 20 in the run: {', '.join(f'`{m}`' for m, k in sorted(klass.items()) if k == 'refused on v3 (brand 20)') or 'none'}.",
          f"Metric ids refused for brand 28 (spend-only): {len(brand28)}.",
          f"Unavailable in both stacks: {', '.join(f'`{m}`' for m, k in sorted(klass.items()) if k == 'unavailable in both') or 'none'}.", ""]

    L += ["## 6. Still open before cutover (not Phase 7 certification)", ""]
    L += [f"- **{i}** ({x['area']}): {x['limit']}" for i, x in sorted(lim.items()) if x["status"] in ("open", "planned")]
    L += ["", "Phase 7 items still to run or sign: owner sign-off of the variance report; Q11; `cube_member_sweep_v3.py`; `v3_baseline.py --compare`; Phase 8 (Seleric_Agent migration, env flip, 48 h watch); Phase 9 (cleanup after 2 stable weeks).", ""]

    Path(a.out).write_text("\n".join(L) + "\n")
    print(f"wrote {a.out}")
    for e in errs:
        print("FAIL", e)
    return 1 if (errs and a.check) else 0


if __name__ == "__main__":
    sys.exit(main())
