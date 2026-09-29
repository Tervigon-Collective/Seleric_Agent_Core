#!/usr/bin/env python3
"""Static catalogue audit for the seleric-mcp catalogue.

Loads the real catalogue via the shipped loader, cross-checks against the live
Cube /v1/meta, and reports the defect classes a real operator would hit:
  A. integrity  - does the catalogue even load (loader._check_integrity)
  B. broken     - metrics whose cube members are gone from live /meta
  C. overlap    - metrics that collide semantically (same measure / near-dup desc)
  D. coverage   - cube measures/dims with no catalogue metric/dimension (dead surface)
  E. grain/axis - views with no resolved date axis; metrics stranded on them
  F. drilldown  - join-key reachability matrix for causal chains
"""
from __future__ import annotations
import json, os, re, sys, urllib.request
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path

BASE = Path(r"c:/SpacePeppers/SpacePeppers/Base_Agent")
sys.path.insert(0, str(BASE / "src"))
CAT_DIR = BASE / "catalogue"
CUBE_API = os.environ.get("SELERIC_CUBE_API", "http://127.0.0.1:4001")

def cube_meta() -> dict:
    try:
        with urllib.request.urlopen(f"{CUBE_API}/cubejs-api/v1/meta", timeout=30) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        print(f"!! cube /meta unreachable: {e}", file=sys.stderr)
        return {}

def norm(s: str) -> str:
    s = (s or "").lower()
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    return " ".join(s.split())

