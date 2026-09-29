#!/usr/bin/env python3
"""Absorb a new mart with minimal hand-work.

When a new Cube view (mart) appears, its DIMENSIONS auto-expose (see
sync_dimension_coverage.py) and its lineage/domain/axis auto-derive (see
sync_catalogue_from_sources.py). The only thing that cannot be derived is a
measure's business SEMANTICS. This tool writes a draft metric stub per uncovered
measure so onboarding is "run this, fill the description, move it in" instead of
authoring every metric from scratch.

  py scripts/scaffold_metrics_for_view.py --list                 # report gaps, all views
  py scripts/scaffold_metrics_for_view.py --list <view>          # report gaps, one view
  py scripts/scaffold_metrics_for_view.py <view>                 # write stubs to catalogue/_scaffold/

Stubs land in catalogue/_scaffold/ (NOT auto-loaded). Review, fill each `description`
and set a real `status`, then move the file into catalogue/metrics/. The governance
gate lists any measure still missing a metric until it is catalogued or waived.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.request
from pathlib import Path

import yaml

CORE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(CORE / "src"))
from seleric_mcp.catalogue_service.loader import load_catalogue  # noqa: E402

CAT_DIR = CORE / "catalogue"
CUBE_API = os.environ.get("SELERIC_CUBE_API", "http://127.0.0.1:4001")
OUT_DIR = CAT_DIR / "_scaffold"

RATIO_TYPES = {"number", "avg"}
RATIO_HINT = re.compile(r"(_rate$|_pct$|ctr|cpc|cpm|roas|ratio|^avg_|_per_)")
CURRENCY_HINT = re.compile(r"(spend|revenue|sales|cogs|profit|cost|amount|aov|cpc|cpm|discount|margin$)")
COUNT_HINT = re.compile(r"(orders?$|units?$|sessions?$|clicks?$|impressions?$|events?$|_count$|customers?$)")


def cube_measures() -> dict[str, list[dict]]:
    with urllib.request.urlopen(f"{CUBE_API}/cubejs-api/v1/meta", timeout=30) as r:
        meta = json.loads(r.read().decode())
    return {c["name"]: (c.get("measures") or []) for c in meta.get("cubes", [])}


def _module_of(cat, view: str) -> str | None:
    for mid, mod in cat.modules.items():
        onto = cat.openmetadata.ontology if cat.openmetadata else None
        dom_views = set(mod.extra_views)
        for dom in mod.domains:
            if onto and dom in (onto.domains or {}):
                dom_views |= set(onto.domains[dom].get("cube_views") or [])
        if view in dom_views:
            return mid
    return None


def _stub(cat, view: str, m: dict) -> dict:
    member = m["name"]
    name = member.split(".")[-1]
    # Name wins over Cube type: many additive counts/amounts are declared type
    # "number" (computed) in the model, so trust an explicit rate/currency/count word
    # first and only fall back to the type when the name says nothing.
    if RATIO_HINT.search(name):
        is_ratio = True
    elif COUNT_HINT.search(name) or CURRENCY_HINT.search(name):
        is_ratio = False
    else:
        is_ratio = m.get("type") in RATIO_TYPES
    if is_ratio:
        unit = "ratio"
    elif CURRENCY_HINT.search(name):
        unit = "INR"
    elif COUNT_HINT.search(name):
        unit = "count"
    else:
        unit = "TODO"
    stub = {
        "id": name,
        "display_name": name.replace("_", " ").title(),
        "category": _module_of(cat, view) or "TODO",
        "status": "draft",  # not queryable until reviewed + promoted
        "description": f"TODO: define {name} on {view}. {m.get('title') or ''}".strip(),
        "formula": {"human_readable": f"TODO ({m.get('type', 'sum')} of {member})",
                    "authoritative_source": "cube"},
        "cube_mapping": {"view": view, "measure": member},
        "aggregation": "ratio" if is_ratio else "additive",
        "unit": unit,
        "grain": "TODO: from the view's data contract",
        "supported_dimensions": [],   # derived by the loader from the view minus waivers
        "supported_filters": [],
        "data_owner": "TODO",
    }
    if is_ratio:
        stub["ratio_components"] = {"numerator": "TODO.member", "denominator": "TODO.member"}
    return stub


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("view", nargs="?", help="view to scaffold; omit with --list for all views")
    ap.add_argument("--list", action="store_true", help="report uncovered measures, write nothing")
    args = ap.parse_args()

    cat = load_catalogue(CAT_DIR)
    covered = {(m.cube_mapping.view, m.cube_mapping.measure) for m in cat.metrics.values()}
    for m in cat.metrics.values():
        if m.cube_mapping.measure_pct:
            covered.add((m.cube_mapping.view, m.cube_mapping.measure_pct))
    measures = cube_measures()

    views = [args.view] if args.view else sorted(cat.views)
    total = 0
    for view in views:
        if view not in cat.views:
            print(f"!! '{view}' is not a catalogue view", file=sys.stderr)
            continue
        uncovered = [m for m in measures.get(view, []) if (view, m["name"]) not in covered]
        if not uncovered:
            continue
        total += len(uncovered)
        if args.list:
            print(f"\n{view}: {len(uncovered)} uncovered measure(s)")
            for m in uncovered:
                print(f"  - {m['name'].split('.')[-1]:32} ({m.get('type', '?')})")
            continue
        OUT_DIR.mkdir(exist_ok=True)
        stubs = [_stub(cat, view, m) for m in uncovered]
        out = OUT_DIR / f"{view}.yaml"
        body = "".join(
            "---\n" + yaml.safe_dump(s, sort_keys=False, allow_unicode=True) for s in stubs
        )
        out.write_text(
            f"# DRAFT stubs for uncovered measures on '{view}'. Fill each description,\n"
            f"# set status/grain/data_owner (+ ratio_components for ratios), then move the\n"
            f"# individual metrics into catalogue/metrics/. NOT auto-loaded from here.\n" + body,
            encoding="utf-8",
        )
        print(f"wrote {out.relative_to(CORE)} ({len(stubs)} draft stubs)")

    if args.list:
        print(f"\nTotal uncovered measures across {len(views)} view(s): {total}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
