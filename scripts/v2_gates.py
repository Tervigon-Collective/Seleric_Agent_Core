#!/usr/bin/env python3
"""Semantic v2 gates over catalogue_v2 (doc/semantic_v2). Exit 1 when a gate fails.

  uv run python scripts/v2_gates.py [--live] [--agent /opt/seleric/Seleric_Agent] [--json out.json]

Gates
  resolution conflicts   every phrase through resolve_term / resolve_concept / search (top hit) / the
                         Seleric_Agent registry aliases — a conflict is two resolvers naming different ids
  registry drift         registry catalogue_metric not in catalogue_v2, or catalogue_filters not on its view
  hard-cut coverage      every v1 metric id is a live v2 id or listed in deprecations.yaml
  sliced bindings        every breakdown dimension of a sliced binding names its slice; (--live) every slice of
                         an additive metric equals its home total for the slice's platform (one full copy each)
  duplicate numbers      (--live) two v2 metrics returning the same number for brand 20, last 3 full months
Phrases: the inventory probe list + v1 glossary terms + v1 / v2 concept aliases + v1 and v2 registry aliases.
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import os
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))


def main() -> int:
    from build_metric_inventory import PROBE_PHRASES
    from seleric_mcp.catalogue_service.loader import load_catalogue
    from seleric_mcp.catalogue_service.service import CatalogueService

    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", default=os.environ.get("SELERIC_AGENT", "/opt/seleric/Seleric_Agent"),
                    help="Seleric_Agent checkout whose config/metric_registry.yaml is on v2 ids")
    ap.add_argument("--v1-agent", default="/opt/seleric/Seleric_Agent", help="registry whose aliases seed phrases")
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--json")
    args = ap.parse_args()

    svc = CatalogueService(load_catalogue(ROOT / "catalogue_v2"))
    reg = yaml.safe_load(Path(args.agent, "config", "metric_registry.yaml").read_text())["metrics"]
    v1_reg = yaml.safe_load(Path(args.v1_agent, "config", "metric_registry.yaml").read_text())["metrics"]
    phrases = {p.lower() for p in PROBE_PHRASES}
    phrases |= {t["term"].lower() for t in yaml.safe_load((ROOT / "catalogue" / "glossary" / "terms.yaml").read_text())["terms"]}
    for d in ("catalogue/concepts", "catalogue_v2/concepts"):
        for f in glob.glob(str(ROOT / d / "*.yaml")):
            phrases |= {a.lower() for a in yaml.safe_load(open(f)).get("aliases") or []}
    for r in (*reg, *v1_reg):
        phrases |= {str(a).lower() for a in r.get("aliases") or []}
    reg_alias = {}
    for r in reg:
        for a in r.get("aliases") or []:
            reg_alias.setdefault(str(a).lower(), r.get("catalogue_metric"))

    def mid(res) -> str:
        d = res.model_dump()
        return d.get("metric_id") or d.get("replacement") or ""

    rows = []
    for p in sorted(phrases):
        term, conc = mid(svc.resolve_term(p)), mid(svc.resolve_concept(p))
        hits = svc.search(p).matches
        top = hits[0].id if hits else ""
        agent = reg_alias.get(p, "")
        named = {x for x in (term, conc, top, agent) if x}
        rows.append({"phrase": p, "resolve_term": term, "resolve_concept": conc, "search_top1": top,
                     "agent_registry": agent,
                     "verdict": "CONFLICT" if len(named) > 1 else ("UNRESOLVED" if not named else "agree")})
    conflicts = [r for r in rows if r["verdict"] == "CONFLICT"]

    drift = []
    for r in reg:
        cm = r.get("catalogue_metric")
        if not cm:
            continue
        m = svc.cat.metrics.get(cm)
        if m is None:
            drift.append(f"{r['id']}: catalogue_metric {cm} not in catalogue_v2")
            continue
        for did in r.get("catalogue_filters") or {}:
            d = svc.cat.dimensions.get(did)
            if d is None or m.cube_mapping.view not in d.views:
                drift.append(f"{r['id']}: filter {did} not on view {m.cube_mapping.view}")

    v1_ids = {yaml.safe_load(open(f))["id"] for f in glob.glob(str(ROOT / "catalogue" / "metrics" / "*.yaml"))}
    deprecated = {d.old for d in svc.cat.deprecations}
    uncovered = sorted(i for i in v1_ids if i not in svc.cat.metrics and i not in deprecated)

    sliced = []
    for m in svc.cat.metrics.values():
        for b in m.bindings:
            if b.slice_dimension:
                sliced += [f"{m.id}/{b.name}: dimension {d} has no slice" for d in b.dimensions
                           if d != b.slice_dimension and d not in b.slices]
    dups = []
    if args.live:
        sliced += live_slices(svc)
        dups = live_duplicates(svc)

    gates = [("resolution conflicts", len(conflicts)), ("registry drift", len(drift)),
             ("hard-cut coverage gaps", len(uncovered)), ("sliced bindings", len(sliced))] + ([("duplicate numbers", len(dups))] if args.live else [])
    print(f"semantic v2 gates (catalogue_v2 {svc.version}, {len(rows)} phrases):")
    for name, n in gates:
        print(f"  {'PASS' if n == 0 else 'FAIL'}  {name:26s} {n}")
    for r in conflicts:
        print(f"    conflict {r['phrase']!r}: term={r['resolve_term']} concept={r['resolve_concept']} "
              f"search={r['search_top1']} agent={r['agent_registry']}")
    for x in drift:
        print("    drift", x)
    for x in uncovered:
        print("    uncovered v1 id", x)
    for x in sliced:
        print("    slice", x)
    for x in dups:
        print("    same number", x)
    unresolved = [r["phrase"] for r in rows if r["verdict"] == "UNRESOLVED"]
    print(f"  info: {len(unresolved)} phrases unresolved by every resolver (no metric — e.g. dimension words)")
    if args.json:
        json.dump({"rows": rows, "drift": drift, "uncovered": uncovered, "sliced": sliced, "duplicates": dups}, open(args.json, "w"), indent=1)
    return 1 if any(n for _, n in gates) else 0


def live_slices(svc) -> list[str]:
    """Each slice of a sliced binding repeats the home total (brand 20, last full month): the planner
    pins one slice, so a slice that is NOT a full copy would make breakdown answers wrong."""
    from v2_parity import V2_URL, last_full_months, load, secret, token
    tok = token(secret())
    (s, e), = last_full_months(1)
    bad = []
    for m in svc.cat.metrics.values():
        if m.formula and m.formula.depends_on or not m.is_queryable:
            continue  # ratios follow from their additive parts
        for b in m.bindings:
            if not b.slice_dimension:
                continue
            home_view = m.cube_mapping.view
            platform = {f"{home_view}.ad_platform": "meta"}  # sliced facts here are Meta-only (binding note)
            axis = f"{home_view}.{svc.cat.views[home_view].date_dimension}"
            want = load(V2_URL, tok, m.cube_mapping.measure, axis, "20", s, e, platform)
            for sl in sorted({x for v in b.slices.values() for x in v}):
                got = load(V2_URL, tok, b.measure, f"{b.view}.report_date", "20", s, e,
                           {f"{b.view}.{b.slice_dimension}": sl})
                if not (isinstance(want, float) and isinstance(got, float)
                        and abs(want - got) <= max(0.011, 1e-6 * abs(want))):
                    bad.append(f"{m.id}/{b.name} {b.slice_dimension}={sl}: {got} vs home {want}")
    return bad


def live_duplicates(svc) -> list[tuple]:
    """Metrics with equal non-zero values for brand 20 over the last 3 full months (nulls as 0)."""
    from v2_parity import V2_URL, last_full_months, load, secret, token
    tok = token(secret())
    cells = last_full_months(3)
    vec = {}
    for m in svc.cat.metrics.values():
        if not m.is_queryable:
            continue
        axis = m.cube_mapping.time_dimension or f"{m.cube_mapping.view}.{svc.cat.views[m.cube_mapping.view].date_dimension}"
        vals = []
        for s, e in cells:
            v = load(V2_URL, tok, m.cube_mapping.measure, axis, "20", s, e)
            vals.append(0.0 if v is None or isinstance(v, str) else float(v))
        if any(vals):
            vec[m.id] = vals
    groups = collections.defaultdict(list)
    ids = sorted(vec)
    out = []
    for i, a in enumerate(ids):
        for b in ids[i + 1:]:
            # relative tolerance (rates are small); 1 paisa slack only for money-sized numbers
            if all(abs(x - y) <= max(0.011 if abs(x) > 100 else 0.0, 1e-6 * abs(x)) for x, y in zip(vec[a], vec[b])):
                out.append((a, b, vec[a]))
    return out


if __name__ == "__main__":
    sys.exit(main())
