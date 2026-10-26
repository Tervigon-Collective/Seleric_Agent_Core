#!/usr/bin/env python
"""Capability audit — what the MCP catalogue can actually resolve, and where it can't.

Reads the catalogue the seleric-mcp server serves (``load_catalogue`` — the same
model behind ``catalogue_list_metrics`` / ``catalogue_list_dimensions``), NOT the
Cube ``/v1/meta`` endpoint: the MCP is the layer the agent resolves against, so
the audit must reflect the MCP's own view of reachability.

It answers the question the "Pro Suspender Boots by source, exclude exchanges"
trace exposed — *can a single metric carry the dimensions this question needs at
one grain?* — for every dimension pair, with no hardcoded dimension list:

  A. Coverage summary (metrics / queryable / dimensions / views).
  B. Undeclared dimensions — referenced by a metric but absent from the
     dimension index (a real catalogue drift; the agent can't resolve them).
  C. Orphan dimensions — declared but supported by no queryable metric.
  D. Reachability by grain family — which grain(s) can carry each dimension.
  E. Cross-grain joint gaps — dimension pairs each answerable alone but never
     together on one metric (e.g. product  x  source_name). This is the backlog
     for Cube/serve extensions.

Run:  python scripts/capability_audit.py [--catalogue DIR] [--json OUT]
Exit: 0 always (report tool). Undeclared dimensions are printed as WARN.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "src"))

from seleric_mcp.catalogue_service.loader import Catalogue, load_catalogue  # noqa: E402


def grain_family(grain: str) -> str:
    """Coarse grain bucket from a metric's free-text ``grain``. Two metrics can
    only be combined by the same query if they share a grain family, so this is
    the axis joint-reachability is measured on. Keyword-based and deliberately
    conservative: an unrecognized grain becomes its own family verbatim."""
    g = (grain or "").strip().lower()
    if not g:
        return "unknown"
    if "order_item" in g or "line" in g or "product" in g or "sku" in g:
        return "product/line"
    if "attribution" in g or "last-touch" in g or "touch" in g:
        return "attribution/order"
    if "order" in g:
        return "order"
    if "session" in g or "event" in g or "funnel" in g:
        return "web/session"
    if "day" in g or "daily" in g or "date" in g:
        return "daily"
    if "customer" in g:
        return "customer"
    return g


def _capabilities(metric) -> set[str]:
    """Every dimension a metric can carry, as a breakdown or a filter."""
    return {d for d in (*metric.supported_dimensions, *metric.supported_filters) if d}


def audit(cat: Catalogue) -> dict:
    queryable = [m for m in cat.metrics.values() if m.is_queryable]
    declared_dims = set(cat.dimensions)

    # dimension -> {metric_id: family} and dimension -> set(families)
    dim_metrics: dict[str, dict[str, str]] = defaultdict(dict)
    dim_families: dict[str, set[str]] = defaultdict(set)
    referenced_dims: set[str] = set()
    for m in queryable:
        fam = grain_family(m.grain)
        for d in _capabilities(m):
            referenced_dims.add(d)
            dim_metrics[d][m.id] = m.grain
            dim_families[d].add(fam)

    undeclared = sorted(referenced_dims - declared_dims)
    orphan = sorted(declared_dims - referenced_dims)

    # Cross-grain joint gaps: dims each supported by >=1 metric, but no single
    # metric supports both. Restricted to pairs whose supporting families do not
    # overlap — a true structural gap (you can't reach them at one grain),
    # not merely a metric nobody has authored yet within one family.
    supporting: dict[str, set[str]] = {d: set(dim_metrics[d]) for d in referenced_dims}
    dims_sorted = sorted(referenced_dims)
    joint_gaps: list[dict] = []
    for i, d1 in enumerate(dims_sorted):
        for d2 in dims_sorted[i + 1 :]:
            if supporting[d1] & supporting[d2]:
                continue  # some metric carries both — reachable
            if dim_families[d1] & dim_families[d2]:
                continue  # same grain family; a within-family metric could join them
            joint_gaps.append(
                {
                    "dimensions": [d1, d2],
                    "families": {d1: sorted(dim_families[d1]), d2: sorted(dim_families[d2])},
                }
            )

    return {
        "version": cat.version,
        "counts": {
            "metrics_total": len(cat.metrics),
            "metrics_queryable": len(queryable),
            "dimensions_declared": len(declared_dims),
            "dimensions_referenced": len(referenced_dims),
            "views": len({m.cube_mapping.view for m in queryable}),
        },
        "undeclared_dimensions": undeclared,
        "orphan_dimensions": orphan,
        "reachability": {
            d: {"families": sorted(dim_families[d]), "metric_count": len(dim_metrics[d])}
            for d in dims_sorted
        },
        "cross_grain_joint_gaps": joint_gaps,
    }


def _print_report(report: dict) -> None:
    c = report["counts"]
    print(f"\n=== Capability audit (catalogue {report['version']}) ===")
    print(
        f"metrics: {c['metrics_queryable']} queryable / {c['metrics_total']} total | "
        f"dimensions: {c['dimensions_referenced']} used / {c['dimensions_declared']} declared | "
        f"views: {c['views']}"
    )

    und = report["undeclared_dimensions"]
    print(f"\n[B] Undeclared dimensions (referenced but not in the dimension index): {len(und)}")
    for d in und:
        print(f"   WARN  {d}")

    orph = report["orphan_dimensions"]
    print(f"\n[C] Orphan dimensions (declared, supported by no queryable metric): {len(orph)}")
    print("   " + (", ".join(orph) if orph else "(none)"))

    gaps = report["cross_grain_joint_gaps"]
    print(f"\n[E] Cross-grain joint gaps (each dim reachable alone, never together): {len(gaps)}")
    # Surface the ones a product/line dimension is on one side of — the trace class.
    prod = [
        g for g in gaps
        if any("product/line" in g["families"][d] for d in g["dimensions"])
    ]
    print(f"    of which involve a product/line-grain dimension: {len(prod)}")
    for g in prod[:40]:
        d1, d2 = g["dimensions"]
        print(f"   GAP   {d1}  x  {d2}   ({'/'.join(g['families'][d1])} | {'/'.join(g['families'][d2])})")
    if len(prod) > 40:
        print(f"   ... +{len(prod) - 40} more (see --json)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--catalogue", type=Path, default=_REPO / "catalogue")
    ap.add_argument("--json", type=Path, default=None, help="write full report JSON here")
    args = ap.parse_args()

    cat = load_catalogue(args.catalogue)
    report = audit(cat)
    _print_report(report)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(report, indent=2), encoding="utf-8")
        print(f"\nfull report -> {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