def main():
    from seleric_mcp.catalogue_service.loader import load_catalogue
    out = {"integrity_ok": True, "integrity_error": None}
    try:
        cat = load_catalogue(CAT_DIR)
    except Exception as e:
        out["integrity_ok"] = False
        out["integrity_error"] = str(e)
        print(json.dumps(out, indent=2))
        return
    print(f"catalogue loaded: version={cat.version}  metrics={len(cat.metrics)}  "
          f"dimensions={len(cat.dimensions)}  views={len(cat.views)}  concepts={len(cat.concepts)}")

    meta = cube_meta()
    members = set()
    view_measures = defaultdict(set)   # view -> {measure member}
    view_dims = defaultdict(set)       # view -> {dim member}
    view_timedims = defaultdict(set)
    for c in meta.get("cubes", []):
        v = c["name"]
        for m in c.get("measures", []):
            members.add(m["name"]); view_measures[v].add(m["name"])
        for d in c.get("dimensions", []):
            members.add(d["name"]); view_dims[v].add(d["name"])
            if d.get("type") == "time":
                view_timedims[v].add(d["name"])

    # ---- B. broken metrics (validate.py logic) ----
    broken = []
    for m in cat.metrics.values():
        needed = [m.cube_mapping.measure]
        if m.cube_mapping.measure_pct: needed.append(m.cube_mapping.measure_pct)
        if m.ratio_components:
            needed += [m.ratio_components.numerator, m.ratio_components.denominator]
        for dim_id in m.supported_dimensions:
            dim = cat.dimensions.get(dim_id)
            if dim and m.cube_mapping.view in dim.views:
                needed.append(dim.views[m.cube_mapping.view])
        missing = [n for n in needed if n not in members]
        if missing:
            broken.append({"metric": m.id, "status": m.status, "view": m.cube_mapping.view, "missing": missing})

    # ---- C. semantic collisions & description overlap ----
    by_measure = defaultdict(list)
    for m in cat.metrics.values():
        by_measure[(m.cube_mapping.view, m.cube_mapping.measure)].append(m.id)
    measure_collisions = {f"{v}.{mm}": ids for (v, mm), ids in by_measure.items() if len(ids) > 1}

    metrics = list(cat.metrics.values())
    desc_overlaps = []
    for i in range(len(metrics)):
        for j in range(i+1, len(metrics)):
            a, b = metrics[i], metrics[j]
            na, nb = norm(a.description)[:400], norm(b.description)[:400]
            if not na or not nb: continue
            r = SequenceMatcher(None, na, nb).ratio()
            if r >= 0.72:
                desc_overlaps.append({"a": a.id, "b": b.id, "ratio": round(r, 3),
                                      "same_measure": a.cube_mapping.measure == b.cube_mapping.measure and a.cube_mapping.view == b.cube_mapping.view})
    desc_overlaps.sort(key=lambda x: -x["ratio"])

    # ---- D. coverage: cube measures/dims not exposed by any catalogue metric/dim ----
    catalogued_measures = {f"{m.cube_mapping.view}|{m.cube_mapping.measure}" for m in cat.metrics.values()}
    # also count measure_pct / ratio components as "covered"
    for m in cat.metrics.values():
        if m.cube_mapping.measure_pct:
            catalogued_measures.add(f"{m.cube_mapping.view}|{m.cube_mapping.measure_pct}")
    # which cube views are certified views the catalogue knows about
    cat_views = set(cat.views)
    uncovered_measures = defaultdict(list)
    for v, meas in view_measures.items():
        if v not in cat_views:
            continue  # not a catalogue-exposed view
        for mm in sorted(meas):
            if f"{v}|{mm}" not in catalogued_measures:
                uncovered_measures[v].append(mm)
    # dims present in cube view but not mapped by any catalogue dimension for that view
    dim_member_by_view = defaultdict(set)
    for d in cat.dimensions.values():
        for v, member in d.views.items():
            dim_member_by_view[v].add(member)
    uncovered_dims = defaultdict(list)
    for v, dims in view_dims.items():
        if v not in cat_views: continue
        for dm in sorted(dims):
            if dm not in dim_member_by_view.get(v, set()):
                uncovered_dims[v].append(dm)

    # ---- E. grain / axis ----
    noaxis_views = sorted(v.name for v in cat.views.values() if not v.date_dimension and not v.datetime_dimension)
    metrics_on_noaxis = defaultdict(list)
    for m in cat.metrics.values():
        if m.cube_mapping.view in noaxis_views:
            metrics_on_noaxis[m.cube_mapping.view].append(m.id)

    # ---- F2. FULL metric x dimension coverage matrix ----
    # For every metric, which dimensions on ITS cube view are askable (exist in
    # Cube) but NOT exposed via supported_dimensions -> silently-blocked drill-downs.
    INTERNAL = re.compile(r"(^|\.)(is_final|model_version|source_basis|credit_pct|"
                          r"credit_rows|touch_id|order_id|is_eligible|is_cost_set|created_at)")
    # map cube member -> catalogue dimension id (per view) so we report business names
    member_to_dimid = defaultdict(dict)  # view -> {member: dim_id}
    for d in cat.dimensions.values():
        for v, member in d.views.items():
            member_to_dimid[v][member] = d.id
    blocked = {}          # metric -> [blocked dims/members on its own view]
    blocked_counter = defaultdict(int)
    for m in cat.metrics.values():
        v = m.cube_mapping.view
        cube_dims_here = view_dims.get(v, set())
        mapped_members = set()
        for dim_id in m.supported_dimensions:
            dd = cat.dimensions.get(dim_id)
            if dd and v in dd.views:
                mapped_members.add(dd.views[v])
        blk = []
        for member in sorted(cube_dims_here):
            if member in mapped_members: continue
            if INTERNAL.search(member): continue
            label = member_to_dimid[v].get(member, member.split(".")[-1] + " (uncatalogued dim)")
            blk.append(label)
            blocked_counter[label] += 1
        if blk:
            blocked[m.id] = {"view": v, "blocked": blk}
    # metrics with the widest blocked drill-down
    widest = sorted(blocked.items(), key=lambda kv: -len(kv[1]["blocked"]))[:25]

    # ---- F. drill-down join-key reachability ----
    # dimension id -> views that expose it (from catalogue mapping)
    dim_to_views = {d.id: sorted(d.views) for d in cat.dimensions.values()}
    # causal-chain join keys the user cares about
    chain_keys = ["brand_id", "channel", "campaign_id", "campaign_name", "adset_id", "ad_id",
                  "product_id", "sku", "variant_id", "variant_title", "report_date", "order_date",
                  "customer_id", "session_id", "landing_page", "traffic_source"]
    keymap = {k: dim_to_views.get(k, []) for k in chain_keys}
    # which dims exist at all
    all_dims = sorted(cat.dimensions)

    result = {
        "summary": {
            "metrics": len(cat.metrics), "dimensions": len(cat.dimensions),
            "views": len(cat.views), "concepts": len(cat.concepts),
            "cube_views_live": len(view_measures),
            "broken": len(broken), "measure_collisions": len(measure_collisions),
            "desc_overlaps>=0.72": len(desc_overlaps),
            "views_uncovered_measures": len(uncovered_measures),
            "views_uncovered_dims": len(uncovered_dims),
            "views_no_date_axis": len(noaxis_views),
            "metrics_with_blocked_drilldowns": len(blocked),
            "total_blocked_metric_dim_combos": sum(len(b["blocked"]) for b in blocked.values()),
        },
        "blocked_drilldowns_widest": [{"metric": k, **v} for k, v in widest],
        "blocked_dim_frequency": dict(sorted(blocked_counter.items(), key=lambda kv: -kv[1])),
        "blocked_drilldowns_all": blocked,
        "broken": broken,
        "measure_collisions": measure_collisions,
        "desc_overlaps": desc_overlaps[:40],
        "uncovered_measures": {k: v for k, v in sorted(uncovered_measures.items())},
        "uncovered_dims": {k: v for k, v in sorted(uncovered_dims.items())},
        "views_no_date_axis": noaxis_views,
        "metrics_on_noaxis_views": dict(metrics_on_noaxis),
        "chain_keymap": keymap,
        "all_dimensions": all_dims,
    }
    outpath = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("audit_result.json")
    outpath.write_text(json.dumps(result, indent=2))
    print(json.dumps(result["summary"], indent=2))
    print(f"\nfull result -> {outpath}")

if __name__ == "__main__":
    main()
