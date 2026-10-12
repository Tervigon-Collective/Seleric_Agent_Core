"""Export the canonical metric contract the Seleric Agent consumes (SEM-12/14).

Emits ``doc/semantic_contracts/canonical_metrics.json`` — the single signed
source of truth for metric algebra (composition signs, ratio components and their
scaling multiplier, effective Cube members per view). Deterministic: rerunning
over the same catalogue yields byte-identical output.

    uv run python scripts/export_canonical_contract.py            # write
    uv run python scripts/export_canonical_contract.py --check    # fail if stale
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

from seleric_mcp.catalogue_service.loader import load_catalogue  # noqa: E402

CATALOGUE = REPO / "catalogue_v2"
OUT = REPO / "doc" / "semantic_contracts" / "canonical_metrics.json"


def build(catalogue_dir: Path) -> dict:
    cat = load_catalogue(catalogue_dir)
    metrics: dict[str, dict] = {}
    for mid, m in sorted(cat.metrics.items()):
        rc = m.ratio_components
        entry: dict = {
            "id": mid,
            "display_name": m.display_name,
            "category": m.category,
            "status": m.status,
            "version": m.version,
            "aggregation": m.aggregation,
            "unit": m.unit,
            "grain": m.grain,
            "view": m.cube_mapping.view,
            "measure": m.cube_mapping.measure,
            "time_dimension": m.cube_mapping.time_dimension,
            "formula": {
                "human_readable": m.formula.human_readable,
                "authoritative_source": m.formula.authoritative_source,
                "depends_on": list(m.formula.depends_on),
                "composition": [t.model_dump() for t in m.formula.composition],
            },
            "depends_on": list(m.formula.depends_on),
            "composition": [t.model_dump() for t in m.formula.composition],
            "supported_dimensions": list(m.supported_dimensions),
            "excluded_dimensions": dict(m.excluded_dimensions),
            "bindings": [b.model_dump() for b in m.bindings],
            "replaces": list(m.replaces),
            "unavailable_reason": m.unavailable_reason,
        }
        if rc:
            entry["ratio_components"] = {
                "numerator": rc.numerator,
                "denominator": rc.denominator,
                "multiplier": rc.multiplier,
            }
        if m.ratio_component_members:
            entry["ratio_component_members"] = {
                k: list(v) for k, v in sorted(m.ratio_component_members.items())
            }
        metrics[mid] = entry
    return {"version": cat.version, "semantic_version": cat.semantic_version, "metrics": metrics}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--check", action="store_true", help="fail if the committed artifact is stale")
    ap.add_argument("--catalogue", default=str(CATALOGUE))
    args = ap.parse_args()

    doc = build(Path(args.catalogue))
    payload = json.dumps(doc, indent=2, sort_keys=False) + "\n"
    if args.check:
        if not OUT.is_file() or OUT.read_text(encoding="utf-8") != payload:
            print("canonical_metrics.json is stale — rerun without --check", file=sys.stderr)
            return 1
        print(f"canonical contract OK ({doc['version']}): {len(doc['metrics'])} metrics")
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(payload, encoding="utf-8")
    scaled = sum(1 for m in doc["metrics"].values()
                 if m.get("ratio_components") and m["ratio_components"]["multiplier"] != 1)
    print(f"wrote {OUT.relative_to(REPO)} ({doc['version']}): {len(doc['metrics'])} metrics, "
          f"{scaled} scaled ratio(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
