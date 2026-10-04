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
  live filters           (--live) every filter the catalogue or registry can emit (glossary, concept axes,
                         retired-id replacements, registry catalogue_filters) runs on Cube v2 without error
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


def load_surface(agent: str):
    """(CatalogueService on catalogue_v2, the agent's v2 registry entries)."""
    from seleric_mcp.catalogue_service.loader import load_catalogue
    from seleric_mcp.catalogue_service.service import CatalogueService
    svc = CatalogueService(load_catalogue(ROOT / "catalogue_v2"))
    reg = yaml.safe_load(Path(agent, "config", "metric_registry.yaml").read_text())["metrics"]
    return svc, reg


def compute(svc, reg, v1_reg, live: bool) -> dict:
    """Every gate's findings (lists) plus the per-phrase resolution rows."""
    from probe_phrases import PROBE_PHRASES
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
    filter_errors = []
    if live:
        filter_errors = live_filters(svc, reg)
        sliced += live_slices(svc)
        dups = live_duplicates(svc)
    gates = [("resolution conflicts", len(conflicts)), ("registry drift", len(drift)),
             ("hard-cut coverage gaps", len(uncovered)), ("sliced bindings", len(sliced))] + (
        [("live filters", len(filter_errors)), ("duplicate numbers", len(dups))] if live else [])
    return {"gates": gates, "rows": rows, "conflicts": conflicts, "drift": drift, "uncovered": uncovered,
            "sliced": sliced, "filters": filter_errors, "duplicates": dups}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--agent", default=os.environ.get("SELERIC_AGENT", "/opt/seleric/Seleric_Agent"),
                    help="Seleric_Agent checkout whose config/metric_registry.yaml is on v2 ids")
    ap.add_argument("--v1-agent", default="/opt/seleric/Seleric_Agent", help="registry whose aliases seed phrases")
    ap.add_argument("--live", action="store_true")
    ap.add_argument("--json")
    args = ap.parse_args()
    svc, reg = load_surface(args.agent)
    v1_reg = yaml.safe_load(Path(args.v1_agent, "config", "metric_registry.yaml").read_text())["metrics"]
    r = compute(svc, reg, v1_reg, args.live)
    gates, rows, conflicts = r["gates"], r["rows"], r["conflicts"]
    drift, uncovered, sliced, filter_errors, dups = r["drift"], r["uncovered"], r["sliced"], r["filters"], r["duplicates"]
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
    for x in filter_errors:
        print("    filter", x)
    for x in dups:
        print("    same number", x)
    unresolved = [r["phrase"] for r in rows if r["verdict"] == "UNRESOLVED"]
    print(f"  info: {len(unresolved)} phrases unresolved by every resolver (no metric — e.g. dimension words)")
    if args.json:
        json.dump({k: v for k, v in r.items() if k != "gates"}, open(args.json, "w"), indent=1)
    return 1 if any(n for _, n in gates) else 0


def live_filters(svc, reg) -> list[str]:
    """Run every (metric, filters) pair the surface can produce on Cube v2 (brand 20, last full month).
    A type mismatch (e.g. boolean 'true' on a UInt8 column) only shows up when ClickHouse runs it."""
    from v2_parity import V2_URL, last_full_months, load, secret, token
    tok = token(secret())
    (s, e), = last_full_months(1)
    pairs = filter_pairs(svc, reg)
    bad = []
    for mid, flt in sorted(pairs):
        m = svc.cat.metrics.get(mid)
        if m is None or not m.is_queryable:
            continue
        members, err = filter_members(svc, m, flt)
        if err:
            bad.append(f"{mid} {dict(flt)}: {err}")
            continue
        axis = m.cube_mapping.time_dimension or f"{m.cube_mapping.view}.{svc.cat.views[m.cube_mapping.view].date_dimension}"
        got = load(V2_URL, tok, m.cube_mapping.measure, axis, "20", s, e, members)
        if isinstance(got, str):
            bad.append(f"{mid} {dict(flt)}: {got[:160]}")
    print(f"  info: {len(pairs)} metric + filter pairs run live")
    return bad


def filter_members(svc, m, flt) -> tuple[dict, str | None]:
    """Catalogue filter ids -> Cube members on the metric's view (or the reason it cannot be applied)."""
    view, members = m.cube_mapping.view, {}
    for k, v in flt:
        dim = svc.cat.dimensions.get(k)
        if dim is None or view not in dim.views:
            return {}, f"dimension {k} not on view {view}"
        members[dim.views[view]] = v
    return members, None


def filter_pairs(svc, reg) -> dict[tuple[str, tuple], set[str]]:
    """Every (metric, filters) the surface can emit -> where it comes from
    (glossary / retired-id replacement / agent registry / concept axis)."""
    pairs: dict[tuple[str, tuple], set[str]] = {}

    def add(mid: str, flt: dict, src: str) -> None:
        pairs.setdefault((mid, tuple(sorted((k, str(v)) for k, v in flt.items()))), set()).add(src)

    for t in svc.cat.glossary:
        if t.canonical_id and t.filter:
            add(t.canonical_id, t.filter, f"glossary: {t.term}")
    for d in svc.cat.deprecations:
        if d.filters and d.new in svc.cat.metrics:
            add(d.new, d.filters, f"retired: {d.old}")
    for r in reg:
        if r.get("catalogue_metric") and r.get("catalogue_filters"):
            add(r["catalogue_metric"], r["catalogue_filters"], f"registry: {r['id']}")
    for c in svc.cat.concepts.values():
        targets = {x.metric for x in c.resolves if getattr(x, "metric", None)}
        for axis, values in c.axis_filters.items():
            for value, flt in values.items():
                for mid in targets:
                    m = svc.cat.metrics.get(mid)
                    if m and all(m.cube_mapping.view in getattr(svc.cat.dimensions.get(k), "views", {}) for k in flt):
                        add(mid, flt, f"concept: {c.id} {axis}={value}")
    return pairs


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
