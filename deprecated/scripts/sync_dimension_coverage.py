#!/usr/bin/env python3
"""Materialise mart drill-down coverage into catalogue/dimensions/generated_coverage.yaml.

Every dimension a catalogue-exposed Cube view carries — minus the exclusions in
catalogue/dimension_waivers.yaml — is emitted here as a view mapping, so it becomes
filterable immediately and (via loader._derive_supported_dimensions) groupable too.
This is what makes "add a column to a mart, it becomes queryable" true without hand-
editing 187 metric YAMLs. Only mappings NOT already present in the curated catalogue
are added; the loader merges them onto the existing dimension defs.

  py scripts/sync_dimension_coverage.py            # regenerate
  py scripts/sync_dimension_coverage.py --check     # CI: exit 1 if stale
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
from seleric_mcp.catalogue_service.loader import (  # noqa: E402
    _load_dimension_waivers,
    load_catalogue,
)

CUBE_API = os.environ.get("SELERIC_CUBE_API", "http://127.0.0.1:4001")
CAT_DIR = CORE / "catalogue"
OUT = CAT_DIR / "dimensions" / "generated_coverage.yaml"
# Members that are never a business axis regardless of waivers (pipeline plumbing).
HARD_SKIP = re.compile(r"(^|\.)(is_final|model_version|source_basis)$")


def cube_view_dims() -> dict[str, list[str]]:
    with urllib.request.urlopen(f"{CUBE_API}/cubejs-api/v1/meta", timeout=30) as r:
        meta = json.loads(r.read().decode())
    out: dict[str, list[str]] = {}
    for c in meta.get("cubes", []):
        out[c["name"]] = sorted(d["name"] for d in c.get("dimensions", []))
    return out


def build() -> dict:
    cat = load_catalogue(CAT_DIR)
    waived, patterns = _load_dimension_waivers(CAT_DIR)

    def is_waived(dim_id: str) -> bool:
        return dim_id in waived or any(p.search(dim_id) for p in patterns)

    # member already mapped by ANY curated dimension on that view -> leave it alone.
    # Read the curated files directly, EXCLUDING our own output, so regeneration is
    # idempotent (load_catalogue merges generated_coverage.yaml back in, which would
    # otherwise make every mapping look already-present on the second run).
    mapped: set[tuple[str, str]] = set()  # (view, member)
    for p in sorted((CAT_DIR / "dimensions").glob("*.yaml")):
        if p.resolve() == OUT.resolve():
            continue
        for raw in (yaml.safe_load(p.read_text(encoding="utf-8")) or {}).get("dimensions", []) or []:
            for view, member in (raw.get("views") or {}).items():
                mapped.add((view, member))

    view_dims = cube_view_dims()
    new_views: dict[str, dict[str, str]] = {}  # dim_id -> {view: member}
    for view in sorted(cat.views):
        for member in view_dims.get(view, []):
            name = member.split(".")[-1]
            if HARD_SKIP.search(member) or is_waived(name):
                continue
            if (view, member) in mapped:
                continue
            new_views.setdefault(name, {})[view] = member

    dims = []
    for dim_id in sorted(new_views):
        dims.append(
            {
                "id": dim_id,
                "display_name": dim_id.replace("_", " ").title(),
                "views": dict(sorted(new_views[dim_id].items())),
            }
        )
    return {"dimensions": dims}


def render(doc: dict) -> str:
    header = (
        "# GENERATED FILE - do not hand-edit.\n"
        "# Regenerate:  py scripts/sync_dimension_coverage.py\n"
        "# Verify:      py scripts/sync_dimension_coverage.py --check\n"
        "#\n"
        "# Mart dimensions (Cube /meta) minus catalogue/dimension_waivers.yaml, emitted\n"
        "# as view mappings the loader merges onto the curated dimension defs. To change\n"
        "# what appears here, add a mart column or edit dimension_waivers.yaml — never\n"
        "# this file. Curated semantics (display_name, aliases, allowed_values) live in\n"
        "# dimensions/core.yaml and win over anything here.\n"
    )
    return header + yaml.safe_dump(doc, sort_keys=False, width=100, allow_unicode=True)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="exit 1 if the file on disk is stale")
    args = ap.parse_args()

    new = render(build())
    old = OUT.read_text(encoding="utf-8") if OUT.exists() else ""
    if args.check:
        if old == new:
            print(f"dimension coverage up to date ({OUT.relative_to(CORE)})")
            return 0
        print(f"dimension coverage STALE — {OUT.relative_to(CORE)} does not match Cube /meta")
        print("Regenerate: py scripts/sync_dimension_coverage.py")
        return 1

    OUT.write_text(new, encoding="utf-8")
    n_dims = new.count("\n  - id:") or new.count("- id:")
    print(f"wrote {OUT.relative_to(CORE)} ({new.count('id:')} dimension entries)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
